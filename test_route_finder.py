"""Regression test for the live Route Finder (providers/osm_routes.py + /route-finder/* in app.py).

Two halves, both with the network hard-blocked (requests.get/post raise) so a bug can never hit the
free public OpenStreetMap servers, and with every OSM answer faked:

  1. Provider: place-name cleanup, highway refs, path geometry (simplify / project / bounding boxes),
     toll plazas, petrol pumps, towns, OSRM parsing (alternatives, elapsed-time table), Photon
     suggestions, Overpass failure handling.
  2. Server: login gating, input validation, caching, concurrency guards, pinned places, the
     "your own trips on this route" lookup, and the page markup of both Route Analytics surfaces.

Runs against a disposable copy of fleet.db; the real file is never touched.

Usage:
    python3 test_route_finder.py
"""
import json
import math
import os
import re
import shutil
import sys
import tempfile

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TMP_DIR = tempfile.mkdtemp(prefix='route_finder_test_')
TEST_DB = os.path.join(TMP_DIR, 'test.db')

if not os.path.exists(REAL_DB):
    print(f"No fleet.db found at {REAL_DB} -- aborting.")
    sys.exit(1)
shutil.copyfile(REAL_DB, TEST_DB)
os.environ['DATABASE_PATH'] = TEST_DB
sys.path.insert(0, REPO_DIR)

import requests  # noqa: E402


def _blocked(*a, **k):
    raise RuntimeError('network blocked in test')


requests.get = requests.post = _blocked

import app as a  # noqa: E402
from providers import osm_routes as o  # noqa: E402

assert a.DB == TEST_DB, 'app is not pointed at the disposable copy'

_ok = _fail = 0


def check(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"[OK]   {name}")
    else:
        _fail += 1
        print(f"[FAIL] {name}")


class R:
    """A fake `requests` response."""
    def __init__(self, j, code=200):
        self._j, self.status_code = j, code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._j


# =========================================================================== 1. PROVIDER

print("\n--- provider: text and highway refs")
check("search text: doubled Jharsuguda", o.clean_search_text("Jharsuguda, Odisha Jharsuguda, Odisha Odisha") == "Jharsuguda, Odisha")
check("search text: RSP RSP null", o.clean_search_text("RSP RSP null") == "RSP")
check("search text: empty", o.clean_search_text(None) == "")
for raw, want in [("NH 16", "NH 16"), ("NH16", "NH 16"), ("NH-16", "NH 16"), ("NH 49;AH 45", "NH 49"),
                  ("AH 45;NH 49", "NH 49"), ("SH 10", "SH 10"), (None, None)]:
    check(f"ref {raw!r} -> {want!r}", o.normalize_ref(raw) == want)

print("\n--- provider: path geometry")
sparse = [[20.0, 85.0], [21.0, 85.0]]            # a straight ~111 km N-S path as only 2 vertices
scum = o.cumulative_km(sparse)
check("cumulative ~111.2 km", abs(scum[-1] - 111.2) < 0.5)
km, d = o.project_on_path(sparse, scum, 20.5, 85.0005)
check("project on a SPARSE path: ~55.6 km along, ~52 m off", abs(km - 55.6) < 0.3 and 40 < d < 65)
km0, d0 = o.project_on_path(sparse, scum, 19.9, 85.0)
check("project: point before the start clamps to km 0", km0 == 0.0 and d0 > 10000)
wig = [[20.0 + i * 0.001, 85.0 + (0.00001 if i % 2 else 0)] for i in range(1001)]
check("simplify: a wiggle collapses to 2 points", len(o.simplify(wig, 25)) == 2)
corner = [[20.0 + i * 0.001, 85.0] for i in range(500)] + [[20.5, 85.0 + j * 0.001] for j in range(1, 500)]
check("simplify keeps a real corner", 3 <= len(o.simplify(corner, 25)) <= 4)
big = [[20 + i * 1e-4, 85 + math.sin(i / 500) * 0.01] for i in range(40000)]
check("simplify: 40k points runs and shrinks", len(o.simplify(big, 25)) < 3000)
boxes = o.route_bboxes(sparse, scum)
check("bounding boxes cover both ends", boxes[0][0] < 20.0 and boxes[-1][2] > 21.0)

print("\n--- provider: toll plazas, petrol pumps, towns")
hw = [{'ref': 'NH 49', 'km': 50.0, 'from_km': 0.0}, {'ref': 'NH 16', 'km': 61.2, 'from_km': 50.0}]
els = [
    {'id': 1, 'lat': 20.300, 'lon': 85.0001, 'tags': {'barrier': 'toll_booth', 'name': 'Sambalpur Toll Plaza'}},
    {'id': 2, 'lat': 20.301, 'lon': 85.0002, 'tags': {'barrier': 'toll_booth', 'name': 'Sambalpur Toll Plaza'}},
    {'id': 3, 'lat': 20.302, 'lon': 85.0001, 'tags': {'barrier': 'toll_booth'}},
    {'id': 4, 'lat': 20.800, 'lon': 85.0000, 'tags': {'highway': 'toll_gantry'}},
    {'id': 5, 'lat': 20.500, 'lon': 85.0200, 'tags': {'barrier': 'toll_booth', 'name': 'Decoy on parallel road'}},
    {'id': 6, 'lat': 20.9, 'lon': 85.0, 'tags': {'amenity': 'cafe'}},
    {'id': 7, 'lat': 20.2, 'lon': 85.0, 'tags': {'place': 'city', 'name': 'Bigcity', 'population': '900000'}},
    {'id': 8, 'lat': 20.25, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'Near Town A', 'population': '20000'}},
    {'id': 9, 'lat': 20.6, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'Mid Town', 'population': '50000'}},
    {'id': 10, 'lat': 20.65, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'Mid Town Neighbour', 'population': '8000'}},
    {'id': 11, 'lat': 20.001, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'AtStart'}},
    {'id': 12, 'lat': 20.9, 'lon': 85.0, 'tags': {'place': 'village', 'name': 'Not major'}},
    {'id': 13, 'lat': 20.8, 'lon': 85.0, 'tags': {'place': 'town'}},
    {'id': 14, 'lat': 20.82, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'Gantry Town', 'population': '1000'}},
]
tp = o._town_points(els)
tolls = o.build_tolls(els, sparse, scum, hw, scale=1.0, town_pts=tp)
check("tolls: 3 lane nodes -> 1 plaza + the gantry; the parallel-road decoy is dropped", len(tolls) == 2)
check("tolls: named plaza at ~33 km on NH 49", tolls[0]['name'] == 'Sambalpur Toll Plaza' and abs(tolls[0]['km'] - 33.4) < 1.5
      and tolls[0]['highway'] == 'NH 49' and tolls[0]['named'])
check("tolls: unnamed gantry is labelled by its nearest town, barrier-free, on NH 16",
      tolls[1]['name'] == 'Toll plaza near Gantry Town' and tolls[1]['named'] is False and tolls[1]['barrierless'] and tolls[1]['highway'] == 'NH 16')
check("tolls: scale factor applied to km", abs(o.build_tolls(els, sparse, scum, hw, scale=1.01, town_pts=tp)[0]['km'] - tolls[0]['km'] * 1.01) < 0.2)
places = o.build_places(els, sparse, scum, scum[-1])
check("places: cities and towns kept in driving order; the start-adjacent, unnamed and village ones dropped",
      [t['name'] for t in places] == ['Bigcity', 'Near Town A', 'Mid Town', 'Mid Town Neighbour', 'Gantry Town'])
check("places: each carries a label, a score and its km", places[0]['label'] == 'City' and places[1]['label'] == 'Town' and all(isinstance(t['score'], int) and 'km' in t for t in places))
check("places: a city outranks a town, and a bigger town outranks a smaller one (population)",
      places[0]['score'] > places[2]['score'] > places[1]['score'] and places[2]['score'] > places[3]['score'])

# landmarks: only the notable ones, ranked, absorbed into a neighbouring city, with their own distance limits
lm = [
    {'id': 21, 'lat': 20.200, 'lon': 85.0, 'tags': {'place': 'city', 'name': 'Bigcity', 'population': '900000'}},
    {'id': 22, 'lat': 20.2005, 'lon': 85.0, 'tags': {'railway': 'station', 'name': 'Bigcity Junction', 'wikidata': 'Q1'}},      # beside the city: absorbed
    {'id': 23, 'lat': 20.40, 'lon': 85.05, 'tags': {'aeroway': 'aerodrome', 'name': 'Big Airport', 'iata': 'BIG'}},             # ~5.5 km off the route: allowed for an airport
    {'id': 24, 'lat': 20.45, 'lon': 85.05, 'tags': {'aeroway': 'aerodrome', 'name': 'Strip', 'icao': 'XXXX'}},                  # no IATA code: not major
    {'id': 25, 'lat': 20.60, 'lon': 85.0, 'tags': {'railway': 'station', 'name': 'Midtown Jn'}},                              # a junction
    {'id': 26, 'lat': 20.62, 'lon': 85.0, 'tags': {'railway': 'station', 'name': 'Tiny Halt'}},                               # an ordinary halt
    {'id': 27, 'lat': 20.80, 'lon': 85.02, 'tags': {'tourism': 'attraction', 'name': 'Famous Temple', 'wikidata': 'Q2'}},     # ~2.2 km off
    {'id': 28, 'lat': 20.85, 'lon': 85.0, 'tags': {'tourism': 'attraction', 'name': 'Roadside Statue'}},                      # no wikidata: not notable
    {'id': 29, 'lat': 20.70, 'lon': 85.06, 'tags': {'tourism': 'attraction', 'name': 'Far Landmark', 'wikidata': 'Q3'}},      # ~6.6 km off: too far for a landmark
    {'id': 30, 'lat': 20.50, 'lon': 85.0, 'tags': {'landuse': 'industrial', 'name': 'Steel Works', 'wikidata': 'Q4'}},
    {'id': 31, 'lat': 20.52, 'lon': 85.0, 'tags': {'landuse': 'industrial', 'name': 'Some Yard'}},                            # not notable
    {'id': 32, 'lat': 20.90, 'lon': 85.0, 'tags': {'man_made': 'dam', 'name': 'Great Dam', 'wikidata': 'Q5'}},
]
lp = o.build_places(lm, sparse, scum, scum[-1])
byname = {c['name']: c for c in lp}
check("landmarks: only notable ones are kept (airport with IATA, junction, wikidata landmark/industrial/dam)",
      set(byname) == {'Bigcity', 'Big Airport', 'Midtown Jn', 'Famous Temple', 'Steel Works', 'Great Dam'})
check("landmarks: a junction beside a city is absorbed by the city", 'Bigcity Junction' not in byname)
check("landmarks: kinds are labelled", byname['Big Airport']['label'] == 'Airport' and byname['Midtown Jn']['label'] == 'Railway junction'
      and byname['Famous Temple']['label'] == 'Landmark' and byname['Steel Works']['label'] == 'Industrial area' and byname['Great Dam']['label'] == 'Dam')
check("landmarks: a city outranks every landmark here; an airport outranks a junction",
      byname['Bigcity']['score'] > max(c['score'] for c in lp if c['name'] != 'Bigcity') and byname['Big Airport']['score'] > byname['Midtown Jn']['score'])
check("landmarks: the ones beside the road stay in driving order", [c['km'] for c in lp] == sorted(c['km'] for c in lp))
check("importance: capital / wikidata / population all add to the score",
      o._importance({'place': 'town', 'population': '600000', 'capital': 'yes', 'wikidata': 'Q'}, 'town') > o._importance({'place': 'town'}, 'town') + 70)

# the rule for how many fit the screen
check("budget: a very short hop shows none", o.stop_budget(10, 6) == 0)
check("budget: about one per 40 km", o.stop_budget(125, 10) == 3 and o.stop_budget(309, 10) == 8)
check("budget: never more than the screen holds", o.stop_budget(800, 6) == 6 and o.stop_budget(800, 8) == 8 and o.stop_budget(800, 10) == 10)
check("budget: a mid-length route still gets at least one", o.stop_budget(30, 6) == 1)
many = [{'name': f'P{i}', 'km': 10.0 * i, 'score': 50 + (i * 7) % 40} for i in range(1, 40)]            # 39 places over ~400 km
many.append({'name': 'HugeCity', 'km': 223.0, 'score': 200})
many.sort(key=lambda c: c['km'])
for cap in (6, 8, 10):
    sel = o.select_major(many, 400, o.stop_budget(400, cap))
    check(f"select (cap {cap}): exactly the screen's budget, in driving order", len(sel) == cap and sel == sorted(sel))
    gaps = [many[b]['km'] - many[a]['km'] for a, b in zip(sel, sel[1:])]
    check(f"select (cap {cap}): spread along the route (no two closer than the gap), clear of the start/end", min(gaps) >= max(8.0, 400 / (cap * 2.0)) / 2 and many[sel[0]]['km'] >= 16 and many[sel[-1]]['km'] <= 384)
check("select: the biggest city on a long route is always shown", any(many[i]['name'] == 'HugeCity' for i in o.select_major(many, 400, 6)))
few = [{'name': 'A', 'km': 100.0, 'score': 90}, {'name': 'B', 'km': 104.0, 'score': 80}]
check("select: two places 4 km apart are one stop, not two (a short list is fine when that's all there is)", len(o.select_major(few, 200, 3)) == 1)
check("select: nothing to choose from / zero budget -> empty", o.select_major([], 100, 3) == [] and o.select_major(many, 400, 0) == [])


def fuel(i, lat, lon, **tags):
    t = {'amenity': 'fuel'}
    t.update(tags)
    return {'type': 'node', 'id': i, 'lat': lat, 'lon': lon, 'tags': t}


fels = [fuel(1, 20.200, 85.0003, name='Indian Oil'),
        fuel(2, 20.2001, 85.0004, name='Indian Oil'),                      # the same station mapped twice
        fuel(3, 20.400, 85.0004, brand='HP'),
        fuel(4, 20.500, 85.0002),                                          # unnamed
        fuel(5, 20.600, 85.0003, name='Private depot', access='private'),
        fuel(6, 20.700, 85.0003, name='Closed', disused='yes'),
        fuel(7, 20.800, 85.0200, name='Far away'),                         # ~2 km off the path
        {'type': 'node', 'id': 8, 'lat': 20.9, 'lon': 85.0, 'tags': {'amenity': 'cafe'}}]
fu = o.build_fuels(fels, sparse, scum, hw, scale=1.0)
check("petrol pumps: duplicate merged; private/disused/far/non-fuel dropped -> 3", len(fu) == 3)
check("petrol pumps: driving order, name -> brand -> default", [f['name'] for f in fu] == ['Indian Oil', 'HP', 'Petrol pump'] and fu[0]['named'] and not fu[2]['named'])
check("petrol pumps: km along the path and highway", abs(fu[0]['km'] - 22.2) < 0.4 and fu[0]['highway'] == 'NH 49')
q = o._overpass_query([(20.0, 85.0, 21.0, 85.1)])
check("overpass query asks for petrol pumps (points + areas) and area centres", 'nwr["amenity"="fuel"]' in q and q.rstrip().endswith('out center;'))
check("overpass query also asks for major landmarks (airports, junctions, notable landmarks/industrial/dams)",
      all(k in q for k in ('"aeroway"="aerodrome"]["iata"]', 'Junction| Jn', '"tourism"="attraction"]["wikidata"]', '"historic"]["wikidata"]', '"landuse"="industrial"]["wikidata"]', '"man_made"="dam"]["wikidata"]')))

print("\n--- provider: OSRM parsing")


def rt(dist, dur, coords, steps):
    return {'distance': dist, 'duration': dur, 'geometry': {'coordinates': coords}, 'legs': [{'steps': steps}]}


c1 = [[85.0, 20.0], [85.0, 20.5], [85.5, 20.5]]
osrm = {'code': 'Ok', 'routes': [
    rt(123400, 7380, c1, [{'distance': 50000, 'ref': 'NH 49'}, {'distance': 20000, 'ref': 'NH-49'}, {'distance': 50000, 'ref': 'NH 16;AH 45'}, {'distance': 3400}]),
    rt(118000, 8100, [[85.0, 20.0], [85.3, 20.2], [85.5, 20.5]], [{'distance': 118000, 'ref': 'SH 10'}]),
    rt(123500, 7390, c1, [{'distance': 123500, 'ref': 'NH 49'}]),          # near-duplicate of route 1
]}


class SessOsrm:
    def get(self, url, **k):
        assert 'alternatives' in k['params']
        return R(osrm)


routes = o.fetch_routes(20, 85, 20.5, 85.5, session=SessOsrm())
check("fetch_routes: 3 parsed", len(routes) == 3 and routes[0]['distance_km'] == 123.4)
check("route 1: steps merged by ref, with km and start km",
      [(h['ref'], h['km'], h['from_km']) for h in routes[0]['highways']] == [('NH 49', 70.0, 0.0), ('NH 16', 50.0, 70.0), (None, 3.4, 120.0)])
check("route 1: points are [lat, lng]", routes[0]['points'][0] == [20.0, 85.0])
res = o.search_routes({'lat': 20, 'lng': 85}, {'lat': 20.5, 'lng': 85.5}, session=SessOsrm())
check("search_routes: near-duplicate dropped -> 2 routes, labelled Fastest + Shortest",
      len(res) == 2 and res[0]['label'] == 'Fastest' and res[1]['label'] == 'Shortest')
check("search_routes: 'via' text and geometry", res[0]['via'] == 'NH 49, NH 16' and res[1]['via'] == 'SH 10' and len(res[0]['geometry']) >= 2)


class SessNoRoute:
    def get(self, url, **k):
        return R({'code': 'NoRoute', 'routes': []})


try:
    o.fetch_routes(20, 85, 20.5, 85.5, session=SessNoRoute())
    check("NoRoute raises", False)
except o.RouteLookupError:
    check("NoRoute raises RouteLookupError", True)

steps = [{'distance': 400, 'duration': 60, 'ref': 'NH 49'}, {'distance': 700, 'duration': 50, 'ref': 'NH 49'},
         {'distance': 30000, 'duration': 1500, 'ref': 'NH 49'}, {'distance': 20000, 'duration': 1200, 'ref': 'NH 16'},
         {'distance': 300, 'duration': 40}]
pr = o._parse_route(rt(51400, 2850, [[85.0, 20.0], [85.5, 20.5]], steps))
check("pace: starts at 0 km / 0 min", pr['pace'][0] == [0.0, 0.0])
check("pace: ends at the route's total km and minutes", pr['pace'][-1][0] == 51.4 and abs(pr['pace'][-1][1] - 47.5) < 0.2)
check("pace: strictly increasing in km, never decreasing in minutes", all(b[0] > a[0] and b[1] >= a[1] for a, b in zip(pr['pace'], pr['pace'][1:])))
check("pace: thinned to ~1 km spacing", len(pr['pace']) <= 5)
check("search_routes hands the browser the pace table", all(r['pace'][0] == [0.0, 0.0] for r in res))

print("\n--- provider: Overpass outcomes")


class SessOverpass:
    def post(self, url, **k):
        assert 'toll_booth' in k['data']['data']
        return R({'elements': els + fels})


ex = o.route_extras(sparse, 111.2, hw, session=SessOverpass())
check("extras ok: tolls, petrol pumps and ranked places all returned", ex['extras_ok'] and len(ex['tolls']) == 2 and len(ex['fuels']) == 3 and len(ex['towns']) >= 2)
check("extras: a pick-list for each screen size, within its cap, indices valid and in driving order",
      set(ex['picks']) == {'sm', 'md', 'lg'} and all(len(v) <= o.SCREEN_STOP_CAPS[m] and v == sorted(v) and all(0 <= i < len(ex['towns']) for i in v) for m, v in ex['picks'].items()))
check("extras: a bigger screen never shows fewer places than a smaller one", len(ex['picks']['sm']) <= len(ex['picks']['md']) <= len(ex['picks']['lg']))


class Sess429:
    def post(self, url, **k):
        return R({}, 429)


class SessDown:
    def post(self, url, **k):
        raise requests.RequestException('boom')


for cls, word in ((Sess429, 'busy'), (SessDown, None)):
    e2 = o.route_extras(sparse, 111.2, hw, session=cls())
    check(f"extras {cls.__name__}: reported, not raised; nothing invented",
          e2['extras_ok'] is False and e2['tolls'] == [] and e2['towns'] == [] and e2['fuels'] == [] and all(v == [] for v in e2['picks'].values()) and e2['note'] and (word is None or word in e2['note']))
check("extras: empty path handled", o.route_extras([], 0, [])['extras_ok'] is False and o.route_extras([], 0, [])['fuels'] == [])


class SessFlaky:
    """Main server 504, mirror 500, then the main server answers on the retry."""
    def __init__(self):
        self.calls = []

    def post(self, url, **k):
        self.calls.append(url)
        return R({'elements': els + fels}) if len(self.calls) == 3 else R({}, 504 if len(self.calls) == 1 else 500)


flaky = SessFlaky()
ex3 = o.route_extras(sparse, 111.2, hw, session=flaky)
check("Overpass: main 504 -> mirror 500 -> main retried and answered (no needless failure)",
      ex3['extras_ok'] and len(flaky.calls) == 3 and flaky.calls[0] == flaky.calls[2] != flaky.calls[1] and len(ex3['fuels']) == 3)


class SessArea:
    def post(self, url, **k):
        return R({'elements': [
            {'type': 'way', 'id': 5, 'center': {'lat': 20.3, 'lon': 85.0001}, 'tags': {'amenity': 'fuel', 'name': 'Area pump'}},
            {'type': 'node', 'id': 5, 'lat': 20.31, 'lon': 85.0, 'tags': {'barrier': 'toll_booth'}},     # same number, different type
            {'type': 'way', 'id': 6, 'tags': {'amenity': 'fuel'}},                                          # no centre: dropped
            {'type': 'node', 'id': 7, 'lat': 20.4, 'lon': 85.0, 'tags': {'place': 'town', 'name': 'T'}}]})


els2 = o.fetch_nearby([(20.0, 85.0, 21.0, 85.1)], session=SessArea())
check("an area's centre becomes lat/lon; centre-less dropped; same id of another type kept",
      len(els2) == 3 and els2[0]['lat'] == 20.3 and els2[0]['lon'] == 85.0001)

print("\n--- provider: Nominatim + Photon")
nom = [{'lat': '28.6', 'lon': '77.2', 'display_name': 'Delhi RSP'}, {'lat': '22.25', 'lon': '84.85', 'display_name': 'RSP, Rourkela'}]


class SessNom:
    def get(self, url, **k):
        return R(list(nom))


o._gate_last['nominatim'] = 0
cands = o.geocode_candidates('RSP', bias_center=(22.2, 84.9), session=SessNom())
check("geocode candidates: nearest to the bias first, all returned", len(cands) == 2 and cands[0]['display_name'] == 'RSP, Rourkela')
nom.clear()
check("geocode: no results -> None / []", o.geocode('Nowhere', session=SessNom()) is None and o.geocode_candidates('Nowhere', session=SessNom()) == [])


def feat(name, key, val, lat, lon, cc='IN', state='Odisha', **kw):
    pr = {'name': name, 'osm_key': key, 'osm_value': val, 'countrycode': cc, 'state': state}
    pr.update(kw)
    return {'properties': pr, 'geometry': {'coordinates': [lon, lat]}}


photon = {'features': [
    feat('Rourkela', 'boundary', 'administrative', 22.254, 84.858),
    feat('Rourkela', 'place', 'city', 22.231, 84.868, district='Raurkela (M)'),
    feat('Rourkela Junction', 'railway', 'station', 22.228, 84.863, city='Raurkela (M)'),
    feat('Rourkela Steel Plant', 'landuse', 'industrial', 22.215, 84.869, district='Raurkela (M)'),
    feat('Rourkela Airport', 'aeroway', 'aerodrome', 22.255, 84.816, county='Chhend'),
    feat('Rourkela Road', 'highway', 'trunk', 22.246, 84.969),
    feat('Rourkela Pakistan', 'place', 'town', 34.3, 71.6, cc='PK', state='Khyber Pakhtunkhwa'),
    {'properties': {'osm_key': 'place', 'countrycode': 'IN'}, 'geometry': {'coordinates': [84, 22]}},
    {'properties': {'name': 'Broken', 'countrycode': 'IN'}, 'geometry': {}},
]}
seen = {}


class SessPhoton:
    def get(self, url, **k):
        seen.update(k['params'])
        seen['url'] = url
        return R(photon)


o._gate_last['photon'] = 0
sug = o.suggest_places('rourk', session=SessPhoton())
check("suggest: asks Photon, India only", seen['url'] == o.PHOTON_URL and seen['bbox'] == o.INDIA_BBOX and seen['q'] == 'rourk')
check("suggest: non-India, nameless and coordinate-less hits dropped", all(r['name'] not in ('Rourkela Pakistan', 'Broken') for r in sug))
check("suggest: the boundary twin is replaced by the real city", [r['name'] for r in sug].count('Rourkela') == 1 and sug[0]['kind'] == 'City')
kinds = {r['name']: r['kind'] for r in sug}
check("suggest: place kinds labelled", kinds['Rourkela Junction'] == 'Railway station' and kinds['Rourkela Steel Plant'] == 'Industrial area'
      and kinds['Rourkela Airport'] == 'Airport' and kinds['Rourkela Road'] == 'Road')
check("suggest: label, detail, display_name, coordinates", sug[0]['label'] == 'Rourkela, Odisha' and sug[0]['detail'] == 'Raurkela (M), Odisha'
      and sug[0]['display_name'] == 'Rourkela, Raurkela (M), Odisha, India' and sug[0]['lat'] == 22.231 and sug[0]['lng'] == 84.868)
check("suggest: under 3 letters -> [] without any request", o.suggest_places('ro', session=object()) == [] and o.suggest_places('  ', session=object()) == [])
seen.clear()
o._gate_last['photon'] = 0
o.suggest_places('tangi', bias_center=(22.2, 84.9), session=SessPhoton())
check("suggest: bias passed as lat/lon", seen.get('lat') == 22.2 and seen.get('lon') == 84.9)


class SessMany:
    def get(self, url, **k):
        return R({'features': [feat(f'Place {i}', 'place', 'village', 20 + i, 85) for i in range(20)]})


o._gate_last['photon'] = 0
check("suggest: capped at the limit", len(o.suggest_places('place', session=SessMany(), limit=7)) == 7)


class SessBusy:
    def get(self, url, **k):
        return R({}, 429)


o._gate_last['photon'] = 0
try:
    o.suggest_places('rourk', session=SessBusy())
    check("suggest: 429 raises", False)
except o.RouteLookupError as e:
    check("suggest: 429 -> a friendly 'busy' error", 'busy' in str(e))


class SessPhotonDown:
    def get(self, url, **k):
        raise requests.RequestException('x')


o._gate_last['photon'] = 0
try:
    o.suggest_places('rourk', session=SessPhotonDown())
    check("suggest: outage raises", False)
except o.RouteLookupError as e:
    check("suggest: outage -> an error that says typing the full name still works", 'full place name' in str(e))

# =========================================================================== 2. SERVER

print("\n--- server: pages")
calls = {'geo': [], 'routes': 0, 'extras': 0, 'sug': []}


def fake_geo(text, bias_center=None, budget=None, session=None, limit=5):
    calls['geo'].append((text, bias_center))
    book = {'rourkela': [{'lat': 22.26, 'lng': 84.85, 'display_name': 'Rourkela, Odisha, India'}],
            'paradeep': [{'lat': 20.31, 'lng': 86.61, 'display_name': 'Paradeep, Odisha, India'}],
            'rsp': [{'lat': 22.25, 'lng': 84.88, 'display_name': 'RSP, Rourkela'}, {'lat': 28.6, 'lng': 77.2, 'display_name': 'RSP, Delhi'}]}
    return book.get(text.lower().split(',')[0], [])


ROUTES = [{'distance_km': 397.4, 'duration_min': 287, 'highways': [{'ref': 'NH 49', 'km': 100.0, 'from_km': 0.0}, {'ref': None, 'km': 297.4, 'from_km': 100.0}],
           'via': 'NH 49', 'pace': [[0.0, 0.0], [397.4, 287.0]], 'geometry': [[22.26, 84.85], [21.0, 85.5], [20.31, 86.61]], 'idx': 0, 'label': 'Fastest'},
          {'distance_km': 410.0, 'duration_min': 300, 'highways': [], 'via': '', 'pace': [[0.0, 0.0], [410.0, 300.0]],
           'geometry': [[22.26, 84.85], [20.31, 86.61]], 'idx': 1, 'label': 'Alternative'}]


def fake_search(f, t, session=None):
    calls['routes'] += 1
    return json.loads(json.dumps(ROUTES))


def fake_extras(poly, dist, hws, session=None):
    calls['extras'] += 1
    return {'tolls': [{'name': 'Toll plaza near Talcher', 'named': False, 'lat': 20.9, 'lng': 85.3, 'km': 196.0, 'highway': 'NH 49', 'barrierless': False}],
            'towns': [{'name': 'Talcher', 'kind': 'town', 'label': 'Town', 'score': 70, 'lat': 20.95, 'lng': 85.2, 'km': 195.8, 'off_route_km': 1.4}],
            'picks': {'sm': [0], 'md': [0], 'lg': [0]},
            'fuels': [{'name': 'Indian Oil', 'named': True, 'lat': 20.96, 'lng': 85.21, 'km': 196.4, 'highway': 'NH 49'}],
            'extras_ok': True, 'note': None}


def fake_suggest(text, bias_center=None, session=None, limit=7):
    calls['sug'].append((text, bias_center))
    return [{'name': 'Rourkela', 'detail': 'Raurkela (M), Odisha', 'kind': 'City', 'label': 'Rourkela, Odisha',
             'display_name': 'Rourkela, Raurkela (M), Odisha, India', 'lat': 22.231, 'lng': 84.868}]


o.geocode_candidates, o.search_routes, o.route_extras, o.suggest_places = fake_geo, fake_search, fake_extras, fake_suggest

cl = a.app.test_client()
check("search requires login (redirects)", cl.get('/route-finder/search?from=a&to=b').status_code == 302)
check("suggest requires login (redirects)", a.app.test_client().get('/route-finder/suggest?q=rourk').status_code == 302)
with cl.session_transaction() as s:
    s['user_id'], s['username'], s['is_admin'] = 8, '9668348621', 1

h = cl.get('/route-analytics?tab=route-finder&rf_from=Rourkela&rf_to=Paradeep').data.decode()
check("website: Route Finder tab button + active, prefilled panel",
      'id="tabBtn-route-finder"' in h and re.search(r'class="ra-tab-panel active" id="ra-route-finder"', h) and 'data-from="Rourkela"' in h and 'data-to="Paradeep"' in h)
check("website: scripts, styles and endpoints wired", 'route_finder.js' in h and 'route_finder.css' in h
      and 'data-search-url="/route-finder/search"' in h and 'data-suggest-url="/route-finder/suggest"' in h and 'data-extras-url="/route-finder/extras"' in h)
check("website: NO predefined place list", '<datalist' not in h and 'list="rfPlaces"' not in h)
check("website: the redundant 'Roads used' bar is gone", 'Roads used' not in h and 'rf-hwbar' not in h)
check("website: map + stops layout present", 'class="rf-layout"' in h and 'class="rf-map"' in h and 'Route Stops' in h and 'class="rf-stops"' in h)
h2 = cl.get('/route-analytics').data.decode()
check("website default tab: finder panel inactive; Route Summary rows link to the finder",
      'id="ra-route-finder"' in h2 and 'tab=route-finder&rf_from=' in h2)
check("website: other tab still renders", cl.get('/route-analytics?tab=route-rates').status_code == 200)
h3 = cl.get('/app/analytics?tab=finder&rf_from=Rourkela&rf_to=Paradeep').data.decode()
check("app: finder renders with prefill; exactly one script tag, inside the extra-scripts block",
      'id="rfRoot"' in h3 and 'data-from="Rourkela"' in h3 and len(re.findall(r'<script src="[^"]*route_finder\.js', h3)) == 1
      and h3.index('id="appExtraScripts"') < re.search(r'<script src="[^"]*route_finder\.js', h3).start())
check("app: finder is a dedicated screen (compact header, no Business Overview period bar)", 'Route Finder' in h3 and 'rf-pagehead' in h3 and 'periodSheet' not in h3.split('id="rfRoot"')[0])
h4 = cl.get('/app/analytics?tab=best').data.decode()
check("app best tab: rows link to the finder, filter button + overview header kept, no finder markup",
      'tab=finder&rf_from=' in h4 and 'openRaFilterSheet()' in h4 and 'id="rfRoot"' not in h4 and 'periodSheet' in h4)
check("app: rates tab and a bogus tab still render", cl.get('/app/analytics?tab=rates').status_code == 200 and cl.get('/app/analytics?tab=zzz').status_code == 200)

print("\n--- server: /route-finder/search")
j = cl.get('/route-finder/search?from=Rourkela&to=Paradeep').get_json()
check("search ok: both places and 2 routes, with the pace table", j['ok'] and len(j['routes']) == 2 and j['from']['chosen']['display_name'].startswith('Rourkela') and j['routes'][0]['pace'])
check("search: the To end is biased towards the From end", calls['geo'][1][1] == (22.26, 84.85))
check("search: straight-line distance is sane", 250 < j['straight_km'] < 330)
n_geo, n_routes = len(calls['geo']), calls['routes']
cl.get('/route-finder/search?from=Rourkela&to=Paradeep')
check("repeat search is served from the cache", len(calls['geo']) == n_geo and calls['routes'] == n_routes)
j = cl.get('/route-finder/search?from=RSP&to=Paradeep').get_json()
check("an ambiguous place returns candidates for the picker", j['ok'] and len(j['from']['candidates']) == 2)
j2 = cl.get('/route-finder/search?from=RSP&to=Paradeep&from_lat=28.6&from_lng=77.2&from_name=RSP, Delhi').get_json()
check("a pinned end skips the name lookup and is used as given", j2['ok'] and j2['from']['chosen']['lat'] == 28.6 and j2['from']['candidates'] == [])
check("an unknown place -> 404 with helpful text", cl.get('/route-finder/search?from=Nowhereville&to=Paradeep').status_code == 404)
check("missing To -> 400", cl.get('/route-finder/search?from=Rourkela').status_code == 400)
check("same place -> 400", cl.get('/route-finder/search?from=Rourkela&to=Rourkela').status_code == 400)
check("garbage pin coordinates are ignored (falls back to the name)", cl.get('/route-finder/search?from=Rourkela&to=Paradeep&from_lat=999&from_lng=x').get_json()['ok'])
check("very long text is truncated, not rejected", cl.get('/route-finder/search?from=Rourkela' + 'x' * 500 + '&to=Paradeep').status_code in (200, 404))
before = len(calls['geo'])
j = cl.get('/route-finder/search?from=Rourkela, Odisha&to=Paradeep, Odisha&from_lat=22.231&from_lng=84.868&to_lat=20.269&to_lng=86.651&from_name=Rourkela&to_name=Paradeep').get_json()
check("both ends picked from suggestions: no geocoding calls at all", j['ok'] and len(calls['geo']) == before and j['from']['chosen']['lat'] == 22.231)
o.geocode_candidates = lambda text, bias_center=None, **k: [{'lat': 22.2 if 'rourkela' in text.lower() else 21.9, 'lng': 84.8 if 'rourkela' in text.lower() else 84.0, 'display_name': text}]
j = cl.get('/route-finder/search?from=Rourkela Odisha&to=Jharsuguda, Odisha').get_json()
check("history: matches your own Rourkela -> Jharsuguda trips", j['ok'] and j['history'] and j['history']['trips'] > 0)
o.geocode_candidates = fake_geo

print("\n--- server: /route-finder/extras")
body = {'geometry': ROUTES[0]['geometry'], 'distance_km': 397.4, 'highways': ROUTES[0]['highways']}
r = cl.post('/route-finder/extras', json=body)
jj = r.get_json()
check("extras ok: tolls, towns and petrol pumps come through", r.status_code == 200 and jj['ok'] and jj['tolls'][0]['name'] == 'Toll plaza near Talcher'
      and jj['fuels'][0]['name'] == 'Indian Oil' and jj['towns'][0]['name'] == 'Talcher')
n = calls['extras']
cl.post('/route-finder/extras', json=body)
check("extras are cached on repeat", calls['extras'] == n)
for label, bad in [("no geometry", {'distance_km': 10}), ("1-point geometry", dict(body, geometry=[[1, 2]])),
                   ("non-numeric coordinates", dict(body, geometry=[['a', 'b'], [1, 2]])), ("latitude out of range", dict(body, geometry=[[100, 0], [1, 2]])),
                   ("zero distance", dict(body, distance_km=0)), ("huge distance", dict(body, distance_km=99999)),
                   ("bad highways", dict(body, highways=[{'ref': 5}])), ("too many points", dict(body, geometry=[[1, 2]] * 5000))]:
    check(f"extras rejects: {label}", cl.post('/route-finder/extras', json=bad).status_code == 400)
check("extras rejects non-JSON", cl.post('/route-finder/extras', data='x').status_code == 400)
o.route_extras = lambda poly, dist, hws, session=None: {'tolls': [], 'towns': [], 'picks': {'sm': [], 'md': [], 'lg': []}, 'fuels': [], 'extras_ok': False, 'note': 'Overpass busy'}
b2 = dict(body, distance_km=111.0)
j = cl.post('/route-finder/extras', json=b2).get_json()
check("a failed lookup is reported (nothing invented), not raised", j['ok'] and j['extras_ok'] is False and j['note'] == 'Overpass busy' and j['fuels'] == [])
o.route_extras = fake_extras
n = calls['extras']
cl.post('/route-finder/extras', json=b2)
check("a failed lookup is not cached, so Retry really retries", calls['extras'] == n + 1)

print("\n--- server: /route-finder/suggest")
j = cl.get('/route-finder/suggest?q=rourk').get_json()
check("suggest ok: results", j['ok'] and j['results'][0]['label'] == 'Rourkela, Odisha')
check("suggest: under 3 letters -> empty, provider not called", cl.get('/route-finder/suggest?q=ro').get_json() == {'ok': True, 'results': []} and len(calls['sug']) == 1)
n = len(calls['sug'])
cl.get('/route-finder/suggest?q=ROURK')
check("suggest: a repeat (any letter case) is served from the cache", len(calls['sug']) == n)
cl.get('/route-finder/suggest?q=tangi&lat=22.2&lon=84.9')
check("suggest: a valid bias is passed through", calls['sug'][-1] == ('tangi', (22.2, 84.9)))
cl.get('/route-finder/suggest?q=tangi2&lat=999&lon=abc')
check("suggest: a garbage bias is ignored", calls['sug'][-1] == ('tangi2', None))
o.suggest_places = lambda *a_, **k_: (_ for _ in ()).throw(o.RouteLookupError('Place search is busy right now'))
r = cl.get('/route-finder/suggest?q=newplace')
check("suggest: provider error -> 502 with its friendly text", r.status_code == 502 and 'busy' in r.get_json()['error'])
o.suggest_places = lambda *a_, **k_: (_ for _ in ()).throw(ValueError('secret internals'))
r = cl.get('/route-finder/suggest?q=another')
check("suggest: an unexpected error -> 500, generic text, no internals", r.status_code == 500 and b'secret internals' not in r.data)
o.suggest_places = fake_suggest
for _ in range(4):
    a._RF_SUGGEST_SLOTS.acquire()
j = cl.get('/route-finder/suggest?q=uncachedquery').get_json()
check("suggest: all slots taken -> empty + busy flag (not an error)", j['ok'] and j['results'] == [] and j.get('busy') is True)
for _ in range(4):
    a._RF_SUGGEST_SLOTS.release()
check("suggest: slots are released afterwards", cl.get('/route-finder/suggest?q=uncachedquery').get_json()['results'] != [])

print("\n--- server: concurrency guard and error safety")
for _ in range(3):
    a._RF_SLOTS.acquire()
check("search: 429 when all slots are taken", cl.get('/route-finder/search?from=Rourkela&to=Paradeep2').status_code == 429)
check("extras: 429 when all slots are taken (uncached)", cl.post('/route-finder/extras', json=dict(body, distance_km=222.2)).status_code == 429)
for _ in range(3):
    a._RF_SLOTS.release()
check("slots released afterwards", cl.get('/route-finder/search?from=Rourkela&to=Paradeep').status_code == 200)
o.search_routes = lambda *a_, **k_: (_ for _ in ()).throw(ValueError('secret internals'))
a._RF_CACHE.clear()
r = cl.get('/route-finder/search?from=Rourkela&to=Paradeep')
free = a._RF_SLOTS.acquire(blocking=False)
if free:
    a._RF_SLOTS.release()
check("an unexpected error -> 500, generic text, no internals, slot released", r.status_code == 500 and b'secret internals' not in r.data and free)

shutil.rmtree(TMP_DIR, ignore_errors=True)
print(f"\n{_ok} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
