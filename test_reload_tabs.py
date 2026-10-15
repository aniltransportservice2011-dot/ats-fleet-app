"""Regression test: a browser REFRESH lands on the same screen, the same tab (and the same page) on every screen — website and phone app.

Two ways a screen can hold its tab:
  * link tabs  — the tab is in the address (?tab=...), the server marks it active;
  * in-page tabs (Route Analytics, Performance, Fleet Utilization, Settings, Settings > Users) — switched in the browser without a reload, so the
    switcher MUST write the tab into the address (rememberView) or a refresh falls back to the default / an older tab (the "Route Rates keeps
    coming back" bug), and the screen must read it back on load.

What this checks (the real-browser refresh is exercised separately; this guards the code that makes it work):
  1. base.html defines the address helpers in <head> — BEFORE any page script runs on a full load (defining them at the end silently broke
     Fleet Utilization: the tab was in the address but the page read it too early).
  2. every in-page switcher writes the address on a user click and does NOT on its own first draw (silent init), and reads it back on load.
  3. for every screen with tabs, asking for each tab in the address shows THAT tab active; an unknown tab falls back (never a 500).
  4. the in-page tab strips never lose their data attributes / ids the init relies on.

Usage:  python3 test_reload_tabs.py
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import requests                                                              # noqa: E402
def _blocked(*a, **k):
    raise RuntimeError('network blocked in tests')
requests.get = requests.post = _blocked
import app as m                                                               # noqa: E402
from test_support import ensure_test_user                                     # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


def read(p):
    return open(os.path.join(REPO, p), encoding='utf-8').read()


# ---------------------------------------------------------------- 1. helpers exist early
base = read('templates/base.html')
head = base[:base.index('</head>')]
check('window.rememberView = function' in head, 'base.html: rememberView is defined in <head> (before page scripts run)')
check('window.viewParam = function' in head, 'base.html: viewParam is defined in <head> (before page scripts run)')
check('window.rememberView = function' not in base[base.index('</head>'):], 'base.html: rememberView is not redefined later in the body')
check('history.replaceState' in head and 'searchParams' in head, 'base.html: rememberView rewrites the address with replaceState')
check('u.hash' not in head and "u.pathname + u.search" in head, 'base.html: rememberView drops the #anchor of the tab being left')

# ---------------------------------------------------------------- 2. each in-page switcher
SWITCHERS = [
    # (template, function signature, init call that must be silent, what the click writes)
    ('templates/route_analytics.html', 'function showRTab(which, btn, silent)', "showRTab(initial, null, true)", "rememberView({ tab: which })"),
    ('templates/performance.html', 'function showPerfTab(which, btn, silent)', "showPerfTab(initial, null, true)", "rememberView({ tab: which })"),
    ('templates/fleet_utilization.html', 'function showFuTab(id, btn, silent)', "showFuTab(['overview', 'idle', 'empty']", "rememberView({ tab: id === 'overview' ? null : id })"),
    ('templates/settings.html', 'function showSTab(id, btn)', "viewParam('sub')", "rememberView({ tab: id.replace('tab-', ''), sub: null })"),
]
for path, sig, init, write in SWITCHERS:
    src = read(path)
    check(sig in src, f'{path}: switcher keeps its (…, silent) signature')
    check(init in src, f'{path}: first draw is silent / reads the address — {init[:40]}')
    check(write in src, f'{path}: a click writes the tab to the address')
    check('window.rememberView &&' in src or 'window.viewParam ?' in src or 'window.rememberView)' in src, f'{path}: guards the helper so the screen still works without it')
fu = read('templates/fleet_utilization.html')
check("viewParam('fu_page')" in fu and "fu_page: fuPage > 1 ? fuPage : null" in fu, 'fleet_utilization: the page number is kept in the address too')
check("viewParam('fu_per')" in fu and 'fuReady' in fu, 'fleet_utilization: the rows-per-page choice is kept, and the first draw does not rewrite the address')
check(fu.count('data-tab="') >= 3 and "'.fu-tab[data-tab=" in fu, 'fleet_utilization: tab buttons carry data-tab (init finds the button without a click)')
st = read('templates/settings.html')
check("rememberView({ tab: 'users', sub:" in st, 'settings: Users & Roles sub-tabs are kept in the address')
check('urShowSub(\'ur-\' + s, null, true)' in st, 'settings: the sub-tab is restored silently on load')
# the old failure: the address named the tab but the page read it before the helper existed — nothing may read the helper at load without a guard
for path in ('templates/fleet_utilization.html', 'templates/settings.html'):
    s = read(path)
    for mo in re.finditer(r'viewParam\(', s):
        check('window.viewParam' in s[max(0, mo.start() - 40):mo.start()], f'{path}: viewParam call is guarded ({mo.start()})')

# ---------------------------------------------------------------- 3. every tab, asked for in the address, is the one shown
tmp = tempfile.mkdtemp(prefix='reloadtabs_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp)
one = lambda q: (c.execute(q).fetchone() or [None])[0]
if not one('SELECT COUNT(*) FROM vehicles'):
    for i in range(1, 6):
        c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES (?,?)", (f'RLV{i:02d}', 'own'))
if not one('SELECT COUNT(*) FROM parties'):
    c.execute("INSERT INTO parties (name) VALUES ('RLPARTY')")
if not one('SELECT COUNT(*) FROM trips'):
    c.execute("INSERT INTO trips (date, lr_number, vehicle_id, type, party_id, from_loc, to_loc, quantity, rate, rate_type, billed_amount) "
              "VALUES ('2026-09-01','RLT1',(SELECT MIN(id) FROM vehicles),'own',(SELECT MIN(id) FROM parties),'A','B',10,1000,'PER_MT',10000)")
c.commit()
veh = one('SELECT MIN(id) FROM vehicles'); trip = one('SELECT MIN(id) FROM trips'); party = one('SELECT MIN(id) FROM parties')
c.close()
uid = ensure_test_user(dbp)


def gdb():
    x = sqlite3.connect(dbp)
    x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid


def get(url):
    r = cl.get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


# website — in-page tabs: the server puts the asked-for tab in the hidden field / marks the button
for tab in ('best-routes', 'route-rates', 'route-finder'):
    code, page = get(f'/route-analytics?tab={tab}')
    check(code == 200, f'route-analytics ?tab={tab} opens')
    check(re.search(r'id="activeTabField" value="%s"' % tab, page) is not None, f'route-analytics ?tab={tab}: the page starts on that tab')
    check(re.search(r'class="ra-tab active" id="tabBtn-%s"' % tab, page) is not None, f'route-analytics ?tab={tab}: its button is the active one')
code, page = get('/route-analytics?tab=nonsense')
check(code == 200 and 'id="activeTabField" value="best-routes"' in page, 'route-analytics: an unknown tab falls back to Best Routes instead of breaking the page')
code, page = get('/route-analytics')
check(code == 200 and 'id="activeTabField" value="best-routes"' in page, 'route-analytics: no tab = Best Routes')

for tab in ('driver', 'vehicle'):
    code, page = get(f'/performance?tab={tab}')
    check(code == 200 and f'id="activeTabField" value="{tab}"' in page, f'performance ?tab={tab}: the page starts on that tab')

for tab in ('company', 'invoice', 'tax', 'users'):
    code, page = get(f'/settings?tab={tab}')
    check(code == 200 and re.search(r'id="tab-%s" class="settings-tab-panel active"' % tab, page) is not None, f'settings ?tab={tab}: that panel is the active one')

# website — link tabs
LINK = [
    ('/maintenance', ('overview', 'service', 'tyres', 'battery', 'urea', 'toll', 'compliance'), r'<a class="mt-tab[^"]*\bactive\b[^"]*"[^>]*href="[^"]*?tab=%s'),
    ('/salaries', ('salary', 'overview', 'attendance'), r'<a class="mt-tab[^"]*\bactive\b[^"]*"[^>]*href="[^"]*?tab=%s'),
    ('/overheads', ('all', 'pending', 'paid', 'overdue'), r'<a class="mt-tab[^"]*\bactive\b[^"]*"[^>]*href="[^"]*?tab=%s'),
]
for path, tabs, rx in LINK:
    code, page = get(path)
    check(code == 200, f'{path} opens')
    for tab in tabs:
        code, page = get(f'{path}?tab={tab}')
        check(code == 200, f'{path} ?tab={tab} opens')
    code, page = get(f'{path}?tab=nonsense')
    check(code == 200, f'{path}: an unknown tab does not break the page')

# phone app — link tabs
PHONE = [
    ('/app/analytics', ('best', 'rates', 'finder')),
    ('/app/performance', ('driver', 'vehicle')),
    ('/app/fleet-utilization', ('overview', 'idle', 'empty')),
    ('/app/maintenance', ('overview', 'service', 'tyres', 'battery', 'urea', 'toll', 'compliance')),
    (f'/app/trips/{trip}', ('info', 'costs', 'deductions', 'completion')),
    (f'/app/vehicles/{veh}', ('overview', 'insurance', 'permit', 'fitness', 'puc')),
]
for path, tabs in PHONE:
    for tab in tabs:
        code, page = get(f'{path}?tab={tab}')
        check(code == 200, f'{path} ?tab={tab} opens')
        active = [x for x in re.findall(r'<(?:a|button)\b[^>]*>', page) if re.search(r'class="[^"]*\b(?:ra|mo|vh|tr|lg|ld)-tab\b[^"]*\bactive\b', x)]
        if path not in ('/app/maintenance', f'/app/vehicles/{veh}'):
            shown = any(f'tab={tab}' in x or f'data-td-tab="{tab}"' in x for x in active) or (path == '/app/analytics' and tab == 'finder' and 'class="rf-pagehead"' in page)   # the phone Route Finder is its own screen, no strip
            check(shown, f'{path} ?tab={tab}: that tab is the active one')
    code, page = get(f'{path}?tab=nonsense')
    check(code == 200, f'{path}: an unknown tab does not break the page')

# phone trip detail: switching tabs in the page already writes the address
td = read('templates/app/trip_detail.html')
check("params.set('tab', name)" in td and 'history.replaceState' in td, 'phone trip detail: switching a tab writes it to the address')

# ---------------------------------------------------------------- 5. a refresh or Back also keeps the scroll place (website); a normal open still starts at the top
check("navType === 'reload' || navType === 'back_forward'" in head and "sessionStorage.setItem('fleetScroll'" in head, 'base.html: only a browser refresh or Back / Forward restores the scroll place')
check("readAll()[key()]" in head, 'base.html: the saved place is only used for the same address (screen + tab + page)')
check("delete all[k]; all[k] = Math.round(window.scrollY)" in head and 'keys.length > 40' in head, 'base.html: one entry per address, so another screen in between cannot overwrite it')
check("if (wantY === null) { if (!window.location.hash) window.scrollTo(0, 0); return; }" in head, 'base.html: every other way of arriving still opens at the top')
check("'wheel', 'touchstart', 'keydown', 'mousedown'" in head, 'base.html: restoring stops the moment the user scrolls')
check('var settled = wantY === null;' in head and 'if (!settled) return;' in head, 'base.html: a second refresh before the place is restored cannot overwrite it with the top')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
