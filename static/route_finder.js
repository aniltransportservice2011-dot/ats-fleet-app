/* Route Finder front end — shared by the website and the mobile app (see
   templates/partials/_route_finder.html for the markup and the data-* settings this reads).

   Flow: Search -> /route-finder/search (places + routes, fast) -> draw map and route cards ->
   for the selected route, /route-finder/extras in three parts, one after another and only once per route:
   'places' (cities and towns from the server's built-in list — instant), then 'tolls' and 'fuel' (petrol
   pumps + landmarks) from the slow free OpenStreetMap server. Each part fills in when it arrives and a
   failure of one hides only its own numbers. All text that came from OpenStreetMap is escaped before it touches the DOM. */
(function () {
  'use strict';

  var leafletPromise = null;
  function loadOnce(tag, attrs) {
    return new Promise(function (resolve, reject) {
      var el = document.createElement(tag);
      Object.keys(attrs).forEach(function (k) { el[k] = attrs[k]; });
      el.onload = resolve;
      el.onerror = function () { reject(new Error('Could not load the map library.')); };
      document.head.appendChild(el);
    });
  }
  // Leaflet is loaded on demand (and only once), not via <script> in the page: the mobile app swaps
  // page content without a reload, and a script tag added that way runs *before* its src finishes
  // loading — so the init code would find L undefined.
  function ensureLeaflet(base) {
    if (window.L && window.L.map) return Promise.resolve();
    if (!leafletPromise) {
      leafletPromise = loadOnce('link', { rel: 'stylesheet', href: base + 'leaflet.css' })
        .then(function () { return loadOnce('script', { src: base + 'leaflet.js' }); })
        .catch(function (e) { leafletPromise = null; throw e; });
    }
    return leafletPromise;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtTime(min) {
    var h = Math.floor(min / 60), m = Math.round(min % 60);
    return h ? h + ' h ' + (m < 10 ? '0' : '') + m + ' min' : m + ' min';
  }
  function fmtKm(km) { return (Math.round(km * 10) / 10).toLocaleString('en-IN') + ' km'; }
  function short(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function hwClass(ref) {
    if (!ref) return 'local';
    if (/^NH/.test(ref)) return 'nh';
    if (/^(SH|MDR|ODR)/.test(ref)) return 'sh';
    return 'ot';
  }
  function $(root, sel) { return root.querySelector(sel); }

  var SUGGEST_MIN = 3;       // letters before suggestions start
  var SUGGEST_DELAY = 350;   // ms of typing pause before asking — keeps requests few

  function highlight(name, q) {
    var i = name.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(name);
    return esc(name.slice(0, i)) + '<mark>' + esc(name.slice(i, i + q.length)) + '</mark>' + esc(name.slice(i + q.length));
  }

  // Search-as-you-type for one From/To box: anywhere in India, via /route-finder/suggest. Picking a
  // suggestion pins its exact coordinates to the box (input.__pin), so Search needs no second name
  // lookup and can't land on a different place of the same name. Typing again clears the pin.
  function Typeahead(finder, input, getBiasPin, nextEl) {
    var self = this;
    this.finder = finder; this.input = input; this.getBiasPin = getBiasPin; this.nextEl = nextEl;
    this.items = []; this.active = -1; this.timer = null; this.ctl = null; this.token = 0;
    this.list = document.createElement('ul');
    this.list.className = 'rf-sug' + (input.name === 'to' ? ' end' : ''); this.list.hidden = true; this.list.id = input.id + 'Sug';
    this.list.setAttribute('role', 'listbox');
    input.parentNode.appendChild(this.list);
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', this.list.id); input.setAttribute('aria-expanded', 'false');
    input.addEventListener('input', function () { self.onInput(); });
    input.addEventListener('keydown', function (e) { self.onKey(e); });
    input.addEventListener('blur', function () { setTimeout(function () { self.close(); }, 180); });
    input.addEventListener('focus', function () {
      if (self.items.length && !input.__pin && input.value.trim().length >= SUGGEST_MIN) self.open();
    });
    // The on-screen keyboard shrinks the *visual* viewport (and Android also resizes the page): when
    // that changes while a list is open, re-fit it into what's still visible.
    var refit = function () { if (!self.list.hidden) self.fit(); };
    if (window.visualViewport) window.visualViewport.addEventListener('resize', refit);
    window.addEventListener('resize', refit);
    // pointerdown, not click: it fires before the box loses focus (which would close the list first)
    this.list.addEventListener('pointerdown', function (e) {
      var li = e.target.closest('li[data-i]');
      if (li) { e.preventDefault(); self.pick(+li.getAttribute('data-i')); }
    });
  }
  Typeahead.prototype.open = function () {
    this.list.hidden = false; this.input.setAttribute('aria-expanded', 'true');
    this.fit();
  };
  Typeahead.prototype.close = function () {
    this.list.hidden = true; this.active = -1; this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.list.style.maxHeight = '';
    this.finder.releaseSpace();
  };
  // Make sure the list is actually visible. It opens BELOW its box, and on a phone that is exactly
  // where the on-screen keyboard (and the app's bottom bar) sit — so a box low on the screen used to
  // open a list nobody could see, which looks like "no suggestions". On a narrow screen, scroll the
  // page just enough to lift the box up, and cap the list to the room that's left above the keyboard.
  Typeahead.prototype.fit = function () {
    var narrow = window.matchMedia && window.matchMedia('(max-width: 760px)').matches;
    if (!narrow) { this.list.style.maxHeight = ''; return; }
    var vv = window.visualViewport;
    var viewBottom = (vv ? vv.offsetTop + vv.height : window.innerHeight);
    var nav = document.querySelector('.a-bottom-nav');
    var reserve = nav ? nav.getBoundingClientRect().height : 0;   // bottom bar: covers the page above the keyboard on Android
    var header = document.querySelector('.a-topbar, header');
    var topSafe = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
    var WANT = 190;                                                  // room for ~4 rows
    var r = this.input.getBoundingClientRect();
    var room = viewBottom - reserve - r.bottom - 8;
    if (room < WANT) {
      this.finder.reserveSpace(WANT + reserve + 60);                 // let the page scroll far enough
      var delta = Math.min(WANT - room, r.top - topSafe - 8);
      if (delta > 0) window.scrollBy(0, delta);
      r = this.input.getBoundingClientRect();
      room = viewBottom - reserve - r.bottom - 8;
    }
    this.list.style.maxHeight = Math.max(120, Math.min(340, room)) + 'px';
  };
  Typeahead.prototype.showMsg = function (html) {
    this.items = []; this.active = -1;
    this.list.innerHTML = '<li class="msg">' + html + '</li>';
    this.open();
  };
  Typeahead.prototype.onInput = function () {
    var self = this, q = this.input.value.trim();
    this.input.__pin = null; this.input.classList.remove('pinned');
    clearTimeout(this.timer);
    if (this.ctl) this.ctl.abort();
    this.token++;
    if (q.length < SUGGEST_MIN) { this.items = []; this.close(); return; }
    this.timer = setTimeout(function () { self.fetchSuggestions(q); }, SUGGEST_DELAY);
  };
  Typeahead.prototype.fetchSuggestions = function (q) {
    var self = this, token = ++this.token;
    if (this.ctl) this.ctl.abort();
    this.ctl = window.AbortController ? new AbortController() : null;
    this.showMsg('<span class="rf-spin"></span>Searching places…');
    var url = this.finder.cfg.suggestUrl + '?q=' + encodeURIComponent(q);
    var bias = this.getBiasPin();
    if (bias) url += '&lat=' + bias.lat + '&lon=' + bias.lng;
    fetch(url, { credentials: 'same-origin', signal: this.ctl ? this.ctl.signal : undefined })
      .then(readJson)
      .then(function (d) {
        if (token !== self.token) return;
        if (!d.ok) { self.showMsg(esc(d.error || 'Place search is unavailable right now.')); return; }
        if (d.busy) { self.showMsg('Place search is busy — keep typing and it will try again.'); return; }
        self.items = d.results || [];
        self.render(q);
      })
      .catch(function (e) {
        if ((e && e.name === 'AbortError') || token !== self.token) return;
        self.showMsg('Could not reach the server. You can still type the full place name and press Search route.');
      });
  };
  Typeahead.prototype.render = function (q) {
    if (!this.items.length) {
      this.showMsg('No places found for “' + esc(q) + '”. Try another spelling, or add the state.');
      return;
    }
    var self = this;
    this.list.innerHTML = this.items.map(function (it, i) {
      return '<li role="option" id="' + self.list.id + i + '" data-i="' + i + '"><span class="tx"><span class="nm">' + highlight(it.name, q) +
        '</span><span class="dt">' + esc(it.detail || 'India') + '</span></span>' + (it.kind ? '<span class="kd">' + esc(it.kind) + '</span>' : '') + '</li>';
    }).join('');
    this.active = -1;
    this.open();
  };
  Typeahead.prototype.setActive = function (i) {
    var lis = this.list.querySelectorAll('li[data-i]');
    if (!lis.length) return;
    this.active = (i + lis.length) % lis.length;
    lis.forEach(function (li, n) { li.classList.toggle('on', n === this.active); }, this);
    this.input.setAttribute('aria-activedescendant', lis[this.active].id);
    lis[this.active].scrollIntoView({ block: 'nearest' });
  };
  Typeahead.prototype.onKey = function (e) {
    var isOpen = !this.list.hidden && this.items.length;
    if (e.key === 'ArrowDown' && isOpen) { e.preventDefault(); this.setActive(this.active + 1); }
    else if (e.key === 'ArrowUp' && isOpen) { e.preventDefault(); this.setActive(this.active - 1); }
    else if (e.key === 'Escape') { this.close(); }
    else if (e.key === 'Enter' && isOpen) {
      // Like a maps app: Enter takes the highlighted suggestion, or the top one; a second Enter searches.
      e.preventDefault();
      this.pick(this.active >= 0 ? this.active : 0);
    }
  };
  Typeahead.prototype.pick = function (i) {
    var it = this.items[i];
    if (!it) return;
    this.input.value = it.label;
    this.input.__pin = { lat: it.lat, lng: it.lng, display_name: it.display_name };
    this.input.classList.add('pinned');
    this.items = []; this.close();
    if (this.nextEl) this.nextEl.focus();
  };

  function pinInput(input, pin) {
    input.__pin = pin || null;
    input.classList.toggle('pinned', !!pin);
  }

  // Parse a fetch response as JSON; a login redirect comes back as an HTML page, not JSON.
  function readJson(res) {
    return res.text().then(function (t) {
      try { return JSON.parse(t); }
      catch (e) { return { ok: false, error: 'Your session may have expired — please log in again.' }; }
    });
  }
  var fmtElapsed = function (min) {
    if (min == null) return '';
    min = Math.round(min);
    if (min < 1) return '';
    var h = Math.floor(min / 60), m = min % 60;
    return h ? (m ? h + ' h ' + (m < 10 ? '0' : '') + m + ' min' : h + ' h') : m + ' min';
  };
  function ic(id) { return '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#rf-i-' + id + '"/></svg>'; }
  function hav(a, b, c, d) {
    var R = 6371.0088, rad = Math.PI / 180, p1 = a * rad, p2 = c * rad, dp = (c - a) * rad, dl = (d - b) * rad;
    var x = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }
  function nhClass(ref) { return /^NH/.test(ref || '') ? 'rf-nh' : 'rf-nh sh'; }
  function splitPlace(display) {      // "Rourkela, Raurkela (M), Odisha, India" -> name + short detail
    var parts = String(display || '').split(',').map(function (x) { return x.trim(); }).filter(Boolean);
    return { name: parts[0] || 'Place', sub: parts.slice(1, 3).join(', ') };
  }
  function divIcon(cls, html, size) {
    return L.divIcon({ className: '', html: '<div class="rf-mk ' + cls + '">' + (html || '') + '</div>', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  }

  // ---------------------------------------------------------------------------------------------
  function Finder(root) {
    var self = this;
    this.root = root;
    this.cfg = {
      searchUrl: root.getAttribute('data-search-url'),
      extrasUrl: root.getAttribute('data-extras-url'),
      suggestUrl: root.getAttribute('data-suggest-url'),
      leafletBase: root.getAttribute('data-leaflet-base')
    };
    this.fromEl = $(root, '[name=from]');
    this.toEl = $(root, '[name=to]');
    this.goBtn = $(root, '.rf-go');
    this.statusEl = $(root, '.rf-status');
    this.resultsEl = $(root, '.rf-results');
    this.mapWrap = $(root, '.rf-mapwrap');
    this.state = { routes: [], sel: 0, ends: null, history: null, extras: {}, loading: null, token: 0, stopId: null, openId: null };
    this.map = null; this.layers = null; this.stopMarkers = {}; this.geo = null;
    this.pendingAuto = !!(root.getAttribute('data-from') && root.getAttribute('data-to'));
    this.fromEl.value = root.getAttribute('data-from') || '';
    this.toEl.value = root.getAttribute('data-to') || '';

    // Each box suggests places as you type; the other box's picked place (if any) biases the results.
    this.taFrom = new Typeahead(this, this.fromEl, function () { return self.toEl.__pin; }, this.toEl);
    this.taTo = new Typeahead(this, this.toEl, function () { return self.fromEl.__pin; }, this.goBtn);
    $(root, '.rf-form').addEventListener('submit', function (e) {
      e.preventDefault();
      self.taFrom.close(); self.taTo.close();
      self.search();
    });
    $(root, '.rf-swap').addEventListener('click', function () {
      var v = self.fromEl.value, pf = self.fromEl.__pin, pt = self.toEl.__pin;
      self.fromEl.value = self.toEl.value; self.toEl.value = v;
      pinInput(self.fromEl, pt); pinInput(self.toEl, pf);
      if (self.fromEl.value.trim() && self.toEl.value.trim()) self.search();
    });
    $(root, '.rf-fs').addEventListener('click', function () { self.toggleFullscreen(); });
    $(root, '.rf-fit').addEventListener('click', function () {
      self.fitRoute();
      if (self.root.getAttribute('data-w') === 'sm') self.mapWrap.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && self.mapWrap.classList.contains('full')) self.toggleFullscreen(false); });
    // The list of stops delegates its clicks (it is rebuilt whenever the route or the lookups change).
    $(root, '.rf-stops').addEventListener('click', function (e) {
      var onmap = e.target.closest('.rf-onmap');
      var row = e.target.closest('.rf-stop');
      if (!row) return;
      if (onmap) { self.selectStop(row.getAttribute('data-id'), { pan: true, showMap: true }); return; }
      if (e.target.closest('.rf-stop-more')) return;
      if (e.target.closest('.rf-stop-main')) self.toggleStop(row.getAttribute('data-id'));
    });
    this.applyWidth();
    // the finder's own width (a ResizeObserver catches a sidebar collapsing, which a window resize doesn't) AND plain
    // window resizes / phone rotation, belt and braces — applyWidth() does nothing when the layout class is unchanged
    if (window.ResizeObserver) new ResizeObserver(function () { self.applyWidth(); }).observe(root);
    window.addEventListener('resize', function () { self.applyWidth(); });
  }

  // Layout is chosen from the finder's own width (see route_finder.css): the website's sidebar means
  // the window width says little about the room this actually has.
  Finder.prototype.applyWidth = function () {
    var w = this.root.getBoundingClientRect().width;
    if (!w) return;
    var k = w >= 980 ? 'lg' : (w >= 700 ? 'md' : 'sm');
    if (this.root.getAttribute('data-w') !== k) {
      this.root.setAttribute('data-w', k);
      var self = this, st = this.state;
      // a different layout fits a different number of key locations: re-choose them from the server's ranking
      if (st.routes.length && this.geo && this.extrasStatus(st.sel) === 'ok') {
        this.renderStops();
        if (st.stopId && !this.stopById(st.stopId)) { st.stopId = null; st.openId = null; this.renderStops(); }
        this.drawOverlays();
      }
      // the map's box just changed size (rotated phone, sidebar collapsed, window resized): re-measure and re-fit
      if (this.map) setTimeout(function () { self.map.invalidateSize(); self.fitRoute(); }, 60);
    }
  };

  // Extra space under the finder while a phone list is open, so the page can scroll the box up even
  // when the content is short; released once both lists are closed.
  Finder.prototype.reserveSpace = function (px) { this.root.style.paddingBottom = Math.round(px) + 'px'; };
  Finder.prototype.releaseSpace = function () {
    if ((this.taFrom && !this.taFrom.list.hidden) || (this.taTo && !this.taTo.list.hidden)) return;
    this.root.style.paddingBottom = '';
  };

  Finder.prototype.activate = function () {
    this.applyWidth();
    if (this.map) this.map.invalidateSize();
    if (this.pendingAuto) { this.pendingAuto = false; this.search(); }
  };

  Finder.prototype.status = function (kind, html) {
    this.statusEl.className = 'rf-status' + (kind ? ' show ' + kind : '');
    this.statusEl.innerHTML = html || '';
  };

  // ------------------------------------------------------------------ search
  Finder.prototype.search = function (pinned) {
    var self = this;
    var from = this.fromEl.value.trim(), to = this.toEl.value.trim();
    if (!from || !to) { this.status('err', 'Enter both a From and a To place.'); return; }
    var q = new URLSearchParams({ from: from, to: to });
    // A place picked from the suggestions is searched by its exact coordinates; a place typed out
    // in full without picking one is looked up by name on the server.
    pinned = pinned || { from: this.fromEl.__pin || null, to: this.toEl.__pin || null };
    ['from', 'to'].forEach(function (side) {
      if (pinned[side]) {
        q.set(side + '_lat', pinned[side].lat); q.set(side + '_lng', pinned[side].lng);
        q.set(side + '_name', pinned[side].display_name || '');
      }
    });
    var token = ++this.state.token;
    this.goBtn.disabled = true;
    this.status('busy', '<span class="rf-spin"></span>Finding routes…');
    fetch(this.cfg.searchUrl + '?' + q.toString(), { credentials: 'same-origin' })
      .then(readJson)
      .then(function (d) {
        if (token !== self.state.token) return;
        self.goBtn.disabled = false;
        if (!d.ok) { self.status('err', esc(d.error || 'Something went wrong.')); return; }
        self.status('', '');
        var st = self.state;
        st.ends = { from: d.from, to: d.to, fromPinned: !!(pinned.from), toPinned: !!(pinned.to) };
        st.routes = d.routes; st.history = d.history; st.straight = d.straight_km;
        st.extras = {}; st.retried = {}; st.loading = null; st.sel = 0; st.stopId = null; st.openId = null;
        self.resultsEl.hidden = false;
        self.applyWidth();
        self.renderPlaces();
        self.renderRouteChips();
        self.syncUrl(from, to);
        return ensureLeaflet(self.cfg.leafletBase).then(function () {
          if (token !== self.state.token) return;
          self.initMap(); self.select(0);
        }).catch(function (e) {
          self.status('err', esc(e.message) + ' The route details below still work.');
          self.select(0);
        });
      })
      .catch(function () {
        if (token !== self.state.token) return;
        self.goBtn.disabled = false;
        self.status('err', 'Could not reach the server. Check your connection and try again.');
      });
  };

  Finder.prototype.syncUrl = function (from, to) {
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('rf_from', from); u.searchParams.set('rf_to', to);
      history.replaceState(null, '', u.pathname + '?' + u.searchParams.toString() + u.hash);
    } catch (e) { /* cosmetic only */ }
  };

  // Only for a place that was typed out and then looked up by name (so it might not be the one
  // meant): show what it resolved to and let the user pick another match. A place picked from the
  // suggestions needs nothing here — the box already says what was chosen.
  Finder.prototype.renderPlaces = function () {
    var self = this, el = $(this.root, '.rf-places'), ends = this.state.ends;
    el.innerHTML = '';
    [['from', 'From', ends.fromPinned], ['to', 'To', ends.toPinned]].forEach(function (t) {
      var side = t[0], end = ends[side];
      if (t[2]) return;
      var box = document.createElement('div'); box.className = 'rf-place';
      var h = '<span class="k">' + t[1] + '</span><span class="v" title="' + esc(end.chosen.display_name) + '">' + esc(end.chosen.display_name) + '</span>';
      if (end.candidates && end.candidates.length > 1) {
        h += '<select aria-label="Not the right ' + t[1] + ' place?">' + end.candidates.map(function (c, i) {
          return '<option value="' + i + '"' + (c.lat === end.chosen.lat && c.lng === end.chosen.lng ? ' selected' : '') + '>' +
            (i === 0 ? 'Best match: ' : 'Or: ') + esc(short(c.display_name, 70)) + '</option>';
        }).join('') + '</select>';
      }
      box.innerHTML = h;
      var sel = box.querySelector('select');
      if (sel) sel.addEventListener('change', function () {
        var pin = {};
        pin[side] = end.candidates[+sel.value];
        pinInput(side === 'from' ? self.fromEl : self.toEl, pin[side]);
        var other = side === 'from' ? 'to' : 'from';
        pin[other] = self.state.ends[other].chosen;          // keep the other end exactly where it is
        self.search(pin);
      });
      el.appendChild(box);
    });
  };

  Finder.prototype.renderRouteChips = function () {
    var self = this, el = $(this.root, '.rf-routes');
    el.innerHTML = '';
    if (this.state.routes.length < 2) return;
    this.state.routes.forEach(function (r, i) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'rf-chip'; b.setAttribute('data-i', i);
      b.innerHTML = '<span class="l">' + esc(r.label) + '</span><span class="d">' + esc(fmtKm(r.distance_km)) + '<i>' + esc(fmtTime(r.duration_min)) + '</i></span>';
      b.addEventListener('click', function () { self.select(i); });
      el.appendChild(b);
    });
  };

  // ------------------------------------------------------------------ route maths (all from the real response)
  Finder.prototype.route = function () { return this.state.routes[this.state.sel]; };

  // Cumulative km along the (simplified) path, scaled so its end equals OSRM's own distance.
  Finder.prototype.buildGeo = function () {
    var g = this.route().geometry, cum = [0];
    for (var i = 1; i < g.length; i++) cum.push(cum[i - 1] + hav(g[i - 1][0], g[i - 1][1], g[i][0], g[i][1]));
    this.geo = { cum: cum, scale: cum[cum.length - 1] ? this.route().distance_km / cum[cum.length - 1] : 1 };
  };
  Finder.prototype.pointAtKm = function (km) {
    var g = this.route().geometry, cum = this.geo.cum, t = km / (this.geo.scale || 1);
    if (t <= 0) return g[0];
    if (t >= cum[cum.length - 1]) return g[g.length - 1];
    var lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) { var m = (lo + hi) >> 1; if (cum[m] <= t) lo = m; else hi = m; }
    var f = (t - cum[lo]) / ((cum[hi] - cum[lo]) || 1);
    return [g[lo][0] + (g[hi][0] - g[lo][0]) * f, g[lo][1] + (g[hi][1] - g[lo][1]) * f];
  };
  // Minutes of driving to reach a point `km` along the route, from OSRM's own per-step durations.
  Finder.prototype.elapsedAt = function (km) {
    var p = this.route().pace;
    if (!p || p.length < 2) return null;
    if (km <= p[0][0]) return 0;
    if (km >= p[p.length - 1][0]) return p[p.length - 1][1];
    var lo = 0, hi = p.length - 1;
    while (hi - lo > 1) { var m = (lo + hi) >> 1; if (p[m][0] <= km) lo = m; else hi = m; }
    return p[lo][1] + (p[hi][1] - p[lo][1]) * ((km - p[lo][0]) / ((p[hi][0] - p[lo][0]) || 1));
  };
  Finder.prototype.highwayAt = function (km) {
    var ref = null, hw = this.route().highways;
    for (var i = 0; i < hw.length; i++) { if (hw[i].from_km <= km + 1e-6) ref = hw[i].ref; else break; }
    return ref;
  };
  // Roads driven between two points, in order, with how far on each (>= 0.5 km only).
  Finder.prototype.roadsBetween = function (a, b) {
    var out = [], idx = {};
    this.route().highways.forEach(function (h) {
      var ov = Math.min(h.from_km + h.km, b) - Math.max(h.from_km, a);
      if (ov < 0.5) return;
      var k = h.ref || 'Local roads';
      if (!(k in idx)) { idx[k] = out.length; out.push({ ref: h.ref, name: k, km: 0 }); }
      out[idx[k]].km += ov;
    });
    return out;
  };

  Finder.prototype.extrasStatus = function (i) {
    var x = this.state.extras[i];
    if (x) return x.failed ? 'failed' : 'ok';
    return 'loading';
  };

  // Toll plazas and petrol pumps arrive after the towns, each on its own: 'loading' until they answer,
  // 'ok' with the list, or 'failed' (then the towns and route still show). kind = 'toll' | 'fuel'.
  Finder.prototype.facStatus = function (i, kind) {
    var x = this.state.extras[i];
    if (!x) return 'loading';
    if (x.failed) return 'failed';
    return x[kind + 'St'] || 'ok';
  };

  // The ordered stops: start, the major towns the lookup found, end — each with the highway it is on,
  // its elapsed driving time, and the toll plazas / petrol pumps mapped on the stretch leading to it.
  Finder.prototype.buildStops = function () {
    var self = this, st = this.state, r = this.route(), x = st.extras[st.sel], status = this.extrasStatus(st.sel);
    var a = st.ends.from.chosen, b = st.ends.to.chosen, pa = splitPlace(a.display_name), pb = splitPlace(b.display_name);
    var stops = [{ id: 'a', kind: 'start', name: pa.name, sub: pa.sub, full: a.display_name, km: 0, min: 0, hw: this.highwayAt(0) }];
    this.foundPlaces = 0; this.shownPlaces = 0;
    if (status === 'ok') {
      // The server ranked every major place near the route and chose, for each screen size, the few that fit
      // (biggest first, spread along the way). Show the choice for THIS layout.
      var mode = this.root.getAttribute('data-w') || 'sm';
      var picks = (x.picks && x.picks[mode]) || x.towns.map(function (t, i) { return i; });
      var endsText = (a.display_name + ' | ' + b.display_name).toLowerCase(), n = 0;
      picks.forEach(function (idx) {
        var t = x.towns[idx];
        if (!t || endsText.indexOf(t.name.toLowerCase()) !== -1) return;     // that's the start or the end itself
        stops.push({ id: 't' + idx, kind: 'stop', n: ++n, name: t.name, sub: t.label || (t.kind === 'city' ? 'City' : 'Town'), km: t.km,
                     min: self.elapsedAt(t.km), hw: self.highwayAt(t.km), off: t.off_route_km });
      });
      this.shownPlaces = n; this.foundPlaces = x.towns.length;
    }
    stops.push({ id: 'b', kind: 'end', name: pb.name, sub: pb.sub, full: b.display_name, km: r.distance_km, min: this.elapsedAt(r.distance_km), hw: this.highwayAt(Math.max(0, r.distance_km - 0.2)) });
    stops.forEach(function (s, i) {
      var p = self.pointAtKm(s.km); s.lat = p[0]; s.lng = p[1];
      if (i === 0) return;
      s.from = stops[i - 1];
      s.roads = self.roadsBetween(stops[i - 1].km, s.km);
      if (status === 'ok') {
        var inStretch = function (z) { return z.km > stops[i - 1].km && (z.km <= s.km || (s.kind === 'end' && z.km <= s.km + 0.5)); };
        s.tolls = x.tolls.filter(inStretch);
        s.fuels = (x.fuels || []).filter(inStretch);
      }
    });
    return stops;
  };

  // ------------------------------------------------------------------ rendering
  Finder.prototype.select = function (i) {
    var st = this.state;
    st.sel = i; st.stopId = null; st.openId = null;
    this.buildGeo();
    this.root.querySelectorAll('.rf-chip').forEach(function (c) { c.classList.toggle('sel', +c.getAttribute('data-i') === i); });
    this.drawRoutes();
    this.renderSummary();
    this.renderStops();
    this.drawOverlays();
    this.loadExtras(i, false);
  };

  function facTile(status, list) {
    if (status === 'loading') return '<span class="rf-sk"></span>';
    if (status === 'failed') return '—';
    return list.length ? String(list.length) : '<span class="rf-none" title="OpenStreetMap has none mapped on this route — its data can be incomplete">None mapped</span>';
  }

  Finder.prototype.renderSummary = function () {
    var st = this.state, r = this.route(), status = this.extrasStatus(st.sel), x = st.extras[st.sel] || {};
    var roads = {};
    r.highways.forEach(function (h) { if (h.ref) roads[h.ref] = (roads[h.ref] || 0) + h.km; });
    var nRoads = Object.keys(roads).filter(function (k) { return roads[k] >= 1; }).length;
    var tile = function (cls, icon, label, val) {
      return '<div class="rf-tile"><span class="ic ' + cls + '">' + ic(icon) + '</span><span><div class="k">' + label + '</div><div class="v">' + val + '</div></span></div>';
    };
    var hist = st.history, histHtml = '';
    if (hist) {
      histHtml = '<div class="rf-history"><b>Your own trips on this route:</b> ' + hist.trips + ' trip' + (hist.trips === 1 ? '' : 's') + ' recorded. ' +
        (hist.trips_with_toll ? 'Toll you recorded averages <b>₹' + hist.avg_toll.toLocaleString('en-IN') + '</b> per trip' +
          (hist.min_toll === hist.max_toll ? '' : ' (₹' + hist.min_toll.toLocaleString('en-IN') + ' – ₹' + hist.max_toll.toLocaleString('en-IN') + ')') +
          ', across ' + hist.trips_with_toll + ' trip' + (hist.trips_with_toll === 1 ? '' : 's') + ' that logged one.'
          : 'None of them logged a toll amount.') + '</div>';
    }
    $(this.root, '.rf-summary').innerHTML = '<div class="rf-tiles">' +
      tile('dist', 'map', 'Distance', esc(fmtKm(r.distance_km))) +
      tile('time', 'clock', 'Drive time', esc(fmtTime(r.duration_min))) +
      tile('road', 'road', 'Highways', nRoads || '—') +
      tile('toll', 'toll', 'Toll plazas', facTile(this.facStatus(st.sel, 'toll'), x.tolls || [])) +
      tile('fuel', 'fuel', 'Petrol pumps', facTile(this.facStatus(st.sel, 'fuel'), x.fuels || [])) +
      '</div>' + histHtml;
  };

  Finder.prototype.renderStops = function () {
    var self = this, st = this.state, status = this.extrasStatus(st.sel), x = st.extras[st.sel];
    var stops = this.stops = this.buildStops();
    var list = $(this.root, '.rf-stops'), count = $(this.root, '.rf-count');
    var rowHtml = function (s, i) {
      var mk = s.kind === 'start' ? 'A' : (s.kind === 'end' ? 'B' : s.n);
      var chips = '';
      if (s.hw) chips += '<span class="' + nhClass(s.hw) + '">' + esc(s.hw) + '</span>';
      if (s.kind !== 'start') {
        if (status === 'loading') chips += '<span class="rf-sk"></span>';
        else if (status === 'ok') {
          var fsF = self.facStatus(st.sel, 'fuel'), fsT = self.facStatus(st.sel, 'toll');
          var chip = function (fs, list, kind, label) {
            if (fs === 'loading') return '<span class="rf-sk"></span>';
            if (fs === 'failed') return '<span class="rf-fac dim" title="Could not be looked up right now">' + ic(kind) + '—</span>';
            return list.length ? '<span class="rf-fac ' + kind + '" title="' + list.length + ' ' + label + '(s) mapped since the previous stop">' + ic(kind) + list.length + '</span>'
                               : '<span class="rf-fac dim" title="None mapped since the previous stop">' + ic(kind) + '—</span>';
          };
          chips += chip(fsF, s.fuels, 'fuel', 'petrol pump') + chip(fsT, s.tolls, 'toll', 'toll plaza');
        }
      }
      var open = st.openId === s.id, sel = st.stopId === s.id;
      return '<li class="rf-stop' + (sel ? ' sel' : '') + (open ? ' open' : '') + '" data-id="' + s.id + '">' +
        '<button type="button" class="rf-stop-main" aria-expanded="' + (open ? 'true' : 'false') + '">' +
          '<span class="rf-mkr ' + s.kind + '">' + mk + '</span>' +
          '<span class="rf-stop-txt"><span class="nm">' + esc(s.name) + '</span>' + (s.sub ? '<span class="sb">' + esc(s.sub) + '</span>' : '') +
            '<span class="rf-chips">' + chips + '</span></span>' +
          '<span class="rf-stop-d"><b>' + esc(s.kind === 'start' ? '0 km' : fmtKm(s.km)) + '</b><i>' + esc(s.kind === 'start' ? 'Start' : fmtElapsed(s.min)) + '</i></span>' +
          '<svg class="rf-chev" viewBox="0 0 24 24" aria-hidden="true"><use href="#rf-i-chev"/></svg>' +
        '</button>' +
        '<div class="rf-stop-more"' + (open ? '' : ' hidden') + '>' + (open ? self.stopMoreHtml(s, status) : '') + '</div></li>';
    };
    var html = '';
    stops.forEach(function (s, i) {
      if (s.kind === 'end' && status === 'loading') {
        html += '<ul class="rf-skel" style="padding:0;margin:0" aria-hidden="true">' + '<li><span class="c"></span><span><div class="l"></div><div class="l s"></div></span></li>'.repeat(3) + '</ul>';
      }
      html += rowHtml(s, i);
    });
    list.innerHTML = html;
    if (status === 'failed') {
      var note = document.createElement('li');
      note.className = 'rf-note';
      note.innerHTML = esc(x.note) + ' <button type="button">Try again</button>';
      list.insertBefore(note, list.lastElementChild);
      note.querySelector('button').addEventListener('click', function () { self.loadExtras(st.sel, true); });
    }
    if (status === 'ok') {
      var failedParts = ['toll', 'fuel'].filter(function (k) { return self.facStatus(st.sel, k) === 'failed'; });
      if (failedParts.length) {
        var pn = document.createElement('li');
        pn.className = 'rf-note';
        pn.innerHTML = esc((failedParts.indexOf('toll') >= 0 ? x.tollNote : x.fuelNote) || 'Some toll and petrol details could not be fetched from OpenStreetMap right now.') + ' <button type="button">Try again</button>';
        list.insertBefore(pn, list.lastElementChild);
        pn.querySelector('button').addEventListener('click', function () { self.loadParts(st.sel, true); });
      }
    }
    count.textContent = status === 'loading' ? 'Finding key locations…'
      : status === 'failed' ? 'Key locations unavailable'
      : stops.length + ' key location' + (stops.length === 1 ? '' : 's');
    var limit = $(this.root, '.rf-limit');
    if (status === 'ok' && this.foundPlaces > this.shownPlaces) {
      limit.textContent = 'Showing the ' + this.shownPlaces + ' most major of ' + this.foundPlaces + ' cities, towns and landmarks along this route.';
      limit.hidden = false;
    } else {
      limit.hidden = true;
    }
  };

  Finder.prototype.stopMoreHtml = function (s, status) {
    var x = this.state.extras[this.state.sel];
    var h = '';
    if (s.kind === 'start') {
      h += '<h4>Departure point</h4><div>' + esc(s.full) + '</div>';
      if (s.hw) h += '<h4>Road</h4><div>Joins <span class="' + nhClass(s.hw) + '">' + esc(s.hw) + '</span> from here.</div>';
    } else {
      h += '<h4>' + (s.kind === 'end' ? 'Destination' : 'Stop') + ' · ' + esc(fmtKm(s.km - s.from.km)) + ' after ' + esc(s.from.name) + '</h4>';
      if (s.kind === 'end') h += '<div>' + esc(s.full) + '</div>';
      if (s.off > 1) h += '<div class="empty">' + esc(s.sub === 'City' || s.sub === 'Town' ? s.sub + ' centre' : s.sub) + ' is ' + esc(s.off) + ' km off the route.</div>';
      h += '<h4>Roads on this stretch</h4><ul>' + (s.roads.length ? s.roads.map(function (rd) {
        return '<li><span class="t">' + (rd.ref ? '<span class="' + nhClass(rd.ref) + '">' + esc(rd.ref) + '</span>' : 'Local roads') + '</span><span class="m">' + esc(fmtKm(rd.km)) + '</span></li>';
      }).join('') : '<li class="empty">No road details.</li>') + '</ul>';
      if (status === 'ok') {
        var many = function (arr, icon, cls, fallback) {
          var shown = arr.slice(0, 6).map(function (f) {
            return '<li><span class="rf-fac ' + cls + '">' + ic(icon) + '</span><span class="t">' + esc(f.name || fallback) + (f.barrierless ? ' (barrier-free gantry)' : '') + '</span><span class="m">' +
              esc(Math.round(f.km)) + ' km' + (f.highway ? ' · ' + esc(f.highway) : '') + '</span></li>';
          }).join('');
          return '<ul>' + shown + '</ul>' + (arr.length > 6 ? '<div class="more">+ ' + (arr.length - 6) + ' more mapped on this stretch</div>' : '');
        };
        var fsT = this.facStatus(this.state.sel, 'toll'), fsF = this.facStatus(this.state.sel, 'fuel');
        var part = function (title, fs, list, fallbackName, icon, cls, none) {
          if (fs === 'loading') return '<h4>' + title + '</h4><div class="empty">Looking up…</div>';
          if (fs === 'failed') return '<h4>' + title + '</h4><div class="empty">Could not be looked up right now.</div>';
          return '<h4>' + title + '</h4>' + (list.length ? many(list, icon, cls, fallbackName) : '<div class="empty">' + none + '</div>');
        };
        h += part('Toll plazas on this stretch', fsT, s.tolls, 'Toll plaza', 'toll', 'toll',
                  'None mapped in OpenStreetMap — which can be incomplete, so don’t read this as “no toll”.');
        h += part('Petrol pumps on this stretch', fsF, s.fuels, 'Petrol pump', 'fuel', 'fuel', 'None mapped in OpenStreetMap on this stretch.');
      } else if (status === 'loading') {
        h += '<div class="empty">Looking up toll plazas and petrol pumps…</div>';
      } else {
        h += '<div class="empty">Toll plazas and petrol pumps could not be looked up right now.</div>';
      }
    }
    if (this.map) h += '<button type="button" class="rf-onmap">' + ic('map') + 'Show on map</button>';
    return h;
  };

  // ------------------------------------------------------------------ selecting a stop
  Finder.prototype.stopById = function (id) { return (this.stops || []).filter(function (s) { return s.id === id; })[0]; };

  Finder.prototype.toggleStop = function (id) {
    var st = this.state;
    if (st.openId === id) { st.openId = null; this.updateRows(); return; }   // collapse, keep it highlighted
    this.selectStop(id, { pan: true });
  };

  // Highlight a stop in the list and on the map, expand it, and (optionally) move the map to it.
  // Never scrolls the list itself unless the pick came from the map — the user keeps their place.
  Finder.prototype.selectStop = function (id, opts) {
    opts = opts || {};
    var st = this.state, s = this.stopById(id);
    if (!s) return;
    st.stopId = id; st.openId = id;
    this.updateRows();
    this.updateStopMarkers();
    this.drawFuel(s);
    if (this.map && opts.pan) this.map.setView([s.lat, s.lng], Math.max(this.map.getZoom(), 10), { animate: true });
    if (opts.fromMap) {
      var row = $(this.root, '.rf-stop[data-id="' + id + '"]');
      if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    if (opts.showMap && this.root.getAttribute('data-w') === 'sm') this.mapWrap.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };

  // Update row highlight/expansion in place (so the list's scroll position never jumps).
  Finder.prototype.updateRows = function () {
    var self = this, st = this.state, status = this.extrasStatus(st.sel);
    this.root.querySelectorAll('.rf-stop').forEach(function (row) {
      var id = row.getAttribute('data-id'), open = st.openId === id, sel = st.stopId === id;
      row.classList.toggle('sel', sel); row.classList.toggle('open', open);
      row.querySelector('.rf-stop-main').setAttribute('aria-expanded', open ? 'true' : 'false');
      var more = row.querySelector('.rf-stop-more');
      if (open) { more.innerHTML = self.stopMoreHtml(self.stopById(id), status); more.hidden = false; }
      else { more.hidden = true; more.innerHTML = ''; }
    });
  };

  // ------------------------------------------------------------------ map
  Finder.prototype.initMap = function () {
    if (this.map) { this.map.remove(); this.map = null; }
    var map = L.map($(this.root, '.rf-map'), { scrollWheelZoom: false, zoomSnap: 0.25 });   // quarter-steps, so a route fills its box instead of stopping a zoom level short
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    map.on('focus', function () { map.scrollWheelZoom.enable(); });
    map.on('blur', function () { map.scrollWheelZoom.disable(); });
    var self = this;
    // The map's box changes size when the layout does (phone <-> desktop, rotating, full screen). Re-measure it
    // once the new size has settled, and re-fit the route — unless the user has since panned or zoomed it.
    L.DomEvent.on(map.getContainer(), 'mousedown touchstart wheel', function () { self.userMoved = true; });
    if (window.ResizeObserver && !this.mapObserver) {
      var tm;
      this.mapObserver = new ResizeObserver(function () {
        clearTimeout(tm);
        tm = setTimeout(function () { if (self.map) { self.map.invalidateSize(); if (!self.userMoved) self.fitRoute(); } }, 150);
      });
      this.mapObserver.observe(this.mapWrap);
    }
    this.map = map;
    this.layers = { routes: L.layerGroup().addTo(map), badges: L.layerGroup().addTo(map), tolls: L.layerGroup().addTo(map),
                    fuel: L.layerGroup().addTo(map), stops: L.layerGroup().addTo(map), pins: L.layerGroup().addTo(map) };
    map.invalidateSize();
  };

  Finder.prototype.drawRoutes = function () {
    var self = this, st = this.state;
    if (!this.map) return;
    this.layers.routes.clearLayers(); this.layers.pins.clearLayers();
    // the other options first (grey, clickable), the chosen one last so it sits on top with a white casing
    st.routes.forEach(function (r, i) {
      if (i === st.sel) return;
      L.polyline(r.geometry, { color: '#8d99ae', weight: 6, opacity: 0.85 }).on('click', function () { self.select(i); }).addTo(self.layers.routes);
    });
    var cur = st.routes[st.sel];
    L.polyline(cur.geometry, { color: '#ffffff', weight: 10, opacity: 1 }).addTo(this.layers.routes);
    this.routeLine = L.polyline(cur.geometry, { color: '#E85B19', weight: 6, opacity: 1 }).addTo(this.layers.routes);
    var a = st.ends.from.chosen, b = st.ends.to.chosen;
    L.marker([a.lat, a.lng], { icon: divIcon('a', 'A', 28), zIndexOffset: 1000 }).bindTooltip(esc(short(a.display_name, 60))).addTo(this.layers.pins);
    L.marker([b.lat, b.lng], { icon: divIcon('b', 'B', 28), zIndexOffset: 1000 }).bindTooltip(esc(short(b.display_name, 60))).addTo(this.layers.pins);
    this.fitRoute();
  };

  Finder.prototype.fitRoute = function () {
    this.userMoved = false;
    if (this.map && this.routeLine) this.map.fitBounds(this.routeLine.getBounds(), { padding: [30, 30] });
  };

  // Everything drawn on top of the route: highway tags, numbered stops, toll plazas.
  // (Petrol pumps are drawn only for the stop you opened — dozens of pumps would bury the map.)
  Finder.prototype.drawOverlays = function () {
    var self = this, st = this.state, r = this.route(), status = this.extrasStatus(st.sel);
    if (!this.map) return;
    ['badges', 'tolls', 'stops', 'fuel'].forEach(function (k) { self.layers[k].clearLayers(); });
    this.stopMarkers = {};
    r.highways.forEach(function (h) {
      if (!h.ref || h.km < 40 || !/^(NH|SH)/.test(h.ref)) return;     // only the long national/state highway stretches — shorter ones crowd the stop markers
      var p = self.pointAtKm(h.from_km + h.km / 2);
      L.marker(p, { icon: L.divIcon({ className: '', html: '<span class="rf-nh-tag">' + esc(h.ref) + '</span>', iconSize: null }),
                    interactive: false, keyboard: false, zIndexOffset: -500 }).addTo(self.layers.badges);
    });
    if (status !== 'ok') return;
    var x = st.extras[st.sel];
    x.tolls.forEach(function (t) {
      L.marker([t.lat, t.lng], { icon: divIcon('toll', ic('toll'), 24), zIndexOffset: 200 }).bindTooltip(esc(t.name))
        .on('click', function () {
          var s = (self.stops || []).filter(function (z) { return z.kind !== 'start' && z.from && t.km > z.from.km && t.km <= z.km + 0.5; })[0];
          if (s) self.selectStop(s.id, { fromMap: true });
        }).addTo(self.layers.tolls);
    });
    (this.stops || []).forEach(function (s) {
      if (s.kind !== 'stop') return;
      var m = L.marker([s.lat, s.lng], { icon: divIcon('stop', s.n, 24), zIndexOffset: 400 }).bindTooltip(esc(s.name))
        .on('click', function () { self.selectStop(s.id, { fromMap: true }); }).addTo(self.layers.stops);
      self.stopMarkers[s.id] = m;
    });
    this.updateStopMarkers();
  };

  Finder.prototype.updateStopMarkers = function () {
    var self = this, st = this.state;
    (this.stops || []).forEach(function (s) {
      var m = self.stopMarkers[s.id];
      if (!m) return;
      var sel = st.stopId === s.id;
      m.setIcon(divIcon('stop' + (sel ? ' sel' : ''), s.n, sel ? 32 : 24));
      m.setZIndexOffset(sel ? 900 : 400);
    });
  };

  Finder.prototype.drawFuel = function (s) {
    if (!this.map) return;
    this.layers.fuel.clearLayers();
    var self = this;
    (s.fuels || []).forEach(function (f) {
      L.marker([f.lat, f.lng], { icon: divIcon('fuel', ic('fuel'), 24), zIndexOffset: 300 }).bindTooltip(esc(f.name)).addTo(self.layers.fuel);
    });
  };

  Finder.prototype.toggleFullscreen = function (force) {
    var on = typeof force === 'boolean' ? force : !this.mapWrap.classList.contains('full');
    this.mapWrap.classList.toggle('full', on);
    document.documentElement.classList.toggle('rf-noscroll', on);
    var btn = $(this.root, '.rf-fs');
    btn.innerHTML = ic(on ? 'close' : 'expand');
    btn.setAttribute('aria-label', on ? 'Close full-screen map' : 'Expand map');
    btn.title = on ? 'Close full-screen map' : 'Expand map';
    var self = this;
    setTimeout(function () { if (self.map) { self.map.invalidateSize(); self.fitRoute(); } }, 80);
  };

  // ------------------------------------------------------------------ toll plazas, petrol pumps, towns (slow, lazy)
  Finder.prototype.postExtras = function (i, part) {
    var r = this.state.routes[i];
    return fetch(this.cfg.extrasUrl, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ part: part, geometry: r.geometry, distance_km: r.distance_km, highways: r.highways })
    }).then(readJson);
  };

  // Part 1: the cities and towns (the server's own list — answers at once). Then the two slow parts follow.
  Finder.prototype.loadExtras = function (i, force, isAutoRetry) {
    var self = this, st = this.state, token = st.token;
    if (st.extras[i] && !force) return;
    delete st.extras[i];
    st.loading = i;
    if (force && !isAutoRetry) { st.retried = st.retried || {}; st.retried[i] = false; }   // a manual "Try again" gets its own auto-retry
    if (force && st.sel === i) { this.renderSummary(); this.renderStops(); this.drawOverlays(); }
    this.postExtras(i, 'places').then(function (d) {
      if (token !== self.state.token) return;
      if (d.ok && d.extras_ok) {
        st.extras[i] = { towns: d.towns, picks: d.picks, tolls: [], fuels: [], tollSt: 'loading', fuelSt: 'loading' };
        self.afterExtras(i);
        self.loadParts(i, false);
        return;
      }
      self.extrasFailed(i, token, d.note || d.error || 'Key locations could not be found right now.');
    }).catch(function () {
      if (token !== self.state.token) return;
      self.extrasFailed(i, token, 'Could not reach the server.');
    });
  };

  // Parts 2 and 3, one after the other (the free server dislikes parallel requests): toll plazas, then petrol
  // pumps + landmarks. force = "Try again": redo only the parts that failed.
  Finder.prototype.loadParts = function (i, force) {
    var self = this, st = this.state, x = st.extras[i], token = st.token;
    if (!x || x.failed) return;
    var order = ['tolls', 'fuel'], k = 0;
    var next = function () {
      if (token !== self.state.token || st.extras[i] !== x || k >= order.length) return;
      var part = order[k++], key = part === 'tolls' ? 'toll' : 'fuel';
      if (!force && x[key + 'St'] === 'ok') return next();
      if (force && x[key + 'St'] !== 'failed') return next();
      x[key + 'St'] = 'loading'; x.partTry = x.partTry || {};
      if (force) { x.partTry[part] = 0; if (st.sel === i) { self.renderSummary(); self.renderStops(); } }
      var attempt = function () {
        self.postExtras(i, part).then(function (d) {
          if (token !== self.state.token || st.extras[i] !== x) return;
          if (d.ok && d.extras_ok) {
            if (part === 'tolls') x.tolls = d.tolls || [];
            else {
              var before = JSON.stringify(x.towns);
              x.fuels = d.fuels || []; x.towns = d.towns || x.towns; x.picks = d.picks || x.picks;   // landmarks re-rank the towns
              // stop ids are positions in the town list: if it changed, an open town row would now point at another place
              if (JSON.stringify(x.towns) !== before && /^t/.test(st.stopId || '')) { st.stopId = null; st.openId = null; }
            }
            x[key + 'St'] = 'ok';
            self.afterExtras(i); next();
            return;
          }
          failed(d.note || d.error);
        }).catch(function () { if (token === self.state.token && st.extras[i] === x) failed('Could not reach the server.'); });
      };
      var failed = function (note) {
        if (!x.partTry[part]) { x.partTry[part] = 1; setTimeout(function () { if (token === self.state.token && st.extras[i] === x) attempt(); }, 4000); return; }   // one quiet retry
        x[key + 'St'] = 'failed'; x[key + 'Note'] = note || 'Some toll and petrol details could not be fetched from OpenStreetMap right now.';
        self.afterExtras(i); next();
      };
      attempt();
    };
    next();
  };
  // The free OpenStreetMap lookup is sometimes briefly unavailable. Try once more by itself after a few
  // seconds (the list keeps showing its loading state meanwhile); only then show the note + "Try again".
  Finder.prototype.extrasFailed = function (i, token, note) {
    var self = this, st = this.state;
    st.retried = st.retried || {};
    if (!st.retried[i]) {
      st.retried[i] = true;
      setTimeout(function () { if (token === self.state.token) self.loadExtras(i, true, true); }, 4000);
      return;
    }
    st.extras[i] = { failed: true, note: note };
    this.afterExtras(i);
  };
  Finder.prototype.afterExtras = function (i) {
    if (this.state.sel !== i) return;
    this.renderSummary(); this.renderStops(); this.drawOverlays();
  };

  window.RouteFinder = {
    mount: function (root) {
      if (!root) return null;
      if (!root.__rf) root.__rf = new Finder(root);
      return root.__rf;
    }
  };
})();
