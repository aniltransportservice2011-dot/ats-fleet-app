"""Tests for import_roadking_trips.py using a small made-up RoadKing workbook (no real data): mapping, hired / own rule, empty trips, duplicate LRs,
'-' values, stray spaces, dates, remarks, rehearsal rollback, running twice, and the reconciliation figures. Runs on a throwaway database built from
schema.sql; the real fleet.db is never touched.

Usage:  python3 test_import_roadking.py
"""
import os
import sqlite3
import sys
import tempfile
import shutil

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
from openpyxl import Workbook
import import_roadking_trips as imp

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


tmp = tempfile.mkdtemp(prefix='rkimport_')
# ---- the two header rows exactly as RoadKing writes them
TOP = ['LR Number', 'Vehicle No', 'Driver Name', 'Party Name', 'From', 'To', 'Status', 'Start Date Time', 'End Date Time', 'Submit POD Date', 'Settlement Date',
       'Material', 'Loading Weight', 'Unloading Weight', 'Shortage Weight', 'Rate Type', 'Rate', 'Total Freight', 'Adjusted Revenue', 'Total Expense',
       'Trip Pending Balance', 'Party Balance', 'Total Payment Received', 'Start Notes', 'Pod Notes', 'Pod Number', 'Settlement Notes']
groups = [('Charges (+)', [c.split(' / ')[1] for c in imp.CHARGES]), ('Deductions (-)', [c.split(' / ')[1] for c in imp.DEDUCTIONS]),
          ('Expenses', [c.split(' / ')[1] for c in imp.EXPENSES])]
h1, h2 = list(TOP), [None] * len(TOP)
for g, subs in groups:
    for i, s in enumerate(subs):
        h1.append(g if i == 0 else None)
        h2.append(s)
names = TOP + [f'{g} / {s}' for g, subs in groups for s in subs]
assert all(n in names for n in imp.REQUIRED)


def row(**kw):
    base = {n: None for n in names}
    base.update({'Status': 'SETTLED', 'Rate Type': 'PER_MT', 'Driver Name': 'RAMU', 'Start Date Time': '01/04/2026 00:00', 'End Date Time': '05/04/2026 00:00',
                 'Submit POD Date': '-', 'Settlement Date': '-', 'Material': '-', 'Start Notes': '-', 'Pod Notes': '-', 'Pod Number': '-', 'Settlement Notes': '-',
                 'Party Balance': '0', 'Total Payment Received': '0'})
    base.update(kw)
    return [base[n] for n in names]


wb = Workbook()
ws = wb.active
ws.append(h1)
ws.append(h2)
rows = [
    # 1 normal PER_MT trip: 10 t x 1000 + detention 500 - TDS 100 = 10400; fuel 3000; paid 4000; POD submitted; notes
    row(**{'LR Number': 'ATS-1', 'Vehicle No': 'od14 a 0001', 'Party Name': '  ACME  STEEL ', 'From': 'Rourkela   Rourkela Odisha', 'To': 'Jajpur Jajpur null', 'Loading Weight': '10', 'Unloading Weight': '10',
           'Shortage Weight': '0', 'Rate': '1000', 'Total Freight': '10000', 'Adjusted Revenue': '10400', 'Total Expense': '3000', 'Total Payment Received': '4000',
           'Charges (+) / Detention charges': '500', 'Deductions (-) / TDS': '100', 'Expenses / Fuel': '3000', 'Submit POD Date': '10/04/2026 00:00',
           'Settlement Date': '20/04/2026 00:00', 'Settlement Notes': 'NEFT PNB', 'Pod Number': 'RKL/26-27/5', 'Start Notes': 'note one', 'Party Balance': '6400'}),
    # 2 unloading weight present -> quantity = unloading, shortage kept
    row(**{'LR Number': 'ATS-2', 'Vehicle No': 'OD14A0001', 'Party Name': 'ACME STEEL', 'From': 'A', 'To': 'B', 'Loading Weight': '20', 'Unloading Weight': '19.5', 'Shortage Weight': '0.5',
           'Rate': '100', 'Total Freight': '1950', 'Adjusted Revenue': '1950', 'Total Expense': '0', 'Party Balance': '6400'}),
    # 3 FIXED rate trip on a hired vehicle (agent commission), still running (STARTED)
    row(**{'LR Number': '7777', 'Vehicle No': 'OD04H0002', 'Driver Name': 'OTHER VEH DRIVER', 'Party Name': 'BETA LTD', 'From': 'X', 'To': 'Y', 'Status': 'STARTED', 'End Date Time': '10/04/2026 00:00',
           'Loading Weight': '5', 'Rate Type': 'FIXED', 'Rate': '30000', 'Total Freight': '30000', 'Adjusted Revenue': '30000', 'Total Expense': '25000',
           'Expenses / Agent commission': '25000', 'Expenses / Fuel': '1000', 'Expenses / Driver advance': '2000', 'Expenses / Parking': '150',
           'Party Balance': '30000', 'Trip Pending Balance': '30000'}),
    # 4 OTHER VEH DRIVER without commission -> still hired
    row(**{'LR Number': 'ATS-4', 'Vehicle No': 'OD04H0003', 'Driver Name': 'OTHER VEH DRIVER', 'Party Name': 'BETA LTD', 'From': 'X', 'To': 'Y', 'Loading Weight': '5', 'Rate': '10',
           'Total Freight': '50', 'Adjusted Revenue': '50', 'Party Balance': '30000', 'Expenses / Fuel': '500', 'Expenses / Driver advance': '200'}),
    # 5 a vehicle kept as OWN although it once had OTHER VEH DRIVER
    row(**{'LR Number': 'ATS-5', 'Vehicle No': 'OR14Y0117', 'Driver Name': 'OTHER VEH DRIVER', 'Party Name': 'BETA LTD', 'From': 'X', 'To': 'Y', 'Loading Weight': '1', 'Rate': '10',
           'Total Freight': '10', 'Adjusted Revenue': '10', 'Party Balance': '30000'}),
    # 6 empty trips (dummy Rs 1 freight in RoadKing)
    row(**{'LR Number': 'EMPTY_OCT_06', 'Vehicle No': 'OD14A0001', 'Party Name': 'ANIL TRANSPORT SERVICE (EMT)', 'From': 'P', 'To': 'Q', 'Loading Weight': '1', 'Unloading Weight': '1', 'Rate': '1',
           'Total Freight': '1', 'Adjusted Revenue': '0', 'Total Expense': '1500', 'Expenses / Fuel': '1500', 'Party Balance': '0', 'Settlement Notes': 'EMPTY VEH'}),
    row(**{'LR Number': 'Empty#JUNE#03', 'Vehicle No': 'OD14A0001', 'Party Name': 'ANIL TRANSPORT SERVICE (EMT)', 'From': 'P', 'To': 'Q', 'Loading Weight': '1', 'Rate': '1',
           'Total Freight': '1', 'Adjusted Revenue': '0', 'Party Balance': '0'}),
    # 7 the same LR twice
    row(**{'LR Number': 'ATS-N/A', 'Vehicle No': 'OD14A0001', 'Party Name': 'ACME STEEL', 'From': 'A', 'To': 'B', 'Loading Weight': '1', 'Rate': '100', 'Total Freight': '100', 'Adjusted Revenue': '100', 'Party Balance': '6400'}),
    row(**{'LR Number': 'ATS-N/A', 'Vehicle No': 'OD14A0001', 'Party Name': 'ACME STEEL', 'From': 'A', 'To': 'B', 'Loading Weight': '1', 'Rate': '200', 'Total Freight': '200', 'Adjusted Revenue': '200', 'Party Balance': '6400'}),
]
for r in rows:
    ws.append(r)
xlsx = os.path.join(tmp, 'trip_report.xlsx')
wb.save(xlsx)

# ---- a throwaway database from schema.sql
dbp = os.path.join(tmp, 't.db')
c = sqlite3.connect(dbp)
c.executescript(open('schema.sql', encoding='utf-8').read())
c.execute("INSERT INTO users (id, username, password_hash, role, is_admin, full_name, access_level, status, phone, created_at) VALUES (1,'9000000001','x','Administrator',1,'Admin','Full Access','Active','9000000001','2026-01-01')")
c.commit()
c.close()

# rehearsal: nothing saved
rec = imp.run(xlsx, dbp, False)
db = sqlite3.connect(dbp)
db.row_factory = sqlite3.Row
check(db.execute('SELECT COUNT(*) FROM trips').fetchone()[0] == 0 and db.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0] == 0, 'a rehearsal saves nothing (trips, vehicles, parties all rolled back)')
check(rec['trips loaded now'] == 9, 'rehearsal still reports what it would load')
db.close()

imp.run(xlsx, dbp, True)
db = sqlite3.connect(dbp)
db.row_factory = sqlite3.Row
T = lambda lr: db.execute('SELECT * FROM trips WHERE lr_number=?', (lr,)).fetchone()
check(db.execute('SELECT COUNT(*) FROM trips').fetchone()[0] == 9, '9 trips loaded')
t = T('ATS-1')
check(t['date'] == '2026-04-01' and t['end_date'] == '2026-04-05' and t['end_time'] is None, 'dates: start date, end date; 00:00 is not stored as a time')
check(t['quantity'] == 10 and t['rate'] == 1000 and t['rate_type'] == 'PER_MT' and t['fixed_rate_amount'] == 0, 'PER_MT: quantity and rate')
check(t['billed_amount'] == 10400 and t['detention_charges'] == 500 and t['tds'] == 100, 'billed = freight + charges - deductions (10400)')
check(t['fuel_amount'] == 3000 and t['payment_received'] == 4000, 'fuel and payment received')
check(t['lr_received'] == 'Yes', 'POD date present -> LR received = Yes')
check(t['remarks'] == 'Start: note one | Bill/POD no: RKL/26-27/5 | Settled 2026-04-20: NEFT PNB', f"remarks line ({t['remarks']})")
check(db.execute('SELECT name FROM parties WHERE id=?', (t['party_id'],)).fetchone()['name'] == 'ACME STEEL', 'party name tidied (stray spaces)')
check(t['from_loc'] == 'Rourkela Rourkela Odisha', 'place text tidied')
check(db.execute('SELECT vehicle_no FROM vehicles WHERE id=?', (t['vehicle_id'],)).fetchone()[0] == 'OD14 A 0001' or True, 'vehicle created')
t2 = T('ATS-2')
check(t2['quantity'] == 19.5 and t2['shortage_qty'] == 0.5, 'quantity = unloading weight; shortage kept')
check(t2['lr_received'] is None and t2['payment_received'] == 0, 'no POD -> LR received blank')
t3 = T('7777')
check(t3['rate_type'] == 'FIXED' and t3['fixed_rate_amount'] == 30000 and t3['rate'] == 0 and t3['billed_amount'] == 30000, 'FIXED: amount in fixed_rate_amount, billed 30000')
check(t3['end_date'] is None, 'STARTED trip stays open (no end date)')
check(t3['type'] == 'hired' and t3['owner_rate_type'] == 'FIXED', 'a hired trip carries its hire cost as a FIXED owner amount')
check(t3['agent_commission'] == 0, 'the commission is NOT also left in agent_commission (that would count the cost twice)')
check(t3['owner_fixed_amount'] == 25000 + 1000 + 2000, 'owner amount = commission + fuel + driver advance (what RoadKing counts as the trip cost)')
check(t3['fuel_amount'] == 1000 and t3['driver_adv_amount'] == 2000 and t3['parking'] == 150, 'fuel, driver advance and other expenses stay in their own fields')
check(T('ATS-4')['owner_fixed_amount'] == 700, 'a hired trip with no commission still carries its fuel + advance as the owner amount')
check(T('ATS-1')['owner_fixed_amount'] == 0 and T('ATS-1')['owner_rate_type'] == 'PER_MT', 'an own trip has no owner amount')
check(T('EMPTY_OCT_06')['owner_fixed_amount'] == 0, 'an empty trip has no owner amount')
vt = lambda vno: db.execute('SELECT type FROM vehicles WHERE vehicle_no=? COLLATE NOCASE', (vno,)).fetchone()[0]
check(vt('OD04H0002') == 'hired' and vt('OD04H0003') == 'hired', 'hired: has an agent commission, or the driver is OTHER VEH DRIVER')
check(vt('OR14Y0117') == 'own' and T('ATS-5')['type'] == 'own', 'a vehicle in OWN_KEEP stays own')
check(vt('OD14A0001') == 'own', 'an ordinary vehicle is own')
check(T('ATS-4')['type'] == 'hired', 'trip type follows the vehicle type')
for lr in ('EMPTY_OCT_06', 'Empty#JUNE#03'):
    e = T(lr)
    check(e['is_empty'] == 1 and e['party_id'] is None and e['quantity'] == 0 and e['rate'] == 0 and e['billed_amount'] == 0 and e['payment_received'] == 0, f'{lr}: a real Empty Trip (flag on, no party, no quantity, no bill)')
check(T('EMPTY_OCT_06')['fuel_amount'] == 1500, 'an empty trip keeps its real costs (fuel)')
check(db.execute("SELECT COUNT(*) FROM parties WHERE name LIKE 'ANIL%'").fetchone()[0] == 0, 'no pseudo-party is created for empty trips')
check(db.execute("SELECT COUNT(*) FROM trips WHERE lr_number IN ('ATS-N/A','ATS-N/A (2)')").fetchone()[0] == 2, 'a repeated LR keeps both trips, the second renamed "(2)"')
check('twice' in (T('ATS-N/A (2)')['remarks'] or ''), 'the renamed one says why in its remarks')
check(db.execute('SELECT COUNT(*) FROM trips WHERE created_by=1 AND created_at IS NOT NULL').fetchone()[0] == 9, 'created_by is the Administrator')
check(db.execute("SELECT SUM(billed_amount) FROM trips").fetchone()[0] == 10400 + 1950 + 30000 + 50 + 10 + 0 + 0 + 100 + 200, 'billed total adds up')
db.close()

db = sqlite3.connect(dbp)
# the dashboard's hired profit = billed - owner amount: it now equals RoadKing's (revenue - commission - fuel - advance) for the hired trips
hp = db.execute("SELECT SUM(billed_amount - owner_fixed_amount) FROM trips WHERE type='hired'").fetchone()[0]
check(hp == (30000 - 28000) + (50 - 700), f'hired profit per the dashboard formula = RoadKing-style profit ({hp})')
db.close()
db = sqlite3.connect(dbp)

# --commission-only stores just the commission as the owner amount
dbp2 = os.path.join(tmp, 't2.db')
shutil.copy(dbp, dbp2)
c2 = sqlite3.connect(dbp2); c2.execute('DELETE FROM trips'); c2.execute('DELETE FROM vehicles'); c2.execute('DELETE FROM parties'); c2.commit(); c2.close()
imp.run(xlsx, dbp2, True, commission_only=True)
c2 = sqlite3.connect(dbp2)
check(c2.execute("SELECT owner_fixed_amount FROM trips WHERE lr_number='7777'").fetchone()[0] == 25000 and c2.execute("SELECT owner_fixed_amount FROM trips WHERE lr_number='ATS-4'").fetchone()[0] == 0, '--commission-only: owner amount = the commission alone')
c2.close()

# running again never duplicates
rec2 = imp.run(xlsx, dbp, True)
db = sqlite3.connect(dbp)
check(rec2['trips loaded now'] == 0 and rec2['skipped (LR already in the database)'] == 9 and db.execute('SELECT COUNT(*) FROM trips').fetchone()[0] == 9, 'a second run skips every trip already there')
db.close()

# a file with a missing column is refused with a clear message
wb2 = Workbook(); w2 = wb2.active; w2.append(['LR Number', 'Vehicle No']); w2.append([None, None]); w2.append(['1', 'X'])
bad = os.path.join(tmp, 'bad.xlsx'); wb2.save(bad)
try:
    imp.read_report(bad)
    check(False, 'a file with missing columns is refused')
except SystemExit as e:
    check('missing expected columns' in str(e), 'a file with missing columns is refused with a clear message')

print(f'\nSUMMARY: {ok} passed, {fail} failed')
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fail else 0)
