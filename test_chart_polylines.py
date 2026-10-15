"""Regression test: every trend line (<polyline>) drawn on every website page has valid coordinates. A malformed "points" list (for example one that
starts with a blank, which is what happened when a template forgot the previous point) makes the browser silently refuse to draw the line, and the
chart shows dots only. Runs on a disposable copy of fleet.db with a few extra months of trips so every chart has several points; the real file is
never touched.

Usage:  python3 test_chart_polylines.py
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


tmp = tempfile.mkdtemp(prefix='polyline_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp)
veh = c.execute('SELECT id FROM vehicles ORDER BY id LIMIT 1').fetchone()
if veh is None:                                                              # an empty database: make a vehicle, a party and trips in five months
    c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TEST0001','own')")
    veh = c.execute('SELECT id FROM vehicles ORDER BY id LIMIT 1').fetchone()
    c.execute("INSERT INTO parties (name) VALUES ('TEST PARTY')")
pid = c.execute('SELECT id FROM parties ORDER BY id LIMIT 1').fetchone()[0]
for i, month in enumerate(('2026-04', '2026-05', '2026-06', '2026-07', '2026-08')):
    c.execute("INSERT INTO trips (date, lr_number, vehicle_id, type, party_id, from_loc, to_loc, quantity, rate, rate_type, billed_amount, fuel_amount) "
              "VALUES (?,?,?,?,?,?,?,10,1000,'PER_MT',?,?)", (f'{month}-10', f'POLY-{i}', veh[0], 'own', pid, 'AAA', 'BBB', 10000 + i * 500, 3000 + i * 100))
c.commit(); c.close()
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
GOOD = re.compile(r'^\s*(-?\d+(\.\d+)?,-?\d+(\.\d+)?)(\s+-?\d+(\.\d+)?,-?\d+(\.\d+)?)*\s*$')
pages = ['/dashboard', '/business-performance', '/overheads', '/maintenance', '/maintenance?tab=toll', '/maintenance?tab=urea', '/maintenance?tab=compliance',
         '/fleet-utilization', '/fleet-utilization?tab=idle', '/performance', '/route-analytics?tab=route-rates', '/route-analytics?tab=best-routes',
         '/salaries', '/employees', '/accounts', '/vehicles', '/trips']
total = 0
for url in pages:
    r = cl.get(url)
    if r.status_code != 200:
        continue
    html = r.data.decode('utf-8', 'ignore')
    pts = re.findall(r'<polyline[^>]*\bpoints="([^"]*)"', html)
    bad = [p for p in pts if p.strip() and not GOOD.match(p)]
    total += len(pts)
    check(not bad, f'{url}: every <polyline> has valid points ({len(pts)} lines)' + (f' — bad: {bad[:2]}' if bad else ''))
check(total > 0, f'the scan really found chart lines ({total})')

# Business Performance in particular: both net-profit charts must draw a segment between each pair of months
html = cl.get('/business-performance?date_from=2026-04-01&date_to=2026-08-31').data.decode('utf-8', 'ignore')
months = len(re.findall(r'font-size="8.5" text-anchor="middle"', html)) // 2           # each chart prints one label per month
pl = re.findall(r'<polyline[^>]*\bpoints="([^"]*)"[^>]*stroke="(#2a78d6|#4a3aa7)"', html)
blue = [p for p, col in pl if col == '#2a78d6']
purple = [p for p, col in pl if col == '#4a3aa7']
check(months >= 5, f'Business Performance shows at least 5 months of data ({months})')
check(len(blue) == months - 1 and len(purple) == months - 1, f'both profit charts draw one line segment between each pair of months ({len(blue)} and {len(purple)} for {months} months)')
check(all(GOOD.match(p) and len(p.split()) == 2 for p in blue + purple), 'each segment joins exactly two real points')

print(f'\nSUMMARY: {ok} passed, {fail} failed')
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fail else 0)
