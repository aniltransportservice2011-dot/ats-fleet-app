"""Load test for the fleet app (website + mobile /app/ screens).

Never touches the real fleet.db or the network: it builds its own large database from schema.sql,
fills it with realistic volumes, and measures how long every screen / report / button takes.

    python3 load_test.py seed   [--trips 12000] [--db PATH]     build the big database (cached)
    python3 load_test.py pages  [--db PATH] [--runs 5]           time every page + export + button action, in-process
    python3 load_test.py users  [--db PATH] [--secs 40]          real server (gunicorn, same settings as Render), concurrent users
    python3 load_test.py all                                     seed + pages + users, then write the report

Numbers are SERVER time (what the app itself spends building the response). A phone adds network and
rendering time on top, and Render's small instance is slower than a laptop, so read them as "relative to
each other and to the standard thresholds", and expect production to be a few times slower.
"""
import argparse
import datetime
import json
import os
import random
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import http.cookiejar

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
WORK = os.environ.get('LOAD_TEST_DIR') or os.path.join(tempfile.gettempdir(), 'fleet_load_test')
os.makedirs(WORK, exist_ok=True)
DEFAULT_DB = os.path.join(WORK, 'load.db')
RESULTS = os.path.join(WORK, 'results.json')
REPORT = os.path.join(REPO, 'load_test_report.md')

LOAD_USER = ('9000000777', 'load-test-pass-1')       # throwaway login that exists only in the load-test database

# ---------------------------------------------------------------------------------------------------
# 1. SEED
# ---------------------------------------------------------------------------------------------------
PLACES = ['Rourkela', 'Cuttack', 'Bhubaneswar', 'Jamshedpur', 'Kolkata', 'Raipur', 'Ranchi', 'Paradip', 'Haldia', 'Visakhapatnam', 'Angul', 'Jharsuguda',
          'Sambalpur', 'Bokaro', 'Dhanbad', 'Durgapur', 'Raurkela Steel Plant', 'Kalinganagar', 'Keonjhar', 'Barbil', 'Joda', 'Talcher', 'Bargarh', 'Rajgangpur',
          'Nagpur', 'Hyderabad', 'Chennai', 'Patna', 'Guwahati', 'Siliguri']
MATERIALS = ['Coal', 'Iron Ore', 'Steel Coil', 'Cement', 'Fly Ash', 'Sponge Iron', 'Pipes', 'Limestone', 'Manganese', 'Billets']
MAINT_CATS = ['Service', 'Service', 'Service', 'Tyres', 'Battery', 'Urea', 'Insurance', 'Repair', 'Permit', 'Fitness']
OVERHEAD_CATS = ['Office Rent', 'Electricity', 'Internet', 'Stationery', 'Insurance', 'Legal', 'Telephone', 'Fuel Station Rent', 'Tea & Snacks', 'Software', 'Audit Fees']


def _d(rnd, start, end):
    span = (end - start).days
    return (start + datetime.timedelta(days=rnd.randint(0, span))).isoformat()


def seed(db_path, n_trips=12000):
    t0 = time.time()
    if os.path.exists(db_path):
        os.remove(db_path)
    rnd = random.Random(20261010)
    c = sqlite3.connect(db_path)
    c.executescript(open(os.path.join(REPO, 'schema.sql')).read())
    start, end = datetime.date(2025, 4, 1), datetime.date(2026, 10, 10)
    now = '2026-10-10 09:00:00'

    from werkzeug.security import generate_password_hash
    c.execute("INSERT INTO users (id,username,password_hash,role,is_admin,full_name,access_level,status,phone,created_at) VALUES (1,?,?,?,1,'Load Tester','Full Access','Active',?,?)",
              (LOAD_USER[0], generate_password_hash(LOAD_USER[1]), 'Administrator', LOAD_USER[0], now))
    c.execute("INSERT INTO users (id,username,password_hash,role,is_admin,full_name,access_level,status,phone,created_at) VALUES (2,'9000000778',?,?,0,'Viewer','Read Only','Active','9000000778',?)",
              (generate_password_hash(LOAD_USER[1]), 'Staff', now))

    # masters --------------------------------------------------------------------------------------
    party_ids = []
    for i in range(1, 151):
        cur = c.execute("INSERT INTO parties (name,contact,address,email,credit_limit,since_date,opening_balance,gstin,category,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'Active',?)",
                        (f'Party {i:03d} {rnd.choice(["Steel", "Mining", "Cement", "Power", "Logistics", "Traders", "Industries"])} Pvt Ltd', f'98{rnd.randint(10000000, 99999999)}',
                         rnd.choice(PLACES), f'accounts{i}@party.example', rnd.choice([0, 500000, 1000000, 2500000]), _d(rnd, start, start + datetime.timedelta(days=60)),
                         rnd.choice([0, 0, 0, 25000, -12000, 80000]), f'21AAAC{i:04d}A1Z{rnd.randint(1, 9)}', rnd.choice(['Steel', 'Mining', 'Cement', 'Other']), now))
        party_ids.append(cur.lastrowid)
    vendor_ids = []
    for i in range(1, 101):
        cur = c.execute("INSERT INTO vendors (name,category,contact,address,credit_limit,since_date,opening_balance,gstin,status,created_at) VALUES (?,?,?,?,?,?,?,?,'Active',?)",
                        (f'Vendor {i:03d} {rnd.choice(["Motors", "Tyres", "Fuel Station", "Garage", "Auto Parts", "Transport", "Owner"])}', rnd.choice(['Fuel', 'Garage', 'Tyre', 'Owner', 'Parts']),
                         f'97{rnd.randint(10000000, 99999999)}', rnd.choice(PLACES), 0, _d(rnd, start, start + datetime.timedelta(days=60)),
                         rnd.choice([0, 0, 0, -15000, 20000]), f'21BBBCV{i:04d}B1Z{rnd.randint(1, 9)}', now))
        vendor_ids.append(cur.lastrowid)
    owner_vendor_ids = vendor_ids[:40]
    vehicles = []                                   # (id, type, vehicle_no)
    for i in range(1, 161):
        vtype = 'own' if i <= 90 else 'hired'
        vno = f'OD{rnd.randint(1, 35):02d}{rnd.choice("ABCDEFGH")}{rnd.choice("ABCDEFGH")}{1000 + i}'
        cur = c.execute("""INSERT INTO vehicles (vehicle_no,type,registration_date,capacity_mt,insurance_expiry,fitness_expiry,puc_valid_upto,permit_valid_upto,status,body_type,created_at)
                           VALUES (?,?,?,?,?,?,?,?,'Active',?,?)""",
                        (vno, vtype, _d(rnd, datetime.date(2015, 1, 1), datetime.date(2024, 12, 31)), rnd.choice([16, 20, 25, 30, 35]),
                         _d(rnd, datetime.date(2026, 8, 1), datetime.date(2027, 6, 30)), _d(rnd, datetime.date(2026, 8, 1), datetime.date(2027, 6, 30)),
                         _d(rnd, datetime.date(2026, 8, 1), datetime.date(2027, 3, 30)), _d(rnd, datetime.date(2026, 8, 1), datetime.date(2027, 9, 30)),
                         rnd.choice(['Open', 'Tipper', 'Closed', 'Trailer']), now))
        vehicles.append((cur.lastrowid, vtype, vno))
    own = [v for v in vehicles if v[1] == 'own']
    hired = [v for v in vehicles if v[1] == 'hired']
    employee_ids = []
    for i in range(1, 121):
        cur = c.execute("""INSERT INTO employees (name,type,opening_balance,employee_code,role,mobile,joining_date,status,basic_salary,created_at) VALUES (?,?,?,?,?,?,?,'Active',?,?)""",
                        (f'Employee {i:03d}', 'Driver' if i <= 80 else 'Staff', rnd.choice([0, 0, 5000, 12000]), f"{'DR' if i <= 80 else 'ST'}-{i:03d}",
                         'Driver' if i <= 80 else rnd.choice(['Accountant', 'Supervisor', 'Helper', 'Office']), f'96{rnd.randint(10000000, 99999999)}',
                         _d(rnd, datetime.date(2018, 1, 1), datetime.date(2025, 3, 31)), 'Driver' and rnd.choice([12000, 15000, 18000, 22000, 30000]), now))
        employee_ids.append(cur.lastrowid)
    emp_names = {r[0]: r[1] for r in c.execute("SELECT id,name FROM employees")}

    # trips ----------------------------------------------------------------------------------------
    trip_rows = []
    for i in range(1, n_trips + 1):
        veh = rnd.choice(own) if rnd.random() < 0.72 else rnd.choice(hired)
        vid, vtype, _ = veh
        pid = rnd.choice(party_ids[: int(len(party_ids) * (0.15 if rnd.random() < 0.6 else 1.0))])     # a few parties carry most of the volume
        date = _d(rnd, start, end)
        recipe = rnd.choice(['permt', 'permt', 'permt', 'fixed', 'guarantee', 'charged', 'shortage'])
        qty = round(rnd.uniform(6, 32), 2)
        rate = round(rnd.uniform(380, 2600), 2)
        fixed = round(rnd.uniform(8000, 90000), 0)
        guarantee = round(qty + rnd.uniform(1, 4), 2) if recipe == 'guarantee' else 0
        rate_type = 'FIXED' if recipe == 'fixed' else 'PER_MT'
        freight = fixed if rate_type == 'FIXED' else (guarantee or qty) * rate
        det = round(rnd.choice([0, 0, 0, 500, 1200]), 2) if recipe == 'charged' else 0
        load = round(rnd.choice([0, 0, 300, 600]), 2) if recipe == 'charged' else 0
        tds = round(rnd.choice([0, 0, 0, 200, 450]), 2) if recipe == 'charged' else 0
        short = round(rnd.choice([0, 400, 900, 1500]), 2) if recipe == 'shortage' else 0
        billed = round(freight + det + load - tds - short, 2)
        fuel = round(rnd.uniform(3000, 28000), 0) if rnd.random() < 0.85 else 0
        adv_d = round(rnd.uniform(500, 6000), 0) if rnd.random() < 0.6 else 0
        fuel_v = rnd.choice(vendor_ids) if rnd.random() < 0.5 else None
        adv_v = rnd.choice(vendor_ids) if (adv_d and rnd.random() < 0.3) else None
        party_adv = round(billed * rnd.choice([0, 0, 0.1, 0.3, 0.5]), 2)
        owner_v = rnd.choice(owner_vendor_ids) if vtype == 'hired' else None
        owner_fixed = round(billed * rnd.uniform(0.7, 0.88), 0) if vtype == 'hired' else 0
        paid_owner = round(owner_fixed * rnd.choice([0, 0, 0.2]), 2) if vtype == 'hired' else 0
        is_empty = 1 if rnd.random() < 0.05 else 0
        ended = rnd.random() < 0.82 and date < '2026-09-25'
        lr = f'EMP/{datetime.date.fromisoformat(date).strftime("%d%b%y").upper()}/{i % 97:02d}-{i}' if is_empty else f'LR-{i:06d}'
        trip_rows.append((date, lr, vid, vtype, pid, rnd.choice(PLACES), rnd.choice(PLACES), qty, guarantee, rate if rate_type == 'PER_MT' else 0,
                          f'Driver {rnd.randint(1, 80)}', rnd.choice(MATERIALS), rate_type, billed, det, load, tds, short, fuel, fuel_v, adv_d, adv_v, party_adv, 0.0,
                          'Owner' if owner_v else None, fixed if rate_type == 'FIXED' else 0, owner_v, owner_fixed if owner_v else 0, 'FIXED' if owner_v else 'PER_MT', paid_owner,
                          rnd.choice(['Yes', 'No']) if ended else None, date if ended else None, round(rnd.uniform(80, 900), 1) if ended else None,
                          rnd.randint(300, 1500) if rnd.random() < 0.1 else 0, is_empty, 1, now))
    c.executemany("""INSERT INTO trips (date,lr_number,vehicle_id,type,party_id,from_loc,to_loc,quantity,guarantee_qty,rate,driver_name,material,rate_type,billed_amount,
                     detention_charges,loading_charge,tds,shortage_amount,fuel_amount,fuel_vendor_id,driver_adv_amount,driver_adv_vendor_id,party_advance,payment_received,
                     owner_name,fixed_rate_amount,owner_vendor_id,owner_fixed_amount,owner_rate_type,paid_to_owner,lr_received,end_date,actual_km,parking,is_empty,created_by,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", trip_rows)
    # fix the two columns the multi-row insert mapped loosely (parking holds a small random amount)
    c.commit()

    # party payments, allocated oldest-first like the app does ------------------------------------------
    n_pay = 0
    for pid in party_ids:
        trips = c.execute("SELECT id,date,billed_amount,payment_received,party_advance FROM trips WHERE party_id=? ORDER BY date,id", (pid,)).fetchall()
        if not trips:
            continue
        pending = {t[0]: (t[2] or 0) - (t[3] or 0) - (t[4] or 0) for t in trips}
        total_pending = sum(pending.values())
        k = max(1, min(120, len(trips) // 4))
        target = total_pending * rnd.uniform(0.55, 0.95)
        for _ in range(k):
            amt = round(max(target / k * rnd.uniform(0.5, 1.5), 500), 2)
            pay_date = _d(rnd, start + datetime.timedelta(days=20), end)
            cur = c.execute("INSERT INTO payments (date,payment_type,amount,party_id,mode,reference_id,created_by,created_at,allocated_amount) VALUES (?,?,?,?,?,?,1,?,0)",
                            (pay_date, 'received', amt, pid, rnd.choice(['Cash', 'Bank Transfer', 'UPI', 'Cheque']), f'UTR{rnd.randint(10**9, 10**10)}' if rnd.random() < 0.7 else None, now))
            pay_id, remaining, alloc_total = cur.lastrowid, amt, 0.0
            for t in trips:
                if remaining <= 0.004:
                    break
                p = pending[t[0]]
                if p <= 0.004:
                    continue
                a = round(min(p, remaining), 2)
                c.execute("UPDATE trips SET payment_received=COALESCE(payment_received,0)+? WHERE id=?", (a, t[0]))
                c.execute("INSERT INTO payment_allocations (payment_id,trip_id,amount,created_by,created_at) VALUES (?,?,?,1,?)", (pay_id, t[0], a, now))
                pending[t[0]] -= a
                remaining -= a
                alloc_total += a
            c.execute("UPDATE payments SET allocated_amount=? WHERE id=?", (alloc_total, pay_id))
            n_pay += 1
    c.commit()

    # maintenance ----------------------------------------------------------------------------------
    maint_rows = []
    for i in range(4500):
        vid = rnd.choice(vehicles)[0]
        cat = rnd.choice(MAINT_CATS)
        amount = round(rnd.uniform(400, 70000), 0)
        paid = rnd.choice([0, amount, amount, round(amount * 0.4, 0)])
        maint_rows.append((_d(rnd, start, end), vid, cat, amount, paid, rnd.choice(vendor_ids), f'{cat} work', rnd.randint(10000, 900000), 'General Service' if cat == 'Service' else None,
                           rnd.choice(['Completed', 'Completed', 'In Progress']), 'New Tyre Fitted' if cat == 'Tyres' else None, f'TY{i:05d}' if cat == 'Tyres' else None,
                           rnd.choice(['FL', 'FR', 'RL', 'RR']) if cat == 'Tyres' else None, f'INV-{rnd.randint(1000, 99999)}', 1, now))
    c.executemany("""INSERT INTO maintenance (date,vehicle_id,category,amount,paid_amount,vendor_id,notes,km_reading,service_type,status,tyre_action,tyre_id,tyre_position,invoice_no,created_by,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", maint_rows)
    # vendor payments, allocated to hired-trip dues then maintenance
    n_vpay = 0
    for vid in vendor_ids:
        trips = c.execute("""SELECT id, owner_fixed_amount, COALESCE(paid_to_owner,0) FROM trips WHERE owner_vendor_id=? ORDER BY date,id""", (vid,)).fetchall()
        maints = c.execute("SELECT id, amount, COALESCE(paid_amount,0) FROM maintenance WHERE vendor_id=? ORDER BY date,id", (vid,)).fetchall()
        owed = sum(max((t[1] or 0) - t[2], 0) for t in trips) + sum(max(m[1] - m[2], 0) for m in maints)
        if owed <= 0:
            continue
        k = min(60, max(1, (len(trips) + len(maints)) // 6))
        for _ in range(k):
            amt = round(max(owed / k * rnd.uniform(0.4, 1.0), 500), 2)
            cur = c.execute("INSERT INTO payments (date,payment_type,amount,vendor_id,mode,reference_id,created_by,created_at,allocated_amount) VALUES (?,?,?,?,?,?,1,?,0)",
                            (_d(rnd, start + datetime.timedelta(days=20), end), 'paid', amt, vid, rnd.choice(['Cash', 'Bank Transfer', 'UPI']), f'VP{rnd.randint(10**8, 10**9)}', now))
            pay_id, remaining, alloc_total = cur.lastrowid, amt, 0.0
            for i2, t in enumerate(trips):
                if remaining <= 0.004:
                    break
                p = (t[1] or 0) - t[2]
                if p <= 0.004:
                    continue
                a = round(min(p, remaining), 2)
                c.execute("UPDATE trips SET paid_to_owner=COALESCE(paid_to_owner,0)+? WHERE id=?", (a, t[0]))
                c.execute("INSERT INTO payment_allocations (payment_id,trip_id,amount,created_by,created_at) VALUES (?,?,?,1,?)", (pay_id, t[0], a, now))
                trips[i2] = (t[0], t[1], t[2] + a)
                remaining -= a
                alloc_total += a
            for i2, m in enumerate(maints):
                if remaining <= 0.004:
                    break
                p = m[1] - m[2]
                if p <= 0.004:
                    continue
                a = round(min(p, remaining), 2)
                c.execute("UPDATE maintenance SET paid_amount=COALESCE(paid_amount,0)+? WHERE id=?", (a, m[0]))
                c.execute("INSERT INTO payment_allocations (payment_id,maintenance_id,amount,created_by,created_at) VALUES (?,?,?,1,?)", (pay_id, m[0], a, now))
                maints[i2] = (m[0], m[1], m[2] + a)
                remaining -= a
                alloc_total += a
            c.execute("UPDATE payments SET allocated_amount=? WHERE id=?", (alloc_total, pay_id))
            n_vpay += 1
    c.commit()

    # salaries, advances, attendance --------------------------------------------------------------------
    sal = []
    months = []
    y, m = 2025, 4
    while (y, m) <= (2026, 10):
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    for eid in employee_ids:
        basic = c.execute("SELECT basic_salary FROM employees WHERE id=?", (eid,)).fetchone()[0]
        for (yy, mm) in months:
            mk = f'{yy}-{mm:02d}'
            day = f'{yy}-{mm:02d}-{rnd.randint(1, 28):02d}'
            sal.append((emp_names[eid], datetime.date(yy, mm, 1).strftime('%b %Y'), basic, day, eid, mk, basic, basic, basic, 'paid', day, rnd.choice(['Cash', 'Bank Transfer'])))
    c.executemany("""INSERT INTO salaries (employee,month,amount,date,employee_id,month_key,basic_salary,gross_salary,net_salary,payment_status,payment_date,payment_mode,created_by,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'2026-10-10 09:00:00')""", sal)
    adv = []
    for _ in range(2500):
        eid = rnd.choice(employee_ids)
        adv.append((emp_names[eid], _d(rnd, start, end), rnd.choice([1000, 2000, 3000, 5000, 8000]), rnd.choice(['given', 'given', 'repaid']), 1, now))
    c.executemany("INSERT INTO advances (employee,date,amount,type,created_by,created_at) VALUES (?,?,?,?,?,?)", adv)
    att = []
    for eid in employee_ids:
        for d in range(200):
            day = (datetime.date(2026, 10, 10) - datetime.timedelta(days=d)).isoformat()
            att.append((eid, day, rnd.choices(['Present', 'Absent', 'Leave', 'Half Day'], [80, 8, 8, 4])[0], 'Admin', now, 1))
    c.executemany("INSERT INTO attendance (employee_id,date,status,marked_by,created_at,created_by) VALUES (?,?,?,?,?,?)", att)

    # expenses ---------------------------------------------------------------------------------------
    ov = []
    vendor_names = [r[0] for r in c.execute("SELECT name FROM vendors LIMIT 30")]
    for _ in range(3500):
        st = rnd.choice(['Paid', 'Paid', 'Paid', 'Pending'])
        v = rnd.choice(vendor_names) if rnd.random() < 0.5 else None
        ov.append((_d(rnd, start, end), rnd.choice(OVERHEAD_CATS), round(rnd.uniform(200, 45000), 0), rnd.choice(['Cash', 'Bank Transfer', 'UPI']), v, st,
                   1 if rnd.random() < 0.1 else 0, 'Monthly', _d(rnd, datetime.date(2026, 9, 1), datetime.date(2026, 12, 30)) if st == 'Pending' else None, 1, now))
    c.executemany("""INSERT INTO overheads (date,category,amount,payment_mode,vendor,status,is_recurring,recurring_frequency,due_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", ov)

    # tolls, urea, batteries, tyres, compliance, invoices -----------------------------------------------
    plazas = ['Barkote', 'Kharagpur', 'Jamshedpur', 'Kuakhia', 'Kolkata Bypass', 'Rourkela', 'Chandikhol', 'Raipur Ring']
    tolls = []
    for i in range(6000):
        vid = rnd.choice(vehicles)[0]
        tolls.append((_d(rnd, start, end), '10:30', vid, rnd.choice(plazas), 'NH-' + str(rnd.choice([16, 49, 6, 20])), 'Odisha', round(rnd.uniform(80, 1400), 0), rnd.choice(['manual', 'fastag']), 'approved', 1, now))
    c.executemany("INSERT INTO toll_entries (date,time,vehicle_id,toll_plaza,highway,state,amount,source,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", tolls)
    urea = []
    stock = 0
    for i in range(1800):
        if i % 3 == 0:
            urea.append((_d(rnd, start, end), 'stock_in', 'stock', f'B{i}', rnd.choice(vendor_ids), None, 1000.0, 85.0, 85000.0, 'Yard', 1, now))
        else:
            urea.append((_d(rnd, start, end), 'stock_out', 'stock', f'B{i}', None, rnd.choice(own)[0], round(rnd.uniform(5, 40), 2), 0, 0, 'Yard', 1, now))
    for u in urea:
        if u[1] == 'stock_in':
            c.execute("INSERT INTO urea_transactions (date,txn_type,source,batch_no,supplier_id,quantity_l,unit_price,total_value,location,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (u[0], u[1], u[2], u[3], u[4], u[6], u[7], u[8], u[9], u[10], u[11]))
        else:
            c.execute("INSERT INTO urea_transactions (date,txn_type,source,batch_no,vehicle_id,quantity_l,unit_price,total_value,location,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (u[0], u[1], u[2], u[3], u[5], u[6], u[7], u[8], u[9], u[10], u[11]))
    for i in range(500):
        c.execute("""INSERT INTO batteries (battery_no,brand,capacity_ah,battery_type,vehicle_id,install_date,purchase_date,purchase_price,vendor_id,warranty_months,health_pct,created_by,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                  (f'BAT-{i:04d}', rnd.choice(['Exide', 'Amaron', 'Luminous']), rnd.choice([100, 150, 180]), 'Lead Acid', rnd.choice(vehicles)[0],
                   _d(rnd, start, end), _d(rnd, start, end), round(rnd.uniform(6000, 14000), 0), rnd.choice(vendor_ids), 24, round(rnd.uniform(40, 100), 0), now))
    for i in range(600):
        c.execute("INSERT INTO tyre_stock (tyre_id,brand,tyre_type,purchase_date,purchase_cost,vendor_id,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,1,?)",
                  (f'ST{i:05d}', rnd.choice(['MRF', 'Apollo', 'CEAT', 'JK']), 'New', _d(rnd, start, end), round(rnd.uniform(14000, 30000), 0), rnd.choice(vendor_ids), rnd.choice(['In Stock', 'Installed']), now))
    for v in vehicles:
        c.execute("INSERT INTO insurance_policies (vehicle_id,insurance_type,policy_number,start_date,expiry_date,premium_amount,created_by,created_at) VALUES (?,?,?,?,?,?,1,?)",
                  (v[0], 'Comprehensive', f'POL{v[0]:05d}', '2026-01-01', _d(rnd, datetime.date(2026, 9, 1), datetime.date(2027, 6, 1)), round(rnd.uniform(18000, 60000), 0), now))
        for ctype in ('fitness', 'puc', 'permit'):
            c.execute("INSERT INTO vehicle_compliance (vehicle_id,compliance_type,document_number,valid_from,valid_upto,source,created_by) VALUES (?,?,?,?,?,?,1)",
                      (v[0], ctype, f'{ctype[:3].upper()}{v[0]:05d}', '2026-01-01', _d(rnd, datetime.date(2026, 8, 1), datetime.date(2027, 8, 1)), 'manual'))
    trip_ids = [r[0] for r in c.execute("SELECT id FROM trips ORDER BY id")]
    for i in range(400):
        pid = rnd.choice(party_ids[:30])
        cur = c.execute("INSERT INTO invoice_batches (invoice_number,invoice_type,party_id,invoice_date,status,payment_status,created_by,created_at) VALUES (?,?,?,?,'generated','PENDING',1,?)",
                        (f'ATS/INV/2026/{i + 1:04d}', 'party', pid, _d(rnd, start, end), now))
        for tid in rnd.sample(trip_ids, rnd.randint(4, 25)):
            c.execute("INSERT INTO invoice_batch_trips (invoice_batch_id,trip_id,created_by,created_at) VALUES (?,?,1,?)", (cur.lastrowid, tid, now))
    for tid in rnd.sample(trip_ids, 3000):
        c.execute("INSERT INTO invoice_items (trip_id,description,amount,item_type,created_by,created_at) VALUES (?,?,?,?,1,?)",
                  (tid, rnd.choice(['Detention', 'Late fee', 'Loading', 'Discount']), round(rnd.uniform(100, 3000), 0), rnd.choice(['charge', 'deduction']), now))
    for i in range(8000):
        c.execute("INSERT INTO access_logs (user_id,event,date) VALUES (1,'Login',?)", (_d(rnd, start, end),))
    c.commit()
    c.execute("ANALYZE")
    counts = {t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
              ('parties', 'vendors', 'vehicles', 'employees', 'trips', 'payments', 'payment_allocations', 'maintenance', 'salaries', 'advances', 'attendance', 'overheads',
               'toll_entries', 'urea_transactions', 'batteries', 'tyre_stock', 'invoice_batches', 'invoice_batch_trips', 'invoice_items')}
    c.close()
    counts['db_size_mb'] = round(os.path.getsize(db_path) / 1e6, 1)
    counts['seed_seconds'] = round(time.time() - t0, 1)
    json.dump(counts, open(os.path.join(WORK, 'seed_counts.json'), 'w'))
    return counts


# ---------------------------------------------------------------------------------------------------
# 2. PAGES (in-process, one request at a time — pure server cost of each screen)
# ---------------------------------------------------------------------------------------------------
def _pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0
    k = (len(vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _boot(db_path):
    os.environ['DATABASE_PATH'] = db_path
    import requests
    requests.get = requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network blocked in load test'))
    import app as appmod
    appmod.app.config['PROPAGATE_EXCEPTIONS'] = False
    appmod.DB = db_path
    return appmod


def _biggest(c, sql):
    r = c.execute(sql).fetchone()
    return r[0] if r else 1


def discover_urls(appmod, db_path):
    """Every GET screen in the app, with realistic parameters, plus filtered / paged variants of the heavy lists."""
    c = sqlite3.connect(db_path)
    ids = {
        'trip_id': _biggest(c, "SELECT id FROM trips ORDER BY id DESC LIMIT 1"),
        'party_id': _biggest(c, "SELECT party_id FROM trips GROUP BY party_id ORDER BY COUNT(*) DESC LIMIT 1"),
        'vendor_id': _biggest(c, "SELECT owner_vendor_id FROM trips WHERE owner_vendor_id IS NOT NULL GROUP BY owner_vendor_id ORDER BY COUNT(*) DESC LIMIT 1"),
        'vehicle_id': _biggest(c, "SELECT vehicle_id FROM trips GROUP BY vehicle_id ORDER BY COUNT(*) DESC LIMIT 1"),
        'v_id': _biggest(c, "SELECT vehicle_id FROM trips GROUP BY vehicle_id ORDER BY COUNT(*) DESC LIMIT 1"),
        'm_id': _biggest(c, "SELECT id FROM maintenance ORDER BY id DESC LIMIT 1"),
        'o_id': _biggest(c, "SELECT id FROM overheads ORDER BY id DESC LIMIT 1"),
        'batch_id': _biggest(c, "SELECT id FROM invoice_batches ORDER BY id DESC LIMIT 1"),
        'payment_id': _biggest(c, "SELECT id FROM payments ORDER BY id DESC LIMIT 1"),
        'employee_id': 1, 'user_id': 1, 'battery_id': 1, 'entry_id': 1, 'txn_id': 1, 'policy_id': 1, 'stock_id': 1, 'item_id': 1, 'salary_id': 1, 'account_id': 1, 'company_id': 1,
    }
    ids['employee'] = c.execute("SELECT name FROM employees WHERE id=1").fetchone()[0]
    party_name = c.execute("SELECT name FROM parties WHERE id=?", (ids['party_id'],)).fetchone()[0]
    vehicle_no = c.execute("SELECT vehicle_no FROM vehicles WHERE id=?", (ids['vehicle_id'],)).fetchone()[0]
    ids['entity_id'] = ids['party_id']
    c.close()
    urls = []
    skip = ('/static', '/logout', '/login', '/app/login', '/app/signup', '/internal', '/route-finder', '/manifest', '/sw.js', '/favicon', '/app/logout', '/toll/template', '/sync')
    for rule in appmod.app.url_map.iter_rules():
        if 'GET' not in rule.methods or rule.endpoint == 'static' or any(k in rule.rule for k in skip):
            continue
        url = rule.rule

        def sub(m):
            name = m.group(1)
            if name == 'entity_type' or name == 'role':
                return 'party'
            return str(ids.get(name, 1))
        url = re.sub(r'<(?:[a-z]+:)?([a-z_]+)>', sub, url)
        urls.append(url)
    q = urllib.parse.quote
    extra = [
        '/trips?per_page=100', '/trips?page=2', '/trips?page=40', '/trips?page=120&per_page=100', f'/trips?party={q(party_name)}', f'/trips?vehicle={q(vehicle_no)}',
        '/trips?status=active', '/trips?date_from=2026-04-01&date_to=2026-09-30', '/trips?lr_number=LR-0123', '/trips?search=Rourkela&per_page=100',
        '/accounts?per_page=100', '/accounts?role=Party', '/accounts?search=Steel',
        f'/ledger/party/{ids["party_id"]}?per_page=100', f'/ledger/party/{ids["party_id"]}?page=5', f'/ledger/party/{ids["party_id"]}?date_from=2026-04-01&date_to=2026-09-30',
        f'/ledger/vendor/{ids["vendor_id"]}?per_page=100', '/maintenance?tab=service&per_page=100', '/maintenance?tab=tyres', '/maintenance?tab=battery', '/maintenance?tab=urea', '/maintenance?tab=toll',
        '/maintenance?tab=toll&per_page=100&page=30', '/maintenance?tab=compliance', '/maintenance?date_from=2025-04-01&date_to=2026-10-10', '/overheads?per_page=100', '/overheads?status=Pending',
        '/overheads?tab=recurring', '/salaries?tab=salary', '/salaries?tab=attendance', '/salaries?tab=overview&per_page=50',
        '/dashboard?date_from=2025-04-01&date_to=2026-10-10', '/dashboard?date_from=2026-10-01&date_to=2026-10-10', f'/dashboard?vehicle={q(vehicle_no)}',
        '/business-performance?date_from=2025-04-01&date_to=2026-10-10', '/route-analytics?period=fy', '/fleet-utilization?period=fy', '/performance?period=fy',
        f'/invoice-center?invoice_type=party&party_id={ids["party_id"]}', f'/invoice-center?invoice_type=vehicle_owner&vendor_id={ids["vendor_id"]}', '/invoices/generated?search=ATS',
        '/app/trips?show=100', '/app/trips?show=500', '/app/ledger?show=100', f'/app/ledger/party/{ids["party_id"]}?show=100', '/app/maintenance?tab=service&show=100',
        '/app/expenses?show=100', '/app/salary?show=100', '/app/analytics?period=fy', '/app/business-performance?period=fy', '/app/performance?period=fy',
        '/trips/export', '/accounts/export', f'/ledger/party/{ids["party_id"]}/export', f'/ledger/party/{ids["party_id"]}/export/pdf', f'/ledger/vendor/{ids["vendor_id"]}/export',
        f'/ledger/vendor/{ids["vendor_id"]}/export/pdf', '/overheads/export', '/maintenance/export', '/trips/export/pdf', '/maintenance/export/pdf', '/performance/export',
        '/business-performance/export?date_from=2025-04-01&date_to=2026-10-10', '/route-analytics/export', '/fleet-utilization/export', '/export',
    ]
    return sorted(set(urls)) + [u for u in extra if u not in urls], ids


def run_pages(db_path, runs=5, only=None):
    appmod = _boot(db_path)
    counter = {'n': 0}
    _orig = appmod.get_db

    def counting_get_db():
        conn = _orig()
        try:
            conn._conn.set_trace_callback(lambda s: counter.__setitem__('n', counter['n'] + 1))
        except Exception:
            pass
        return conn
    appmod.get_db = counting_get_db
    cl = appmod.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'], s['username'], s['is_admin'] = 1, LOAD_USER[0], 1
    urls, ids = discover_urls(appmod, db_path)
    if only:
        urls = [u for u in urls if re.search(only, u)]
    rows = []
    for i, url in enumerate(urls, 1):
        times, status, size, queries = [], None, 0, 0
        try:
            cl.get(url, follow_redirects=True)           # warm-up (OS cache, template compile)
            for _ in range(runs):
                counter['n'] = 0
                t0 = time.perf_counter()
                r = cl.get(url, follow_redirects=True)
                times.append((time.perf_counter() - t0) * 1000)
                status, size, queries = r.status_code, len(r.data), counter['n']
        except Exception as e:
            status, size = f'EXC {type(e).__name__}', 0
            times = times or [0]
        kind = 'export' if re.search(r'export|/pdf|download', url) else ('app' if url.startswith('/app/') else 'website')
        rows.append({'url': url, 'kind': kind, 'status': status, 'size_kb': round(size / 1024, 1), 'queries': queries,
                     'median_ms': round(statistics.median(times), 1), 'p95_ms': round(_pct(times, 95), 1), 'max_ms': round(max(times), 1), 'min_ms': round(min(times), 1)})
        if i % 20 == 0:
            print(f'  {i}/{len(urls)} screens timed…', flush=True)
    return rows


def run_actions(db_path, runs=5):
    """The write buttons: Save on a new trip / payment / expense etc., timed on the big database."""
    appmod = _boot(db_path)
    cl = appmod.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'], s['username'], s['is_admin'] = 1, LOAD_USER[0], 1
    c = sqlite3.connect(db_path)
    party_id = c.execute("SELECT party_id FROM trips GROUP BY party_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    vendor_id = c.execute("SELECT owner_vendor_id FROM trips WHERE owner_vendor_id IS NOT NULL GROUP BY owner_vendor_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    party_name = c.execute("SELECT name FROM parties WHERE id=?", (party_id,)).fetchone()[0]
    vendor_name = c.execute("SELECT name FROM vendors WHERE id=?", (vendor_id,)).fetchone()[0]
    own_v = c.execute("SELECT vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()[0]
    hired_v = c.execute("SELECT vehicle_no FROM vehicles WHERE type='hired' LIMIT 1").fetchone()[0]
    emp = c.execute("SELECT name FROM employees WHERE id=1").fetchone()[0]
    active_trip = c.execute("SELECT id FROM trips WHERE end_date IS NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    c.close()
    seq = {'n': 0}

    def nxt():
        seq['n'] += 1
        return seq['n']

    def trip_form():
        n = nxt()
        return {'date': '2026-10-10', 'lr_number': f'LOADTEST-{os.getpid()}-{n}-{int(time.time() * 1000) % 100000}', 'vehicle_no': own_v, 'type': 'own', 'party_name': party_name,
                'from_loc': 'Rourkela', 'to_loc': 'Cuttack', 'rate_type': 'PER_MT', 'quantity': '20', 'rate': '1000', 'party_advance': '1000', 'fuel_amount': '5000',
                'driver_adv_amount': '1000', 'material': 'Coal'}
    actions = [
        ('Add Trip (save)', lambda: cl.post('/trips/add', data=trip_form())),
        ('Add Trip — mobile app (save)', lambda: cl.post('/app/trips/add', data=trip_form())),
        ('Add Hired Trip (save)', lambda: cl.post('/trips/add', data={**trip_form(), 'vehicle_no': hired_v, 'type': 'hired', 'owner_name': vendor_name, 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '14000'})),
        ('Add party payment — nothing ticked (auto-allocates across ~hundreds of trips)', lambda: cl.post(f'/payment/party/{party_id}', data={'date': '2026-10-10', 'amount': f'{1000 + nxt()}', 'mode': 'Cash'})),
        ('Add party payment — mobile app', lambda: cl.post(f'/app/ledger/party/{party_id}/payment', data={'date': '2026-10-10', 'amount': f'{1000 + nxt()}', 'mode': 'Cash'})),
        ('Add vendor payment (trips + maintenance + bills)', lambda: cl.post(f'/payment/vendor/{vendor_id}', data={'date': '2026-10-10', 'amount': f'{1000 + nxt()}', 'mode': 'Cash'})),
        ('Add expense (save)', lambda: cl.post('/overheads/add', data={'date': '2026-10-10', 'category': 'Office Rent', 'amount': f'{500 + nxt()}', 'status': 'Pending', 'vendor': vendor_name, 'payment_mode': 'Cash'})),
        ('Add maintenance bill (save)', lambda: cl.post('/maintenance/add', data={'date': '2026-10-10', 'vehicle_no': own_v, 'category': 'Service', 'amount': f'{2000 + nxt()}', 'paid_amount': '0', 'vendor_name': vendor_name})),
        ('Add salary payment (save)', lambda: cl.post(f'/employee/{emp}', data={'entry_kind': 'salary', 'date': '2026-10-10', 'amount': '15000', 'payment_mode': 'Cash'})),
        ('Add advance (save)', lambda: cl.post(f'/employee/{emp}', data={'entry_kind': 'given', 'date': '2026-10-10', 'amount': '2000'})),
        ('Edit a trip (save)', lambda: cl.post(f'/trips/edit/{active_trip}', data={**trip_form(), 'lr_number': f'LT-EDIT-{active_trip}', 'vehicle_no': own_v})),
        ('Invoice PDF preview (batch of 25 trips)', lambda: cl.post('/invoice-center/generate', data={'trip_ids': [str(i) for i in range(1, 26)], 'invoice_type': 'party', 'party_id': str(party_id), 'mode': 'preview', 'invoice_date': '2026-10-10'})),
        ('Invoice PDF preview (200 trips)', lambda: cl.post('/invoice-center/generate', data={'trip_ids': [str(i) for i in range(1, 201)], 'invoice_type': 'party', 'party_id': str(party_id), 'mode': 'preview', 'invoice_date': '2026-10-10'})),
    ]
    out = []
    for name, fn in actions:
        times, status = [], None
        for _ in range(runs):
            t0 = time.perf_counter()
            r = fn()
            times.append((time.perf_counter() - t0) * 1000)
            status = r.status_code
        out.append({'url': name, 'kind': 'action', 'status': status, 'size_kb': 0, 'queries': 0, 'median_ms': round(statistics.median(times), 1),
                    'p95_ms': round(_pct(times, 95), 1), 'max_ms': round(max(times), 1), 'min_ms': round(min(times), 1)})
    return out


# ---------------------------------------------------------------------------------------------------
# 3. CONCURRENT USERS (real server, same gunicorn settings as Render)
# ---------------------------------------------------------------------------------------------------
class VirtualUser(threading.Thread):
    def __init__(self, base, urls_weighted, writes, stop_at, results, rnd_seed):
        super().__init__(daemon=True)
        self.base, self.urls, self.writes, self.stop_at, self.results = base, urls_weighted, writes, stop_at, results
        self.rnd = random.Random(rnd_seed)
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def login(self):
        data = urllib.parse.urlencode({'username': LOAD_USER[0], 'password': LOAD_USER[1]}).encode()
        self.op.open(urllib.request.Request(self.base + '/login', data=data), timeout=60).read()

    def hit(self, method, path, data=None):
        t0 = time.perf_counter()
        status = 0
        try:
            req = urllib.request.Request(self.base + path, data=data, method=method)
            with self.op.open(req, timeout=90) as r:
                r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception:
            status = -1
        self.results.append((path.split('?')[0] if method == 'GET' else f'POST {path.split("?")[0]}', (time.perf_counter() - t0) * 1000, status))

    def run(self):
        try:
            self.login()
        except Exception:
            self.results.append(('login', 0, -1))
            return
        while time.time() < self.stop_at:
            if self.writes and self.rnd.random() < 0.10:
                path, data = self.rnd.choice(self.writes)
                self.hit('POST', path, urllib.parse.urlencode(data(self.rnd)).encode())
            else:
                self.hit('GET', self.rnd.choice(self.urls))
            time.sleep(self.rnd.uniform(0.2, 0.9))          # a person reads the screen between clicks


def run_users(db_path, secs=40, levels=(1, 5, 10, 25, 50)):
    port = 5097
    base = f'http://127.0.0.1:{port}'
    appmod = _boot(db_path)
    urls, ids = discover_urls(appmod, db_path)
    c = sqlite3.connect(db_path)
    party_name = c.execute("SELECT name FROM parties WHERE id=?", (ids['party_id'],)).fetchone()[0]
    own_v = c.execute("SELECT vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()[0]
    c.close()
    weights = ['/dashboard'] * 6 + ['/trips'] * 8 + ['/trips?page=3', '/trips?per_page=100', f'/trips?party={urllib.parse.quote(party_name)}'] * 2 + ['/accounts'] * 3 + [f'/ledger/party/{ids["party_id"]}'] * 3 + \
              ['/maintenance', '/maintenance?tab=service', '/overheads', '/salaries', f'/trips/{ids["trip_id"]}/view', '/business-performance', '/invoices/generated'] + \
              ['/app/dashboard'] * 6 + ['/app/trips'] * 5 + ['/app/ledger'] * 3 + ['/app/expenses', '/app/maintenance', '/app/salary'] + [f'/app/trips/{ids["trip_id"]}', f'/app/ledger/party/{ids["party_id"]}']

    def new_trip(rnd):
        return {'date': '2026-10-10', 'lr_number': f'LU-{rnd.randint(1, 10**9)}', 'vehicle_no': own_v, 'type': 'own', 'party_name': party_name, 'rate_type': 'PER_MT', 'quantity': '20', 'rate': '1000'}
    writes = [('/trips/add', new_trip), (f'/payment/party/{ids["party_id"]}', lambda r: {'date': '2026-10-10', 'amount': str(r.randint(500, 5000)), 'mode': 'Cash'}),
              ('/overheads/add', lambda r: {'date': '2026-10-10', 'category': 'Office Rent', 'amount': str(r.randint(100, 900)), 'status': 'Paid', 'payment_mode': 'Cash'})]
    out = []
    for n_users in levels:
        work_db = os.path.join(WORK, f'users_{n_users}.db')
        for suffix in ('', '-wal', '-shm'):
            if os.path.exists(work_db + suffix):
                os.remove(work_db + suffix)
        import shutil
        shutil.copy2(db_path, work_db)
        env = dict(os.environ, DATABASE_PATH=work_db)
        srv = subprocess.Popen([sys.executable, '-m', 'gunicorn', 'app:app', '--worker-class=gthread', '--workers=2', '--threads=4', '--timeout=90', f'--bind=127.0.0.1:{port}',
                                '--log-level=warning'], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=open(os.path.join(WORK, f'gunicorn_{n_users}.log'), 'w'))
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + '/login', timeout=2).read()
                    break
                except Exception:
                    time.sleep(0.5)
            results = []
            stop_at = time.time() + secs
            users = [VirtualUser(base, weights, writes, stop_at, results, 1000 + i) for i in range(n_users)]
            t0 = time.time()
            for u in users:
                u.start()
            for u in users:
                u.join(timeout=secs + 120)
            elapsed = time.time() - t0
            ok = [r for r in results if r[2] and 200 <= r[2] < 400]
            lat = [r[1] for r in ok]
            errs = [r for r in results if not (r[2] and 200 <= r[2] < 400)]
            by_page = {}
            for p, ms, st in ok:
                by_page.setdefault(p, []).append(ms)
            slowest = sorted(((p, statistics.median(v), _pct(v, 95), len(v)) for p, v in by_page.items() if len(v) >= 3), key=lambda x: -x[2])[:6]
            out.append({'users': n_users, 'secs': round(elapsed, 1), 'requests': len(results), 'rps': round(len(results) / elapsed, 1), 'errors': len(errs),
                        'error_kinds': sorted({f'{r[0]} → {r[2]}' for r in errs})[:5], 'p50_ms': round(_pct(lat, 50), 0), 'p95_ms': round(_pct(lat, 95), 0), 'p99_ms': round(_pct(lat, 99), 0),
                        'max_ms': round(max(lat), 0) if lat else 0, 'slowest': [(p, round(m), round(p95), n) for p, m, p95, n in slowest]})
            print(f"  {n_users:>3} users: {out[-1]['requests']} requests, {out[-1]['rps']} req/s, p50 {out[-1]['p50_ms']:.0f} ms, p95 {out[-1]['p95_ms']:.0f} ms, errors {out[-1]['errors']}", flush=True)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=15)
            except Exception:
                srv.kill()
    return out


# ---------------------------------------------------------------------------------------------------
# 4. REPORT
# ---------------------------------------------------------------------------------------------------
def grade(ms):
    if ms < 100:
        return 'Excellent'
    if ms < 300:
        return 'Good'
    if ms < 1000:
        return 'OK'
    if ms < 3000:
        return 'Slow'
    return 'Poor'


def write_report(results):
    counts = json.load(open(os.path.join(WORK, 'seed_counts.json')))
    pages = results.get('pages', [])
    users = results.get('users', [])
    L = []
    L.append('# Load test report — fleet app (website + mobile app)\n')
    L.append(f"Run: {datetime.datetime.now().strftime('%d %b %Y %H:%M')} · test data: {counts['trips']:,} trips, {counts['payments']:,} payments "
             f"({counts['payment_allocations']:,} allocations), {counts['maintenance']:,} maintenance bills, {counts['salaries']:,} salaries, {counts['overheads']:,} expenses, "
             f"{counts['attendance']:,} attendance rows, {counts['toll_entries']:,} toll entries, {counts['parties']} parties, {counts['vendors']} vendors, {counts['vehicles']} vehicles "
             f"({counts['db_size_mb']} MB database). Machine: Mac, 8 cores. Numbers are server time only.\n")
    L.append('Standard thresholds used: **< 100 ms Excellent · < 300 ms Good · < 1 s OK · 1–3 s Slow · > 3 s Poor** (the usual web guideline is that a page should answer within ~1 s).\n')
    for kind, title in (('website', 'Website screens'), ('app', 'Mobile app screens'), ('export', 'Downloads (Excel / PDF)'), ('action', 'Buttons that save data')):
        rows = sorted([r for r in pages if r['kind'] == kind], key=lambda r: -r['median_ms'])
        if not rows:
            continue
        L.append(f'\n## {title}\n')
        g = {}
        for r in rows:
            g[grade(r['median_ms'])] = g.get(grade(r['median_ms']), 0) + 1
        L.append('Summary: ' + ' · '.join(f'{k} {v}' for k, v in sorted(g.items(), key=lambda kv: ['Excellent', 'Good', 'OK', 'Slow', 'Poor'].index(kv[0]))) + f' (of {len(rows)})\n')
        L.append('| Screen / action | Typical (median) ms | Slowest of 5 ms | SQL queries | Page size KB | Rating | HTTP |')
        L.append('|---|---:|---:|---:|---:|---|---|')
        for r in rows:
            L.append(f"| `{r['url']}` | {r['median_ms']:,.0f} | {r['max_ms']:,.0f} | {r['queries'] or ''} | {r['size_kb'] or ''} | {grade(r['median_ms'])} | {r['status']} |")
    if users:
        L.append('\n## Many people at once (real server, same settings as Render: 2 workers × 4 threads)\n')
        L.append('Each virtual user logs in, then clicks around like a person would (1–2 clicks a second at most, ~10% of clicks are saves).\n')
        L.append('| Users at once | Requests | Requests/sec | Typical (p50) ms | p95 ms | p99 ms | Slowest ms | Errors |')
        L.append('|---:|---:|---:|---:|---:|---:|---:|---:|')
        for u in users:
            L.append(f"| {u['users']} | {u['requests']:,} | {u['rps']} | {u['p50_ms']:,.0f} | {u['p95_ms']:,.0f} | {u['p99_ms']:,.0f} | {u['max_ms']:,.0f} | {u['errors']} |")
        L.append('\nSlowest screens under the heaviest load:\n')
        last = users[-1]
        for p, m, p95, n in last['slowest']:
            L.append(f'- `{p}` — typical {m:,} ms, p95 {p95:,} ms ({n} requests)')
        if any(u['errors'] for u in users):
            L.append('\nErrors seen: ' + '; '.join(sorted({k for u in users for k in u['error_kinds']})))
    open(REPORT, 'w').write('\n'.join(L) + '\n')
    return REPORT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('phase', choices=['seed', 'pages', 'users', 'all', 'report'])
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--trips', type=int, default=12000)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--secs', type=int, default=40)
    ap.add_argument('--only', default=None)
    a = ap.parse_args()
    results = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    if a.phase in ('seed', 'all'):
        print('seeding…', flush=True)
        print(json.dumps(seed(a.db, a.trips)), flush=True)
    if a.phase in ('pages', 'all'):
        print('timing every screen…', flush=True)
        results['pages'] = run_pages(a.db, a.runs, a.only) + (run_actions(a.db, a.runs) if not a.only else [])
        json.dump(results, open(RESULTS, 'w'))
    if a.phase in ('users', 'all'):
        print('concurrent users…', flush=True)
        results['users'] = run_users(a.db, a.secs)
        json.dump(results, open(RESULTS, 'w'))
    if a.phase in ('report', 'all', 'pages', 'users'):
        print('report:', write_report(results))


if __name__ == '__main__':
    main()
