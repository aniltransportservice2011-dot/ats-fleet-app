"""Regression test: payments are ADDED on the trip page as several dated entries, and each one is a real payment in the ledger.

  A. several entries on one save -> one dated `payments` row each, each allocated to THIS trip only (the party's other pending trips are not touched),
     the trip total moves by exactly what was applied, the stored advance / earlier total is untouched, the ledger gets its own dated Payment In line per
     entry and the trip's history lists them;
  B. more than the trip owes -> only what is owed is applied, the rest stays in the ledger as an advance; a fully paid trip refuses another entry;
  C. an error in any entry saves NOTHING (not the entries, not the other edits on the form), the page comes back unlocked with every typed row re-drawn;
  D. Add Trip takes entries too (trip + payments saved together, or neither);
  E. hired trips: "Paid to owner" entries are dated payments to the owner (net of fuel / driver advance paid by us); not available without an owner;
  F. access: Read Only, a user with no Ledger edit and a "hide trip amounts" user cannot record payments this way — and nothing is half-saved;
  G. the form: no Quick Tips card, the old Party Advance / Payment Received / Paid to Owner boxes are gone (the stored totals ride along hidden), blank rows
     are ignored, and the ledger's own Add Payment still behaves exactly as before.

Runs on a disposable copy of fleet.db (the real file is never touched, network blocked).  Usage:  python3 test_trip_entries.py
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


tmp = tempfile.mkdtemp(prefix='tripentries_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
one = lambda q, *a: (c.execute(q, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("UPDATE users SET full_name='Test Runner' WHERE id=?", (uid,))
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TEV01','own')"); veh = one("SELECT id FROM vehicles WHERE vehicle_no='TEV01'")
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TEH01','hired')"); hveh = one("SELECT id FROM vehicles WHERE vehicle_no='TEH01'")
c.execute("INSERT INTO parties (name) VALUES ('TE PARTY')"); party = one("SELECT id FROM parties WHERE name='TE PARTY'")
c.execute("INSERT INTO vendors (name) VALUES ('TE OWNER')"); owner = one("SELECT id FROM vendors WHERE name='TE OWNER'")
c.execute("INSERT INTO vendors (name) VALUES ('TE FUEL')"); fuel_v = one("SELECT id FROM vendors WHERE name='TE FUEL'")


def mk_trip(lr, billed, **kw):
    cols = {'date': '2026-09-20', 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'party_id': party, 'from_loc': 'A', 'to_loc': 'B', 'quantity': 10,
            'rate': billed / 10.0, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': 0}
    cols.update(kw)
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


T = mk_trip('TE-1', 30000, payment_received=3000, party_advance=2000, driver_name='OLD DRIVER')     # 3000 typed earlier, 2000 advance
OTHER = mk_trip('TE-OTHER', 9000)                                                                  # the party's other pending trip: must stay untouched
HT = mk_trip('TE-HIRED', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='TE OWNER', owner_rate=4000, owner_rate_type='PER_MT',
             fuel_amount=6000, fuel_vendor_id=fuel_v, driver_adv_amount=2000)
NOOWN = mk_trip('TE-NOOWNER', 20000, type='hired', vehicle_id=hveh, owner_rate=1000, owner_rate_type='PER_MT')
c.commit()


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


def save_edit(tid, entries=(), who=None, **changes):
    """Re-save the trip's edit form as the page shows it, plus the given payment rows [(kind, date, amount, mode, ref), ...]."""
    body = form_dict(f'/trips/edit/{tid}', who)
    body.update({k: str(v) for k, v in changes.items()})
    body['return_to'] = ''
    data = {k: [v] for k, v in body.items()}
    data['entry_kind'] = [e[0] for e in entries]; data['entry_date'] = [e[1] for e in entries]; data['entry_amount'] = [e[2] for e in entries]
    data['entry_mode'] = [e[3] for e in entries]; data['entry_ref'] = [e[4] for e in entries]
    return (who or cl).post(f'/trips/edit/{tid}', data=data)


def trip(tid):
    return q('SELECT * FROM trips WHERE id=?', tid)[0]


# =====================================================================================================  A. several entries, one save
before_other = trip(OTHER)['payment_received']
r = save_edit(T, [('received', '2026-10-05', '10000', 'UPI', 'E1'), ('received', '2026-10-06', '5000', 'Cash', 'E2')])
fresh()
check(r.status_code == 302, f'A: saving the trip with two payment entries succeeds ({r.status_code})')
p1 = q("SELECT * FROM payments WHERE reference_id='E1'")[0]; p2 = q("SELECT * FROM payments WHERE reference_id='E2'")[0]
check(p1['date'] == '2026-10-05' and p2['date'] == '2026-10-06' and p1['payment_type'] == 'received' and p1['party_id'] == party and p1['created_by'] == uid and p1['created_at'],
      'A: one dated payments row per entry, with who and when')
check(p1['mode'] == 'UPI' and p2['mode'] == 'Cash', 'A: mode kept per entry')
al = {r_['payment_id']: (r_['trip_id'], r_['amount']) for r_ in q('SELECT payment_id, trip_id, amount FROM payment_allocations')}
check(al.get(p1['id']) == (T, 10000) and al.get(p2['id']) == (T, 5000), 'A: each is applied to THIS trip only')
check(trip(T)['payment_received'] == 3000 + 15000 and trip(T)['party_advance'] == 2000, 'A: payment_received moved by exactly 15,000; the earlier 3,000 and the 2,000 advance are untouched')
check(trip(OTHER)['payment_received'] == before_other, 'A: the party\'s other pending trip was NOT touched (a ledger payment would have filled it)')
check(trip(T)['driver_name'] == 'OLD DRIVER', 'A: the rest of the form saved as it was')
with m.app.test_request_context():
    led = m._get_party_ledger_entries(party)
pin = {(e['date'], round(e['credit'], 2)) for e in led if e.get('kind') == 'Payment In'}
check({('2026-10-05', 10000.0), ('2026-10-06', 5000.0)} <= pin, 'A: the party ledger has its own dated Payment In line for each entry')
st, h = page(f'/trips/edit/{T}')
mo = re.search(r'var tfEntryExisting = (\[.*?\]);', h, re.S)
ex = json.loads(mo.group(1)) if mo else []
check({(e['date'], e['ref'], e['kind']) for e in ex} >= {('2026-10-05', 'E1', 'received'), ('2026-10-06', 'E2', 'received')}, 'A: both entries are listed on the trip, editable, with date and reference')
check('ref E1' not in h.split('id="tph-party"')[1].split('id="tpEntries"')[0], 'A: and they are not repeated in the read-only ledger history (that lists only ledger payments)')
check(page(f'/payments/{p1["id"]}/view')[0] == 200, 'A: and each opens in the ledger')

# =====================================================================================================  B. over-payment, nothing left
# T now: billed 30000, received 18000 + advance 2000 -> 10000 still due
r = save_edit(T, [('received', '2026-10-07', '50000', 'Bank Transfer', 'E3')])
fresh()
p3 = q("SELECT * FROM payments WHERE reference_id='E3'")[0]
check(r.status_code == 302 and abs(p3['allocated_amount'] - 10000) < 0.01 and abs(p3['amount'] - 50000) < 0.01, 'B: 50,000 entered, only the 10,000 owed is applied; the rest stays on the ledger as an advance')
check(trip(T)['payment_received'] == 28000, 'B: the trip is now fully received')
n_pay = one('SELECT COUNT(*) FROM payments')
r = save_edit(T, [('received', '2026-10-08', '100', 'Cash', 'E4')], driver_name='SHOULD NOT SAVE')
fresh()
check(r.status_code == 200 and 'nothing is left to receive' in r.data.decode('utf-8', 'ignore') and one('SELECT COUNT(*) FROM payments') == n_pay, 'B: a fully paid trip refuses another entry, with a clear message')
check(trip(T)['driver_name'] == 'OLD DRIVER', 'B: and that failed save changed nothing else on the trip')

# =====================================================================================================  C. an error saves nothing; rows are re-drawn
T2 = mk_trip('TE-2', 12000, driver_name='D2'); c.commit()
n_pay = one('SELECT COUNT(*) FROM payments'); n_alloc = one('SELECT COUNT(*) FROM payment_allocations')
r = save_edit(T2, [('received', '2026-10-05', '4000', 'UPI', 'C1'), ('received', 'not-a-date', '1000', 'Cash', 'C2')], driver_name='CHANGED', quantity='11')
fresh()
html = r.data.decode('utf-8', 'ignore')
check(r.status_code == 200 and 'Payment entry 2' in html and 'valid date' in html, 'C: the error names the entry that is wrong')
check(one('SELECT COUNT(*) FROM payments') == n_pay and one('SELECT COUNT(*) FROM payment_allocations') == n_alloc, 'C: NO payment was saved (not even the good first entry)')
check(trip(T2)['driver_name'] == 'D2' and trip(T2)['payment_received'] == 0 and trip(T2)['quantity'] == 10, 'C: and none of the other edits were saved either')
check('<fieldset id="tfFieldset" class="tf-fs" >' in html and 'id="tfSaveBtn"' in html and not re.search(r'id="tfSaveBtn"[^>]*hidden', html), 'C: the page comes back unlocked with Save, so it can be corrected')
mo = re.search(r'var tfEntryPosted = (\[.*?\]);', html, re.S)
rows = json.loads(mo.group(1)) if mo else []
check(len(rows) == 2 and rows[0]['ref'] == 'C1' and rows[0]['amount'] == '4000' and rows[1]['date'] == 'not-a-date', 'C: every typed row is re-drawn exactly as typed')
# blank rows are ignored
r = save_edit(T2, [('received', '', '', 'UPI', ''), ('received', '2026-10-05', '4000', 'UPI', 'C3')])
fresh()
check(r.status_code == 302 and one("SELECT COUNT(*) FROM payments WHERE reference_id='C3'") == 1 and one('SELECT COUNT(*) FROM payments') == n_pay + 1, 'C: an untouched blank row is ignored; the real one saves')
# the same payment twice in one save is the double-click guard: refused, nothing saved
T3 = mk_trip('TE-3', 12000); c.commit(); n_pay = one('SELECT COUNT(*) FROM payments')
r = save_edit(T3, [('received', '2026-10-05', '1000', 'Cash', 'D1'), ('received', '2026-10-05', '1000', 'Cash', 'D1')])
fresh()
check(r.status_code == 200 and 'twice' in r.data.decode('utf-8', 'ignore') and one('SELECT COUNT(*) FROM payments') == n_pay, 'C: two identical entries in one save are refused as a double entry, and nothing is saved')

# =====================================================================================================  D. Add Trip
n_trips = one('SELECT COUNT(*) FROM trips'); n_pay = one('SELECT COUNT(*) FROM payments')
add = form_dict('/trips/add')
add.update({'date': '2026-10-10', 'lr_number': 'TE-NEW', 'vehicle_no': 'TEV01', 'party_name': 'TE PARTY', 'quantity': '10', 'rate': '1000', 'rate_type': 'PER_MT', 'type': 'own',
            'from_loc': 'X', 'to_loc': 'Y', 'return_to': ''})
data = {k: [v] for k, v in add.items()}
data.update({'entry_kind': ['received'], 'entry_date': ['2026-10-10'], 'entry_amount': ['4000'], 'entry_mode': ['UPI'], 'entry_ref': ['N1']})
r = cl.post('/trips/add', data=data); fresh()
new_id = one("SELECT id FROM trips WHERE lr_number='TE-NEW'")
check(r.status_code == 302 and new_id and one("SELECT COUNT(*) FROM payments WHERE reference_id='N1'") == 1, 'D: Add Trip with an entry saves the trip and the dated payment together')
check(trip(new_id)['payment_received'] == 4000 and one('SELECT trip_id FROM payment_allocations WHERE payment_id=(SELECT id FROM payments WHERE reference_id=?)', 'N1') == new_id, 'D: the payment is applied to the new trip')
n_trips = one('SELECT COUNT(*) FROM trips')
data['lr_number'] = ['TE-NEW2']; data['entry_date'] = ['bad']; data['entry_ref'] = ['N2']
r = cl.post('/trips/add', data=data); fresh()
check(r.status_code == 200 and one('SELECT COUNT(*) FROM trips') == n_trips and one("SELECT COUNT(*) FROM payments WHERE reference_id='N2'") == 0, 'D: a bad entry on Add Trip saves neither the trip nor the payment')
check('"N2"' in r.data.decode('utf-8', 'ignore') and '<fieldset id="tfFieldset" class="tf-fs" >' in r.data.decode('utf-8', 'ignore'), 'D: and the form comes back with the row still there')

# =====================================================================================================  E. owner entries
# owner owes 4000*10 = 40000, less fuel 6000 + driver advance 2000 paid by us = 32000
r = save_edit(HT, [('owner', '2026-10-09', '12000', 'Bank Transfer', 'O1'), ('owner', '2026-10-10', '30000', 'Cash', 'O2')])
fresh()
po1 = q("SELECT * FROM payments WHERE reference_id='O1'")[0]; po2 = q("SELECT * FROM payments WHERE reference_id='O2'")[0]
check(r.status_code == 302 and po1['payment_type'] == 'paid' and po1['vendor_id'] == owner and po1['date'] == '2026-10-09', 'E: an owner entry is a dated Payment Out to the owner')
check(abs(po2['allocated_amount'] - 20000) < 0.01 and trip(HT)['paid_to_owner'] == 32000, 'E: applied net of fuel and advance paid by us (32,000 owed): 12,000 + 20,000, the rest stays an advance')
check(one('SELECT COUNT(*) FROM payment_allocations WHERE payment_id IN (?,?) AND (maintenance_id IS NOT NULL OR overhead_id IS NOT NULL)', po1['id'], po2['id']) == 0, 'E: none of the owner\'s other bills were paid by it')
n_pay = one('SELECT COUNT(*) FROM payments')
r = save_edit(NOOWN, [('owner', '2026-10-09', '100', 'Cash', 'O3')])
fresh()
check(r.status_code == 200 and 'no hired-vehicle owner' in r.data.decode('utf-8', 'ignore') and one('SELECT COUNT(*) FROM payments') == n_pay, 'E: a hired trip with no owner refuses an owner entry')
r = save_edit(T2, [('owner', '2026-10-09', '100', 'Cash', 'O4')])
fresh()
check(r.status_code == 200 and one("SELECT COUNT(*) FROM payments WHERE reference_id='O4'") == 0, 'E: so does an own-vehicle trip')

# =====================================================================================================  F. access
def user(uid_, level, modules='', hide=0):
    c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, hide_trip_amounts, status, phone, created_at) "
              "VALUES (?,?,?,?,0,?,?,?,?,'Active',?,'2026-01-01')", (uid_, f'u{uid_}', 'x', 'Manager', 'Other', level, modules, hide, f'91000{uid_}'))
    c.commit()
    return client(uid_)


T4 = mk_trip('TE-4', 15000, driver_name='D4'); c.commit()
n_pay = one('SELECT COUNT(*) FROM payments')
ro = user(900301, 'Read Only')
body = form_dict(f'/trips/edit/{T4}', ro)
r = ro.post(f'/trips/edit/{T4}', data={**{k: [v] for k, v in body.items()}, 'entry_kind': ['received'], 'entry_date': ['2026-10-05'], 'entry_amount': ['100'], 'entry_mode': ['Cash'], 'entry_ref': ['F1']})
fresh()
check(r.status_code in (302, 403) and one("SELECT COUNT(*) FROM payments WHERE reference_id='F1'") == 0, 'F: Read Only cannot save at all')
noled = user(900302, 'Limited Access', 'Trips:e')
r = save_edit(T4, [('received', '2026-10-05', '100', 'Cash', 'F2')], who=noled, driver_name='F-CHANGED')
fresh()
check(r.status_code in (200, 302, 403) and one("SELECT COUNT(*) FROM payments WHERE reference_id='F2'") == 0 and trip(T4)['driver_name'] == 'D4', 'F: Trips edit without Ledger edit cannot record payments, and the trip is not half-saved either')
check('edit access to the Ledger' in r.data.decode('utf-8', 'ignore') or r.status_code in (302, 403), 'F: it says why')
ledg = user(900303, 'Limited Access', 'Trips:e,Ledger:e')
r = save_edit(T4, [('received', '2026-10-05', '100', 'Cash', 'F3')], who=ledg)
fresh()
check(r.status_code == 302 and one("SELECT created_by FROM payments WHERE reference_id='F3'") == 900303, 'F: Trips edit + Ledger edit records it, attributed to that user')
hid = user(900304, 'Limited Access', 'Trips:v,Ledger:e', hide=1)
r = hid.get(f'/trips/edit/{T4}')
check(r.status_code in (302, 403), 'F: a "hide trip amounts" user cannot open the page that would let them')

# =====================================================================================================  G. the form
st, h = page(f'/trips/edit/{T}')
check('Quick Tips' not in h and 'tf-tips-list' not in h, 'G: the Quick Tips card is gone')
check('<label>Party Advance</label>' not in h and '<label>Payment Received</label>' not in h and 'Advance / Paid to Owner' not in h, 'G: the old Party Advance / Payment Received / Paid to Owner boxes are gone')
for nm in ('party_advance', 'payment_received', 'paid_to_owner'):
    check(re.search(r'<input type="hidden" name="%s" value="' % nm, h) is not None, f'G: the stored {nm} rides along hidden, so saving cannot change it by itself')
check('id="tpEntries"' in h and 'id="tpeModal"' in h and 'tfOpenEntry()' in h and "'kind'" in h and 'entry_' in h, 'G: the entries list is there and "Add entry" opens a pop-up')
check('.tpe-menu' in h and 'Edit</button>' in h and 'Delete</button>' in h and ('\u22ee' in h or '\\u22ee' in h), 'G: every entry has a three-dot menu with Edit and Delete')
check('<div class="modal-overlay" id="tpeModal">' in h and h.index('id="tpeModal"') > h.index('</form>'), 'G: the pop-up sits outside the trip form, so its fields are never posted by themselves')
check('.tf-fs:disabled .tpe-menu' in h and '.tf-fs:disabled .tpe-add' in h, 'G: while the trip is read-only the entries are shown, without the Add button and the menus')
check('Quick Tips' not in page('/trips/add')[1], 'G: and gone from Add Trip too')
# the ledger's own Add Payment is unchanged: default behaviour fills the party's oldest pending trips
P2 = mk_trip('TE-L1', 1000); P3 = mk_trip('TE-L2', 2000); c.commit()
r = cl.post(f'/payment/party/{party}', data={'date': '2026-10-11', 'amount': '2500', 'mode': 'UPI', 'reference_id': 'L1'}); fresh()
ids = {r_['trip_id']: r_['amount'] for r_ in q("SELECT trip_id, amount FROM payment_allocations WHERE payment_id=(SELECT id FROM payments WHERE reference_id='L1')")}
check(sum(ids.values()) == 2500 and OTHER in ids and P2 not in ids, 'G: a payment recorded in the LEDGER still goes to the party\'s OLDEST pending trip first, as before (not to the newest)')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
