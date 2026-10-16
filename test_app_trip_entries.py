"""Regression test: the PHONE app has the same trip entries as the website — payment received / payout to owner / fuel / driver advance, added in a pop-up
sheet, listed as dated read-only rows with Edit / Delete, saved through the same engine.

  A. the wizard: the old Fuel / Driver Advance / Party Advance / Payment Received / Paid-to-Owner boxes are gone (the stored totals and the "earlier total" ride along
     hidden), the entries list, the add button and the sheet are there, saved entries are drawn from the server;
  B. phone Update Trip: payment entries become dated ledger payments applied to THIS trip only (marked entered on the trip); edit = undo + re-apply; delete = undo;
     over-payment is capped; a fully paid trip refuses another entry; an error saves NOTHING and the wizard comes back with every typed row;
  C. ledger payments are locked from the phone too: a forged edit / removal is refused and nothing changes;
  D. fuel / driver-advance entries with their own vendors: totals, vendor ledgers, edit, delete; the "earlier total" survives a save untouched;
  E. hired trip: payout-to-owner entries (net of fuel / advance paid by us); refused when the trip has no owner;
  F. Add Trip (phone): trip + entries are saved together, or neither;
  G. the trip detail screen lists the entries read-only, marks which payments came from the ledger, the wizard's history lists only the ledger's;
  H. PARITY: the same steps through the website and through the phone leave identical numbers and payments;
  I. access: Read Only cannot save; a user without Ledger edit cannot record payments; a hide-amounts user gets no entries editor.

Runs on a disposable copy of fleet.db (the real file is never touched, network blocked).  Usage:  python3 test_app_trip_entries.py
"""
import json
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
from test_payment_combinations import _Form                                   # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


tmp = tempfile.mkdtemp(prefix='appentries_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
one = lambda q_, *a: (c.execute(q_, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('AEV01','own')"); veh = one("SELECT id FROM vehicles WHERE vehicle_no='AEV01'")
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('AEH01','hired')"); hveh = one("SELECT id FROM vehicles WHERE vehicle_no='AEH01'")
for nm in ('AE PARTY', 'AE PARTY2'):
    c.execute("INSERT INTO parties (name) VALUES (?)", (nm,))
party = one("SELECT id FROM parties WHERE name='AE PARTY'"); party2 = one("SELECT id FROM parties WHERE name='AE PARTY2'")
for nm in ('AE OWNER', 'AE FUEL', 'AE FUEL2'):
    c.execute("INSERT INTO vendors (name) VALUES (?)", (nm,))
owner = one("SELECT id FROM vendors WHERE name='AE OWNER'"); fuel_v = one("SELECT id FROM vendors WHERE name='AE FUEL'")


def mk_trip(lr, billed, **kw):
    cols = {'date': '2026-09-20', 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'party_id': party, 'from_loc': 'A', 'to_loc': 'B', 'quantity': 10,
            'rate': billed / 10.0, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': 0}
    cols.update(kw)
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


T = mk_trip('AE-1', 30000, payment_received=3000, party_advance=2000, fuel_amount=4000, fuel_vendor_id=fuel_v)
OTHER = mk_trip('AE-OTHER', 9000)
HT = mk_trip('AE-HIRED', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='AE OWNER', owner_rate=4000, owner_rate_type='PER_MT', fuel_amount=6000, fuel_vendor_id=fuel_v)
NOOWN = mk_trip('AE-NOOWNER', 20000, type='hired', vehicle_id=hveh, owner_rate=1000, owner_rate_type='PER_MT')
c.commit()


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True


def client(user_id=uid):
    cl_ = m.app.test_client()
    with cl_.session_transaction() as s:
        s['user_id'] = user_id
    return cl_


cl = client()


def fresh():
    global c
    c.close(); c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row


def q(sql, *a):
    return c.execute(sql, a).fetchall()


def page(url, who=None):
    r = (who or cl).get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


def form_dict(url, who=None):
    f = _Form(); f.feed(page(url, who)[1])
    d = {}
    for n, v in f.fields:
        d.setdefault(n, v)
    return d


def existing(tid):
    """the entries the phone wizard draws for this trip (its `existing` JSON), keyed by id"""
    mo = re.search(r'var existing = (\[.*?\]);', page(f'/app/trips/{tid}/edit')[1], re.S)
    return {e['id']: e for e in (json.loads(mo.group(1)) if mo else [])}


def save_phone(tid, rows=(), deletes=(), who=None, **changes):
    """re-save the PHONE wizard form as the page shows it, plus entry rows [(id, kind, date, amount, mode, ref, vendor, liters), ...]"""
    body = form_dict(f'/app/trips/{tid}/edit', who)
    body.update({k: str(v) for k, v in changes.items()})
    data = {k: [v] for k, v in body.items()}
    for i, k in enumerate(('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters')):
        data['entry_' + k] = [str(r[i]) for r in rows]
    data['entry_delete'] = list(deletes)
    return (who or cl).post(f'/app/trips/{tid}/edit', data=data)


def save_web(tid, rows=(), deletes=(), who=None, **changes):
    body = form_dict(f'/trips/edit/{tid}', who)
    body.update({k: str(v) for k, v in changes.items()})
    body['return_to'] = ''
    data = {k: [v] for k, v in body.items()}
    for i, k in enumerate(('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters')):
        data['entry_' + k] = [str(r[i]) for r in rows]
    data['entry_delete'] = list(deletes)
    return (who or cl).post(f'/trips/edit/{tid}', data=data)


def trip(tid):
    return q('SELECT * FROM trips WHERE id=?', tid)[0]


def R(kind, amount, ref, date='2026-10-05', mode='UPI', vendor='', liters='', id_=''):
    return (id_, kind, date, amount, mode if kind in ('received', 'owner') else '', ref, vendor, liters)


# =====================================================================================================  A. the wizard
status, html = page(f'/app/trips/{T}/edit')
check(status == 200, 'A: phone Update Trip opens')
check('name="fuel_amount"' not in html and 'name="driver_adv_amount"' not in html and 'name="fuel_vendor"' not in html and 'name="driver_adv_vendor"' not in html,
      'A: the old Fuel / Driver Advance boxes are gone')
check('Fuel &amp; Driver Advance' not in html, 'A: the Fuel & Driver Advance section is gone')
check('<label>Party Advance</label>' not in html and '<label>Payment Received</label>' not in html and '<label>Advance / Paid to Owner</label>' not in html,
      'A: the old Party Advance / Payment Received / Paid to Owner boxes are gone')
d = form_dict(f'/app/trips/{T}/edit')
check(d.get('payment_received') == '3000.0' and d.get('party_advance') == '2000.0', f'A: stored totals ride along hidden ({d.get("payment_received")}/{d.get("party_advance")})')
check(d.get('legacy_fuel_amount') == '4000.0' and d.get('legacy_fuel_vendor') == 'AE FUEL', 'A: the earlier fuel total is rendered by the server (so a save always carries it)')
check('id="teEntries"' in html and 'onclick="teOpen()"' in html and 'id="teSheet"' in html and 'id="teBackdrop"' in html, 'A: entries list, add button and sheet are on the page')
check('Quick Tips' not in html, 'A: no Quick Tips')
for url in ('/app/trips/add', f'/app/trips/{T}', f'/app/trips/{T}?tab=costs'):
    check(page(url)[0] == 200, f'A: {url} opens')

# =====================================================================================================  B. payments typed on the phone
before_other = trip(OTHER)['payment_received']
r = save_phone(T, [R('received', '10000', 'P1', '2026-10-05'), R('received', '5000', 'P2', '2026-10-06', 'Cash')])
fresh()
check(r.status_code == 302 and 'updated=1' in (r.headers.get('Location') or ''), f'B: saving with two payment entries succeeds ({r.status_code})')
p1 = q("SELECT * FROM payments WHERE reference_id='P1'")[0]; p2 = q("SELECT * FROM payments WHERE reference_id='P2'")[0]
check(p1['date'] == '2026-10-05' and p2['date'] == '2026-10-06' and p1['entered_from'] == 'trip' and p1['party_id'] == party and p1['created_by'] == uid, 'B: one dated payment per entry, marked as entered on the trip')
check(trip(T)['payment_received'] == 18000 and trip(T)['party_advance'] == 2000, f'B: the trip total moved by exactly what was applied ({trip(T)["payment_received"]})')
check(trip(OTHER)['payment_received'] == before_other, "B: the party's other pending trip was not touched")
check(sum(a['amount'] for a in q('SELECT amount FROM payment_allocations WHERE payment_id IN (?,?)', p1['id'], p2['id'])) == 15000, 'B: allocations add up')
ex = existing(T)
check(len([e for e in ex.values() if e['kind'] == 'received']) == 2, 'B: the wizard now lists both entries')
# edit one (amount) -> undo + re-apply
e1 = [e for e in ex.values() if e['ref'] == 'P1'][0]
r = save_phone(T, [R('received', '4000', 'P1', '2026-10-05', id_=e1['id'])])
fresh()
check(r.status_code == 302 and trip(T)['payment_received'] == 12000, f'B: editing an entry re-applies it (total {trip(T)["payment_received"]})')
check(q("SELECT COUNT(*) FROM payments WHERE reference_id='P1'")[0][0] == 1, 'B: edit leaves one payment, not two')
# delete one
e2 = [e for e in existing(T).values() if e['ref'] == 'P2'][0]
r = save_phone(T, [], deletes=[e2['id']])
fresh()
check(r.status_code == 302 and trip(T)['payment_received'] == 7000 and q("SELECT COUNT(*) FROM payments WHERE reference_id='P2'")[0][0] == 0, f'B: deleting an entry takes it back off the trip ({trip(T)["payment_received"]})')
# over-payment: capped at what is owed, the rest stays unallocated
owed = 30000 - trip(T)['payment_received'] - trip(T)['party_advance']
r = save_phone(T, [R('received', str(owed + 7000), 'P3', '2026-10-07')])
fresh()
p3 = q("SELECT * FROM payments WHERE reference_id='P3'")[0]
check(r.status_code == 302 and trip(T)['payment_received'] == 28000 and p3['amount'] == owed + 7000 and abs((p3['allocated_amount'] or 0) - owed) < 0.01,
      f'B: an over-payment applies only what is owed, the surplus stays in the ledger ({trip(T)["payment_received"]}, {p3["allocated_amount"]})')
r = save_phone(T, [R('received', '100', 'P4', '2026-10-08')])
fresh()
check(r.status_code == 200 and q("SELECT COUNT(*) FROM payments WHERE reference_id='P4'")[0][0] == 0 and 'nothing is left to receive' in r.data.decode(), 'B: a fully paid trip refuses another entry, with a reason')
# an error saves nothing at all, the wizard comes back with the typed rows
tot_before = trip(T)['payment_received']; drv_before = trip(T)['driver_name']
T2 = mk_trip('AE-ERR', 10000)
c.commit()
r = save_phone(T2, [R('received', '3000', 'E1', '2026-10-05'), R('received', '99999999', 'E2', '2026-10-06'), R('received', '1', 'E3', 'not-a-date')], driver_name='CHANGED')
fresh()
body = r.data.decode()
check(r.status_code == 200 and q("SELECT COUNT(*) FROM payments WHERE reference_id IN ('E1','E2','E3')")[0][0] == 0 and trip(T2)['driver_name'] != 'CHANGED',
      'B: an error in any entry saves NOTHING (not the entries, not the other edits)')
posted = re.search(r'var posted = (\[.*?\]);', body, re.S)
rows_back = json.loads(posted.group(1)) if posted else []
check(len(rows_back) >= 2 and rows_back[0].get('ref') == 'E1' and rows_back[0].get('amount') == '3000', 'B: the wizard comes back with every typed row')
check('tw-error-banner' in body, 'B: and shows the reason')

# =====================================================================================================  C. ledger payments are locked
cl.post(f'/payment/party/{party}', data={'date': '2026-10-09', 'amount': '1000', 'mode': 'UPI', 'reference_id': 'L1', 'trip_ids': [str(T2)]})
fresh()
lp = q("SELECT * FROM payments WHERE reference_id='L1'")[0]
check(lp['entered_from'] is None and trip(T2)['payment_received'] == 1000, 'C: ledger payment applied to the trip')
check(f'p{lp["id"]}' not in existing(T2), 'C: it is NOT in the phone wizard\'s editable list')
r = save_phone(T2, [], deletes=[f'p{lp["id"]}'])
fresh()
check(r.status_code == 200 and q('SELECT COUNT(*) FROM payments WHERE id=?', lp['id'])[0][0] == 1 and trip(T2)['payment_received'] == 1000, 'C: a forged removal of a ledger payment is refused')
r = save_phone(T2, [R('received', '500', 'L1x', '2026-10-09', id_=f'p{lp["id"]}')])
fresh()
check(r.status_code == 200 and trip(T2)['payment_received'] == 1000 and q('SELECT amount FROM payments WHERE id=?', lp['id'])[0][0] == 1000, 'C: a forged edit of a ledger payment is refused')

# =====================================================================================================  D. fuel / driver advance entries
r = save_phone(T, [R('fuel', '2500', 'F1', '2026-10-02', vendor='AE FUEL2', liters='30'), R('driver_adv', '1500', 'D1', '2026-10-03', vendor='AE FUEL')])
fresh()
check(r.status_code == 302, f'D: fuel and driver-advance entries save ({r.status_code})')
check(trip(T)['fuel_amount'] == 6500 and trip(T)['driver_adv_amount'] == 1500, f'D: the trip totals = earlier total + entries ({trip(T)["fuel_amount"]}/{trip(T)["driver_adv_amount"]})')
ce = q('SELECT e.*, v.name vn FROM trip_cost_entries e LEFT JOIN vendors v ON e.vendor_id=v.id WHERE e.trip_id=? ORDER BY e.id', T)
check([(x['kind'], x['amount'], x['vn']) for x in ce] == [('fuel', 2500, 'AE FUEL2'), ('driver_adv', 1500, 'AE FUEL')], f'D: each entry keeps its own vendor ({[(x["kind"], x["amount"], x["vn"]) for x in ce]})')
check(d.get('legacy_fuel_amount') == '4000.0' and existing(T), 'D: the earlier total is still 4000 and untouched')
d2 = form_dict(f'/app/trips/{T}/edit')
check(d2.get('legacy_fuel_amount') == '4000.0' and d2.get('legacy_fuel_vendor') == 'AE FUEL', 'D: the earlier fuel total (amount + vendor) is unchanged by the save')
with m.app.test_request_context():
    led = m._get_vendor_ledger_entries(one("SELECT id FROM vendors WHERE name='AE FUEL2'"))
    check(any(abs((e.get('credit') or 0) - 2500) < 0.01 or abs((e.get('debit') or 0) - 2500) < 0.01 for e in led), 'D: the vendor ledger shows the fuel entry for that vendor')
ce1 = [x for x in existing(T).values() if x['kind'] == 'fuel'][0]
r = save_phone(T, [R('fuel', '1000', 'F1', '2026-10-02', vendor='AE FUEL', id_=ce1['id'])])
fresh()
check(r.status_code == 302 and trip(T)['fuel_amount'] == 5000, f'D: editing a fuel entry (amount and vendor) updates the total ({trip(T)["fuel_amount"]})')
r = save_phone(T, [], deletes=[ce1['id']])
fresh()
check(r.status_code == 302 and trip(T)['fuel_amount'] == 4000, f'D: deleting a fuel entry leaves just the earlier total ({trip(T)["fuel_amount"]})')

# =====================================================================================================  E. hired trip
r = save_phone(HT, [R('owner', '20000', 'O1', '2026-10-05', 'Bank Transfer')])
fresh()
op = q("SELECT * FROM payments WHERE reference_id='O1'")
check(r.status_code == 302 and op and op[0]['payment_type'] != 'received' and op[0]['entered_from'] == 'trip' and trip(HT)['paid_to_owner'] == 20000, f'E: payout to owner entry saved ({trip(HT)["paid_to_owner"]})')
r = save_phone(NOOWN, [R('owner', '1000', 'O2', '2026-10-05')])
fresh()
check(r.status_code == 200 and q("SELECT COUNT(*) FROM payments WHERE reference_id='O2'")[0][0] == 0, 'E: no payout without an owner on the trip')

# =====================================================================================================  F. Add Trip on the phone
def add_phone(lr, rows, extra=None):
    body = form_dict('/app/trips/add')
    body.update({'date': '2026-10-01', 'lr_number': lr, 'vehicle_no': 'AEV01', 'type': 'own', 'party_name': 'AE PARTY', 'from_loc': 'X', 'to_loc': 'Y', 'quantity': '10',
                 'rate': '1000', 'rate_type': 'PER_MT'})
    body.update(extra or {})
    data = {k: [v] for k, v in body.items()}
    for i, k in enumerate(('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters')):
        data['entry_' + k] = [str(rw[i]) for rw in rows]
    return cl.post('/app/trips/add', data=data)


r = add_phone('AE-NEW', [R('received', '4000', 'N1', '2026-10-01'), R('fuel', '2000', 'N2', '2026-10-01', vendor='AE FUEL', liters='25')])
fresh()
nt = q("SELECT * FROM trips WHERE lr_number='AE-NEW'")
check(r.status_code == 302 and nt and nt[0]['payment_received'] == 4000 and nt[0]['fuel_amount'] == 2000, f'F: trip + payment + fuel entry saved together ({nt[0]["payment_received"] if nt else None})')
r = add_phone('AE-NEW2', [R('received', '4000', 'N3', '2026-10-01'), R('received', '1', 'N4', 'bad-date')])
fresh()
check(r.status_code == 200 and not q("SELECT 1 FROM trips WHERE lr_number='AE-NEW2'") and not q("SELECT 1 FROM payments WHERE reference_id IN ('N3','N4')"), 'F: a bad entry saves neither the trip nor the other entries')
check('var posted' in r.data.decode() and '"N3"' in r.data.decode(), 'F: and the wizard comes back with the typed rows')

# =====================================================================================================  G. detail screen + history
status, html = page(f'/app/trips/{T2}?tab=costs')
check('From ledger' in html, 'G: the detail marks ledger payments as "From ledger"')
status, html = page(f'/app/trips/{T}?tab=costs')
check('Entered on this trip' in html, 'G: the detail marks payments typed on the trip')
check('class="te-item"' in html and 'te-badge' in html, 'G: the detail lists the entries read-only')
check('onclick="teOpen' not in html and 'teMenu' not in html, 'G: and has no edit controls')
status, html = page(f'/app/trips/{T2}/edit')
hist = html.split('id="tph2-party"')[1].split('teEntries')[0] if 'id="tph2-party"' in html else ''
check('From ledger' in hist, 'G: the wizard history lists the ledger payment')
status, html = page(f'/app/trips/{T}/edit')
hist = html.split('id="tph2-party"')[1].split('teEntries')[0] if 'id="tph2-party"' in html else ''
check('Entered on this trip' not in hist, 'G: the wizard history does NOT repeat the payments typed on the trip (they are in the editable list)')

# =====================================================================================================  H. parity with the website
def snapshot(tid):
    t = trip(tid)
    pays = q("""SELECT p.payment_type, p.amount, p.allocated_amount, p.date, p.mode, p.entered_from FROM payments p
                WHERE EXISTS (SELECT 1 FROM payment_allocations a WHERE a.payment_id=p.id AND a.trip_id=?) ORDER BY p.date, p.id""", tid)
    ces = q('SELECT kind, amount, date FROM trip_cost_entries WHERE trip_id=? ORDER BY date, id', tid)
    return (t['payment_received'], t['paid_to_owner'], t['fuel_amount'], t['driver_adv_amount'], [tuple(p) for p in pays], [tuple(x) for x in ces])


def parity_pair(lr, **kw):
    a = mk_trip(lr + '-W', 30000, **kw); b = mk_trip(lr + '-P', 30000, **kw); c.commit()
    return a, b


for label, rows_a, kw in (
        ('received x2 + fuel + advance', [R('received', '9000', 'H1', '2026-10-02'), R('received', '4000', 'H2', '2026-10-03', 'Cash'), R('fuel', '3000', 'HF', '2026-10-02', vendor='AE FUEL'), R('driver_adv', '800', 'HD', '2026-10-02', vendor='')], dict(payment_received=1000, fuel_amount=2000)),
        ('over-payment', [R('received', '45000', 'H3', '2026-10-02')], dict(party_advance=500)),
        ('hired payout + fuel by us', [R('owner', '12000', 'H4', '2026-10-02', 'UPI'), R('fuel', '1000', 'HF2', '2026-10-02', vendor='AE FUEL2')],
         dict(type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='AE OWNER', owner_rate=4000, owner_rate_type='PER_MT', fuel_amount=2000, fuel_vendor_id=fuel_v)),
):
    a, b = parity_pair('AEP' + str(abs(hash(label)) % 1000), **kw)
    rows_b = [r[:5] + (r[5] + 'p',) + r[6:] for r in rows_a]        # the ledger refuses an identical payment twice, so the phone's references differ
    ra = save_web(a, rows_a); rb = save_phone(b, rows_b)
    fresh()
    check(ra.status_code == 302 and rb.status_code == 302, f'H[{label}]: both accept ({ra.status_code}/{rb.status_code})')
    sa, sb = snapshot(a), snapshot(b)
    check(sa == sb, f'H[{label}]: website and phone leave identical numbers and payments\n   web {sa}\n   app {sb}')
    # then edit one and delete one on each
    exa = {e['id']: e for e in json.loads(re.search(r'var tfEntryExisting = (\[.*?\]);', page(f'/trips/edit/{a}')[1], re.S).group(1))}
    exb = existing(b)
    pay_a = [e for e in exa.values() if e['kind'] in ('received', 'owner')]; pay_b = [e for e in exb.values() if e['kind'] in ('received', 'owner')]
    if pay_a and pay_b:
        ra = save_web(a, [R(pay_a[0]['kind'], '2500', pay_a[0]['ref'], pay_a[0]['date'], pay_a[0]['mode'] or 'UPI', id_=pay_a[0]['id'])])
        rb = save_phone(b, [R(pay_b[0]['kind'], '2500', pay_b[0]['ref'], pay_b[0]['date'], pay_b[0]['mode'] or 'UPI', id_=pay_b[0]['id'])])
        fresh()
        check(snapshot(a) == snapshot(b), f'H[{label}]: after an edit they still match')
        ra = save_web(a, [], deletes=[pay_a[0]['id']]); rb = save_phone(b, [], deletes=[pay_b[0]['id']])
        fresh()
        check(snapshot(a) == snapshot(b), f'H[{label}]: after a delete they still match')

# =====================================================================================================  I. access
def user(uid_, level, modules='', hide=0):
    c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, hide_trip_amounts, status, phone, created_at) "
              "VALUES (?,?,?,?,0,?,?,?,?,'Active',?,'2026-01-01')", (uid_, f'u{uid_}', 'x', 'Manager', 'Other', level, modules, hide, f'91000{uid_}'))
    c.commit()
    return client(uid_)


T3 = mk_trip('AE-ACC', 10000, driver_name='D3'); c.commit()
ro = user(900401, 'Read Only')
body = form_dict(f'/app/trips/{T3}/edit', ro)
r = ro.post(f'/app/trips/{T3}/edit', data={**{k: [v] for k, v in body.items()}, 'entry_kind': ['received'], 'entry_amount': ['100'], 'entry_date': ['2026-10-05'], 'entry_mode': ['Cash'], 'entry_ref': ['X1']})
fresh()
check(r.status_code in (302, 403) and q("SELECT COUNT(*) FROM payments WHERE reference_id='X1'")[0][0] == 0, 'I: Read Only cannot save entries from the phone')
noled = user(900402, 'Limited Access', 'Trips:e')
r = save_phone(T3, [R('received', '100', 'X2')], who=noled, driver_name='X-CHANGED')
fresh()
check(r.status_code in (200, 302, 403) and q("SELECT COUNT(*) FROM payments WHERE reference_id='X2'")[0][0] == 0 and trip(T3)['driver_name'] == 'D3',
      'I: Trips edit without Ledger edit cannot record payments from the phone, and the trip is not half-saved either')
check('edit access to the Ledger' in r.data.decode('utf-8', 'ignore') or r.status_code in (302, 403), 'I: it says why')
ledg = user(900403, 'Limited Access', 'Trips:e,Ledger:e')
r = save_phone(T3, [R('received', '100', 'X3')], who=ledg)
fresh()
check(r.status_code == 302 and one("SELECT created_by FROM payments WHERE reference_id='X3'") == 900403, 'I: Trips edit + Ledger edit records it, attributed to that user')
# fuel / driver-advance entries need only trip edit
r = save_phone(T3, [R('fuel', '700', 'X4', vendor='AE FUEL')], who=noled)
fresh()
check(r.status_code == 302 and trip(T3)['fuel_amount'] == 700, 'I: fuel / driver-advance entries need only trip edit access')
hid = user(900404, 'Limited Access', 'Trips:v,Ledger:e', hide=1)
status, html = page(f'/app/trips/{T3}/edit', hid)
check(status in (302, 403) or 'onclick="teOpen()"' not in html, 'I: a "hide trip amounts" user gets no entries editor')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
