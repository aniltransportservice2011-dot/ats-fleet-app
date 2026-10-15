"""Loads a RoadKing "Trip report" Excel export into the fleet database's trips (creating the vehicles and parties it mentions).

    python3 import_roadking_trips.py <trip_report.xlsx> --db <fleet.db>            rehearsal: does everything, prints the reconciliation, then
                                                                                   ROLLS BACK — nothing is saved
    python3 import_roadking_trips.py <trip_report.xlsx> --db <fleet.db> --apply    the same, and saves (one transaction: all or nothing)

Safe to run again: a trip whose LR number is already in the database is skipped, never duplicated.
Back the database up first (CLAUDE.md: test against a disposable copy, back up before touching the real file).

How a RoadKing row becomes a trip (the same mapping the earlier import used, plus the fields it used to leave blank):
  Start Date Time -> date          End Date Time -> end_date / end_time (only for trips that are not "STARTED")
  Vehicle No -> vehicles (own / hired) + vehicle_id + type          Party Name -> parties + party_id
  Unloading Weight (else Loading Weight) -> quantity      Shortage Weight -> shortage_qty
  Rate Type / Rate -> rate_type, rate (PER_MT) or fixed_rate_amount (FIXED)
  charges, deductions, expenses -> the same-named trip fields
  HIRED trips: RoadKing's Agent commission is what is paid to the owner of the hired vehicle, so it is stored as the OWNER AMOUNT
      (owner_rate_type = FIXED, owner_fixed_amount) — that is what the app's dashboard subtracts as a hired trip's cost — and agent_commission is left 0.
      RoadKing's profit also subtracts the fuel and driver advance given to the hired truck, while the app treats those two as already inside the
      owner amount; so by default owner_fixed_amount = commission + fuel + driver advance, which makes the app's hired profit equal RoadKing's.
      The fuel / driver advance stay in their own fields. Use --commission-only to store just the commission as the owner amount.
  Total Payment Received -> payment_received          Submit POD Date present -> lr_received = 'Yes'
  notes, bill (Pod) number, settlement date -> remarks (one line)
  billed_amount is computed exactly as the app does (freight + charges - deductions) and compared with RoadKing's "Adjusted Revenue".
  Total Freight / Total Expense / Profit / Trip Pending Balance / Party Balance are not stored — the app works them out; they are used to check.
An "EMPTY..." LR is a real Empty Trip: is_empty = 1, no party, no quantity / rate / bill.
A vehicle is HIRED when any of its trips has an Agent commission or the driver "OTHER VEH DRIVER" (except OWN_KEEP below).
"""
import argparse
import collections
import datetime
import re
import sqlite3
import sys

from openpyxl import load_workbook

OTHER_DRIVER = 'OTHER VEH DRIVER'
# vehicles the earlier database knew as OWN even though an occasional trip shows "OTHER VEH DRIVER" — they stay own
OWN_KEEP = {'OR14Y0117', 'OR14T3877'}

# RoadKing column -> trips column (plain numbers copied across)
CHARGES = {'Charges (+) / Detention charges': 'detention_charges', 'Charges (+) / Unloading expense': 'unloading_charge',
           'Charges (+) / Police charges': 'police_charges', 'Charges (+) / Weight charges': 'weight_charges',
           'Charges (+) / Other charges': 'other_charges', 'Charges (+) / GPS cost': 'gps_cost',
           'Charges (+) / Loading expense': 'loading_charge', 'Charges (+) / SIM tracking': 'sim_tracking',
           'Charges (+) / Union charges': 'union_charges', 'Charges (+) / Driver payment': 'driver_payment'}
DEDUCTIONS = {'Deductions (-) / Brokerage': 'brokerage', 'Deductions (-) / Builty commission': 'builty_commission',
              'Deductions (-) / Late fees': 'late_fees', 'Deductions (-) / Material damage': 'material_damage',
              'Deductions (-) / Shortage amount': 'shortage_amount', 'Deductions (-) / TDS': 'tds',
              'Deductions (-) / Other charges': 'other_deductions'}
EXPENSES = {'Expenses / Agent commission': 'agent_commission', 'Expenses / Builty expense': 'builty_expense',
            'Expenses / Conductor expense': 'conductor_expense', 'Expenses / Driver advance': 'driver_adv_amount',
            'Expenses / Fuel': 'fuel_amount', 'Expenses / Fine': 'fine', 'Expenses / Labour charges': 'labour_charges',
            'Expenses / Parking': 'parking', 'Expenses / Puncture': 'puncture', 'Expenses / Toll': 'toll',
            'Expenses / Urea': 'urea', 'Expenses / Loading expense': 'loading_expense',
            'Expenses / Unloading expense': 'unloading_expense', 'Expenses / Wear and tear': 'wear_tear',
            'Expenses / Weigh bridge charges': 'weighbridge_charges', 'Expenses / Other': 'other_expense'}
REQUIRED = ['LR Number', 'Vehicle No', 'Driver Name', 'Party Name', 'From', 'To', 'Status', 'Start Date Time', 'End Date Time',
            'Submit POD Date', 'Settlement Date', 'Material', 'Loading Weight', 'Unloading Weight', 'Shortage Weight', 'Rate Type',
            'Rate', 'Adjusted Revenue', 'Total Payment Received', 'Party Balance', 'Trip Pending Balance', 'Total Expense',
            'Start Notes', 'Pod Notes', 'Pod Number', 'Settlement Notes'] + list(CHARGES) + list(DEDUCTIONS) + list(EXPENSES)


def blank(v):
    return v is None or str(v).strip() in ('', '-')


def num(v):
    if blank(v):
        return 0.0
    try:
        return float(str(v).replace(',', '').strip())
    except ValueError:
        return 0.0


def tidy(v):
    """Collapse stray / doubled spaces."""
    return ' '.join(str(v).split()) if not blank(v) else ''


def lr_text(v):
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def parse_dt(v):
    """'05/04/2026 00:00' -> (date 'YYYY-MM-DD', time 'HH:MM' or None)"""
    if blank(v):
        return None, None
    s = str(v).strip()
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y'):
        try:
            d = datetime.datetime.strptime(s, fmt)
            return d.strftime('%Y-%m-%d'), (d.strftime('%H:%M') if fmt.endswith('%H:%M') and d.strftime('%H:%M') != '00:00' else None)
        except ValueError:
            pass
    raise ValueError(f'unreadable date {s!r}')


def read_report(path):
    """-> list of dicts keyed by RoadKing column name (two header rows merged: 'Group / Sub' when a group has sub-columns)."""
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if len(rows) < 3:
        raise SystemExit('The file has no trip rows.')
    n = max(len(r) for r in rows)
    h1 = rows[0] + [None] * (n - len(rows[0]))
    h2 = rows[1] + [None] * (n - len(rows[1]))
    names, group = [], None
    for top, sub in zip(h1, h2):
        if top is not None and sub is None:
            names.append(str(top).strip())
            group = None
        elif top is not None:
            group = str(top).strip()
            names.append(f'{group} / {str(sub).strip()}')
        elif sub is not None:
            names.append(f'{group} / {str(sub).strip()}' if group else str(sub).strip())
        else:
            names.append(None)
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise SystemExit('The file is missing expected columns: ' + ', '.join(missing))
    out = []
    for r in rows[2:]:
        r = r + [None] * (n - len(r))
        if any(v not in (None, '') for v in r):
            out.append({nm: v for nm, v in zip(names, r) if nm})
    return out


def run(xlsx, db, apply_, commission_only=False):
    data = read_report(xlsx)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('BEGIN IMMEDIATE')
    admin = conn.execute("SELECT id FROM users WHERE LOWER(TRIM(COALESCE(role,'')))='administrator' AND COALESCE(status,'Active')='Active' ORDER BY id LIMIT 1").fetchone()
    admin_id = admin['id'] if admin else None
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log = collections.OrderedDict()
    notes = []

    # ---- which vehicles are hired
    hired = set()
    for r in data:
        v = tidy(r['Vehicle No']).upper()
        if num(r['Expenses / Agent commission']) > 0 or tidy(r['Driver Name']).upper() == OTHER_DRIVER:
            hired.add(v)
    hired -= OWN_KEEP

    def vehicle_id(vno):
        row = conn.execute('SELECT id FROM vehicles WHERE vehicle_no=? COLLATE NOCASE', (vno,)).fetchone()
        if row:
            return row['id'], False
        cur = conn.execute('INSERT INTO vehicles (vehicle_no, type, created_by, created_at) VALUES (?,?,?,?)',
                           (vno, 'hired' if vno in hired else 'own', admin_id, now))
        return cur.lastrowid, True

    def party_id(name):
        row = conn.execute('SELECT id FROM parties WHERE name=? COLLATE NOCASE', (name,)).fetchone()
        if row:
            return row['id'], False
        cur = conn.execute('INSERT INTO parties (name, created_by, created_at) VALUES (?,?,?)', (name, admin_id, now))
        pid = cur.lastrowid
        vm = conn.execute('SELECT id FROM vendors WHERE name=? COLLATE NOCASE AND linked_party_id IS NULL', (name,)).fetchone()
        if vm:                                                       # same rule the app uses: a vendor of the same name shares the party's ledger
            conn.execute('UPDATE vendors SET linked_party_id=?, updated_by=?, updated_at=? WHERE id=?', (pid, admin_id, now, vm['id']))
        return pid, True

    hired_commission = 0.0
    existing = {str(r[0]).strip().upper() for r in conn.execute("SELECT lr_number FROM trips WHERE lr_number IS NOT NULL")}
    seen = set(existing)
    new_vehicles = new_parties = 0
    inserted = skipped = 0
    ours = collections.defaultdict(float)           # what we stored, by field
    theirs = collections.defaultdict(float)         # what RoadKing says
    billed_mismatch = []
    renamed = []
    inserted_lrs = []
    by_party = collections.defaultdict(float)
    rk_party_balance = {}

    cols = ['date', 'lr_number', 'vehicle_id', 'type', 'party_id', 'from_loc', 'to_loc', 'quantity', 'rate', 'driver_name', 'material', 'rate_type',
            'billed_amount', 'fixed_rate_amount', 'shortage_qty', 'payment_received', 'party_advance', 'end_date', 'end_time', 'shortage_date',
            'lr_received', 'remarks', 'is_empty', 'guarantee_qty', 'owner_rate_type', 'owner_fixed_amount', 'created_by', 'created_at'] + \
           list(CHARGES.values()) + list(DEDUCTIONS.values()) + list(EXPENSES.values())
    cols = list(dict.fromkeys(cols))
    insert_sql = f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"

    for r in data:
        lr = lr_text(r['LR Number'])
        key = lr.upper()
        if key in existing:
            skipped += 1
            continue
        remarks_extra = []
        if key in seen:                                             # RoadKing allowed the same LR twice; the app does not
            n = 2
            while f'{lr} ({n})'.upper() in seen:
                n += 1
            renamed.append((lr, f'{lr} ({n})'))
            remarks_extra.append(f'Same LR number appeared twice in RoadKing; this is the #{n} one')
            lr = f'{lr} ({n})'
            key = lr.upper()
        seen.add(key)

        vno = tidy(r['Vehicle No']).upper()
        vid, vnew = vehicle_id(vno)
        new_vehicles += vnew
        vtype = conn.execute('SELECT type FROM vehicles WHERE id=?', (vid,)).fetchone()['type']
        is_empty = lr.upper().startswith('EMPTY')
        pname = tidy(r['Party Name'])
        pid = None
        if pname and not is_empty:
            pid, pnew = party_id(pname)
            new_parties += pnew

        date, _ = parse_dt(r['Start Date Time'])
        status = str(r['Status']).strip().upper()
        end_date = end_time = None
        if status != 'STARTED':
            end_date, end_time = parse_dt(r['End Date Time'])
        rate_type = str(r['Rate Type']).strip().upper() or 'PER_MT'
        rate = num(r['Rate'])
        qty = (num(r['Unloading Weight']) if not blank(r['Unloading Weight']) else num(r['Loading Weight']))
        vals = {c: 0.0 for c in list(CHARGES.values()) + list(DEDUCTIONS.values()) + list(EXPENSES.values())}
        for src, dst in {**CHARGES, **DEDUCTIONS, **EXPENSES}.items():
            vals[dst] = num(r[src])
        vals['driver_payment'] = 0.0                                # the app no longer collects "driver payment"; RoadKing's column is empty anyway
        if is_empty:
            qty, rate, fixed, billed, received, rate_type_store = 0.0, 0.0, 0.0, 0.0, 0.0, 'PER_MT'
            for k in list(CHARGES.values()) + list(DEDUCTIONS.values()):
                vals[k] = 0.0
        else:
            fixed = rate if rate_type == 'FIXED' else 0.0
            rate_store = 0.0 if rate_type == 'FIXED' else rate
            freight = fixed if rate_type == 'FIXED' else qty * rate
            billed = freight + sum(vals[v] for v in CHARGES.values()) - sum(vals[v] for v in DEDUCTIONS.values())
            received = num(r['Total Payment Received'])
            rate = rate_store
            rate_type_store = rate_type
            if abs(billed - num(r['Adjusted Revenue'])) > 0.01:
                billed_mismatch.append((lr, round(billed, 2), num(r['Adjusted Revenue'])))
        owner_type, owner_amount = 'PER_MT', 0.0
        if vtype == 'hired' and not is_empty:
            commission = vals['agent_commission']
            owner_type = 'FIXED'
            owner_amount = commission if commission_only else commission + vals['fuel_amount'] + vals['driver_adv_amount']
            vals['agent_commission'] = 0.0                      # the commission IS the owner's amount, not a separate expense
        bits = []
        if not blank(r['Start Notes']):
            bits.append(f"Start: {tidy(r['Start Notes'])}")
        if not blank(r['Pod Notes']):
            bits.append(f"POD note: {tidy(r['Pod Notes'])}")
        if not blank(r['Pod Number']):
            bits.append(f"Bill/POD no: {lr_text(r['Pod Number'])}")
        sd, _ = parse_dt(r['Settlement Date'])
        if not blank(r['Settlement Notes']) or sd:
            bits.append('Settled' + (f' {sd}' if sd else '') + (f": {tidy(r['Settlement Notes'])}" if not blank(r['Settlement Notes']) else ''))
        bits += remarks_extra
        remarks = ' | '.join(bits) or None
        row = dict(date=date, lr_number=lr, vehicle_id=vid, type=vtype, party_id=pid, from_loc=tidy(r['From']) or None, to_loc=tidy(r['To']) or None,
                   quantity=qty, rate=rate, driver_name=tidy(r['Driver Name']) or None, material=(tidy(r['Material']) or None), rate_type=rate_type_store,
                   billed_amount=billed, fixed_rate_amount=fixed, shortage_qty=(0.0 if is_empty else num(r['Shortage Weight'])), payment_received=received,
                   party_advance=0.0, end_date=end_date, end_time=end_time,
                   shortage_date=(end_date if vals['shortage_amount'] else None),
                   lr_received=('Yes' if not blank(r['Submit POD Date']) else None), remarks=remarks, is_empty=1 if is_empty else 0,
                   guarantee_qty=0.0, owner_rate_type=owner_type, owner_fixed_amount=owner_amount, created_by=admin_id, created_at=now)
        row.update(vals)
        conn.execute(insert_sql, [row[c] for c in cols])
        inserted += 1
        inserted_lrs.append(lr)
        for k in ('billed_amount', 'payment_received'):
            ours[k] += row[k]
        for k in list(EXPENSES.values()):
            ours[k] += row[k]
        theirs['Adjusted Revenue'] += 0.0 if is_empty else num(r['Adjusted Revenue'])
        theirs['Total Payment Received'] += 0.0 if is_empty else num(r['Total Payment Received'])
        theirs['Total Expense'] += num(r['Total Expense'])
        theirs['Total Freight'] += num(r['Total Freight']) if 'Total Freight' in r else 0.0
        if vtype == 'hired':
            hired_commission += num(r['Expenses / Agent commission'])
        if pid:
            by_party[pname] += row['billed_amount'] - row['payment_received']
        if pname:
            rk_party_balance[pname] = num(r['Party Balance'])

    # ---- reconciliation (read straight back from the database)
    c1 = lambda q, *a: conn.execute(q, a).fetchone()[0]
    rec = collections.OrderedDict()
    rec['trips in the file'] = len(data)
    rec['trips loaded now'] = inserted
    rec['skipped (LR already in the database)'] = skipped
    rec['vehicles created'] = new_vehicles
    rec['  of which hired / own (in the whole fleet table)'] = f"{c1('SELECT COUNT(*) FROM vehicles WHERE type=?', 'hired')} / {c1('SELECT COUNT(*) FROM vehicles WHERE type=?', 'own')}"
    rec['parties created'] = new_parties
    rec['empty trips (is_empty = 1)'] = c1('SELECT COUNT(*) FROM trips WHERE is_empty=1')
    rec['hired-vehicle trips'] = c1("SELECT COUNT(*) FROM trips WHERE type='hired'")
    rec['trips with an end date (Completed) / open (Active)'] = f"{c1('SELECT COUNT(*) FROM trips WHERE end_date IS NOT NULL')} / {c1('SELECT COUNT(*) FROM trips WHERE end_date IS NULL')}"
    rec['LR received = Yes'] = c1("SELECT COUNT(*) FROM trips WHERE lr_received='Yes'")
    rec['date range'] = f"{c1('SELECT MIN(date) FROM trips')} to {c1('SELECT MAX(date) FROM trips')}"
    rec['billed total (our trips table)'] = round(c1('SELECT COALESCE(SUM(billed_amount),0) FROM trips'), 2)
    rec['  RoadKing Adjusted Revenue (non-empty trips)'] = round(theirs['Adjusted Revenue'], 2)
    rec['payment received total (our trips table)'] = round(c1('SELECT COALESCE(SUM(payment_received),0) FROM trips'), 2)
    rec['  RoadKing Total Payment Received'] = round(theirs['Total Payment Received'], 2)
    rec['pending from parties (billed - received)'] = round(c1('SELECT COALESCE(SUM(billed_amount - payment_received),0) FROM trips'), 2)
    rec['owner amount stored on hired trips (the hire cost)'] = round(c1("SELECT COALESCE(SUM(owner_fixed_amount),0) FROM trips WHERE type='hired'"), 2)
    rec['  RoadKing agent commission (hired trips)'] = round(hired_commission, 2)
    # the cost the app counts: own trips = every expense field; hired trips = the owner amount + the other expense fields (their fuel / driver
    # advance are the owner's money by the app's rule, so they are not counted again)
    all_exp = '+'.join(f'COALESCE({c},0)' for c in EXPENSES.values())
    other_exp = '+'.join(f'COALESCE({c},0)' for c in EXPENSES.values() if c not in ('fuel_amount', 'driver_adv_amount'))
    app_cost = c1(f"SELECT COALESCE(SUM(CASE WHEN type='hired' THEN COALESCE(owner_fixed_amount,0)+({other_exp}) ELSE ({all_exp}) END),0) FROM trips")
    rec['cost the app counts (own: all expenses; hired: owner amount + other)'] = round(app_cost, 2)
    rec['  RoadKing Total Expense'] = round(theirs['Total Expense'], 2)
    rec['  difference (expense RoadKing does not itemise)'] = round(theirs['Total Expense'] - app_cost, 2)
    h_rev, h_cost = c1("SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE type='hired'"), c1("SELECT COALESCE(SUM(owner_fixed_amount),0) FROM trips WHERE type='hired'")
    rec['hired vehicle profit as the dashboard computes it (billed - owner amount)'] = round(h_rev - h_cost, 2)
    bad_party = [(p, round(by_party.get(p, 0.0), 2), b) for p, b in rk_party_balance.items() if abs(by_party.get(p, 0.0) - b) > 1.0 and p in by_party]
    rec['parties whose balance differs from RoadKing Party Balance'] = len(bad_party)
    rec['trips where our billed amount differs from RoadKing Adjusted Revenue'] = len(billed_mismatch)
    rec['LR numbers that had to be renamed (duplicates)'] = len(renamed)

    if apply_:
        conn.commit()
        mode = 'SAVED'
    else:
        conn.rollback()
        mode = 'REHEARSAL ONLY — rolled back, nothing saved'
    conn.close()
    print(f'\n{mode}\n' + '=' * 78)
    for k, v in rec.items():
        print(f'{k:<68s} {v:>14,.2f}' if isinstance(v, float) else f'{k:<68s} {v!s:>14}')
    if bad_party:
        print('\nparties whose balance differs:', bad_party[:10])
    if billed_mismatch:
        print('\nbilled differs from RoadKing:', billed_mismatch[:10])
    if renamed:
        print('\nrenamed LRs:', renamed)
    return rec


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('xlsx')
    ap.add_argument('--db', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--commission-only', action='store_true', help='owner amount = the agent commission only (default: commission + fuel + driver advance)')
    a = ap.parse_args()
    run(a.xlsx, a.db, a.apply, a.commission_only)
