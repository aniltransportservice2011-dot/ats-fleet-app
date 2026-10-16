"""Regression test: every table can be sorted by clicking its column headers.

  A. the engine: _table_sort (columns by name or function, text without regard to case, numbers as numbers, blanks always last either way, stable ties, unknown key
     ignored, separate parameter names for a second table on a page) and the sort_th header (keeps the page's filters, drops the page number, flips the direction,
     ⇅ / ▲ / ▼, extra parameters such as the tab);
  B. every paginated table, with real rows behind it: for EACH sortable column, ascending and descending, the rows read across ALL pages are in order — not
     just the page on screen — with empty cells last, and the sort link of the opposite direction is offered;
  C. the tables that show all their rows are sorted in the browser: every one of them has the script, no paginated table is left to it, tables you type into are left
     alone, and the script's own reading of numbers (₹ , % Dr / Cr), dates and text is run for real in Node;
  D. a user who may not see trip amounts cannot sort trips by them.

Runs on a disposable copy of fleet.db with extra rows (the real file is never touched, network blocked).  Usage:  python3 test_table_sorting.py
"""
import datetime
import glob
import html as H
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from html.parser import HTMLParser

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


tmp = tempfile.mkdtemp(prefix='tablesort_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
uid = ensure_test_user(dbp)


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
read = lambda p: open(p, encoding='utf-8').read()

# =====================================================================================================  A. the engine
with m.app.test_request_context('/x?sort=name&dir=asc'):
    rows = [{'name': 'banana', 'n': 3}, {'name': 'Apple', 'n': 10}, {'name': '', 'n': None}, {'name': 'cherry', 'n': 2}, {'name': 'apple', 'n': 10}, {'name': None, 'n': 1}]
    out, key, d = m._table_sort(rows, {'name': 'name', 'n': 'n'})
    check([r['name'] for r in out] == ['Apple', 'apple', 'banana', 'cherry', '', None] and (key, d) == ('name', 'asc'), f'A: text A-Z without regard to case, blanks last: {[r["name"] for r in out]}')
with m.app.test_request_context('/x?sort=name&dir=desc'):
    out, key, d = m._table_sort(rows, {'name': 'name'})
    check([r['name'] for r in out] == ['cherry', 'banana', 'Apple', 'apple', '', None] and d == 'desc', f'A: descending also keeps blanks last and equal rows in their order: {[r["name"] for r in out]}')
with m.app.test_request_context('/x?sort=n&dir=asc'):
    out, _, _ = m._table_sort(rows, {'n': 'n'})
    check([r['n'] for r in out] == [1, 2, 3, 10, 10, None], f'A: numbers sort as numbers (10 after 3): {[r["n"] for r in out]}')
with m.app.test_request_context('/x?sort=n&dir=desc'):
    out, _, _ = m._table_sort(rows, {'n': lambda r: r['n']})
    check([r['n'] for r in out] == [10, 10, 3, 2, 1, None], 'A: a column given as a function works')
    check([r['name'] for r in out][:2] == ['Apple', 'apple'], 'A: ties keep their original order when descending too')
with m.app.test_request_context('/x?sort=hack;drop&dir=asc'):
    out, key, d = m._table_sort(rows, {'name': 'name'})
    check(out is rows and key == '' and d == '', 'A: an unknown column is ignored (rows untouched)')
with m.app.test_request_context('/x?sort=name'):
    out, key, d = m._table_sort(rows, {'name': 'name'})
    check(d == 'asc', 'A: no direction means ascending')
with m.app.test_request_context('/x?sort=missing&dir=asc'):
    out, _, _ = m._table_sort([{'a': 1}, {'a': 2}], {'missing': 'b'})
    check(len(out) == 2, 'A: a row without the column does not crash the page')
with m.app.test_request_context('/x?d_sort=n&d_dir=desc&sort=name'):
    out, key, _ = m._table_sort(rows, {'n': 'n'}, sp='d_sort', dp='d_dir')
    check(key == 'n' and out[0]['n'] == 10, 'A: a second table on a page has its own parameter names')
    out, key, _ = m._table_sort(rows, {'n': 'n'})
    check(key == '', "A: and the other table's sort does not move this one")
with m.app.test_request_context('/trips?party=AB&sort=date&dir=desc&page=4&per_page=50'):
    h = str(m._sort_th('Date', 'date'))
    check('sort=date' in h and 'dir=asc' in h and 'party=AB' in h and 'page=' not in h.replace('per_page', '') and 'per_page=50' in h, f'A: header link keeps the filters, drops the page, flips desc -> asc: {h}')
    check('▼' in h and 'sort-ic on' in h, 'A: the active descending column shows ▼')
    h = str(m._sort_th('Party', 'party'))
    check('⇅' in h and 'dir=asc' in h and 'sort-ic on' not in h, 'A: a column that is not sorted shows ⇅ and sorts ascending first')
    h = str(m._sort_th('Billed', 'billed', first='desc'))
    check('dir=desc' in h, 'A: a number column can sort high-to-low first')
with m.app.test_request_context('/trips?party=AB'):
    h = str(m._sort_th('Name', 'name', default=True, defdir='asc'))
    check('▲' in h and 'dir=desc' in h, f'A: the column the page is ordered by already shows its arrow, and a click reverses it: {h}')
    h = str(m._sort_th('Date', 'date', default=True, defdir='desc', first='desc'))
    check('▼' in h and 'dir=asc' in h, 'A: a default descending column shows ▼ and offers ascending')
with m.app.test_request_context('/p?tab=driver&d_sort=name&d_dir=asc&v_page=3'):
    h = str(m._sort_th('Driver', 'name', sp='d_sort', dp='d_dir', pagekey='d_page', extra={'tab': 'driver'}))
    check('d_sort=name' in h and 'd_dir=desc' in h and 'tab=driver' in h and 'v_page=3' in h, f'A: a header with its own parameters keeps the other table\'s page and sets its tab: {h}')
    h = str(m._sort_th('Age <span title="x">&#9432;</span>', 'age'))
    check('&#9432;' in h and 'title="Sort by Age"' in h, 'A: a label with an info icon keeps the icon and gives a plain title')

# =====================================================================================================  B. every paginated table, with data
db = gdb()
n_before = {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('overheads', 'maintenance', 'employees', 'toll_entries', 'urea_transactions', 'batteries', 'tyre_stock', 'compliance_expenses')}
veh = [r['id'] for r in db.execute("SELECT id FROM vehicles WHERE type='own' ORDER BY id LIMIT 6").fetchall()]
vend = [r['id'] for r in db.execute("SELECT id FROM vendors ORDER BY id LIMIT 4").fetchall()]
d0 = datetime.date(2026, 9, 1)
dt = lambda i: (d0 + datetime.timedelta(days=(i * 7) % 40)).isoformat()
names = ['Zed', 'amar', 'Bala', 'chandan', 'Dev', 'eshan', 'Farid', 'Gopal', 'hari', 'Imran', 'Jai', 'Kiran', 'lal', 'Mohan']
for i in range(14):
    db.execute("INSERT INTO overheads (date, category, amount, payment_mode, vendor, description, status, due_date) VALUES (?,?,?,?,?,?,?,?)",
               (dt(i), ['Rent', 'Office', 'Electricity', 'Tea', 'Internet'][i % 5], 500 + (i * 137) % 900, ['Cash', 'UPI', 'Bank Transfer'][i % 3], (None if i % 6 == 0 else names[i % 7]),
                (None if i % 5 == 0 else f'desc {names[(i * 3) % 14]}'), ['Paid', 'Pending'][i % 2], dt(i + 3)))
    db.execute("INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, km_reading, service_type, next_due_km, next_service_date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (dt(i), veh[i % 6], 'Service', 1000 + (i * 313) % 4000, (0 if i % 3 else 1000 + (i * 313) % 4000), vend[i % 4], (None if i % 5 == 0 else 50000 + i * 900), ['Oil Change', 'General', 'Brake'][i % 3],
                (None if i % 2 else 60000 + i * 100), (dt(i + 20) if i % 2 else None), ['Completed', 'Scheduled'][i % 2]))
    db.execute("INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, tyre_action, tyre_id, tyre_brand, tyre_position, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (dt(i), veh[i % 6], 'Tyre', 8000 + (i * 411) % 6000, 0, vend[i % 4], ['New Tyre Fitted', 'Tyre Resole', 'Puncture Repair'][i % 3], f'TY{(i * 7) % 20:02d}', ['MRF', 'Apollo', 'JK'][i % 3], ['FL', 'FR', 'RL1'][i % 3], 'Completed'))
    db.execute("INSERT INTO compliance_expenses (date, vehicle_id, compliance_type, description, vendor_id, amount, payment_mode) VALUES (?,?,?,?,?,?,?)",
               (dt(i), veh[i % 6], ['Insurance', 'PUC', 'Fitness', 'Permit'][i % 4], (None if i % 4 == 0 else f'renewal {i}'), vend[i % 4], 700 + (i * 97) % 3000, ['Cash', 'UPI'][i % 2]))
    db.execute("INSERT INTO toll_entries (date, time, vehicle_id, toll_plaza, highway, amount, source, status) VALUES (?,?,?,?,?,?,?,?)",
               (dt(i), f'{8 + i % 10:02d}:{(i * 13) % 60:02d}', veh[i % 6], f'Plaza {names[i % 14]}', (None if i % 4 == 0 else ['NH-49', 'NH-16', 'NH-6'][i % 3]), 100 + (i * 53) % 700, ['manual', 'fastag'][i % 2], ['pending', 'approved', 'rejected'][i % 3]))
    db.execute("INSERT INTO urea_transactions (date, txn_type, source, supplier_id, vehicle_id, quantity_l, unit_price, total_value, location) VALUES (?,?,?,?,?,?,?,?,?)",
               (dt(i), 'stock_in' if i % 3 == 0 else 'stock_out', 'stock' if i % 4 else 'direct', vend[i % 4] if i % 3 == 0 else None, None if i % 3 == 0 else veh[i % 6], 20 + (i * 11) % 80, 60, (20 + (i * 11) % 80) * 60, ['Yard', 'Depot', None][i % 3]))
    db.execute("INSERT INTO batteries (battery_no, brand, model, capacity_ah, vehicle_id, install_date, purchase_date, purchase_price, warranty_months, health_pct) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f'BAT-{(i * 5) % 17:03d}', ['Exide', 'Amaron', 'Luminous'][i % 3], ['A', 'B'][i % 2], 100 + 10 * (i % 4), (None if i % 4 == 0 else veh[i % 6]), (None if i % 4 == 0 else dt(i)), dt(i), 7000 + i * 100, 24, 60 + i))
    db.execute("INSERT INTO employees (name, type, employee_code, role, joining_date, status, basic_salary, mobile) VALUES (?,?,?,?,?,?,?,?)",
               (f'{names[i]} Emp', ['Driver', 'Staff'][i % 2], f'E{i:03d}', ['Driver', 'Clerk', 'Manager'][i % 3], (None if i % 5 == 0 else dt(i)), 'Active', 12000 + i * 500, f'98000000{i:02d}'))
db.commit()
emp_ids = [r['id'] for r in db.execute("SELECT id, name FROM employees WHERE name LIKE '% Emp' ORDER BY id").fetchall()]
for i, eid in enumerate(emp_ids):
    for k in range(1 + i % 3):
        db.execute("INSERT INTO salaries (employee_id, employee, month, amount, date, net_salary, payment_status) VALUES (?,?,?,?,?,?,?)",
                   (eid, f'{names[i]} Emp', 'Sep', 10000 + i * 300, f'2026-09-{10 + k:02d}', 10000 + i * 300 + k * 100, 'Paid'))
db.commit()
db.close()

cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid


class Tables(HTMLParser):
    """every <table> as rows of cells: [{'th': bool, 'cls': str, 'href': str|None, 'text': str, 'first': str}]"""
    def __init__(self):
        super().__init__(); self.tables = []; self.stack = []; self.cell = None; self.row = None; self.depth = 0
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'table':
            self.stack.append([])
        elif tag == 'tr' and self.stack:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'th': tag == 'th', 'cls': a.get('class', ''), 'href': None, 'segs': [''], 'colspan': int(a.get('colspan', 1) or 1)}
        elif self.cell is not None:
            if tag == 'a' and a.get('class', '').startswith('sort-link'):
                self.cell['href'] = a.get('href')
            if tag in ('br', 'div', 'p', 'li'):
                self.cell['segs'].append('')
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            segs = [' '.join(x.split()) for x in self.cell['segs']]
            segs = [x for x in segs if x]
            self.cell['text'] = ' '.join(segs); self.cell['first'] = segs[0] if segs else ''; self.cell['segs'] = segs
            self.row.append(self.cell); self.cell = None
        elif tag == 'tr' and self.row is not None and self.stack:
            self.stack[-1].append(self.row); self.row = None
        elif tag == 'table' and self.stack:
            self.tables.append(self.stack.pop())
    def handle_data(self, d):
        if self.cell is not None:
            self.cell['segs'][-1] += d


def tables_of(html):
    p = Tables(); p.feed(html)
    return p.tables


def sortable_table(html, want_key=None, sp=None):
    """(header cells, data rows) of the first table that has sortable headers (and the given key)"""
    for t in tables_of(html):
        heads = [r for r in t if r and all(c['th'] for c in r) and any(c['cls'].startswith('sortable') or 'sortable' in c['cls'] for c in r)]
        if not heads:
            continue
        head = heads[-1]
        if sp and len({(re.search(r'[?&]%s=([a-z_0-9]+)' % sp, c['href'] or '') or [None, None])[1] for c in head} - {None}) < 2:
            continue                                   # this table's own links carry a different key in every header; another table's sort is the same in all of them
        if want_key and not any(c['href'] and f'sort={want_key}&' in c['href'] or (c['href'] and re.search(r'[?&][a-z]*_?sort=%s(&|$)' % want_key, c['href'])) for c in head):
            continue
        body = [r for r in t if r and not all(c['th'] for c in r) and len(r) == len(head)]
        return head, body
    return None, []


MONTH = {m_: i for i, m_ in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}


def as_number(t):
    s = re.sub(r'[₹,%\s]', '', t.replace('−', '-'))
    sign = -1 if re.search(r'Cr$', s) else 1
    s = re.sub(r'(Dr|Cr)$', '', s).replace('+', '')
    s = re.sub(r'(KM|AH|Years|L)$', '', s)
    s = s.lstrip('≈')
    return sign * float(s) if re.fullmatch(r'-?\d+(\.\d+)?', s) else None


def as_date(t):
    mo = re.match(r'(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2}))?', t)
    if mo:
        return float(f'{mo.group(1)}{mo.group(2)}{mo.group(3)}{mo.group(4) or "00"}{mo.group(5) or "00"}')
    mo = re.match(r'(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})', t)
    if mo and mo.group(2).lower() in MONTH:
        return float(f'{mo.group(3)}{MONTH[mo.group(2).lower()] + 1:02d}{int(mo.group(1)):02d}0000')
    return None


def column_values(rows, c, extract=None):
    """the sort value of every row's column c (None = blank), in the order shown"""
    texts = [(extract(r[c]) if extract else r[c]['first']) for r in rows]
    blank = lambda t: t is None or t.strip() in ('', '—', '-', 'None')       # a trip with no party shows the text None
    present = [t for t in texts if not blank(t)]
    if present and all(as_date(t) is not None for t in present):
        conv = as_date
    elif present and all(as_number(t) is not None for t in present):
        conv = as_number
    else:
        conv = lambda t: t.lower()
    return [None if blank(t) else conv(t) for t in texts]


def in_order(values, direction):
    nz = [v for v in values if v is not None]
    tail_blank = all(v is None for v in values[len(nz):]) and all(v is not None for v in values[:len(nz)])
    if not tail_blank:
        return False, 'blank cells are not all at the end'
    pairs = list(zip(nz, nz[1:]))
    good = all(a <= b for a, b in pairs) if direction == 'asc' else all(a >= b for a, b in pairs)
    return good, '' if good else 'order is wrong'


def all_pages(url, page_param='page', per_page_param='per_page', per_page=None, max_pages=40, parse=None, sp=None):
    """the data rows of EVERY page of a table, in order, plus the header cells of page 1"""
    rows, head, page = [], None, 1
    while page <= max_pages:
        sep = '&' if '?' in url else '?'
        u = f'{url}{sep}{page_param}={page}' + (f'&{per_page_param}={per_page}' if per_page else '')
        r = cl.get(u)
        if r.status_code != 200:
            return None, None, r.status_code
        h = r.data.decode('utf-8', 'ignore')
        hd, body = (parse or sortable_table)(h) if not sp else sortable_table(h, sp=sp)
        if hd is None:
            return None, None, 'no sortable table'
        head = head or hd
        if not body or (rows and body == rows[-len(body):] and page > 1):
            break
        rows += body
        mo = re.search(r'Page %d of (\d+)' % page, h)
        if not mo or page >= int(mo.group(1)):
            break
        page += 1
    return head, rows, 200


def sort_key_of(cell, sp='sort'):
    mo = re.search(r'[?&]%s=([a-z_0-9]+)' % sp, cell['href'] or '')
    return mo.group(1) if mo else None


def date_in_cell(c):
    for s in c['segs']:
        mo = re.search(r'\d{1,2} [A-Za-z]{3} \d{4}', s)
        if mo:
            return mo.group(0)
    return ''


def trip_status(c):
    return 'Completed' if c['first'].startswith('Completed') else 'Active'


# (label, url, per-page param, per-page size, sort param, direction param, page param, {key: extractor}, keys to skip)
CASES = [
    ('accounts', '/accounts?per_page=10', 'sort', 'dir', 'page', {}, {}),
    ('vehicles', '/vehicles?per_page=10', 'sort', 'dir', 'page',
     {'insurance': date_in_cell, 'fitness': date_in_cell, 'puc': date_in_cell, 'permit': date_in_cell, 'roadtax': date_in_cell, 'status': lambda c: c['first']}, {}),
    ('trips', '/trips?per_page=25', 'sort', 'dir', 'page', {'status': trip_status}, {}),
    ('overheads', '/overheads?per_page=8', 'sort', 'dir', 'page', {'status': lambda c: c['first'].split(' ·')[0]}, {}),
    ('employees overview', '/salaries?tab=overview&per_page=5', 'sort', 'dir', 'page', {'attendance': lambda c: c['first']}, {'attendance', 'salary'}),
    ('employees salary', '/salaries?tab=salary&per_page=5', 'sort', 'dir', 'page', {}, {}),
    ('urea', '/maintenance?tab=urea&per_page=10', 'sort', 'dir', 'page', {'type': lambda c: c['first'], 'value': lambda c: c['first'].lstrip('≈')}, {}),
    ('toll', '/maintenance?tab=toll&per_page=10', 'sort', 'dir', 'page', {'date': lambda c: c['text']}, {}),
    ('compliance', '/maintenance?tab=compliance-expenses&per_page=10', 'sort', 'dir', 'page', {}, {}),
    ('tyres', '/maintenance?tab=tyres&per_page=10', 'sort', 'dir', 'page', {}, {}),
    ('battery', '/maintenance?tab=battery&per_page=10', 'sort', 'dir', 'page', {'type': lambda c: c['first']}, {}),
    ('service', '/maintenance?tab=service&per_page=10', 'sort', 'dir', 'page', {'due': lambda c: c['first']}, {'due'}),
    ('performance drivers', '/performance?tab=driver&per_page=10', 'd_sort', 'd_dir', 'd_page', {'fuel': lambda c: c['first'].lstrip('-')}, {}),
    ('performance vehicles', '/performance?tab=vehicle&per_page=10', 'v_sort', 'v_dir', 'v_page', {'fuel': lambda c: c['first'].lstrip('-'), 'maint': lambda c: c['first'].lstrip('-')}, {}),
    ('route summary', '/route-analytics?tab=best-routes&per_page=10', 'sort', 'dir', 'page', {'route': lambda c: c['first']}, {}),
]
# the party with the most entries, for the ledger
party = gdb().execute("SELECT party_id FROM trips WHERE party_id IS NOT NULL GROUP BY party_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
CASES.append(('party ledger', f'/ledger/party/{party}?per_page=10', 'sort', 'dir', 'page', {}, {}))

for label, url, sp, dp, pk, extract, skip in CASES:
    r = cl.get(url)
    if r.status_code != 200:
        check(False, f'B[{label}]: the page opens ({r.status_code})')
        continue
    head, rows0 = sortable_table(r.data.decode('utf-8', 'ignore'), sp=(sp if sp != 'sort' else None))
    if not head:
        check(False, f'B[{label}]: the page has sortable headers (no data behind it?)')
        continue
    keys = [(i, sort_key_of(c, sp)) for i, c in enumerate(head) if sort_key_of(c, sp)]
    check(len(keys) >= 2, f'B[{label}]: {len(keys)} sortable columns offered')
    for i, key in keys:
        if key in skip:
            continue
        for direction in ('asc', 'desc'):
            head2, rows, code = all_pages(f'{url}&{sp}={key}&{dp}={direction}', page_param=pk, sp=(sp if sp != 'sort' else None))
            if rows is None:
                check(False, f'B[{label}]: {key} {direction}: page opens ({code})')
                continue
            vals = column_values(rows, i, extract.get(key))
            good, why = in_order(vals, direction)
            check(good and len(rows) >= 2, f'B[{label}]: sorting by "{key}" {direction}: all {len(rows)} rows across every page are in order ({why}) first values {vals[:4]} … {vals[-3:]}')
        # the icon / link state after sorting
        head2, rows, _ = all_pages(f'{url}&{sp}={key}&{dp}=desc', page_param=pk, max_pages=1, sp=(sp if sp != 'sort' else None))
        c = head2[i]
        check('▼' in c['text'] and f'{dp}=asc' in (c['href'] or ''), f'B[{label}]: "{key}" sorted descending shows ▼ and offers ascending next')

# the route-rates matrix (two-row header): From / To order, and the numeric columns open
for key, col in (('from', 0), ('to', 1)):
    for direction in ('asc', 'desc'):
        rows, page = [], 1
        while page < 30:
            h = cl.get(f'/route-analytics?tab=route-rates&per_page=10&rr_sort={key}&rr_dir={direction}&rr_page={page}').data.decode('utf-8', 'ignore')
            body = [r for t in tables_of(h) for r in t if r and r[0]['cls'].find('sticky-from') >= 0 and not r[0]['th']]
            rows += [r[col]['first'].lstrip('▸ ').strip() for r in body]
            mo = re.search(r'Page %d of (\d+)' % page, h.split('rrPager')[-1]) if 'rrPager' in h else None
            tot = int(re.findall(r'Page \d+ of (\d+)', h.split('id="rrPager"')[-1])[0]) if 'id="rrPager"' in h and re.findall(r'Page \d+ of (\d+)', h.split('id="rrPager"')[-1]) else 1
            if page >= tot:
                break
            page += 1
        lows = [x.lower() for x in rows]
        check(len(rows) > 10 and lows == sorted(lows, reverse=(direction == 'desc')), f'B[route rates]: "{key}" {direction}: {len(rows)} rows across every page in order')
for key in ('own_average', 'hired_party_trips', 'hired_owner_highest', 'margin_avg', 'margin_pct', 'own_lowest'):
    r = cl.get(f'/route-analytics?tab=route-rates&rr_sort={key}&rr_dir=desc')
    check(r.status_code == 200 and f'rr_sort={key}' not in r.data.decode('utf-8', 'ignore').split('Route Rates')[0], f'B[route rates]: sorting by {key} opens')
h = cl.get('/route-analytics?tab=route-rates&rr_sort=own_average&rr_dir=desc').data.decode('utf-8', 'ignore')
check(h.count('rr_sort=') >= 12 and 'tab=route-rates' in h and 'rr_page' not in re.findall(r'href="([^"]*rr_sort[^"]*)"', H.unescape(h))[0], 'B[route rates]: the headers keep the tab and start from page 1')

# a header link really leads to the sorted page (follow it, like a click) and keeps the filters
r = cl.get('/trips?party=&vehicle=&per_page=25&page=2')
head, _ = sortable_table(r.data.decode('utf-8', 'ignore'))
link = H.unescape([c for c in head if sort_key_of(c) == 'vehicle'][0]['href'])
r2 = cl.get(link)
check(r2.status_code == 200 and 'per_page=25' in link and 'page=2' not in link.replace('per_page', ''), f'B: following a header link opens the sorted page 1 ({link})')
head2, body2 = sortable_table(r2.data.decode('utf-8', 'ignore'))
vcol = [i for i, c in enumerate(head2) if sort_key_of(c) == 'vehicle'][0]
check(head2[vcol]['text'].endswith('▲') and [r_[vcol]['first'].lower() for r_ in body2] == sorted(r_[vcol]['first'].lower() for r_ in body2), 'B: and that page reads A-Z by vehicle')

# =====================================================================================================  C. the browser-side sorting
html_files = sorted(glob.glob('templates/**/*.html', recursive=True))
base = read('templates/base.html')
check("asset_v('table_sort.js')" in base, 'C: the website loads the table-sort script on every screen')
js = read('static/table_sort.js')
check('th.sortable' in js and 'hasPager' in js and 'data-nosort' in js, 'C: the script leaves server-sorted tables, tables with a pager and data-nosort tables alone')
# no paginated table may depend on the browser-side sorting: every template that draws a pager sorts its table on the server
paged = [f for f in html_files if 'render_pager(' in read(f) and '<table' in read(f) and '/app/' not in f.replace('\\', '/')]
unsorted_paged = []
for f in paged:
    s = read(f)
    for mo in re.finditer(r'<table[^>]*>(.*?)</table>', s, re.S):
        after = s[mo.end():mo.end() + 700]
        if 'render_pager(' in after and 'sort_th(' not in mo.group(1) and 'sticky-from' not in mo.group(1):
            unsorted_paged.append(f)
check(not unsorted_paged, f'C: every table that is followed by a pager sorts on the server: {unsorted_paged}')
# ---- the script's own reading of values, run in Node
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync('static/table_sort.js', 'utf8');
const win = {}; const doc = { readyState: 'complete', querySelectorAll: () => [], documentElement: {}, addEventListener() {} };
const ctx = { window: win, document: doc, setTimeout: (f) => 0, clearTimeout() {}, MutationObserver: undefined, Node: {} };
win.document = doc; vm.createContext(ctx); vm.runInContext(src, ctx);
const t = win.__tableSortParse, cell = (s) => ({ textContent: s });
const rows = (arr) => arr.map(s => ({ cells: [cell(s)] }));
console.log(JSON.stringify({
  n1: t.parseNumber('₹1,23,456'), n2: t.parseNumber('12.5%'), n3: t.parseNumber('₹5,000 Cr'), n4: t.parseNumber('₹5,000 Dr'), n5: t.parseNumber('(300)'), n6: t.parseNumber('abc'), n7: t.parseNumber('+7'),
  d1: t.parseDate('2026-10-16'), d2: t.parseDate('16/10/2026'), d3: t.parseDate('16 Oct 2026'), d4: t.parseDate('2026-10-16 09:30'), d5: t.parseDate('hello'),
  k_num: t.columnKind(rows(['₹10', '₹2', '—']), 0), k_date: t.columnKind(rows(['2026-01-02', '5 Mar 2026']), 0), k_text: t.columnKind(rows(['abc', '12']), 0), k_empty: t.columnKind(rows(['—', '']), 0),
  v_blank: t.cellValue(cell('—'), 'text'), v_text: t.cellValue(cell('Zed'), 'text'), v_num: t.cellValue(cell('₹1,000'), 'num'),
  order_ok: t.parseDate('2026-10-16') > t.parseDate('2026-10-15') && t.parseDate('16 Oct 2026') === t.parseDate('2026-10-16') && t.parseDate('16/10/2026') === t.parseDate('2026-10-16'),
}));
"""
open(os.path.join(tmp, 'h.js'), 'w').write(HARNESS)
res = subprocess.run(['node', os.path.join(tmp, 'h.js')], capture_output=True, text=True, cwd=REPO)
try:
    R = json.loads(res.stdout.strip().splitlines()[-1])
except Exception:
    R = None
    print(res.stdout, res.stderr)
check(R is not None, 'C: the script runs in Node')
if R:
    check(R['n1'] == 123456 and R['n2'] == 12.5 and R['n3'] == -5000 and R['n4'] == 5000 and R['n5'] == -300 and R['n6'] is None and R['n7'] == 7, f'C: numbers are read with ₹ , % Dr Cr and (negatives): {R}')
    check(R['d1'] is not None and R['d4'] > R['d1'] and R['d5'] is None and R['order_ok'], 'C: ISO, dd/mm/yyyy and "16 Oct 2026" dates all read as the same moment')
    check((R['k_num'], R['k_date'], R['k_text'], R['k_empty']) == ('num', 'date', 'text', 'text'), f'C: a column is a number / date / text column by what it holds ({R["k_num"]}, {R["k_date"]}, {R["k_text"]}, {R["k_empty"]})')
    check(R['v_blank'] is None and R['v_text'] == 'zed' and R['v_num'] == 1000, 'C: blank cells have no value (they go last)')

# =====================================================================================================  D. hidden amounts
c = gdb()
c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, hide_trip_amounts, status, phone, created_at) "
          "VALUES (900601,'u900601','x','Manager',0,'Other','Limited Access','Trips:v',1,'Active','910006001','2026-01-01')")
c.commit(); c.close()
hid = m.app.test_client()
with hid.session_transaction() as s:
    s['user_id'] = 900601
r = hid.get('/trips?sort=billed&dir=desc')
txt = r.data.decode('utf-8', 'ignore')
head, body = sortable_table(txt)
keys = [sort_key_of(c_) for c_ in (head or [])]
check(r.status_code == 200 and 'billed' not in keys and 'pending' not in keys and 'vehicle' in keys, f'D: a hide-amounts user gets no sort links for Billed / Pending (keys: {keys})')
vals = [x for x in re.findall(r'<td class="num">([^<]*)</td>', txt)]
check(all(not re.search(r'\d{3,}', v) for v in vals[:20]), 'D: and a forged ?sort=billed neither reveals nor reorders by the amounts')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
