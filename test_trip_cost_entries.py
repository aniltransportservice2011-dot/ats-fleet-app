"""Regression test: fuel and driver-advance ENTRIES (each with its own vendor), editing / removing entries added on the trip, and the lock on ledger payments.

  A. fuel / driver-advance entries: each is a dated row with its own vendor; trips.fuel_amount / driver_adv_amount stay the TOTALS (earlier total + entries);
     litres and price follow; every vendor's ledger shows its own dated line; the fast balance code (_accounts_rows) and the per-vendor ledger agree to the rupee;
  B. a trip with ONE fuel entry for vendor X gives vendor X exactly the same balance as a trip entered the old way with fuel vendor X (nothing changed for old data);
  C. the "earlier total" (the old single values) is kept when a trip is re-saved, stays editable, and is attributed to its own vendor next to the entries;
  D. entries added on the trip can be edited and removed there (cost entries: totals follow; payments: reversed and re-applied, nothing left behind);
  E. a payment recorded in the LEDGER can never be edited or removed from the trip — not by the page, not by a forged form — and a payment typed on the trip
     is marked as such, read-only in the ledger, and says so there;
  F. an error anywhere (bad fuel date, ...) saves nothing; the phone app's totals can never drop below what the entries add up to; access rules;
  G. deleting a trip removes its fuel / driver entries; Add Trip takes all four kinds at once.

Disposable copy of fleet.db, network blocked.  Usage:  python3 test_trip_cost_entries.py
"""
import json
import os
import re
import shutil
import sqlite3
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


tmp = tempfile.mkdtemp(prefix='tripcost_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
one = lambda q, *a: (c.execute(q, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("UPDATE users SET full_name='Test Runner' WHERE id=?", (uid,))
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TCV01','own')"); veh = one("SELECT id FROM vehicles WHERE vehicle_no='TCV01'")
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TCH01','hired')"); hveh = one("SELECT id FROM vehicles WHERE vehicle_no='TCH01'")
c.execute("INSERT INTO parties (name) VALUES ('TC PARTY')"); party = one("SELECT id FROM parties WHERE name='TC PARTY'")
vend = {}
for n in ('V1', 'V2', 'V3', 'VA', 'VB', 'VL', 'OWN'):
    c.execute("INSERT INTO vendors (name) VALUES (?)", ('TC ' + n,)); vend[n] = one("SELECT id FROM vendors WHERE name=?", 'TC ' + n)


def mk_trip(lr, billed, **kw):
    cols = {'date': '2026-09-20', 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'party_id': party, 'from_loc': 'A', 'to_loc': 'B', 'quantity': 10,
            'rate': billed / 10.0, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': 0}
    cols.update(kw)
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True


def client(user_id=uid):
    cl = m.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'] = user_id
    return cl


cl = client()


def fresh():
    global c
    c.close(); c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row


def q(sql, *a):
    return c.execute(sql, a).fetchall()


class _Form(HTMLParser):
    def __init__(self):
        super().__init__(); self.inform = False; self.fields = []; self._sel = None; self._ta = None
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'form' and a.get('id') in ('tripForm', 'twForm'):
            self.inform = True
        if not self.inform:
            return
        if tag == 'input' and a.get('name'):
            t = (a.get('type') or 'text').lower()
            if t in ('checkbox', 'radio') and 'checked' not in a:
                return
            if t in ('submit', 'button'):
                return
            self.fields.append((a['name'], a.get('value', '')))
        elif tag == 'select' and a.get('name'):
            self._sel = [a['name'], None, None]
        elif tag == 'option' and self._sel:
            if 'selected' in a:
                self._sel[1] = a.get('value', '')
            if self._sel[2] is None:
                self._sel[2] = a.get('value', '')
        elif tag == 'textarea' and a.get('name'):
            self._ta = [a['name'], '']
    def handle_endtag(self, tag):
        if tag == 'select' and self._sel:
            self.fields.append((self._sel[0], self._sel[1] if self._sel[1] is not None else (self._sel[2] or ''))); self._sel = None
        if tag == 'textarea' and self._ta:
            self.fields.append(tuple(self._ta)); self._ta = None
    def handle_data(self, d):
        if self._ta is not None:
            self._ta[1] += d


def page(url, who=None):
    r = (who or cl).get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


def form_dict(url, who=None):
    f = _Form(); f.feed(page(url, who)[1])
    d = {}
    for n, v in f.fields:
        d.setdefault(n, v)
    return d


def stored_entries(tid):
    """what the trip page offers for editing right now: {id: row}"""
    st, h = page(f'/trips/edit/{tid}')
    mo = re.search(r'var tfEntryExisting = (\[.*?\]);', h, re.S)
    return {e['id']: e for e in (json.loads(mo.group(1)) if mo else [])}


E = lambda **k: dict({'id': '', 'kind': 'received', 'date': '2026-10-05', 'amount': '', 'mode': 'UPI', 'ref': '', 'vendor': '', 'liters': ''}, **k)


def save(tid, rows=(), deletes=(), who=None, **changes):
    body = form_dict(f'/trips/edit/{tid}', who)
    body.update({k: str(v) for k, v in changes.items()})
    body['return_to'] = ''
    data = {k: [v] for k, v in body.items()}
    for k in ('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters'):
        data['entry_' + k] = [r[k] for r in rows]
    data['entry_delete'] = list(deletes)
    return (who or cl).post(f'/trips/edit/{tid}', data=data)


def trip(tid):
    return q('SELECT * FROM trips WHERE id=?', tid)[0]


def vendor_balance(vid):
    with m.app.test_request_context():
        ent = m._get_vendor_ledger_entries(vid)
    return round(ent[0]['balance'], 2) if ent else 0.0


def fast_balances():
    with m.app.test_request_context():
        cc = gdb()
        rows = m._accounts_rows(cc)
        cc.close()
    return {r['id']: round(r['balance'], 2) for r in rows if r['role'] == 'Vendor'}


# =====================================================================================================  A. fuel / driver advance entries
T = mk_trip('TC-1', 40000); c.commit()
r = save(T, [E(kind='fuel', date='2026-09-21', amount='5000', vendor='TC V1', liters='50', ref='bill 1'),
             E(kind='fuel', date='2026-09-22', amount='3000', vendor='TC V2', liters='30'),
             E(kind='driver_adv', date='2026-09-21', amount='2000', vendor='TC V3', ref='cash')])
fresh()
check(r.status_code == 302, f'A: saving fuel and driver-advance entries succeeds ({r.status_code})')
tr = trip(T)
check(tr['fuel_amount'] == 8000 and tr['driver_adv_amount'] == 2000, f'A: the trip totals are the sum of the entries ({tr["fuel_amount"]}, {tr["driver_adv_amount"]})')
check(float(tr['fuel_liters']) == 80 and abs(tr['fuel_price'] - 100) < 0.01, f'A: litres add up (80) and the price per litre follows (100): {tr["fuel_liters"]}, {tr["fuel_price"]}')
es = q('SELECT e.*, v.name AS vn FROM trip_cost_entries e LEFT JOIN vendors v ON e.vendor_id=v.id WHERE e.trip_id=? ORDER BY e.id', T)
check([(e['kind'], e['date'], e['vn'], e['amount']) for e in es] == [('fuel', '2026-09-21', 'TC V1', 5000), ('fuel', '2026-09-22', 'TC V2', 3000), ('driver_adv', '2026-09-21', 'TC V3', 2000)], 'A: each entry has its own date and vendor')
check(es[0]['created_by'] == uid and es[0]['created_at'], 'A: and who / when')
with m.app.test_request_context():
    led1 = m._get_vendor_ledger_entries(vend['V1']); led2 = m._get_vendor_ledger_entries(vend['V2']); led3 = m._get_vendor_ledger_entries(vend['V3'])
check(any(e['date'] == '2026-09-21' and abs(e['credit'] - 5000) < 0.01 and 'Fuel' in e['detail'] for e in led1), 'A: vendor 1\'s ledger has its fuel line, dated, 5,000')
check(any(e['date'] == '2026-09-22' and abs(e['credit'] - 3000) < 0.01 for e in led2) and not any(abs(e['credit'] - 5000) < 0.01 for e in led2), 'A: vendor 2 only gets its own 3,000')
check(any(abs(e['credit'] - 2000) < 0.01 and 'Driver Advance' in e['detail'] for e in led3), 'A: the driver advance goes to its own vendor')
fb = fast_balances()
check(all(abs(fb.get(vend[k], 0) - vendor_balance(vend[k])) < 0.01 for k in ('V1', 'V2', 'V3')), f'A: the fast balance code and each vendor\'s ledger agree: {[(k, fb.get(vend[k]), vendor_balance(vend[k])) for k in ("V1", "V2", "V3")]}')
check(vendor_balance(vend['V1']) == -5000 and vendor_balance(vend['V2']) == -3000 and vendor_balance(vend['V3']) == -2000, 'A: and each owes exactly what was entered')
st, h = page(f'/trips/edit/{T}')
check(re.search(r'id="lgRow-fuel"\s+hidden', h) is not None and '<label>Fuel</label>' not in h and 'Driver Adv. Vendor' not in h, 'A: the old single fuel / driver-advance boxes are gone (and no earlier-total line on this trip)')

# =====================================================================================================  B. one entry == the old way
TA = mk_trip('TC-OLD', 10000, fuel_amount=5000, fuel_vendor_id=vend['VA']); TB = mk_trip('TC-NEW', 10000); c.commit()
save(TB, [E(kind='fuel', date='2026-09-20', amount='5000', vendor='TC VB')]); fresh()
check(vendor_balance(vend['VA']) == vendor_balance(vend['VB']) == -5000, 'B: one fuel entry for a vendor owes him exactly what the old single fuel value did')
fb = fast_balances()
check(fb[vend['VA']] == fb[vend['VB']], 'B: and the fast balance code says the same')

# =====================================================================================================  C. the earlier total
HT = mk_trip('TC-HIRED', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=vend['OWN'], owner_name='TC OWN', owner_rate=4000, owner_rate_type='PER_MT',
             fuel_amount=6000, fuel_vendor_id=vend['VL'], fuel_liters='60', fuel_price=100, driver_adv_amount=1000)
c.commit()
st, h = page(f'/trips/edit/{HT}')
check('name="legacy_fuel_amount" value="6000.0"' in h and 'name="legacy_fuel_vendor" value="TC VL"' in h and re.search(r'id="lgRow-fuel"\s*>', h) is not None, 'C: the old fuel value is carried by the form and shown as an "earlier total" line with its vendor')
r = save(HT); fresh()
check(r.status_code == 302 and trip(HT)['fuel_amount'] == 6000 and trip(HT)['fuel_vendor_id'] == vend['VL'] and trip(HT)['driver_adv_amount'] == 1000, 'C: re-saving the trip unchanged keeps fuel, its vendor and the driver advance exactly')
before = vendor_balance(vend['VL'])
r = save(HT, [E(kind='fuel', date='2026-09-21', amount='1000', vendor='TC V2')]); fresh()
check(trip(HT)['fuel_amount'] == 7000 and trip(HT)['fuel_vendor_id'] == vend['VL'], 'C: a new entry adds to the earlier total (7,000) and the earlier part stays with its vendor')
check(vendor_balance(vend['VL']) == before == -6000, 'C: the earlier vendor still owes only his 6,000')
with m.app.test_request_context():
    cc = gdb(); pay = m._trip_detail_data(cc, HT)['pay']['owner']; cc.close()
check(abs(pay['company_paid'] - 8000) < 0.01 and abs(pay['pending'] - (40000 - 8000)) < 0.01, f'C: the owner\'s pending is net of ALL fuel and driver advance paid by us (8,000): {pay["company_paid"]}, {pay["pending"]}')
with m.app.test_request_context():
    cc = gdb(); pend = {x['id']: x['pending'] for x in m._vendor_pending_trips(cc, vend['OWN'])}; cc.close()
check(abs(pend[HT] - pay['pending']) < 0.01, 'C: the ledger\'s own pending list agrees with the trip screen to the rupee')
fb = fast_balances()
check(all(abs(fb.get(vend[k], 0) - vendor_balance(vend[k])) < 0.01 for k in ('VL', 'V2', 'OWN')), 'C: fast balances == vendor ledgers for the owner and both fuel vendors')
r = save(HT, legacy_fuel_amount='2500'); fresh()
check(trip(HT)['fuel_amount'] == 3500, 'C: editing the earlier total changes the total (2,500 + 1,000 entry = 3,500)')

# =====================================================================================================  D. edit / remove entries added on the trip
T2 = mk_trip('TC-2', 30000, driver_name='D2'); c.commit()
save(T2, [E(kind='fuel', date='2026-09-21', amount='5000', vendor='TC V1', liters='50'), E(kind='received', date='2026-10-01', amount='10000', mode='UPI', ref='R1'),
          E(kind='driver_adv', date='2026-09-21', amount='1500', vendor='TC V3')]); fresh()
ex = stored_entries(T2)
fuel_id = next(i for i, e in ex.items() if e['kind'] == 'fuel'); pay_id = next(i for i, e in ex.items() if e['kind'] == 'received'); adv_id = next(i for i, e in ex.items() if e['kind'] == 'driver_adv')
check(ex[pay_id]['orig_alloc'] == 10000 and ex[fuel_id]['vendor'] == 'TC V1' and ex[fuel_id]['liters'] == 50, 'D: the trip offers those entries for editing with what they hold')
r = save(T2, [E(id=fuel_id, kind='fuel', date='2026-09-21', amount='4000', vendor='TC V1', liters='40')]); fresh()
check(r.status_code == 302 and trip(T2)['fuel_amount'] == 4000 and float(trip(T2)['fuel_liters']) == 40, 'D: editing a fuel entry updates the total and the litres')
check(vendor_balance(vend['V1']) == -9000, f"D: the vendor ledger follows the edit (5,000 on trip 1 + 4,000 on this one): {vendor_balance(vend['V1'])}")
check(one('SELECT amount FROM trip_cost_entries WHERE id=?', int(fuel_id[1:])) == 4000, 'D: the entry row itself changed')
r = save(T2, deletes=[adv_id]); fresh()
check(trip(T2)['driver_adv_amount'] == 0 and one('SELECT COUNT(*) FROM trip_cost_entries WHERE id=?', int(adv_id[1:])) == 0, 'D: removing a driver-advance entry takes it out of the total')
# payment entry: edit amount + date -> reversed and re-applied
n_pay = one('SELECT COUNT(*) FROM payments')
r = save(T2, [E(id=pay_id, kind='received', date='2026-10-03', amount='6000', mode='Cash', ref='R1b')]); fresh()
check(r.status_code == 302 and trip(T2)['payment_received'] == 6000 and one('SELECT COUNT(*) FROM payments') == n_pay, 'D: editing a payment entry replaces it (total 6,000, same number of payments)')
np_ = q("SELECT * FROM payments WHERE reference_id='R1b'")[0]
check(np_['date'] == '2026-10-03' and np_['mode'] == 'Cash' and np_['entered_from'] == 'trip' and one("SELECT COUNT(*) FROM payments WHERE reference_id='R1'") == 0, 'D: new date / mode / reference, still marked as entered on the trip, the old payment is gone')
check(one('SELECT COUNT(*) FROM payment_allocations WHERE payment_id=?', np_['id']) == 1, 'D: with one allocation to this trip')
new_pay_id = 'p%d' % np_['id']
n_alloc = one('SELECT COUNT(*) FROM payment_allocations')
r = save(T2, deletes=[new_pay_id]); fresh()
check(trip(T2)['payment_received'] == 0 and one('SELECT COUNT(*) FROM payments WHERE id=?', np_['id']) == 0 and one('SELECT COUNT(*) FROM payment_allocations') == n_alloc - 1, 'D: removing a payment entry gives the trip its balance back and leaves nothing behind')
with m.app.test_request_context():
    led = m._get_party_ledger_entries(party)
check(not any(e.get('kind') == 'Payment In' and e['date'] == '2026-10-03' for e in led), 'D: and it is gone from the party ledger too')
# unchanged stored rows are not touched (their record time stays)
T3 = mk_trip('TC-3', 20000); c.commit()
save(T3, [E(kind='received', date='2026-10-01', amount='5000', ref='U1')]); fresh()
p_before = tuple(q("SELECT id, created_at FROM payments WHERE reference_id='U1'")[0])
uid3 = next(iter(stored_entries(T3)))
save(T3, [E(id=uid3, kind='received', date='2026-10-01', amount='5000', mode='UPI', ref='U1')]); fresh()
check(tuple(q("SELECT id, created_at FROM payments WHERE reference_id='U1'")[0]) == p_before, 'D: a stored payment saved without changes is left exactly as it was (same record, same time)')

# =====================================================================================================  E. ledger payments are locked on the trip
T4 = mk_trip('TC-4', 20000); c.commit()
cl.post(f'/payment/party/{party}', data={'date': '2026-10-02', 'amount': '7000', 'mode': 'Bank Transfer', 'reference_id': 'LED1', 'trip_ids': [str(T4)]}); fresh()
lp = q("SELECT * FROM payments WHERE reference_id='LED1'")[0]
check(lp['entered_from'] is None, 'E: a payment recorded in the ledger carries no trip marker')
check('p%d' % lp['id'] not in stored_entries(T4), 'E: the trip does not offer it for editing')
st, h = page(f'/trips/edit/{T4}')
hist = h.split('id="tph-party"')[1].split('id="tpEntries"')[0]
check('From ledger' in hist and 'ref LED1' in hist.replace('&#8226;', ''), 'E: it is listed read-only in the history, marked "From ledger"')
n_pay = one('SELECT COUNT(*) FROM payments')
r = save(T4, deletes=['p%d' % lp['id']], driver_name='SHOULD NOT SAVE'); fresh()
check(r.status_code == 200 and 'recorded in the ledger' in r.data.decode('utf-8', 'ignore') and one('SELECT COUNT(*) FROM payments') == n_pay and trip(T4)['payment_received'] == 7000, 'E: a forged removal of a ledger payment is refused, with a clear message')
check(trip(T4)['driver_name'] != 'SHOULD NOT SAVE', 'E: and nothing else on that save went through')
r = save(T4, [E(id='p%d' % lp['id'], kind='received', date='2026-10-02', amount='1', mode='Cash', ref='X')]); fresh()
check(r.status_code == 200 and one("SELECT amount FROM payments WHERE id=?", lp['id']) == 7000, 'E: and so is a forged edit of it')
r = save(T4, [E(kind='received', date='2026-10-04', amount='3000', mode='UPI', ref='TRIPPAY')]); fresh()
tp = q("SELECT * FROM payments WHERE reference_id='TRIPPAY'")[0]
check(tp['entered_from'] == 'trip', 'E: a payment typed on the trip is marked as entered there')
rules = {r_.rule for r_ in m.app.url_map.iter_rules()}
check(not any(re.search(r'payment', r_) and re.search(r'edit|delete|remove|update', r_) for r_ in rules if not re.search(r'trips|app/ledger|opening', r_)), 'E: the ledger has no way to edit or remove any payment (so a trip-entered one is read-only there too)')
st, pv = page(f'/payments/{tp["id"]}/view')
check(st == 200 and 'Entered on a trip' in pv and 'read-only' in pv and f'/trips/edit/{T4}' in pv, 'E: the ledger\'s payment view says it was typed on the trip and links to it')
st, pv = page(f'/payments/{lp["id"]}/view')
check(st == 200 and 'Entered on a trip' not in pv, 'E: a ledger payment says nothing of the kind')

# =====================================================================================================  F. errors, phone floor, access
T5 = mk_trip('TC-5', 30000, driver_name='D5'); c.commit()
n_e = one('SELECT COUNT(*) FROM trip_cost_entries'); n_pay = one('SELECT COUNT(*) FROM payments')
r = save(T5, [E(kind='received', date='2026-10-05', amount='1000', ref='F1'), E(kind='fuel', date='', amount='500', vendor='TC V1')], driver_name='CHANGED')
fresh()
check(r.status_code == 200 and 'Fuel entry' in r.data.decode('utf-8', 'ignore') and one('SELECT COUNT(*) FROM trip_cost_entries') == n_e and one('SELECT COUNT(*) FROM payments') == n_pay and trip(T5)['driver_name'] == 'D5',
      'F: a fuel entry without a date saves NOTHING (not the payment next to it, not the other edits)')
for label, row in [('amount 0', E(kind='fuel', amount='0')), ('negative', E(kind='driver_adv', amount='-5')), ('bad litres', E(kind='fuel', amount='10', liters='abc'))]:
    r = save(T5, [row]); fresh()
    check(r.status_code == 200 and one('SELECT COUNT(*) FROM trip_cost_entries') == n_e, f'F: {label} is refused')
# phone path: totals can never drop below the entries
r = save(T5, [E(kind='fuel', date='2026-10-05', amount='4000', vendor='TC V1')]); fresh()
body = form_dict(f'/app/trips/{T5}/edit')
body.update({'fuel_amount': '100', 'return_to': ''})
cl.post(f'/app/trips/{T5}/edit', data=body); fresh()
check(trip(T5)['fuel_amount'] >= 4000, f'F: the phone app posting a smaller fuel total cannot drop it below what the entries add up to ({trip(T5)["fuel_amount"]})')
def user(uid_, level, modules=''):
    c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, hide_trip_amounts, status, phone, created_at) "
              "VALUES (?,?,?,?,0,?,?,?,0,'Active',?,'2026-01-01')", (uid_, f'u{uid_}', 'x', 'Manager', 'Other', level, modules, f'91000{uid_}'))
    c.commit()
    return client(uid_)
trip_only = user(900401, 'Limited Access', 'Trips:e')
T6 = mk_trip('TC-6', 20000); c.commit()
r = save(T6, [E(kind='fuel', date='2026-10-05', amount='700', vendor='TC V1')], who=trip_only); fresh()
check(r.status_code == 302 and trip(T6)['fuel_amount'] == 700, 'F: a user with Trips edit but no Ledger access can add fuel and driver-advance entries (they are trip data)')
r = save(T6, [E(kind='received', date='2026-10-05', amount='100', ref='NOLED')], who=trip_only, driver_name='X'); fresh()
check(one("SELECT COUNT(*) FROM payments WHERE reference_id='NOLED'") == 0 and trip(T6)['driver_name'] != 'X', 'F: but not payments, and nothing half-saves')

# =====================================================================================================  G. delete the trip; Add Trip with everything
T7 = mk_trip('TC-7', 10000); c.commit()
save(T7, [E(kind='fuel', date='2026-10-05', amount='700', vendor='TC V1'), E(kind='received', date='2026-10-05', amount='500', ref='G1')]); fresh()
cl.post(f'/trips/delete/{T7}'); fresh()
check(one('SELECT COUNT(*) FROM trips WHERE id=?', T7) == 1, 'G: a trip with a payment entered on it cannot be deleted until that payment is removed (as before)')
save(T7, deletes=['p%d' % one("SELECT id FROM payments WHERE reference_id='G1'")]); fresh()
cl.post(f'/trips/delete/{T7}'); fresh()
check(one('SELECT COUNT(*) FROM trips WHERE id=?', T7) == 0 and one('SELECT COUNT(*) FROM trip_cost_entries WHERE trip_id=?', T7) == 0, 'G: once it is removed the trip can be deleted, and its fuel entry goes with it')
add = form_dict('/trips/add')
add.update({'date': '2026-10-10', 'lr_number': 'TC-NEW9', 'vehicle_no': 'TCV01', 'party_name': 'TC PARTY', 'quantity': '10', 'rate': '1000', 'rate_type': 'PER_MT', 'type': 'own', 'from_loc': 'X', 'to_loc': 'Y', 'return_to': ''})
data = {k: [v] for k, v in add.items()}
rows = [E(kind='received', date='2026-10-10', amount='4000', ref='A1'), E(kind='fuel', date='2026-10-10', amount='1200', vendor='TC V1', liters='12'), E(kind='driver_adv', date='2026-10-10', amount='300', vendor='TC V3')]
for k in ('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters'):
    data['entry_' + k] = [r_[k] for r_ in rows]
r = cl.post('/trips/add', data=data); fresh()
nid = one("SELECT id FROM trips WHERE lr_number='TC-NEW9'")
check(r.status_code == 302 and nid and trip(nid)['payment_received'] == 4000 and trip(nid)['fuel_amount'] == 1200 and trip(nid)['driver_adv_amount'] == 300, 'G: Add Trip takes payment, fuel and driver advance entries in one save')
check(one('SELECT COUNT(*) FROM trip_cost_entries WHERE trip_id=?', nid) == 2 and float(trip(nid)['fuel_liters']) == 12, 'G: with their dated rows and litres')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
