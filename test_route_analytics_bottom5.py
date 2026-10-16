"""Regression test: Route Analytics also shows the WORST routes — the bottom five by profit (a route that loses money, i.e. negative profit, comes first) and the
bottom five by revenue — next to the existing best ones, on the website and on the phone.

  A. the data (_route_analytics_data): lowest profit first with negative profits leading, ties in a fixed order, at most five, margin / revenue / trips carried;
     lowest revenue first with bars scaled to the biggest of the five; the existing best-five lists are unchanged;
  B. the From / To / vehicle-type filters apply to the worst lists exactly like the rest of the page; fewer than five routes and no routes at all do not break anything;
  C. the website page (Best Routes tab) and the phone page draw both worst lists, in order, negative profit in red.

Runs on a disposable copy of fleet.db whose trips are replaced by a handful of known ones (the real file is never touched, network blocked).
Usage:  python3 test_route_analytics_bottom5.py
"""
import os
import re
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


tmp = tempfile.mkdtemp(prefix='bottom5_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
uid = ensure_test_user(dbp)


def gdb():
    c = sqlite3.connect(dbp)
    c.row_factory = sqlite3.Row
    return c


m.get_db = gdb
m.app.config['TESTING'] = True
db = gdb()
db.execute("DELETE FROM trips")                       # the disposable copy only: replaced by known trips
veh_own = db.execute("SELECT id FROM vehicles WHERE type='own' ORDER BY id LIMIT 1").fetchone()
veh_hired = db.execute("SELECT id FROM vehicles WHERE type='hired' ORDER BY id LIMIT 1").fetchone()
if not veh_own:
    db.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('B5OWN','own')"); veh_own = db.execute("SELECT id FROM vehicles WHERE vehicle_no='B5OWN'").fetchone()
if not veh_hired:
    db.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('B5HIR','hired')"); veh_hired = db.execute("SELECT id FROM vehicles WHERE vehicle_no='B5HIR'").fetchone()
n = [0]


def trip(frm, to, billed, fuel, vtype='own'):
    n[0] += 1
    db.execute("INSERT INTO trips (date, lr_number, from_loc, to_loc, type, rate_type, rate, quantity, billed_amount, vehicle_id, fuel_amount, driver_adv_amount, is_empty) "
               "VALUES ('2026-09-01',?,?,?,?,'PER_MT',?,10,?,?,?,0,0)", (f'B5-{n[0]}', frm, to, vtype, billed / 10.0, billed, (veh_own if vtype == 'own' else veh_hired)['id'], fuel))


# (route, billed, fuel)  ->  profit
trip('R1A', 'R1B', 30000, 25000)        # +5,000
trip('R2A', 'R2B', 5000, 8000)          # -3,000
trip('R3A', 'R3B', 12000, 21000)        # -9,000
trip('R4A', 'R4B', 7000, 7000)          # 0
trip('R5A', 'R5B', 4100, 4000)          # +100
trip('R6A', 'R6B', 90000, 70000)        # +20,000
trip('R7A', 'R7B', 3300, 3000)          # +300
trip('R8A', 'R8B', 1000, 1500)          # -500, the smallest revenue
trip('R6A', 'R6B', 10000, 9000)         # a second trip on R6: +1,000 more (R6 = +21,000, revenue 100,000)
db.commit()

cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid


def data(frm='', to='', vt=''):
    c = gdb()
    try:
        return m._route_analytics_data(c, None, None, frm, to, vt)
    finally:
        c.close()


# --------------------------------------------------------------------------------------------------------------------  A. the data
d = data()
names = lambda rows: [r['clean_from'][:2] for r in rows]
check(names(d['bottom5_by_profit']) == ['R3', 'R2', 'R8', 'R4', 'R5'], f'A: lowest profit first, losses leading: {names(d["bottom5_by_profit"])}')
check([r['profit'] for r in d['bottom5_by_profit']] == [-9000, -3000, -500, 0, 100], f'A: with the right profits: {[r["profit"] for r in d["bottom5_by_profit"]]}')
check(len(d['bottom5_by_profit']) == 5 and len(d['bottom5_by_revenue']) == 5, 'A: five of each')
r3 = d['bottom5_by_profit'][0]
check(r3['trips'] == 1 and r3['revenue'] == 12000 and r3['margin'] == -75.0, f'A: trips / revenue / margin are carried ({r3["trips"]}, {r3["revenue"]}, {r3["margin"]})')
check(names(d['bottom5_by_revenue']) == ['R8', 'R7', 'R5', 'R2', 'R4'], f'A: lowest revenue first: {names(d["bottom5_by_revenue"])}')
check(d['bottom5_by_revenue'][-1]['pct'] == 100.0 and d['bottom5_by_revenue'][0]['pct'] == round(1000 / 7000 * 100, 1), 'A: revenue bars are scaled to the biggest of the five')
check(names(d['top5_by_revenue']) == ['R6', 'R1', 'R3', 'R4', 'R2'] and names(d['top5_by_margin'])[0] == 'R6', f'A: the best-five lists are unchanged: {names(d["top5_by_revenue"])} / {names(d["top5_by_margin"])}')
check(all(r['route'] for r in d['bottom5_by_profit']), 'A: every row carries its route name')
check(d['bottom5_by_profit'] == data()['bottom5_by_profit'], 'A: the order is stable between calls')

# --------------------------------------------------------------------------------------------------------------------  B. filters, small and empty
d = data(frm='R3A')
check(names(d['bottom5_by_profit']) == ['R3'] and names(d['bottom5_by_revenue']) == ['R3'], 'B: the From filter narrows the worst lists too')
db.execute("UPDATE trips SET type='hired', vehicle_id=? WHERE from_loc='R2A'", (veh_hired['id'],)); db.commit()
d = data(vt='hired')
check(names(d['bottom5_by_profit']) == ['R2'], f'B: the vehicle-type filter applies: {names(d["bottom5_by_profit"])}')
d = data(frm='NOWHERE')
check(d['bottom5_by_profit'] == [] and d['bottom5_by_revenue'] == [], 'B: no routes -> empty lists, no error')

# --------------------------------------------------------------------------------------------------------------------  C. the pages
db.execute("UPDATE trips SET type='own', vehicle_id=? WHERE from_loc='R2A'", (veh_own['id'],)); db.commit()
html = cl.get('/route-analytics').data.decode('utf-8', 'ignore')
check('Bottom 5 Worst Performing Routes' in html and 'Bottom 5 Routes by Revenue' in html, 'C: the website draws both worst panels')
sec = html.split('Bottom 5 Worst Performing Routes')[1].split('Bottom 5 Routes by Revenue')[0]
order = [x for x in re.findall(r'(R\dA) &rarr;', sec)]
check(order == ['R3A', 'R2A', 'R8A', 'R4A', 'R5A'], f'C: website: worst first, in order: {order}')
check('₹-9,000' in sec and re.search(r'color:var\(--red\);font-weight:600;">₹-9,000', sec) is not None, 'C: website: negative profit is shown in red')
check(re.search(r'color:var\(--green\);font-weight:600;">₹100<', sec) is not None, 'C: website: a small positive profit stays green')
sec2 = html.split('Bottom 5 Routes by Revenue')[1].split('Route Summary')[0]
check(re.findall(r'(R\dA) &rarr;', sec2) == ['R8A', 'R7A', 'R5A', 'R2A', 'R4A'], 'C: website: revenue bars, lowest first')
check('Top 5 Best Performing Routes' in html and 'Top Routes by Revenue' in html, 'C: the best panels are still there')
r = cl.get('/route-analytics?tab=best-routes&from=NOWHERE')
check(r.status_code == 200 and 'No routes found for this selection' in r.data.decode(), 'C: website: an empty selection says so')

phone = cl.get('/app/analytics?tab=best').data.decode('utf-8', 'ignore')
check('Bottom 5 Worst Performing Routes' in phone and 'Bottom 5 Routes by Revenue' in phone, 'C: the phone draws both worst panels')
psec = phone.split('Bottom 5 Worst Performing Routes')[1].split('Bottom 5 Routes by Revenue')[0]
porder = re.findall(r'(R\dA) → ', psec)
check(porder == ['R3A', 'R2A', 'R8A', 'R4A', 'R5A'], f'C: phone: worst first, in order: {porder} (period filter may apply: {len(porder)} rows)')
check('ra-route-margin neg' in psec and '-₹9,000' in psec, 'C: phone: a loss is shown as a red negative amount')
check('rbad' in psec, 'C: phone: loss-making ranks are red')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
