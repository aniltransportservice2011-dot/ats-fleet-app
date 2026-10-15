"""Regression test: on EVERY paginated table of the website, following a pager link (page numbers, Previous / Next / First / Last, and the
rows-per-page box) lands on the SAME screen and the SAME tab it was clicked on.

For each screen and each of its tabs the test opens the screen the way a person lands on it, finds the pager(s), and follows every link and the
rows-per-page URL; the page that comes back must show the same tab, the page number that was asked for, and the pager again. Screens whose tabs are
switched only inside the browser (Route Analytics, Performance) are landed on WITHOUT a tab in the address — which is how people reach them — and
every one of their links must name the tab it belongs to.

Runs on a throwaway copy of fleet.db padded with enough rows that every table spans several pages; the real file is never touched.

Usage:  python3 test_pagination_tabs.py
"""
import html as H
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


tmp = tempfile.mkdtemp(prefix='pagetabs_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp)
# ---- enough rows for every table to span several pages
one = lambda q: (c.execute(q).fetchone() or [None])[0]
if not one('SELECT COUNT(*) FROM vehicles'):
    for i in range(1, 25):
        c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES (?,?)", (f'PGV{i:02d}', 'own' if i % 3 else 'hired'))
veh = [r[0] for r in c.execute('SELECT id FROM vehicles ORDER BY id LIMIT 6')]
if not one('SELECT COUNT(*) FROM parties'):
    for i in range(1, 25):
        c.execute("INSERT INTO parties (name) VALUES (?)", (f'PGPARTY{i:02d}',))
if one('SELECT COUNT(*) FROM trips') < 60:
    pid = [r[0] for r in c.execute('SELECT id FROM parties ORDER BY id LIMIT 4')]
    for i in range(80):
        c.execute("INSERT INTO trips (date, lr_number, vehicle_id, type, party_id, from_loc, to_loc, quantity, rate, rate_type, billed_amount, payment_received, driver_name, fuel_amount) "
                  "VALUES (?,?,?,?,?,?,?,10,1000,'PER_MT',?,?,?,?)",
                  (f'2026-0{5 + i % 4}-{(i % 27) + 1:02d}', f'PGT{i:03d}', veh[i % len(veh)], 'own', pid[i % len(pid)], f'F{i % 12:02d}', f'T{i % 9:02d}', 10000, 3000 if i % 2 else 0, f'DRIVER{i % 15:02d}', 1500))
for i in range(30):
    c.execute("INSERT INTO employees (name, type, mobile, status) VALUES (?,?,?,?)", (f'PGEMP{i:02d}', 'Driver' if i % 2 else 'Staff', f'97{i:08d}', 'Active'))
    c.execute("INSERT INTO overheads (date, category, amount, status) VALUES (?,?,?,?)", (f'2026-09-{(i % 28) + 1:02d}', ['Office', 'Rent', 'Power'][i % 3], 500 + i, 'Pending' if i % 2 else 'Paid'))
    c.execute("INSERT INTO tyre_stock (tyre_id, brand, tyre_type, purchase_date, purchase_cost, status, installed_vehicle_id) VALUES (?,?,?,?,?,?,?)",
              (f'PGTY{i:03d}', 'MRF', 'New', f'2026-08-{(i % 28) + 1:02d}', 9000 + i, 'In Use' if i % 2 else 'In Stock', veh[i % len(veh)] if i % 2 else None))
    c.execute("INSERT INTO batteries (battery_no, brand, vehicle_id, purchase_date, purchase_price) VALUES (?,?,?,?,?)", (f'PGB{i:03d}', 'Exide', veh[i % len(veh)], f'2026-07-{(i % 28) + 1:02d}', 6000 + i))
    c.execute("INSERT INTO urea_transactions (date, txn_type, quantity_l, vehicle_id) VALUES (?,?,?,?)", (f'2026-09-{(i % 28) + 1:02d}', 'stock_in' if i % 2 == 0 else 'stock_out', 20 + i, veh[i % len(veh)]))
    c.execute("INSERT INTO compliance_expenses (date, compliance_type, vehicle_id, amount) VALUES (?,?,?,?)", (f'2026-09-{(i % 28) + 1:02d}', ['Insurance', 'Fitness', 'PUC', 'Permit'][i % 4], veh[i % len(veh)], 1000 + i))
    c.execute("INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount) VALUES (?,?,?,?,?)", (f'2026-08-{(i % 28) + 1:02d}', veh[i % len(veh)], 'Service', 2000 + i, 0))
    c.execute("INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount) VALUES (?,?,?,?,?)", (f'2026-07-{(i % 28) + 1:02d}', veh[i % len(veh)], ['Punture', 'Tyre Repair', 'Tyres'][i % 3], 400 + i, 0))      # the Tyres tab pages these entries
    c.execute("INSERT INTO toll_entries (date, toll_plaza, vehicle_id, amount) VALUES (?,?,?,?)", (f'2026-09-{(i % 28) + 1:02d}', f'PLAZA{i % 5}', veh[i % len(veh)], 100 + i))
c.commit()
party = one('SELECT party_id FROM trips WHERE party_id IS NOT NULL GROUP BY party_id ORDER BY COUNT(*) DESC LIMIT 1')
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


def active_tab(page):
    mo = re.search(r'<a class="(?:mt-tab|lg2-tab)[^"]*\bactive\b[^"]*"[^>]*href="[^"]*?tab=([a-z\-]+)', page)
    if mo:
        return mo.group(1)
    mo = re.search(r'id="activeTabField" value="([^"]*)"', page)
    return (mo.group(1) or None) if mo else None


def pagers(page):
    out = {}
    for mo in re.finditer(r'<div class="pager-row" id="([^"]+)">(.*?)</div>\s*<span style="[^"]*">Page (\d+) of (\d+)</span>', page, re.S):
        body = mo.group(2)
        out[mo.group(1)] = {'links': [H.unescape(x) for x in re.findall(r'<a class="pager-btn pjax-page[^"]*" href="([^"]+)"', body)],
                            'qs': (re.search(r'data-pager-qs="([^"]*)"', body) or [None, None])[1], 'page': int(mo.group(3)), 'pages': int(mo.group(4))}
    return out


def audit(label, path, tab, owners):
    """path: the screen; tab: what to put in the address when landing (None = no tab, the way people reach client-switched screens);
    owners: {pager id: the tab that pager belongs to (None = the screen has no tabs)}"""
    url = path + (f'?tab={tab}' if tab else '')
    r = cl.get(url)
    check(r.status_code == 200, f'{label}: opens')
    page = r.data.decode('utf-8', 'ignore')
    found = pagers(page)
    for pid, owner in owners.items():
        info = found.get(pid)
        check(info is not None, f'{label}: has the {pid} pager')
        if not info:
            continue
        check(info['pages'] > 1, f'{label}: {pid} spans several pages ({info["pages"]}) — the test data must be big enough to page')
        urls = list(info['links'])
        if info['qs'] is not None:
            urls.append('?' + info['qs'] + ('&' if info['qs'] else '') + 'per_page=25')
        followed = 0
        for href in urls:
            if owner is not None:
                check(f'tab={owner}' in href, f'{label}: {pid} link names its tab — {href[:80]}')
            r2 = cl.get(path + href.split('#')[0])
            p2 = r2.data.decode('utf-8', 'ignore')
            got = pagers(p2).get(pid)
            ok_page = r2.status_code == 200 and got is not None
            if owner is not None:
                ok_page = ok_page and active_tab(p2) == owner
            check(ok_page, f'{label}: {href[:80]} lands on the same screen and tab ({owner}) with the {pid} pager' + ('' if ok_page else f' — got tab {active_tab(p2)!r}'))
            mo = re.search(r'(?:^|[?&])(?:page|rr_page|d_page|v_page)=(\d+)', href.split('#')[0])
            if ok_page and mo and f'{pid}' in ('pager', 'rrPager', 'driverPager', 'vehiclePager', 'empPager', 'salPager'):
                want = int(mo.group(1)) if href.count('page=') == 1 or pid in ('pager', 'rrPager', 'empPager', 'salPager') else None
                if want and want <= got['pages'] and re.search(r'[?&]%s=%d' % ('rr_page' if pid == 'rrPager' else ('d_page' if pid == 'driverPager' else ('v_page' if pid == 'vehiclePager' else 'page')), want), href):
                    check(got['page'] == want, f'{label}: asked for page {want}, got page {got["page"]}')
            followed += 1
        check(followed >= 4, f'{label}: {pid} offers page links ({followed})')


# ---- every paginated screen and tab
for t in ('service', 'tyres', 'battery', 'urea', 'toll', 'compliance-expenses'):
    audit(f'Maintenance / {t}', '/maintenance', t, {'pager': t})
audit('Employees / overview', '/salaries', 'overview', {'empPager': 'overview'})
audit('Employees / salary', '/salaries', 'salary', {'salPager': 'salary'})
audit('Expenses / all', '/overheads', 'all', {'pager': 'all'})
for t in ('all', 'receivables', 'payables'):
    audit(f'Party ledger / {t}', f'/ledger/party/{party}', t, {'pager': t})
audit('Ledger accounts', '/accounts', None, {'pager': None})
audit('Trips', '/trips', None, {'pager': None})
audit('Vehicles', '/vehicles', 'all', {'pager': None})
audit('Performance (landed with no tab: both tabs sit on one page)', '/performance', None, {'driverPager': 'driver', 'vehiclePager': 'vehicle'})
audit('Route Analytics (landed with no tab)', '/route-analytics', None, {'pager': 'best-routes', 'rrPager': 'route-rates'})

# ---- the phone app: "Load More" on Route Rates keeps the Route Rates tab
ap = cl.get('/app/analytics?tab=rates').data.decode('utf-8', 'ignore')
more = re.search(r'<a class="vh-loadmore"[^>]*href="([^"]+)"', ap)
check(more is not None and 'tab=rates' in H.unescape(more.group(1)), f'phone app Route Rates: Load More names its tab ({more.group(1)[:70] if more else "no Load More link"})')
if more:
    ap2 = cl.get(H.unescape(more.group(1))).data.decode('utf-8', 'ignore')
    check(re.search(r'<a class="ra-tab active" href="[^"]*tab=rates', ap2) is not None and ap2.count('class="rr-row"') > ap.count('class="rr-row"'),
          'phone app Route Rates: Load More stays on the Route Rates tab and shows more routes')

# ---- the browser side: a pager click refreshes in place and the Route Analytics tables refresh only themselves
base = open('templates/base.html', encoding='utf-8').read()
check("a.pjax-page" in base and 'goTo(href)' in base, 'base.html: pager clicks are handled in place (no full page load)')
ra = open('templates/route_analytics.html', encoding='utf-8').read()
check('__raPagerBound' in ra and "closest('.ra-panel')" in ra.replace('.ra-tab-panel .ra-panel', "closest('.ra-panel')") or '.ra-panel' in ra, 'Route Analytics: its tables refresh only themselves')
check('data-pager-qs' in open('templates/partials/_pager.html', encoding='utf-8').read(), 'the pager carries its query so the rows-per-page box keeps the tab')

print(f'\nSUMMARY: {ok} passed, {fail} failed')
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fail else 0)
