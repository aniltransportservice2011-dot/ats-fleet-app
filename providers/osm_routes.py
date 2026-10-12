"""
OpenStreetMap-backed live route finder: place name -> coordinates (Nominatim), coordinates ->
driving routes with distance, time and highway list (OSRM), and toll plazas / major towns along a
chosen route (Overpass).

Nothing here is stored. The Route Finder asks on demand, the way a maps app does, and keeps only a
short in-memory cache (see app.py) so a repeat search doesn't hit the free public servers again.
Those servers run on donated infrastructure, so this module is deliberately gentle: one Nominatim
request per second, hard timeouts on every call, a shared deadline so one lookup can't outlive a 60s
web worker, and bounding-box Overpass queries (a "within X metres of a 400 km line" query times out
on the public servers).

Type-ahead place suggestions come from Photon (photon.komoot.io), NOT Nominatim: Nominatim's usage
policy forbids autocomplete on the public server, while Photon is built for search-as-you-type on the
same OpenStreetMap data. Nominatim is still used for the final Search when a place was typed in full
without picking a suggestion.

Towns and cities along a route do NOT come from Overpass any more: they are read from a small fixed
file of Indian places shipped with the app (providers/data/in_places.tsv, from GeoNames, ~5,000 places
of 10,000+ people or administrative seats), so they appear with the route in milliseconds and cannot be
hit by the free server's busy spells. Only toll plazas, petrol pumps and landmarks still need Overpass,
and they are fetched separately so one failure hides only its own part. The file is read-only reference
data held in memory — it is not in the database and never grows.

Pure functions plus plain `requests` calls — no database or Flask imports — so every piece of path
maths below is tested with canned responses and no network.

Caveats the UI repeats to the user: routes are OSRM's *car* routes (a truck-restricted road or a
bypass banned for heavy vehicles is not modelled), and OpenStreetMap's toll data is incomplete —
most plazas have a location but no name.
"""
import math
import os
import re
import threading
import time

import requests

USER_AGENT = 'RATH-fleet-app/1.0 (route finder)'

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
OSRM_URL = 'https://router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}'
OVERPASS_URLS = ('https://overpass-api.de/api/interpreter',
                 'https://overpass.kumi.systems/api/interpreter')

PHOTON_URL = 'https://photon.komoot.io/api/'
# India's bounding box (min lon, min lat, max lon, max lat). It also catches slivers of neighbouring
# countries, so results are filtered to countrycode IN as well.
INDIA_BBOX = '68.1,6.5,97.5,35.8'
PHOTON_TIMEOUT = 6
SUGGEST_MIN_CHARS = 3
NOMINATIM_TIMEOUT = 8
OSRM_TIMEOUT = 15
OVERPASS_TIMEOUT = 22
SEARCH_BUDGET = 30   # seconds: geocode x2 + routing
EXTRAS_BUDGET = 30   # seconds: Overpass (the all-in-one lookup)
PART_BUDGET = 20     # seconds: each of the separate toll / petrol lookups — they are optional extras, so give up sooner
MAX_ALTERNATIVES = 3

# How close to the path a feature must be to count, in metres. OSRM snaps the path onto one
# carriageway and a plaza has booths on both, so tolls get slack; wider would pull in plazas on a
# parallel road the route never uses. Towns may sit a few km off the highway (bypasses) and still
# be "on the way".
TOLL_RADIUS_M = 250
FUEL_RADIUS_M = 200   # petrol pumps sit beside the road, sometimes on a service lane
TOWN_RADIUS_M = 4000
LANDMARK_RADIUS_M = 3000     # a landmark counts as "on the way" within this of the route
AIRPORT_RADIUS_M = 8000      # airports sit well away from the highway
MAX_CANDIDATES = 40          # ranked major places kept per route; the screen then shows only the biggest few
# How many "key locations" fit each layout (the finder's own width class: sm = phone, md = tablet, lg = desktop),
# and roughly how far apart they should be — a long route gets more, a short one few, never more than the screen holds.
SCREEN_STOP_CAPS = {'sm': 6, 'md': 8, 'lg': 10}
KM_PER_STOP = 40
BBOX_CHUNK_KM = 50
BBOX_PAD_DEG = 0.045  # ~5 km
PATH_TOLERANCE_M = 25


class RouteLookupError(Exception):
    """Raised with a message that is safe to show the user as-is."""


# --------------------------------------------------------------------------- rate limiting

_gate_locks = {}
_gate_last = {}
_gate_guard = threading.Lock()


def _throttle(key, min_gap):
    """Block until at least `min_gap` seconds have passed since the last call under `key` —
    process-wide, so two requests arriving together still go out one second apart."""
    with _gate_guard:
        lock = _gate_locks.setdefault(key, threading.Lock())
    with lock:
        wait = _gate_last.get(key, 0) + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _gate_last[key] = time.monotonic()


def _headers():
    return {'User-Agent': USER_AGENT, 'Accept': '*/*'}


class _Budget:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def left(self):
        return self.end - time.monotonic()

    def timeout_for(self, nominal):
        """Per-call timeout: the nominal one, but never more than what's left of the budget."""
        return max(1.0, min(nominal, self.left()))


# --------------------------------------------------------------------------- geometry

def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cumulative_km(points):
    """Distance along the path, in km, at every vertex (first vertex = 0)."""
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + haversine_km(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]))
    return cum


def simplify(points, tolerance_m):
    """Ramer-Douglas-Peucker, iterative (a 30k-vertex path would blow Python's recursion limit).
    Distances use a local equirectangular projection — plenty accurate at tolerance scale."""
    n = len(points)
    if n < 3:
        return [list(p) for p in points]
    lat0 = sum(p[0] for p in points) / n
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    xy = [((p[1]) * kx, (p[0]) * ky) for p in points]
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_m * tolerance_m
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
            if d2 > best:
                best, best_i = d2, i
        if best > tol2:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [list(p) for p, k in zip(points, keep) if k]


def project_on_path(poly, cum, lat, lng):
    """Where a point falls on a path: (km along the path, distance off the path in metres).
    Point-to-segment on a local equirectangular projection, so it works on a simplified path
    whose vertices can be many km apart (nearest-vertex would be wildly off there)."""
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0
    px, py = lng * kx, lat * ky
    best, best_km = float('inf'), 0.0
    for i in range(len(poly) - 1):
        ax, ay = poly[i][1] * kx, poly[i][0] * ky
        bx, by = poly[i + 1][1] * kx, poly[i + 1][0] * ky
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
        if d2 < best:
            best = d2
            best_km = cum[i] + t * (cum[i + 1] - cum[i])
    return best_km, math.sqrt(best)


def route_bboxes(points, cum, chunk_km=BBOX_CHUNK_KM, pad=BBOX_PAD_DEG):
    """Bounding boxes (south, west, north, east) covering the path in ~chunk_km pieces."""
    boxes, start = [], 0
    n = len(points)
    for i in range(1, n + 1):
        if i == n or cum[i] - cum[start] >= chunk_km:
            seg = points[start:i + 1]
            lats = [p[0] for p in seg]
            lngs = [p[1] for p in seg]
            boxes.append((min(lats) - pad, min(lngs) - pad, max(lats) + pad, max(lngs) + pad))
            start = i
    return boxes


# --------------------------------------------------------------------------- built-in places

PLACES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'in_places.tsv')
_CAPITAL_BY_CODE = {'PPLC': 'yes', 'PPLA': '4', 'PPLA2': '5'}      # same meaning as OpenStreetMap's `capital` tag
_places_cache = None
_places_lock = threading.Lock()


def load_places():
    """The built-in list of Indian places, read once per process: [(name, lat, lng, population, feature_code)].
    A missing or unreadable file gives an empty list (the route then simply shows no towns, never an error)."""
    global _places_cache
    if _places_cache is not None:
        return _places_cache
    with _places_lock:
        if _places_cache is None:
            rows = []
            try:
                with open(PLACES_FILE, encoding='utf-8') as f:
                    for line in f:
                        if not line.strip() or line.startswith('#'):
                            continue
                        parts = line.rstrip('\n').split('\t')
                        try:
                            rows.append((parts[0], float(parts[1]), float(parts[2]), int(parts[3]), parts[4]))
                        except (IndexError, ValueError):
                            continue
            except OSError:
                rows = []
            _places_cache = rows
    return _places_cache


def local_place_elements(boxes):
    """Built-in places inside any of the route's bounding boxes, shaped like Overpass elements so the same
    build_places() / build_tolls() code ranks them. A place of 100,000+ people is a city, any other a town."""
    out = []
    for i, (name, lat, lng, pop, code) in enumerate(load_places()):
        if not any(s_ <= lat <= n_ and w_ <= lng <= e_ for s_, w_, n_, e_ in boxes):
            continue
        tags = {'place': 'city' if pop >= 100000 else 'town', 'name': name, 'population': str(pop)}
        if code in _CAPITAL_BY_CODE:
            tags['capital'] = _CAPITAL_BY_CODE[code]
        out.append({'type': 'node', 'id': f'gz{i}', 'lat': lat, 'lon': lng, 'tags': tags})
    return out


# --------------------------------------------------------------------------- Nominatim

def clean_search_text(raw):
    """Turn the doubled-up text the Trip form's location box ends up storing ("Jharsuguda,
    Odisha Jharsuguda, Odisha Odisha", "RSP RSP null") into something a geocoder understands:
    drop 'null', drop repeated words, keep the first-seen order."""
    seen, out = set(), []
    for tok in re.split(r'\s+', (raw or '').strip()):
        norm = re.sub(r'[^\w]', '', tok).lower()
        if not norm or norm == 'null' or norm in seen:
            continue
        seen.add(norm)
        out.append(tok)
    return ' '.join(out).strip(' ,')


def geocode_candidates(text, bias_center=None, budget=None, session=None, limit=5):
    """Look a place name up. Returns up to `limit` matches, best first:
    [{'lat','lng','display_name'}, ...] — empty when nothing matched.

    `bias_center` ((lat, lng) or None) is where the other end of the trip is: Nominatim is asked
    to prefer that area and the matches are ordered nearest-first. Local names like "RSP" or
    "IDC" exist in many Indian cities; without this the first hit is often the wrong city."""
    text = (text or '').strip()
    if not text:
        return []
    budget = budget or _Budget(SEARCH_BUDGET)
    get = (session or requests).get
    params = {'q': text, 'format': 'jsonv2', 'limit': limit, 'countrycodes': 'in', 'addressdetails': 0}
    if bias_center:
        lat, lng = bias_center
        params['viewbox'] = f'{lng - 4},{lat + 4},{lng + 4},{lat - 4}'
        params['bounded'] = 0
    _throttle('nominatim', 1.1)
    try:
        r = get(NOMINATIM_URL, params=params, headers=_headers(), timeout=budget.timeout_for(NOMINATIM_TIMEOUT))
        r.raise_for_status()
        results = r.json()
    except (requests.RequestException, ValueError) as e:
        raise RouteLookupError(f'The place search (OpenStreetMap Nominatim) did not respond ({e.__class__.__name__}). Try again in a minute.')
    out = [{'lat': float(x['lat']), 'lng': float(x['lon']), 'display_name': x.get('display_name') or text} for x in results]
    if bias_center:
        out.sort(key=lambda c: haversine_km(bias_center[0], bias_center[1], c['lat'], c['lng']))
    return out


def geocode(text, bias_center=None, budget=None, session=None):
    """Best single match or None."""
    c = geocode_candidates(text, bias_center=bias_center, budget=budget, session=session)
    return c[0] if c else None


# --------------------------------------------------------------------------- Photon (type-ahead)

_KIND_BY_VALUE = {
    'city': 'City', 'town': 'Town', 'village': 'Village', 'hamlet': 'Village', 'suburb': 'Area',
    'neighbourhood': 'Area', 'quarter': 'Area', 'locality': 'Area', 'station': 'Railway station',
    'aerodrome': 'Airport', 'industrial': 'Industrial area', 'administrative': 'Area',
    'port': 'Port', 'harbour': 'Port',
}


def _kind_label(key, value):
    value = (value or '').lower()
    if key == 'highway':
        return 'Road'
    if key == 'railway' and value not in _KIND_BY_VALUE:
        return 'Railway'
    if value in _KIND_BY_VALUE:
        return _KIND_BY_VALUE[value]
    return value.replace('_', ' ').title() if value else ''


def suggest_places(text, bias_center=None, session=None, limit=7):
    """Type-ahead matches for a partly typed place name, India only, best first:
    [{'name','detail','kind','label','display_name','lat','lng'}, ...]. Empty below
    SUGGEST_MIN_CHARS letters. `bias_center` ((lat, lng) or None) nudges results towards where the
    other end of the trip is, so a short local name ("Tangi") resolves near it first.

    `label` is the short text put in the box once picked ("Rourkela, Odisha"); `display_name` is the
    full line shown on the results ("Rourkela, Raurkela (M), Odisha, India")."""
    text = (text or '').strip()
    if len(text) < SUGGEST_MIN_CHARS:
        return []
    get = (session or requests).get
    params = {'q': text, 'limit': limit + 6, 'lang': 'en', 'bbox': INDIA_BBOX}
    if bias_center:
        params['lat'], params['lon'] = bias_center
    _throttle('photon', 0.25)
    try:
        r = get(PHOTON_URL, params=params, headers=_headers(), timeout=PHOTON_TIMEOUT)
        if r.status_code == 429:
            raise RouteLookupError('Place search is busy right now — wait a moment and keep typing.')
        r.raise_for_status()
        features = r.json().get('features', [])
    except RouteLookupError:
        raise
    except (requests.RequestException, ValueError) as e:
        raise RouteLookupError(f'Place search is unavailable right now ({e.__class__.__name__}). You can still type the full place name and press Search.')
    out = []
    for f in features:
        pr = f.get('properties') or {}
        if pr.get('countrycode') and pr['countrycode'] != 'IN':
            continue
        try:
            lng, lat = f['geometry']['coordinates'][:2]
        except (KeyError, TypeError, ValueError):
            continue
        name = (pr.get('name') or '').strip()
        if not name:
            continue
        state = (pr.get('state') or '').strip()
        # smallest area that isn't the name itself, then the state
        area = next((pr[k].strip() for k in ('district', 'county', 'city') if pr.get(k) and pr[k].strip().lower() != name.lower()), '')
        parts = [x for x in (area, state) if x]
        key, value = pr.get('osm_key'), pr.get('osm_value')
        out.append({'name': name, 'detail': ', '.join(parts), 'kind': _kind_label(key, value),
                    'label': f'{name}, {state}' if state else name,
                    'display_name': ', '.join([name] + parts + ['India']),
                    'lat': round(lat, 5), 'lng': round(lng, 5),
                    '_admin': key == 'boundary'})
    # Photon lists a town and its administrative boundary as two hits; keep the more useful one
    # (the place itself) and drop an area-outline twin of the same name within ~8 km.
    kept = []
    for c in out:
        twin = next((k for k in kept if k['name'].lower() == c['name'].lower()
                     and haversine_km(k['lat'], k['lng'], c['lat'], c['lng']) < 8), None)
        if twin is None:
            kept.append(c)
        elif twin['_admin'] and not c['_admin']:
            kept[kept.index(twin)] = c
    for c in kept:
        c.pop('_admin', None)
    return kept[:limit]


# --------------------------------------------------------------------------- OSRM

def normalize_ref(ref):
    """OSRM's `ref` for a road can be 'NH 16', 'NH16', 'NH-16;AH 45'. Pick the national/state
    highway designation and write it one way ('NH 16'). Returns None for no ref."""
    if not ref:
        return None
    parts = [p.strip() for p in re.split(r'[;,]', ref) if p.strip()]
    if not parts:
        return None
    ordered = sorted(parts, key=lambda p: 0 if re.match(r'^NH', p, re.I) else (1 if re.match(r'^(SH|MDR|ODR)', p, re.I) else 2))
    chosen = ordered[0]
    m = re.match(r'^([A-Za-z]+)\s*[-]?\s*(\d+[A-Za-z]?)$', chosen)
    if m:
        return f'{m.group(1).upper()} {m.group(2).upper()}'
    return chosen


def _parse_route(route):
    points = [[c[1], c[0]] for c in route['geometry']['coordinates']]
    segments = []
    run_km = 0.0
    run_min = 0.0
    pace = [[0.0, 0.0]]   # [km from start, minutes from start] — lets the UI say "after 2 h 20 min"
    for leg in route.get('legs', []):
        for step in leg.get('steps', []):
            km = (step.get('distance') or 0) / 1000.0
            ref = normalize_ref(step.get('ref'))
            if segments and segments[-1]['ref'] == ref:
                segments[-1]['km'] += km
            else:
                segments.append({'ref': ref, 'km': km, 'from_km': run_km})
            run_km += km
            run_min += (step.get('duration') or 0) / 60.0
            if run_km - pace[-1][0] >= 1.0:
                pace.append([round(run_km, 2), round(run_min, 1)])
    if pace[-1][0] < round(run_km, 2):
        pace.append([round(run_km, 2), round(run_min, 1)])
    for s in segments:
        s['km'] = round(s['km'], 1)
        s['from_km'] = round(s['from_km'], 1)
    return {'distance_km': round(route['distance'] / 1000.0, 1),
            'duration_min': round(route['duration'] / 60.0),
            'points': points, 'highways': segments, 'pace': pace}


def fetch_routes(lat1, lng1, lat2, lng2, budget=None, session=None):
    """Driving routes between two points, fastest first, plus up to MAX_ALTERNATIVES-1 alternatives
    when OSRM finds distinct ones. Each: {'distance_km','duration_min','points' (dense, [lat,lng]),
    'highways':[{'ref','km','from_km'}] in travel order, consecutive same-road steps merged}."""
    budget = budget or _Budget(SEARCH_BUDGET)
    get = (session or requests).get
    url = OSRM_URL.format(lat1=lat1, lng1=lng1, lat2=lat2, lng2=lng2)
    try:
        r = get(url, params={'overview': 'full', 'geometries': 'geojson', 'steps': 'true',
                             'alternatives': str(MAX_ALTERNATIVES)},
                headers=_headers(), timeout=budget.timeout_for(OSRM_TIMEOUT))
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise RouteLookupError(f'The road routing service (OpenStreetMap OSRM) did not respond ({e.__class__.__name__}). Try again in a minute.')
    if data.get('code') != 'Ok' or not data.get('routes'):
        raise RouteLookupError('No driving route was found between these two places — check that both are placed correctly.')
    return [_parse_route(rt) for rt in data['routes']]


def fetch_route(lat1, lng1, lat2, lng2, budget=None, session=None):
    """The single fastest route."""
    return fetch_routes(lat1, lng1, lat2, lng2, budget=budget, session=session)[0]


def _via(highways):
    """'via NH 49, SH 10' — the roads that carry most of the distance."""
    totals = {}
    for h in highways:
        if h['ref']:
            totals[h['ref']] = totals.get(h['ref'], 0) + h['km']
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:3]
    return ', '.join(r for r, km in top if km >= 5)


def search_routes(from_pt, to_pt, session=None):
    """Routes between two {'lat','lng'} points, shaped for the browser: a simplified path to draw,
    plus distance, time, the highways used and a label (Fastest / Shortest / Alternative).
    Near-identical alternatives are dropped."""
    budget = _Budget(SEARCH_BUDGET)
    routes = fetch_routes(from_pt['lat'], from_pt['lng'], to_pt['lat'], to_pt['lng'], budget=budget, session=session)
    out = []
    for r in routes:
        if any(abs(r['distance_km'] - o['distance_km']) < 0.3 and abs(r['duration_min'] - o['duration_min']) < 2 for o in out):
            continue
        poly = simplify(r['points'], PATH_TOLERANCE_M)
        out.append({'distance_km': r['distance_km'], 'duration_min': r['duration_min'],
                    'highways': r['highways'], 'via': _via(r['highways']), 'pace': r['pace'],
                    'geometry': [[round(a, 5), round(b, 5)] for a, b in poly]})
    shortest = min(range(len(out)), key=lambda i: out[i]['distance_km']) if out else None
    for i, r in enumerate(out):
        r['idx'] = i
        if i == 0 and shortest == 0:
            r['label'] = 'Fastest · Shortest' if len(out) > 1 else 'Route'
        elif i == 0:
            r['label'] = 'Fastest'
        elif i == shortest:
            r['label'] = 'Shortest'
        else:
            r['label'] = 'Alternative'
    return out


# --------------------------------------------------------------------------- Overpass

_Q_TOWNS = ('node["place"~"^(city|town)$"]{bb};',)
_Q_TOLLS = ('node["barrier"="toll_booth"]{bb};', 'node["highway"="toll_gantry"]{bb};')
# petrol pumps are mapped as points or as small areas — ask for both (areas via their centre)
_Q_FUEL = ('nwr["amenity"="fuel"]{bb};',)
# major landmarks: airports (IATA code), railway junctions, and anything notable enough to have a
# Wikidata entry (landmarks, historic sites, big industrial areas, dams) — the notability filter keeps
# the corridor's thousands of ordinary buildings out
_Q_LANDMARKS = ('nwr["aeroway"="aerodrome"]["iata"]{bb};',
                'nwr["railway"="station"]["name"~"Junction| Jn",i]{bb};',
                'nwr["tourism"="attraction"]["wikidata"]{bb};',
                'nwr["historic"]["wikidata"]{bb};',
                'nwr["landuse"="industrial"]["wikidata"]{bb};',
                'nwr["man_made"="dam"]["wikidata"]{bb};',
                'nwr["waterway"="dam"]["wikidata"]{bb};')
OVERPASS_GROUPS = {'towns': _Q_TOWNS, 'tolls': _Q_TOLLS, 'fuel': _Q_FUEL, 'landmarks': _Q_LANDMARKS}


def _overpass_query(boxes, groups=('towns', 'tolls', 'fuel', 'landmarks')):
    """One Overpass query for the chosen groups of features over every box. Asking for fewer groups
    makes a much lighter query — the public server answers a small one even when it is refusing a big one."""
    parts = []
    for s_, w_, n_, e_ in boxes:
        bb = f'({s_:.4f},{w_:.4f},{n_:.4f},{e_:.4f})'
        for g in groups:
            parts.extend(clause.format(bb=bb) for clause in OVERPASS_GROUPS[g])
    return '[out:json][timeout:20];(' + ''.join(parts) + ');out center;'


def fetch_nearby(boxes, budget=None, session=None, groups=('towns', 'tolls', 'fuel', 'landmarks')):
    """Raw Overpass elements (toll booths/gantries, cities, towns) inside the given boxes.
    Raises RouteLookupError if every Overpass server fails or the budget runs out."""
    budget = budget or _Budget(EXTRAS_BUDGET)
    post = (session or requests).post
    query = _overpass_query(boxes, groups)
    last_err = None
    # Main server, then the mirror, then the main server once more: its 504s ("busy") are usually
    # momentary, while the mirror often refuses a long route's query outright — so a second try on
    # the main one is worth more than a second mirror.
    for url in (OVERPASS_URLS[0], OVERPASS_URLS[-1], OVERPASS_URLS[0]):
        if budget.left() < 4:
            break
        try:
            r = post(url, data={'data': query}, headers=_headers(), timeout=budget.timeout_for(OVERPASS_TIMEOUT))
            if r.status_code == 429:
                raise RouteLookupError('The OpenStreetMap server that lists toll plazas and towns is busy right now (it limits how often it can be asked). Try again in a minute.')
            r.raise_for_status()
            seen, out = set(), []
            for el in r.json().get('elements', []):
                key = (el.get('type'), el.get('id'))
                if key in seen:
                    continue
                seen.add(key)
                if 'lat' not in el and isinstance(el.get('center'), dict):   # an area: use its centre
                    el['lat'], el['lon'] = el['center'].get('lat'), el['center'].get('lon')
                if el.get('lat') is None or el.get('lon') is None:
                    continue
                out.append(el)
            return out
        except RouteLookupError:
            raise
        except (requests.RequestException, ValueError) as e:
            last_err = e
    # say what actually came back ("HTTP 504"), not just the exception's class name
    why = ''
    if last_err is not None:
        resp = getattr(last_err, 'response', None)
        why = f' (HTTP {resp.status_code})' if resp is not None else f' ({last_err.__class__.__name__})'
    raise RouteLookupError('Toll plazas, petrol pumps and towns could not be fetched from OpenStreetMap right now' + why + '. Try again in a minute.')


def _highway_at(highways, km):
    ref = None
    for s in highways:
        if s['from_km'] <= km + 1e-6:
            ref = s['ref']
        else:
            break
    return ref


def _town_points(elements):
    out = []
    for el in elements:
        tags = el.get('tags') or {}
        if tags.get('place') in ('city', 'town'):
            name = (tags.get('name:en') or tags.get('name') or '').strip()
            if name:
                out.append((name, el['lat'], el['lon']))
    return out


def build_tolls(elements, poly, cum, highways, scale=1.0, town_pts=None):
    """Group raw toll booth/gantry nodes into plazas, ordered by distance from the start.

    A plaza is mapped as one node per lane (and often both directions), so nodes within 1.5 km of
    each other along the path are treated as one plaza; its position is their average. OpenStreetMap
    names few Indian plazas, so an unnamed one is described by the nearest town ("near Talcher")
    instead of an anonymous pin — `named` tells the UI which kind it is."""
    found = []
    for el in elements:
        tags = el.get('tags') or {}
        if tags.get('barrier') != 'toll_booth' and tags.get('highway') != 'toll_gantry':
            continue
        km, dist_m = project_on_path(poly, cum, el['lat'], el['lon'])
        if dist_m > TOLL_RADIUS_M:
            continue
        found.append({'km': km * scale, 'lat': el['lat'], 'lng': el['lon'],
                      'name': (tags.get('name:en') or tags.get('name') or '').strip(),
                      'gantry': tags.get('highway') == 'toll_gantry'})
    found.sort(key=lambda f: f['km'])
    groups = []
    for f in found:
        if groups and f['km'] - groups[-1][-1]['km'] <= 1.5:
            groups[-1].append(f)
        else:
            groups.append([f])
    tolls = []
    for g in groups:
        names = [x['name'] for x in g if x['name']]
        lat = sum(x['lat'] for x in g) / len(g)
        lng = sum(x['lng'] for x in g) / len(g)
        km = sum(x['km'] for x in g) / len(g)
        if names:
            name, named = max(set(names), key=names.count), True
        else:
            named = False
            near = min(town_pts or [], key=lambda t: haversine_km(lat, lng, t[1], t[2]), default=None)
            if near and haversine_km(lat, lng, near[1], near[2]) <= 25:
                name = f'Toll plaza near {near[0]}'
            else:
                name = 'Toll plaza'
        tolls.append({'name': name, 'named': named, 'lat': round(lat, 5), 'lng': round(lng, 5),
                      'km': round(km, 1), 'highway': _highway_at(highways, km),
                      'barrierless': all(x['gantry'] for x in g)})
    return tolls


def build_fuels(elements, poly, cum, highways, scale=1.0):
    """Petrol pumps along the path, in driving order. A station mapped twice (a point and an area)
    is counted once. Private/closed ones are skipped."""
    found = []
    for el in elements:
        tags = el.get('tags') or {}
        if tags.get('amenity') != 'fuel' or tags.get('access') in ('private', 'no') or tags.get('disused') == 'yes':
            continue
        km, dist_m = project_on_path(poly, cum, el['lat'], el['lon'])
        if dist_m > FUEL_RADIUS_M:
            continue
        name = (tags.get('name:en') or tags.get('name') or tags.get('brand') or tags.get('operator') or '').strip()
        found.append({'km': km * scale, 'lat': el['lat'], 'lng': el['lon'], 'name': name})
    found.sort(key=lambda f: f['km'])
    kept = []
    for f in found:
        if any(abs(f['km'] - k['km']) < 0.15 and haversine_km(f['lat'], f['lng'], k['lat'], k['lng']) < 0.12 for k in kept[-4:]):
            continue
        kept.append(f)
    return [{'name': f['name'] or 'Petrol pump', 'named': bool(f['name']), 'lat': round(f['lat'], 5), 'lng': round(f['lng'], 5),
             'km': round(f['km'], 1), 'highway': _highway_at(highways, f['km'])} for f in kept]


_PLACE_LABEL = {'city': 'City', 'town': 'Town', 'airport': 'Airport', 'junction': 'Railway junction',
                'attraction': 'Landmark', 'industrial': 'Industrial area', 'dam': 'Dam'}
_PLACE_BASE = {'city': 100, 'town': 60, 'airport': 70, 'junction': 45, 'attraction': 42, 'industrial': 40, 'dam': 38}


def _place_kind(tags):
    """What kind of major place this OpenStreetMap element is, or None if it isn't one."""
    if tags.get('place') in ('city', 'town'):
        return tags['place']
    if tags.get('aeroway') == 'aerodrome' and tags.get('iata'):
        return 'airport'
    name = (tags.get('name:en') or tags.get('name') or '')
    if tags.get('railway') == 'station' and re.search(r'\b(junction|jn)\b', name, re.I):
        return 'junction'
    if tags.get('wikidata') or tags.get('wikipedia'):
        if tags.get('tourism') == 'attraction' or tags.get('historic'):
            return 'attraction'
        if tags.get('landuse') == 'industrial':
            return 'industrial'
        if tags.get('man_made') == 'dam' or tags.get('waterway') == 'dam':
            return 'dam'
    return None


def _importance(tags, kind):
    """How major a place is, higher = bigger: its kind, then population, being a capital / district seat,
    and being notable enough to have a Wikidata/Wikipedia entry."""
    score = _PLACE_BASE[kind]
    try:
        pop = int(re.sub(r'\D', '', tags.get('population', '')) or 0)
    except ValueError:
        pop = 0
    score += 40 if pop >= 1_000_000 else 30 if pop >= 500_000 else 20 if pop >= 100_000 else 12 if pop >= 50_000 else 6 if pop >= 10_000 else 0
    cap = str(tags.get('capital', ''))
    score += 35 if cap in ('yes', '4') else 20 if cap in ('5', '6') else 0
    if tags.get('wikidata') or tags.get('wikipedia'):
        score += 12
    return score


def build_places(elements, poly, cum, total_km, scale=1.0):
    """Every major place near the route, ranked, in driving order: cities, towns, and landmarks worth naming
    (airports, railway junctions, notable landmarks, big industrial areas, dams). NOT thinned for the
    screen — select_major() does that, because how many fit depends on the layout. Only de-duplicated: a
    junction or airport right beside a city is that city, so the higher-ranked place wins within ~4 km."""
    cands = []
    for el in elements:
        tags = el.get('tags') or {}
        kind = _place_kind(tags)
        name = (tags.get('name:en') or tags.get('name') or '').strip()
        if not kind or not name:
            continue
        km, dist_m = project_on_path(poly, cum, el['lat'], el['lon'])
        limit = TOWN_RADIUS_M if kind in ('city', 'town') else AIRPORT_RADIUS_M if kind == 'airport' else LANDMARK_RADIUS_M
        if dist_m > limit:
            continue
        cands.append({'name': name, 'kind': kind, 'label': _PLACE_LABEL[kind], 'score': _importance(tags, kind), 'km': km * scale,
                      'lat': round(el['lat'], 5), 'lng': round(el['lon'], 5), 'off_route_km': round(dist_m / 1000.0, 1)})
    cands.sort(key=lambda c: -c['score'])
    kept = []
    for c in cands:
        if c['km'] < 3 or total_km - c['km'] < 3:
            continue                         # that's the start or the end itself
        if any(abs(c['km'] - k['km']) < 4 or (c['name'].lower() == k['name'].lower() and abs(c['km'] - k['km']) < 25) for k in kept):
            continue
        kept.append(c)
    kept = sorted(kept[:MAX_CANDIDATES], key=lambda c: c['km'])
    for c in kept:
        c['km'] = round(c['km'], 1)
    return kept


def stop_budget(total_km, cap):
    """How many key locations to show: about one per KM_PER_STOP km, never more than the screen holds
    (`cap`), and none at all for a very short hop."""
    if total_km < 25:
        return 0
    return max(1, min(cap, round(total_km / KM_PER_STOP)))


def select_major(places, total_km, budget):
    """The `budget` most major places, spread along the route: biggest first, each at least `min_gap` km
    from the ones already chosen (so a long run of towns can't crowd out a big city further on), keeping
    clear of the start and end. If that leaves the list short (few places, or all bunched up), the gap is
    halved to fill it. Returns indices into `places`, in driving order."""
    if budget <= 0 or not places:
        return []
    edge = max(4.0, total_km * 0.04)
    pool = [i for i, c in enumerate(places) if edge <= c['km'] <= total_km - edge]
    pool.sort(key=lambda i: -places[i]['score'])
    chosen = []
    min_gap = max(8.0, total_km / (budget * 2.0))
    for gap in (min_gap, min_gap / 2):
        for i in pool:
            if len(chosen) >= budget:
                break
            if i not in chosen and all(abs(places[i]['km'] - places[j]['km']) >= gap for j in chosen):
                chosen.append(i)
    return sorted(chosen, key=lambda i: places[i]['km'])


def route_extras(poly, distance_km, highways, session=None):
    """Toll plazas, petrol pumps and major towns along one chosen route, from its simplified path (the same
    geometry search_routes() handed the browser, sent back — so this stays stateless across
    web workers). Returns {'tolls','towns' (ranked major places),'picks','fuels','extras_ok','note'}; a failed Overpass call is reported,
    not raised, so the caller can still show the route itself."""
    budget = _Budget(EXTRAS_BUDGET)
    if len(poly) < 2:
        return {'tolls': [], 'towns': [], 'picks': {m: [] for m in SCREEN_STOP_CAPS}, 'fuels': [], 'extras_ok': False, 'note': 'No path to look along.'}
    cum = cumulative_km(poly)
    scale = (distance_km / cum[-1]) if cum[-1] else 1.0
    try:
        elements = fetch_nearby(route_bboxes(poly, cum), budget=budget, session=session)
    except RouteLookupError as e:
        return {'tolls': [], 'towns': [], 'picks': {m: [] for m in SCREEN_STOP_CAPS}, 'fuels': [], 'extras_ok': False, 'note': str(e)}
    places = build_places(elements, poly, cum, distance_km, scale=scale)
    return {'tolls': build_tolls(elements, poly, cum, highways, scale=scale, town_pts=_town_points(elements)),
            'towns': places,
            # which of `towns` (indices) each layout should show — chosen here so it is tested, sent for all
            # three sizes so rotating a phone or resizing a window re-fits the list with no new lookup
            'picks': {mode: select_major(places, distance_km, stop_budget(distance_km, cap)) for mode, cap in SCREEN_STOP_CAPS.items()},
            'fuels': build_fuels(elements, poly, cum, highways, scale=scale),
            'extras_ok': True, 'note': None}


# --------------------------------------------------------------------------- the three parts of a route's extras
# The browser asks for them one after another, so the towns (instant, local) appear first and the two
# Overpass parts fill in as they arrive; a failure in one never hides the others.

def _empty_picks():
    return {m: [] for m in SCREEN_STOP_CAPS}


def _picks_for(places, distance_km):
    return {mode: select_major(places, distance_km, stop_budget(distance_km, cap)) for mode, cap in SCREEN_STOP_CAPS.items()}


def route_places(poly, distance_km):
    """Cities and towns along one route, from the built-in list — no network at all. Returns
    {'towns', 'picks', 'extras_ok', 'note'} in the same shape route_extras() uses."""
    if len(poly) < 2:
        return {'towns': [], 'picks': _empty_picks(), 'extras_ok': False, 'note': 'No path to look along.'}
    cum = cumulative_km(poly)
    scale = (distance_km / cum[-1]) if cum[-1] else 1.0
    places = build_places(local_place_elements(route_bboxes(poly, cum)), poly, cum, distance_km, scale=scale)
    return {'towns': places, 'picks': _picks_for(places, distance_km), 'extras_ok': True, 'note': None}


def route_tolls(poly, distance_km, highways, session=None):
    """Toll plazas along one route (Overpass, tolls only — the lightest query). An unnamed plaza is
    described by the nearest built-in town. A failed lookup is reported in `note`, never raised."""
    if len(poly) < 2:
        return {'tolls': [], 'extras_ok': False, 'note': 'No path to look along.'}
    cum = cumulative_km(poly)
    scale = (distance_km / cum[-1]) if cum[-1] else 1.0
    boxes = route_bboxes(poly, cum)
    try:
        elements = fetch_nearby(boxes, budget=_Budget(PART_BUDGET), session=session, groups=('tolls',))
    except RouteLookupError as e:
        return {'tolls': [], 'extras_ok': False, 'note': str(e).replace('Toll plazas, petrol pumps and towns', 'Toll plazas')}
    towns = _town_points(local_place_elements(boxes))
    return {'tolls': build_tolls(elements, poly, cum, highways, scale=scale, town_pts=towns), 'extras_ok': True, 'note': None}


def route_fuel(poly, distance_km, highways, session=None):
    """Petrol pumps and landmarks (airports, junctions, notable sites) along one route (Overpass). Landmarks
    are merged with the built-in towns, so this also returns the re-ranked town list and picks."""
    if len(poly) < 2:
        return {'fuels': [], 'towns': [], 'picks': _empty_picks(), 'extras_ok': False, 'note': 'No path to look along.'}
    cum = cumulative_km(poly)
    scale = (distance_km / cum[-1]) if cum[-1] else 1.0
    boxes = route_bboxes(poly, cum)
    try:
        elements = fetch_nearby(boxes, budget=_Budget(PART_BUDGET), session=session, groups=('fuel', 'landmarks'))
    except RouteLookupError as e:
        return {'fuels': [], 'towns': [], 'picks': _empty_picks(), 'extras_ok': False,
                'note': str(e).replace('Toll plazas, petrol pumps and towns', 'Petrol pumps and landmarks')}
    places = build_places(elements + local_place_elements(boxes), poly, cum, distance_km, scale=scale)
    return {'fuels': build_fuels(elements, poly, cum, highways, scale=scale), 'towns': places,
            'picks': _picks_for(places, distance_km), 'extras_ok': True, 'note': None}
