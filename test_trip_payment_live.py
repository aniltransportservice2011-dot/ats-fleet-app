"""Regression test: the Payment Tracking figures on the trip forms (website Add / Edit Trip, phone Add / Update Trip wizard) update LIVE while the form is
edited, and what they show is exactly what the server stores when the trip is saved.

How it proves that: the REAL static/trip_payment_live.js is run in Node (with a small stand-in for the page) for many typed combinations — advance, payment
received (including below what the ledger already applied), paid to owner, fuel / driver advance, a changed owner amount — and its Balance / Received /
Pending / status is compared with the figures the server computes after the same values are actually saved through the edit form. Nothing is saved
until the form is submitted (viewing and typing store nothing).

  A. markup hooks exist on all four forms, the script is loaded, and tfRecalc / twPayLive call it on every edit (website + phone, add + edit);
  B. JS == server, party side: Received, Balance, status for ~60 typed combinations on trips with ledger payments (floor included);
  C. JS == server, owner side (hired trips): Paid, Pending (net of fuel / driver advance paid by us), status;
  D. nothing is stored by merely loading a page; the typed fields are the same inputs as before (no field renamed or removed);
  E. Add Trip shows the live figures (no history) and warns when the advance is more than the bill;
  F. the phone Review step shows Received and Balance.

Needs Node (already used for the UI checks). Usage:  python3 test_trip_payment_live.py
"""
import json
import os
import random
import re
import shutil
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


def read(p):
    return open(os.path.join(REPO, p), encoding='utf-8').read()


# ---------------------------------------------------------------------------------------------------------------- A. markup + wiring
js = read('static/trip_payment_live.js')
check('window.TripPayLive' in js and 'update:' in js and 'party:' in js, 'A: the shared live script exposes update() and party()')
check('Math.max(typed, dated)' in js and 'Math.max(typed, dated)' in js.replace('\n', ''), 'A: it applies the same floor the server does (never below what the ledger applied)')
check('Not saved yet' not in js and 'Not saved yet' not in read('templates/partials/_trip_payment_history.html'), 'A: no "not saved yet" message anywhere (the figures simply follow what is typed and revert if the page is left)')
check('No payment has been recorded' not in read('templates/partials/_trip_payment_history.html') + read('templates/app/_trip_payment_history.html') and 'Nothing has been paid to the owner' not in read('templates/partials/_trip_payment_history.html') + read('templates/app/_trip_payment_history.html'), 'A: no "no payment recorded yet" message in either card')
web = read('templates/trip_form.html')
phone = read('templates/app/trip_add.html')
check("asset_v('trip_payment_live.js')" in web and 'TripPayLive.update(document.getElementById(\'tripForm\'), net, ownerAmount, isHired)' in web, 'A: website form loads the script and tfRecalc (runs on every input / change) calls it')
check("asset_v('trip_payment_live.js')" in phone and "f.addEventListener('input', twPayLive)" in phone and "f.addEventListener('change', twPayLive)" in phone, 'A: phone wizard loads the script and re-draws on every input / change')
check('twPayLive();' in phone.split('function twUpdateReview')[1][:900] or 'twPayLive();' in phone, 'A: phone Review step refreshes the figures')
for path in ('templates/partials/_trip_payment_history.html', 'templates/app/_trip_payment_history.html'):
    s = read(path)
    for hook in ('data-tpl-root="{{ kind }}"', 'data-dated=', 'data-tpl="billed"', 'data-tpl="received"', 'data-tpl="balance"', 'data-tpl="pill"', 'data-tpl="bar-u"',
                 'data-tpl="bar-d"', 'data-tpl="note"', 'data-tpl="adv-row"', 'data-tpl="lump-row"', 'data-tpl="owner-amount"', 'data-tpl="pending"', 'data-tpl="company"'):
        check(hook in s, f'A: {path} has the {hook} hook')
    check('live_only' in s and 'data-add="1"' in s, f'A: {path} supports the new-trip (live only) mode')

# ---------------------------------------------------------------------------------------------------------------- fixtures
tmp = tempfile.mkdtemp(prefix='trippaylive_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp)
c.row_factory = sqlite3.Row
one = lambda q, *a: (c.execute(q, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TLV01','own')"); veh = one("SELECT id FROM vehicles WHERE vehicle_no='TLV01'")
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('TLH01','hired')"); hveh = one("SELECT id FROM vehicles WHERE vehicle_no='TLH01'")
c.execute("INSERT INTO parties (name) VALUES ('TL PARTY')"); party = one("SELECT id FROM parties WHERE name='TL PARTY'")
c.execute("INSERT INTO vendors (name) VALUES ('TL OWNER')"); owner = one("SELECT id FROM vendors WHERE name='TL OWNER'")
c.execute("INSERT INTO vendors (name) VALUES ('TL FUEL')"); fuel_v = one("SELECT id FROM vendors WHERE name='TL FUEL'")


def mk_trip(lr, billed, **kw):
    cols = {'date': '2026-09-20', 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'party_id': party, 'from_loc': 'A', 'to_loc': 'B', 'quantity': 10,
            'rate': billed / 10.0, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': 0}
    cols.update(kw)
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def gdb():
    x = sqlite3.connect(dbp)
    x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid
c.commit()


def fresh():
    global c
    c.close(); c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row


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


def form_fields(html_text):
    f = _Form(); f.feed(html_text)
    d = {}
    for n, v in f.fields:
        d.setdefault(n, v)
    return d


def save_edit(tid, entries=(), **typed):
    body = form_fields(cl.get(f'/trips/edit/{tid}').data.decode('utf-8', 'ignore'))
    body.update({k: str(v) for k, v in typed.items()})
    body['return_to'] = ''
    data = {k: [v] for k, v in body.items()}
    data['entry_kind'] = [e[0] for e in entries]; data['entry_date'] = ['2026-10-12'] * len(entries); data['entry_amount'] = [str(e[1]) for e in entries]
    data['entry_mode'] = ['Cash'] * len(entries); data['entry_ref'] = [f'LV{tid}-{i}' for i in range(len(entries))]
    return cl.post(f'/trips/edit/{tid}', data=data)


def stored_pay(tid):
    with m.app.test_request_context():
        from flask import g
        g.access_level = 'Full Access'
        cc = gdb()
        d = m._trip_detail_data(cc, tid)
        cc.close()
    return d


# ---------------------------------------------------------------------------------------------------------------- the real script, in Node
NODE = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const scenarios = JSON.parse(fs.readFileSync(0, 'utf8'));
function mkEl(attrs) {
  const el = { attrs: Object.assign({}, attrs || {}), hidden: false, textContent: '', className: '', style: {}, children: {},
    getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }, hasAttribute(n) { return n in this.attrs; },
    classList: { _s: new Set(), toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    querySelector(sel) { const m = sel.match(/\[data-tpl="([^"]+)"\]/); return m ? (this.children[m[1]] || null) : null; } };
  return el;
}
global.window = {};
new Function('window', src)(global.window);
const out = [];
for (const sc of scenarios) {
  const partyRoot = mkEl({ 'data-dated': String(sc.party_dated), 'data-nrows': '1', 'data-init-adv': '0', 'data-init-received': '0', 'data-init-billed': String(sc.billed), ...(sc.add ? { 'data-add': '1' } : {}) });
  const ownerRoot = mkEl({ 'data-dated': String(sc.owner_dated), 'data-nrows': '1', 'data-init-paid': '0', 'data-init-owner': '0', ...(sc.add ? { 'data-add': '1' } : {}) });
  for (const n of ['billed', 'received', 'balance', 'pill', 'bar', 'bar-u', 'bar-d', 'note', 'adv-row', 'adv-amt', 'lump-row', 'lump-amt', 'none']) partyRoot.children[n] = mkEl({ 'data-prefix': 'tph' });
  for (const n of ['owner-amount', 'company', 'paid', 'owner-net', 'pending', 'pill', 'bar', 'bar-u', 'bar-d', 'note', 'lump-row', 'lump-amt', 'none']) ownerRoot.children[n] = mkEl({ 'data-prefix': 'tph' });
  const rowEls = (sc.rows || []).map(r => ({ hasAttribute(n) { return n === 'data-id' && !!r.id; },
    getAttribute(n) { return n === 'data-id' ? (r.id || '') : n === 'data-changed' ? (r.changed ? '1' : '') : n === 'data-orig-alloc' ? String(r.origAlloc || 0) : null; },
    querySelector(sel) { return sel.indexOf('kind') > -1 ? { value: r.kind } : (sel.indexOf('vendor') > -1 ? { value: r.vendor || '' } : { value: String(r.amount) }); } }));
  const delEls = (sc.deleted || []).map(d => ({ getAttribute(n) { return n === 'data-kind' ? d.kind : String(d.alloc); } }));
  const form = { elements: {}, querySelectorAll(sel) { return { forEach: f => (sel.indexOf('entry_delete') > -1 ? delEls : rowEls).forEach(f) }; }, querySelector(sel) { return sel.indexOf('"party"') > -1 ? partyRoot : (sel.indexOf('"owner"') > -1 ? ownerRoot : null); } };
  for (const [k, v] of Object.entries(sc.fields)) form.elements[k] = { value: String(v) };
  window.TripPayLive.update(form, sc.billed, sc.owner_amount, sc.hired);
  const g = (r, n) => r.children[n];
  out.push({ party: { billed: g(partyRoot, 'billed').textContent, received: g(partyRoot, 'received').textContent, balance: g(partyRoot, 'balance').textContent,
                      pill: g(partyRoot, 'pill').hidden ? '' : g(partyRoot, 'pill').textContent, note: g(partyRoot, 'note').hidden ? '' : g(partyRoot, 'note').textContent,
                      advRow: !g(partyRoot, 'adv-row').hidden, lumpRow: !g(partyRoot, 'lump-row').hidden, lumpAmt: g(partyRoot, 'lump-amt').textContent },
             owner: { note: g(ownerRoot, 'note').hidden ? '' : g(ownerRoot, 'note').textContent, amount: g(ownerRoot, 'owner-amount').textContent, company: g(ownerRoot, 'company').textContent, paid: g(ownerRoot, 'paid').textContent,
                      pending: g(ownerRoot, 'pending').textContent, pill: g(ownerRoot, 'pill').hidden ? '' : g(ownerRoot, 'pill').textContent, hidden: ownerRoot.hidden } });
}
process.stdout.write(JSON.stringify(out));
'''
node_file = os.path.join(tmp, 'run_live.js')
open(node_file, 'w').write(NODE)


def run_live(scenarios):
    r = subprocess.run(['node', node_file, os.path.join(REPO, 'static/trip_payment_live.js')], input=json.dumps(scenarios), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(r.stderr[:500])
    return json.loads(r.stdout)


def money(t):
    return float(t.replace('₹', '').replace(',', '')) if t else 0.0


# ---------------------------------------------------------------------------------------------------------------- B. party side: JS == what the server stores
random.seed(7)
rows = []        # (trip id, billed, dated, scenario fields)
for i in range(30):
    billed = random.choice([10000, 20000, 35147.5, 48000, 7500, 125000])
    t = mk_trip(f'TL-P{i}', billed, party_advance=random.choice([0, 0, 1000, 2500]), payment_received=random.choice([0, 0, 500, 3000]))
    c.commit()
    dated = 0
    if i % 2 == 0:
        amt = random.choice([2000, 5000, 9000, billed / 4])
        r = cl.post(f'/payment/party/{party}', data={'date': '2026-10-01', 'amount': str(amt), 'mode': 'UPI', 'reference_id': f'TLP{i}', 'trip_ids': [str(t)]})
        fresh()
        dated = float(one('SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE trip_id=?', t))
    rows.append((t, billed, dated))
# a ledger payment that is bigger than its ticked trip spills onto the party's OTHER pending trips (oldest first), so read what each trip really has applied
# once every fixture payment exists, not at creation time
rows = [(t, b, float(one('SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE trip_id=?', t))) for (t, b, _d) in rows]
scen = []
plan = []
for (t, billed, dated) in rows:
    for _ in range(2):
        adv = random.choice([0, 500, 1000, 9000, round(billed * random.choice([0.1, 0.3, 0.5]), 2)])
        rec = random.choice([0, 500, 3000, 10000, round(dated + 1, 2), round(max(dated - 1000, 0), 2), round(billed * 0.5, 2), round(billed - adv, 2)])
        if adv > billed:
            adv = 0
        plan.append((t, billed, dated, adv, rec))
        scen.append({'billed': billed, 'owner_amount': 0, 'hired': False, 'party_dated': dated, 'owner_dated': 0, 'fields': {'party_advance': adv, 'payment_received': rec}})
live = run_live(scen)
bad = []
for (t, billed, dated, adv, rec), lv in zip(plan, live):
    r = save_edit(t, party_advance=adv, payment_received=rec)
    fresh()
    d = stored_pay(t)
    p = d['pay']['party']
    exp_due = max(p['due'], 0)
    got_due = money(lv['party']['balance'])
    got_recv = money(lv['party']['received'])
    if r.status_code not in (200, 302) or abs(exp_due - got_due) > 0.011 or abs(p['received_total'] - got_recv) > 0.011 or (p['status'] or '') != lv['party']['pill']:
        bad.append((t, billed, dated, adv, rec, 'server', round(exp_due, 2), p['received_total'], p['status'], 'js', got_due, got_recv, lv['party']['pill']))
check(not bad, f'B: JS balance / received / status == the server after saving, for {len(plan)} typed combinations (incl. below the ledger floor): {bad[:3]}')
floor_cases = [(pl, lv) for pl, lv in zip(plan, live) if pl[4] + 0.004 < pl[2]]
check(len(floor_cases) >= 3 and all('already recorded in the ledger' in lv['party']['note'] for _, lv in floor_cases), f'B: typing below what the ledger applied says so ({len(floor_cases)} cases)')

# ---------------------------------------------------------------------------------------------------------------- C. owner side
bad = []
plan_o = []
owner_trips = []
for i in range(16):
    qty = 10
    orate = random.choice([3000, 4000, 4500])
    fuel = random.choice([0, 6000, 9000])
    if i % 4 == 1 and not fuel:
        fuel = 6000
    drv = random.choice([0, 2000])
    t = mk_trip(f'TL-O{i}', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='TL OWNER', owner_rate=orate, owner_rate_type='PER_MT',
                fuel_amount=fuel, fuel_vendor_id=(owner if i % 4 == 1 else fuel_v) if fuel else None, driver_adv_amount=drv, paid_to_owner=random.choice([0, 0, 1000]))
    c.commit()
    dated = 0
    if i % 2 == 0:
        r = cl.post(f'/payment/vendor/{owner}', data={'date': '2026-10-02', 'amount': '6000', 'mode': 'Cash', 'reference_id': f'TLO{i}', 'trip_ids': [str(t)]})
        fresh()
        dated = float(one('SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE trip_id=?', t))
    owner_trips.append((t, orate, fuel, drv, (i % 4 == 1)))
scen_o = []
for (t, orate, fuel, drv, via_owner) in owner_trips:
    qty = 10
    dated = float(one('SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE trip_id=?', t))
    for paid in (0, 3000, round(dated + 500, 2), max(dated - 100, 0), 20000):
        plan_o.append((t, dated, paid, orate, fuel, drv))
        scen_o.append({'billed': 50000, 'owner_amount': orate * qty, 'hired': True, 'party_dated': 0, 'owner_dated': dated,
                       'fields': {'party_advance': 0, 'payment_received': 0, 'paid_to_owner': paid, 'owner_name': 'TL OWNER', 'fuel_vendor': ('TL OWNER' if via_owner else 'TL FUEL') if fuel else '',
                                  'fuel_amount': fuel, 'driver_adv_vendor': '', 'driver_adv_amount': drv}})
live_o = run_live(scen_o)
for (t, dated, paid, orate, fuel, drv), lv in zip(plan_o, live_o):
    save_edit(t, paid_to_owner=paid)
    fresh()
    o = stored_pay(t)['pay']['owner']
    exp_pending = max(o['pending'], 0)
    if abs(exp_pending - money(lv['owner']['pending'])) > 0.011 or abs(o['paid_total'] - money(lv['owner']['paid'])) > 0.011 or abs(o['company_paid'] - money(lv['owner']['company'])) > 0.011 \
            or (o['status'] or '') != lv['owner']['pill']:
        bad.append((t, dated, paid, 'server', round(exp_pending, 2), o['paid_total'], o['company_paid'], o['status'], 'js', lv['owner']))
check(not bad, f'C: JS Paid / Pending / company-paid / status == the server after saving, for {len(plan_o)} owner combinations: {bad[:2]}')
lv_nohire = run_live([{'billed': 1000, 'owner_amount': 0, 'hired': False, 'party_dated': 0, 'owner_dated': 0, 'fields': {}}])[0]
check(lv_nohire['owner']['hidden'] is True, 'C: the owner block is hidden when the trip is not hired')

# ---------------------------------------------------------------------------------------------------------------- B2/C2. payment ROWS being added
bad = []
plan_r = []; scen_r = []
for i in range(24):
    billed = random.choice([10000, 20000, 48000, 125000])
    t = mk_trip(f'TL-R{i}', billed, party_advance=random.choice([0, 1000]), payment_received=random.choice([0, 3000]))
    c.commit()
    rows_ = [('received', random.choice([500, 3000, 9000, billed, billed * 2])) for _ in range(random.choice([1, 2, 3]))]
    plan_r.append((t, billed, rows_))
for (t, billed, rows_) in plan_r:
    f = form_fields(cl.get(f'/trips/edit/{t}').data.decode('utf-8', 'ignore'))
    scen_r.append({'billed': billed, 'owner_amount': 0, 'hired': False, 'party_dated': 0, 'owner_dated': 0,
                   'fields': {'party_advance': f['party_advance'], 'payment_received': f['payment_received']}, 'rows': [{'kind': k, 'amount': a} for k, a in rows_]})
live_r = run_live(scen_r)
for (t, billed, rows_), lv in zip(plan_r, live_r):
    r = save_edit(t, entries=rows_)
    fresh()
    p = stored_pay(t)['pay']['party']
    exp_due = max(p['due'], 0)
    if 'nothing is left to receive' in lv['party']['note']:        # the live card flags a row the server will refuse; the server must refuse it (and save nothing)
        if r.status_code != 200 or one('SELECT COUNT(*) FROM payments WHERE reference_id LIKE ?', f'LV{t}-%') != 0:
            bad.append((t, rows_, 'flagged but the server saved', r.status_code))
        continue
    if r.status_code != 302 or abs(exp_due - money(lv['party']['balance'])) > 0.011 or abs(p['received_total'] - money(lv['party']['received'])) > 0.011 or (p['status'] or '') != lv['party']['pill']:
        bad.append((t, billed, rows_, r.status_code, 'server', round(exp_due, 2), p['received_total'], p['status'], 'js', lv['party']['balance'], lv['party']['received'], lv['party']['pill']))
check(not bad, f'B2: JS balance / received / status with payment ROWS == the server after saving the same rows, for {len(plan_r)} trips: {bad[:2]}')
check(any('more than this trip still owes' in lv['party']['note'] for lv in live_r) and any('nothing is left to receive' in lv['party']['note'] for lv in live_r), 'B2: entering more than the trip owes says the extra stays in the ledger as an advance; a row that comes when nothing is left is flagged')
bad = []; plan_w = []
for i in range(10):
    t = mk_trip(f'TL-W{i}', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='TL OWNER', owner_rate=4000, owner_rate_type='PER_MT',
                fuel_amount=6000, fuel_vendor_id=fuel_v, driver_adv_amount=2000)
    c.commit()
    plan_w.append((t, [('owner', random.choice([1000, 12000, 30000, 90000])) for _ in range(random.choice([1, 2]))]))
scen_w = []
for (t, rows_) in plan_w:
    f = form_fields(cl.get(f'/trips/edit/{t}').data.decode('utf-8', 'ignore'))
    scen_w.append({'billed': 50000, 'owner_amount': 40000, 'hired': True, 'party_dated': 0, 'owner_dated': 0,
                   'fields': {'party_advance': 0, 'payment_received': 0, 'paid_to_owner': f['paid_to_owner'], 'owner_name': 'TL OWNER', 'fuel_vendor': 'TL FUEL',
                              'fuel_amount': 6000, 'driver_adv_vendor': '', 'driver_adv_amount': 2000}, 'rows': [{'kind': k, 'amount': a} for k, a in rows_]})
live_w = run_live(scen_w)
for (t, rows_), lv in zip(plan_w, live_w):
    r = save_edit(t, entries=rows_)
    fresh()
    o = stored_pay(t)['pay']['owner']
    if 'nothing is left to pay the owner' in lv['owner']['note']:
        if r.status_code != 200 or one('SELECT COUNT(*) FROM payments WHERE reference_id LIKE ?', f'LV{t}-%') != 0:
            bad.append((t, rows_, 'flagged but the server saved', r.status_code))
        continue
    if r.status_code != 302 or abs(max(o['pending'], 0) - money(lv['owner']['pending'])) > 0.011 or abs(o['paid_total'] - money(lv['owner']['paid'])) > 0.011 or (o['status'] or '') != lv['owner']['pill']:
        bad.append((t, rows_, r.status_code, 'server', round(o['pending'], 2), o['paid_total'], o['status'], 'js', lv['owner']))
check(not bad, f'C2: JS Paid / Pending / status with owner ROWS == the server after saving them, for {len(plan_w)} hired trips: {bad[:2]}')

# ---------------------------------------------------------------------------------------------------------------- B3/C3. stored entries edited / removed, fuel vendors
def entry_post(tid, rows, deletes=()):
    body = form_fields(cl.get(f'/trips/edit/{tid}').data.decode('utf-8', 'ignore'))
    body['return_to'] = ''
    data = {k: [v] for k, v in body.items()}
    for k in ('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters'):
        data['entry_' + k] = [r.get(k, '') for r in rows]
    data['entry_delete'] = list(deletes)
    return cl.post(f'/trips/edit/{tid}', data=data)


bad = []
for i in range(12):
    billed = random.choice([20000, 48000])
    t = mk_trip(f'TL-E{i}', billed)
    c.commit()
    entry_post(t, [{'kind': 'received', 'date': '2026-10-01', 'amount': '4000', 'mode': 'UPI', 'ref': f'EA{i}'}, {'kind': 'received', 'date': '2026-10-02', 'amount': '3000', 'mode': 'Cash', 'ref': f'EB{i}'}])
    fresh()
    ids = [r_['id'] for r_ in c.execute("SELECT id FROM payments WHERE reference_id IN (?,?) ORDER BY id", (f'EA{i}', f'EB{i}')).fetchall()]
    f = form_fields(cl.get(f'/trips/edit/{t}').data.decode('utf-8', 'ignore'))
    dated = float(c.execute('SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE trip_id=?', (t,)).fetchone()[0])
    new_first = random.choice([1000, 9000, billed * 2])
    extra = random.choice([0, 2500])
    rows_ = [{'id': 'p%d' % ids[0], 'kind': 'received', 'changed': True, 'origAlloc': 4000, 'amount': new_first}]
    if extra:
        rows_.append({'kind': 'received', 'amount': extra})
    sc = {'billed': billed, 'owner_amount': 0, 'hired': False, 'party_dated': dated, 'owner_dated': 0,
          'fields': {'party_advance': f['party_advance'], 'payment_received': f['payment_received']}, 'rows': rows_,
          'deleted': [{'kind': 'received', 'alloc': 3000}]}
    lv = run_live([sc])[0]
    post_rows = [{'id': 'p%d' % ids[0], 'kind': 'received', 'date': '2026-10-01', 'amount': str(new_first), 'mode': 'UPI', 'ref': f'EA{i}x'}]
    if extra:
        post_rows.append({'kind': 'received', 'date': '2026-10-05', 'amount': str(extra), 'mode': 'Cash', 'ref': f'EN{i}'})
    r = entry_post(t, post_rows, ['p%d' % ids[1]])
    fresh()
    p = stored_pay(t)['pay']['party']
    exp_due = max(p['due'], 0)
    if 'nothing is left' in lv['party']['note']:
        if r.status_code != 200:
            bad.append((t, 'flagged but saved'))
        continue
    if r.status_code != 302 or abs(exp_due - money(lv['party']['balance'])) > 0.011 or abs(p['received_total'] - money(lv['party']['received'])) > 0.011:
        bad.append((t, billed, new_first, extra, r.status_code, 'server', round(exp_due, 2), p['received_total'], 'js', lv['party']['balance'], lv['party']['received']))
check(not bad, f'B3: JS == server after EDITING one stored payment, REMOVING another and adding a new one, for 12 trips: {bad[:2]}')

bad = []
for i in range(8):
    t = mk_trip(f'TL-F{i}', 50000, type='hired', vehicle_id=hveh, owner_vendor_id=owner, owner_name='TL OWNER', owner_rate=4000, owner_rate_type='PER_MT')
    c.commit()
    fuel_rows = [{'kind': 'fuel', 'amount': 3000, 'vendor': random.choice(['TL FUEL', 'TL OWNER', ''])}, {'kind': 'driver_adv', 'amount': 1000, 'vendor': random.choice(['Raju', 'TL OWNER'])}]
    f = form_fields(cl.get(f'/trips/edit/{t}').data.decode('utf-8', 'ignore'))
    sc = {'billed': 50000, 'owner_amount': 40000, 'hired': True, 'party_dated': 0, 'owner_dated': 0, 'rows': fuel_rows,
          'fields': {'party_advance': 0, 'payment_received': 0, 'paid_to_owner': f['paid_to_owner'], 'owner_name': 'TL OWNER',
                     'legacy_fuel_amount': f['legacy_fuel_amount'], 'legacy_fuel_vendor': f['legacy_fuel_vendor'], 'legacy_adv_amount': f['legacy_adv_amount'], 'legacy_adv_vendor': f['legacy_adv_vendor']}}
    lv = run_live([sc])[0]
    post_rows = [{'kind': r_['kind'], 'date': '2026-10-05', 'amount': str(r_['amount']), 'vendor': r_['vendor']} for r_ in fuel_rows]
    r = entry_post(t, post_rows)
    fresh()
    o = stored_pay(t)['pay']['owner']
    if r.status_code != 302 or abs(max(o['pending'], 0) - money(lv['owner']['pending'])) > 0.011 or abs(o['company_paid'] - money(lv['owner']['company'])) > 0.011:
        bad.append((t, fuel_rows, r.status_code, 'server', round(o['pending'], 2), o['company_paid'], 'js', lv['owner']['pending'], lv['owner']['company']))
check(not bad, f'C3: JS company-paid / pending == the server with fuel and driver-advance ENTRIES for different vendors (owner as vendor does not count): {bad[:2]}')

# ---------------------------------------------------------------------------------------------------------------- D. nothing stored by viewing / typing; fields unchanged
tid = rows[0][0]
snap = tuple(c.execute('SELECT payment_received, paid_to_owner, party_advance, billed_amount FROM trips WHERE id=?', (tid,)).fetchone())
n_pay = one('SELECT COUNT(*) FROM payments'); n_alloc = one('SELECT COUNT(*) FROM payment_allocations')
for url in (f'/trips/edit/{tid}', '/trips/add', f'/app/trips/{tid}/edit', '/app/trips/add'):
    cl.get(url)
fresh()
check(tuple(c.execute('SELECT payment_received, paid_to_owner, party_advance, billed_amount FROM trips WHERE id=?', (tid,)).fetchone()) == snap and one('SELECT COUNT(*) FROM payments') == n_pay
      and one('SELECT COUNT(*) FROM payment_allocations') == n_alloc, 'D: opening the forms stores nothing')
for url in (f'/trips/edit/{tid}', f'/app/trips/{tid}/edit', '/trips/add', '/app/trips/add'):
    names = set(form_fields(cl.get(url).data.decode('utf-8', 'ignore')))
    need = {'party_advance', 'payment_received', 'paid_to_owner', 'quantity', 'legacy_fuel_amount', 'legacy_adv_amount'}
    check(need <= names, f'D: {url} still posts the payment fields (website and phone alike: fuel / driver advance as the "earlier total")')

# ---------------------------------------------------------------------------------------------------------------- E. Add Trip: live figures, no history, warning
html = cl.get('/trips/add').data.decode('utf-8', 'ignore')
check('data-tpl-root="party"' in html and 'data-add="1"' in html and 'class="tph-tl"' not in html and '<details class="tph-manual"' not in html, 'E: website Add Trip has the live figures only (no history, plain inputs)')
check('data-tpl-root="owner"' in html and re.search(r'data-tpl-root="owner"[^>]*hidden', html) is not None, 'E: and the owner block is there but hidden until the trip is hired')
html = cl.get('/app/trips/add').data.decode('utf-8', 'ignore')
check('data-tpl-root="party"' in html and 'data-add="1"' in html and 'class="tph2-tl"' not in html, 'E: phone Add Trip has the live figures only')
add_live = run_live([{'billed': 10000, 'owner_amount': 0, 'hired': False, 'party_dated': 0, 'owner_dated': 0, 'add': True, 'fields': {'party_advance': 12000, 'payment_received': 0}},
                     {'billed': 10000, 'owner_amount': 0, 'hired': False, 'party_dated': 0, 'owner_dated': 0, 'add': True, 'fields': {'party_advance': 4000, 'payment_received': 0}}])
check('can’t be more than the trip bill' in add_live[0]['party']['note'] and add_live[1]['party']['note'] == '', 'E: on a new trip an advance above the bill is flagged live (the server refuses it); a normal one is not, and there is no extra line')
r = cl.post('/trips/add', data={'date': '2026-10-20', 'lr_number': 'TL-ADV', 'vehicle_no': 'TLV01', 'party_name': 'TL PARTY', 'quantity': '10', 'rate': '1000', 'rate_type': 'PER_MT',
                                 'party_advance': '12000', 'payment_received': '0', 'type': 'own'})
fresh()
check(one("SELECT COUNT(*) FROM trips WHERE lr_number='TL-ADV'") == 0, 'E: and the server indeed refuses that advance (so the live warning is truthful)')

# ---------------------------------------------------------------------------------------------------------------- F. phone review step
check('id="rvReceived"' in phone and 'id="rvBalance"' in phone and "getElementById('rvReceived')" in phone and "getElementById('rvBalance')" in phone, 'F: the phone Review step has Received and Balance due lines, filled by the same live figures')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
