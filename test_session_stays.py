"""Regression test: nobody is logged out for being idle, and a screen left alone for hours still responds.

  A. no idle logout: a session whose last request was 5 h / 30 h / 20 days ago is still signed in (website and phone), for every access level;
  B. every login is a long-lived session: the cookie carries an expiry ~30 days ahead even without "Remember me", is renewed on each request, and Logout still ends it;
  C. /session-ping: 200 + {"ok": true} when signed in (also for a Limited Access user), a redirect to the login page when not — and it changes nothing;
  D. the pages: the shared guard script is on the website, the phone app and the phone trip wizard; soft navigation falls back to a normal page load when the
     answer is the login page, an error or not a page of the app;
  E. static/session_guard.js, run for real in Node with a fake clock: a click after < 30 min does nothing extra; after 30 min it asks the server first and reloads when
     the answer is "signed out" / unreachable / an error, and does not reload when still signed in; after 8 h with nothing typed it reloads; after 8 h with typed
     text it only checks; coming back to the tab does the same check.

Runs on a disposable copy of fleet.db (the real file is never touched, network blocked).  Usage:  python3 test_session_stays.py
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import requests                                                              # noqa: E402
def _blocked(*a, **k):
    raise RuntimeError('network blocked in tests')
requests.get = requests.post = _blocked
import app as m                                                               # noqa: E402
from test_support import ensure_test_user                                     # noqa: E402
from werkzeug.security import generate_password_hash                          # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


tmp = tempfile.mkdtemp(prefix='sessionstays_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
uid = ensure_test_user(dbp)
c = sqlite3.connect(dbp)
c.execute("UPDATE users SET password_hash=?, username='9000000000' WHERE id=?", (generate_password_hash('Test#12345'), uid))
for u, level, mods in ((900501, 'Read Only', ''), (900502, 'Limited Access', 'Trips:v')):
    c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, status, phone, created_at) "
              "VALUES (?,?,?,?,0,?,?,?,'Active',?,'2026-01-01')", (u, f'u{u}', 'x', 'Manager', 'Other', level, mods, f'91000{u}'))
c.commit(); c.close()


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
read = lambda p: open(p, encoding='utf-8').read()


def client(user_id, last_activity=None):
    cl = m.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'] = user_id
        if last_activity:
            s['last_activity'] = last_activity          # what the old idle timeout read; must now be ignored
    return cl


# ------------------------------------------------------------------------------------------------------------------  A. no idle logout
check('IDLE_TIMEOUT_SECONDS' not in read('app.py'), 'A: the idle-timeout constant is gone')
for hours in (5, 30, 24 * 20):
    old = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    for who, label in ((uid, 'Full Access'), (900501, 'Read Only'), (900502, 'Limited Access')):
        cl = client(who, old)
        r1 = cl.get('/dashboard' if who != 900502 else '/trips'); r2 = cl.get('/app/dashboard' if who != 900502 else '/app/trips')
        check(r1.status_code in (200, 302) and '/login' not in (r1.headers.get('Location') or ''), f'A: website, {label}, idle {hours} h -> still signed in ({r1.status_code} {r1.headers.get("Location")})')
        check(r2.status_code in (200, 302) and '/login' not in (r2.headers.get('Location') or ''), f'A: phone, {label}, idle {hours} h -> still signed in ({r2.status_code} {r2.headers.get("Location")})')

# ------------------------------------------------------------------------------------------------------------------  B. long-lived sessions
cl = m.app.test_client()
r = cl.post('/login', data={'username': '9000000000', 'password': 'Test#12345'})          # no "remember me"
cookie = r.headers.get('Set-Cookie', '')
exp = re.search(r'Expires=([^;]+)', cookie)
when = datetime.datetime.strptime(exp.group(1), '%a, %d %b %Y %H:%M:%S %Z') if exp else None
days = (when - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds() / 86400 if when else 0
check(r.status_code == 302 and exp is not None and 29 <= days <= 31, f'B: a login without "Remember me" still gets a ~30-day cookie ({days:.1f} days)')
r = cl.get('/dashboard')
c2 = r.headers.get('Set-Cookie', '')
check(r.status_code == 200 and 'Expires=' in c2, 'B: and every request renews it')
r = cl.post('/app/login', data={'username': '9000000000', 'password': 'Test#12345'})
check('Expires=' in r.headers.get('Set-Cookie', ''), 'B: the phone login does the same')
cl.get('/logout')
check(cl.get('/dashboard').status_code == 302 and '/login' in (cl.get('/dashboard').headers.get('Location') or ''), 'B: Logout still ends the session')

# ------------------------------------------------------------------------------------------------------------------  C. /session-ping
before = gdb().execute('SELECT COUNT(*) FROM access_logs').fetchone()[0]
for who, label in ((uid, 'Full Access'), (900501, 'Read Only'), (900502, 'Limited Access')):
    r = client(who).get('/session-ping')
    check(r.status_code == 200 and r.get_json() == {'ok': True} and 'no-store' in (r.headers.get('Cache-Control') or ''), f'C: signed in ({label}) -> 200 {{ok: true}}, not cached')
r = m.app.test_client().get('/session-ping')
check(r.status_code == 302 and '/login' in r.headers.get('Location', ''), 'C: signed out -> redirected to the login page')
check(gdb().execute('SELECT COUNT(*) FROM access_logs').fetchone()[0] == before, 'C: pinging changes nothing')

# ------------------------------------------------------------------------------------------------------------------  D. the pages
base = read('templates/base.html'); app_base = read('templates/app/base_app.html'); wiz = read('templates/app/trip_add.html')
check("asset_v('session_guard.js')" in base and "asset_v('session_guard.js')" in app_base and "asset_v('session_guard.js')" in wiz, 'D: the guard script is on the website, the phone app and the phone trip wizard')
check('function softOk(resp)' in base and "window.location = url; return null;" in base and "id=[\"']main-content[\"']" in base, 'D: website soft navigation falls back to a normal page load on a login / error / non-app answer')
check(base.count('.then(textOrReload)') == 3, 'D: the three pop-up loaders reload instead of drawing the login page inside the pop-up')
check("res.redirected && /\\/login/.test(new URL(res.url).pathname)" in app_base, 'D: phone soft navigation does the same')
for url in ('/dashboard', '/trips'):
    check('session_guard.js' in client(uid).get(url).data.decode(), f'D: {url} serves the guard')
check('session_guard.js' in client(uid).get('/app/dashboard').data.decode() and 'session_guard.js' in client(uid).get('/app/trips/add').data.decode(), 'D: so do the phone pages')

# ------------------------------------------------------------------------------------------------------------------  E. the guard script, for real
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync('static/session_guard.js', 'utf8');
function run(steps) {
  let now = 1000000000000, reloads = 0, pings = 0, pingMode = 'ok', listeners = {}, vis = 'visible';
  const doc = { visibilityState: 'visible', addEventListener(t, f) { (listeners['d:' + t] = listeners['d:' + t] || []).push(f); } };
  Object.defineProperty(doc, 'visibilityState', { get() { return vis; } });
  const win = { location: { reload() { reloads++; }, href: 'http://x/' }, addEventListener(t, f) { (listeners['w:' + t] = listeners['w:' + t] || []).push(f); }, AbortController };
  const D = { now: () => now };
  function fetchStub(url) {
    pings++;
    if (pingMode === 'throw') return Promise.reject(new Error('net'));
    if (pingMode === 'error') return Promise.resolve({ ok: false, redirected: false, url: 'http://x/session-ping' });
    if (pingMode === 'login') return Promise.resolve({ ok: true, redirected: true, url: 'http://x/login' });
    return Promise.resolve({ ok: true, redirected: false, url: 'http://x/session-ping' });
  }
  const ctx = { window: win, document: doc, Date: D, fetch: fetchStub, URL, setTimeout, clearTimeout, AbortController, console };
  win.document = doc; ctx.window = Object.assign(win, { __sessionGuard: undefined });
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const fire = (key, ev) => (listeners[key] || []).forEach(f => f(ev || {}));
  const out = [];
  return (async () => {
    for (const s of steps) {
      if (s.advance) now += s.advance * 60000;
      if (s.mode) pingMode = s.mode;
      if (s.vis) vis = s.vis;
      if (s.type) fire('d:input', { target: { closest: () => ({}) } });
      if (s.click) fire('d:pointerdown');
      if (s.visible) fire('d:visibilitychange');
      if (s.pageshow) fire('w:pageshow', { persisted: true });
      await new Promise(r => setTimeout(r, 5));
      out.push({ pings, reloads });
    }
    return out;
  })();
}
(async () => {
  const r = {};
  r.quick = await run([{ advance: 10, click: 1 }]);
  r.after40_ok = await run([{ advance: 40, click: 1, mode: 'ok' }]);
  r.after40_login = await run([{ advance: 40, click: 1, mode: 'login' }]);
  r.after40_error = await run([{ advance: 40, click: 1, mode: 'error' }]);
  r.after40_throw = await run([{ advance: 40, click: 1, mode: 'throw' }]);
  r.after9h_clean = await run([{ advance: 9 * 60, click: 1 }]);
  r.after9h_typed = await run([{ type: 1 }, { advance: 9 * 60, click: 1, mode: 'ok' }]);
  r.back_to_tab = await run([{ advance: 90, visible: 1, mode: 'login' }]);
  r.back_to_tab_quick = await run([{ advance: 5, visible: 1 }]);
  r.bfcache = await run([{ pageshow: 1, mode: 'login' }]);
  r.second_click = await run([{ advance: 40, click: 1, mode: 'ok' }, { advance: 1, click: 1 }]);
  console.log(JSON.stringify(r));
})();
"""
open(os.path.join(tmp, 'h.js'), 'w').write(HARNESS)
res = subprocess.run(['node', os.path.join(tmp, 'h.js')], capture_output=True, text=True, cwd=REPO)
try:
    R = json.loads(res.stdout.strip().splitlines()[-1])
except Exception:
    R = None
    print(res.stdout, res.stderr)
check(R is not None, 'E: the guard script runs in Node')
if R:
    last = lambda k: R[k][-1]
    check(last('quick') == {'pings': 0, 'reloads': 0}, f'E: a click after 10 minutes does nothing extra {last("quick")}')
    check(last('after40_ok') == {'pings': 1, 'reloads': 0}, f'E: after 40 minutes the click asks the server, and does NOT reload while still signed in {last("after40_ok")}')
    check(last('after40_login') == {'pings': 1, 'reloads': 1}, f'E: ... reloads when the answer is the login page {last("after40_login")}')
    check(last('after40_error') == {'pings': 1, 'reloads': 1}, f'E: ... reloads on a server error {last("after40_error")}')
    check(last('after40_throw') == {'pings': 1, 'reloads': 1}, f'E: ... reloads when the server cannot be reached {last("after40_throw")}')
    check(last('after9h_clean') == {'pings': 0, 'reloads': 1}, f'E: after 9 hours with nothing typed, the click reloads the page {last("after9h_clean")}')
    check(R['after9h_typed'][-1] == {'pings': 1, 'reloads': 0}, f'E: after 9 hours with typed text it only checks (nothing is lost) {R["after9h_typed"][-1]}')
    check(last('back_to_tab') == {'pings': 1, 'reloads': 1}, f'E: coming back to the tab after 90 minutes checks straight away and reloads when signed out {last("back_to_tab")}')
    check(last('back_to_tab_quick') == {'pings': 0, 'reloads': 0}, f'E: coming back after 5 minutes does nothing {last("back_to_tab_quick")}')
    check(last('bfcache') == {'pings': 1, 'reloads': 1}, f'E: a page restored from the back/forward cache is checked too {last("bfcache")}')
    check(last('second_click') == {'pings': 1, 'reloads': 0}, f'E: the click right after a check does not ask again {last("second_click")}')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
