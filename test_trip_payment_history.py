"""Regression test: a payment recorded in the LEDGER is listed, with its date, inside the trip (website trip edit page right-hand Payment Tracking
card, phone trip edit wizard, phone trip detail Costs tab) — and nothing about how payments are stored or saved changes.

Runs on a disposable copy of fleet.db (the real file is never touched, network blocked). It proves:
  A. a ledger payment shows on that trip: date, mode, reference, recorded-by (a name, never the login phone number), "part of ₹X" when split
     across trips, a label worked out from the payment (Advance / Part payment / Balance payment / Full payment / Owner payment);
  B. undated money (an advance or amount typed on the trip, or imported) is shown as "No date" — never given a date — and the numbers add up;
  C. the trip form is untouched: Party Advance / Payment Received are still real inputs inside the form (a closed <details> still submits them),
     and re-saving the edit form exactly as shown changes no stored total and no payment / allocation row;
  D. "Open in ledger" / "Open ledger" stay in the SAME window (no target=_blank), go to the right party / vendor ledger, and the ledger opens that
     payment's detail (website) or highlights its card (phone);
  E. access: a user without Ledger access sees the history but no ledger links; Read Only sees it; "hide trip amounts" sees none of it;
  F. there is no way to add a payment from the trip (payments are only recorded in the Ledger);
  G. every trip in the database renders on all three screens.

Usage:  python3 test_trip_payment_history.py
"""
import html as H
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


tmp = tempfile.mkdtemp(prefix='trippayhist_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp)
c.row_factory = sqlite3.Row
one = lambda q, *a: (c.execute(q, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("UPDATE users SET full_name='Test Runner', phone='9000000000' WHERE id=?", (uid,))
veh = one('SELECT MIN(id) FROM vehicles') or c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('THV01','own')").lastrowid
c.execute("INSERT INTO parties (name) VALUES ('TH PARTY')"); party = one("SELECT id FROM parties WHERE name='TH PARTY'")
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('THH01','hired')"); hveh = one("SELECT id FROM vehicles WHERE vehicle_no='THH01'")
c.execute("INSERT INTO vendors (name) VALUES ('TH OWNER')"); owner = one("SELECT id FROM vendors WHERE name='TH OWNER'")


def trip(lr, date, billed, **kw):
    cols = {'date': date, 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'party_id': party, 'from_loc': 'A', 'to_loc': 'B', 'quantity': 10,
            'rate': 1000, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': 0}
    cols.update(kw)
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


T1 = trip('TH-1', '2026-09-10', 10000)
T2 = trip('TH-2', '2026-09-11', 20000)
T3 = trip('TH-3', '2026-09-12', 30000, party_advance=5000)                      # advance typed on the trip
T4 = trip('TH-4', '2026-09-13', 8000, payment_received=3000)                    # amount typed on the trip, no payment record
T5 = trip('TH-5', '2026-09-14', 12000)                                          # will get ONE payment for the whole bill
HT = trip('TH-HIRED', '2026-09-15', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='TH OWNER', owner_rate=4000, owner_rate_type='PER_MT')
NT = trip('TH-NONE', '2026-09-16', 9000)                                        # no payment of any kind
EMP = trip('TH-EMPTY', '2026-09-17', 0, party_id=None, is_empty=1)
c.commit()


def gdb():
    x = sqlite3.connect(dbp)
    x.row_factory = sqlite3.Row
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


def page(url, who=None):
    r = (who or cl).get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


def ledger_pay(party_id, date, amount, mode, ref, trip_ids=None, remarks=''):
    data = {'date': date, 'amount': str(amount), 'mode': mode, 'reference_id': ref, 'remarks': remarks}
    if trip_ids:
        data['trip_ids'] = [str(t) for t in trip_ids]
    r = cl.post(f'/payment/party/{party_id}', data=data)
    fresh()
    return r.status_code


def vendor_pay(vendor_id, date, amount, mode, ref, trip_ids):
    r = cl.post(f'/payment/vendor/{vendor_id}', data={'date': date, 'amount': str(amount), 'mode': mode, 'reference_id': ref, 'trip_ids': [str(t) for t in trip_ids]})
    fresh()
    return r.status_code


def payid(ref):
    return one('SELECT id FROM payments WHERE reference_id=?', ref)


# ---- payments, all made in the LEDGER
ledger_pay(party, '2026-09-05', 4000, 'Cash', 'ADV-1', [T1])                                  # dated BEFORE TH-1's date -> Advance
ledger_pay(party, '2026-10-01', 6000, 'UPI', 'UTR-1', [T1])                                   # clears TH-1 -> Balance payment
ledger_pay(party, '2026-10-02', 25000, 'Bank Transfer', 'NEFT-1', [T2, T3], 'split across two trips')   # 20000 to TH-2, 5000 to TH-3 (TH-3 owes 25000)
ledger_pay(party, '2026-10-03', 12000, 'UPI', 'FULL-1', [T5])                                 # the only money on TH-5 -> Full payment
vendor_pay(owner, '2026-10-04', 15000, 'Bank Transfer', 'OWN-1', [HT])
snap = {r['id']: (r['payment_received'], r['paid_to_owner'], r['party_advance']) for r in q('SELECT id, payment_received, paid_to_owner, party_advance FROM trips')}
snap_pay = [tuple(r) for r in q('SELECT id, date, amount, allocated_amount, mode, reference_id FROM payments ORDER BY id')]
snap_alloc = [tuple(r) for r in q('SELECT id, payment_id, trip_id, amount FROM payment_allocations ORDER BY id')]

W = lambda tid: f'/trips/edit/{tid}'
P_EDIT = lambda tid: f'/app/trips/{tid}/edit'
P_DET = lambda tid: f'/app/trips/{tid}?tab=costs'

# =====================================================================================================  A. ledger payments show on the trip
st, h = page(W(T1))
check(st == 200 and 'id="tph-party"' in h, 'A: website trip edit page has the payment history in the right-hand card')
check('05 Sep 2026' in h and '01 Oct 2026' in h, 'A: shows both payment dates')
check('Cash' in h and 'UPI' in h and 'ref UTR-1' in h and 'ref ADV-1' in h, 'A: shows mode and reference')
check(h.count('by Test Runner') == 2 and '9000000000' not in h.split('id="tph-party"')[1].split('</details>')[0], 'A: shows who recorded it by NAME (never the login phone number)')
check(re.search(r'tph-chip adv">Advance<', h) and re.search(r'tph-chip bal">Balance payment<', h), 'A: label — a payment dated before the trip reads Advance; the one that clears the bill reads Balance payment')
check('tph-pill tph-paid' in h and '>Paid<' in h, 'A: a fully received trip reads Paid')
st, h2 = page(W(T2))
check('tph-chip full">Full payment<' in h2 and 'part of ₹25,000' in h2, 'A: TH-2 got 20,000 of a 25,000 payment: it says "part of ₹25,000", and since that clears it, it reads Full payment')
st, h3p = page(W(T3))
check('tph-chip part">Part payment<' in h3p and 'part of ₹25,000' in h3p and 'Partly paid' in h3p, 'A: TH-3 got the other 5,000: a Part payment, status Partly paid')
st, h5 = page(W(T5))
check('tph-chip full">Full payment<' in h5, 'A: the only money on a trip that clears it reads Full payment')
st, hh = page(W(HT))
check('id="tph-owner"' in hh and 'Owner payment' in hh and 'ref OWN-1' in hh and '04 Oct 2026' in hh, 'A: a hired trip lists the owner payment too')
st, hn = page(W(NT))
check('No payment has been recorded' not in hn and 'will appear here with their date' not in hn, 'A: a trip with no payment shows no explanatory message (the totals simply show nothing received)')
st, he = page(W(EMP))
check(st == 200 and 'id="tph-party"' not in he, 'A: a trip with no party has no party section (and still opens)')
# phone: edit wizard and detail
st, pe = page(P_EDIT(T1))
check(st == 200 and 'id="tph2-party"' in pe and '05 Sep 2026' in pe and 'ref UTR-1' in pe, 'A: phone edit wizard shows the history in Payment Tracking')
st, pd = page(P_DET(T2))
check(st == 200 and 'id="tph2-party"' in pd and 'tph2-card' in pd and 'part of ₹25,000' in pd and '02 Oct 2026' in pd, 'A: phone trip detail Costs tab shows the history')
check('by Test Runner' in pd and 'Full payment' in pd and 'Part payment' in page(P_DET(T3))[1], 'A: phone shows who and the label')
# the number on the screen is the number in the ledger
with m.app.test_request_context():
    led = m._get_party_ledger_entries(party)
ledger_credit = {}
for e in led:
    if e.get('kind') == 'Payment In':
        ledger_credit[e['date']] = ledger_credit.get(e['date'], 0) + e['credit']
check(abs(ledger_credit.get('2026-10-02', 0) - 25000) < 0.01, 'A: the ledger carries the same 25,000 that the two trips show between them')

# =====================================================================================================  B. undated money
st, h3 = page(W(T3))
check('No date' in h3 and 'Party advance' in h3 and 'typed on this trip' in h3, 'B: the advance typed on the trip shows as a dated-as-unknown row')
st, h4 = page(W(T4))
check('without a payment record' in h4 and h4.count('>No date<') >= 1, 'B: amount typed on the trip shows as "No date"')
with m.app.test_request_context():
    from flask import g
    g.access_level = 'Full Access'
    cc = gdb()
    for tid in (T1, T2, T3, T4, T5):
        d = m._trip_detail_data(cc, tid)
        p = d['pay']['party']
        t = d['t']
        check(abs(p['dated_total'] + p['before_tracking'] - (t['payment_received'] or 0)) < 0.01, f'B: trip {tid} — dated + undated = the stored payment_received')
        check(abs(p['due'] - d['balance_due']) < 0.01, f'B: trip {tid} — the history\'s Balance equals the existing Balance Due')
        check(all(r['raw_date'] for r in p['rows']), f'B: trip {tid} — every dated row really has a date')
    cc.close()
check(snap[T3][2] == 5000 and snap[T4][0] == 3000, 'B: the typed amounts were not changed by showing them')

# =====================================================================================================  C. the form is untouched
class _Form(HTMLParser):
    def __init__(self):
        super().__init__(); self.inform = False; self.depth_details = 0; self.fields = []; self.in_details_names = set(); self._sel = None; self._ta = None
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'form' and a.get('id') in ('tripForm', 'twForm'):
            self.inform = True
        if not self.inform:
            return
        if tag == 'details':
            self.depth_details += 1
        if tag == 'input' and a.get('name'):
            t = (a.get('type') or 'text').lower()
            if t in ('checkbox', 'radio') and 'checked' not in a:
                return
            if t in ('submit', 'button'):
                return
            self.fields.append((a['name'], a.get('value', '')))
            if self.depth_details:
                self.in_details_names.add(a['name'])
        elif tag == 'select' and a.get('name'):
            self._sel = [a['name'], None, None]
        elif tag == 'option' and self._sel:
            if 'selected' in a or self._sel[1] is None:
                if 'selected' in a or self._sel[2] is None:
                    self._sel[1] = a.get('value', '')
            if self._sel[2] is None:
                self._sel[2] = a.get('value', '')
        elif tag == 'textarea' and a.get('name'):
            self._ta = [a['name'], '']
    def handle_endtag(self, tag):
        if tag == 'details' and self.depth_details:
            self.depth_details -= 1
        if tag == 'select' and self._sel:
            self.fields.append((self._sel[0], self._sel[1] if self._sel[1] is not None else (self._sel[2] or ''))); self._sel = None
        if tag == 'textarea' and self._ta:
            self.fields.append(tuple(self._ta)); self._ta = None
    def handle_data(self, d):
        if self._ta is not None:
            self._ta[1] += d


def form_of(html_text):
    f = _Form(); f.feed(html_text)
    return f


fp = form_of(page(W(T2))[1])
names = [n for n, _ in fp.fields]
check('party_advance' in names and 'payment_received' in names, 'C: Party Advance and Payment Received are still inputs inside the trip form')
check('<input type="hidden" name="party_advance"' in page(W(T2))[1] and '<input type="hidden" name="payment_received"' in page(W(T2))[1], 'C: the stored totals travel with the form as hidden fields, so saving never changes them by itself')
check('Edit totals by hand' not in page(W(T2))[1] and 'id="tpEntries"' in page(W(T2))[1], 'C: payments are added as dated entries now (the by-hand boxes are gone)')
# add mode (new trip) is exactly as before: plain inputs, no history, no details
st, ah = page('/trips/add')
check(st == 200 and 'name="payment_received"' in ah and 'class="tph-tl"' not in ah and 'id="tpEntries"' in ah, 'C: Add Trip has the entries list and no history list (only the live figures, see test_trip_payment_live.py)')
# re-save the edit form exactly as shown -> no stored number and no payment/allocation row changes
for tid in (T1, T2, T3, T4, HT):
    body = {}
    for n, v in form_of(page(W(tid))[1]).fields:
        body.setdefault(n, v)
    body['return_to'] = ''
    r = cl.post(W(tid), data=body)
    fresh()
    now = {rr['id']: (rr['payment_received'], rr['paid_to_owner'], rr['party_advance']) for rr in q('SELECT id, payment_received, paid_to_owner, party_advance FROM trips')}
    check(r.status_code in (302, 200) and now[tid] == snap[tid], f'C: re-saving trip {tid} unchanged leaves its payment totals exactly as they were ({snap[tid]} -> {now[tid]})')
check(snap_pay == [tuple(r) for r in q('SELECT id, date, amount, allocated_amount, mode, reference_id FROM payments ORDER BY id')], 'C: no payment row changed')
check(snap_alloc == [tuple(r) for r in q('SELECT id, payment_id, trip_id, amount FROM payment_allocations ORDER BY id')], 'C: no allocation row changed')
# typing a different total by hand still works exactly as before (floored at what the ledger already allocated)
body = {}
for n, v in form_of(page(W(T1))[1]).fields:
    body.setdefault(n, v)
body['payment_received'] = '1'
cl.post(W(T1), data=body); fresh()
check(one('SELECT payment_received FROM trips WHERE id=?', T1) == 10000, 'C: the existing floor still applies — a hand-typed total cannot drop below what the ledger allocated')

# =====================================================================================================  D. links stay in the same window and land on the payment
pid1 = payid('UTR-1'); pidN = payid('NEFT-1'); pidO = payid('OWN-1')
st, h = page(W(T1))
links = re.findall(r'<a class="tph-lk" href="([^"]+)"([^>]*)>', h)
check(len(links) == 2 and all('target=' not in rest for _, rest in links), 'D: "Open in ledger" has no target — it opens in the same window')
check(f'/ledger/party/{party}"' in H.unescape(h) and 'open_payment' not in h, 'D: it goes to this party\'s ledger — just the ledger, no pop-up asked for')
st, hh = page(W(HT))
check(f'/ledger/vendor/{owner}"' in H.unescape(hh) and 'open_payment' not in hh, 'D: an owner payment goes to the owner\'s ledger, also without a pop-up')
for lurl in (f'/ledger/party/{party}', f'/ledger/party/{party}?open_payment={pid1}', f'/ledger/vendor/{owner}?open_payment={pidO}'):
    st, lh = page(lurl)
    check(st == 200 and "openRecordModal('/payments/'" not in lh and "get('open_payment')" not in lh, f'D: the ledger never opens a payment pop-up on arrival ({lurl})')
check(page(f'/payments/{pid1}/view')[0] == 200, 'D: the payment detail it opens exists')
st, ph = page(P_DET(T2))
pl = re.findall(r'<a class="tph2-lk" href="([^"]+)"([^>]*)>', ph)
check(len(pl) == 1 and 'target=' not in pl[0][1] and f'/app/ledger/party/{party}?show=300&pay={pidN}' in H.unescape(pl[0][0]), 'D: phone "Open ledger" stays in the same window and names the payment')
st, lph = page(f'/app/ledger/party/{party}?show=300&pay={pidN}')
check(st == 200 and f'data-pay="{pidN}"' in lph and "params.get('pay')" in lph, 'D: the phone ledger marks that payment\'s card and scrolls to it')
check('.ld-txn-card.ld-hl' in open('static/app.css').read(), 'D: the phone ledger highlights it')
base = open('templates/base.html').read()
check("navType === 'reload' || navType === 'back_forward'" in base, 'D: Back lands on the trip again where it was left (scroll restored on Back / Forward)')

# =====================================================================================================  E. access
def user(uid_, level, modules='', hide=0):
    c.execute("INSERT OR REPLACE INTO users (id, username, password_hash, role, is_admin, full_name, access_level, module_access, hide_trip_amounts, status, phone, created_at) "
              "VALUES (?,?,?,?,0,?,?,?,?,'Active',?,'2026-01-01')", (uid_, f'u{uid_}', 'x', 'Manager', 'Other User', level, modules, hide, f'91000{uid_}'))
    c.commit()
    return client(uid_)


ro = user(900201, 'Read Only')
st, h = page(W(T1), ro)
check(st == 200 and 'id="tph-party"' in h and '<a class="tph-lk"' in h, 'E: Read Only sees the history (and the ledger links — they can read the ledger)')
noled = user(900202, 'Limited Access', 'Trips:e')
st, h = page(W(T1), noled)
check(st == 200 and 'id="tph-party"' in h and '<a class="tph-lk"' not in h, 'E: no Ledger access = history shown, no ledger links')
st, h = page(P_DET(T2), noled)
check(st == 200 and 'id="tph2-party"' in h and '<a class="tph2-lk"' not in h, 'E: same on the phone')
hid = user(900203, 'Limited Access', 'Trips:v,Ledger:v', hide=1)
st, h = page(P_DET(T2), hid)
check(st == 200 and 'id="tph2-party"' not in h and 'UTR' not in h and 'NEFT-1' not in h, 'E: a "hide trip amounts" user gets no payment history on the phone detail')
r = hid.get(W(T1))
check(r.status_code in (302, 403) or 'NEFT-1' not in r.data.decode('utf-8', 'ignore'), 'E: and cannot reach the edit page that would show it')
with m.app.test_request_context():
    from flask import g
    g.access_level = 'Limited Access'; g.hide_trip_amounts = True; g.modules = {'Trips': 'v'}
    cc = gdb()
    check(m._trip_detail_data(cc, T2)['pay'] == {'hidden': True}, 'E: the data layer returns nothing for a hidden-amounts user')
    cc.close()

# =====================================================================================================  F. no way to add a payment from a trip
rules = {r.rule for r in m.app.url_map.iter_rules()}
check(not any(re.search(r'/trips/.*/payment', r) for r in rules), 'F: there is no "add payment" route on a trip — payments are only recorded in the Ledger')
for h_ in (page(W(T2))[1], page(P_EDIT(T2))[1], page(P_DET(T2))[1]):
    check('+ Add payment' not in h_ and 'Add payment' not in h_.split('Payment Tracking')[-1][:4000], 'F: no Add payment button on the trip screens')

# =====================================================================================================  G. every trip renders
fresh()
ids = [r['id'] for r in q('SELECT id FROM trips ORDER BY id')]
bad = []
for tid in ids:
    codes = (page(W(tid))[0], page(P_EDIT(tid))[0], page(P_DET(tid))[0])
    if codes != (200, 200, 200):
        bad.append((tid, codes))
check(not bad, f'G: all {len(ids)} trips open on the website edit page, the phone edit wizard and the phone detail: {bad[:5]}')
moved = {r['id'] for r in q('SELECT id, payment_received, paid_to_owner, party_advance FROM trips') if snap.get(r['id']) not in (None, (r['payment_received'], r['paid_to_owner'], r['party_advance']))}
check(not moved, f'G: opening and re-saving trips moved no payment total: {moved}')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
