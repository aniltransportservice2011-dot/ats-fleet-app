"""Exhaustive combination test: payments on trips and in the ledger, every combination, checked against an independent model.

For each scenario the test performs real operations through the app (ledger payments, trip saves with entries, edits and removals of entries typed on the
trip) and, in parallel, replays the same operations in a small independent MODEL written from the rules (not from the app's code). After every step the app's
answer (accepted / refused) and, at the end, every number it stores or shows must equal the model's.

Families
  P  party side:  4 starting states (none / advance / earlier amount / both) x ledger payment sequences (0-2 payments, each under / exact / over what is due,
                  default order or the trip ticked) x trip-entry sequences (same) x which comes first x (none / edit / remove an entry) x a second older pending trip
                  of the same party x entries in one save or one by one
  O  owner side:  hired trip: the same payment combinations towards the owner, crossed with fuel / driver-advance set-ups (earlier total with the owner / another
                  vendor / nobody, dated entries for another vendor / the owner himself / nobody)
  C  cost entries: every combination of fuel and driver-advance entries and earlier totals, on an own and on a hired trip, with an edit and a removal
  M  mixed:       one hired trip with party AND owner payments entered from the trip and from the ledger

After each scenario (invariants, whatever the sequence):
  - trip totals == earlier amount + what the ledger / trip payments applied; never below what was applied; never negative;
  - every payment: allocated == sum of its allocations <= its amount; trip-entered payments are marked 'trip', ledger ones are not;
  - party balance, owner balance and every fuel vendor's balance == the model's, in the per-vendor ledger AND in the fast balance code (_accounts_rows);
  - the trip screen's pending figures == the ledger's own pending lists, to the rupee;
  - the trip's editable list holds exactly the trip-entered payments (+ cost entries); its read-only history holds exactly the ledger ones;
  - a forged edit / removal of a ledger payment from the trip is refused and changes nothing.

Usage:  python3 test_payment_combinations.py [--workers N] [--family P,O,C,M] [--limit N] [--replay ID]
Runs on throwaway copies of fleet.db (the real file is never touched); network blocked.
"""
import argparse
import itertools
import json
import multiprocessing as mp
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
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

EPS = 0.004
SURFACE = 'web'                                          # 'web' = the website's trip form, 'app' = the phone wizard (same engine, same expectations)
AMT = ('A', 'X', 'O')                                    # a fixed 5,000 / exactly what is due / more than is due
SEQS = [()] + [(a,) for a in AMT] + [(a, b) for a in AMT for b in AMT]


# ======================================================================================================================  the independent model
class Model:
    """What SHOULD happen, from the rules:
       - a payment from the party / to the owner is applied to a trip up to what that trip still owes; anything more stays unallocated (an advance);
       - a ledger payment with no trip ticked fills the oldest pending trips first, a ticked trip first;
       - a payment typed on a trip is applied to that trip only and is refused when nothing is left;
       - editing a typed payment undoes it and applies it again; removing one undoes it;
       - fuel / driver advance are totals made of an earlier amount + dated entries, each owed to its own vendor (a blank vendor = paid directly); whatever is paid
         to a vendor other than the owner comes off what the owner is owed."""

    def __init__(self):
        self.trips = {}
        self.pays = []
        self.cost = {}                       # trip -> list of {'id','kind','amount','vendor'}
        self.pid = 0
        self.cid = 0

    def add_trip(self, tid, date, billed, adv=0, lump_recv=0, owed=None, lump_paid=0, owner='OWN', lf=(0, ''), la=(0, ''), party=True):
        self.trips[tid] = dict(date=date, billed=billed, adv=adv, lump_recv=lump_recv, owed=owed, lump_paid=lump_paid, owner=owner, lf=lf, la=la, party=party)
        self.cost[tid] = []

    # ---- derived
    def alloc(self, tid, typ):
        return round(sum(a for p in self.pays if p['type'] == typ for t, a in p['allocs'].items() if t == tid), 2)

    def company(self, tid):
        t = self.trips[tid]
        if t['owed'] is None:
            return 0.0
        tot = 0.0
        for amt, v in [t['lf'], t['la']] + [(c['amount'], c['vendor']) for c in self.cost[tid]]:
            if v != t['owner']:
                tot += amt
        return round(tot, 2)

    def pend_party(self, tid):
        t = self.trips[tid]
        return round(t['billed'] - (t['lump_recv'] + self.alloc(tid, 'received')) - t['adv'], 2)

    def pend_owner(self, tid):
        t = self.trips[tid]
        return round((t['owed'] or 0) - (t['lump_paid'] + self.alloc(tid, 'paid')) - self.company(tid), 2)

    # ---- operations (each returns an error text, or None when accepted; a refused operation changes nothing)
    def _new_pay(self, typ, amount, origin, allocs, ref):
        self.pid += 1
        self.pays.append(dict(id=self.pid, type=typ, amount=amount, origin=origin, allocs=allocs, ref=ref))
        return self.pid

    def ledger_party(self, party_trips, amount, ticked, ref):
        pend = {t: self.pend_party(t) for t in party_trips if self.pend_party(t) > EPS}
        order = [t for t in ticked if t in pend] + [t for t in sorted(pend, key=lambda x: (self.trips[x]['date'], x)) if t not in ticked]
        left, allocs = amount, {}
        for t in order:
            if left <= EPS:
                break
            a = round(min(pend[t], left), 2)
            allocs[t] = a
            left -= a
        self._new_pay('received', amount, 'ledger', allocs, ref)

    def ledger_owner(self, owner_trips, amount, ticked, ref):
        pend = {t: self.pend_owner(t) for t in owner_trips if self.pend_owner(t) > EPS}
        order = [t for t in ticked if t in pend] + [t for t in sorted(pend, key=lambda x: (self.trips[x]['date'], x)) if t not in ticked]
        left, allocs = amount, {}
        for t in order:
            if left <= EPS:
                break
            a = round(min(pend[t], left), 2)
            allocs[t] = a
            left -= a
        self._new_pay('paid', amount, 'ledger', allocs, ref)

    def trip_save(self, tid, rows, deletes):
        """rows: dicts {id, kind, amount, vendor}; applies cost entries first, then undoes edited / removed typed payments, then applies new / edited ones.
        Returns an error text (nothing changed) or None."""
        import copy
        snap = copy.deepcopy((self.pays, self.cost, self.pid, self.cid))
        err = self._trip_save(tid, rows, deletes)
        if err:
            self.pays, self.cost, self.pid, self.cid = snap
        return err

    def _trip_save(self, tid, rows, deletes):
        t = self.trips[tid]
        for d in deletes:
            if d.startswith('c:'):
                if not any('c:' + c['ref'] == d for c in self.cost[tid]):
                    return 'entry no longer exists'
                self.cost[tid] = [c for c in self.cost[tid] if 'c:' + c['ref'] != d]
        for r in rows:
            if r['kind'] in ('fuel', 'driver_adv'):
                if r['id']:
                    for c in self.cost[tid]:
                        if 'c:' + c['ref'] == r['id']:
                            c['amount'], c['vendor'], c['ref'] = r['amount'], r['vendor'], r['ref']
                else:
                    self.cid += 1
                    self.cost[tid].append(dict(id=self.cid, kind=r['kind'], amount=r['amount'], vendor=r['vendor'], ref=r['ref']))
        pay_rows = [r for r in rows if r['kind'] in ('received', 'owner')]
        mine = {'p:' + p['ref']: p for p in self.pays if p['origin'] == 'trip' and tid in p['allocs']}
        rev, ins = [], []
        for d in deletes:
            if d.startswith('p:'):
                if d not in mine:
                    return 'ledger payment cannot be removed'
                rev.append(d)
        for r in pay_rows:
            if r['id']:
                cur = mine.get(r['id'])
                if cur is None:
                    return 'ledger payment cannot be changed'
                same = abs(cur['amount'] - r['amount']) < 0.005 and cur['type'] == ('received' if r['kind'] == 'received' else 'paid')
                if same:
                    continue
                if r['id'] not in rev:
                    rev.append(r['id'])
            ins.append(r)
        for d in rev:
            self.pays = [p for p in self.pays if 'p:' + p['ref'] != d]
        for r in ins:
            if r['kind'] == 'received':
                if not t['party']:
                    return 'no party'
                pend = self.pend_party(tid)
                if pend <= EPS:
                    return 'nothing left to receive'
                a = round(min(pend, r['amount']), 2)
                self._new_pay('received', r['amount'], 'trip', {tid: a}, r.get('ref', ''))
            else:
                if t['owed'] is None:
                    return 'no owner'
                pend = self.pend_owner(tid)
                if pend <= EPS:
                    return 'nothing left to pay the owner'
                a = round(min(pend, r['amount']), 2)
                self._new_pay('paid', r['amount'], 'trip', {tid: a}, r.get('ref', ''))
        return None

    # ---- expected balances
    def party_balance(self, party_trips):
        s = sum(self.trips[t]['billed'] - self.trips[t]['lump_recv'] - self.trips[t]['adv'] for t in party_trips)
        s -= sum(p['amount'] for p in self.pays if p['type'] == 'received')
        return round(s, 2)

    def vendor_balances(self):
        """{vendor key: balance} for the owner and every fuel / driver-advance vendor (blank = nobody)"""
        out = {}
        for tid, t in self.trips.items():
            parts = [t['lf'], t['la']] + [(c['amount'], c['vendor']) for c in self.cost[tid]]
            for amt, v in parts:
                if v and v != t['owner'] or (v and t['owed'] is None):
                    out[v] = out.get(v, 0) - amt
            if t['owed'] is not None:
                o = t['owner']
                out[o] = out.get(o, 0) + t['lump_paid'] - t['owed']
                for amt, v in parts:
                    if v != o:
                        out[o] += amt
        for p in self.pays:
            if p['type'] == 'paid':
                out['OWN'] = out.get('OWN', 0) + p['amount']
        return {k: round(v, 2) for k, v in out.items()}


# ======================================================================================================================  the app side
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


class Env:
    def __init__(self, dbp):
        self.dbp = dbp
        m.get_db = self.gdb
        m.app.config['TESTING'] = True
        uid = ensure_test_user(dbp)
        self.cl = m.app.test_client()
        with self.cl.session_transaction() as s:
            s['user_id'] = uid
        c = self.gdb()
        c.execute("INSERT OR IGNORE INTO vehicles (vehicle_no, type) VALUES ('CMBO','own')")
        c.execute("INSERT OR IGNORE INTO vehicles (vehicle_no, type) VALUES ('CMBH','hired')")
        c.commit()
        self.veh_own = c.execute("SELECT id FROM vehicles WHERE vehicle_no='CMBO'").fetchone()[0]
        self.veh_hired = c.execute("SELECT id FROM vehicles WHERE vehicle_no='CMBH'").fetchone()[0]
        c.close()
        self.n = 0

    def gdb(self):
        x = sqlite3.connect(self.dbp, timeout=60)
        x.row_factory = sqlite3.Row
        return x

    def q(self, sql, *a):
        c = self.gdb()
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def one(self, sql, *a):
        r = self.q(sql, *a)
        return r[0][0] if r else None

    def exec(self, sql, *a):
        c = self.gdb()
        c.execute(sql, a)
        c.commit()
        cur = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.close()
        return cur

    def form(self, tid):
        r = self.cl.get(self.edit_url(tid))
        f = _Form(); f.feed(r.data.decode('utf-8', 'ignore'))
        d = {}
        for n, v in f.fields:
            d.setdefault(n, v)
        return d, r.data.decode('utf-8', 'ignore')

    def edit_url(self, tid):
        return f'/app/trips/{tid}/edit' if SURFACE == 'app' else f'/trips/edit/{tid}'

    def existing_entries(self, tid):
        _, html = self.form(tid)
        mo = re.search(r'var (?:tfEntryExisting|existing) = (\[.*?\]);', html, re.S)
        return {e['id']: e for e in (json.loads(mo.group(1)) if mo else [])}

    def save(self, tid, rows, deletes=()):
        body, _ = self.form(tid)
        body['return_to'] = ''
        data = {k: [v] for k, v in body.items()}
        for k in ('id', 'kind', 'date', 'amount', 'mode', 'ref', 'vendor', 'liters'):
            data['entry_' + k] = [str(r.get(k, '')) for r in rows]
        data['entry_delete'] = list(deletes)
        return self.cl.post(self.edit_url(tid), data=data)


def vname(sid, key):
    return ('CM %s%d' % (key, sid)) if key else ''


def build_trip(env, model, sid, *, tid_name, date, billed, hired, party_id, lump_recv=0, adv=0, owner_vendor=None, lf=(0, ''), la=(0, ''), lump_paid=0):
    cols = {'date': date, 'lr_number': tid_name, 'vehicle_id': env.veh_hired if hired else env.veh_own, 'type': 'hired' if hired else 'own', 'party_id': party_id,
            'from_loc': 'A', 'to_loc': 'B', 'quantity': 10, 'rate': billed / 10.0, 'rate_type': 'PER_MT', 'billed_amount': billed, 'payment_received': lump_recv,
            'party_advance': adv}
    owed = None
    if hired:
        owed = 40000.0
        cols.update({'owner_vendor_id': owner_vendor, 'owner_name': vname(sid, 'OWN'), 'owner_rate': 4000, 'owner_rate_type': 'PER_MT', 'paid_to_owner': lump_paid})
    for (amt, v), (ca, cv, cn) in ((lf, ('fuel_amount', 'fuel_vendor_id', 'fuel')), (la, ('driver_adv_amount', 'driver_adv_vendor_id', 'adv'))):
        if amt:
            cols[ca] = amt
            if v:
                cols[cv] = env.one('SELECT id FROM vendors WHERE name=?', vname(sid, v))
    cols_sql = ','.join(cols)
    tid = env.exec(f"INSERT INTO trips ({cols_sql}) VALUES ({','.join('?' * len(cols))})", *cols.values())
    model.add_trip(tid, date, billed, adv=adv, lump_recv=lump_recv, owed=owed, lump_paid=lump_paid, owner='OWN', lf=(lf[0], lf[1]), la=(la[0], la[1]))
    return tid


def make_vendors(env, sid, keys):
    ids = {}
    for k in keys:
        env.exec("INSERT INTO vendors (name) VALUES (?)", vname(sid, k))
        ids[k] = env.one('SELECT id FROM vendors WHERE name=?', vname(sid, k))
    return ids


# ======================================================================================================================  running one scenario
def amount_for(code, model, tid, kind):
    pend = model.pend_owner(tid) if kind in ('owner', 'paid') else model.pend_party(tid)
    if code == 'X':
        return round(pend, 2) if pend > EPS else 5000.0
    if code == 'O':
        return round(max(pend, 0) + 7000, 2)
    return 5000.0


def check_state(env, model, ctx, trips_party, trips_owner, party_id, errors, vend_ids):
    """compare everything the app stores / shows with the model"""
    c = env.gdb()
    try:
        for tid, t in model.trips.items():
            row = c.execute('SELECT payment_received, paid_to_owner, party_advance, fuel_amount, driver_adv_amount FROM trips WHERE id=?', (tid,)).fetchone()
            exp_recv = round(t['lump_recv'] + model.alloc(tid, 'received'), 2)
            if abs((row['payment_received'] or 0) - exp_recv) > 0.011:
                errors.append(f'trip {tid}: payment_received {row["payment_received"]} != model {exp_recv}')
            if t['owed'] is not None:
                exp_paid = round(t['lump_paid'] + model.alloc(tid, 'paid'), 2)
                if abs((row['paid_to_owner'] or 0) - exp_paid) > 0.011:
                    errors.append(f'trip {tid}: paid_to_owner {row["paid_to_owner"]} != model {exp_paid}')
            exp_fuel = round(t['lf'][0] + sum(x['amount'] for x in model.cost[tid] if x['kind'] == 'fuel'), 2)
            exp_adv = round(t['la'][0] + sum(x['amount'] for x in model.cost[tid] if x['kind'] == 'driver_adv'), 2)
            if abs((row['fuel_amount'] or 0) - exp_fuel) > 0.011 or abs((row['driver_adv_amount'] or 0) - exp_adv) > 0.011:
                errors.append(f'trip {tid}: fuel/adv {row["fuel_amount"]}/{row["driver_adv_amount"]} != model {exp_fuel}/{exp_adv}')
            if (row['payment_received'] or 0) < -0.001 or (row['paid_to_owner'] or 0) < -0.001:
                errors.append(f'trip {tid}: negative total')
        # payments
        app_pays = c.execute("SELECT id, payment_type, amount, allocated_amount, entered_from, reference_id FROM payments WHERE reference_id LIKE ?", (ctx + '-%',)).fetchall()
        mp = {p['ref']: p for p in model.pays}
        if sorted(r['reference_id'] for r in app_pays) != sorted(p['ref'] for p in model.pays if p['ref']) and not any(not p['ref'] for p in model.pays):
            errors.append(f'payments differ: app {sorted(r["reference_id"] for r in app_pays)} vs model {sorted(p["ref"] for p in model.pays)}')
        for r in app_pays:
            mpay = mp.get(r['reference_id'])
            allocs = c.execute('SELECT trip_id, amount FROM payment_allocations WHERE payment_id=?', (r['id'],)).fetchall()
            s = round(sum(a['amount'] for a in allocs), 2)
            if abs(s - (r['allocated_amount'] or 0)) > 0.011 or s > (r['amount'] or 0) + 0.011:
                errors.append(f'payment {r["reference_id"]}: allocated {r["allocated_amount"]} vs sum {s} vs amount {r["amount"]}')
            if mpay:
                if abs(s - round(sum(mpay['allocs'].values()), 2)) > 0.011 or abs((r['amount'] or 0) - mpay['amount']) > 0.011:
                    errors.append(f'payment {r["reference_id"]}: app alloc {s}/{r["amount"]} vs model {sum(mpay["allocs"].values())}/{mpay["amount"]}')
                if (r['entered_from'] == 'trip') != (mpay['origin'] == 'trip'):
                    errors.append(f'payment {r["reference_id"]}: origin marker {r["entered_from"]} vs {mpay["origin"]}')
    finally:
        c.close()
    # balances: per-entity ledger and the fast code
    with m.app.test_request_context():
        cc = env.gdb()
        fast = {r['id']: round(r['balance'], 2) for r in m._accounts_rows(cc)}
        cc.close()
        if party_id:
            led = m._get_party_ledger_entries(party_id)
            bal = round(led[0]['balance'], 2) if led else 0.0
            exp = model.party_balance(trips_party)
            if abs(bal - exp) > 0.011:
                errors.append(f'party ledger balance {bal} != model {exp}')
            if abs(fast.get(party_id, 0) - bal) > 0.011:
                errors.append(f'party fast balance {fast.get(party_id)} != ledger {bal}')
        expv = model.vendor_balances()
        for key, vid in vend_ids.items():
            led = m._get_vendor_ledger_entries(vid)
            bal = round(led[0]['balance'], 2) if led else 0.0
            if abs(bal - expv.get(key, 0)) > 0.011:
                errors.append(f'vendor {key} ledger balance {bal} != model {expv.get(key, 0)}')
            if abs(fast.get(('v', vid), fast.get(vid, 0)) - bal) > 0.011 and ('v', vid) in fast:
                errors.append(f'vendor {key} fast balance {fast.get(("v", vid))} != ledger {bal}')
    # trip screen vs the ledger's own pending lists, and the editable / read-only split
    for tid in trips_party + trips_owner:
        with m.app.test_request_context():
            from flask import g
            g.access_level = 'Full Access'
            cc = env.gdb()
            d = m._trip_detail_data(cc, tid)
            pend_p = {x['id']: x['pending'] for x in m._party_pending_trips(cc, party_id)} if party_id else {}
            pay = d['pay']
            t = model.trips[tid]
            if pay.get('party'):
                mine = pend_p.get(tid, 0)
                if abs(max(pay['party']['due'], 0) - max(mine, 0)) > 0.011:
                    errors.append(f'trip {tid}: screen balance {pay["party"]["due"]} vs ledger pending {mine}')
                if abs(pay['party']['due'] - model.pend_party(tid)) > 0.011:
                    errors.append(f'trip {tid}: screen balance {pay["party"]["due"]} != model {model.pend_party(tid)}')
            if pay.get('owner') and t['owed'] is not None:
                ov = env.one('SELECT owner_vendor_id FROM trips WHERE id=?', tid)
                pend_o = {x['id']: x['pending'] for x in m._vendor_pending_trips(cc, ov)} if ov else {}
                if abs(max(pay['owner']['pending'], 0) - max(pend_o.get(tid, 0), 0)) > 0.011:
                    errors.append(f'trip {tid}: screen pending {pay["owner"]["pending"]} vs ledger pending {pend_o.get(tid, 0)}')
                if abs(pay['owner']['pending'] - model.pend_owner(tid)) > 0.011:
                    errors.append(f'trip {tid}: screen pending {pay["owner"]["pending"]} != model {model.pend_owner(tid)}')
                if abs(pay['owner']['company_paid'] - model.company(tid)) > 0.011:
                    errors.append(f'trip {tid}: company paid {pay["owner"]["company_paid"]} != model {model.company(tid)}')
            ent = pay['entries']
            typed = sorted(e['id'] for e in ent['payments'])
            exp_typed = sorted('p%d' % 0 for _ in [])
            led_rows = (pay['party'] or {}).get('ledger_rows', []) + (pay['owner'] or {}).get('ledger_rows', [])
            n_led_model = sum(1 for p in model.pays if p['origin'] == 'ledger' and tid in p['allocs'])
            n_trip_model = sum(1 for p in model.pays if p['origin'] == 'trip' and tid in p['allocs'])
            if len(led_rows) != n_led_model or len(ent['payments']) != n_trip_model:
                errors.append(f'trip {tid}: history/editable split {len(led_rows)}/{len(ent["payments"])} vs model {n_led_model}/{n_trip_model}')
            cc.close()


def run_scenario(spec):
    env = ENV
    env.n += 1
    sid = spec['sid']
    fam = spec['family']
    errors = []
    model = Model()
    ctx = 'CMB%d' % sid
    ref_n = [0]

    def ref():
        ref_n[0] += 1
        return '%s-%d' % (ctx, ref_n[0])

    env.exec("INSERT INTO parties (name) VALUES (?)", 'CM PARTY%d' % sid)
    party_id = env.one('SELECT id FROM parties WHERE name=?', 'CM PARTY%d' % sid)
    vend_ids = make_vendors(env, sid, ['OWN', 'X', 'Y']) if fam in ('O', 'C', 'M') else {}
    trips_party, trips_owner = [], []
    op_log = []

    # ---- build the trips
    if fam == 'P':
        T = build_trip(env, model, sid, tid_name=ctx + 'T', date='2026-09-01', billed=30000, hired=False, party_id=party_id, lump_recv=spec['lump'], adv=spec['adv'])
        trips_party.append(T)
        if spec['other']:
            O = build_trip(env, model, sid, tid_name=ctx + 'O', date='2026-08-01', billed=9000, hired=False, party_id=party_id)
            trips_party.append(O)
        main = T
    elif fam in ('O', 'M'):
        T = build_trip(env, model, sid, tid_name=ctx + 'T', date='2026-09-01', billed=30000, hired=True, party_id=party_id, owner_vendor=vend_ids['OWN'],
                       lf=tuple(spec['lf']), la=tuple(spec['la']), lump_paid=spec['lump_paid'], lump_recv=spec.get('lump', 0), adv=spec.get('adv', 0))
        trips_party.append(T); trips_owner.append(T)
        main = T
    else:  # C
        T = build_trip(env, model, sid, tid_name=ctx + 'T', date='2026-09-01', billed=30000, hired=spec['hired'], party_id=party_id,
                       owner_vendor=vend_ids.get('OWN') if spec['hired'] else None, lf=tuple(spec['lf']), la=tuple(spec['la']))
        trips_party.append(T)
        if spec['hired']:
            trips_owner.append(T)
        main = T

    # ---- the steps
    def do_ledger_party(code, ticked):
        a = amount_for(code, model, main, 'received')
        model.ledger_party(trips_party, a, [main] if ticked else [], None)
        model.pays[-1]['ref'] = r = ref()
        resp = env.cl.post(f'/payment/party/{party_id}', data={'date': '2026-10-01', 'amount': str(a), 'mode': 'UPI', 'reference_id': r, **({'trip_ids': [str(main)]} if ticked else {})})
        if resp.status_code not in (302, 200):
            errors.append(f'ledger party payment HTTP {resp.status_code}')
        op_log.append(('ledger-party', code, a, ticked))

    def do_ledger_owner(code, ticked):
        a = amount_for(code, model, main, 'paid')
        model.ledger_owner(trips_owner, a, [main] if ticked else [], None)
        model.pays[-1]['ref'] = r = ref()
        resp = env.cl.post(f'/payment/vendor/{vend_ids["OWN"]}', data={'date': '2026-10-02', 'amount': str(a), 'mode': 'Bank Transfer', 'reference_id': r, **({'trip_ids': [str(main)]} if ticked else {})})
        if resp.status_code not in (302, 200):
            errors.append(f'ledger owner payment HTTP {resp.status_code}')
        op_log.append(('ledger-owner', code, a, ticked))

    def mkey(app_id):
        """app entry id ('p12' / 'c5') -> the model's key (by reference)"""
        if not app_id:
            return ''
        if app_id[0] == 'p':
            return 'p:' + str(env.one('SELECT reference_id FROM payments WHERE id=?', int(app_id[1:])))
        return 'c:' + str(env.one('SELECT ref FROM trip_cost_entries WHERE id=?', int(app_id[1:])))

    def do_save(rows_spec, deletes=()):
        """rows_spec: list of (kind, code|amount, vendor_key, id). Resolve amounts at the moment of saving, ask the model, then the app, compare."""
        rows_m, rows_a = [], []
        # amounts resolved against the state BEFORE this save, row by row on a scratch copy so 'exact' means what is due when that row is reached
        import copy
        scratch = copy.deepcopy(model)
        for (kind, code, vkey, rid) in rows_spec:
            if isinstance(code, (int, float)):
                a = float(code)
            else:
                a = amount_for(code, scratch, main, 'owner' if kind == 'owner' else 'received')
            rm = dict(id=mkey(rid), kind=kind, amount=a, vendor=vkey or '', ref=ref())
            rows_m.append(rm)
            scratch.trip_save(main, [rm], [])    # keep the scratch current for the next row's 'exact'
            rows_a.append({'id': rid or '', 'kind': kind, 'date': '2026-10-03', 'amount': str(a), 'mode': 'UPI', 'ref': rm['ref'], 'vendor': vname(sid, vkey) if vkey else '', 'liters': ''})
        exp_err = model.trip_save(main, rows_m, [mkey(d) for d in deletes])
        resp = env.save(main, rows_a, deletes)
        got_err = resp.status_code == 200
        if bool(exp_err) != got_err:
            errors.append(f'save {rows_spec} deletes={list(deletes)}: model {"refuses (" + exp_err + ")" if exp_err else "accepts"} but app {"refused" if got_err else "accepted"} ({resp.status_code})')
        op_log.append(('save', [(r['kind'], r['amount'], r['vendor'], r['id']) for r in rows_m], list(deletes), exp_err))

    def typed_ids(kind):
        return sorted(env.existing_entries(main).items())

    # ---- per family
    try:
        if fam == 'P':
            L, Tq, order, post, batch, ticked = spec['L'], spec['T'], spec['order'], spec['post'], spec['batch'], spec['ticked']
            def ledger_ops():
                for code in L:
                    do_ledger_party(code, ticked)
            def trip_ops():
                if batch and Tq:
                    do_save([('received', code, '', '') for code in Tq])
                else:
                    for code in Tq:
                        do_save([('received', code, '', '')])
            if order == 'LT':
                ledger_ops(); trip_ops()
            else:
                trip_ops(); ledger_ops()
            if post != 'none' and Tq:
                ex = [e for e in env.existing_entries(main).values() if e['kind'] == 'received']
                if ex:
                    first = ex[0]
                    if post == 'edit':
                        new_amt = max(round(first['amount'] / 2, 2), 100.0)
                        do_save([('received', new_amt, '', first['id'])])
                    else:
                        do_save([], deletes=[first['id']])
            # a forged change of a ledger payment from the trip must be refused
            led_ids = [r['id'] for r in env.q("SELECT id FROM payments WHERE reference_id LIKE ? AND entered_from IS NULL", ctx + '-%')]
            if led_ids:
                before = env.one('SELECT payment_received FROM trips WHERE id=?', main)
                resp = env.save(main, [], deletes=['p%d' % led_ids[0]])
                if resp.status_code != 200 or env.one('SELECT payment_received FROM trips WHERE id=?', main) != before or env.one('SELECT COUNT(*) FROM payments WHERE id=?', led_ids[0]) != 1:
                    errors.append('a forged removal of a ledger payment was not refused')
        elif fam == 'O':
            L, Tq, order, post, batch, ticked = spec['L'], spec['T'], spec['order'], spec['post'], spec['batch'], spec['ticked']
            costrows = [('fuel', a, v, '') for (a, v) in spec['fuel']] + [('driver_adv', a, v, '') for (a, v) in spec['adv_rows']]
            if costrows:
                do_save(costrows)
            def ledger_ops():
                for code in L:
                    do_ledger_owner(code, ticked)
            def trip_ops():
                if batch and Tq:
                    do_save([('owner', code, '', '') for code in Tq])
                else:
                    for code in Tq:
                        do_save([('owner', code, '', '')])
            if order == 'LT':
                ledger_ops(); trip_ops()
            else:
                trip_ops(); ledger_ops()
            if post != 'none':
                ex = env.existing_entries(main)
                pays_ = [e for e in ex.values() if e['kind'] == 'owner']
                costs_ = [e for e in ex.values() if e['kind'] in ('fuel', 'driver_adv')]
                if post == 'edit' and pays_:
                    do_save([('owner', max(round(pays_[0]['amount'] / 2, 2), 100.0), '', pays_[0]['id'])])
                elif post == 'delete' and pays_:
                    do_save([], deletes=[pays_[0]['id']])
                elif post == 'delcost' and costs_:
                    do_save([], deletes=[costs_[0]['id']])
        elif fam == 'C':
            rows = [('fuel', a, v, '') for (a, v) in spec['fuel']] + [('driver_adv', a, v, '') for (a, v) in spec['adv_rows']]
            if rows:
                do_save(rows)
            post = spec['post']
            ex = [e for e in env.existing_entries(main).values() if e['kind'] in ('fuel', 'driver_adv')]
            if post == 'edit' and ex:
                do_save([(ex[0]['kind'], max(round(ex[0]['amount'] / 2, 2), 100.0), spec['edit_vendor'], ex[0]['id'])])
            elif post == 'delete' and ex:
                do_save([], deletes=[ex[0]['id']])
        else:  # M: party and owner payments on one hired trip, from the ledger and from the trip
            for step in spec['steps']:
                kind = step[0]
                if kind == 'LP':
                    do_ledger_party(step[1], step[2])
                elif kind == 'LO':
                    do_ledger_owner(step[1], step[2])
                else:
                    do_save([(k, c, '', '') for (k, c) in step[1]])
        check_state(env, model, ctx, trips_party, trips_owner, party_id, errors, vend_ids)
    except Exception as e:                                           # an unhandled error is exactly the bug
        import traceback
        errors.append('EXCEPTION %r %s' % (e, traceback.format_exc()[-400:]))
    return {'spec': spec, 'errors': errors, 'ops': op_log}


# ======================================================================================================================  scenarios
def scenarios(families):
    out = []
    sid = 0
    counter = 0
    if 'P' in families:
        for (adv, lump) in ((0, 0), (5000, 0), (0, 3000), (5000, 3000)):
            for L in SEQS:
                for Tq in SEQS:
                    orders = ['LT', 'TL'] if (L and Tq) else ['LT']
                    for order in orders:
                        for post in (['none', 'edit', 'delete'] if Tq else ['none']):
                            for other in (False, True):
                                counter += 1; sid += 1
                                out.append(dict(sid=sid, family='P', adv=adv, lump=lump, L=L, T=Tq, order=order, post=post, other=other, batch=(counter % 2 == 0), ticked=(counter % 3 == 0)))
    if 'O' in families:
        legacy_f = [(0, ''), (6000, 'X'), (6000, 'OWN')]
        fuel_sets = [[], [(3000, 'X')], [(3000, 'OWN')], [(3000, '')], [(3000, 'X'), (2000, 'OWN')]]
        adv_sets = [[], [(1000, 'Y')]]
        configs = list(itertools.product(legacy_f, fuel_sets, adv_sets))
        counter = 0
        for L in SEQS:
            for Tq in SEQS:
                orders = ['LT', 'TL'] if (L and Tq) else ['LT']
                for order in orders:
                    for post in (['none', 'edit', 'delete', 'delcost'] if Tq else ['none', 'delcost']):
                        counter += 1; sid += 1
                        lf, fuel, advr = configs[counter % len(configs)]
                        out.append(dict(sid=sid, family='O', L=L, T=Tq, order=order, post=post, lf=lf, la=(1000, '') if counter % 4 == 0 else (0, ''), fuel=fuel, adv_rows=advr,
                                        lump_paid=3000 if counter % 2 else 0, batch=(counter % 2 == 0), ticked=(counter % 3 == 0)))
    if 'C' in families:
        legacy_f = [(0, ''), (6000, 'X'), (6000, 'OWN'), (6000, '')]
        legacy_a = [(0, ''), (1000, 'Y')]
        fuel_sets = [[], [(3000, 'X')], [(3000, 'OWN')], [(3000, '')], [(3000, 'X'), (2000, 'OWN')], [(3000, 'X'), (2000, 'Y')]]
        adv_sets = [[], [(1000, 'X')], [(1000, 'OWN')], [(1000, '')]]
        for hired in (False, True):
            for lf in legacy_f:
                for la in legacy_a:
                    for fuel in fuel_sets:
                        for advr in adv_sets:
                            for post in ('none', 'edit', 'delete'):
                                sid += 1
                                out.append(dict(sid=sid, family='C', hired=hired, lf=lf, la=la, fuel=fuel, adv_rows=advr, post=post, edit_vendor='Y'))
    if 'M' in families:
        for P_ in SEQS:
            for O_ in SEQS:
                steps = [('LP', c, False) for c in P_] + [('S', [('owner', c) for c in O_])] if O_ else [('LP', c, False) for c in P_]
                # party-side typed entries and owner ledger payments in the same story
                steps += [('S', [('received', c) for c in P_[:1]])] if P_ else []
                steps += [('LO', c, False) for c in O_[:1]]
                sid += 1
                out.append(dict(sid=sid, family='M', lf=(6000, 'X'), la=(0, ''), lump_paid=0, steps=steps))
    return out


ENV = None


def _init(base_db):
    global ENV
    tmpd = tempfile.mkdtemp(prefix='paycombo_')
    dbp = os.path.join(tmpd, 't_%d.db' % os.getpid())
    src = sqlite3.connect(base_db); src.backup(sqlite3.connect(dbp)); src.close()
    ENV = Env(dbp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument('--family', default='P,O,C,M')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--replay', type=int, default=0)
    ap.add_argument('--out', default='')
    ap.add_argument('--surface', default='web', choices=['web', 'app'])
    args = ap.parse_args()
    global SURFACE
    SURFACE = args.surface
    fams = set(args.family.split(','))
    todo = scenarios(fams)
    if args.replay:
        todo = [s for s in todo if s['sid'] == args.replay]
    if args.limit:
        step = max(1, len(todo) // args.limit)
        todo = todo[::step][:args.limit]
    base = os.path.join(tempfile.mkdtemp(prefix='paycombo_base_'), 'base.db')
    src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(base)); src.close()
    t0 = time.time()
    results = []
    with mp.get_context('fork').Pool(args.workers, initializer=_init, initargs=(base,)) as pool:
        for i, res in enumerate(pool.imap_unordered(run_scenario, todo, chunksize=8), 1):
            results.append(res)
            if i % 500 == 0:
                bad_so_far = sum(1 for r in results if r['errors'])
                print(f'  {i}/{len(todo)} scenarios ({time.time() - t0:.0f}s, {bad_so_far} failing)', flush=True)
    bad = [r for r in results if r['errors']]
    by_fam = {}
    for r in results:
        f = r['spec']['family']
        by_fam.setdefault(f, [0, 0])
        by_fam[f][0] += 1
        by_fam[f][1] += 1 if r['errors'] else 0
    print()
    for f, (n, b) in sorted(by_fam.items()):
        print(f'family {f}: {n} scenarios, {b} failing')
    # group identical symptoms
    sym = {}
    for r in bad:
        for e in r['errors']:
            key = re.sub(r'-?\d+(\.\d+)?', 'N', e)[:140]
            sym.setdefault(key, []).append(r)
    for key, rs in sorted(sym.items(), key=lambda kv: -len(kv[1]))[:12]:
        r0 = rs[0]
        print(f'\n[{len(rs)}x] {key}\n   e.g. scenario {r0["spec"]["sid"]} {json.dumps({k: v for k, v in r0["spec"].items() if k != "sid"})}\n   {r0["errors"][0][:300]}\n   ops: {r0["ops"]}')
    if args.out:
        json.dump(bad, open(args.out, 'w'), indent=1, default=str)
    print(f'\n{len(results)} scenarios, {len(results) - len(bad)} passed, {len(bad)} failed ({time.time() - t0:.0f}s)')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
