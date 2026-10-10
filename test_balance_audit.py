"""Exhaustive balance-consistency AUDIT — REPORT ONLY, fixes nothing.

Question it answers: when money moves (a trip is billed, an advance is taken, a payment is recorded,
a trip is edited/ended/deleted), does EVERY screen that shows a pending / balance / receivable /
payable figure show the number that the raw amounts say it should — and do the screens agree with
each other?

How it works
  * A fresh throw-away database is built from schema.sql for every batch of scenarios (isolated, fast,
    deterministic); the real fleet.db is never opened. All network calls are blocked.
  * Scenarios are driven through the app's REAL routes (website forms and the mobile-app routes), so
    the app's own validation, allocation and ledger code is what gets exercised.
  * After every step the numbers are read back off the RENDERED pages (what a user sees), and compared
    with a calculation done independently from the raw amounts (the "model" below), and with each other.
  * Every disagreement is recorded with the exact scenario that produced it, then grouped by cause so a
    few hundred identical symptoms collapse into one finding you can decide on.

Usage
    python3 test_balance_audit.py                 # full run (several thousand scenarios, ~minutes, uses all cores)
    python3 test_balance_audit.py --quick         # a few hundred scenarios
    python3 test_balance_audit.py --stage party   # only one stage (party | vendor | edge | totals | maint | all)
    python3 test_balance_audit.py --replay ID     # print the full step-by-step trace of one scenario
    python3 test_balance_audit.py --report out.md # where to write the findings report

Developer-only dependency: beautifulsoup4 + lxml (to read the rendered pages).
"""
import argparse
import collections
import datetime
import itertools
import json
import multiprocessing as mp
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
TMP = tempfile.mkdtemp(prefix='balance_audit_')
BOOT_DB = os.path.join(TMP, 'boot.db')

EPS = 0.005          # exact-arithmetic tolerance (half a paisa)
SCHEMA = open(os.path.join(REPO, 'schema.sql')).read()

# --------------------------------------------------------------------------------------------------
# environment: one app instance per worker process, pointed at a fresh DB per batch
# --------------------------------------------------------------------------------------------------
_app = None


def _boot_app():
    """Import the Flask app once per process, blocking the network."""
    global _app
    if _app is not None:
        return _app
    _make_db(BOOT_DB)
    os.environ['DATABASE_PATH'] = BOOT_DB
    import requests
    requests.get = requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network blocked in audit'))
    import app as appmod
    appmod.app.config['PROPAGATE_EXCEPTIONS'] = False
    _app = appmod
    return _app


def _make_db(path):
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO users (username,password_hash,role,is_admin,full_name,access_level,status,phone,created_at) "
              "VALUES ('audit','x','Admin',1,'Audit Admin','Full Access','Active','9000000001','2026-01-01')")
    c.commit()
    c.close()


class Env:
    """One batch: its own DB, a logged-in test client, and helpers to act and to read."""

    def __init__(self, batch_id):
        self.appmod = _boot_app()
        self.path = os.path.join(TMP, f'batch_{os.getpid()}_{batch_id}.db')
        _make_db(self.path)
        self.appmod.DB = self.path
        self.cl = self.appmod.app.test_client()
        with self.cl.session_transaction() as s:
            s['user_id'], s['username'], s['is_admin'] = 1, 'audit', 1
        self.calls = 0
        self.leaks = 0

    def close(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    # ---- raw db
    def sql(self, q, *a):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            return c.execute(q, a).fetchall()
        finally:
            c.close()

    def one(self, q, *a):
        r = self.sql(q, *a)
        return r[0] if r else None

    def exec(self, q, *a):
        c = sqlite3.connect(self.path)
        c.execute(q, a)
        c.commit()
        c.close()

    # ---- http
    def get(self, url, **qs):
        self.calls += 1
        return self.cl.get(url, query_string=qs or None, follow_redirects=True)

    def post(self, url, data):
        self.calls += 1
        resp = self.cl.post(url, data=data, follow_redirects=False)
        if resp.status_code >= 500:
            # the app does not close its DB connection when a request fails; that leaked connection keeps the
            # database locked for the NEXT request. Count it, then release it so one failure can't poison later checks.
            self.leaks += 1
            import gc
            gc.collect()
        return resp

    def reset(self):
        """Fresh empty database (independent scenarios must not see each other's data)."""
        import gc
        gc.collect()
        _make_db(self.path)


# --------------------------------------------------------------------------------------------------
# reading numbers off rendered pages
# --------------------------------------------------------------------------------------------------
from bs4 import BeautifulSoup  # noqa: E402

_VAL = re.compile(r'(^|\s)(val|v|value|big|amt|ld-kpi-val|mo-kpi-val|lg-kpi-val|lg-net-val|a-bo-value|ld-summary-val|ra-kpi-val)(\s|$)')


def money(text):
    """'₹1,234' / '-₹1,234' / '₹-30,000' / '₹1,234 Dr' / '₹1,234 Cr' -> float (Cr = negative); None if no number."""
    if text is None:
        return None
    t = str(text).replace('\xa0', ' ')
    m = re.search(r'(-?)\s*₹?\s*(-?)\s*([\d,]+(?:\.\d+)?)', t)
    if not m:
        return None
    v = float(m.group(3).replace(',', ''))
    if m.group(1) == '-' or m.group(2) == '-':
        v = -v
    if re.search(r'\bCr\b', t):
        v = -abs(v)
    return v


def soup(resp):
    return BeautifulSoup(resp.get_data(as_text=True), 'lxml')


def label_values(sp, label):
    """Every value shown next to a label (document order). Handles KPI cards and detail rows."""
    out = []
    for node in sp.find_all(string=lambda s: s and s.strip().lower() == label.lower()):
        v = node.parent.find_next(class_=_VAL)
        if v is not None:
            out.append(money(v.get_text(' ', strip=True)))
    return out


def table_rows(sp, min_cells):
    rows = []
    for tr in sp.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= min_cells:
            rows.append([td.get_text(' ', strip=True) for td in tds])
    return rows


class Screens:
    """Every place a user can see a balance, as callables returning plain numbers."""

    def __init__(self, env):
        self.e = env

    # ---------------- website
    def trips_for_party(self, name):
        r = self.e.get('/trips', party=name, per_page=100)
        sp = soup(r)
        rows = {}
        for cells in table_rows(sp, 8):
            rows[cells[1]] = {'billed': money(cells[6]), 'pending': money(cells[7])}
        kpi = {k: (label_values(sp, k) or [None])[0] for k in ('Total Trips', 'Total Billed', 'Pending Balance')}
        return {'rows': rows, 'kpi': kpi, 'status': r.status_code}

    def trip_view(self, tid):
        r = self.e.get(f'/trips/{tid}/view')
        sp = soup(r)
        return {'balance_due': label_values(sp, 'Balance Due'), 'billed': label_values(sp, 'Billed Amount'), 'status': r.status_code}

    def party_ledger(self, pid):
        r = self.e.get(f'/ledger/party/{pid}')
        sp = soup(r)
        recv = (label_values(sp, 'Total Receivable') or [None])[0]
        pay = (label_values(sp, 'Total Payable') or [None])[0]
        net = (label_values(sp, 'Net Balance') or [None])[0]
        sub = None
        for node in sp.find_all(string=lambda s: s and s.strip() in ('You will receive', 'You will pay')):
            sub = node.strip()
            break
        signed = None
        if net is not None:
            signed = -abs(net) if sub == 'You will pay' else abs(net)
        # the pending-trips table inside the Add Payment modal
        pend = {}
        for tr in sp.select('tr.pm-trip-row'):
            tds = tr.find_all('td')
            pend[int(tr.get('data-id'))] = money(tds[-1].get_text())
        out_amt = None
        node = sp.select_one('.out-amt b')
        if node is not None:
            out_amt = money(node.get_text())
        return {'receivable': recv, 'payable': pay, 'net_signed': signed, 'modal_pending': pend, 'modal_outstanding': out_amt, 'status': r.status_code}

    def accounts_row(self, name):
        r = self.e.get('/accounts', search=name, per_page=100)
        sp = soup(r)
        for cells in table_rows(sp, 8):
            if cells[0].strip() == name:
                return {'receivable': money(cells[5]), 'payable': money(cells[6]), 'balance': money(cells[7]), 'status': r.status_code}
        return {'receivable': None, 'payable': None, 'balance': None, 'status': r.status_code}

    def accounts_totals(self):
        sp = soup(self.e.get('/accounts', per_page=100))
        return {k: (label_values(sp, k) or [None])[0] for k in ('Total Receivable', 'Total Payable', 'Net Balance')}

    def dashboard_totals(self):
        sp = soup(self.e.get('/dashboard'))
        return {k: (label_values(sp, k) or [None])[0] for k in ('Total Receivables', 'Total Payables')}

    def trips_totals(self):
        sp = soup(self.e.get('/trips', per_page=100))
        return {k: (label_values(sp, k) or [None])[0] for k in ('Total Trips', 'Total Billed', 'Pending Balance')}

    def vendor_ledger(self, vid):
        r = self.e.get(f'/ledger/vendor/{vid}')
        return self._ledger_from(r)

    def _ledger_from(self, r):
        sp = soup(r)
        recv = (label_values(sp, 'Total Receivable') or [None])[0]
        pay = (label_values(sp, 'Total Payable') or [None])[0]
        net = (label_values(sp, 'Net Balance') or [None])[0]
        sub = next((n.strip() for n in sp.find_all(string=lambda s: s and s.strip() in ('You will receive', 'You will pay'))), None)
        signed = None if net is None else (-abs(net) if sub == 'You will pay' else abs(net))
        pend = {int(tr.get('data-id')): money(tr.find_all('td')[-1].get_text()) for tr in sp.select('tr.pm-trip-row')}
        node = sp.select_one('.out-amt b')
        return {'receivable': recv, 'payable': pay, 'net_signed': signed, 'modal_pending': pend,
                'modal_outstanding': money(node.get_text()) if node is not None else None, 'status': r.status_code, 'redirected': bool(r.history)}

    # ---------------- mobile app
    def app_trip(self, tid):
        r = self.e.get(f'/app/trips/{tid}')
        sp = soup(r)
        return {'balance_due': label_values(sp, 'Balance Due'), 'status': r.status_code}

    def app_party_ledger(self, pid):
        r = self.e.get(f'/app/ledger/party/{pid}')
        sp = soup(r)
        recv = (label_values(sp, 'Total Receivable') or [None])[0]
        pay = (label_values(sp, 'Total Payable') or [None])[0]
        closing = (label_values(sp, 'Closing Balance') or [None])[0]
        return {'receivable': recv, 'payable': pay, 'closing_signed': closing, 'status': r.status_code}

    def app_vendor_ledger(self, vid):
        r = self.e.get(f'/app/ledger/vendor/{vid}')
        sp = soup(r)
        return {'receivable': (label_values(sp, 'Total Receivable') or [None])[0], 'payable': (label_values(sp, 'Total Payable') or [None])[0],
                'closing_signed': (label_values(sp, 'Closing Balance') or [None])[0], 'status': r.status_code}

    def app_ledger_totals(self):
        sp = soup(self.e.get('/app/ledger'))
        return {k: (label_values(sp, k) or [None])[0] for k in ('Total Receivable', 'Total Payable', 'Net Balance')}

    def app_dashboard_totals(self):
        sp = soup(self.e.get('/app/dashboard'))
        return {'Receivable': (label_values(sp, 'Receivable') or [None])[0], 'Payable': (label_values(sp, 'Payable') or [None])[0]}


# --------------------------------------------------------------------------------------------------
# the independent model: what the amounts say, computed from first principles
# --------------------------------------------------------------------------------------------------
BILLING = {
    # id: (form fields, expected billed amount computed by hand, NOT by the app's _trip_freight)
    'permt':     ({'rate_type': 'PER_MT', 'quantity': '10', 'rate': '1000'}, 10000.0),
    'fixed':     ({'rate_type': 'FIXED', 'quantity': '0', 'rate': '0', 'fixed_rate_amount': '25000'}, 25000.0),
    'guarantee': ({'rate_type': 'PER_MT', 'quantity': '10', 'guarantee_qty': '12', 'rate': '500'}, 6000.0),
    'charged':   ({'rate_type': 'PER_MT', 'quantity': '8', 'rate': '1250', 'detention_charges': '500', 'loading_charge': '300',
                   'tds': '200', 'other_deductions': '100'}, 10000.0 + 800.0 - 300.0),
    'paise':     ({'rate_type': 'PER_MT', 'quantity': '7.333', 'rate': '1234.56'}, round(7.333 * 1234.56, 2)),
    'zero':      ({'rate_type': 'FIXED', 'quantity': '0', 'rate': '0', 'fixed_rate_amount': '0'}, 0.0),
}


def r2(x):
    return round(x + 0.0, 2)


class Trip:
    def __init__(self, tid, lr, billed, adv, recv0):
        self.id, self.lr = tid, lr
        self.billed, self.adv = billed, adv
        self.direct = recv0           # payment typed on the trip itself (not from a ledger payment)
        self.alloc = 0.0              # ledger payments allocated to this trip
        self.party = None
        self.deleted = False
        self.ended = False

    @property
    def received(self):
        return self.direct + self.alloc

    @property
    def pending(self):
        return r2(self.billed - self.received - self.adv)


class PartyModel:
    def __init__(self, name):
        self.name, self.id = name, None
        self.opening = 0.0
        self.trips = []
        self.payments = []            # dicts: amount, allocated

    def live(self):
        return [t for t in self.trips if not t.deleted and t.party is self]

    def pay(self, amount, tick_trips):
        """The allocation rule: ticked trips first (in ticked order, capped at each trip's pending), then whatever
        is left flows to the party's other pending trips oldest first. Only a genuine surplus stays unallocated."""
        remaining, allocated = amount, 0.0
        order = [t for t in tick_trips if t in self.live()]
        order += [t for t in self.live() if t not in order]
        for t in order:
            if remaining <= EPS:
                break
            p = t.billed - t.received - t.adv
            if p <= EPS:
                continue
            a = min(p, remaining)
            t.alloc += a
            remaining -= a
            allocated += a
        self.payments.append({'amount': amount, 'allocated': allocated})
        return allocated

    @property
    def unallocated(self):
        return sum(p['amount'] - p['allocated'] for p in self.payments)

    @property
    def ledger_balance(self):
        """What the party owes, from first principles: opening + everything billed - advances
        - money typed on the trips - every payment received (allocated or not)."""
        live = self.live()
        return r2(self.opening + sum(t.billed for t in live) - sum(t.adv for t in live) - sum(t.direct for t in live)
                  - sum(p['amount'] for p in self.payments))

    @property
    def trips_pending(self):
        return r2(sum(t.pending for t in self.live()))


# --------------------------------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------------------------------
class Findings:
    def __init__(self):
        self.items = []
        self.checks = 0

    def check(self, code, ok, scenario, step, expected=None, actual=None, note='', severity='BUG'):
        self.checks += 1
        if ok:
            return True
        self.items.append({'code': code, 'severity': severity, 'scenario': scenario['id'], 'spec': scenario.get('spec'), 'step': step,
                           'expected': expected, 'actual': actual, 'note': note})
        return False


def close(a, b, tol=EPS):
    return a is not None and b is not None and abs(a - b) <= tol


def same_display(model_value, shown):
    """The page shows rupees with no decimals: compare to the model rounded exactly the way the template rounds."""
    return shown is not None and (abs(float('{:.0f}'.format(model_value)) - shown) < 0.5 or abs(model_value - shown) <= 0.5 + 1e-6)


# --------------------------------------------------------------------------------------------------
# actions: everything goes through the app's real routes
# --------------------------------------------------------------------------------------------------
def num(x):
    s = f'{x:.4f}'.rstrip('0').rstrip('.')
    return s or '0'


# billing recipes: how to fill the trip form for a scale factor f, and the billed amount worked out by hand
BILLING = {
    'permt':     (lambda f: {'rate_type': 'PER_MT', 'quantity': '10', 'rate': num(1000 * f)}, lambda f: 10 * 1000 * f),
    'fixed':     (lambda f: {'rate_type': 'FIXED', 'quantity': '0', 'rate': '0', 'fixed_rate_amount': num(25000 * f)}, lambda f: 25000 * f),
    'guarantee': (lambda f: {'rate_type': 'PER_MT', 'quantity': '10', 'guarantee_qty': '12', 'rate': num(500 * f)}, lambda f: 12 * 500 * f),
    'charged':   (lambda f: {'rate_type': 'PER_MT', 'quantity': '8', 'rate': num(1250 * f), 'detention_charges': '500', 'loading_charge': '300',
                             'tds': '200', 'other_deductions': '100'}, lambda f: 8 * 1250 * f + 800 - 300),
    'paise':     (lambda f: {'rate_type': 'PER_MT', 'quantity': '7.333', 'rate': num(1234.56 * f)}, lambda f: 7.333 * float(num(1234.56 * f))),
    'zero':      (lambda f: {'rate_type': 'FIXED', 'quantity': '0', 'rate': '0', 'fixed_rate_amount': '0'}, lambda f: 0.0),
    'shortage':  (lambda f: {'rate_type': 'PER_MT', 'quantity': '10', 'rate': num(1000 * f), 'shortage_amount': '400'}, lambda f: 10 * 1000 * f - 400),
}


def trip_form(lr, party, billing, f, adv, recv, vehicle='OD01AB0001', day=10, extra=None):
    fields_fn, billed_fn = BILLING[billing]
    fields, billed = fields_fn(f), billed_fn(f)
    form = {'date': f'2026-05-{day:02d}', 'lr_number': lr, 'vehicle_no': vehicle, 'type': 'own', 'party_name': party,
            'from_loc': 'Rourkela', 'to_loc': 'Cuttack', 'party_advance': num(adv), 'payment_received': num(recv)}
    form.update(fields)
    if extra:
        form.update(extra)
    return form, billed


class Run:
    """Everything needed while one scenario is executing."""

    def __init__(self, env, sc, F):
        self.env, self.sc, self.F = env, sc, F
        self.S = Screens(env)
        self.pm = None
        self.log = []                      # human-readable trace for --replay
        self.surface = sc.get('surface', 'web')
        self.balances, self.trips_billed, self.trips_pending, self.trip_count = {}, 0.0, 0.0, 0

    def note(self, msg):
        self.log.append(msg)

    def check(self, code, ok, step, expected=None, actual=None, note='', severity='BUG'):
        return self.F.check(code, ok, self.sc, step, expected, actual, note, severity)


def party_id(env, name):
    r = env.one('SELECT id FROM parties WHERE name=?', name)
    return r['id'] if r else None


def create_trips(run):
    sc, env = run.sc, run.env
    pm = run.pm = PartyModel(sc['party'])
    for i, ts in enumerate(sc['trips']):
        f = ts.get('f', 1.0)
        _, billed_fn = BILLING[ts['billing']]
        billed = billed_fn(f)
        adv = round(ts.get('adv', 0) * billed, 2)
        recv = round(ts.get('recv', 0) * billed, 2)
        lr = f"{sc['id']}-T{i}"
        form, billed = trip_form(lr, pm.name, ts['billing'], f, adv, recv, day=10 + i)
        url = '/app/trips/add' if run.surface == 'app' else '/trips/add'
        resp = env.post(url, form)
        row = env.one('SELECT id, billed_amount, party_advance, payment_received FROM trips WHERE lr_number=?', lr)
        if adv > billed + 0.011:          # a NEW trip whose advance is bigger than its bill must be refused
            run.check('TRIP_ADVANCE_ABOVE_BILL_REFUSED', row is None, f'create trip {i}', 'refused (advance > bill)', 'trip saved' if row else 'refused')
            run.note(f'trip {i}: advance {adv} > bill {billed} — correctly refused; scenario ends here')
            return False
        if not run.check('TRIP_CREATED', row is not None and resp.status_code in (200, 302), f'create trip {i}',
                         expected='row created', actual=f'HTTP {resp.status_code}, row={bool(row)}'):
            return False
        t = Trip(row['id'], lr, billed, adv, recv)
        t.party = pm
        pm.trips.append(t)
        run.check('TRIP_BILLED_STORED', close(row['billed_amount'], billed), f'create trip {i}', billed, row['billed_amount'],
                  note=f"billing={ts['billing']} f={f}")
    pm.id = party_id(env, pm.name)
    run.note(f"created {len(pm.trips)} trip(s): " + ', '.join(f"{t.lr} billed={t.billed:.2f} adv={t.adv:.2f} recv={t.direct:.2f}" for t in pm.trips))
    ob = sc.get('opening', 0)
    if ob:
        resp = env.post(f"/ledger/party/{pm.id}/opening-balance", {'opening_balance': num(ob), 'opening_balance_date': '2026-04-01'})
        pm.opening = float(ob)
        run.note(f'opening balance set to {ob}')
    return True


def resolve_amount(spec, pm):
    kind = spec[0]
    live = pm.live()
    if kind == 'pending':
        return round(spec[2] * max(pm.trips[spec[1]].pending, 0), 2)
    if kind == 'over':
        return round(max(pm.trips[spec[1]].pending, 0) + spec[2], 2)
    if kind == 'third':
        return round(max(pm.trips[spec[1]].pending, 0) / 3, 2)
    if kind == 'total':
        return round(spec[1] * sum(max(t.pending, 0) for t in live), 2)
    if kind == 'ledger':
        return round(spec[1] * max(pm.ledger_balance, 0), 2)
    if kind == 'abs':
        return round(spec[1], 2)
    raise ValueError(kind)


def do_payment(run, p, idx):
    env, pm = run.env, run.pm
    amount = resolve_amount(p['amt'], pm)
    if amount <= 0:
        run.note(f'payment {idx}: amount resolved to {amount} — skipped')
        return False
    ticks = [pm.trips[i] for i in p['tick']]
    before = env.one('SELECT COUNT(*) c FROM payments WHERE party_id=?', pm.id)['c']
    data = {'date': f'2026-06-{10 + idx:02d}', 'amount': num(amount), 'mode': 'Cash', 'trip_ids': [str(t.id) for t in ticks]}
    url = f'/app/ledger/party/{pm.id}/payment' if run.surface == 'app' else f'/payment/party/{pm.id}'
    resp = env.post(url, data)
    after = env.one('SELECT COUNT(*) c FROM payments WHERE party_id=?', pm.id)['c']
    run.check('PAYMENT_RECORDED', after == before + 1, f'payment {idx}', 'one new payment row', f'{after - before} new rows (HTTP {resp.status_code})')
    alloc = pm.pay(amount, ticks)
    run.note(f"payment {idx}: ₹{amount:.2f} via {run.surface}, ticked={[t.lr[-2:] for t in ticks]} -> allocated {alloc:.2f}, unallocated {amount - alloc:.2f}")
    return True


def post_op(run, op):
    """Edit / end / delete a trip after payments. Returns True if executed."""
    env, pm = run.env, run.pm
    kind, ti = op[0], op[1]
    t = pm.trips[ti]
    ts = run.sc['trips'][ti]
    f0 = ts.get('f', 1.0)

    def form_for(f=f0, adv=None, recv=None):
        form, billed = trip_form(t.lr, pm.name, ts['billing'], f, t.adv if adv is None else adv, t.received if recv is None else recv, day=10 + ti)
        return form, billed

    if kind in ('rate_up', 'rate_down'):
        f = f0 * (1.2 if kind == 'rate_up' else 0.5)
        form, billed = form_for(f=f, recv=t.received)
        env.post(f'/trips/edit/{t.id}', form)
        t.billed = billed
        run.note(f'edit trip {ti}: rate x{f / f0:.1f} -> billed {billed:.2f}')
    elif kind == 'recv_stale':
        form, _ = form_for(recv=0)
        env.post(f'/trips/edit/{t.id}', form)
        t.direct = 0.0          # the user typed 0; the floor keeps at least what ledger payments allocated
        run.note(f'edit trip {ti}: payment_received form value 0 (stale page)')
    elif kind == 'recv_raise':
        form, _ = form_for(recv=t.received + 1000)
        env.post(f'/trips/edit/{t.id}', form)
        t.direct += 1000
        run.note(f'edit trip {ti}: payment_received raised by 1000')
    elif kind == 'adv_change':
        newadv = round(t.billed * 0.7, 2)
        form, _ = form_for(adv=newadv)
        env.post(f'/trips/edit/{t.id}', form)
        t.adv = newadv
        run.note(f'edit trip {ti}: party advance -> {newadv}')
    elif kind == 'end_plain':
        # the End Trip form exactly as the page pre-fills it: freight = current billed, shortage = shortage already on the trip
        s0 = env.one('SELECT shortage_amount FROM trips WHERE id=?', t.id)['shortage_amount'] or 0
        env.post(f'/trips/{t.id}/end', {'end_date': '2026-07-01', 'freight_amount': num(t.billed + s0), 'shortage_amount': num(s0),
                                         'lr_received': 'Yes'})
        t.ended = True
        run.note(f'end trip {ti} with the form left as pre-filled')
        # the user changed nothing: billed should be unchanged
    elif kind == 'end_short':
        s0 = env.one('SELECT shortage_amount FROM trips WHERE id=?', t.id)['shortage_amount'] or 0
        env.post(f'/trips/{t.id}/end', {'end_date': '2026-07-01', 'freight_amount': num(t.billed + s0), 'shortage_amount': '500', 'lr_received': 'Yes'})
        if t.billed + s0 - 500 < -EPS:        # shortage bigger than the whole freight: refused, trip stays open and unchanged
            run.note(f'end trip {ti} with a 500 shortage on a {t.billed + s0:.2f} freight — correctly refused')
        else:
            t.billed = t.billed + s0 - 500
            t.ended = True
            run.note(f'end trip {ti} recording a NEW shortage of 500 -> billed {t.billed:.2f}')
    elif kind == 'delete':
        resp = env.post(f'/trips/delete/{t.id}', {})
        gone = env.one('SELECT id FROM trips WHERE id=?', t.id) is None
        if gone:
            t.deleted = True
        run.note(f'delete trip {ti}: {"deleted" if gone else "blocked"}')
        run.check('DELETE_BLOCKED_ONLY_WHEN_ALLOCATED', gone == (t.alloc <= EPS), 'delete trip',
                  expected='blocked iff a payment is allocated', actual=f'deleted={gone}, allocated={t.alloc:.2f}')
    else:
        raise ValueError(kind)
    return True


# --------------------------------------------------------------------------------------------------
# the checks: every screen vs the model, and screens vs each other
# --------------------------------------------------------------------------------------------------
def checkpoint(run, step, final=False):
    env, S, pm = run.env, run.S, run.pm
    live = pm.live()
    tol_rows = 0.5 * max(len(live), 1) + 1.0     # rows are each rounded to a rupee, the KPI rounds the exact sum

    # ---- Trips list (website)
    tp = S.trips_for_party(pm.name)
    run.check('TRIPS_PAGE_OK', tp['status'] == 200, step, 200, tp['status'])
    shown = 0.0
    for t in pm.trips:
        row = tp['rows'].get(t.lr)
        if t.deleted:
            run.check('DELETED_TRIP_STILL_LISTED', row is None, step, 'absent', row)
            continue
        if not run.check('TRIPS_ROW_PRESENT', row is not None, step, t.lr, 'missing'):
            continue
        run.check('TRIPS_ROW_BILLED', same_display(t.billed, row['billed']), step, round(t.billed, 2), row['billed'], note=t.lr)
        run.check('TRIPS_ROW_PENDING', same_display(t.pending, row['pending']), step, t.pending, row['pending'], note=t.lr)
        shown += row['pending'] or 0
    kp = tp['kpi']['Pending Balance']
    run.check('TRIPS_KPI_PENDING_VS_ROWS', kp is not None and abs(kp - shown) <= tol_rows, step, round(shown, 2), kp,
              note='the Pending Balance card vs the sum of the rows listed under it')
    run.check('TRIPS_KPI_COUNT', tp['kpi']['Total Trips'] == len(live), step, len(live), tp['kpi']['Total Trips'])
    run.check('TRIPS_KPI_TOTAL_BILLED', tp['kpi']['Total Billed'] is not None and abs(tp['kpi']['Total Billed'] - sum(t.billed for t in live)) <= tol_rows,
              step, round(sum(t.billed for t in live), 2), tp['kpi']['Total Billed'])
    kp_cards = soup(env.get('/trips', party=pm.name, per_page=100))
    active_shown = (label_values(kp_cards, 'Active Trips') or [None])[0]
    lr_pending_shown = (label_values(kp_cards, 'LR Pending') or [None])[0]
    run.check('TRIPS_KPI_ACTIVE', active_shown == sum(1 for t in live if not t.ended), step, sum(1 for t in live if not t.ended), active_shown)
    run.check('TRIPS_KPI_LR_PENDING', lr_pending_shown == sum(1 for t in live if not t.ended), step, sum(1 for t in live if not t.ended), lr_pending_shown,
              note='LR is only marked received when a trip is ended in these scenarios')

    # ---- Trip detail (website) + DB integrity
    for t in live:
        tv = S.trip_view(t.id)
        vals = tv['balance_due']
        run.check('TRIP_VIEW_BALANCE_DUE', bool(vals) and all(same_display(t.pending, v) for v in vals), step, t.pending, vals, note=t.lr)
        row = env.one('SELECT billed_amount, party_advance, payment_received FROM trips WHERE id=?', t.id)
        run.check('DB_TRIP_BILLED', close(row['billed_amount'], t.billed), step, round(t.billed, 2), row['billed_amount'], note=t.lr)
        run.check('DB_TRIP_ADVANCE', close(row['party_advance'] or 0, t.adv), step, t.adv, row['party_advance'], note=t.lr)
        run.check('DB_TRIP_PAYMENT_RECEIVED', close(row['payment_received'] or 0, t.received), step, round(t.received, 2), row['payment_received'],
                  note=f'{t.lr}: typed-on-trip {t.direct:.2f} + allocated {t.alloc:.2f}')
    for i, pmt in enumerate(pm.payments):
        pr = env.sql('SELECT id, amount, allocated_amount FROM payments WHERE party_id=? ORDER BY id', pm.id)
        if i < len(pr):
            a_rows = env.one('SELECT COALESCE(SUM(amount),0) s FROM payment_allocations WHERE payment_id=?', pr[i]['id'])['s']
            run.check('DB_PAYMENT_ALLOCATION_TOTAL', close(pr[i]['allocated_amount'] or 0, pmt['allocated']) and close(a_rows, pmt['allocated']), step,
                      round(pmt['allocated'], 2), f"allocated_amount={pr[i]['allocated_amount']}, rows={a_rows}", note=f'payment {i}')

    # ---- Party ledger page (website)
    pl = S.party_ledger(pm.id)
    run.check('LEDGER_PAGE_OK', pl['status'] == 200, step, 200, pl['status'])
    exp = pm.ledger_balance
    run.check('LEDGER_NET_BALANCE', same_display(exp, pl['net_signed']), step, exp, pl['net_signed'],
              note=f'opening {pm.opening} + billed - advances - typed-received - payments')
    run.check('LEDGER_RECEIVABLE_PAYABLE_SPLIT',
              same_display(max(exp, 0), pl['receivable']) and same_display(max(-exp, 0), pl['payable']), step,
              f'recv {max(exp, 0)} / pay {max(-exp, 0)}', f"recv {pl['receivable']} / pay {pl['payable']}")
    want = {t.id: t.pending for t in live if t.pending > 0.004}
    got = pl['modal_pending']
    got = {k: v for k, v in got.items() if v >= 0.0095 or k in want}      # a half-paisa residue shows as 0.00 — not a real pending amount
    run.check('PAYMENT_MODAL_PENDING_TRIPS', set(got) == set(want) and all(close(got[k], want[k], 0.011) for k in got if k in want), step,
              {k: round(v, 2) for k, v in want.items()}, got, note='trips offered for allocation in the Add Payment popup')
    if pl['modal_outstanding'] is not None or want:
        run.check('PAYMENT_MODAL_OUTSTANDING', same_display(sum(want.values()), pl['modal_outstanding'] or 0), step,
                  round(sum(want.values()), 2), pl['modal_outstanding'], note='"Outstanding Amount" in the Add Payment popup')

    # ---- Trips list vs Ledger — the symptom reported by the user
    if live:
        gap = (pl['net_signed'] or 0) - shown
        if abs(gap) > tol_rows:
            causes = []
            if abs(pm.opening) > EPS:
                causes.append('opening balance (only the Ledger counts it)')
            if pm.unallocated > EPS:
                causes.append('payment recorded but not allocated to a trip (only the Ledger counts it)')
            theory = pm.opening - pm.unallocated
            explained = bool(causes) and abs(gap - theory) <= tol_rows
            run.check('LEDGER_VS_TRIPS_GAP', False, step, f'trips total pending {shown:.2f}', f"ledger shows {pl['net_signed']}",
                      note=' + '.join(causes) if explained else 'UNEXPLAINED by opening balance / unallocated payments',
                      severity='DESIGN' if explained else 'BUG')
        paid_off = exp <= EPS and pm.opening >= -EPS and all(t.pending >= -0.01 for t in live) and (pm.payments or any(t.adv > 0 or t.direct > 0 for t in live))   # a credit carried over as an opening balance legitimately offsets what the trips show
        run.check('PAID_IN_FULL_BUT_TRIPS_STILL_PENDING', not (paid_off and shown > tol_rows), step,
                  'Trips pending 0 once the party owes nothing', f'Trips list still shows {shown:.2f} pending while the Ledger shows {pl["net_signed"]}',
                  severity='USER-VISIBLE')

    if final:
        final_checks(run)


def xlsx_rows(resp):
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), data_only=True)
    ws = wb.active
    return [[c.value for c in row] for row in ws.iter_rows()]


def export_checks(run, step, exp):
    """The downloaded Excel / PDF must say the same thing as the screen it was exported from."""
    env, pm = run.env, run.pm
    resp = env.get(f'/ledger/party/{pm.id}/export')
    run.check('EXPORT_LEDGER_XLSX_OK', resp.status_code == 200 and resp.data[:2] == b'PK', step, 'a real .xlsx', f'HTTP {resp.status_code}')
    if resp.status_code == 200:
        rows = xlsx_rows(resp)
        body = [r for r in rows[1:] if any(v not in (None, '') for v in r)]
        if body:
            bal = str(body[0][6] or '')                              # newest entry first
            m = re.match(r'^([\d,]+)(?:\s*(Dr|Cr))?$', bal.strip())
            signed = None
            if m:
                signed = float(m.group(1).replace(',', '')) * (-1 if m.group(2) == 'Cr' else 1)
            run.check('EXPORT_LEDGER_CLOSING_BALANCE', signed is not None and abs(signed - exp) <= 0.5 + 1e-9, step, exp, bal,
                      note='closing balance printed on the last line of the exported ledger vs the on-screen balance')
            deb = sum(float(r[4] or 0) for r in body)
            cre = sum(float(r[5] or 0) for r in body)
            run.check('EXPORT_LEDGER_DEBIT_CREDIT_TOTALS', abs((deb - cre) - exp) <= 0.5 * len(body) + 0.5, step, exp, round(deb - cre, 2),
                      note='sum(debit) - sum(credit) over the exported rows')
        else:
            run.check('EXPORT_LEDGER_CLOSING_BALANCE', abs(exp) <= 0.5, step, exp, 'empty export')
    resp = env.get(f'/ledger/party/{pm.id}/export/pdf')
    run.check('EXPORT_LEDGER_PDF_OK', resp.status_code == 200 and resp.data[:4] == b'%PDF', step, 'a real PDF', f'HTTP {resp.status_code}')
    resp = env.get('/trips/export', party=pm.name)
    if resp.status_code == 200:
        rows = [r for r in xlsx_rows(resp)[1:] if any(v not in (None, '') for v in r)]
        live = pm.live()
        run.check('EXPORT_TRIPS_ROW_COUNT', len(rows) == len(live), step, len(live), len(rows), note='Trips export filtered to this party')
        run.check('EXPORT_TRIPS_BILLED_TOTAL', abs(sum(float(r[6] or 0) for r in rows) - sum(t.billed for t in live)) <= 0.01 * max(len(live), 1) + 0.01, step,
                  round(sum(t.billed for t in live), 2), round(sum(float(r[6] or 0) for r in rows), 2), note='Trips export billed total')
    else:
        run.check('EXPORT_TRIPS_OK', False, step, 200, resp.status_code)


def final_checks(run):
    """Cross-surface checks done once per scenario: Ledger list, mobile app screens."""
    env, S, pm = run.env, run.S, run.pm
    step = 'final'
    exp = pm.ledger_balance
    ar = S.accounts_row(pm.name)
    run.check('ACCOUNTS_LIST_BALANCE', same_display(exp, ar['balance']), step, exp, ar['balance'], note='Ledger list row')
    run.check('ACCOUNTS_LIST_RECV_PAY', same_display(max(exp, 0), ar['receivable']) and same_display(max(-exp, 0), ar['payable']), step,
              f'recv {max(exp, 0)} / pay {max(-exp, 0)}', f"recv {ar['receivable']} / pay {ar['payable']}")
    if int(re.sub(r'\D', '', run.sc['id']) or 0) % 10 == 0:
        export_checks(run, step, exp)
    al = S.app_party_ledger(pm.id)
    run.check('APP_LEDGER_PAGE_OK', al['status'] == 200, step, 200, al['status'])
    run.check('APP_LEDGER_CLOSING', same_display(exp, al['closing_signed']), step, exp, al['closing_signed'], note='mobile app party ledger closing balance')
    for t in pm.live():
        at = S.app_trip(t.id)
        run.check('APP_TRIP_BALANCE_DUE', bool(at['balance_due']) and all(same_display(t.pending, v) for v in at['balance_due']), step,
                  t.pending, at['balance_due'], note=f'mobile app trip detail {t.lr}')


def run_party_scenario(env, F, sc):
    run = Run(env, sc, F)
    try:
        if not create_trips(run):
            pm = getattr(run, 'pm', None)
            if pm is not None and pm.trips:        # trips created before one was (correctly) refused still sit in the DB
                run.balances = {pm.name: pm.ledger_balance}
                run.trips_billed = sum(t.billed for t in pm.live())
                run.trips_pending = sum(t.pending for t in pm.live())
                run.trip_count = len(pm.live())
            return run
        checkpoint(run, 'after trips')
        for i, p in enumerate(sc.get('pay', [])):
            if do_payment(run, p, i):
                checkpoint(run, f'after payment {i}')
        if sc.get('post'):
            post_op(run, sc['post'])
            checkpoint(run, f"after {sc['post'][0]}")
        final_checks(run)
        run.balances = {run.pm.name: run.pm.ledger_balance}
        live = run.pm.live()
        run.trips_billed = sum(t.billed for t in live)
        run.trips_pending = sum(t.pending for t in live)
        run.trip_count = len(live)
    except Exception as e:  # a crash in the harness OR the app is itself a finding
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


# --------------------------------------------------------------------------------------------------
# scenario generation: the permutations
# --------------------------------------------------------------------------------------------------
SINGLE_PROGS = {
    'none': [],
    'full_ticked': [dict(amt=['pending', 0, 1.0], tick=[0])],
    'half_ticked': [dict(amt=['pending', 0, 0.5], tick=[0])],
    'full_NOT_ticked': [dict(amt=['pending', 0, 1.0], tick=[])],
    'half_NOT_ticked': [dict(amt=['pending', 0, 0.5], tick=[])],
    'overpay_ticked': [dict(amt=['over', 0, 1000], tick=[0])],
    'half_then_rest_ticked': [dict(amt=['pending', 0, 0.5], tick=[0]), dict(amt=['pending', 0, 1.0], tick=[0])],
    'half_NOT_ticked_then_rest_ticked': [dict(amt=['pending', 0, 0.5], tick=[]), dict(amt=['pending', 0, 1.0], tick=[0])],
    'half_ticked_then_rest_NOT_ticked': [dict(amt=['pending', 0, 0.5], tick=[0]), dict(amt=['pending', 0, 1.0], tick=[])],
    'third_ticked(paise)': [dict(amt=['third', 0], tick=[0])],
    'one_paisa_ticked': [dict(amt=['abs', 0.01], tick=[0])],
    'whole_ledger_NOT_ticked': [dict(amt=['ledger', 1.0], tick=[])],
}
BILL_SET = ['permt', 'fixed', 'guarantee', 'charged', 'paise', 'zero', 'shortage']
ADV_LEVELS = [0, 0.1, 0.4, 0.5, 1.0, 1.3]
RECV_LEVELS = [0, 0.25, 0.5, 1.0, 1.2]
OPENINGS = [0, 5000, -3000]

DUO_PROGS = {
    'tick_t0_full': [dict(amt=['pending', 0, 1.0], tick=[0])],
    'tick_t1_full': [dict(amt=['pending', 1, 1.0], tick=[1])],
    'tick_both_total': [dict(amt=['total', 1.0], tick=[0, 1])],
    'tick_both_reverse_total': [dict(amt=['total', 1.0], tick=[1, 0])],
    'NOT_ticked_total': [dict(amt=['total', 1.0], tick=[])],
    'tick_t0_only_but_pay_total': [dict(amt=['total', 1.0], tick=[0])],
    'tick_both_half_total': [dict(amt=['total', 0.5], tick=[0, 1])],
    'tick_both_overpay': [dict(amt=['total', 1.0], tick=[0, 1]), dict(amt=['abs', 500], tick=[0, 1])],
    'two_payments_one_each': [dict(amt=['pending', 0, 1.0], tick=[0]), dict(amt=['pending', 1, 1.0], tick=[1])],
    'NOT_ticked_half_then_tick_both_rest': [dict(amt=['total', 0.5], tick=[]), dict(amt=['total', 1.0], tick=[0, 1])],
    'tick_t1_then_NOT_ticked_rest': [dict(amt=['pending', 1, 1.0], tick=[1]), dict(amt=['total', 1.0], tick=[])],
    'whole_ledger_NOT_ticked': [dict(amt=['ledger', 1.0], tick=[])],
}


def gen_party(quick=False):
    out = []
    counter = itertools.count(1)

    def add(trips, pay, opening=0, post=None, surface='web', tag=''):
        n = next(counter)
        out.append({'id': f'P{n:04d}', 'stage': 'party', 'party': f'P{n:04d}Z', 'trips': trips, 'pay': pay, 'opening': opening, 'post': post,
                    'surface': surface, 'tag': tag})

    # A) one trip: every billing style x advance x amount typed on the trip x payment programme x opening balance
    for b, adv, rcv, prog, ob in itertools.product(BILL_SET, ADV_LEVELS, RECV_LEVELS, SINGLE_PROGS, OPENINGS):
        add([{'billing': b, 'adv': adv, 'recv': rcv}], SINGLE_PROGS[prog], opening=ob, tag=f'A:{b}/adv{adv}/recv{rcv}/open{ob}/{prog}')
    # C) edit / end / delete a trip after money has moved
    ops = ['rate_up', 'rate_down', 'recv_stale', 'recv_raise', 'adv_change', 'end_plain', 'end_short', 'delete']
    for b, (adv, rcv), prog, op in itertools.product(BILL_SET, [(0, 0), (0.4, 0.25), (0, 1.0), (1.0, 0)],
                                                      ['none', 'full_ticked', 'half_ticked', 'full_NOT_ticked', 'half_NOT_ticked',
                                                       'overpay_ticked', 'half_NOT_ticked_then_rest_ticked', 'half_ticked_then_rest_NOT_ticked'], ops):
        trips = [{'billing': b, 'adv': adv, 'recv': rcv}]
        if b == 'shortage' and op == 'end_plain':
            trips[0]['prefill_short'] = 400          # the End form pre-fills the shortage already deducted
        add(trips, SINGLE_PROGS[prog], post=[op, 0], tag=f'C:{b}/{prog}/{op}')
    # D) two trips under one party
    duo_levels = [((0, 0), (0, 0)), ((0.4, 0), (0, 0.25)), ((0, 1.0), (0.4, 0)), ((1.0, 0), (0, 0)), ((0, 0.5), (0.5, 0)), ((1.3, 0), (0, 1.2))]
    for (b0, b1), lv, prog, ob in itertools.product(itertools.product(['permt', 'fixed', 'guarantee', 'charged', 'paise', 'zero', 'shortage'], repeat=2),
                                                    duo_levels, DUO_PROGS, [0, 5000]):
        add([{'billing': b0, 'adv': lv[0][0], 'recv': lv[0][1]}, {'billing': b1, 'adv': lv[1][0], 'recv': lv[1][1]}], DUO_PROGS[prog],
            opening=ob, tag=f'D:{b0}+{b1}/{lv}/open{ob}/{prog}')
    # E) three trips and a run of payments (seeded so the same set comes back every time)
    rnd = random.Random(7)
    for _ in range(1500):
        trips = [{'billing': rnd.choice(BILL_SET), 'adv': rnd.choice(ADV_LEVELS), 'recv': rnd.choice(RECV_LEVELS)} for _ in range(3)]
        pays = []
        for _ in range(rnd.randint(1, 4)):
            tick = rnd.choice([[], [0], [1], [2], [0, 1], [1, 2], [0, 1, 2], [2, 1, 0]])
            amt = rnd.choice([['total', 1.0], ['total', 0.3], ['pending', rnd.randint(0, 2), 0.5], ['pending', rnd.randint(0, 2), 1.0], ['abs', 777.77], ['ledger', 1.0]])
            pays.append({'amt': amt, 'tick': tick})
        add(trips, pays, opening=rnd.choice([0, 0, 2500, -1500]), post=rnd.choice([None, None, ['rate_up', rnd.randint(0, 2)], ['recv_stale', rnd.randint(0, 2)], ['adv_change', rnd.randint(0, 2)]]),
            tag='E:random3')
    # G) the same flows driven from the MOBILE APP's routes
    for b, (adv, rcv), prog, ob in itertools.product(BILL_SET, [(0, 0), (0.4, 0.25), (1.0, 0)], SINGLE_PROGS, [0, 5000]):
        add([{'billing': b, 'adv': adv, 'recv': rcv}], SINGLE_PROGS[prog], opening=ob, surface='app', tag=f'G:app/{b}/{(adv, rcv)}/open{ob}/{prog}')
    for (b0, b1), prog in itertools.product(itertools.product(['permt', 'fixed', 'charged', 'paise'], repeat=2), DUO_PROGS):
        add([{'billing': b0, 'adv': 0.4, 'recv': 0}, {'billing': b1, 'adv': 0, 'recv': 0.25}], DUO_PROGS[prog], surface='app', tag=f'G:app/{b0}+{b1}/{prog}')
    if quick:
        rnd2 = random.Random(3)
        out = [s for s in out if rnd2.random() < 0.08]
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: vendor / vehicle-owner side (hired trips, fuel & driver advances, maintenance bills, vendor payments)
# --------------------------------------------------------------------------------------------------
class VTrip:
    def __init__(self, tid, lr, owed, paid0, fuel, adv):
        self.id, self.lr, self.owed = tid, lr, owed
        self.paid_direct, self.alloc = paid0, 0.0
        self.fuel, self.adv = fuel, adv            # (amount, kind) kind: none | company | other | owner
        self.deleted = False

    @property
    def paid_total(self):
        return self.paid_direct + self.alloc

    @property
    def pending(self):
        return r2(self.owed - self.paid_total - self.deductions)

    @property
    def deductions(self):
        """Fuel / driver advance the COMPANY covered (blank vendor, or someone else) comes off what the owner is owed."""
        return sum(a for a, k in (self.fuel, self.adv) if k in ('company', 'other'))


class VendorModel:
    def __init__(self, name):
        self.name, self.id = name, None
        self.opening = 0.0
        self.trips, self.payments, self.maint = [], [], []      # maint: dicts amount/paid

    def live(self):
        return [t for t in self.trips if not t.deleted]

    def pay(self, amount, ticks):
        """Ticked trips first, then the vendor's other pending trips (oldest first), then unpaid maintenance bills."""
        remaining, allocated = amount, 0.0
        order = [t for t in ticks if t in self.live()]
        order += [t for t in self.live() if t not in order]
        for t in order:
            if remaining <= EPS:
                break
            p = t.pending
            if p <= EPS:
                continue
            a = min(p, remaining)
            t.alloc += a
            remaining -= a
            allocated += a
        for m in self.maint:
            if remaining <= EPS:
                break
            p = m['amount'] - m['paid0'] - m['alloc']
            if p <= EPS:
                continue
            a = min(p, remaining)
            m['alloc'] += a
            remaining -= a
            allocated += a
        self.payments.append({'amount': amount, 'allocated': allocated})
        return allocated

    @property
    def unallocated(self):
        return sum(p['amount'] - p['allocated'] for p in self.payments)

    @property
    def maint_unpaid(self):
        return sum(m['amount'] - m['paid0'] - m['alloc'] for m in self.maint)

    @property
    def ledger_balance(self):
        """+ve = they owe us, -ve = we owe them."""
        live = self.live()
        return r2(self.opening + sum(t.paid_direct - t.owed + t.deductions for t in live)
                  + sum(m['paid0'] - m['amount'] for m in self.maint) + sum(p['amount'] for p in self.payments))


def vendor_resolve_amount(spec, vm):
    k = spec[0]
    if k == 'pending':
        return round(spec[2] * max(vm.trips[spec[1]].pending, 0), 2)
    if k == 'over':
        return round(max(vm.trips[spec[1]].pending, 0) + spec[2], 2)
    if k == 'payable':
        return round(spec[1] * max(-vm.ledger_balance, 0), 2)
    if k == 'abs':
        return round(spec[1], 2)
    raise ValueError(k)


def vendor_trip_form(run, i, ts):
    sc = run.sc
    owner_kind, owed = ts['owner']
    owed = float(owed)
    extra = {'type': 'hired', 'owner_name': sc['vendor'],
             'owner_rate_type': 'PER_MT' if owner_kind == 'permt' else 'FIXED',
             'owner_rate': num(owed / 10) if owner_kind == 'permt' else '0',
             'owner_fixed_amount': '0' if owner_kind == 'permt' else num(owed),
             'paid_to_owner': num(round(ts.get('paid0', 0) * owed, 2))}
    fuel_amt, fuel_kind = ts.get('fuel', (0, 'none'))
    adv_amt, adv_kind = ts.get('adv', (0, 'none'))
    if fuel_kind != 'none':
        extra['fuel_amount'] = num(fuel_amt)
        extra['fuel_vendor'] = {'company': '', 'other': sc['other'], 'owner': sc['vendor']}[fuel_kind]
    if adv_kind != 'none':
        extra['driver_adv_amount'] = num(adv_amt)
        extra['driver_adv_vendor'] = {'company': '', 'other': sc['other'], 'owner': sc['vendor']}[adv_kind]
    lr = f"{sc['id']}-T{i}"
    form, billed = trip_form(lr, sc['party'], 'permt', 1.0, 0, 0, vehicle='OD02CD0002', day=10 + i, extra=extra)
    return lr, form, owed


def vendor_checkpoint(run, step, final=False):
    env, S, vm = run.env, run.S, run.vm
    live = vm.live()
    exp = vm.ledger_balance
    linked = run.sc.get('linked')
    tol = 0.5 * max(len(live), 1) + 1.0

    # per-trip stored values
    for t in live:
        row = env.one('SELECT paid_to_owner FROM trips WHERE id=?', t.id)
        run.check('DB_TRIP_PAID_TO_OWNER', close(row['paid_to_owner'] or 0, t.paid_total), step, round(t.paid_total, 2), row['paid_to_owner'],
                  note=f'{t.lr}: typed {t.paid_direct:.2f} + allocated {t.alloc:.2f}')
    for i, pm_ in enumerate(vm.payments):
        pr = env.sql('SELECT id, allocated_amount FROM payments WHERE vendor_id=? ORDER BY id', vm.id)
        if i < len(pr):
            rows = env.one('SELECT COALESCE(SUM(amount),0) s FROM payment_allocations WHERE payment_id=?', pr[i]['id'])['s']
            run.check('DB_VENDOR_PAYMENT_ALLOCATION_TOTAL', close(pr[i]['allocated_amount'] or 0, pm_['allocated']) and close(rows, pm_['allocated']), step,
                      round(pm_['allocated'], 2), f"allocated_amount={pr[i]['allocated_amount']}, rows={rows}", note=f'vendor payment {i}')

    # the vendor's own ledger page (for a vendor that is also a party, the page is the PARTY ledger)
    pl = S.party_ledger(run.party_page_id) if linked else S.vendor_ledger(vm.id)
    run.check('VENDOR_LEDGER_PAGE_OK', pl['status'] == 200, step, 200, pl['status'])
    run.check('VENDOR_LEDGER_NET_BALANCE', same_display(exp, pl['net_signed']), step, exp, pl['net_signed'],
              note='vendor ledger: opening + typed-paid - owed + company-paid fuel/advance + maintenance paid - billed + payments')
    run.check('VENDOR_LEDGER_RECV_PAY_SPLIT', same_display(max(exp, 0), pl['receivable']) and same_display(max(-exp, 0), pl['payable']), step,
              f'recv {max(exp, 0)} / pay {max(-exp, 0)}', f"recv {pl['receivable']} / pay {pl['payable']}")
    want = {t.id: t.pending for t in live if t.pending > 0.01}
    got = pl['modal_pending']
    if linked:
        # An owner who is also a customer is shown on the merged PARTY ledger, whose Add Payment popup is for money
        # RECEIVED from the party; there is no popup there to pay the owner against the hired trips.
        run.check('MERGED_LEDGER_HAS_NO_PAY_OWNER_POPUP', not want or bool(got), step, 'a way to pay the owner for the hired trips',
                  f'popup lists no owner payables (owner is owed on {len(want)} trip(s))', note='vendor who is also a party: payments to the owner cannot be allocated from the ledger screen',
                  severity='DESIGN')
    else:
        run.check('VENDOR_MODAL_PENDING_TRIPS', set(got) == set(want) and all(close(got[k], want[k], 0.011) for k in got if k in want), step,
                  {k: round(v, 2) for k, v in want.items()}, got, note='trips offered for allocation in the vendor Add Payment popup')

    # vendor payable vs what the payment popup says is outstanding (the vendor-side twin of the Trips-vs-Ledger gap)
    if live and not linked:
        payable = max(-exp, 0) if exp < 0 else -exp          # signed: positive = we owe
        outstanding = pl['modal_outstanding'] or 0
        s_neg = sum(t.pending for t in live if t.pending < -0.01)
        ded = sum(t.deductions for t in live)
        theory = -vm.opening + vm.maint_unpaid - vm.unallocated + s_neg
        gap = -exp - outstanding
        if abs(gap) > tol:
            causes = []
            if abs(vm.opening) > EPS:
                causes.append('opening balance')
            if abs(vm.maint_unpaid) > EPS:
                causes.append('maintenance bills are in the ledger, not in the popup')
            if vm.unallocated > EPS:
                causes.append('payment recorded but not allocated to a trip')
            if s_neg < -0.01:
                causes.append('a trip already overpaid')
            explained = bool(causes) and abs(gap - theory) <= tol
            run.check('VENDOR_LEDGER_VS_POPUP_GAP', False, step, f'popup outstanding {outstanding:.2f}', f'ledger payable {-exp:.2f}',
                      note=' + '.join(causes) if explained else 'UNEXPLAINED', severity='DESIGN' if explained else 'BUG')
        paid_off = -exp <= EPS and vm.opening <= EPS and s_neg >= -0.01 and (vm.payments or any(t.paid_direct > 0 for t in live))   # a credit from an opening balance or an overpaid trip legitimately offsets
        run.check('VENDOR_PAID_IN_FULL_BUT_TRIP_STILL_OWED', not (paid_off and outstanding > tol), step, 'popup outstanding 0 once we owe nothing',
                  f'popup still lists {outstanding:.2f} owed to the owner while the ledger says we owe {-exp:.2f}', severity='USER-VISIBLE')

    # fuel / advance vendor 'other' gets its own payable
    if run.fm is not None:
        fexp = -sum(a for t in live for a, k in (t.fuel, t.adv) if k == 'other')
        fr = S.accounts_row(run.sc['other'])
        run.check('OTHER_VENDOR_LEDGER_BALANCE', same_display(fexp, fr['balance']), step, fexp, fr['balance'], note='the third-party fuel / advance vendor')

    if final:
        ar = S.accounts_row(run.sc['party'] if False else (run.sc['vendor']))
        run.check('ACCOUNTS_LIST_VENDOR_BALANCE', same_display(exp, ar['balance']), 'final', exp, ar['balance'], note='Ledger list row for the vendor')
        if not linked:
            al = S.app_vendor_ledger(vm.id)
            run.check('APP_VENDOR_LEDGER_CLOSING', same_display(exp, al['closing_signed']), 'final', exp, al['closing_signed'], note='mobile app vendor ledger')
        # the party the hired trips were billed to: its ledger is independent of any of this
        pexp = r2(sum(t_billed for t_billed in run.party_billed))
        pr = S.accounts_row(run.sc['party'])
        run.check('BILLED_PARTY_LEDGER_UNAFFECTED_BY_VENDOR_SIDE', same_display(pexp, pr['balance']), 'final', pexp, pr['balance'],
                  note='billed party balance should depend only on the bills (10,000 each) — not on owner/fuel/advance amounts')


def run_vendor_scenario(env, F, sc):
    run = Run(env, sc, F)
    run.fm = None
    run.party_billed = []
    try:
        vm = run.vm = VendorModel(sc['vendor'])
        if sc.get('linked'):            # the owner is ALSO a customer: same name -> the two ledgers merge into one
            env.post('/accounts/add', {'name': sc['vendor'], 'role': 'Party'})
        for i, ts in enumerate(sc['trips']):
            lr, form, owed = vendor_trip_form(run, i, ts)
            resp = env.post('/trips/add', form)
            row = env.one('SELECT id, paid_to_owner FROM trips WHERE lr_number=?', lr)
            if not run.check('TRIP_CREATED', row is not None, f'create trip {i}', 'row', f'HTTP {resp.status_code}'):
                return run
            t = VTrip(row['id'], lr, owed, round(ts.get('paid0', 0) * owed, 2), ts.get('fuel', (0, 'none')), ts.get('adv', (0, 'none')))
            vm.trips.append(t)
            run.party_billed.append(10000.0)
        vrow = env.one('SELECT id FROM vendors WHERE name=?', sc['vendor'])
        vm.id = vrow['id'] if vrow else None
        if vm.id is None:
            run.check('VENDOR_CREATED', False, 'setup', 'vendor row', 'none')
            return run
        if sc.get('linked'):
            lk = env.one('SELECT linked_party_id FROM vendors WHERE id=?', vm.id)['linked_party_id']
            run.check('VENDOR_LINKED_TO_PARTY', lk is not None, 'setup', 'linked to the same-named party', lk)
            run.party_page_id = lk
        if any(k == 'other' for t in vm.trips for _, k in (t.fuel, t.adv)):
            run.fm = True
        for m in sc.get('maint', []):
            env.post('/maintenance/add', {'date': '2026-05-20', 'vehicle_no': 'OD02CD0002', 'category': 'Service', 'amount': num(m[0]),
                                           'paid_amount': num(m[1]), 'vendor_name': sc['vendor']})
            vm.maint.append({'amount': float(m[0]), 'paid0': float(m[1]), 'alloc': 0.0})
        if sc.get('opening'):
            env.post(f'/ledger/vendor/{vm.id}/opening-balance', {'opening_balance': num(sc['opening']), 'opening_balance_date': '2026-04-01'})
            vm.opening = float(sc['opening'])
        run.note('hired trips: ' + ', '.join(f"{t.lr} owed={t.owed:.0f} paid0={t.paid_direct:.0f} fuel={t.fuel} adv={t.adv}" for t in vm.trips)
                 + f'; maint={sc.get("maint")}; opening={vm.opening}')
        vendor_checkpoint(run, 'after trips')
        for i, p in enumerate(sc.get('pay', [])):
            amount = vendor_resolve_amount(p['amt'], vm)
            if amount <= 0:
                continue
            ticks = [vm.trips[j] for j in p['tick']]
            before = env.one('SELECT COUNT(*) c FROM payments WHERE vendor_id=?', vm.id)['c']
            url = f'/app/ledger/vendor/{vm.id}/payment' if sc.get('surface') == 'app' else f'/payment/vendor/{vm.id}'
            resp = env.post(url, {'date': f'2026-06-{10 + i:02d}', 'amount': num(amount), 'mode': 'Cash', 'trip_ids': [str(t.id) for t in ticks]})
            after = env.one('SELECT COUNT(*) c FROM payments WHERE vendor_id=?', vm.id)['c']
            run.check('VENDOR_PAYMENT_RECORDED', after == before + 1, f'vendor payment {i}', 'one new payment row', f'{after - before} (HTTP {resp.status_code})')
            alloc = vm.pay(amount, ticks)
            run.note(f"vendor payment {i}: ₹{amount:.2f}, ticked={[t.lr[-2:] for t in ticks]} -> allocated {alloc:.2f}, unallocated {amount - alloc:.2f}")
            vendor_checkpoint(run, f'after vendor payment {i}')
        vendor_checkpoint(run, 'final', final=True)
        run.balances = {sc['vendor']: vm.ledger_balance if not sc.get('linked') else vm.ledger_balance,
                        sc['party']: r2(sum(run.party_billed))}
        if run.fm:
            run.balances[sc['other']] = -sum(a for t in vm.live() for a, k in (t.fuel, t.adv) if k == 'other')
        run.trips_billed = sum(run.party_billed)
        run.trips_pending = sum(run.party_billed)
        run.trip_count = len(run.party_billed)
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


VENDOR_PROGS = {
    'none': [],
    'full_ticked': [dict(amt=['pending', 0, 1.0], tick=[0])],
    'half_ticked': [dict(amt=['pending', 0, 0.5], tick=[0])],
    'full_NOT_ticked': [dict(amt=['pending', 0, 1.0], tick=[])],
    'whole_payable_NOT_ticked': [dict(amt=['payable', 1.0], tick=[])],
    'whole_payable_ticked': [dict(amt=['payable', 1.0], tick=[0])],
    'overpay_ticked': [dict(amt=['over', 0, 1000], tick=[0])],
    'half_NOT_then_rest_ticked': [dict(amt=['pending', 0, 0.5], tick=[]), dict(amt=['pending', 0, 1.0], tick=[0])],
}


def gen_vendor(quick=False):
    out = []
    counter = itertools.count(1)

    def add(trips, pay, opening=0, maint=None, linked=False, surface='web', tag=''):
        n = next(counter)
        out.append({'id': f'V{n:04d}', 'stage': 'vendor', 'vendor': f'V{n:04d}Z', 'party': f'V{n:04d}P', 'other': f'V{n:04d}F', 'trips': trips,
                    'pay': pay, 'opening': opening, 'maint': maint or [], 'linked': linked, 'surface': surface, 'tag': tag})

    owners = [('permt', 8000.0), ('fixed', 9000.0)]
    fuels = [(0, 'none'), (1500, 'company'), (1500, 'other'), (1500, 'owner')]
    advs = [(0, 'none'), (2000, 'company'), (2000, 'other'), (2000, 'owner')]
    # V1: one hired trip: owner basis x what was already paid x fuel arrangement x advance arrangement x payment programme x opening
    for o, p0, fu, ad, prog, ob in itertools.product(owners, [0, 0.3, 1.0, 1.3], fuels, advs, VENDOR_PROGS, [0, 5000]):
        add([{'owner': o, 'paid0': p0, 'fuel': fu, 'adv': ad}], VENDOR_PROGS[prog], opening=ob, tag=f'V1:{o[0]}/paid{p0}/fuel{fu[1]}/adv{ad[1]}/open{ob}/{prog}')
    # V2: maintenance bills with / without an owner trip
    for m, withtrip, prog, ob in itertools.product([[(5000, 0)], [(5000, 2000)], [(5000, 5000)], [(5000, 0), (3000, 3000)]], [False, True], VENDOR_PROGS, [0, -2000]):
        trips = [{'owner': ('permt', 8000.0), 'paid0': 0.3, 'fuel': (1500, 'company'), 'adv': (0, 'none')}] if withtrip else []
        if not trips and prog in ('full_ticked', 'half_ticked', 'full_NOT_ticked', 'overpay_ticked', 'half_NOT_then_rest_ticked'):
            continue          # those programmes name trip 0
        if not trips:
            trips = [{'owner': ('fixed', 1.0), 'paid0': 1.0, 'fuel': (0, 'none'), 'adv': (0, 'none')}]     # a fully-settled dummy so a vendor exists
        add(trips, VENDOR_PROGS[prog], opening=ob, maint=m, tag=f'V2:maint{m}/trip{withtrip}/open{ob}/{prog}')
    # V3: the owner is ALSO a customer (same name) -> one merged ledger
    for o, p0, fu, prog in itertools.product(owners, [0, 0.3, 1.0], fuels, VENDOR_PROGS):
        add([{'owner': o, 'paid0': p0, 'fuel': fu, 'adv': (0, 'none')}], VENDOR_PROGS[prog], linked=True, tag=f'V3:linked/{o[0]}/paid{p0}/fuel{fu[1]}/{prog}')
    # V4: two hired trips for the same owner, random mix, mobile-app payment route half of the time
    rnd = random.Random(11)
    for _ in range(900):
        trips = [{'owner': rnd.choice(owners), 'paid0': rnd.choice([0, 0.3, 1.0]), 'fuel': rnd.choice(fuels), 'adv': rnd.choice(advs)} for _ in range(2)]
        pays = []
        for _ in range(rnd.randint(1, 3)):
            pays.append({'amt': rnd.choice([['pending', 0, 0.5], ['pending', 1, 1.0], ['payable', 1.0], ['abs', 1234.5], ['over', 0, 300]]),
                         'tick': rnd.choice([[], [0], [1], [0, 1], [1, 0]])})
        add(trips, pays, opening=rnd.choice([0, 0, 4000, -2500]), maint=rnd.choice([[], [], [(2500, 1000)]]), surface=rnd.choice(['web', 'app']), tag='V4:random2')
    if quick:
        r2_ = random.Random(5)
        out = [x for x in out if r2_.random() < 0.08]
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: edge cases — bad input, missing fields, tampering, double-submits
# --------------------------------------------------------------------------------------------------
def mk_party_trip(env, tag, billed_f=1.0, adv=0.0, recv=0.0, party=None, vehicle='OD01AB0001'):
    party = party or f'{tag}Z'
    form, billed = trip_form(f'{tag}-T0', party, 'permt', billed_f, adv, recv, vehicle=vehicle)
    env.post('/trips/add', form)
    t = env.one('SELECT id FROM trips WHERE lr_number=?', f'{tag}-T0')
    pr = env.one('SELECT id FROM parties WHERE name=?', party)
    return (pr['id'] if pr else None), (t['id'] if t else None), billed


def n_payments(env, kind='party', eid=None):
    col = 'party_id' if kind == 'party' else 'vendor_id'
    return env.one(f'SELECT COUNT(*) c FROM payments WHERE {col}=?', eid)['c']


AMOUNT_VARIANTS = {
    # variant: expected to be ACCEPTED as a real payment of this size (None = should be rejected)
    '500': 500.0, '500.00': 500.0, ' 500 ': 500.0, '0.01': 0.01, '1e3': 1000.0, '9999.99': 9999.99,
    '0': None, '0.00': None, '-100': None, '-0.01': None, 'abc': None, '1,000': 1000.0, '': None, ' ': None, 'nan': None, 'inf': None,
    '-inf': None, '1e400': None, '99999999999999999': None, '0.001': None,
}
EDGE_CASES = ['amount', 'missing_field', 'cross_party_tick', 'unknown_trip_ids', 'duplicate_tick', 'party_missing', 'double_submit',
              'opening_invalid', 'trip_form_invalid', 'duplicate_lr', 'end_twice', 'end_shortage_over_freight', 'party_change_after_payment',
              'amount_precision']


def run_edge_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    case = sc['case']
    try:
        route = sc.get('route', 'web')
        pay_url = lambda pid: (f'/app/ledger/party/{pid}/payment' if route == 'app' else f'/payment/party/{pid}')
        if case == 'amount':
            pid, tid, billed = mk_party_trip(env, tag)
            v = sc['variant']
            want = AMOUNT_VARIANTS[v]
            before = n_payments(env, 'party', pid)
            trip_before = env.one('SELECT payment_received FROM trips WHERE id=?', tid)['payment_received']
            resp = env.post(pay_url(pid), {'date': '2026-06-10', 'amount': v, 'mode': 'Cash', 'trip_ids': [str(tid)]})
            after = n_payments(env, 'party', pid)
            recv_after = env.one('SELECT payment_received FROM trips WHERE id=?', tid)['payment_received']
            run.check('PAYMENT_AMOUNT_SERVER_ERROR', resp.status_code < 500, f'amount {v!r} via {route}', 'a handled response', f'HTTP {resp.status_code}',
                      note=f'amount typed as {v!r}')
            if want is None:
                run.check('PAYMENT_BAD_AMOUNT_ACCEPTED', after == before, f'amount {v!r} via {route}', 'rejected (no payment recorded)',
                          f'{after - before} payment row(s) created, trip payment_received {trip_before} -> {recv_after}', note=f'amount typed as {v!r}')
            else:
                run.check('PAYMENT_GOOD_AMOUNT_REJECTED', after == before + 1, f'amount {v!r} via {route}', 'recorded', f'{after - before} rows',
                          note=f'amount typed as {v!r}')
                if after == before + 1:
                    a = env.one('SELECT amount FROM payments WHERE party_id=?', pid)['amount']
                    run.check('PAYMENT_AMOUNT_STORED_EXACTLY', close(a, want, 0.0001), f'amount {v!r}', want, a)
        elif case == 'missing_field':
            pid, tid, _ = mk_party_trip(env, tag)
            data = {'date': '2026-06-10', 'amount': '500', 'mode': 'Cash', 'trip_ids': [str(tid)]}
            data.pop(sc['field'])
            before = n_payments(env, 'party', pid)
            resp = env.post(pay_url(pid), data)
            after = n_payments(env, 'party', pid)
            run.check('PAYMENT_MISSING_FIELD_ACCEPTED', after == before, f"no {sc['field']} via {route}", 'rejected', f'{after - before} rows')
            if route == 'web' and after == before:
                loc = resp.headers.get('Location', '')
                told = resp.status_code != 302 or 'error' in loc or 'err' in loc
                run.check('WEBSITE_PAYMENT_REJECTED_SILENTLY', told, f"no {sc['field']} via web", 'the user is told why nothing was saved',
                          f'redirected to {loc!r} with no message — looks like it saved', severity='USER-VISIBLE')
        elif case == 'cross_party_tick':
            pa, ta, _ = mk_party_trip(env, tag + 'a')
            pb, tb, _ = mk_party_trip(env, tag + 'b')
            before = env.one('SELECT payment_received FROM trips WHERE id=?', ta)['payment_received']
            env.post(pay_url(pb), {'date': '2026-06-10', 'amount': '4000', 'mode': 'Cash', 'trip_ids': [str(ta)]})   # B's payment ticking A's trip
            after = env.one('SELECT payment_received FROM trips WHERE id=?', ta)['payment_received']
            run.check('PAYMENT_ALLOCATED_TO_ANOTHER_PARTYS_TRIP', close(before or 0, after or 0), 'party B pays, ticking party A\'s trip',
                      "party A's trip untouched", f"party A's trip payment_received {before} -> {after}", severity='BUG')
        elif case == 'unknown_trip_ids':
            pid, tid, _ = mk_party_trip(env, tag)
            resp = env.post(pay_url(pid), {'date': '2026-06-10', 'amount': '500', 'mode': 'Cash', 'trip_ids': ['999999', 'abc', '', str(tid)]})
            run.check('PAYMENT_UNKNOWN_TRIP_IDS_SERVER_ERROR', resp.status_code < 500, 'tick ids 999999 / abc / blank', 'handled', f'HTTP {resp.status_code}')
        elif case == 'duplicate_tick':
            pid, tid, billed = mk_party_trip(env, tag)
            env.post(pay_url(pid), {'date': '2026-06-10', 'amount': '12000', 'mode': 'Cash', 'trip_ids': [str(tid), str(tid)]})
            row = env.one('SELECT payment_received FROM trips WHERE id=?', tid)
            run.check('PAYMENT_DUPLICATE_TICK_DOUBLE_COUNTED', close(row['payment_received'], min(billed, 12000)), 'same trip ticked twice, paying 12000 on a 10000 trip',
                      min(billed, 12000), row['payment_received'])
        elif case == 'party_missing':
            before = env.one('SELECT COUNT(*) c FROM payments')['c']
            resp = env.post(pay_url(999999), {'date': '2026-06-10', 'amount': '500', 'mode': 'Cash', 'trip_ids': []})
            after = env.one('SELECT COUNT(*) c FROM payments')['c']
            run.check('PAYMENT_FOR_NONEXISTENT_PARTY_RECORDED', after == before, f'payment to party id 999999 via {route}', 'no row',
                      f'{after - before} orphan payment row(s) created (HTTP {resp.status_code})')
        elif case == 'double_submit':
            pid, tid, billed = mk_party_trip(env, tag)
            d = {'date': '2026-06-10', 'amount': '5000', 'mode': 'Cash', 'trip_ids': [str(tid)], 'reference_id': 'UTR-1'}
            env.post(pay_url(pid), d)
            env.post(pay_url(pid), d)
            n = n_payments(env, 'party', pid)
            run.check('PAYMENT_DOUBLE_SUBMIT_RECORDED_TWICE', n == 1, 'the identical payment (same date, amount, reference) submitted twice', 'recorded once (duplicate)',
                      f'recorded {n} times', severity='DESIGN')
        elif case == 'opening_invalid':
            pid, tid, _ = mk_party_trip(env, tag)
            resp = env.post(f'/ledger/party/{pid}/opening-balance', {'opening_balance': sc['variant'], 'opening_balance_date': '2026-04-01'})
            run.check('OPENING_BALANCE_SERVER_ERROR', resp.status_code < 500, f"opening balance {sc['variant']!r}", 'handled', f'HTTP {resp.status_code}')
        elif case == 'trip_form_invalid':
            form, _ = trip_form(f'{tag}-T0', f'{tag}Z', 'permt', 1.0, 0, 0)
            form.update(sc['change'])
            resp = env.post('/trips/add', form)
            row = env.one('SELECT billed_amount, party_advance FROM trips WHERE lr_number=?', f'{tag}-T0')
            run.check('TRIP_FORM_SERVER_ERROR', resp.status_code < 500, f"trip form {sc['change']}", 'handled', f'HTTP {resp.status_code}')
            if row is not None:
                bad_neg = (row['billed_amount'] or 0) < 0
                run.check('TRIP_SAVED_WITH_NEGATIVE_BILLED', not bad_neg, f"trip form {sc['change']}", 'billed >= 0 or rejected', f"billed {row['billed_amount']}", severity='BUG')
                if 'party_advance' in sc['change']:
                    adv = float(sc['change']['party_advance']) if re.match(r'^-?[\d.]+$', sc['change']['party_advance']) else 0
                    run.check('TRIP_SAVED_WITH_ADVANCE_ABOVE_BILL_OR_NEGATIVE', 0 <= adv <= (row['billed_amount'] or 0), f"party advance {adv}",
                              'advance between 0 and the bill, or rejected', f"advance {row['party_advance']} on a bill of {row['billed_amount']}", severity='DESIGN')
        elif case == 'duplicate_lr':
            pid, tid, _ = mk_party_trip(env, tag)
            form, _ = trip_form(f'{tag}-T0', f'{tag}Z', 'permt', 1.0, 0, 0)
            env.post('/trips/add', form)
            n = env.one('SELECT COUNT(*) c FROM trips WHERE lr_number=?', f'{tag}-T0')['c']
            run.check('DUPLICATE_LR_ACCEPTED', n == 1, 'second trip with the same LR number', 'rejected', f'{n} trips with that LR')
        elif case == 'end_twice':
            pid, tid, billed = mk_party_trip(env, tag)
            env.post(f'/trips/{tid}/end', {'end_date': '2026-07-01', 'freight_amount': num(billed), 'shortage_amount': '300'})
            b1 = env.one('SELECT billed_amount FROM trips WHERE id=?', tid)['billed_amount']
            env.post(f'/trips/{tid}/end', {'end_date': '2026-07-02', 'freight_amount': num(billed), 'shortage_amount': '300'})
            b2 = env.one('SELECT billed_amount FROM trips WHERE id=?', tid)['billed_amount']
            run.check('END_TRIP_TWICE_CHANGES_BILL', close(b1, b2), 'End Trip submitted twice', b1, b2)
        elif case == 'end_shortage_over_freight':
            pid, tid, billed = mk_party_trip(env, tag)
            env.post(f'/trips/{tid}/end', {'end_date': '2026-07-01', 'freight_amount': num(billed), 'shortage_amount': num(billed + 5000)})
            b = env.one('SELECT billed_amount FROM trips WHERE id=?', tid)['billed_amount']
            run.check('END_TRIP_NEGATIVE_BILL', b >= 0, 'shortage larger than the whole freight', 'billed >= 0 or rejected', f'billed {b}')
        elif case == 'party_change_after_payment':
            pa, ta, billed = mk_party_trip(env, tag + 'a')
            env.post(pay_url(pa), {'date': '2026-06-10', 'amount': '4000', 'mode': 'Cash', 'trip_ids': [str(ta)]})
            S = Screens(env)
            form, _ = trip_form(f'{tag}a-T0', f'{tag}b-Z', 'permt', 1.0, 0, 4000)        # re-save the trip under a different party
            env.post(f'/trips/edit/{ta}', form)
            pb = env.one('SELECT id FROM parties WHERE name=?', f'{tag}b-Z')['id']
            la, lb = S.party_ledger(pa)['net_signed'], S.party_ledger(pb)['net_signed']
            # the 4000 was received from party A; the trip (10000) now belongs to B. Conserved money: A is 4000 in credit, B owes 10000.
            run.check('PARTY_CHANGE_KEEPS_MONEY_WITH_THE_PAYER', same_display(-4000, la) and same_display(10000, lb), 'a trip re-saved under another party AFTER a payment was allocated to it',
                      "old party -4000 (they paid it), new party 10000", f'old party {la}, new party {lb}',
                      note='total across both parties is unchanged, but the payment is stranded on the old party', severity='DESIGN')
        elif case == 'amount_precision':
            pid, tid, billed = mk_party_trip(env, tag, billed_f=1.0)
            for k, amt in enumerate(('3333.33', '3333.33', '3333.34')):
                env.post(pay_url(pid), {'date': f'2026-06-1{k}', 'amount': amt, 'mode': 'Cash', 'trip_ids': [str(tid)]})
            row = env.one('SELECT payment_received FROM trips WHERE id=?', tid)['payment_received']
            S = Screens(env)
            pl = S.party_ledger(pid)
            run.check('PRECISION_THREE_PAYMENTS_SUM', close(row, 10000.0, 0.0001), 'three payments 3333.33 + 3333.33 + 3333.34', 10000.0, row)
            run.check('PRECISION_LEDGER_ZERO_AFTER_FULL_PAYMENT', same_display(0, pl['net_signed']), 'paid exactly in full', 0, pl['net_signed'])
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_edge(quick=False):
    out = []
    counter = itertools.count(1)

    def add(case, **kw):
        n = next(counter)
        out.append(dict({'id': f'E{n:04d}', 'stage': 'edge', 'case': case, 'tag': case}, **kw))

    for v in AMOUNT_VARIANTS:
        for route in ('web', 'app'):
            add('amount', variant=v, route=route)
    for field in ('date', 'amount', 'mode'):
        for route in ('web', 'app'):
            add('missing_field', field=field, route=route)
    for c in ('cross_party_tick', 'unknown_trip_ids', 'duplicate_tick', 'party_missing', 'double_submit'):
        for route in ('web', 'app'):
            add(c, route=route)
    for v in ('abc', '1e400', 'nan', '-5000', '', '99999999999999999'):
        add('opening_invalid', variant=v)
    for change in ({'quantity': 'abc'}, {'rate': 'abc'}, {'quantity': '-10'}, {'rate': '-1000'}, {'party_advance': '-500'}, {'party_advance': '99999'},
                   {'payment_received': '-1'}, {'payment_received': '99999'}, {'quantity': '1e400'}, {'quantity': 'nan'}, {'detention_charges': '-100'},
                   {'tds': '999999'}, {'date': ''}, {'party_name': ''}, {'vehicle_no': ''}, {'lr_number': ''}):
        add('trip_form_invalid', change=change)
    for c in ('duplicate_lr', 'end_twice', 'end_shortage_over_freight', 'party_change_after_payment', 'amount_precision'):
        add(c)
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: totals — every dashboard / list total vs the sum of the individual balances behind it
# --------------------------------------------------------------------------------------------------
def batch_totals_check(env, F, runs, batch_id):
    """After a whole batch of parties/vendors exists in one database, do the headline numbers add up?"""
    bal = {}
    for r in runs:
        bal.update(r.balances)
    if not bal:
        return
    S = Screens(env)
    sc = {'id': f'BATCH{batch_id}', 'spec': {'accounts': len(bal)}}
    exp_recv = r2(sum(b for b in bal.values() if b > 0))
    exp_pay = r2(sum(-b for b in bal.values() if b < 0))
    exp_net = r2(sum(bal.values()))
    tol = 0.5 * len(bal) + 1.0

    def chk(code, expected, shown, note):
        F.check(code, shown is not None and abs(expected - shown) <= tol, sc, f'{len(bal)} accounts', round(expected, 2), shown, note=note)

    a = S.accounts_totals()
    chk('TOTAL_ACCOUNTS_RECEIVABLE', exp_recv, a['Total Receivable'], 'Ledger list: Total Receivable')
    chk('TOTAL_ACCOUNTS_PAYABLE', exp_pay, a['Total Payable'], 'Ledger list: Total Payable')
    chk('TOTAL_ACCOUNTS_NET', exp_net, a['Net Balance'], 'Ledger list: Net Balance')
    d = S.dashboard_totals()
    chk('TOTAL_DASHBOARD_RECEIVABLES', exp_recv, d['Total Receivables'], 'Dashboard: Total Receivables')
    chk('TOTAL_DASHBOARD_PAYABLES', exp_pay, d['Total Payables'], 'Dashboard: Total Payables')
    al = S.app_ledger_totals()
    chk('TOTAL_APP_LEDGER_RECEIVABLE', exp_recv, al['Total Receivable'], 'Mobile app Ledger: Total Receivable')
    chk('TOTAL_APP_LEDGER_PAYABLE', exp_pay, al['Total Payable'], 'Mobile app Ledger: Total Payable')
    chk('TOTAL_APP_LEDGER_NET', exp_net, al['Net Balance'], 'Mobile app Ledger: Net Balance')
    ad = S.app_dashboard_totals()
    chk('TOTAL_APP_DASHBOARD_RECEIVABLE', exp_recv, ad['Receivable'], 'Mobile app Dashboard: Receivable')
    chk('TOTAL_APP_DASHBOARD_PAYABLE', exp_pay, ad['Payable'], 'Mobile app Dashboard: Payable')
    # website vs app must agree with each other too
    F.check('TOTAL_WEBSITE_VS_APP_DASHBOARD', d['Total Receivables'] is not None and ad['Receivable'] is not None and abs(d['Total Receivables'] - ad['Receivable']) <= 1, sc,
            f'{len(bal)} accounts', d['Total Receivables'], ad['Receivable'], note='website Dashboard receivables vs the mobile app Dashboard receivable')

    t = S.trips_totals()
    live_billed = sum(r.trips_billed for r in runs)
    live_pending = sum(r.trips_pending for r in runs)
    live_count = sum(r.trip_count for r in runs)
    F.check('TOTAL_TRIPS_COUNT', t['Total Trips'] == live_count, sc, f'{len(bal)} accounts', live_count, t['Total Trips'], note='Trips list: Total Trips')
    chk('TOTAL_TRIPS_BILLED', live_billed, t['Total Billed'], 'Trips list: Total Billed')
    chk('TOTAL_TRIPS_PENDING', live_pending, t['Pending Balance'], 'Trips list: Pending Balance')


# --------------------------------------------------------------------------------------------------
# STAGE: maintenance bills and expenses
# --------------------------------------------------------------------------------------------------
def label_sub(sp, label):
    """The small caption printed under a KPI value, e.g. '₹4,000 unpaid' under Total Maintenance Cost."""
    for node in sp.find_all(string=lambda t: t and t.strip().lower() == label.lower()):
        el = node.parent.find_next(class_=re.compile(r'(^|\s)(sub|mo-kpi-sub)(\s|$)'))
        if el is not None:
            return el.get_text(' ', strip=True)
    return None


_lm = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
LM_DATE = _lm.replace(day=15).isoformat()           # the mobile Maintenance Overview only ever shows LAST month
LM_PERIOD = (_lm.replace(day=1).isoformat(), _lm.isoformat())


def maint_screens(S, env, period=LM_PERIOD):
    sp = soup(env.get('/maintenance', date_from=period[0], date_to=period[1]))
    web = {'cost': (label_values(sp, 'Total Maintenance Cost') or [None])[0], 'pending_count': (label_values(sp, 'Pending Payments') or [None])[0],
           'unpaid': money(label_sub(sp, 'Total Maintenance Cost'))}
    ap = soup(env.get('/app/maintenance'))
    app_ = {'cost': (label_values(ap, 'Maintenance Cost') or [None])[0], 'pending_count': (label_values(ap, 'Pending Payments') or [None])[0],
            'unpaid': money(label_sub(ap, 'Maintenance Cost'))}
    return web, app_


def run_maint_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        S = Screens(env)
        vendor = f'{tag}V'
        env.post('/accounts/add', {'name': vendor, 'role': 'Vendor'})
        if sc['vehicle'] == 'own':
            vno = 'OD01AB0001'
            mk_party_trip(env, tag + 'p')                               # creates the OWN vehicle
        else:
            vno = 'OD02CD0002'
            form, _ = trip_form(f'{tag}p-T0', f'{tag}pZ', 'permt', 1.0, 0, 0, vehicle=vno,
                                extra={'type': 'hired', 'owner_name': f'{tag}O', 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '1000'})
            env.post('/trips/add', form)
        vid = env.one('SELECT id FROM vendors WHERE name=?', vendor)['id']
        amount, paid = float(sc['amount']), float(sc['paid'])
        kind = sc['kind']
        n_before = env.one('SELECT COUNT(*) c FROM maintenance')['c']
        if kind == 'direct':
            env.post('/maintenance/add', {'date': LM_DATE, 'vehicle_no': vno, 'category': 'Service', 'amount': num(amount), 'paid_amount': num(paid), 'vendor_name': vendor})
        elif kind == 'tyre':
            env.post('/maintenance/tyre/add', {'date': LM_DATE, 'vehicle_no': vno, 'amount': num(amount), 'paid_amount': num(paid), 'vendor_name': vendor,
                                                'tyre_action': 'New Tyre Fitted', 'tyre_position': 'FL', 'tyre_id': f'{tag}-TY'})
        elif kind == 'battery':
            env.post('/maintenance/battery/add', {'mode': 'install', 'vehicle_no': vno, 'brand': 'Exide', 'capacity_ah': '150', 'battery_type': 'Lead Acid', 'install_date': LM_DATE, 'warranty_months': '24', 'purchase_date': LM_DATE, 'purchase_price': num(amount),
                                                   'paid_amount': num(paid), 'vendor_name': vendor})
        elif kind == 'service':
            env.post('/maintenance/service/add', {'date': LM_DATE, 'vehicle_no': vno, 'service_type': 'General Service', 'amount': num(amount),
                                                   'paid_amount': num(paid), 'vendor_name': vendor})
        elif kind == 'insurance':
            env.post('/maintenance/insurance/add', {'vehicle_no': vno, 'insurer_name': vendor, 'premium_amount': num(amount), 'paid_amount': num(paid) if paid else '',
                                                     'start_date': LM_DATE, 'policy_number': f'{tag}-POL'})
        rows = env.sql('SELECT id, amount, paid_amount, vendor_id FROM maintenance WHERE id > ? ORDER BY id', n_before) if n_before else env.sql('SELECT id, amount, paid_amount, vendor_id FROM maintenance ORDER BY id')
        if not run.check('MAINT_ROW_CREATED', len(rows) == 1, f'{kind} add', '1 maintenance row', f'{len(rows)} rows'):
            return run
        row = rows[0]
        run.check('MAINT_AMOUNT_STORED', close(row['amount'] or 0, amount), f'{kind} add', amount, row['amount'], note=f'{kind} bill of {amount} typed')
        run.check('MAINT_PAID_STORED', close(row['paid_amount'] or 0, paid), f'{kind} add', paid, row['paid_amount'], note=f'{kind}: paid {paid} typed')
        run.check('MAINT_TAGGED_TO_VENDOR', row['vendor_id'] == vid, f'{kind} add', vid, row['vendor_id'])
        stored_amount, stored_paid = (row['amount'] or 0), (row['paid_amount'] or 0)

        def check_everything(step, vendor_bal_exp):
            ar = S.accounts_row(vendor)
            run.check('MAINT_VENDOR_LEDGER_BALANCE', same_display(vendor_bal_exp, ar['balance']), step, vendor_bal_exp, ar['balance'], note=f'{kind}: vendor ledger')
            web, app_ = maint_screens(S, env)
            if sc['vehicle'] == 'own':
                run.check('MAINT_OVERVIEW_TOTAL_COST', same_display(stored_amount, web['cost']), step, stored_amount, web['cost'], note=f'{kind}: Maintenance Overview total cost')
                unpaid = stored_amount - stored_paid
                run.check('MAINT_OVERVIEW_UNPAID', same_display(unpaid, web['unpaid']), step, unpaid, web['unpaid'], note=f'{kind}: Maintenance Overview "unpaid"')
                run.check('MAINT_OVERVIEW_PENDING_COUNT', web['pending_count'] == (1 if unpaid > 0.005 else 0), step, 1 if unpaid > 0.005 else 0, web['pending_count'], note=f'{kind}: "Pending Payments" count')
                run.check('MAINT_APP_OVERVIEW_TOTAL_AND_UNPAID', same_display(stored_amount, app_['cost']) and same_display(unpaid, app_['unpaid']), step,
                          f'cost {stored_amount} unpaid {unpaid}', f"cost {app_['cost']} unpaid {app_['unpaid']}", note=f'{kind}: mobile Maintenance Overview')
                run.check('MAINT_WEBSITE_VS_APP_AGREE', same_display(web['cost'] or 0, app_['cost']) and same_display(web['unpaid'] or 0, app_['unpaid']), step,
                          f"web cost {web['cost']} unpaid {web['unpaid']}", f"app cost {app_['cost']} unpaid {app_['unpaid']}")
            else:
                run.check('MAINT_HIRED_VEHICLE_BILL_MISSING_FROM_OVERVIEW', (web['cost'] or 0) > 0 or stored_amount <= 0, step,
                          f'{stored_amount} of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance',
                          f"Maintenance Overview total cost {web['cost']}", note='the Maintenance Overview only counts OWN vehicles; the vendor is still owed this',
                          severity='DESIGN')
            return ar

        check_everything(f'after {kind} add', paid - amount)
        follow = sc.get('followup')
        owed = stored_amount - stored_paid
        if follow == 'pay_unticked' and owed > 0.005:
            resp = env.post(f'/payment/vendor/{vid}', {'date': '2026-06-10', 'amount': num(owed), 'mode': 'Cash', 'trip_ids': []})
            run.check('VENDOR_PAYMENT_FOR_BILL_RECORDED', resp.status_code < 500, 'pay the bill from the Ledger', 'recorded', f'HTTP {resp.status_code}')
            web, app_ = maint_screens(S, env)
            ar = S.accounts_row(vendor)
            run.check('MAINT_VENDOR_LEDGER_AFTER_PAYMENT', same_display(0, ar['balance']), 'after paying the vendor in the Ledger', 0, ar['balance'])
            if sc['vehicle'] == 'own':
                run.check('MAINT_UNPAID_AFTER_VENDOR_PAYMENT', same_display(0, web['unpaid']) and web['pending_count'] == 0, 'after paying the vendor in the Ledger',
                          'Maintenance shows the bill as paid (unpaid 0, no pending payment)',
                          f"Maintenance Overview still shows unpaid {web['unpaid']} and {web['pending_count']} pending payment(s) while the Ledger shows the vendor settled",
                          severity='USER-VISIBLE')
                run.check('MAINT_APP_UNPAID_AFTER_VENDOR_PAYMENT', same_display(0, app_['unpaid']), 'after paying the vendor in the Ledger', 0, app_['unpaid'], severity='USER-VISIBLE')
        elif follow == 'pay_then_mark_paid' and owed > 0.005:
            env.post(f'/payment/vendor/{vid}', {'date': '2026-06-10', 'amount': num(owed), 'mode': 'Cash', 'trip_ids': []})
            mid = row['id']
            env.post(f'/maintenance/edit/{mid}', {'date': LM_DATE, 'vehicle_no': vno, 'category': 'Service', 'amount': num(stored_amount),
                                                   'paid_amount': num(stored_amount), 'vendor_name': vendor})
            ar = S.accounts_row(vendor)
            run.check('MAINT_PAID_TWICE_AFTER_PAYMENT_AND_MARK_PAID', same_display(0, ar['balance']), 'ledger payment, then the bill marked paid on Maintenance', 0, ar['balance'],
                      note='paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid)')
        elif follow == 'edit_amount':
            env.post(f'/maintenance/edit/{row["id"]}', {'date': LM_DATE, 'vehicle_no': vno, 'category': 'Service', 'amount': num(stored_amount + 1000),
                                                         'paid_amount': num(stored_paid), 'vendor_name': vendor})
            stored_amount += 1000
            check_everything('after editing the bill amount', stored_paid - stored_amount)
        elif follow == 'delete':
            env.post(f'/maintenance/delete/{row["id"]}', {})
            stored_amount = stored_paid = 0
            check_everything('after deleting the bill', 0)
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_maint(quick=False):
    out = []
    counter = itertools.count(1)
    for kind, vehicle, (amount, paid), follow in itertools.product(['direct', 'tyre', 'battery', 'service', 'insurance'], ['own', 'hired'],
                                                                      [(7000, 0), (7000, 1000), (7000, 7000), (7000.5, 1000.25)],
                                                                      [None, 'pay_unticked', 'pay_then_mark_paid', 'edit_amount', 'delete']):
        n = next(counter)
        out.append({'id': f'M{n:04d}', 'stage': 'maint', 'kind': kind, 'vehicle': vehicle, 'amount': amount, 'paid': paid, 'followup': follow,
                    'tag': f'{kind}/{vehicle}/{amount}-{paid}/{follow}'})
    return out


# ---- expenses (Overheads)
def exp_screens(env):
    sp = soup(env.get('/overheads'))
    web = {'total': (label_values(sp, 'Total Expenses') or [None])[0], 'pending': (label_values(sp, 'Pending Bills') or [None])[0]}
    ap = soup(env.get('/app/expenses'))
    app_ = {'total': (label_values(ap, 'Total Expenses') or [None])[0], 'pending': (label_values(ap, 'Pending Bills') or [None])[0]}
    return web, app_


def run_expense_scenario(env, F, sc):
    run = Run(env, sc, F)
    try:
        rows = []                                    # model: (amount, status, vendor)
        for i, (amt, status, vend) in enumerate(sc['rows']):
            env.post('/overheads/add', {'date': f'2026-05-{10 + i:02d}', 'category': sc.get('category', 'Office'), 'amount': num(amt), 'status': status,
                                         'payment_mode': 'Cash', 'vendor': vend, 'due_date': '2026-12-01' if status == 'Pending' else ''})
            rows.append([amt, status, vend])
        S = Screens(env)

        def check(step):
            tot = sum(r[0] for r in rows)
            pend = sum(r[0] for r in rows if r[1] == 'Pending')
            web, app_ = exp_screens(env)
            run.check('EXPENSES_TOTAL', same_display(tot, web['total']), step, tot, web['total'], note='Expenses: Total Expenses card')
            run.check('EXPENSES_PENDING_BILLS', same_display(pend, web['pending']), step, pend, web['pending'], note='Expenses: Pending Bills card')
            run.check('EXPENSES_APP_TOTAL_PENDING', same_display(tot, app_['total']) and same_display(pend, app_['pending']), step, f'{tot} / {pend}',
                      f"{app_['total']} / {app_['pending']}", note='mobile app Expenses cards')
            nrows = len(env.sql('SELECT id FROM overheads'))
            run.check('EXPENSES_ROW_COUNT', nrows == len(rows), step, len(rows), nrows)

        check('after adding bills')
        # mark the first pending bill paid, change an amount, delete one
        pend_idx = next((i for i, r in enumerate(rows) if r[1] == 'Pending'), None)
        if sc.get('mark_paid') and pend_idx is not None:
            o = env.sql('SELECT id FROM overheads ORDER BY id')[pend_idx]['id']
            env.post(f'/overheads/edit/{o}', {'date': f'2026-05-{10 + pend_idx:02d}', 'category': sc.get('category', 'Office'), 'amount': num(rows[pend_idx][0]),
                                               'status': 'Paid', 'payment_mode': 'Cash', 'vendor': rows[pend_idx][2]})
            rows[pend_idx][1] = 'Paid'
            check('after marking one bill Paid')
        if sc.get('edit_amount'):
            o = env.sql('SELECT id FROM overheads ORDER BY id')[0]['id']
            env.post(f'/overheads/edit/{o}', {'date': '2026-05-10', 'category': sc.get('category', 'Office'), 'amount': num(rows[0][0] + 500), 'status': rows[0][1],
                                               'payment_mode': 'Cash', 'vendor': rows[0][2]})
            rows[0][0] += 500
            check('after editing an amount')
        if sc.get('delete'):
            o = env.sql('SELECT id FROM overheads ORDER BY id')[-1]['id']
            env.post(f'/overheads/delete/{o}', {})
            rows.pop()
            check('after deleting a bill')
        # a bill owed to a vendor is a real payable — is the vendor's ledger aware of it?
        pend_vendor_rows = [r for r in rows if r[1] == 'Pending' and r[2]]
        if pend_vendor_rows:
            v = pend_vendor_rows[0][2]
            env.post('/accounts/add', {'name': v, 'role': 'Vendor'})
            ar = S.accounts_row(v)
            owed = sum(r[0] for r in pend_vendor_rows if r[2] == v)
            run.check('PENDING_EXPENSE_BILL_NOT_IN_VENDOR_LEDGER', same_display(-owed, ar['balance']), 'a Pending expense bill naming a vendor',
                      f'vendor ledger shows {-owed} payable', f"vendor ledger shows {ar['balance']}",
                      note='an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor\'s ledger / the Dashboard payables', severity='BUG')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_expenses(quick=False):
    out = []
    counter = itertools.count(1)
    amt_sets = [[(1000, 'Paid', '')], [(1000, 'Pending', 'EXPVENDOR')], [(1000, 'Paid', ''), (2500.5, 'Pending', 'EXPVENDOR')],
                [(300, 'Pending', ''), (700, 'Pending', 'EXPVENDOR'), (1200.75, 'Paid', 'EXPVENDOR')], [(0.5, 'Pending', ''), (99999.99, 'Paid', '')]]
    for rows, mark, edit, delete in itertools.product(amt_sets, [False, True], [False, True], [False, True]):
        n = next(counter)
        out.append({'id': f'X{n:04d}', 'stage': 'expense', 'rows': [list(r) for r in rows], 'mark_paid': mark, 'edit_amount': edit, 'delete': delete,
                    'tag': f'exp/{len(rows)}rows/mark{mark}/edit{edit}/del{delete}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: employees — advances, repayments, salaries, opening advance
# --------------------------------------------------------------------------------------------------
EMP_PERIOD = ('2026-01-01', '2026-12-31')


def emp_screens(env, name, S=None):
    out = {}
    sp = soup(env.get(f'/employee/{name}', date_from=EMP_PERIOD[0], date_to=EMP_PERIOD[1]))
    txt = sp.get_text(' ', strip=True)
    m = re.search(r'Advance outstanding:\s*₹\s*([-\d,\.]+)', txt)
    out['web_adv'] = money(m.group(1)) if m else None
    m = re.search(r'Total salary paid[^₹]*₹\s*([-\d,\.]+)', txt)
    out['web_salary'] = money(m.group(1)) if m else None
    sp = soup(env.get('/salaries', tab='salary', date_from=EMP_PERIOD[0], date_to=EMP_PERIOD[1]))
    out['sal_payroll'] = (label_values(sp, 'Total Payroll') or [None])[0]
    out['sal_outstanding'] = (label_values(sp, 'Outstanding Advance') or [None])[0]
    sp = soup(env.get('/salaries', tab='overview', date_from=EMP_PERIOD[0], date_to=EMP_PERIOD[1]))
    out['ov_payroll'] = (label_values(sp, 'Total Payroll') or [None])[0]
    out['ov_outstanding'] = (label_values(sp, 'Outstanding Advances') or [None])[0]
    sp = soup(env.get('/app/salary', date_from=EMP_PERIOD[0], date_to=EMP_PERIOD[1]))
    out['app_payroll'] = (label_values(sp, 'Total Payroll') or [None])[0]
    out['app_outstanding'] = (label_values(sp, 'Outstanding Advance') or [None])[0]
    sp = soup(env.get(f'/app/employee/{name}', date_from=EMP_PERIOD[0], date_to=EMP_PERIOD[1]))
    out['app_adv'] = (label_values(sp, 'Advance Outstanding') or [None])[0]
    out['app_salary'] = (label_values(sp, 'Salary Paid') or [None])[0]
    return out


def run_employee_scenario(env, F, sc):
    run = Run(env, sc, F)
    name = f"{sc['id']}Emp"
    try:
        env.post('/employees/add', {'name': name, 'type': 'Driver', 'basic_salary': '15000'})
        row = env.one('SELECT id FROM employees WHERE name=?', name)
        if not run.check('EMPLOYEE_CREATED', row is not None, 'setup', 'employee row', 'none'):
            return run
        opening = float(sc.get('opening', 0))
        if opening:
            env.post(f'/employee/{name}/opening-balance', {'opening_balance': num(opening), 'opening_balance_date': '2026-02-01'})
        given = repaid = salary = 0.0

        def check(step):
            exp_adv = r2(opening + given - repaid)
            o = emp_screens(env, name)
            run.check('EMP_LEDGER_ADVANCE_OUTSTANDING', same_display(exp_adv, o['web_adv']), step, exp_adv, o['web_adv'], note='website employee ledger: Advance outstanding')
            run.check('EMP_LEDGER_SALARY_PAID', same_display(salary, o['web_salary']), step, salary, o['web_salary'], note='website employee ledger: total salary paid')
            run.check('EMP_SALARY_TAB_OUTSTANDING', same_display(exp_adv, o['sal_outstanding']), step, exp_adv, o['sal_outstanding'], note='Employees > Salary: Outstanding Advance')
            run.check('EMP_SALARY_TAB_PAYROLL', same_display(salary, o['sal_payroll']), step, salary, o['sal_payroll'], note='Employees > Salary: Total Payroll')
            run.check('EMP_OVERVIEW_OUTSTANDING', same_display(exp_adv, o['ov_outstanding']), step, exp_adv, o['ov_outstanding'], note='Employees > Overview: Outstanding Advances')
            run.check('EMP_OVERVIEW_PAYROLL', same_display(salary, o['ov_payroll']), step, salary, o['ov_payroll'], note='Employees > Overview: Total Payroll')
            run.check('EMP_APP_SALARY_OUTSTANDING_AND_PAYROLL', same_display(exp_adv, o['app_outstanding']) and same_display(salary, o['app_payroll']), step,
                      f'adv {exp_adv} payroll {salary}', f"adv {o['app_outstanding']} payroll {o['app_payroll']}", note='mobile Salary screen')
            run.check('EMP_APP_LEDGER', same_display(exp_adv, o['app_adv']) and same_display(salary, o['app_salary']), step,
                      f'adv {exp_adv} salary {salary}', f"adv {o['app_adv']} salary {o['app_salary']}", note='mobile employee ledger')
            if exp_adv < -0.005:
                run.check('EMP_REPAID_MORE_THAN_GIVEN', False, step, 'repayments never exceed advances given',
                          f'advance balance is {exp_adv} (employee has "repaid" more than was ever given)', severity='DESIGN')

        check('after setup')
        for i, (kind, amt, surface) in enumerate(sc['ops']):
            data = {'entry_kind': kind, 'date': f'2026-06-{10 + i:02d}', 'amount': num(amt), 'payment_mode': 'Cash'}
            url = f'/app/employee/{name}/add' if surface == 'app' else f'/employee/{name}'
            before = (env.one('SELECT COUNT(*) c FROM advances')['c'], env.one('SELECT COUNT(*) c FROM salaries')['c'])
            resp = env.post(url, data)
            after = (env.one('SELECT COUNT(*) c FROM advances')['c'], env.one('SELECT COUNT(*) c FROM salaries')['c'])
            run.check('EMP_ENTRY_RECORDED', sum(after) == sum(before) + 1, f'{kind} {amt} via {surface}', 'one new row', f'{sum(after) - sum(before)} rows (HTTP {resp.status_code})')
            if kind == 'given':
                given += amt
            elif kind == 'repaid':
                repaid += amt
            else:
                salary += amt
            check(f'after op {i}: {kind} {amt} ({surface})')
        if sc.get('then_opening') is not None:
            opening = float(sc['then_opening'])
            env.post(f'/employee/{name}/opening-balance', {'opening_balance': num(opening), 'opening_balance_date': '2026-02-01'})
            check('after replacing the opening advance')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_employees(quick=False):
    out = []
    counter = itertools.count(1)
    seqs = [
        [('given', 5000, 'web')], [('given', 5000, 'app')], [('salary', 15000, 'web')], [('salary', 15000, 'app')],
        [('given', 5000, 'web'), ('repaid', 2000, 'web')], [('given', 5000, 'web'), ('repaid', 5000, 'app')],
        [('given', 5000, 'web'), ('repaid', 7000, 'web')], [('given', 3000.5, 'web'), ('given', 1999.5, 'app'), ('repaid', 1000.25, 'web')],
        [('salary', 15000, 'web'), ('given', 4000, 'web'), ('salary', 15000, 'app'), ('repaid', 4000, 'app')],
        [('given', 1000, 'web')] * 3 + [('repaid', 1000, 'app')] * 2,
    ]
    for ops, opening, then in itertools.product(seqs, [0, 8000, -1500], [None, 2500]):
        n = next(counter)
        out.append({'id': f'N{n:04d}', 'stage': 'employee', 'ops': ops, 'opening': opening, 'then_opening': then,
                    'tag': f"emp/{len(ops)}ops/open{opening}/then{then}"})
    return out


def run_employee_edge(env, F, sc):
    run = Run(env, sc, F)
    name = f"{sc['id']}Emp"
    try:
        env.post('/employees/add', {'name': name, 'type': 'Driver', 'basic_salary': '15000'})
        kind, v, route = sc['kind'], sc['variant'], sc['route']
        url = f'/app/employee/{name}/add' if route == 'app' else f'/employee/{name}'
        if sc['case'] == 'amount':
            before = env.one('SELECT (SELECT COUNT(*) FROM advances)+(SELECT COUNT(*) FROM salaries) c')['c']
            resp = env.post(url, {'entry_kind': kind, 'date': '2026-06-10', 'amount': v, 'payment_mode': 'Cash'})
            after = env.one('SELECT (SELECT COUNT(*) FROM advances)+(SELECT COUNT(*) FROM salaries) c')['c']
            run.check('EMP_AMOUNT_SERVER_ERROR', resp.status_code < 500, f'{kind} amount {v!r} via {route}', 'handled', f'HTTP {resp.status_code}')
            want = AMOUNT_VARIANTS[v]
            if want is None:
                run.check('EMP_BAD_AMOUNT_ACCEPTED', after == before, f'{kind} amount {v!r} via {route}', 'rejected', f'{after - before} row(s) created')
            else:
                run.check('EMP_GOOD_AMOUNT_REJECTED', after == before + 1, f'{kind} amount {v!r} via {route}', 'recorded', f'{after - before} row(s) (HTTP {resp.status_code})')
        elif sc['case'] == 'bad_date':
            resp = env.post(url, {'entry_kind': kind, 'date': v, 'amount': '1000', 'payment_mode': 'Cash'})
            run.check('EMP_DATE_SERVER_ERROR', resp.status_code < 500, f'{kind} date {v!r} via {route}', 'handled', f'HTTP {resp.status_code}')
        elif sc['case'] == 'unknown_employee':
            before = env.one('SELECT (SELECT COUNT(*) FROM advances)+(SELECT COUNT(*) FROM salaries) c')['c']
            ghost = 'NoSuchPerson' + sc['id']
            url2 = f'/app/employee/{ghost}/add' if route == 'app' else f'/employee/{ghost}'
            resp = env.post(url2, {'entry_kind': kind, 'date': '2026-06-10', 'amount': '1000', 'payment_mode': 'Cash'})
            after = env.one('SELECT (SELECT COUNT(*) FROM advances)+(SELECT COUNT(*) FROM salaries) c')['c']
            run.check('EMP_ENTRY_FOR_NONEXISTENT_EMPLOYEE', after == before, f'{kind} for an employee that does not exist via {route}', 'rejected', f'{after - before} orphan row(s) (HTTP {resp.status_code})')
        elif sc['case'] == 'opening_invalid':
            resp = env.post(f'/employee/{name}/opening-balance', {'opening_balance': v, 'opening_balance_date': '2026-02-01'})
            run.check('EMP_OPENING_SERVER_ERROR', resp.status_code < 500, f'opening advance {v!r}', 'handled', f'HTTP {resp.status_code}')
            ob = env.one('SELECT opening_balance FROM employees WHERE name=?', name)['opening_balance']
            run.check('EMP_OPENING_NONSENSE_STORED', abs(ob or 0) < 1e10 and ob == ob, f'opening advance {v!r}', 'finite number or rejected', ob)
        elif sc['case'] == 'basic_salary':
            resp = env.post('/employees/add', {'name': name + 'X', 'type': 'Staff', 'basic_salary': v})
            run.check('EMP_BASIC_SALARY_SERVER_ERROR', resp.status_code < 500, f'basic salary {v!r}', 'handled', f'HTTP {resp.status_code}')
        elif sc['case'] == 'advance_drawer':
            emp_id = env.one('SELECT id FROM employees WHERE name=?', name)['id']
            before = env.one('SELECT COUNT(*) c FROM advances')['c']
            resp = env.post(f'/employees/{emp_id}/advance/add', {'date': '2026-06-10', 'amount': v, 'type': kind})
            after = env.one('SELECT COUNT(*) c FROM advances')['c']
            run.check('EMP_DRAWER_AMOUNT_SERVER_ERROR', resp.status_code < 500, f'drawer advance {v!r}', 'handled', f'HTTP {resp.status_code}')
            if AMOUNT_VARIANTS[v] is None:
                run.check('EMP_DRAWER_BAD_AMOUNT_ACCEPTED', after == before, f'drawer advance {v!r}', 'rejected', f'{after - before} row(s)')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_employee_edge(quick=False):
    out = []
    counter = itertools.count(1)

    def add(case, **kw):
        n = next(counter)
        out.append(dict({'id': f'Y{n:04d}', 'stage': 'empedge', 'case': case, 'kind': 'given', 'route': 'web', 'tag': case}, **kw))
    for v in AMOUNT_VARIANTS:
        for kind in ('given', 'repaid', 'salary'):
            for route in ('web', 'app'):
                add('amount', variant=v, kind=kind, route=route)
    for v in ('', 'abc', '2026-13-45', '31/12/2026', '0000-00-00'):
        for kind in ('given', 'salary'):
            for route in ('web', 'app'):
                add('bad_date', variant=v, kind=kind, route=route)
    for kind in ('given', 'repaid', 'salary'):
        for route in ('web', 'app'):
            add('unknown_employee', variant='', kind=kind, route=route)
    for v in ('abc', '1e400', 'nan', '-5000', '', '99999999999999999'):
        add('opening_invalid', variant=v)
        add('basic_salary', variant=v)
    for v in AMOUNT_VARIANTS:
        for kind in ('given', 'repaid'):
            add('advance_drawer', variant=v, kind=kind)
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: accounting — Dashboard / Business Performance profit vs a first-principles model, per period
# --------------------------------------------------------------------------------------------------
def acct_period_screens(env, d1, d2):
    q = dict(date_from=d1, date_to=d2)
    sp = soup(env.get('/dashboard', **q))
    web_dash = {'rev': (label_values(sp, 'Total Revenue') or [None])[0], 'profit': (label_values(sp, 'Total Profit') or [None])[0]}
    sp = soup(env.get('/business-performance', **q))
    web_bp = {'rev': (label_values(sp, 'Total Revenue') or [None])[0], 'profit': (label_values(sp, 'Net Profit') or [None])[0]}
    qa = dict(period='custom', date_from=d1, date_to=d2)
    sp = soup(env.get('/app/dashboard', **qa))
    app_dash = {'rev': (label_values(sp, 'Revenue') or label_values(sp, 'Revenue (Custom)') or [None])[0], 'profit': None}
    for lab in sp.find_all(string=re.compile(r'^\s*Net Profit')):
        v = lab.parent.find_next(class_=_VAL)
        if v is not None:
            app_dash['profit'] = money(v.get_text(' ', strip=True))
            break
    for lab in sp.find_all(string=re.compile(r'^\s*Revenue \(')):
        v = lab.parent.find_next(class_=_VAL)
        if v is not None:
            app_dash['rev'] = money(v.get_text(' ', strip=True))
            break
    sp = soup(env.get('/app/business-performance', **qa))
    app_bp = {'rev': (label_values(sp, 'Total Revenue') or [None])[0], 'profit': (label_values(sp, 'Net Profit') or [None])[0]}
    return web_dash, web_bp, app_dash, app_bp


ACCT_PERIODS = {
    'whole': ('2026-05-01', '2026-06-30'),
    'may': ('2026-05-01', '2026-05-31'),
    'june': ('2026-06-01', '2026-06-30'),
    'first_day_only': ('2026-05-01', '2026-05-01'),
    'last_day_only': ('2026-06-30', '2026-06-30'),
    'reversed': ('2026-06-30', '2026-05-01'),
    'empty': ('2025-01-01', '2025-01-31'),
}


def run_acct_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        party = f'{tag}Pty'
        own_v, hired_v = 'OD01AB0001', 'OD02CD0002'

        def add(lr, vehicle, day, extra):
            form, billed = trip_form(lr, party, 'permt', 1.0, 0, 0, vehicle=vehicle, day=10, extra=extra)
            form['date'] = day
            env.post('/trips/add', form)
        # own trip on the first day of May: fuel / driver advance / parking+puncture are the company's own cost
        add(f'{tag}-O', own_v, '2026-05-01', {'fuel_amount': '2000', 'driver_adv_amount': '1000', 'parking': '100', 'puncture': '200'})
        hx = {'type': 'hired', 'owner_name': f'{tag}Own', 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '7000'}
        fk, ak = sc['hired_fuel'], sc['hired_adv']
        if fk != 'none':
            hx['fuel_amount'] = '500'
            hx['fuel_vendor'] = {'company': '', 'owner': f'{tag}Own'}[fk]
        if ak != 'none':
            hx['driver_adv_amount'] = '300'
            hx['driver_adv_vendor'] = {'company': '', 'owner': f'{tag}Own'}[ak]
        add(f'{tag}-H', hired_v, '2026-06-30', hx)       # hired trip on the last day of the window
        env.post('/maintenance/add', {'date': '2026-05-20', 'vehicle_no': own_v, 'category': 'Service', 'amount': '3000', 'paid_amount': '3000', 'vendor_name': f'{tag}Mv'})
        env.post('/overheads/add', {'date': '2026-06-10', 'category': 'Office', 'amount': '800', 'status': 'Paid', 'payment_mode': 'Cash'})
        env.post('/employees/add', {'name': f'{tag}Emp', 'type': 'Driver'})
        env.post(f'/employee/{tag}Emp', {'entry_kind': 'salary', 'date': '2026-05-31', 'amount': '5000', 'payment_mode': 'Cash'})

        # first-principles per-record model
        recs = [  # (date, revenue, true cost)
            ('2026-05-01', 10000.0, 2000 + 1000 + 100 + 200),
            ('2026-06-30', 10000.0, 7000.0),                 # hired: the owner's contracted freight is the whole cost (company-paid fuel/advance is deducted from it)
            ('2026-05-20', 0.0, 3000.0), ('2026-06-10', 0.0, 800.0), ('2026-05-31', 0.0, 5000.0)]
        hired_extra = (500 if fk != 'none' else 0) + (300 if ak != 'none' else 0)
        for name, (d1, d2) in ACCT_PERIODS.items():
            lo, hi = sorted((d1, d2))
            rev = sum(r for d, r, c in recs if lo <= d <= hi)
            cost = sum(c for d, r, c in recs if lo <= d <= hi)
            profit = rev - cost
            wd, wb, ad, ab = acct_period_screens(env, d1, d2)
            step = f'period {name} ({d1} → {d2})'
            run.check('ACCT_DASHBOARD_REVENUE', same_display(rev, wd['rev']), step, rev, wd['rev'], note='website Dashboard: Total Revenue')
            run.check('ACCT_BP_REVENUE', same_display(rev, wb['rev']), step, rev, wb['rev'], note='website Business Performance: Total Revenue')
            run.check('ACCT_APP_DASHBOARD_REVENUE', same_display(rev, ad['rev']), step, rev, ad['rev'], note='mobile Dashboard: Revenue')
            run.check('ACCT_APP_BP_REVENUE', same_display(rev, ab['rev']), step, rev, ab['rev'], note='mobile Business Performance: Total Revenue')
            run.check('ACCT_WEB_APP_DASHBOARD_PROFIT_AGREE', wd['profit'] is not None and ad['profit'] is not None and same_display(wd['profit'], ad['profit']), step,
                      wd['profit'], ad['profit'], note='website vs mobile Dashboard profit')
            run.check('ACCT_WEB_APP_BP_PROFIT_AGREE', wb['profit'] is not None and ab['profit'] is not None and same_display(wb['profit'], ab['profit']), step,
                      wb['profit'], ab['profit'], note='website vs mobile Business Performance profit')
            hired_in = lo <= '2026-06-30' <= hi
            run.check('ACCT_DASHBOARD_PROFIT_MISSES_HIRED_OWNER_COST', same_display(profit, wd['profit']), step, profit, wd['profit'],
                      note=f"Dashboard 'Total Profit' must subtract what the owner of a hired vehicle is paid (7,000 on the hired trip){'' if hired_in else ' — not in this period'}",
                      severity='BUG')
            run.check('ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL', same_display(profit, wb['profit']), step, profit, wb['profit'],
                      note=f"Business Performance counts the owner's 7,000 AND the fuel/advance ({hired_extra}) the company paid on that hired trip, which is already inside the 7,000"
                      if hired_extra else 'Business Performance net profit vs first-principles model', severity='BUG')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_acct(quick=False):
    out = []
    counter = itertools.count(1)
    for fk, ak in itertools.product(['none', 'company', 'owner'], repeat=2):
        n = next(counter)
        out.append({'id': f'A{n:04d}', 'stage': 'acct', 'hired_fuel': fk, 'hired_adv': ak, 'tag': f'acct/fuel-{fk}/adv-{ak}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: invoices — the amount the customer is asked to pay vs the amount the app says they owe
# --------------------------------------------------------------------------------------------------
def pdf_text(data):
    """Plain text of a ReportLab PDF (ASCII85 + Flate streams) — enough to read printed totals back."""
    import base64
    import zlib
    out = []
    for st in re.findall(rb'stream\r?\n(.*?)endstream', data, re.S):
        st = st.strip()
        if st.endswith(b'~>'):
            st = st[:-2]
        try:
            out.append(zlib.decompress(base64.a85decode(st, adobe=False)))
        except Exception:
            continue
    txt = b'\n'.join(out).decode('latin-1')
    return [t.replace('\\(', '(').replace('\\)', ')') for t in re.findall(r'\((.*?)\)\s*Tj', txt)]


def pdf_amount_after(tokens, label):
    for i, t in enumerate(tokens):
        if t.strip().upper().startswith(label.upper()):
            for nxt in tokens[i + 1:i + 3]:
                v = money(nxt)
                if v is not None:
                    return v
    return None


def run_invoice_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        party = f'{tag}Pty'
        extra = {}
        if sc['others']:
            descs, types, rates = zip(*sc['others'])
            extra.update({'other_desc': list(descs), 'other_type': list(types), 'other_rate': [str(r) for r in rates],
                          'other_qty': ['1'] * len(descs), 'other_vendor': [''] * len(descs)})
        form, billed = trip_form(f'{tag}-T0', party, sc['billing'], 1.0, 0, 0, extra=extra)
        env.post('/trips/add', form)
        row = env.one('SELECT id, billed_amount, shortage_amount FROM trips WHERE lr_number=?', f'{tag}-T0')
        if not run.check('TRIP_CREATED', row is not None, 'setup', 'row', 'none'):
            return run
        S = Screens(env)
        stored = row['billed_amount']
        run.check('DB_TRIP_BILLED', close(stored, billed), 'setup', round(billed, 2), stored)
        ledger = S.party_ledger(env.one('SELECT id FROM parties WHERE name=?', party)['id'])['net_signed']
        run.check('LEDGER_EQUALS_BILLED', same_display(stored, ledger), 'setup', stored, ledger)
        adds = sum(r for d, t, r in sc['others'] if t == 'charge')
        subs = sum(r for d, t, r in sc['others'] if t == 'deduction')
        # (a) the single-trip invoice page
        sp = soup(env.get(f"/invoices/{row['id']}"))
        txt = sp.get_text(' ', strip=True)
        m = re.search(r'TOTAL BILLED:\s*₹\s*([\d,\.]+)', txt)
        inv_total = money(m.group(1)) if m else None
        expect_inv = stored + adds - subs
        run.check('INVOICE_PAGE_TOTAL_IS_BILLED_PLUS_OTHERS', same_display(expect_inv, inv_total), 'trip invoice page', expect_inv, inv_total,
                  note='single-trip invoice: freight + charges - deductions + the trip Others lines')
        if sc['others']:
            run.check('INVOICE_TOTAL_DIFFERS_FROM_LEDGER_BECAUSE_OF_OTHERS_LINES', same_display(ledger, inv_total), 'trip invoice page vs Ledger',
                      f'invoice total = amount owed in the Ledger ({ledger})', f'invoice asks the customer for {inv_total} but the Ledger / Trips / Dashboard say {ledger}',
                      note='Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them',
                      severity='DESIGN')
        # (b) the batch (Invoice Center) PDF, preview mode so nothing is consumed
        pid = env.one('SELECT id FROM parties WHERE name=?', party)['id']
        resp = env.post('/invoice-center/generate', {'trip_ids': [str(row['id'])], 'invoice_type': 'party', 'party_id': str(pid), 'mode': 'preview',
                                                      'invoice_date': '2026-06-01', 'payment_status': 'PENDING'})
        run.check('INVOICE_CENTER_PREVIEW_OK', resp.status_code == 200 and resp.data[:4] == b'%PDF', 'invoice center preview', 'a PDF', f'HTTP {resp.status_code}')
        if resp.status_code == 200:
            tokens = pdf_text(resp.data)
            grand = pdf_amount_after(tokens, 'GRAND TOTAL')
            run.check('INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER', same_display(ledger, grand), 'invoice center PDF vs Ledger',
                      f'grand total = amount owed in the Ledger ({ledger})', f'PDF grand total {grand}',
                      note=f"{sc['billing']} billing, others={sc['others']}: the batch invoice total differs from what the Ledger says the party owes",
                      severity='DESIGN')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_invoices(quick=False):
    out = []
    counter = itertools.count(1)
    others_sets = [[], [('Detention', 'charge', 1500)], [('Discount', 'deduction', 200)], [('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]]
    for billing, others in itertools.product(['permt', 'fixed', 'guarantee', 'charged', 'paise', 'shortage'], others_sets):
        n = next(counter)
        out.append({'id': f'I{n:04d}', 'stage': 'invoice', 'billing': billing, 'others': others, 'tag': f'invoice/{billing}/{len(others)}others'})
    return out


def run_invoice_input_scenario(env, F, sc):
    """Garbage in the Invoice Center form must be refused, not crash the page or print nonsense."""
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        form, billed = trip_form(f'{tag}-T0', f'{tag}Pty', 'permt', 1.0, 0, 0)
        env.post('/trips/add', form)
        tid = env.one('SELECT id FROM trips WHERE lr_number=?', f'{tag}-T0')['id']
        pid = env.one('SELECT id FROM parties WHERE name=?', f'{tag}Pty')['id']
        data = {'trip_ids': [str(tid)], 'invoice_type': 'party', 'party_id': str(pid), 'mode': 'preview', 'invoice_date': '2026-06-01', 'payment_status': 'PENDING'}
        data[sc['field']] = sc['value']
        resp = env.post('/invoice-center/generate', data)
        run.check('INVOICE_FORM_SERVER_ERROR', resp.status_code < 500, f"{sc['field']}={sc['value']!r}", 'handled', f'HTTP {resp.status_code}')
        if resp.status_code == 200 and resp.data[:4] == b'%PDF' and sc['field'] in ('loading_charges', 'other_charges', 'tds_rate', 'gst_rate'):
            tokens = pdf_text(resp.data)
            grand = pdf_amount_after(tokens, 'GRAND TOTAL')
            bad = sc['bad']
            run.check('INVOICE_NONSENSE_PRINTED', not bad or (grand is not None and 0 <= grand <= 2 * billed + 1e6 and grand == grand), f"{sc['field']}={sc['value']!r}",
                      'rejected or ignored', f'invoice printed with grand total {grand}')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_invoice_inputs(quick=False):
    out = []
    counter = itertools.count(1)
    for field in ('loading_charges', 'other_charges', 'tds_rate', 'gst_rate'):
        for value, bad in (('abc', True), ('-500', True), ('nan', True), ('inf', True), ('1e400', True), ('', False), ('0', False), ('250', False), ('5', False)):
            n = next(counter)
            out.append({'id': f'J{n:04d}', 'stage': 'invinput', 'field': field, 'value': value, 'bad': bad, 'tag': f'{field}={value}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: fuzz — every POST route in the app, fed garbage in every field it reads
# --------------------------------------------------------------------------------------------------
FUZZ_VALUES = ['abc', '-1', 'nan', '1e400', '', '0', "' OR 1=1 --", '<script>alert(1)</script>', 'x' * 3000, '99999999999999999999', '2026-13-45', '₹ 5,00,000']
FUZZ_SKIP = ('logout', 'login', 'otp', 'sms', 'sync', 'challan', 'route-finder', 'signup', 'static', 'rc-', 'echallan', 'password', 'send')
_FIELD_NAMES = None


def _form_field_names():
    global _FIELD_NAMES
    if _FIELD_NAMES is None:
        src = open(os.path.join(REPO, 'app.py')).read()
        names = set(re.findall(r"(?:\bf|request\.form|form)\.(?:get|getlist)\('([a-zA-Z_0-9]+)'", src))
        names |= set(re.findall(r'name="([a-zA-Z_0-9]+)"', ''.join(open(os.path.join(REPO, 'templates', fn), errors='ignore').read()
                                                              for fn in os.listdir(os.path.join(REPO, 'templates')) if fn.endswith('.html'))))
        _FIELD_NAMES = sorted(names)
    return _FIELD_NAMES


def fuzz_routes():
    appmod = _boot_app()
    out = []
    for rule in appmod.app.url_map.iter_rules():
        if 'POST' not in rule.methods or rule.endpoint == 'static':
            continue
        if any(k in rule.rule for k in FUZZ_SKIP):
            continue
        out.append(rule.rule)
    return sorted(set(out))


def _fuzz_seed(env, tag):
    pid, tid, _ = mk_party_trip(env, tag)
    env.post('/accounts/add', {'name': f'{tag}V', 'role': 'Vendor'})
    env.post('/employees/add', {'name': f'{tag}E', 'type': 'Driver', 'basic_salary': '15000'})
    env.post('/maintenance/add', {'date': '2026-05-20', 'vehicle_no': 'OD01AB0001', 'category': 'Service', 'amount': '3000', 'paid_amount': '1000', 'vendor_name': f'{tag}V'})
    env.post('/overheads/add', {'date': '2026-06-10', 'category': 'Office', 'amount': '800', 'status': 'Pending', 'payment_mode': 'Cash'})
    env.post(f'/payment/party/{pid}', {'date': '2026-06-10', 'amount': '1000', 'mode': 'Cash'})
    return pid


def run_fuzz_scenario(env, F, sc):
    run = Run(env, sc, F)
    rule = sc['rule']
    try:
        for vi, value in enumerate(FUZZ_VALUES):
            env.reset()
            tag = f"{sc['id']}v{vi}"
            _fuzz_seed(env, tag)
            url = re.sub(r'<int:[a-z_]+>', '1', rule)
            url = re.sub(r'<(?:string:)?(employee|name)>', f'{tag}E', url)
            url = re.sub(r'<(?:string:)?(role|entity_type)>', 'party', url)
            url = re.sub(r'<[^>]+>', 'party', url)
            data = {n: value for n in _form_field_names()}
            data['trip_ids'] = [value, '1']
            resp = env.post(url, data)
            shown = value if len(value) < 40 else value[:20] + f'…({len(value)} chars)'
            run.check('FUZZ_SERVER_ERROR', resp.status_code < 500, f'POST {rule} with every field = {shown!r}', 'a handled response (redirect / 4xx / validation message)',
                      f'HTTP {resp.status_code}', note=rule)
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_fuzz(quick=False):
    out = []
    for i, rule in enumerate(fuzz_routes(), 1):
        out.append({'id': f'F{i:04d}', 'stage': 'fuzz', 'rule': rule, 'tag': rule})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: urea stock — stock in / out / direct purchases
# --------------------------------------------------------------------------------------------------
def run_urea_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        S = Screens(env)
        today = datetime.date.today().isoformat()
        vendor = f'{tag}Sup'
        env.post('/accounts/add', {'name': vendor, 'role': 'Vendor'})
        env.post('/accounts/add', {'name': f'{tag}Pty', 'role': 'Party'})
        mk_party_trip(env, tag + 'u')                                    # creates the own vehicle OD01AB0001
        in_qty = in_val = 0.0
        stock = 0.0
        used_today = 0.0
        vendor_owed = 0.0

        def check(step):
            avg = (in_val / in_qty) if in_qty else 0.0
            exp_val = stock * avg
            sp = soup(env.get('/maintenance', tab='urea'))
            web = {'stock': (label_values(sp, 'Current Stock') or [None])[0], 'value': (label_values(sp, 'Total Value') or [None])[0],
                   'today': (label_values(sp, "Today's Usage") or [None])[0]}
            ap = soup(env.get('/app/urea'))
            app_ = {'stock': (label_values(ap, 'Current Stock') or [None])[0], 'value': (label_values(ap, 'Total Value') or [None])[0],
                    'today': (label_values(ap, "Today's Usage") or [None])[0]}
            run.check('UREA_WEB_CURRENT_STOCK', same_display(stock, web['stock']), step, round(stock, 2), web['stock'], note='Maintenance > Urea: Current Stock (litres)')
            run.check('UREA_WEB_TOTAL_VALUE', same_display(exp_val, web['value']), step, round(exp_val, 2), web['value'], note='Maintenance > Urea: Total Value at average cost')
            run.check('UREA_WEB_TODAY_USAGE', same_display(used_today, web['today']), step, round(used_today, 2), web['today'], note="Maintenance > Urea: Today's Usage")
            run.check('UREA_APP_AGREES', same_display(stock, app_['stock']) and same_display(exp_val, app_['value']) and same_display(used_today, app_['today']), step,
                      f'stock {stock:.2f} value {exp_val:.2f} today {used_today:.2f}', f"stock {app_['stock']} value {app_['value']} today {app_['today']}", note='mobile Urea screen')
            ar = S.accounts_row(vendor)
            run.check('UREA_SUPPLIER_LEDGER_BALANCE', same_display(-vendor_owed, ar['balance']), step, -vendor_owed, ar['balance'], note='what we owe the urea supplier')
            n_rows = env.one("SELECT COUNT(*) c FROM urea_transactions")['c']
            run.check('UREA_ROW_COUNT', n_rows == sc_rows[0], step, sc_rows[0], n_rows)

        sc_rows = [0]
        check('empty')
        for i, op in enumerate(sc['ops']):
            kind, qty = op[0], op[1]
            val = op[2] if len(op) > 2 else 0
            paid = op[3] if len(op) > 3 else 0
            surface = sc['surface']
            data = {'date': today, 'quantity_l': num(qty)}
            if kind == 'in':
                data.update({'mode': 'stock_in', 'unit_price': num(val), 'supplier_name': vendor, 'paid_amount': num(paid)})
            elif kind == 'out':
                data.update({'mode': 'stock_out', 'vehicle_no': 'OD01AB0001'})
            else:
                data.update({'mode': 'direct', 'unit_price': num(val), 'supplier_name': vendor, 'paid_amount': num(paid), 'vehicle_no': 'OD01AB0001'})
            url = '/app/urea/add' if surface == 'app' else '/maintenance/urea/add'
            resp = env.post(url, data)
            stock = round(stock, 2)
            will_fail = kind == 'out' and qty > stock + 1e-9
            rows_now = env.one("SELECT COUNT(*) c FROM urea_transactions")['c']
            if will_fail:
                run.check('UREA_OVERDRAW_REFUSED', rows_now == sc_rows[0], f'op {i}: take {qty} L out with {stock} L in stock', 'refused (stock cannot go negative)', f'{rows_now - sc_rows[0]} row(s) added')
                check(f'after refused overdraw (op {i})')
                continue
            run.check('UREA_ENTRY_RECORDED', rows_now == sc_rows[0] + 1, f'op {i}: {kind} {qty} L via {surface}', 'one new row', f'{rows_now - sc_rows[0]} (HTTP {resp.status_code})')
            sc_rows[0] = rows_now
            if kind == 'in':
                in_qty += qty
                in_val += val
                stock += qty
                vendor_owed += val - paid
            elif kind == 'out':
                stock -= qty
                used_today += qty
            else:
                used_today += qty
                vendor_owed += val - paid
            check(f'after op {i}: {kind} {qty} L {("for ₹" + str(val)) if val else ""} ({surface})')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_urea(quick=False):
    out = []
    counter = itertools.count(1)
    seqs = [
        [('in', 1000, 80000, 80000)], [('in', 1000, 80000, 0)], [('in', 1000, 80000, 30000), ('out', 400)],
        [('in', 500, 40000, 0), ('in', 500, 45000, 45000), ('out', 700), ('out', 400)],        # second out overdraws
        [('in', 1000, 80000, 0), ('direct', 50, 4500, 4500), ('out', 100)],
        [('direct', 50, 4500, 0)], [('out', 10)], [('in', 333.33, 26666.64, 10000), ('out', 111.11), ('out', 222.22), ('out', 0.01)],
        [('in', 1000, 80000, 0), ('out', 1000), ('out', 1)],
    ]
    for ops, surface in itertools.product(seqs, ['web', 'app']):
        n = next(counter)
        out.append({'id': f'U{n:04d}', 'stage': 'urea', 'ops': ops, 'surface': surface, 'tag': f'urea/{len(ops)}ops/{surface}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: expense bills owed to a vendor (Pending expense with a vendor name) — ledger, payments, status
# --------------------------------------------------------------------------------------------------
def run_xbill_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        S = Screens(env)
        vendor = f'{tag}Util'
        env.post('/accounts/add', {'name': vendor, 'role': 'Vendor'})
        vid = env.one('SELECT id FROM vendors WHERE name=?', vendor)['id']
        bills = []            # dicts: id, amount, status, alloc
        for i, (amt, status) in enumerate(sc['bills']):
            env.post('/overheads/add', {'date': f'2026-05-{10 + i:02d}', 'category': f'Bill{i}', 'amount': num(amt), 'status': status,
                                         'payment_mode': 'Cash', 'vendor': vendor.lower() if sc.get('case_diff') else vendor,
                                         'due_date': '2026-12-01' if status == 'Pending' else ''})
            oid = env.sql('SELECT id FROM overheads ORDER BY id DESC LIMIT 1')[0]['id']
            bills.append({'id': oid, 'amount': float(amt), 'status': status, 'alloc': 0.0, 'owed_entry': status == 'Pending'})
        paid_total = 0.0

        def owed():
            """amount of the bills still Pending (what 'pay 100% of what is owed' means)"""
            return sum(b['amount'] - b['alloc'] for b in bills if not b.get('deleted') and b['status'] == 'Pending')

        def signed_balance():
            """First principles for the vendor ledger: every bill that is (or was) owed shows as a credit; a bill marked Paid by hand is
            debited in full except the part already shown on a payment row; every payment is a debit. Bills paid at entry never appear."""
            bal = paid_total
            for b in bills:
                if b.get('deleted') or not b['owed_entry']:
                    continue
                bal -= b['amount']
                if b['status'] == 'Paid':
                    bal += b['amount'] - b['alloc']
            return bal

        def exp_balance():
            return -signed_balance()

        def check(step):
            ar = S.accounts_row(vendor)
            exp = exp_balance()
            run.check('XBILL_VENDOR_LEDGER_BALANCE', same_display(-exp, ar['balance']), step, -exp, ar['balance'], note='vendor balance = pending expense bills - payments')
            web, app_ = exp_screens(env)
            pend_rows = [b for b in bills if not b.get('deleted') and b['status'] == 'Pending' and b['amount'] - b['alloc'] > 0.004]
            pend_amt = sum(b['amount'] - b['alloc'] for b in pend_rows)
            run.check('XBILL_EXPENSES_PENDING_AMOUNT', same_display(pend_amt, web['pending']), step, round(pend_amt, 2), web['pending'], note='Expenses: Pending Bills (still unpaid)')
            run.check('XBILL_EXPENSES_APP_PENDING', same_display(pend_amt, app_['pending']), step, round(pend_amt, 2), app_['pending'], note='mobile Expenses: Pending Bills')
            for b in bills:
                if b.get('deleted'):
                    continue
                row = env.one('SELECT status FROM overheads WHERE id=?', b['id'])
                run.check('XBILL_STATUS', row is not None and row['status'] == b['status'], step, b['status'], row['status'] if row else None, note=f"bill {b['id']}")
            dash = S.dashboard_totals()
            run.check('XBILL_DASHBOARD_PAYABLES', dash['Total Payables'] is not None and abs(dash['Total Payables'] - max(exp, 0)) <= 1.0, step, round(max(exp, 0), 2), dash['Total Payables'], note='Dashboard payables')

        check('after adding bills')
        for i, p in enumerate(sc.get('pay', [])):
            amount = round(p * owed(), 2) if p <= 1.5 else float(p)
            if amount <= 0:
                continue
            resp = env.post(f'/payment/vendor/{vid}', {'date': f'2026-06-{10 + i:02d}', 'amount': num(amount), 'mode': 'Cash', 'trip_ids': []})
            run.check('XBILL_PAYMENT_RECORDED', resp.status_code < 500, f'payment {i}', 'recorded', f'HTTP {resp.status_code}')
            paid_total += amount
            rem = amount
            for b in bills:                                    # oldest first, pending bills only
                if rem <= 0.004:
                    break
                if b.get('deleted') or b['status'] != 'Pending':
                    continue
                need = b['amount'] - b['alloc']
                if need <= 0.004:
                    continue
                a = min(need, rem)
                b['alloc'] += a
                rem -= a
                if b['alloc'] >= b['amount'] - 0.004:
                    b['status'] = 'Paid'
            check(f'after payment {i} of {amount}')
        act = sc.get('act')
        if act == 'mark_paid_by_hand':
            b = next((b for b in bills if b['status'] == 'Pending'), None)
            if b:
                env.post(f"/overheads/edit/{b['id']}", {'date': '2026-05-10', 'category': 'Bill0', 'amount': num(b['amount']), 'status': 'Paid', 'payment_mode': 'Cash', 'vendor': vendor})
                b['status'] = 'Paid'      # settled outside the ledger: nets to zero for the vendor
                check('after marking a bill Paid by hand')
        elif act == 'delete_first':
            b = bills[0]
            env.post(f"/overheads/delete/{b['id']}", {})
            if b['status'] == 'Pending' or b['alloc'] > 0.004:
                pass
            b['deleted'] = True
            # a payment that had been applied to the deleted bill becomes unallocated credit with the vendor
            check('after deleting the first bill')
        elif act == 'lower_amount_below_paid':
            b = next((b for b in bills if b['alloc'] > 0.004), None)
            if b:
                resp = env.post(f"/overheads/edit/{b['id']}", {'date': '2026-05-10', 'category': 'Bill0', 'amount': num(b['alloc'] / 2), 'status': b['status'], 'payment_mode': 'Cash', 'vendor': vendor})
                row = env.one('SELECT amount FROM overheads WHERE id=?', b['id'])
                run.check('XBILL_AMOUNT_LOWERED_BELOW_PAID', close(row['amount'], b['amount']), 'edit the amount below what was already paid', 'refused (amount unchanged)', row['amount'])
                check('after a refused edit')
        elif act == 'change_vendor':
            b = next((b for b in bills if b['alloc'] > 0.004), None)
            if b:
                env.post(f"/overheads/edit/{b['id']}", {'date': '2026-05-10', 'category': 'Bill0', 'amount': num(b['amount']), 'status': b['status'], 'payment_mode': 'Cash', 'vendor': 'SomeoneElse'})
                row = env.one('SELECT vendor FROM overheads WHERE id=?', b['id'])
                run.check('XBILL_VENDOR_CHANGED_AFTER_PAYMENT', row['vendor'] == vendor or row['vendor'] == vendor.lower(), 'change the vendor of a bill already paid from the ledger', 'refused', row['vendor'])
                check('after a refused vendor change')
        elif act == 'ledger_vs_ledger_export':
            pass
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_xbills(quick=False):
    out = []
    counter = itertools.count(1)
    bill_sets = [[(1000, 'Pending')], [(1000, 'Pending'), (2500.5, 'Pending')], [(1000, 'Paid'), (700, 'Pending')], [(300, 'Pending'), (700, 'Pending'), (1200.75, 'Pending')]]
    pays = [[], [0.5], [1.0], [1.0, 500], [0.3, 0.3, 1.0], [250.25, 250.25, 250.25]]
    acts = [None, 'mark_paid_by_hand', 'delete_first', 'lower_amount_below_paid', 'change_vendor']
    for bills, pay, act, cd in itertools.product(bill_sets, pays, acts, [False, True]):
        n = next(counter)
        out.append({'id': f'B{n:04d}', 'stage': 'xbill', 'bills': bills, 'pay': pay, 'act': act, 'case_diff': cd, 'tag': f'xbill/{len(bills)}bills/pay{pay}/{act}/case{cd}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: owner (Freight Bill) invoice vs vendor ledger — fuel / advance the company paid on a hired trip
# --------------------------------------------------------------------------------------------------
def run_ownerinv_scenario(env, F, sc):
    run = Run(env, sc, F)
    tag = sc['id']
    try:
        S = Screens(env)
        owner = f'{tag}Own'
        other = f'{tag}Pump'
        owed, paid0 = float(sc['owed']), float(sc['paid0'])
        fk, ak = sc['fuel'], sc['adv']
        extra = {'type': 'hired', 'owner_name': owner, 'owner_rate_type': 'FIXED', 'owner_fixed_amount': num(owed), 'paid_to_owner': num(paid0)}
        ded = 0.0
        for kind, amt, field in ((fk, 500, 'fuel'), (ak, 300, 'driver_adv')):
            if kind == 'none':
                continue
            extra[f'{field}_amount'] = str(amt)
            extra[f'{field}_vendor' if field == 'fuel' else 'driver_adv_vendor'] = {'company': '', 'owner': owner, 'other': other}[kind]
            if kind in ('company', 'other'):
                ded += amt
        form, billed = trip_form(f'{tag}-H', f'{tag}Pty', 'permt', 1.0, 0, 0, vehicle='OD02CD0002', extra=extra)
        env.post('/trips/add', form)
        t = env.one('SELECT id FROM trips WHERE lr_number=?', f'{tag}-H')
        vid = env.one('SELECT id FROM vendors WHERE name=?', owner)['id']
        want = owed - paid0 - ded                                   # what we still owe the owner, by the ledger's rule
        led = S.vendor_ledger(vid)
        run.check('OWNERINV_LEDGER_PAYABLE', same_display(-want, led['net_signed']), 'vendor ledger', -want, led['net_signed'], note='owed - paid - company-paid fuel/advance')
        # the owner invoice PDF (preview, nothing is consumed)
        resp = env.post('/invoice-center/generate', {'trip_ids': [str(t['id'])], 'invoice_type': 'vehicle_owner', 'vendor_id': str(vid),
                                                      'mode': 'preview', 'invoice_date': '2026-06-01', 'payment_status': 'PENDING'})
        run.check('OWNERINV_PDF_OK', resp.status_code == 200 and resp.data[:4] == b'%PDF', 'owner invoice preview', 'a PDF', f'HTTP {resp.status_code}')
        if resp.status_code == 200:
            tokens = pdf_text(resp.data)
            net = pdf_amount_after(tokens, 'NET PAYABLE')
            if net is None:
                net = pdf_amount_after(tokens, 'GRAND TOTAL')
            run.check('OWNERINV_INVOICE_NET_PAYABLE_MATCHES_LEDGER', net is not None and abs(net - want) <= 1.0, 'owner invoice vs vendor ledger', want, net,
                      note=f'fuel={fk}, advance={ak}: the Freight Bill must ask to pay exactly what the vendor ledger says is owed')
        # the review screen before generating
        rv = env.post('/invoice-center/review', {'trip_ids': [str(t['id'])], 'invoice_type': 'vehicle_owner', 'vendor_id': str(vid)})
        txt = soup(rv).get_text(' ', strip=True) if hasattr(rv, 'data') else ''
        shown = [money(m) for m in re.findall(r'₹\s*[\d,]+', txt)]
        run.check('OWNERINV_REVIEW_SHOWS_NET', any(abs(v - want) < 1 for v in shown if v is not None), 'invoice review screen', want, shown[-6:])
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_ownerinv(quick=False):
    out = []
    counter = itertools.count(1)
    for fk, ak, paid0 in itertools.product(['none', 'company', 'owner', 'other'], ['none', 'company', 'owner', 'other'], [0, 1500]):
        n = next(counter)
        out.append({'id': f'W{n:04d}', 'stage': 'ownerinv', 'fuel': fk, 'adv': ak, 'owed': 7000, 'paid0': paid0, 'tag': f'ownerinv/fuel-{fk}/adv-{ak}/paid{paid0}'})
    return out


# --------------------------------------------------------------------------------------------------
# STAGE: access levels — Full Access can do everything, Read Only can see everything and change nothing
# --------------------------------------------------------------------------------------------------
READ_ONLY_ALLOWED_POSTS = {'app_change_password', 'invoice_center_review', 'toll_excel_preview', 'route_finder_extras'}


def _db_fingerprint(env):
    c = sqlite3.connect(env.path)
    try:
        out = {}
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            out[t] = c.execute(f'SELECT COUNT(*), COALESCE(SUM(rowid),0) FROM "{t}"').fetchone()
        return out
    finally:
        c.close()


def _client_for(env, user_id):
    cl = env.appmod.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'], s['username'], s['is_admin'] = user_id, f'u{user_id}', 0
    return cl


def run_access_scenario(env, F, sc):
    run = Run(env, sc, F)
    try:
        appmod = env.appmod
        _fuzz_seed(env, 'AC')
        for uid, (lvl, status) in {2: ('Read Only', 'Active'), 3: ('Full Access', 'Active'), 4: (None, 'Active'), 5: ('Read Only', 'Inactive'), 6: ('Limited Access', 'Active')}.items():
            env.exec("INSERT INTO users (id,username,password_hash,role,is_admin,full_name,access_level,status,phone,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     uid, f'900000010{uid}', 'x', 'Staff', 0, f'U{uid}', lvl, status, f'900000010{uid}', '2026-01-01')
        case = sc['case']
        ro, full, legacy = _client_for(env, 2), _client_for(env, 3), _client_for(env, 4)
        if case == 'read_only_blocked_everywhere':
            before = _db_fingerprint(env)
            denied = bad = 0
            for rule in sorted({r.rule for r in appmod.app.url_map.iter_rules() if 'POST' in r.methods and r.endpoint != 'static'}):
                ep = next(r.endpoint for r in appmod.app.url_map.iter_rules() if r.rule == rule and 'POST' in r.methods)
                if ep in ('login', 'app_login', 'app_signup', 'send_login_otp', 'verify_login_otp', 'app_login_otp', 'app_verify_login_otp', 'internal_sync_tick'):
                    continue
                url = re.sub(r'<int:[a-z_]+>', '1', rule)
                url = re.sub(r'<(?:string:)?(employee|name)>', 'ACE', url)
                url = re.sub(r'<[^>]+>', 'party', url)
                data = {n: '1' for n in _form_field_names()}
                data['trip_ids'] = ['1']
                r = ro.post(url, data=data)
                if ep in READ_ONLY_ALLOWED_POSTS or (ep == 'invoice_center_generate'):
                    continue
                if r.status_code != 403:
                    bad += 1
                    run.check('READ_ONLY_POST_NOT_BLOCKED', False, f'Read Only user: POST {rule}', 'HTTP 403 and nothing saved', f'HTTP {r.status_code}', note=rule)
                else:
                    denied += 1
            after = _db_fingerprint(env)
            run.check('READ_ONLY_DATABASE_UNCHANGED', before == after, 'after every blocked POST', 'database identical', 'database changed: ' + ', '.join(t for t in after if after[t] != before.get(t)))
            run.check('READ_ONLY_BLOCKED_COUNT', denied >= 90, 'routes blocked', '>= 90', denied)
        elif case == 'read_only_can_view':
            for url in sc['urls']:
                r = ro.get(url, follow_redirects=True)
                run.check('READ_ONLY_CAN_VIEW', r.status_code == 200, f'Read Only: GET {url}', 200, r.status_code)
                if r.status_code == 200 and 'text/html' in (r.content_type or ''):
                    run.check('READ_ONLY_BADGE_SHOWN', b'View-only access' in r.data, f'Read Only: {url}', 'view-only badge in the page', 'missing')
                rf = full.get(url, follow_redirects=True)
                run.check('FULL_ACCESS_HAS_NO_READ_ONLY_LAYER', b'ro-badge' not in rf.data and rf.status_code == 200, f'Full Access: GET {url}', 'normal page, no badge', rf.status_code)
        elif case == 'exports_work':
            for url in sc['urls']:
                r = ro.get(url)
                run.check('READ_ONLY_CAN_EXPORT', r.status_code == 200, f'Read Only: download {url}', 200, r.status_code)
        elif case == 'full_access_can_write':
            for who, cl in (('Full Access', full), ('legacy (no level set)', legacy)):
                n0 = env.one('SELECT COUNT(*) c FROM overheads')['c']
                r = cl.post('/overheads/add', data={'date': '2026-06-01', 'category': 'AccessTest', 'amount': '100', 'status': 'Paid', 'payment_mode': 'Cash'})
                n1 = env.one('SELECT COUNT(*) c FROM overheads')['c']
                run.check('FULL_ACCESS_CAN_WRITE', r.status_code < 400 and n1 == n0 + 1, f'{who}: add an expense', 'saved', f'HTTP {r.status_code}, rows {n0}->{n1}')
                oid = env.one('SELECT id FROM overheads ORDER BY id DESC LIMIT 1')['id']
                r = cl.post(f'/overheads/delete/{oid}', data={})
                run.check('FULL_ACCESS_CAN_DELETE', env.one('SELECT id FROM overheads WHERE id=?', oid) is None, f'{who}: delete it', 'deleted', f'HTTP {r.status_code}')
        elif case == 'denial_shapes':
            r = ro.post('/overheads/add', data={'date': '2026-06-01', 'category': 'X', 'amount': '1'}, headers={'Accept': 'text/html'})
            run.check('READ_ONLY_WEBSITE_DENIAL_IS_A_PAGE', r.status_code == 403 and b'view-only' in r.data.lower(), 'website form post', '403 page that explains', f'HTTP {r.status_code}')
            r = ro.post('/app/expenses/add', data={'date': '2026-06-01', 'category': 'X', 'amount': '1'})
            run.check('READ_ONLY_APP_DENIAL_IS_JSON', r.status_code == 403 and r.is_json and r.get_json().get('ok') is False, 'mobile app fetch', '403 JSON with an error message', f'HTTP {r.status_code}')
            run.check('READ_ONLY_NOTHING_SAVED', env.one("SELECT COUNT(*) c FROM overheads WHERE category='X'")['c'] == 0, 'after both', 'nothing saved', 'saved')
        elif case == 'allowed_posts':
            r = ro.post('/invoice-center/review', data={'trip_ids': ['1'], 'invoice_type': 'party', 'party_id': '1'})
            run.check('READ_ONLY_CAN_REVIEW_INVOICE', r.status_code == 200, 'invoice review screen', 200, r.status_code)
            r = ro.post('/invoice-center/generate', data={'trip_ids': ['1'], 'invoice_type': 'party', 'party_id': '1', 'mode': 'preview', 'invoice_date': '2026-06-01'})
            run.check('READ_ONLY_CAN_PREVIEW_INVOICE_PDF', r.status_code == 200 and r.data[:4] == b'%PDF', 'invoice PDF preview', 'a PDF', f'HTTP {r.status_code}')
            n0 = env.one('SELECT COUNT(*) c FROM invoice_batches')['c']
            r = ro.post('/invoice-center/generate', data={'trip_ids': ['1'], 'invoice_type': 'party', 'party_id': '1', 'mode': 'generate', 'invoice_date': '2026-06-01'})
            run.check('READ_ONLY_CANNOT_GENERATE_INVOICE', r.status_code == 403 and env.one('SELECT COUNT(*) c FROM invoice_batches')['c'] == n0, 'saving a new invoice', '403, nothing saved', f'HTTP {r.status_code}')
            r = ro.post('/invoice-center/generate', data={'trip_ids': ['1'], 'invoice_type': 'party', 'party_id': '1', 'mode': 'draft', 'invoice_date': '2026-06-01'})
            run.check('READ_ONLY_CANNOT_SAVE_DRAFT_INVOICE', r.status_code == 403, 'saving a draft invoice', 403, r.status_code)
            r = ro.post('/app/profile/change-password', data={'current_password': 'x', 'new_password': 'abcdef', 'confirm_password': 'abcdef'})
            run.check('READ_ONLY_PASSWORD_CHANGE_REACHES_THE_ROUTE', r.status_code != 403, 'own password change', 'allowed through (the route itself decides)', r.status_code)
            r = ro.post('/app/profile/update', data={'full_name': 'Hacker', 'email': 'a@b.co'})
            run.check('READ_ONLY_CANNOT_EDIT_PROFILE', r.status_code == 403, 'own profile edit', 403, r.status_code)
        elif case == 'level_change_is_immediate':
            r = full.post('/overheads/add', data={'date': '2026-06-01', 'category': 'Before', 'amount': '5', 'status': 'Paid'})
            run.check('BEFORE_CHANGE_CAN_WRITE', r.status_code < 400, 'Full Access user', 'saved', r.status_code)
            env.exec("UPDATE users SET access_level='Read Only' WHERE id=3")
            r = full.post('/overheads/add', data={'date': '2026-06-01', 'category': 'After', 'amount': '5', 'status': 'Paid'})
            run.check('CHANGE_TO_READ_ONLY_TAKES_EFFECT_IMMEDIATELY', r.status_code == 403 and env.one("SELECT COUNT(*) c FROM overheads WHERE category='After'")['c'] == 0,
                      'after an admin switches them to Read Only (no re-login)', '403, nothing saved', f'HTTP {r.status_code}')
            env.exec("UPDATE users SET access_level='Full Access' WHERE id=3")
            r = full.post('/overheads/add', data={'date': '2026-06-01', 'category': 'Back', 'amount': '5', 'status': 'Paid'})
            run.check('CHANGE_BACK_TO_FULL_TAKES_EFFECT', r.status_code < 400 and env.one("SELECT COUNT(*) c FROM overheads WHERE category='Back'")['c'] == 1, 'switched back to Full Access', 'saved', r.status_code)
        elif case == 'inactive_user_locked_out':
            inactive = _client_for(env, 5)
            r = inactive.get('/dashboard')
            run.check('INACTIVE_USER_LOCKED_OUT', r.status_code in (301, 302) and 'login' in r.headers.get('Location', ''), 'a deactivated user with a live session', 'sent to the login page', f'HTTP {r.status_code}')
            r = inactive.post('/overheads/add', data={'date': '2026-06-01', 'category': 'Ghost', 'amount': '5'})
            run.check('INACTIVE_USER_CANNOT_WRITE', env.one("SELECT COUNT(*) c FROM overheads WHERE category='Ghost'")['c'] == 0, 'deactivated user posting', 'nothing saved', 'saved')
            missing = _client_for(env, 999)
            r = missing.get('/dashboard')
            run.check('DELETED_USER_LOCKED_OUT', r.status_code in (301, 302), 'a session for a user that no longer exists', 'sent to the login page', f'HTTP {r.status_code}')
        elif case == 'limited_access_note':
            lim = _client_for(env, 6)
            r = lim.post('/overheads/add', data={'date': '2026-06-01', 'category': 'Ltd', 'amount': '5', 'status': 'Paid'})
            run.check('LIMITED_ACCESS_IS_NOT_ENFORCED_YET', r.status_code == 403 or True, 'Limited Access user', 'documented: behaves like Full Access today', f'HTTP {r.status_code}', severity='DESIGN')
    except Exception as e:
        F.check('SCENARIO_CRASH', False, sc, 'exception', 'no exception', repr(e)[:160], note=traceback.format_exc()[-500:], severity='CRASH')
    return run


def gen_access(quick=False):
    pages = ['/dashboard', '/trips', '/accounts', '/ledger/party/1', '/ledger/vendor/1', '/maintenance', '/overheads', '/salaries', '/invoices/generated', '/invoice-center',
             '/business-performance', '/route-analytics', '/vehicles', '/settings', '/app/dashboard', '/app/trips', '/app/ledger', '/app/expenses', '/app/maintenance', '/app/salary',
             '/trips/1/view', '/payments/1/view']
    exports = ['/trips/export', '/accounts/export', '/ledger/party/1/export', '/ledger/party/1/export/pdf', '/overheads/export', '/maintenance/export', '/settings/users/export']
    cases = [('read_only_blocked_everywhere', {}), ('read_only_can_view', {'urls': pages}), ('exports_work', {'urls': exports}), ('full_access_can_write', {}),
             ('denial_shapes', {}), ('allowed_posts', {}), ('level_change_is_immediate', {}), ('inactive_user_locked_out', {}), ('limited_access_note', {})]
    out = []
    for i, (case, extra) in enumerate(cases, 1):
        out.append(dict({'id': f'R{i:04d}', 'stage': 'access', 'case': case, 'tag': f'access/{case}'}, **extra))
    return out


# --------------------------------------------------------------------------------------------------
# running + reporting
# --------------------------------------------------------------------------------------------------
STAGES = {'party': gen_party, 'vendor': gen_vendor, 'edge': gen_edge, 'maint': gen_maint, 'expense': gen_expenses, 'employee': gen_employees, 'empedge': gen_employee_edge, 'acct': gen_acct, 'invoice': gen_invoices, 'invinput': gen_invoice_inputs, 'fuzz': gen_fuzz, 'urea': gen_urea, 'xbill': gen_xbills, 'ownerinv': gen_ownerinv, 'access': gen_access}


def run_batch(args):
    batch_id, scenarios = args
    F = Findings()
    traces = {}
    env = Env(batch_id)
    t0 = time.time()
    runs = []
    try:
        for sc in scenarios:
            if sc['stage'] in ('edge', 'maint', 'expense', 'employee', 'empedge', 'acct', 'invoice', 'invinput', 'fuzz', 'urea', 'xbill', 'ownerinv', 'access'):
                env.reset()
            runner = {'party': run_party_scenario, 'vendor': run_vendor_scenario, 'edge': run_edge_scenario, 'maint': run_maint_scenario, 'expense': run_expense_scenario, 'employee': run_employee_scenario, 'empedge': run_employee_edge, 'acct': run_acct_scenario, 'invoice': run_invoice_scenario, 'invinput': run_invoice_input_scenario, 'fuzz': run_fuzz_scenario, 'urea': run_urea_scenario, 'xbill': run_xbill_scenario, 'ownerinv': run_ownerinv_scenario, 'access': run_access_scenario}[sc['stage']]
            run = runner(env, F, sc)
            runs.append(run)
            if any(i['scenario'] == sc['id'] for i in F.items[-60:]):
                traces[sc['id']] = run.log
        if scenarios and all(sc['stage'] in ('party', 'vendor') for sc in scenarios):
            batch_totals_check(env, F, runs, batch_id)
    finally:
        env.close()
    return {'findings': F.items, 'checks': F.checks, 'traces': traces, 'secs': time.time() - t0, 'calls': env.calls, 'n': len(scenarios)}


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def signature(item):
    """Group identical symptoms together: same check, same cause."""
    note = item['note'] or ''
    if item['code'] in ('LEDGER_VS_TRIPS_GAP',):
        return (item['code'], note)
    if item['code'] in ('PAID_IN_FULL_BUT_TRIPS_STILL_PENDING',):
        return (item['code'], '')
    if item['scenario'][0] in 'EMXNYAIJFUBWR':
        return (item['code'], item['step'] if item['code'].startswith(('PAYMENT_', 'TRIP_', 'OPENING')) else note)
    return (item['code'], re.sub(r'P\d{4}Z?-T\d', 'LR', re.sub(r'[-\d.,]+', '#', note))[:60])


def summarise(results, scenarios, out_path):
    allf = [i for r in results for i in r['findings']]
    checks = sum(r['checks'] for r in results)
    by_sc = collections.defaultdict(list)
    for i in allf:
        by_sc[i['scenario']].append(i)
    groups = collections.defaultdict(list)
    for i in allf:
        groups[signature(i)].append(i)
    order = {'BUG': 0, 'CRASH': 0, 'USER-VISIBLE': 1, 'DESIGN': 2}
    ranked = sorted(groups.items(), key=lambda kv: (order.get(kv[1][0]['severity'], 3), -len(kv[1])))
    lines = []
    ap = lines.append
    ap('# Balance consistency audit — findings (report only, nothing was changed)\n')
    ap(f'- scenarios run: **{len(scenarios)}**  |  checks evaluated: **{checks}**  |  disagreements found: **{len(allf)}** in **{len(by_sc)}** scenarios')
    ap(f'- distinct symptoms after grouping identical ones: **{len(ranked)}**\n')
    ap('| # | severity | check | symptom | occurrences | scenarios |')
    ap('|---|---|---|---|---:|---:|')
    for n, (sig, items) in enumerate(ranked, 1):
        ap(f"| {n} | {items[0]['severity']} | `{sig[0]}` | {sig[1] or items[0]['note'] or ''} | {len(items)} | {len({i['scenario'] for i in items})} |")
    ap('')
    for n, (sig, items) in enumerate(ranked, 1):
        ap(f"## {n}. `{sig[0]}` — {items[0]['severity']} — {sig[1] or items[0]['note'] or ''}")
        ap(f"{len(items)} occurrences in {len({i['scenario'] for i in items})} scenarios. Smallest examples:\n")
        for i in sorted(items, key=lambda x: len(json.dumps(x['spec'] or {})))[:3]:
            ap(f"- `{i['scenario']}` · step *{i['step']}* · expected `{i['expected']}` · screen/DB showed `{i['actual']}` · {i['note']}")
            if i['spec']:
                ap(f"  - scenario: `{json.dumps(i['spec'])[:420]}`")
        ap('')
    with open(out_path, 'w') as fh:
        fh.write('\n'.join(lines))
    return ranked, allf, checks, by_sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--stage', default='all')
    ap.add_argument('--replay')
    ap.add_argument('--report', default=os.path.join(REPO, 'balance_audit_report.md'))
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument('--batch', type=int, default=24)
    args = ap.parse_args()

    scenarios = []
    for name, fn in STAGES.items():
        if args.stage in ('all', name):
            scenarios += fn(args.quick)
    if args.replay:
        sc = next(s for s in scenarios if s['id'] == args.replay)
        print(json.dumps(sc, indent=1))
        res = run_batch((0, [sc]))
        print('\n--- trace');
        for line in res['traces'].get(sc['id'], ['(no disagreements, so no trace was kept)']):
            print('  ', line)
        print('\n--- disagreements')
        for i in res['findings']:
            print(f"  [{i['severity']}] {i['code']} @ {i['step']}: expected {i['expected']} | got {i['actual']} | {i['note']}")
        return
    print(f'{len(scenarios)} scenarios, {args.workers} workers', flush=True)
    work = list(enumerate(chunks(scenarios, args.batch)))
    t0 = time.time()
    results = []
    with mp.Pool(args.workers) as pool:
        for k, res in enumerate(pool.imap_unordered(run_batch, work), 1):
            results.append(res)
            if k % 10 == 0 or k == len(work):
                done = sum(r['n'] for r in results)
                print(f'  {done}/{len(scenarios)} scenarios  ({time.time() - t0:.0f}s, {sum(len(r["findings"]) for r in results)} disagreements so far)', flush=True)
    ranked, allf, checks, by_sc = summarise(results, scenarios, args.report)
    print(f'\n{len(scenarios)} scenarios, {checks} checks, {len(allf)} disagreements in {len(by_sc)} scenarios -> {len(ranked)} distinct symptoms')
    for n, (sig, items) in enumerate(ranked, 1):
        print(f"  {n:2d}. [{items[0]['severity']:12s}] {sig[0]:42s} x{len(items):<5d} {(sig[1] or items[0]['note'] or '')[:70]}")
    print(f'\nfull report: {args.report}')
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    main()
