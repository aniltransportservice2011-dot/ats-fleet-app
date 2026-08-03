from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import datetime
import calendar

app = Flask(__name__)
app.secret_key = 'fleet-local-app-anil-transport-secret-key-2026'
app.permanent_session_lifetime = datetime.timedelta(days=30)
DB = 'fleet.db'

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def _vehicle_active_idle_days(conn, vehicle_id, registration_date, date_from, date_to):
    """Active days = union of calendar days spanned by each trip's start..end_date (a trip stays
    'active' from when it's entered until it's explicitly ended, not just on its start date).
    Idle days = the remainder of the window, anchored no earlier than the vehicle's registration
    date so a newly bought vehicle isn't counted idle for days before it existed."""
    anchor = registration_date if registration_date and registration_date > date_from else date_from
    if anchor > date_to:
        return 0, 0, 0
    total_days_v = (datetime.datetime.strptime(date_to, '%Y-%m-%d').date() -
                     datetime.datetime.strptime(anchor, '%Y-%m-%d').date()).days + 1
    trip_rows = conn.execute("""SELECT date, end_date FROM trips WHERE vehicle_id=? AND date<=?
                                AND (end_date IS NULL OR end_date>=?) ORDER BY date""",
                              (vehicle_id, date_to, anchor)).fetchall()
    active_dates = set()
    for t in trip_rows:
        start = t['date']
        end = t['end_date'] or t['date']
        if end < start:
            end = start
        s = max(start, anchor)
        e = min(end, date_to)
        if s > e:
            continue
        d = datetime.datetime.strptime(s, '%Y-%m-%d').date()
        e_d = datetime.datetime.strptime(e, '%Y-%m-%d').date()
        while d <= e_d:
            active_dates.add(d.isoformat())
            d += datetime.timedelta(days=1)
    active_days = len(active_dates)
    idle_days = max(total_days_v - active_days, 0)
    return active_days, idle_days, total_days_v

def _period_financials(conn, date_from, date_to):
    """Revenue/expense/profit breakdown for a date range, using the same charge
    columns as dashboard()/monthly_summary() so the numbers agree app-wide."""
    trips = conn.execute("SELECT * FROM trips WHERE date>=? AND date<=?", (date_from, date_to)).fetchall()
    revenue = sum(t['billed_amount'] or 0 for t in trips)
    fuel = sum(t['fuel_amount'] or 0 for t in trips)
    driver_adv = sum(t['driver_adv_amount'] or 0 for t in trips)
    toll = sum(t['toll'] or 0 for t in trips)
    parking = sum(t['parking'] or 0 for t in trips)
    misc = sum((t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) +
               (t['fine'] or 0) + (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) +
               (t['loading_expense'] or 0) + (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) +
               (t['weighbridge_charges'] or 0) + (t['other_expense'] or 0) + (t['permit_charges'] or 0) for t in trips)
    owner_cost = sum((t['fixed_rate_amount'] or 0) if t['rate_type'] == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
                      for t in trips if t['type'] == 'Market')
    maint = conn.execute("SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    overheads = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    salaries = conn.execute("SELECT COALESCE(SUM(amount),0) FROM salaries WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    total_expenses = fuel + driver_adv + toll + parking + misc + owner_cost + maint + overheads + salaries
    net_profit = revenue - total_expenses
    margin = round(net_profit / revenue * 100, 2) if revenue else 0
    party_ids = set(t['party_id'] for t in trips if t['party_id'])
    return {
        'trips': trips, 'trip_count': len(trips), 'revenue': revenue, 'fuel': fuel, 'driver_adv': driver_adv,
        'toll': toll, 'parking': parking, 'misc': misc, 'owner_cost': owner_cost, 'maint': maint,
        'overheads': overheads, 'salaries': salaries, 'total_expenses': total_expenses,
        'net_profit': net_profit, 'margin': margin, 'party_ids': party_ids,
    }

def _pct_growth(curr, prev):
    if prev:
        return round((curr - prev) / abs(prev) * 100, 2)
    return None

def _shift_period_back(date_from, date_to):
    """Previous period of the same length, immediately before date_from."""
    d_from = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
    d_to = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    length = (d_to - d_from).days + 1
    prev_to = d_from - datetime.timedelta(days=1)
    prev_from = prev_to - datetime.timedelta(days=length - 1)
    return prev_from.isoformat(), prev_to.isoformat()

def _shift_period_year(date_from, date_to):
    def back_a_year(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:
            return d - datetime.timedelta(days=365)
    d_from = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
    d_to = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    return back_a_year(d_from).isoformat(), back_a_year(d_to).isoformat()

def _month_bounds(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1).isoformat(), datetime.date(year, month, last_day).isoformat()

def _quarter_bounds(d):
    q_start_month = (d.month - 1) // 3 * 3 + 1
    start = datetime.date(d.year, q_start_month, 1)
    end_month = q_start_month + 2
    last_day = calendar.monthrange(d.year, end_month)[1]
    end = datetime.date(d.year, end_month, last_day)
    return start.isoformat(), end.isoformat()

def get_or_create_party(conn, name):
    if not name or not name.strip():
        return None
    name = name.strip()
    row = conn.execute("SELECT id FROM parties WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO parties (name) VALUES (?)", (name,))
    party_id = cur.lastrowid
    # A vendor record with the same name may already exist (e.g. created first via
    # Maintenance/Fuel). Link it here too, so both sides share one combined ledger
    # instead of the org getting a second, disconnected balance.
    vendor_match = conn.execute(
        "SELECT id FROM vendors WHERE name = ? COLLATE NOCASE AND linked_party_id IS NULL", (name,)).fetchone()
    if vendor_match:
        conn.execute("UPDATE vendors SET linked_party_id = ? WHERE id = ?", (party_id, vendor_match[0]))
    return party_id

def get_or_create_vendor(conn, name):
    if not name or not name.strip():
        return None
    name = name.strip()
    row = conn.execute("SELECT id FROM vendors WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    # If this name already exists as a Party, link this vendor record to that party
    # instead of creating a fully independent one — keeps ledgers combined.
    party_match = conn.execute("SELECT id FROM parties WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if party_match:
        existing_link = conn.execute("SELECT id FROM vendors WHERE linked_party_id = ?", (party_match[0],)).fetchone()
        if existing_link:
            return existing_link[0]
        cur = conn.execute("INSERT INTO vendors (name, linked_party_id) VALUES (?,?)", (name, party_match[0]))
        return cur.lastrowid
    cur = conn.execute("INSERT INTO vendors (name) VALUES (?)", (name,))
    return cur.lastrowid

@app.route('/accounts')
def accounts():
    conn = get_db()
    search_f = request.args.get('search', '')
    role_f = request.args.get('role', '')

    parties_bal = conn.execute("""SELECT p.id, p.name, p.contact, p.email, p.address, p.credit_limit,
        (SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(payment_received)+SUM(party_advance),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE party_id=p.id AND payment_type='received') +
        COALESCE(p.opening_balance,0) as balance
        FROM parties p ORDER BY p.name""").fetchall()

    vendors_bal = conn.execute("""SELECT v.id, v.name, v.contact, v.email, v.address, v.credit_limit,
        (SELECT COALESCE(SUM(m.amount),0) FROM maintenance m WHERE m.vendor_id=v.id) +
        (SELECT COALESCE(SUM(fuel_amount),0) FROM trips WHERE fuel_vendor_id=v.id) +
        (SELECT COALESCE(SUM(driver_adv_amount),0) FROM trips WHERE driver_adv_vendor_id=v.id) +
        (SELECT COALESCE(SUM(CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE owner_rate*quantity END),0) FROM trips WHERE owner_vendor_id=v.id) -
        (SELECT COALESCE(SUM(m.paid_amount),0) FROM maintenance m WHERE m.vendor_id=v.id) -
        (SELECT COALESCE(SUM(paid_to_owner),0) FROM trips WHERE owner_vendor_id=v.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE vendor_id=v.id AND payment_type='paid') -
        COALESCE(v.opening_balance,0) as balance
        FROM vendors v WHERE v.linked_party_id IS NULL ORDER BY v.name""").fetchall()
    conn.close()

    rows = []
    for p in parties_bal:
        rows.append({'id': p['id'], 'name': p['name'], 'role': 'Party', 'balance': p['balance'] or 0,
                      'contact': p['contact'], 'email': p['email'], 'address': p['address'], 'credit_limit': p['credit_limit']})
    for v in vendors_bal:
        # Vendor's raw balance is costs-minus-paid (positive = we owe them).
        # Negate so positive consistently means "receivable" and negative means "payable", matching parties.
        rows.append({'id': v['id'], 'name': v['name'], 'role': 'Vendor', 'balance': -(v['balance'] or 0),
                      'contact': v['contact'], 'email': v['email'], 'address': v['address'], 'credit_limit': v['credit_limit']})

    if search_f:
        rows = [r for r in rows if search_f.lower() in r['name'].lower()]
    if role_f:
        rows = [r for r in rows if r['role'] == role_f]
    rows.sort(key=lambda r: r['name'])

    return render_template('home.html', rows=rows, f_search=search_f, f_role=role_f, active='accounts')

@app.route('/')
def home():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    vehicle_f = request.args.get('vehicle', '')

    trip_query = "SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id WHERE t.date>=? AND t.date<=?"
    params = [date_from, date_to]
    if vehicle_f:
        trip_query += " AND v.vehicle_no=?"
        params.append(vehicle_f)
    trips = conn.execute(trip_query, params).fetchall()

    total_revenue = sum(t['billed_amount'] or 0 for t in trips)
    trip_count = len(trips)
    total_charges_paid = sum((t['fuel_amount'] or 0)+(t['driver_adv_amount'] or 0)+(t['toll'] or 0)+
                              (t['agent_commission'] or 0)+(t['builty_expense'] or 0)+(t['conductor_expense'] or 0)+
                              (t['fine'] or 0)+(t['labour_charges'] or 0)+(t['parking'] or 0)+(t['puncture'] or 0)+
                              (t['urea'] or 0)+(t['loading_expense'] or 0)+(t['unloading_expense'] or 0)+
                              (t['wear_tear'] or 0)+(t['weighbridge_charges'] or 0)+(t['other_expense'] or 0) for t in trips)
    adj_revenue = total_revenue - total_charges_paid

    maint_total_query = "SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id WHERE m.date>=? AND m.date<=?"
    maint_total_params = [date_from, date_to]
    if vehicle_f:
        maint_total_query += " AND v.vehicle_no=?"
        maint_total_params.append(vehicle_f)
    maint_total = conn.execute(maint_total_query, maint_total_params).fetchone()[0]
    salaries_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM salaries WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    overheads_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    total_expenses = total_charges_paid + maint_total + overheads_total
    total_profit = total_revenue - total_expenses

    all_vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE type IS NOT NULL").fetchall()
    running_vnos = set(t['vehicle_no'] for t in trips if t['vehicle_no'])
    running_count = len(running_vnos)
    idle_count = len(all_vehicles) - running_count

    own_vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type IN ('Line','Local')").fetchall()
    own_running_ids = set(t['vehicle_id'] for t in trips if t['type'] in ('Line','Local'))
    own_idle_count = sum(1 for v in own_vehicles if v['id'] not in own_running_ids)
    own_vehicle_count = len(own_vehicles)

    fuel_total = sum(t['fuel_amount'] or 0 for t in trips)
    driveradv_total = sum(t['driver_adv_amount'] or 0 for t in trips)
    toll_total = sum(t['toll'] or 0 for t in trips)
    other_exp_total = total_charges_paid - fuel_total - driveradv_total - toll_total

    exp_items = [('Fuel', fuel_total, '#2a78d6'), ('Driver Advance', driveradv_total, '#eb6834'),
                 ('Toll', toll_total, '#eda100'), ('Maintenance', maint_total, '#e34948'),
                 ('Salaries', salaries_total, '#1a9c5b'), ('Expenses', overheads_total, '#7a5ad6'),
                 ('Other', other_exp_total, '#4a3aa7')]
    exp_max = max([v for _, v, _ in exp_items], default=1) or 1
    exp_breakdown = [{'label': l, 'value': v, 'color': c, 'pct': round((v/exp_max)*100, 1)} for l, v, c in exp_items]

    # Vehicle-wise revenue, own vehicles only (Line + Local)
    veh_revenue = {}
    for t in trips:
        if t['type'] in ('Line', 'Local') and t['vehicle_no']:
            veh_revenue[t['vehicle_no']] = veh_revenue.get(t['vehicle_no'], 0) + (t['billed_amount'] or 0)
    veh_rev_list = sorted(veh_revenue.items(), key=lambda x: x[1], reverse=True)[:10]
    veh_rev_max = max([v for _, v in veh_rev_list], default=1) or 1
    vehicle_revenue = [{'vehicle_no': vn, 'revenue': rev, 'pct': round((rev/veh_rev_max)*100, 1)} for vn, rev in veh_rev_list]

    parties_bal = conn.execute("""SELECT p.name,
        (SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(payment_received)+SUM(party_advance),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE party_id=p.id AND payment_type='received') +
        COALESCE(p.opening_balance,0) as balance
        FROM parties p ORDER BY balance DESC""").fetchall()
    receivables = [r for r in parties_bal if r['balance'] and r['balance'] > 0]
    total_receivables = sum(r['balance'] for r in receivables)

    vendors_bal = conn.execute("""SELECT v.name,
        (SELECT COALESCE(SUM(m.amount),0) FROM maintenance m WHERE m.vendor_id=v.id) +
        (SELECT COALESCE(SUM(fuel_amount),0) FROM trips WHERE fuel_vendor_id=v.id) +
        (SELECT COALESCE(SUM(driver_adv_amount),0) FROM trips WHERE driver_adv_vendor_id=v.id) -
        (SELECT COALESCE(SUM(m.paid_amount),0) FROM maintenance m WHERE m.vendor_id=v.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE vendor_id=v.id AND payment_type='paid') -
        COALESCE(v.opening_balance,0) as balance
        FROM vendors v ORDER BY balance DESC""").fetchall()
    payables = [v for v in vendors_bal if v['balance'] and v['balance'] > 0]
    total_payables = sum(v['balance'] for v in payables)

    own_vehicle_turnover = sum(t['billed_amount'] or 0 for t in trips if t['type'] in ('Line','Local'))

    market_trips = [t for t in trips if t['type'] == 'Market']
    market_trip_count = len(market_trips)
    market_billed = sum(t['billed_amount'] or 0 for t in market_trips)
    # Owner's actual contracted cost (fixed rate, or owner_rate x quantity) — not what's been paid
    # to them so far, so the margin holds regardless of whether that payment is still pending.
    market_owner_cost = sum(
        (t['fixed_rate_amount'] or 0) if t['rate_type'] == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        for t in market_trips)
    market_vehicle_profit = market_billed - market_owner_cost

    market_veh_data = {}
    for t in market_trips:
        vn = t['vehicle_no'] or 'Unassigned'
        if vn not in market_veh_data:
            market_veh_data[vn] = {'revenue': 0, 'trips': 0}
        market_veh_data[vn]['revenue'] += t['billed_amount'] or 0
        market_veh_data[vn]['trips'] += 1
    market_veh_list = sorted(market_veh_data.items(), key=lambda x: x[1]['revenue'], reverse=True)[:10]
    market_veh_max = max([d['revenue'] for _, d in market_veh_list], default=1) or 1
    market_vehicle_chart = [{'vehicle_no': vn, 'revenue': d['revenue'], 'trips': d['trips'],
                              'pct': round((d['revenue']/market_veh_max)*100, 1)} for vn, d in market_veh_list]

    recent_trips = conn.execute("""SELECT t.date, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, p.name as party_name,
                                    t.driver_name, t.billed_amount
                                    FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                    LEFT JOIN parties p ON t.party_id=p.id
                                    ORDER BY t.date DESC LIMIT 8""").fetchall()

    vehicles_list = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()

    fy_map = {'2026-04-01|2027-03-31': ('2026-04-01','2027-03-31'), '2025-04-01|2026-03-31': ('2025-04-01','2026-03-31')}
    f_fy = ''
    for key, (fy_from, fy_to) in fy_map.items():
        if date_from == fy_from and date_to == fy_to:
            f_fy = key
            break

    maint_query = """SELECT m.category, SUM(m.amount) as total FROM maintenance m
                     LEFT JOIN vehicles v ON m.vehicle_id=v.id
                     WHERE m.date>=? AND m.date<=?"""
    maint_params = [date_from, date_to]
    if vehicle_f:
        maint_query += " AND v.vehicle_no=?"
        maint_params.append(vehicle_f)
    maint_query += " GROUP BY m.category ORDER BY total DESC LIMIT 8"
    maint_by_category = conn.execute(maint_query, maint_params).fetchall()
    maint_cat_max = max([m['total'] for m in maint_by_category], default=1) or 1
    maint_by_category = [{'category': m['category'], 'total': m['total'],
                           'pct': round((m['total'] / maint_cat_max) * 100, 1)} for m in maint_by_category]

    # Fleet status donut (own vehicles): Running / Idle
    fleet_total = own_vehicle_count or 1
    fleet_running_pct = round((own_vehicle_count - own_idle_count) / fleet_total * 100, 1)

    # Top performing vehicles table (own vehicles, by revenue)
    top_vehicles = []
    for vn, rev in veh_rev_list[:5]:
        vtrips = sum(1 for t in trips if t['vehicle_no'] == vn)
        top_vehicles.append({'vehicle_no': vn, 'trips': vtrips, 'revenue': rev})

    # Real alerts from real data (not fake insurance/fitness dates we don't track)
    alerts = []
    for r in receivables[:3]:
        alerts.append({'text': f"{r['name']} balance overdue", 'sub': f"₹{r['balance']:,.0f} pending"})
    if own_idle_count > 0:
        alerts.append({'text': f"{own_idle_count} own vehicle(s) currently idle", 'sub': 'Check Idle Tracker for details'})

    conn.close()
    return render_template('dashboard.html',
        total_revenue=total_revenue, adj_revenue=adj_revenue, total_charges_paid=total_charges_paid,
        total_expenses=total_expenses, total_profit=total_profit, trip_count=trip_count,
        running_count=running_count, idle_count=idle_count, own_vehicle_turnover=own_vehicle_turnover,
        market_trip_count=market_trip_count, market_vehicle_profit=market_vehicle_profit, market_vehicle_chart=market_vehicle_chart,
        own_idle_count=own_idle_count, own_vehicle_count=own_vehicle_count,
        fuel_total=fuel_total, driveradv_total=driveradv_total, toll_total=toll_total, other_exp_total=other_exp_total,
        maint_total=maint_total, salaries_total=salaries_total,
        receivables=receivables[:5], total_receivables=total_receivables,
        payables=payables[:5], total_payables=total_payables,
        recent_trips=recent_trips, vehicles=vehicles_list,
        f_date_from=date_from, f_date_to=date_to, f_vehicle=vehicle_f, f_fy=f_fy,
        maint_by_category=maint_by_category, exp_breakdown=exp_breakdown, vehicle_revenue=vehicle_revenue,
        fleet_running_pct=fleet_running_pct, top_vehicles=top_vehicles, alerts=alerts, active='dashboard')

@app.route('/vehicles')
def vehicles_list():
    conn = get_db()
    type_f = request.args.get('type', '')
    query = """SELECT v.id, v.vehicle_no, v.type, v.registration_date, v.capacity_mt,
               v.insurance_expiry, v.fitness_expiry, v.notes,
               (SELECT COUNT(*) FROM trips WHERE vehicle_id=v.id) as trip_count,
               (SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE vehicle_id=v.id) as total_billed
               FROM vehicles v WHERE v.type IS NOT NULL"""
    params = []
    if type_f:
        query += " AND v.type = ?"
        params.append(type_f)
    query += " ORDER BY v.type, v.vehicle_no"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('vehicles_list.html', rows=rows, f_type=type_f, active='vehicles')

@app.route('/salaries')
def salaries_list():
    conn = get_db()
    emp_f = request.args.get('employee', '')
    type_f = request.args.get('emp_type', '')
    query = "SELECT id, name, type, opening_balance FROM employees WHERE 1=1"
    params = []
    if emp_f:
        query += " AND name = ?"
        params.append(emp_f)
    if type_f:
        query += " AND type = ?"
        params.append(type_f)
    query += " ORDER BY name"
    rows = conn.execute(query, params).fetchall()
    rows_with_balance = []
    for r in rows:
        given = conn.execute("SELECT COALESCE(SUM(amount),0) FROM advances WHERE employee=? COLLATE NOCASE AND type='given'", (r['name'],)).fetchone()[0]
        repaid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM advances WHERE employee=? COLLATE NOCASE AND type='repaid'", (r['name'],)).fetchone()[0]
        rows_with_balance.append({'id': r['id'], 'name': r['name'], 'type': r['type'],
                                   'outstanding': given - repaid + (r['opening_balance'] or 0)})
    employees = conn.execute("SELECT name FROM employees ORDER BY name").fetchall()
    total_employees = len(rows_with_balance)
    total_outstanding = sum(r['outstanding'] for r in rows_with_balance)
    conn.close()
    return render_template('salaries_list.html', rows=rows_with_balance, employees=employees, f_employee=emp_f, f_type=type_f,
                            total_employees=total_employees, total_outstanding=total_outstanding, active='salaries')

@app.route('/overheads')
def overheads_list():
    conn = get_db()
    cat_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = "SELECT id, date, category, amount, notes, payment_mode, receipt_number FROM overheads WHERE 1=1"
    params = []
    if cat_f:
        query += " AND category = ?"
        params.append(cat_f)
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM overheads ORDER BY category").fetchall()

    total_amount = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads").fetchone()[0]
    total_count = conn.execute("SELECT COUNT(*) FROM overheads").fetchone()[0]
    import datetime
    this_month = datetime.datetime.now().strftime('%Y-%m')
    this_month_amount = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE substr(date,1,7)=?", (this_month,)).fetchone()[0]
    month_count = conn.execute("SELECT COUNT(DISTINCT substr(date,1,7)) FROM overheads").fetchone()[0]
    avg_per_month = total_amount / month_count if month_count > 0 else 0

    conn.close()
    return render_template('overheads_list.html', rows=rows, categories=categories,
                            f_category=cat_f, f_date_from=date_from, f_date_to=date_to,
                            total_amount=total_amount, total_count=total_count,
                            this_month_amount=this_month_amount, avg_per_month=avg_per_month, active='overheads')

@app.route('/trips')
def trips_list():
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    lr_f = request.args.get('lr_received', '')
    from_f = request.args.get('from_loc', '')
    to_f = request.args.get('to_loc', '')
    type_f = request.args.get('type', '')

    query = """SELECT t.id, t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received, t.type,
               t.payment_received, t.party_advance,
               t.end_date, t.end_time, t.actual_km, t.shortage_qty, t.shortage_unit, t.shortage_amount, t.shortage_remarks, t.remarks
               FROM trips t
               LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id
               WHERE 1=1"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no LIKE ?"
        params.append(f"%{vehicle_f}%")
    if party_f:
        query += " AND p.name LIKE ?"
        params.append(f"%{party_f}%")
    if date_from:
        query += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND t.date <= ?"
        params.append(date_to)
    if lr_f == 'received':
        query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received':
        query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    if from_f:
        query += " AND t.from_loc LIKE ?"
        params.append(f"%{from_f}%")
    if to_f:
        query += " AND t.to_loc LIKE ?"
        params.append(f"%{to_f}%")
    if type_f:
        query += " AND t.type = ?"
        params.append(type_f)

    where_clause = query[query.find("WHERE"):]

    total_shown = conn.execute(f"SELECT COALESCE(SUM(t.billed_amount),0) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    total_count = conn.execute(f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    pending_total = conn.execute(f"SELECT COALESCE(SUM(t.billed_amount-COALESCE(t.payment_received,0)-COALESCE(t.party_advance,0)),0) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    lr_received_count = conn.execute(f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause} AND t.lr_received='Yes'", params).fetchone()[0]
    lr_pending_count = total_count - lr_received_count

    page = max(1, int(request.args.get('page', 1)))
    per_page = int(request.args.get('per_page', 50))
    if per_page not in (10, 25, 50, 100):
        per_page = 50
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    query += f" ORDER BY t.date DESC LIMIT {per_page} OFFSET {offset}"

    trips = conn.execute(query, params).fetchall()
    vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()
    parties = conn.execute("SELECT DISTINCT name FROM parties ORDER BY name").fetchall()
    conn.close()

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    page_tokens = []
    if total_pages <= 7:
        page_tokens = list(range(1, total_pages + 1))
    else:
        page_tokens = [1]
        if page > 3:
            page_tokens.append('...')
        for p in range(max(2, page - 1), min(total_pages, page + 1) + 1):
            page_tokens.append(p)
        if page < total_pages - 2:
            page_tokens.append('...')
        if total_pages not in page_tokens:
            page_tokens.append(total_pages)

    return render_template('trips_list.html', trips=trips, total_shown=total_shown, total_count=total_count,
                            pending_total=pending_total, lr_received_count=lr_received_count, lr_pending_count=lr_pending_count,
                            page=page, total_pages=total_pages, per_page=per_page, base_qs=base_qs, page_tokens=page_tokens,
                            vehicles=vehicles, parties=parties,
                            f_vehicle=vehicle_f, f_party=party_f, f_date_from=date_from, f_date_to=date_to,
                            f_lr=lr_f, f_from=from_f, f_to=to_f, f_type=type_f, active='trips')

@app.route('/trips/add', methods=['GET', 'POST'])
def add_trip():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        def get_or_create_vehicle(vno):
            if not vno or not vno.strip():
                return None
            vno = vno.strip()
            row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
            if row:
                return row[0]
            cur = conn.execute("INSERT INTO vehicles (vehicle_no) VALUES (?)", (vno,))
            return cur.lastrowid
        def n(key):
            return float(f.get(key) or 0)

        vehicle_id = get_or_create_vehicle(f.get('vehicle_no'))
        party_id = get_or_create_party(conn, f.get('party_name'))
        fuel_vendor_id = get_or_create_vendor(conn, f.get('fuel_vendor'))
        driveradv_vendor_id = get_or_create_vendor(conn, f.get('driver_adv_vendor'))
        owner_vendor_id = get_or_create_vendor(conn, f.get('owner_name')) if f.get('owner_name') else None
        misc_vendor_id = get_or_create_vendor(conn, f.get('misc_vendor'))

        quantity = n('quantity')
        rate = n('rate')
        rate_type = f.get('rate_type')
        fixed_rate_amount = n('fixed_rate_amount')

        freight = fixed_rate_amount if rate_type == 'FIXED' else quantity * rate

        total_charges = (n('driver_payment')+n('detention_charges')+n('gps_cost')+n('loading_charge')+
                          n('unloading_charge')+n('police_charges')+n('sim_tracking')+n('union_charges')+
                          n('weight_charges')+n('other_charges'))
        total_deductions = (n('brokerage')+n('builty_commission')+n('late_fees')+n('material_damage')+
                             n('shortage_amount')+n('tds')+n('other_deductions'))
        billed_amount = freight + total_charges - total_deductions

        total_expense = (n('fuel_amount')+n('driver_adv_amount')+n('agent_commission')+n('builty_expense')+
                          n('conductor_expense')+n('fine')+n('labour_charges')+n('parking')+n('puncture')+
                          n('toll')+n('urea')+n('loading_expense')+n('unloading_expense')+n('wear_tear')+
                          n('weighbridge_charges')+n('other_expense'))

        cols = ['date','lr_number','vehicle_id','type','party_id','from_loc','to_loc','quantity','rate',
                'driver_name','material','rate_type','billed_amount',
                'driver_payment','detention_charges','gps_cost','loading_charge','unloading_charge',
                'police_charges','sim_tracking','union_charges','weight_charges','other_charges',
                'brokerage','builty_commission','late_fees','material_damage','shortage_amount','shortage_qty','tds','other_deductions',
                'fuel_amount','fuel_vendor_id','driver_adv_amount','driver_adv_vendor_id','party_advance','payment_received',
                'owner_name','fixed_rate_amount','owner_rate','paid_to_owner','owner_vendor_id',
                'agent_commission','builty_expense','conductor_expense','fine','labour_charges','parking','puncture',
                'toll','urea','loading_expense','unloading_expense','wear_tear','weighbridge_charges','other_expense','misc_vendor_id',
                'lr_received']
        vals = [f.get('date'), f.get('lr_number'), vehicle_id, f.get('type'), party_id, f.get('from_loc'), f.get('to_loc'),
                quantity, rate, f.get('driver_name'), f.get('material'), rate_type, billed_amount,
                n('driver_payment'), n('detention_charges'), n('gps_cost'), n('loading_charge'), n('unloading_charge'),
                n('police_charges'), n('sim_tracking'), n('union_charges'), n('weight_charges'), n('other_charges'),
                n('brokerage'), n('builty_commission'), n('late_fees'), n('material_damage'), n('shortage_amount'),
                n('shortage_qty'), n('tds'), n('other_deductions'),
                n('fuel_amount'), fuel_vendor_id, n('driver_adv_amount'), driveradv_vendor_id, n('party_advance'), n('payment_received'),
                f.get('owner_name'), fixed_rate_amount, n('owner_rate'), n('paid_to_owner'), owner_vendor_id,
                n('agent_commission'), n('builty_expense'), n('conductor_expense'), n('fine'), n('labour_charges'),
                n('parking'), n('puncture'), n('toll'), n('urea'), n('loading_expense'), n('unloading_expense'),
                n('wear_tear'), n('weighbridge_charges'), n('other_expense'), misc_vendor_id,
                f.get('lr_received') or None]
        placeholders = ','.join('?' * len(cols))
        conn.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({placeholders})", vals)
        conn.commit()
        conn.close()
        return redirect(url_for('trips_list'))
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn2 = get_db()
    employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
    conn2.close()
    return render_template('add_trip.html', vehicles=vehicles, parties=parties, vendors=vendors, combined_names=combined_names, employees=employees, active='trips')

def _get_autocomplete_lists():
    conn = get_db()
    vehicles = conn.execute("SELECT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()
    parties = conn.execute("SELECT name FROM parties ORDER BY name").fetchall()
    vendors = conn.execute("SELECT name FROM vendors ORDER BY name").fetchall()
    combined_names = sorted(set([p['name'] for p in parties] + [v['name'] for v in vendors]))
    conn.close()
    return vehicles, parties, vendors, combined_names

@app.route('/maintenance')
def maintenance_list():
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    vendor_f = request.args.get('vendor', '')
    category_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = """SELECT m.id, m.date, v.vehicle_no, m.category, m.amount, m.paid_amount, m.notes, ve.name as vendor_name
               FROM maintenance m
               LEFT JOIN vehicles v ON m.vehicle_id = v.id
               LEFT JOIN vendors ve ON m.vendor_id = ve.id
               WHERE 1=1"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no LIKE ?"
        params.append(f"%{vehicle_f}%")
    if vendor_f:
        query += " AND ve.name LIKE ?"
        params.append(f"%{vendor_f}%")
    if category_f:
        query += " AND m.category = ?"
        params.append(category_f)
    if date_from:
        query += " AND m.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND m.date <= ?"
        params.append(date_to)
    where_clause = query[query.find("WHERE"):]
    total_shown = conn.execute(f"SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id {where_clause}", params).fetchone()[0]
    total_count = conn.execute(f"SELECT COUNT(*) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id {where_clause}", params).fetchone()[0]
    paid_total = conn.execute(f"SELECT COALESCE(SUM(m.paid_amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id {where_clause}", params).fetchone()[0]
    unpaid_total = total_shown - paid_total

    page = max(1, int(request.args.get('page', 1)))
    per_page = 50
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    query += f" ORDER BY m.date DESC LIMIT {per_page} OFFSET {offset}"

    rows = conn.execute(query, params).fetchall()
    vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()
    vendors = conn.execute("SELECT DISTINCT name FROM vendors ORDER BY name").fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM maintenance WHERE category IS NOT NULL ORDER BY category").fetchall()
    conn.close()
    return render_template('maintenance_list.html', rows=rows, total_shown=total_shown, total_count=total_count,
                            paid_total=paid_total, unpaid_total=unpaid_total,
                            page=page, total_pages=total_pages,
                            vehicles=vehicles, vendors=vendors, categories=categories,
                            f_vehicle=vehicle_f, f_vendor=vendor_f, f_category=category_f,
                            f_date_from=date_from, f_date_to=date_to, active='maintenance')

@app.route('/maintenance/add', methods=['GET', 'POST'])
def add_maintenance():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        def get_or_create_vehicle(vno):
            if not vno or not vno.strip():
                return None
            vno = vno.strip()
            row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
            if row:
                return row[0]
            cur = conn.execute("INSERT INTO vehicles (vehicle_no) VALUES (?)", (vno,))
            return cur.lastrowid
        vehicle_id = get_or_create_vehicle(f.get('vehicle_no'))
        vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
        conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes)
                        VALUES (?,?,?,?,?,?,?)""",
                     (f.get('date'), vehicle_id, f.get('category'), float(f.get('amount') or 0),
                      float(f.get('paid_amount') or 0), vendor_id, f.get('notes')))
        conn.commit()
        conn.close()
        return redirect(url_for('maintenance_list'))
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    return render_template('add_maintenance.html', vehicles=vehicles, vendors=vendors, combined_names=combined_names, active='maintenance')

def _filter_entries_by_date(entries, date_from, date_to):
    if date_from:
        entries = [e for e in entries if (e['date'] or '') >= date_from]
    if date_to:
        entries = [e for e in entries if (e['date'] or '') <= date_to]
    return entries

@app.route('/ledger/party/<int:party_id>')
def party_ledger(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    pending_trips_raw = conn.execute("""SELECT id, date, lr_number, from_loc, to_loc, billed_amount,
                                    (billed_amount - COALESCE(payment_received,0) - COALESCE(party_advance,0)) as pending
                                    FROM trips WHERE party_id=?
                                    AND (billed_amount - COALESCE(payment_received,0) - COALESCE(party_advance,0)) > 0.01
                                    ORDER BY date""", (party_id,)).fetchall()
    conn.close()
    pending_trips = [{'id': t['id'], 'date': t['date'], 'lr_number': t['lr_number'], 'from_loc': t['from_loc'],
                       'to_loc': t['to_loc'], 'billed_amount': t['billed_amount'] or 0, 'pending': t['pending'],
                       'paid': (t['billed_amount'] or 0) - t['pending']} for t in pending_trips_raw]
    total_pending_trips = sum(t['pending'] for t in pending_trips)
    all_entries = _get_party_ledger_entries(party_id)
    final_balance = all_entries[0]['balance'] if all_entries else 0

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    entries = _filter_entries_by_date(all_entries, date_from, date_to)

    return render_template('ledger.html', name=party['name'], role='Party', entries=entries,
                            final_balance=final_balance, payment_url=f'/payment/party/{party_id}', export_url=f'/ledger/party/{party_id}/export',
                            entity_id=party_id, entity_type='party', address=party['address'], contact=party['contact'],
                            email=party['email'], credit_limit=party['credit_limit'], since_date=party['since_date'],
                            opening_balance=party['opening_balance'], opening_balance_date=party['opening_balance_date'],
                            pending_trips=pending_trips, total_pending_trips=total_pending_trips,
                            f_date_from=date_from, f_date_to=date_to, active='accounts')

@app.route('/ledger/vendor/<int:vendor_id>')
def vendor_ledger(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if vendor and vendor['linked_party_id']:
        conn.close()
        return redirect(url_for('party_ledger', party_id=vendor['linked_party_id']))
    pending_trips_raw = conn.execute("""SELECT id, date, lr_number, from_loc, to_loc,
                                    (CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE COALESCE(owner_rate,0)*COALESCE(quantity,0) END) as billed_amount,
                                    (CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE COALESCE(owner_rate,0)*COALESCE(quantity,0) END - COALESCE(paid_to_owner,0)) as pending
                                    FROM trips WHERE owner_vendor_id=?
                                    AND (CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE COALESCE(owner_rate,0)*COALESCE(quantity,0) END - COALESCE(paid_to_owner,0)) > 0.01
                                    ORDER BY date""", (vendor_id,)).fetchall()
    conn.close()
    pending_trips = [{'id': t['id'], 'date': t['date'], 'lr_number': t['lr_number'], 'from_loc': t['from_loc'],
                       'to_loc': t['to_loc'], 'billed_amount': t['billed_amount'] or 0, 'pending': t['pending'],
                       'paid': (t['billed_amount'] or 0) - t['pending']} for t in pending_trips_raw]
    total_pending_trips = sum(t['pending'] for t in pending_trips)
    all_entries = _get_vendor_ledger_entries(vendor_id)
    final_balance = all_entries[0]['balance'] if all_entries else 0

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    entries = _filter_entries_by_date(all_entries, date_from, date_to)

    return render_template('ledger.html', name=vendor['name'], role='Vendor', entries=entries,
                            final_balance=final_balance, payment_url=f'/payment/vendor/{vendor_id}', export_url=f'/ledger/vendor/{vendor_id}/export',
                            entity_id=vendor_id, entity_type='vendor', address=vendor['address'], contact=vendor['contact'],
                            email=vendor['email'], credit_limit=vendor['credit_limit'], since_date=vendor['since_date'],
                            opening_balance=vendor['opening_balance'], opening_balance_date=vendor['opening_balance_date'],
                            pending_trips=pending_trips, total_pending_trips=total_pending_trips,
                            f_date_from=date_from, f_date_to=date_to, active='accounts')

def _export_ledger_entries(name, entries):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    headers = ["Date","Detail","Debit","Credit","Balance"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, e in enumerate(entries, 2):
        ws.cell(row=r_idx, column=1, value=e['date'])
        ws.cell(row=r_idx, column=2, value=e['detail'])
        ws.cell(row=r_idx, column=3, value=e['debit'] or None)
        ws.cell(row=r_idx, column=4, value=e['credit'] or None)
        ws.cell(row=r_idx, column=5, value=e['balance'])
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'ledger_{safe_name}.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def _payment_base_detail(p, label):
    parts = [label]
    if p['mode']:
        parts.append(f"({p['mode']})")
    detail = ' '.join(parts)
    if p['reference_id']:
        detail += f" — Ref: {p['reference_id']}"
    if p['remarks']:
        detail += f" | {p['remarks']}"
    return detail

def _get_party_ledger_entries(party_id):
    conn = get_db()
    party = conn.execute("SELECT opening_balance, opening_balance_date, since_date FROM parties WHERE id=?", (party_id,)).fetchone()
    trips = conn.execute("""SELECT id, date, lr_number, from_loc, to_loc, billed_amount, payment_received, party_advance
                             FROM trips WHERE party_id=? ORDER BY date""", (party_id,)).fetchall()
    # How much of each trip's payment_received came from a ledger payment allocation (vs. being
    # recorded directly on the trip itself) — so the trip's own row isn't inflated with money
    # that actually arrived, and should be shown, on a later payment date.
    trip_alloc = {}
    for row in conn.execute("""SELECT pa.trip_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN trips t ON pa.trip_id=t.id WHERE t.party_id=? GROUP BY pa.trip_id""", (party_id,)).fetchall():
        trip_alloc[row['trip_id']] = row['amt'] or 0
    payments = conn.execute("""SELECT id, date, amount, allocated_amount, mode, reference_id, remarks FROM payments
                                WHERE party_id=? AND payment_type='received' ORDER BY date""", (party_id,)).fetchall()
    linked_vendor = conn.execute("SELECT id FROM vendors WHERE linked_party_id=?", (party_id,)).fetchone()
    entries = []
    if party and party['opening_balance']:
        ob = party['opening_balance']
        entries.append({'date': party['opening_balance_date'] or party['since_date'] or '', 'detail': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0)})
    for t in trips:
        original_received = (t['payment_received'] or 0) - trip_alloc.get(t['id'], 0)
        entries.append({'date': t['date'], 'detail': f"{t['lr_number']}: {t['from_loc']} -> {t['to_loc']}",
                         'debit': t['billed_amount'] or 0, 'credit': original_received + (t['party_advance'] or 0)})
    for p in payments:
        base_detail = _payment_base_detail(p, 'Payment received')
        allocs = conn.execute("""SELECT t.lr_number, pa.amount FROM payment_allocations pa
                                 JOIN trips t ON pa.trip_id=t.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to {a['lr_number'] or 'trip'}",
                             'debit': 0, 'credit': a['amount']})
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        if leftover > 0.004 or not allocs:
            entries.append({'date': p['date'], 'detail': base_detail, 'debit': 0, 'credit': max(leftover, 0)})
    conn.close()
    if linked_vendor:
        # Same organization also acts as a vendor (fuel/maintenance/owner-hire) — pull those in too,
        # so this one ledger reflects everything, instead of splitting across two disconnected records.
        vendor_entries = _get_vendor_ledger_entries(linked_vendor['id'])
        for ve in vendor_entries:
            entries.append({'date': ve['date'], 'detail': ve['detail'] + ' (vendor side)',
                             'debit': ve['debit'], 'credit': ve['credit']})
    entries.sort(key=lambda x: x['date'] or '')
    balance = 0
    for e in entries:
        balance += e['debit'] - e['credit']
        e['balance'] = balance
    entries.reverse()
    return entries

def _get_vendor_ledger_entries(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT opening_balance, opening_balance_date, since_date FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    maint = conn.execute("""SELECT date, category, amount, paid_amount FROM maintenance
                             WHERE vendor_id=? ORDER BY date""", (vendor_id,)).fetchall()
    fuel = conn.execute("""SELECT date, fuel_amount FROM trips WHERE fuel_vendor_id=? ORDER BY date""", (vendor_id,)).fetchall()
    adv = conn.execute("""SELECT date, driver_adv_amount FROM trips WHERE driver_adv_vendor_id=? ORDER BY date""", (vendor_id,)).fetchall()
    owner_trips = conn.execute("""SELECT id, date, lr_number, rate_type, fixed_rate_amount, owner_rate, quantity, paid_to_owner
                                  FROM trips WHERE owner_vendor_id=? ORDER BY date""", (vendor_id,)).fetchall()
    # Ledger-allocated portion of each owner-hire trip's paid_to_owner (see party-side comment above).
    trip_alloc = {}
    for row in conn.execute("""SELECT pa.trip_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN trips t ON pa.trip_id=t.id WHERE t.owner_vendor_id=? GROUP BY pa.trip_id""", (vendor_id,)).fetchall():
        trip_alloc[row['trip_id']] = row['amt'] or 0
    payments = conn.execute("""SELECT id, date, amount, allocated_amount, mode, reference_id, remarks FROM payments
                                WHERE vendor_id=? AND payment_type='paid' ORDER BY date""", (vendor_id,)).fetchall()
    entries = []
    if vendor and vendor['opening_balance']:
        ob = vendor['opening_balance']
        entries.append({'date': vendor['opening_balance_date'] or vendor['since_date'] or '', 'detail': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0)})
    for m in maint:
        entries.append({'date': m['date'], 'detail': f"Maintenance: {m['category']}",
                         'debit': m['paid_amount'] or 0, 'credit': m['amount'] or 0})
    for f in fuel:
        entries.append({'date': f['date'], 'detail': 'Fuel', 'debit': 0, 'credit': f['fuel_amount'] or 0})
    for a in adv:
        entries.append({'date': a['date'], 'detail': 'Driver Advance', 'debit': 0, 'credit': a['driver_adv_amount'] or 0})
    for o in owner_trips:
        owed = o['fixed_rate_amount'] if o['rate_type']=='FIXED' else (o['owner_rate'] or 0) * (o['quantity'] or 0)
        if owed:
            original_paid = (o['paid_to_owner'] or 0) - trip_alloc.get(o['id'], 0)
            entries.append({'date': o['date'], 'detail': f"Vehicle hire: {o['lr_number']}",
                             'debit': original_paid, 'credit': owed})
    for p in payments:
        base_detail = _payment_base_detail(p, 'Payment made')
        allocs = conn.execute("""SELECT t.lr_number, pa.amount FROM payment_allocations pa
                                 JOIN trips t ON pa.trip_id=t.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to {a['lr_number'] or 'trip'}",
                             'debit': a['amount'], 'credit': 0})
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        if leftover > 0.004 or not allocs:
            entries.append({'date': p['date'], 'detail': base_detail, 'debit': max(leftover, 0), 'credit': 0})
    conn.close()
    entries.sort(key=lambda x: x['date'] or '')
    balance = 0
    for e in entries:
        balance += e['debit'] - e['credit']
        e['balance'] = balance
    entries.reverse()
    return entries

@app.route('/ledger/party/<int:party_id>/export')
def export_party_ledger(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    conn.close()
    entries = _get_party_ledger_entries(party_id)
    entries = _filter_entries_by_date(entries, request.args.get('date_from', ''), request.args.get('date_to', ''))
    return _export_ledger_entries(party['name'], entries)

@app.route('/ledger/vendor/<int:vendor_id>/export')
def export_vendor_ledger(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    conn.close()
    entries = _get_vendor_ledger_entries(vendor_id)
    entries = _filter_entries_by_date(entries, request.args.get('date_from', ''), request.args.get('date_to', ''))
    return _export_ledger_entries(vendor['name'], entries)

def _export_ledger_pdf(name, entries, role='', contact='', email='', address=''):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=17, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=8.5, textColor=colors.HexColor('#5A6B8C'), alignment=1)
    title_style = ParagraphStyle('T', parent=styles['Heading2'], fontSize=13, textColor=colors.HexColor('#1B2A4A'))
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'))

    story = [Paragraph("ANIL TRANSPORT SERVICE", company_style),
             Paragraph("Head Off.: Shop No. D/8, Nirmal Market Power House Road, Rourkela - 769001", sub_style),
             Paragraph("GSTIN No.: 21ABDPL6110E1ZG &nbsp;|&nbsp; Mob. +91 9437246272", sub_style),
             Spacer(1, 4)]
    line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
    line_table.setStyle(TableStyle([('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#1B2A4A'))]))
    story.append(line_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"Statement of Account — {name} ({role})", title_style))
    contact_line = " &nbsp;|&nbsp; ".join([p for p in [contact, email, address] if p])
    if contact_line:
        story.append(Paragraph(contact_line, label_style))
    story.append(Spacer(1, 14))

    rows = [['Date', 'Detail', 'Debit (Rs.)', 'Credit (Rs.)', 'Balance (Rs.)']]
    for e in entries:
        rows.append([e['date'] or '', e['detail'] or '',
                     f"{e['debit']:,.0f}" if e['debit'] else '', f"{e['credit']:,.0f}" if e['credit'] else '',
                     f"{e['balance']:,.0f}"])
    t = Table(rows, colWidths=[0.9*inch, 2.9*inch, 1*inch, 1*inch, 1*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('ALIGN', (2,0), (4,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'ledger_{safe_name}.pdf', mimetype='application/pdf')

@app.route('/ledger/party/<int:party_id>/export/pdf')
def export_party_ledger_pdf(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    conn.close()
    entries = _get_party_ledger_entries(party_id)
    entries = _filter_entries_by_date(entries, request.args.get('date_from', ''), request.args.get('date_to', ''))
    return _export_ledger_pdf(party['name'], entries, role='Party', contact=party['contact'] or '', email=party['email'] or '', address=party['address'] or '')

@app.route('/ledger/vendor/<int:vendor_id>/export/pdf')
def export_vendor_ledger_pdf(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    conn.close()
    entries = _get_vendor_ledger_entries(vendor_id)
    entries = _filter_entries_by_date(entries, request.args.get('date_from', ''), request.args.get('date_to', ''))
    return _export_ledger_pdf(vendor['name'], entries, role='Vendor', contact=vendor['contact'] or '', email=vendor['email'] or '', address=vendor['address'] or '')

@app.route('/payment/party/<int:party_id>', methods=['GET', 'POST'])
def add_party_payment(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    if request.method == 'POST':
        f = request.form
        total_amount = float(f.get('amount') or 0)
        cur = conn.execute("INSERT INTO payments (date, payment_type, amount, party_id, mode, reference_id, remarks) VALUES (?,?,?,?,?,?,?)",
                            (f.get('date'), 'received', total_amount, party_id, f.get('mode'),
                             f.get('reference_id') or None, f.get('remarks') or None))
        payment_id = cur.lastrowid
        # Pending is computed live (billed_amount - payment_received - party_advance) since it
        # isn't a stored column; allocation fills each selected trip's balance in submitted
        # order (oldest first) until the payment amount runs out.
        trip_ids = f.getlist('trip_ids')
        remaining = total_amount
        allocated_total = 0
        for tid in trip_ids:
            if remaining <= 0.004:
                break
            trip = conn.execute("SELECT billed_amount, payment_received, party_advance FROM trips WHERE id=?", (tid,)).fetchone()
            if not trip:
                continue
            pending = (trip['billed_amount'] or 0) - (trip['payment_received'] or 0) - (trip['party_advance'] or 0)
            if pending <= 0.004:
                continue
            alloc = min(pending, remaining)
            conn.execute("UPDATE trips SET payment_received = COALESCE(payment_received,0) + ? WHERE id=?", (alloc, tid))
            conn.execute("INSERT INTO payment_allocations (payment_id, trip_id, amount) VALUES (?,?,?)", (payment_id, tid, alloc))
            remaining -= alloc
            allocated_total += alloc
        conn.execute("UPDATE payments SET allocated_amount=? WHERE id=?", (allocated_total, payment_id))
        conn.commit()
        conn.close()
        return redirect(url_for('party_ledger', party_id=party_id))
    conn.close()
    return render_template('add_payment.html', name=party['name'], role='Party', action_url=f'/payment/party/{party_id}', active='accounts')

@app.route('/payment/vendor/<int:vendor_id>', methods=['GET', 'POST'])
def add_vendor_payment(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if request.method == 'POST':
        f = request.form
        total_amount = float(f.get('amount') or 0)
        cur = conn.execute("INSERT INTO payments (date, payment_type, amount, vendor_id, mode, reference_id, remarks) VALUES (?,?,?,?,?,?,?)",
                            (f.get('date'), 'paid', total_amount, vendor_id, f.get('mode'),
                             f.get('reference_id') or None, f.get('remarks') or None))
        payment_id = cur.lastrowid
        trip_ids = f.getlist('trip_ids')
        remaining = total_amount
        allocated_total = 0
        for tid in trip_ids:
            if remaining <= 0.004:
                break
            trip = conn.execute("""SELECT (CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE COALESCE(owner_rate,0)*COALESCE(quantity,0) END) as owed,
                                   paid_to_owner FROM trips WHERE id=?""", (tid,)).fetchone()
            if not trip:
                continue
            pending = (trip['owed'] or 0) - (trip['paid_to_owner'] or 0)
            if pending <= 0.004:
                continue
            alloc = min(pending, remaining)
            conn.execute("UPDATE trips SET paid_to_owner = COALESCE(paid_to_owner,0) + ? WHERE id=?", (alloc, tid))
            conn.execute("INSERT INTO payment_allocations (payment_id, trip_id, amount) VALUES (?,?,?)", (payment_id, tid, alloc))
            remaining -= alloc
            allocated_total += alloc
        conn.execute("UPDATE payments SET allocated_amount=? WHERE id=?", (allocated_total, payment_id))
        conn.commit()
        conn.close()
        return redirect(url_for('vendor_ledger', vendor_id=vendor_id))
    conn.close()
    return render_template('add_payment.html', name=vendor['name'], role='Vendor', action_url=f'/payment/vendor/{vendor_id}', active='accounts')

@app.route('/salaries/add', methods=['GET', 'POST'])
def add_salary():
    import datetime
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        tx_date = f.get('date')
        month_label = datetime.datetime.strptime(tx_date, '%Y-%m-%d').strftime('%b %Y') if tx_date else ''
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        emp_name = f.get('employee')
        emp_type = f.get('employee_type')
        existing = conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE", (emp_name,)).fetchone()
        if existing:
            conn.execute("UPDATE employees SET type=? WHERE id=?", (emp_type, existing[0]))
        else:
            conn.execute("INSERT INTO employees (name, type) VALUES (?,?)", (emp_name, emp_type))
        conn.execute("INSERT INTO salaries (employee, month, amount, date, created_at) VALUES (?,?,?,?,?)",
                     (emp_name, month_label, float(f.get('amount') or 0), tx_date, now))
        conn.commit()
        conn.close()
        return redirect(url_for('salaries_list'))
    employees = conn.execute("SELECT name, type FROM employees ORDER BY name").fetchall()
    conn.close()
    return render_template('add_salary.html', employees=employees, active='salaries')

@app.route('/overheads/add', methods=['GET', 'POST'])
def add_overhead():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        conn.execute("""INSERT INTO overheads (date, category, amount, notes, payment_mode, receipt_number)
                        VALUES (?,?,?,?,?,?)""",
                     (f.get('date'), f.get('category'), float(f.get('amount') or 0), f.get('notes'),
                      f.get('payment_mode'), f.get('receipt_number')))
        conn.commit()
        conn.close()
        return redirect(url_for('overheads_list'))
    conn.close()
    return render_template('add_overhead.html', active='overheads')

@app.route('/trips/delete/<int:trip_id>', methods=['POST'])
def delete_trip(trip_id):
    conn = get_db()
    conn.execute("DELETE FROM trips WHERE id=?", (trip_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('trips_list'))

@app.route('/trips/<int:trip_id>/end', methods=['POST'])
def end_trip(trip_id):
    conn = get_db()
    trip = conn.execute("SELECT id, end_date FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not trip or trip['end_date']:
        # Already ended (or missing) — no further edits allowed, silently ignore.
        conn.close()
        return redirect(url_for('trips_list', **request.args.to_dict()))

    f = request.form
    def n(key):
        return float(f.get(key) or 0)

    end_date = f.get('end_date')
    freight_amount = n('freight_amount')
    shortage_qty = n('shortage_qty')
    shortage_amount = n('shortage_amount')
    new_billed_amount = freight_amount - shortage_amount

    conn.execute("""UPDATE trips SET end_date=?, end_time=?, actual_km=?, lr_received=?,
                    shortage_qty=?, shortage_unit=?, shortage_amount=?, shortage_date=?, shortage_remarks=?,
                    remarks=?, billed_amount=?
                    WHERE id=?""",
                 (end_date, f.get('end_time') or None, n('actual_km') or None, f.get('lr_received') or None,
                  shortage_qty or None, f.get('shortage_unit') or None, shortage_amount,
                  end_date if shortage_amount else None, f.get('shortage_remarks') or None,
                  f.get('remarks') or None, new_billed_amount, trip_id))
    conn.commit()
    conn.close()
    return redirect(url_for('trips_list', **request.args.to_dict()))

@app.route('/maintenance/delete/<int:m_id>', methods=['POST'])
def delete_maintenance(m_id):
    conn = get_db()
    conn.execute("DELETE FROM maintenance WHERE id=?", (m_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list'))

@app.route('/maintenance/edit/<int:m_id>', methods=['GET', 'POST'])
def edit_maintenance(m_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        def get_or_create_vehicle(vno):
            if not vno or not vno.strip():
                return None
            vno = vno.strip()
            row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
            if row:
                return row[0]
            cur = conn.execute("INSERT INTO vehicles (vehicle_no) VALUES (?)", (vno,))
            return cur.lastrowid
        vehicle_id = get_or_create_vehicle(f.get('vehicle_no'))
        vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
        conn.execute("""UPDATE maintenance SET date=?, vehicle_id=?, category=?, amount=?, paid_amount=?, vendor_id=?, notes=?
                        WHERE id=?""",
                     (f.get('date'), vehicle_id, f.get('category'), float(f.get('amount') or 0),
                      float(f.get('paid_amount') or 0), vendor_id, f.get('notes'), m_id))
        conn.commit()
        conn.close()
        return redirect(url_for('maintenance_list'))

    m = conn.execute("""SELECT mt.*, v.vehicle_no, ve.name as vendor_name FROM maintenance mt
                        LEFT JOIN vehicles v ON mt.vehicle_id=v.id
                        LEFT JOIN vendors ve ON mt.vendor_id=ve.id WHERE mt.id=?""", (m_id,)).fetchone()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn.close()
    return render_template('edit_maintenance.html', m=m, vehicles=vehicles, combined_names=combined_names, active='maintenance')

@app.route('/salaries/delete/<int:s_id>', methods=['POST'])
def delete_salary(s_id):
    conn = get_db()
    conn.execute("DELETE FROM salaries WHERE id=?", (s_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries_list'))

@app.route('/overheads/delete/<int:o_id>', methods=['POST'])
def delete_overhead(o_id):
    conn = get_db()
    conn.execute("DELETE FROM overheads WHERE id=?", (o_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('overheads_list'))

@app.route('/vehicles/add', methods=['GET', 'POST'])
def add_vehicle():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        vno = (f.get('vehicle_no') or '').strip()
        vtype = f.get('type')
        existing = conn.execute("SELECT id FROM vehicles WHERE vehicle_no=?", (vno,)).fetchone()
        if existing:
            conn.execute("""UPDATE vehicles SET type=?, registration_date=?, capacity_mt=?,
                            insurance_expiry=?, fitness_expiry=?, notes=? WHERE id=?""",
                         (vtype, f.get('registration_date'), f.get('capacity_mt') or None,
                          f.get('insurance_expiry'), f.get('fitness_expiry'), f.get('notes'), existing[0]))
        else:
            conn.execute("""INSERT INTO vehicles (vehicle_no, type, registration_date, capacity_mt,
                            insurance_expiry, fitness_expiry, notes) VALUES (?,?,?,?,?,?,?)""",
                         (vno, vtype, f.get('registration_date'), f.get('capacity_mt') or None,
                          f.get('insurance_expiry'), f.get('fitness_expiry'), f.get('notes')))
        conn.commit()
        conn.close()
        return redirect(url_for('vehicles_list'))
    conn.close()
    return render_template('add_vehicle.html', active='vehicles')

@app.route('/trips/export')
def export_trips():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    lr_f = request.args.get('lr_received', '')
    query = """SELECT t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received
               FROM trips t LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if party_f: query += " AND p.name LIKE ?"; params.append(f"%{party_f}%")
    if date_from: query += " AND t.date >= ?"; params.append(date_from)
    if date_to: query += " AND t.date <= ?"; params.append(date_to)
    if lr_f == 'received': query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received': query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    query += " ORDER BY t.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Trips"
    headers = ["Date","LR Number","Vehicle","Party","From","To","Billed Amount","LR Received"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='trips_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/trips/export/pdf')
def export_trips_pdf():
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    lr_f = request.args.get('lr_received', '')
    query = """SELECT t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received
               FROM trips t LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if party_f: query += " AND p.name LIKE ?"; params.append(f"%{party_f}%")
    if date_from: query += " AND t.date >= ?"; params.append(date_from)
    if date_to: query += " AND t.date <= ?"; params.append(date_to)
    if lr_f == 'received': query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received': query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    query += " ORDER BY t.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], fontSize=15, textColor=colors.HexColor('#1B2A4A'))
    story = [Paragraph("Trips — Filtered View", title_style), Spacer(1, 10)]

    table_rows = [['Date', 'LR Number', 'Vehicle', 'Party', 'From', 'To', 'Billed (Rs.)', 'LR Received']]
    total_billed = 0
    for r in rows:
        total_billed += r['billed_amount'] or 0
        table_rows.append([r['date'] or '', r['lr_number'] or '', r['vehicle_no'] or '', r['party_name'] or '',
                            r['from_loc'] or '', r['to_loc'] or '', f"{r['billed_amount'] or 0:,.0f}",
                            'Yes' if r['lr_received']=='Yes' else 'No'])
    t = Table(table_rows, colWidths=[0.8*inch, 1*inch, 0.9*inch, 1.6*inch, 1.7*inch, 1.7*inch, 1*inch, 0.9*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (6,0), (6,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Total Trips: {len(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; Total Billed: Rs. {total_billed:,.0f}",
                            ParagraphStyle('F', parent=styles['Normal'], fontSize=10)))
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='trips_export.pdf', mimetype='application/pdf')

@app.route('/maintenance/export')
def export_maintenance():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    vendor_f = request.args.get('vendor', '')
    category_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = """SELECT m.date, v.vehicle_no, m.category, m.amount, ve.name as vendor_name
               FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id = v.id
               LEFT JOIN vendors ve ON m.vendor_id = ve.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if vendor_f: query += " AND ve.name LIKE ?"; params.append(f"%{vendor_f}%")
    if category_f: query += " AND m.category = ?"; params.append(category_f)
    if date_from: query += " AND m.date >= ?"; params.append(date_from)
    if date_to: query += " AND m.date <= ?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Maintenance"
    headers = ["Date","Vehicle","Category","Amount","Vendor"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='maintenance_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/maintenance/export/pdf')
def export_maintenance_pdf():
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    vendor_f = request.args.get('vendor', '')
    category_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = """SELECT m.date, v.vehicle_no, m.category, m.amount, ve.name as vendor_name
               FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id = v.id
               LEFT JOIN vendors ve ON m.vendor_id = ve.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if vendor_f: query += " AND ve.name LIKE ?"; params.append(f"%{vendor_f}%")
    if category_f: query += " AND m.category = ?"; params.append(category_f)
    if date_from: query += " AND m.date >= ?"; params.append(date_from)
    if date_to: query += " AND m.date <= ?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], fontSize=15, textColor=colors.HexColor('#1B2A4A'))
    story = [Paragraph("Maintenance — Filtered View", title_style), Spacer(1, 10)]

    table_rows = [['Date', 'Vehicle', 'Category', 'Vendor', 'Amount (Rs.)']]
    total_amount = 0
    for r in rows:
        total_amount += r['amount'] or 0
        table_rows.append([r['date'] or '', r['vehicle_no'] or '', r['category'] or '', r['vendor_name'] or '—',
                            f"{r['amount'] or 0:,.0f}"])
    t = Table(table_rows, colWidths=[1.1*inch, 1.3*inch, 2*inch, 2*inch, 1.3*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (4,0), (4,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Total Entries: {len(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; Total Amount: Rs. {total_amount:,.0f}",
                            ParagraphStyle('F', parent=styles['Normal'], fontSize=10)))
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='maintenance_export.pdf', mimetype='application/pdf')

@app.route('/advances')
def advances_list():
    conn = get_db()
    rows = conn.execute("""SELECT employee,
        SUM(CASE WHEN type='given' THEN amount ELSE 0 END) as total_given,
        SUM(CASE WHEN type='repaid' THEN amount ELSE 0 END) as total_repaid,
        SUM(CASE WHEN type='given' THEN amount ELSE -amount END) as outstanding
        FROM advances GROUP BY employee ORDER BY employee""").fetchall()
    entries = conn.execute("SELECT employee, date, amount, type, notes FROM advances ORDER BY date DESC").fetchall()
    conn.close()
    return render_template('advances_list.html', rows=rows, entries=entries, active='salaries')

@app.route('/advances/add', methods=['GET', 'POST'])
def add_advance():
    import datetime
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO advances (employee, date, amount, type, notes, created_at) VALUES (?,?,?,?,?,?)",
                     (f.get('employee'), f.get('date'), float(f.get('amount') or 0), f.get('type'), f.get('notes'), now))
        conn.commit()
        conn.close()
        return redirect(url_for('advances_list'))
    conn.close()
    return render_template('add_advance.html', active='salaries')

@app.route('/vehicles/edit/<int:vehicle_id>', methods=['GET', 'POST'])
def edit_vehicle(vehicle_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        conn.execute("""UPDATE vehicles SET vehicle_no=?, type=?, registration_date=?, capacity_mt=?,
                        insurance_expiry=?, fitness_expiry=?, notes=? WHERE id=?""",
                     (f.get('vehicle_no'), f.get('type'), f.get('registration_date'), f.get('capacity_mt') or None,
                      f.get('insurance_expiry'), f.get('fitness_expiry'), f.get('notes'), vehicle_id))
        conn.commit()
        conn.close()
        return redirect(url_for('vehicles_list'))
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    conn.close()
    return render_template('edit_vehicle.html', v=vehicle, active='vehicles')

@app.route('/vehicles/delete/<int:vehicle_id>', methods=['POST'])
def delete_vehicle(vehicle_id):
    conn = get_db()
    conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('vehicles_list'))

@app.route('/employee/<employee>', methods=['GET', 'POST'])
def employee_ledger(employee):
    import datetime
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        entry_kind = f.get('entry_kind')
        tx_date = f.get('date')
        amount = float(f.get('amount') or 0)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if entry_kind == 'salary':
            month_label = datetime.datetime.strptime(tx_date, '%Y-%m-%d').strftime('%b %Y') if tx_date else ''
            conn.execute("INSERT INTO salaries (employee, month, amount, date, created_at) VALUES (?,?,?,?,?)",
                         (employee, month_label, amount, tx_date, now))
        else:
            conn.execute("INSERT INTO advances (employee, date, amount, type, notes, created_at) VALUES (?,?,?,?,?,?)",
                         (employee, tx_date, amount, entry_kind, f.get('notes'), now))
        conn.commit()
        conn.close()
        return redirect(url_for('employee_ledger', employee=employee))

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    sal_query = "SELECT date, amount, created_at FROM salaries WHERE employee=?"
    sal_params = [employee]
    if date_from: sal_query += " AND date >= ?"; sal_params.append(date_from)
    if date_to: sal_query += " AND date <= ?"; sal_params.append(date_to)
    salaries = conn.execute(sal_query, sal_params).fetchall()

    adv_query = "SELECT date, amount, type, notes, created_at FROM advances WHERE employee=?"
    adv_params = [employee]
    if date_from: adv_query += " AND date >= ?"; adv_params.append(date_from)
    if date_to: adv_query += " AND date <= ?"; adv_params.append(date_to)
    advances = conn.execute(adv_query, adv_params).fetchall()
    emp_row = conn.execute("SELECT opening_balance, opening_balance_date FROM employees WHERE name=? COLLATE NOCASE", (employee,)).fetchone()
    conn.close()

    entries = []
    if emp_row and emp_row['opening_balance']:
        ob = emp_row['opening_balance']
        entries.append({'date': emp_row['opening_balance_date'] or '', 'entry_type': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0), 'notes': 'Carried over balance',
                         'created_at': '', 'affects_advance': True})
    for s in salaries:
        entries.append({'date': s['date'], 'entry_type': 'Salary Paid', 'debit': 0, 'credit': s['amount'] or 0,
                         'notes': '', 'created_at': s['created_at'], 'affects_advance': False})
    for a in advances:
        if a['type'] == 'given':
            entries.append({'date': a['date'], 'entry_type': 'Advance Given', 'debit': a['amount'] or 0, 'credit': 0,
                             'notes': a['notes'] or '', 'created_at': a['created_at'], 'affects_advance': True})
        else:
            entries.append({'date': a['date'], 'entry_type': 'Advance Repaid', 'debit': 0, 'credit': a['amount'] or 0,
                             'notes': a['notes'] or '', 'created_at': a['created_at'], 'affects_advance': True})
    entries.sort(key=lambda e: e['date'] or '')

    advance_balance = 0
    for e in entries:
        if e['affects_advance']:
            advance_balance += e['debit'] - e['credit']
        e['running_advance_balance'] = advance_balance
    entries.reverse()

    total_salary_paid = sum(e['credit'] for e in entries if e['entry_type']=='Salary Paid')
    return render_template('employee_ledger.html', employee=employee, entries=entries,
                            advance_balance=advance_balance, total_salary_paid=total_salary_paid,
                            opening_balance=emp_row['opening_balance'] if emp_row else 0,
                            opening_balance_date=emp_row['opening_balance_date'] if emp_row else '',
                            f_date_from=date_from, f_date_to=date_to, active='salaries')

@app.route('/employee/<employee>/opening-balance', methods=['POST'])
def update_employee_opening_balance(employee):
    conn = get_db()
    f = request.form
    conn.execute("UPDATE employees SET opening_balance=?, opening_balance_date=? WHERE name=? COLLATE NOCASE",
                 (float(f.get('opening_balance') or 0), f.get('opening_balance_date') or None, employee))
    conn.commit()
    conn.close()
    return redirect(url_for('employee_ledger', employee=employee))

@app.route('/driver-performance')
def driver_performance():
    return redirect(url_for('performance', **request.args.to_dict()))

def _trend_chart_coords(monthly, value_key):
    """Shared pixel-coordinate helper for the small SVG trend charts on the Performance page."""
    chart_w, chart_h, pad_l, pad_r, pad_t, pad_b = 700, 220, 34, 20, 20, 34
    plot_w, plot_h, n = chart_w - pad_l - pad_r, chart_h - pad_t - pad_b, len(monthly)
    chart_bottom = pad_t + plot_h
    vmax = max([m[value_key] for m in monthly], default=1) or 1
    for i, m in enumerate(monthly):
        m['x'] = round(pad_l + (i * plot_w / (n - 1) if n > 1 else 0), 1)
        m['y'] = round(pad_t + plot_h - (m[value_key] / vmax * plot_h), 1)
    y_ticks = []
    for k in range(5):
        tick_val = round(vmax * k / 4)
        y_ticks.append({'value': tick_val, 'y': round(pad_t + plot_h - (tick_val / vmax * plot_h), 1)})
    return chart_bottom, y_ticks

@app.route('/performance')
def performance():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2026-07-31')

    # ---------- Driver Performance (same calculation as the original Driver Performance page) ----------
    driver_query = """SELECT driver_name,
        COUNT(*) as trip_count,
        SUM(billed_amount) as total_billed,
        SUM(fuel_amount) as total_fuel,
        SUM(COALESCE(driver_payment,0)+COALESCE(toll,0)+COALESCE(detention_charges,0)+COALESCE(other_expense,0)) as total_other_costs,
        MAX(date) as last_trip
        FROM trips WHERE driver_name IS NOT NULL AND driver_name != ''"""
    dparams = []
    if date_from:
        driver_query += " AND date >= ?"; dparams.append(date_from)
    if date_to:
        driver_query += " AND date <= ?"; dparams.append(date_to)
    driver_query += " GROUP BY driver_name ORDER BY total_billed DESC"
    draw = conn.execute(driver_query, dparams).fetchall()

    driver_rows = []
    for r in draw:
        profit = (r['total_billed'] or 0) - (r['total_fuel'] or 0) - (r['total_other_costs'] or 0)
        driver_rows.append({'name': r['driver_name'], 'trip_count': r['trip_count'],
                             'total_billed': r['total_billed'] or 0, 'total_fuel': r['total_fuel'] or 0,
                             'profit': profit, 'last_trip': r['last_trip']})

    total_drivers = len(driver_rows)
    driver_total_trips = sum(r['trip_count'] for r in driver_rows)
    driver_total_billed = sum(r['total_billed'] for r in driver_rows)
    driver_total_fuel = sum(r['total_fuel'] for r in driver_rows)
    driver_total_profit = sum(r['profit'] for r in driver_rows)
    driver_avg_trips = round(driver_total_trips / total_drivers, 1) if total_drivers else 0
    top5_drivers = sorted(driver_rows, key=lambda r: r['trip_count'], reverse=True)[:5]
    top5_drivers_max = max([d['trip_count'] for d in top5_drivers], default=1) or 1
    for d in top5_drivers:
        d['pct'] = round(d['trip_count'] / top5_drivers_max * 100, 1)

    dmonth_q = """SELECT substr(date,1,7) as month, COUNT(*) as trips, COALESCE(SUM(billed_amount),0) as billed
                  FROM trips WHERE driver_name IS NOT NULL AND driver_name != ''"""
    if date_from:
        dmonth_q += " AND date >= ?"
    if date_to:
        dmonth_q += " AND date <= ?"
    dmonth_q += " GROUP BY month ORDER BY month"
    driver_monthly = [{'label': m['month'], 'trips': m['trips'], 'billed': m['billed']}
                       for m in conn.execute(dmonth_q, dparams).fetchall()]
    driver_chart_bottom, driver_y_ticks = _trend_chart_coords(driver_monthly, 'trips')
    driver_trend_max = max([m['trips'] for m in driver_monthly], default=1) or 1

    # ---------- Vehicle Performance (new, mirrors the driver calculation style) ----------
    vehicle_query = """SELECT v.id, v.vehicle_no, v.type,
        COUNT(t.id) as trip_count,
        COALESCE(SUM(t.billed_amount),0) as total_billed,
        COALESCE(SUM(t.fuel_amount),0) as total_fuel,
        COALESCE(SUM(COALESCE(t.driver_payment,0)+COALESCE(t.toll,0)+COALESCE(t.detention_charges,0)+COALESCE(t.other_expense,0)),0) as total_other_costs,
        MAX(t.date) as last_trip
        FROM trips t JOIN vehicles v ON t.vehicle_id=v.id WHERE v.type IN ('Line','Local')"""
    vparams = []
    if date_from:
        vehicle_query += " AND t.date >= ?"; vparams.append(date_from)
    if date_to:
        vehicle_query += " AND t.date <= ?"; vparams.append(date_to)
    vehicle_query += " GROUP BY v.id ORDER BY total_billed DESC"
    vraw = conn.execute(vehicle_query, vparams).fetchall()

    vehicle_rows = []
    for r in vraw:
        maint_q = "SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE vehicle_id=?"
        maint_params = [r['id']]
        if date_from:
            maint_q += " AND date >= ?"; maint_params.append(date_from)
        if date_to:
            maint_q += " AND date <= ?"; maint_params.append(date_to)
        maint_cost = conn.execute(maint_q, maint_params).fetchone()[0]
        profit = (r['total_billed'] or 0) - (r['total_fuel'] or 0) - (r['total_other_costs'] or 0) - maint_cost
        vehicle_rows.append({'name': r['vehicle_no'], 'type': r['type'], 'trip_count': r['trip_count'],
                              'total_billed': r['total_billed'], 'total_fuel': r['total_fuel'],
                              'maint_cost': maint_cost, 'profit': profit, 'last_trip': r['last_trip']})

    total_vehicles = len(vehicle_rows)
    vehicle_total_trips = sum(r['trip_count'] for r in vehicle_rows)
    vehicle_total_billed = sum(r['total_billed'] for r in vehicle_rows)
    vehicle_total_fuel = sum(r['total_fuel'] for r in vehicle_rows)
    vehicle_total_maint = sum(r['maint_cost'] for r in vehicle_rows)
    vehicle_total_profit = sum(r['profit'] for r in vehicle_rows)
    vehicle_avg_trips = round(vehicle_total_trips / total_vehicles, 1) if total_vehicles else 0
    top5_vehicles = sorted(vehicle_rows, key=lambda r: r['trip_count'], reverse=True)[:5]
    top5_vehicles_max = max([v['trip_count'] for v in top5_vehicles], default=1) or 1
    for v in top5_vehicles:
        v['pct'] = round(v['trip_count'] / top5_vehicles_max * 100, 1)

    vmonth_q = """SELECT substr(t.date,1,7) as month, COUNT(*) as trips, COALESCE(SUM(t.billed_amount),0) as billed
                  FROM trips t JOIN vehicles v ON t.vehicle_id=v.id WHERE v.type IN ('Line','Local')"""
    if date_from:
        vmonth_q += " AND t.date >= ?"
    if date_to:
        vmonth_q += " AND t.date <= ?"
    vmonth_q += " GROUP BY month ORDER BY month"
    vehicle_monthly = [{'label': m['month'], 'trips': m['trips'], 'billed': m['billed']}
                        for m in conn.execute(vmonth_q, vparams).fetchall()]
    vehicle_chart_bottom, vehicle_y_ticks = _trend_chart_coords(vehicle_monthly, 'trips')
    vehicle_trend_max = max([m['trips'] for m in vehicle_monthly], default=1) or 1

    conn.close()

    # ---------- Pagination (client-independent, shared page-size convention) ----------
    d_page = max(1, int(request.args.get('d_page', 1)))
    per_page = int(request.args.get('per_page', 5))
    if per_page not in (5, 10, 25, 50):
        per_page = 5
    d_total_pages = max(1, (total_drivers + per_page - 1) // per_page)
    d_page = min(d_page, d_total_pages)
    driver_page_rows = driver_rows[(d_page - 1) * per_page: d_page * per_page]

    v_page = max(1, int(request.args.get('v_page', 1)))
    v_total_pages = max(1, (total_vehicles + per_page - 1) // per_page)
    v_page = min(v_page, v_total_pages)
    vehicle_page_rows = vehicle_rows[(v_page - 1) * per_page: v_page * per_page]

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    for key in ('d_page', 'v_page', 'per_page', 'tab'):
        base_params.pop(key, None)
    base_qs = urlencode(base_params)

    return render_template('performance.html', base_qs=base_qs,
        driver_rows=driver_page_rows, total_drivers=total_drivers, driver_total_trips=driver_total_trips,
        driver_total_billed=driver_total_billed, driver_total_fuel=driver_total_fuel, driver_total_profit=driver_total_profit,
        driver_avg_trips=driver_avg_trips, top5_drivers=top5_drivers,
        driver_monthly=driver_monthly, driver_trend_max=driver_trend_max, driver_chart_bottom=driver_chart_bottom, driver_y_ticks=driver_y_ticks,
        d_page=d_page, d_total_pages=d_total_pages,
        vehicle_rows=vehicle_page_rows, total_vehicles=total_vehicles, vehicle_total_trips=vehicle_total_trips,
        vehicle_total_billed=vehicle_total_billed, vehicle_total_fuel=vehicle_total_fuel, vehicle_total_maint=vehicle_total_maint,
        vehicle_total_profit=vehicle_total_profit, vehicle_avg_trips=vehicle_avg_trips, top5_vehicles=top5_vehicles,
        vehicle_monthly=vehicle_monthly, vehicle_trend_max=vehicle_trend_max, vehicle_chart_bottom=vehicle_chart_bottom, vehicle_y_ticks=vehicle_y_ticks,
        v_page=v_page, v_total_pages=v_total_pages,
        per_page=per_page, f_date_from=date_from, f_date_to=date_to, active='performance')

@app.route('/employees/add', methods=['GET', 'POST'])
def add_employee():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        name = (f.get('name') or '').strip()
        etype = f.get('type')
        existing = conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO employees (name, type) VALUES (?,?)", (name, etype))
            conn.commit()
        conn.close()
        return redirect(url_for('employee_ledger', employee=name))
    conn.close()
    return render_template('add_employee.html', active='salaries')

@app.route('/invoice-center')
def invoice_center():
    conn = get_db()
    invoice_type = request.args.get('invoice_type', 'party')
    party_id = request.args.get('party_id', '')
    vendor_id = request.args.get('vendor_id', '')
    vehicle_f = request.args.get('vehicle', '')
    lr_f = request.args.get('lr_number', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    parties = conn.execute("SELECT id, name, contact FROM parties ORDER BY name").fetchall()
    vendors = conn.execute("SELECT id, name, contact, linked_party_id FROM vendors ORDER BY name").fetchall()

    # Combined owner picker for Market Vehicle Invoices: every vendor, plus every party not already
    # linked to a vendor — so any organization can be picked as an owner regardless of which
    # table it currently lives in. A party-only pick has no vendor row yet, so resolve/create
    # one and redirect to a plain vendor_id before anything downstream touches it.
    linked_party_ids = {v['linked_party_id'] for v in vendors if v['linked_party_id']}
    owner_options = [{'ref': f"v:{v['id']}", 'name': v['name']} for v in vendors]
    owner_options += [{'ref': f"p:{p['id']}", 'name': p['name']} for p in parties if p['id'] not in linked_party_ids]
    owner_options.sort(key=lambda o: o['name'].lower())

    if invoice_type == 'vehicle_owner' and vendor_id.startswith('p:'):
        p_row = conn.execute("SELECT name FROM parties WHERE id=?", (vendor_id[2:],)).fetchone()
        resolved_id = get_or_create_vendor(conn, p_row['name']) if p_row else None
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_center', invoice_type=invoice_type, vendor_id=resolved_id or ''))
    elif vendor_id.startswith('v:'):
        vendor_id = vendor_id[2:]

    selected_party = None
    selected_vendor = None
    trips = []
    if invoice_type in ('party', 'tax', 'bill') and party_id:
        selected_party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
        query = """SELECT t.id, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, t.date, t.billed_amount
                   FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                   WHERE t.party_id=?"""
        params = [party_id]
        if vehicle_f:
            query += " AND v.vehicle_no=?"; params.append(vehicle_f)
        if lr_f:
            query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_f}%")
        if date_from:
            query += " AND t.date>=?"; params.append(date_from)
        if date_to:
            query += " AND t.date<=?"; params.append(date_to)
        query += " ORDER BY t.date DESC LIMIT 200"
        trips = conn.execute(query, params).fetchall()
    elif invoice_type == 'vehicle_owner' and vendor_id:
        selected_vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        query = """SELECT t.id, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, t.date,
                   CASE WHEN t.rate_type='FIXED' THEN t.fixed_rate_amount ELSE t.owner_rate*t.quantity END as billed_amount
                   FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                   WHERE t.owner_vendor_id=?"""
        params = [vendor_id]
        if vehicle_f:
            query += " AND v.vehicle_no=?"; params.append(vehicle_f)
        if lr_f:
            query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_f}%")
        if date_from:
            query += " AND t.date>=?"; params.append(date_from)
        if date_to:
            query += " AND t.date<=?"; params.append(date_to)
        query += " ORDER BY t.date DESC LIMIT 200"
        trips = conn.execute(query, params).fetchall()

    vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()
    conn.close()
    return render_template('invoice_center.html', invoice_type=invoice_type, parties=parties, vendors=vendors,
                            owner_options=owner_options,
                            selected_party=selected_party, selected_vendor=selected_vendor, trips=trips, vehicles=vehicles,
                            f_party_id=party_id, f_vendor_id=vendor_id, f_vehicle=vehicle_f, f_lr=lr_f,
                            f_date_from=date_from, f_date_to=date_to, active='invoices')

@app.route('/invoice-center/review', methods=['POST'])
def invoice_center_review():
    trip_ids = request.form.getlist('trip_ids')
    invoice_type = request.form.get('invoice_type')
    party_id = request.form.get('party_id') or None
    vendor_id = request.form.get('vendor_id') or None

    conn = get_db()
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if invoice_type == 'vehicle_owner' and vendor_id:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    elif party_id:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()

    bank_name = conn.execute("SELECT value FROM settings WHERE key='bank_name'").fetchone()['value']
    account_number = conn.execute("SELECT value FROM settings WHERE key='account_number'").fetchone()['value']
    ifsc_code = conn.execute("SELECT value FROM settings WHERE key='ifsc_code'").fetchone()['value']
    account_holder = conn.execute("SELECT value FROM settings WHERE key='account_holder'").fetchone()['value']
    rcm_clause = conn.execute("SELECT value FROM settings WHERE key='rcm_clause'").fetchone()['value']
    conn.close()

    line_items = []
    for t in trips:
        if invoice_type == 'vehicle_owner':
            freight = t['fixed_rate_amount'] if t['rate_type']=='FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
            already_given = (t['paid_to_owner'] or 0) + (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
            net = freight - already_given
        else:
            freight = t['fixed_rate_amount'] if t['rate_type']=='FIXED' else (t['quantity'] or 0) * (t['rate'] or 0)
            net = t['billed_amount'] or 0
        line_items.append({'trip': t, 'freight': freight, 'net': net})

    total_freight = sum(li['freight'] for li in line_items)
    total_trips = len(line_items)

    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    return render_template('invoice_review.html', line_items=line_items, entity=entity, invoice_type=invoice_type,
                            party_id=party_id, vendor_id=vendor_id, total_freight=total_freight, total_trips=total_trips,
                            bank_name=bank_name, account_number=account_number, ifsc_code=ifsc_code,
                            account_holder=account_holder, rcm_clause=rcm_clause, today=today, active='invoices')

def number_to_words_inr(n):
    n = int(round(n))
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
            'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
    def two_digit(num):
        if num < 20:
            return ones[num]
        return tens[num // 10] + (' ' + ones[num % 10] if num % 10 else '')
    def three_digit(num):
        if num >= 100:
            return ones[num // 100] + ' Hundred' + (' ' + two_digit(num % 100) if num % 100 else '')
        return two_digit(num)
    if n == 0:
        return 'Zero'
    parts = []
    crore = n // 10000000; n %= 10000000
    lakh = n // 100000; n %= 100000
    thousand = n // 1000; n %= 1000
    hundred = n
    if crore: parts.append(two_digit(crore) + ' Crore')
    if lakh: parts.append(two_digit(lakh) + ' Lakh')
    if thousand: parts.append(two_digit(thousand) + ' Thousand')
    if hundred: parts.append(three_digit(hundred))
    return ' '.join(parts)

INVOICE_SETTINGS_KEYS = ['company_name', 'address', 'gstin', 'pan', 'phone', 'email', 'bank_name', 'account_holder',
                         'account_number', 'ifsc_code', 'branch', 'rcm_clause', 'cgst_rate', 'sgst_rate',
                         'invoice_prefix', 'next_invoice_number']

def _get_invoice_settings(conn):
    s = {}
    for key in INVOICE_SETTINGS_KEYS:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        s[key] = row['value'] if row else ''
    return s

def _build_invoice_pdf(trips, invoice_type, entity, s, invoice_number, invoice_date, due_date, payment_status, remarks,
                        cgst_rate=0, sgst_rate=0, extra_loading=0, extra_other=0):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    import io

    # Per-trip freight and charge breakdown
    line_items = []
    for t in trips:
        if invoice_type == 'vehicle_owner':
            freight = t['fixed_rate_amount'] if t['rate_type']=='FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        else:
            freight = t['fixed_rate_amount'] if t['rate_type']=='FIXED' else (t['quantity'] or 0) * (t['rate'] or 0)
        line_items.append({
            'trip': t, 'freight': freight,
            'loading': t['loading_charge'] or 0, 'unloading': t['unloading_charge'] or 0,
            'permit': t['permit_charges'] or 0, 'toll': t['toll'] or 0,
            'weighment': t['weight_charges'] or 0, 'driver_bata': t['driver_payment'] or 0,
            'gps': t['gps_cost'] or 0, 'other': t['other_charges'] or 0,
            'fuel_given': t['fuel_amount'] or 0, 'driver_adv_given': t['driver_adv_amount'] or 0,
            'paid_to_owner': t['paid_to_owner'] or 0,
        })

    total_freight = sum(li['freight'] for li in line_items)
    total_loading = sum(li['loading'] for li in line_items) + extra_loading
    total_unloading = sum(li['unloading'] for li in line_items)
    total_permit = sum(li['permit'] for li in line_items)
    total_toll = sum(li['toll'] for li in line_items)
    total_weighment = sum(li['weighment'] for li in line_items)
    total_driver_bata = sum(li['driver_bata'] for li in line_items)
    total_gps = sum(li['gps'] for li in line_items)
    total_other = sum(li['other'] for li in line_items) + extra_other

    freight_and_charges = total_freight + total_loading + total_unloading + total_permit + total_toll
    additional_charges = total_weighment + total_driver_bata + total_gps + total_other
    sub_total = freight_and_charges + additional_charges

    cgst_amount = round(sub_total * cgst_rate / 100, 2)
    sgst_amount = round(sub_total * sgst_rate / 100, 2)
    pre_round = sub_total + cgst_amount + sgst_amount
    grand_total = round(pre_round)
    round_off = round(grand_total - pre_round, 2)

    total_fuel_given = sum(li['fuel_given'] for li in line_items)
    total_driver_adv_given = sum(li['driver_adv_given'] for li in line_items)
    total_paid_to_owner = sum(li['paid_to_owner'] for li in line_items)
    total_already_given = total_fuel_given + total_driver_adv_given + total_paid_to_owner
    net_payable = grand_total - total_already_given if invoice_type == 'vehicle_owner' else grand_total

    total_weight = sum(li['trip']['quantity'] or 0 for li in line_items)
    total_vehicles = len(set(li['trip']['vehicle_no'] for li in line_items if li['trip']['vehicle_no']))
    dates = [li['trip']['date'] for li in line_items if li['trip']['date']]
    trip_period = f"{min(dates)} to {max(dates)}" if dates else ''

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.4*inch, bottomMargin=0.4*inch, leftMargin=0.45*inch, rightMargin=0.45*inch)
    styles = getSampleStyleSheet()
    BLACK = colors.HexColor('#1A1A1A')
    GREY = colors.HexColor('#5A5A5A')
    LINE = colors.HexColor('#333333')
    LIGHTBG = colors.HexColor('#EFF3FA')
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=16, textColor=BLACK, alignment=0, leading=19)
    tagline_style = ParagraphStyle('TL', parent=styles['Normal'], fontSize=7.5, textColor=GREY, leading=10)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=8, textColor=GREY, leading=11)
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=8.5, textColor=GREY, leading=12)
    title_box_style = ParagraphStyle('TB', parent=styles['Heading2'], fontSize=14, textColor=BLACK, alignment=1)
    section_head_style = ParagraphStyle('SH', parent=styles['Normal'], fontSize=8.5, textColor=BLACK, fontName='Helvetica-Bold')

    invoice_titles = {'party': 'PARTY INVOICE', 'vehicle_owner': 'MARKET VEHICLE INVOICE', 'tax': 'TAX INVOICE', 'bill': 'BILL INVOICE'}
    is_single = len(line_items) == 1

    def section_box(title, rows_data, col_widths, header_bg=LIGHTBG):
        head = Table([[Paragraph(f"<b>{title}</b>", section_head_style)]], colWidths=[sum(col_widths)])
        head.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), header_bg), ('BOX', (0,0), (-1,-1), 0.7, LINE),
                                    ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5), ('LEFTPADDING', (0,0), (-1,-1), 8)]))
        body = Table(rows_data, colWidths=col_widths)
        body.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.7, LINE), ('LINEBELOW', (0,0), (-1,-2), 0.3, colors.HexColor('#DDDDDD')),
            ('FONTSIZE', (0,0), (-1,-1), 8.5), ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
        ]))
        return [head, body]

    # ---------- Header ----------
    company_block = [
        Paragraph(f"<b>{s['company_name']}</b>", company_style),
        Paragraph("SAFE | RELIABLE | ON TIME", tagline_style),
        Spacer(1, 3),
        Paragraph(f"{s['address']}", sub_style),
    ]
    if invoice_type == 'tax':
        company_block.append(Paragraph(f"GSTIN: {s['gstin']} &nbsp;&nbsp; PAN: {s['pan']}", sub_style))
    company_block.append(Paragraph(f"&#9742; {s['phone']} &nbsp;&nbsp; &#9993; {s['email']}", sub_style))

    invoice_box_inner = [[Paragraph(invoice_titles.get(invoice_type, 'INVOICE'), title_box_style)]]
    box_body = Table([
        [Paragraph('Invoice No.', label_style)], [Paragraph(f"<b><font color='#B33A2E'>{invoice_number}</font></b>", label_style)],
        [Paragraph(f"Invoice Date&nbsp;&nbsp;&nbsp;{invoice_date}", label_style)],
        [Paragraph(f"Due Date&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{due_date or '—'}", label_style)],
    ], colWidths=[2.1*inch])
    box_body.setStyle(TableStyle([('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2)]))
    status_color = colors.HexColor('#2E7D32') if payment_status == 'PAID' else colors.HexColor('#B8860B')
    status_bg = colors.HexColor('#E8F5E9') if payment_status == 'PAID' else colors.HexColor('#FFF8E1')
    status_table = Table([[payment_status]], colWidths=[1.3*inch])
    status_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), status_bg), ('TEXTCOLOR', (0,0), (-1,-1), status_color),
                                       ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 9),
                                       ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5)]))

    invoice_box = Table([[Paragraph(invoice_titles.get(invoice_type, 'INVOICE'), title_box_style)], [box_body], [status_table]], colWidths=[2.2*inch])
    invoice_box.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1.2, BLACK), ('LINEBELOW', (0,0), (-1,0), 0.5, colors.HexColor('#CCCCCC')),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7), ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('ALIGN', (0,2), (-1,2), 'CENTER'),
    ]))

    header_table = Table([[company_block, invoice_box]], colWidths=[4.6*inch, 2.4*inch])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story = [header_table, Spacer(1, 10)]

    # ---------- Bill To + Trip Details/Summary ----------
    bill_lines = [f"<b>BILL TO ({'PARTY' if invoice_type != 'vehicle_owner' else 'VEHICLE OWNER'} DETAILS)</b>", "",
                  f"<b>{entity['name'] if entity else ''}</b>"]
    if entity and entity['address']:
        bill_lines.append(entity['address'])
    bill_para = Paragraph('<br/>'.join(bill_lines), label_style)

    if is_single:
        t = line_items[0]['trip']
        trip_rows = [
            ['LR Number', ':', t['lr_number'] or ''], ['Trip Date', ':', t['date'] or ''],
            ['Vehicle No.', ':', t['vehicle_no'] or ''], ['Driver Name', ':', t['driver_name'] or '—'],
            ['From', ':', t['from_loc'] or ''], ['To', ':', t['to_loc'] or ''],
            ['Material', ':', t['material'] or '—'], ['Weight', ':', f"{t['quantity'] or 0} MT"],
            ['Type', ':', t['type'] or '—'],
        ]
        trip_body = Table(trip_rows, colWidths=[1.1*inch, 0.15*inch, 2.05*inch])
        trip_body.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8.3), ('TOPPADDING',(0,0),(-1,-1),2.5), ('BOTTOMPADDING',(0,0),(-1,-1),2.5),
                                        ('TEXTCOLOR',(0,0),(0,-1),GREY)]))
        trip_box = section_box('TRIP DETAILS', [[trip_body]], [3.3*inch])
        two_col = Table([[bill_para, trip_box]], colWidths=[3.5*inch, 3.5*inch])
        two_col.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')]))
        story.append(two_col)
    else:
        summary_rows = [[
            Paragraph(f"Trip Period<br/><b>{trip_period}</b>", label_style),
            Paragraph(f"Total Trips<br/><b>{len(line_items)}</b>", label_style),
            Paragraph(f"Total Vehicles<br/><b>{total_vehicles}</b>", label_style),
            Paragraph(f"Total Weight<br/><b>{total_weight:.3f} MT</b>", label_style),
        ]]
        summary_body = Table(summary_rows, colWidths=[1.7*inch]*4)
        summary_body.setStyle(TableStyle([('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        summary_box = section_box('TRIP SUMMARY', [[summary_body]], [6.9*inch])
        story.append(bill_para)
        story.append(Spacer(1, 8))
        story.extend(summary_box)
    story.append(Spacer(1, 12))

    # ---------- Freight Details (single) / Trips table (multi) ----------
    if is_single:
        li = line_items[0]
        t = li['trip']
        rate_basis = 'Per Trip' if t['rate_type'] == 'FIXED' else f"{t['quantity']} MT"
        rate_val = t['fixed_rate_amount'] if t['rate_type']=='FIXED' else t['rate']
        freight_rows = [['#', 'DESCRIPTION', 'RATE (Rs.)', 'QTY / BASIS', 'AMOUNT (Rs.)'],
                         ['1', 'Freight Charges', f"{rate_val or 0:,.2f}", rate_basis, f"{li['freight']:,.2f}"]]
        n = 2
        for label, val in [('Loading Charges', li['loading']), ('Unloading Charges', li['unloading']),
                            ('Permit Charges', li['permit']), ('Toll Charges (Estimate)', li['toll'])]:
            if val:
                freight_rows.append([str(n), label, f"{val:,.2f}", 'Per Trip', f"{val:,.2f}"])
                n += 1
        ft = Table(freight_rows, colWidths=[0.3*inch, 2.5*inch, 1.3*inch, 1.5*inch, 1.3*inch], repeatRows=1)
        ft.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLACK), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8.3), ('ALIGN', (2,0), (4,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDDDDD')),
            ('TOPPADDING', (0,0), (-1,-1), 4.5), ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
        ]))
        story.append(ft)
        tot_row = Table([['TOTAL FREIGHT & CHARGES', f"Rs. {freight_and_charges:,.2f}"]], colWidths=[5.6*inch, 1.3*inch])
        tot_row.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                      ('FONTSIZE',(0,0),(-1,-1),9), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                      ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(tot_row)
        story.append(Spacer(1, 12))

        addl_rows = [['#', 'DESCRIPTION', 'BASIS', 'AMOUNT (Rs.)']]
        n = 1
        for label, val in [('Weighment Charges', li['weighment']), ('Driver Bata', li['driver_bata']),
                            ('GPS Charges', li['gps']), ('Other Charges', li['other'])]:
            if val:
                addl_rows.append([str(n), label, 'Per Trip', f"{val:,.2f}"])
                n += 1
        if len(addl_rows) > 1:
            addl_left = Table(addl_rows, colWidths=[0.3*inch, 1.9*inch, 1*inch, 1*inch])
            addl_left.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#EEEEEE')), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                ('FONTSIZE',(0,0),(-1,-1),8.3), ('ALIGN',(3,0),(3,-1),'RIGHT'), ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#DDDDDD')),
                ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
            ]))
            addl_box_head = Table([[Paragraph('<b>ADDITIONAL CHARGES</b>', section_head_style)]], colWidths=[4.2*inch])
            addl_box_head.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                                ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),8)]))
            addl_tot = Table([['TOTAL ADDITIONAL CHARGES', f"Rs. {additional_charges:,.2f}"]], colWidths=[3.2*inch, 1*inch])
            addl_tot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                           ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                           ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))

            sub_total_lines = [['Sub Total', f"Rs. {sub_total:,.2f}"]]
            if invoice_type == 'tax':
                sub_total_lines.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
                sub_total_lines.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
                sub_total_lines.append(['Round Off', f"Rs. {round_off:,.2f}"])
            sub_totals_table = Table(sub_total_lines, colWidths=[1.6*inch, 1.1*inch])
            sub_totals_table.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'),
                                                   ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3)]))
            grand_box = Table([['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"]], colWidths=[1.6*inch, 1.1*inch])
            grand_box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                            ('FONTSIZE',(0,0),(-1,-1),10.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.7,LINE),
                                            ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            right_stack = [sub_totals_table, Spacer(1,6), grand_box]

            left_col = [addl_box_head, addl_left, addl_tot]
            combo = Table([[left_col, right_stack]], colWidths=[4.3*inch, 2.7*inch])
            combo.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')]))
            story.append(combo)
        else:
            sub_total_lines = [['Sub Total', f"Rs. {sub_total:,.2f}"]]
            if invoice_type == 'tax':
                sub_total_lines.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
                sub_total_lines.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
                sub_total_lines.append(['Round Off', f"Rs. {round_off:,.2f}"])
            sub_total_lines.append(['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"])
            tt = Table(sub_total_lines, colWidths=[4.7*inch, 1.8*inch])
            tt.setStyle(TableStyle([('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'), ('FONTSIZE',(0,-1),(-1,-1),10.5),
                                     ('BACKGROUND',(0,-1),(-1,-1),LIGHTBG), ('BOX',(0,-1),(-1,-1),0.7,LINE),
                                     ('ALIGN',(1,0),(1,-1),'RIGHT'), ('FONTSIZE',(0,0),(-2,-2),8.5),
                                     ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            story.append(tt)
    else:
        item_rows = [['#', 'LR NUMBER', 'TRIP DATE', 'VEHICLE NO.', 'FROM', 'TO', 'MATERIAL', 'WEIGHT (MT)', 'FREIGHT (Rs.)']]
        for i, li in enumerate(line_items, 1):
            t = li['trip']
            item_rows.append([str(i), t['lr_number'] or '', t['date'] or '', t['vehicle_no'] or '',
                               t['from_loc'] or '', t['to_loc'] or '', t['material'] or '—',
                               f"{t['quantity'] or 0:.3f}", f"{li['freight']:,.2f}"])
        item_rows.append(['', '', '', '', '', '', 'TOTAL', f"{total_weight:.3f}", f"{total_freight:,.2f}"])
        item_table = Table(item_rows, colWidths=[0.25*inch, 0.75*inch, 0.65*inch, 0.75*inch, 1.1*inch, 1.1*inch, 0.8*inch, 0.75*inch, 0.85*inch], repeatRows=1)
        item_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLACK), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'), ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#EEEEEE')),
            ('FONTSIZE', (0,0), (-1,-1), 7.3), ('ALIGN', (7,0), (8,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDDDDD')),
            ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(item_table)
        story.append(Spacer(1, 12))

        multi_additional_total = sub_total - total_freight
        addl_rows = [['DESCRIPTION', 'BASIS', 'AMOUNT (Rs.)']]
        for label, val in [('Loading Charges', total_loading), ('Unloading Charges', total_unloading),
                            ('Permit Charges', total_permit), ('Toll Charges (Actual)', total_toll),
                            ('Weighment Charges', total_weighment), ('Driver Bata', total_driver_bata),
                            ('GPS Charges', total_gps), ('Other Charges', total_other)]:
            if val:
                addl_rows.append([label, 'Per Trip', f"{val:,.2f}"])
        if len(addl_rows) > 1:
            addl_left = Table(addl_rows, colWidths=[2.2*inch, 1*inch, 1*inch])
            addl_left.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#EEEEEE')), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                ('FONTSIZE',(0,0),(-1,-1),8), ('ALIGN',(2,0),(2,-1),'RIGHT'), ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#DDDDDD')),
                ('TOPPADDING',(0,0),(-1,-1),3.5), ('BOTTOMPADDING',(0,0),(-1,-1),3.5),
            ]))
            addl_head = Table([[Paragraph('<b>ADDITIONAL CHARGES (SUMMARY)</b>', section_head_style)]], colWidths=[4.2*inch])
            addl_head.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                            ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),8)]))
            addl_tot = Table([['TOTAL ADDITIONAL CHARGES', f"{multi_additional_total:,.2f}"]], colWidths=[3.2*inch, 1*inch])
            addl_tot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                           ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                           ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            left_col = [addl_head, addl_left, addl_tot]
        else:
            left_col = ''

        r_rows = [['Total Freight', f"Rs. {total_freight:,.2f}"], ['Total Additional Charges', f"Rs. {multi_additional_total:,.2f}"],
                   ['Sub Total', f"Rs. {sub_total:,.2f}"]]
        if invoice_type == 'tax':
            r_rows.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
            r_rows.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
            r_rows.append(['Round Off', f"Rs. {round_off:,.2f}"])
        r_table = Table(r_rows, colWidths=[1.7*inch, 1.1*inch])
        r_table.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8.3), ('ALIGN',(1,0),(1,-1),'RIGHT'),
                                      ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        grand_box = Table([['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"]], colWidths=[1.7*inch, 1.1*inch])
        grand_box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#E8F5E9')), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                        ('FONTSIZE',(0,0),(-1,-1),10.5), ('TEXTCOLOR',(1,0),(1,-1),colors.HexColor('#2E7D32')),
                                        ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.7,LINE),
                                        ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        right_stack = [r_table, Spacer(1,6), grand_box]
        combo = Table([[left_col, right_stack]], colWidths=[4.3*inch, 2.9*inch])
        combo.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')]))
        story.append(combo)

    if invoice_type == 'vehicle_owner' and total_already_given:
        deduction_rows = [['Gross Bill Amount', f"Rs. {grand_total:,.2f}"]]
        if total_fuel_given:
            deduction_rows.append(['Less: Fuel Advance Given', f"Rs. {total_fuel_given:,.2f}"])
        if total_driver_adv_given:
            deduction_rows.append(['Less: Driver Advance Given', f"Rs. {total_driver_adv_given:,.2f}"])
        if total_paid_to_owner:
            deduction_rows.append(['Less: Amount Already Paid', f"Rs. {total_paid_to_owner:,.2f}"])
        deduction_rows.append(['NET PAYABLE TO OWNER (Rs.)', f"{net_payable:,.2f}"])
        deduction_table = Table(deduction_rows, colWidths=[5.6*inch, 1.3*inch])
        deduction_table.setStyle(TableStyle([
            ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'),
            ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3),
            ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'), ('FONTSIZE',(0,-1),(-1,-1),10.5),
            ('BACKGROUND',(0,-1),(-1,-1),LIGHTBG), ('BOX',(0,-1),(-1,-1),0.7,LINE),
        ]))
        story.append(Spacer(1, 8))
        story.append(deduction_table)

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Amount In Words:</b> Rupees {number_to_words_inr(net_payable)} Only", label_style))
    story.append(Spacer(1, 16))

    # ---------- Footer: Bank Details / Terms / Signature ----------
    footer_cells = []
    if invoice_type == 'tax' and s['bank_name']:
        bank_lines = ["<b>BANK DETAILS</b>", f"Bank Name : {s['bank_name']}", f"A/C Number : {s['account_number']}",
                      f"IFSC Code : {s['ifsc_code']}", f"Branch : {s['branch']}"]
        footer_cells.append(Paragraph('<br/>'.join(bank_lines), label_style))
    terms_lines = ["<b>TERMS &amp; CONDITIONS</b>", "1. Payment should be made within due date.",
                   "2. Interest @ 18% p.a. will be charged on overdue.", "3. All disputes subject to Rourkela Jurisdiction."]
    footer_cells.append(Paragraph('<br/>'.join(terms_lines), label_style))
    footer_cells.append(Paragraph(f"<b>FOR {s['company_name']}</b><br/><br/><br/>Authorised Signatory", label_style))
    while len(footer_cells) < 3:
        footer_cells.insert(0, '')
    footer_table = Table([footer_cells], colWidths=[2.3*inch, 2.3*inch, 2.3*inch])
    footer_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(footer_table)

    if invoice_type == 'tax' and s['rcm_clause']:
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"<b>RCM Note:</b> {s['rcm_clause']}", label_style))
    if remarks:
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"<b>Remarks:</b> {remarks}", label_style))

    story.append(Spacer(1, 14))
    story.append(Paragraph("Thank you for your business!", ParagraphStyle('TY', parent=styles['Normal'], fontSize=9, alignment=1, fontName='Helvetica-Oblique')))

    doc.build(story)
    buf.seek(0)
    return buf

@app.route('/invoice-center/generate', methods=['POST'])
def invoice_center_generate():
    from flask import send_file
    import datetime

    f = request.form
    trip_ids = f.getlist('trip_ids')
    invoice_type = f.get('invoice_type')
    party_id = f.get('party_id') or None
    vendor_id = f.get('vendor_id') or None
    invoice_date = f.get('invoice_date') or datetime.datetime.now().strftime('%Y-%m-%d')
    due_date = f.get('due_date') or ''
    payment_terms = f.get('payment_terms') or ''
    place_of_supply = f.get('place_of_supply') or ''
    remarks = f.get('remarks') or ''
    payment_status = f.get('payment_status') or 'PENDING'
    extra_loading = float(f.get('loading_charges') or 0)
    extra_other = float(f.get('other_charges') or 0)
    tds_rate = float(f.get('tds_rate') or 0)
    gst_rate_input = f.get('gst_rate')

    conn = get_db()
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if invoice_type == 'vehicle_owner' and vendor_id:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    elif party_id:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()

    s = _get_invoice_settings(conn)

    if invoice_type == 'tax':
        total_gst_rate = float(gst_rate_input) if gst_rate_input else (float(s['cgst_rate'] or 0) + float(s['sgst_rate'] or 0))
        cgst_rate = sgst_rate = round(total_gst_rate / 2, 4)
    else:
        cgst_rate = sgst_rate = 0

    next_num = int(s['next_invoice_number'] or 1)
    invoice_number = f"{s['invoice_prefix'] or 'ATS/INV'}/{datetime.datetime.now().year}/{next_num:04d}"

    buf = _build_invoice_pdf(trips, invoice_type, entity, s, invoice_number, invoice_date, due_date, payment_status, remarks,
                              cgst_rate=cgst_rate, sgst_rate=sgst_rate, extra_loading=extra_loading, extra_other=extra_other)

    now = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    cur = conn.execute("""INSERT INTO invoice_batches (invoice_number, invoice_type, party_id, vendor_id, invoice_date, due_date,
                          payment_terms, place_of_supply, remarks, gst_rate, tds_rate, loading_charges, other_charges, status, payment_status, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (invoice_number, invoice_type, party_id, vendor_id, invoice_date, due_date, payment_terms,
                  place_of_supply, remarks, cgst_rate+sgst_rate, tds_rate, extra_loading, extra_other,
                  'generated', payment_status, now))
    batch_id = cur.lastrowid
    for tid in trip_ids:
        conn.execute("INSERT INTO invoice_batch_trips (invoice_batch_id, trip_id) VALUES (?,?)", (batch_id, tid))
    conn.execute("UPDATE settings SET value=? WHERE key='next_invoice_number'", (str(next_num + 1),))
    conn.commit()
    conn.close()

    return send_file(buf, as_attachment=True, download_name=f'{invoice_number.replace("/","-")}.pdf', mimetype='application/pdf')

@app.route('/invoices/generated')
def invoice_batches_list():
    conn = get_db()
    search_f = request.args.get('search', '')
    type_f = request.args.get('invoice_type', '')
    query = """SELECT ib.id, ib.invoice_number, ib.invoice_type, ib.invoice_date, ib.due_date,
               ib.payment_status, ib.created_at, p.name as party_name, v.name as vendor_name,
               (SELECT COUNT(*) FROM invoice_batch_trips ibt WHERE ibt.invoice_batch_id=ib.id) as trip_count
               FROM invoice_batches ib
               LEFT JOIN parties p ON ib.party_id=p.id
               LEFT JOIN vendors v ON ib.vendor_id=v.id
               WHERE 1=1"""
    params = []
    if type_f:
        query += " AND ib.invoice_type=?"; params.append(type_f)
    if search_f:
        query += " AND (p.name LIKE ? OR v.name LIKE ? OR ib.invoice_number LIKE ?)"
        params += [f"%{search_f}%", f"%{search_f}%", f"%{search_f}%"]
    query += " ORDER BY ib.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('invoice_batches_list.html', rows=rows, f_search=search_f, f_type=type_f, active='invoices')

@app.route('/invoices/generated/<int:batch_id>/edit', methods=['GET', 'POST'])
def invoice_batch_edit(batch_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        conn.execute("""UPDATE invoice_batches SET invoice_date=?, due_date=?, payment_terms=?, place_of_supply=?,
                        remarks=?, payment_status=?, gst_rate=?, tds_rate=?, loading_charges=?, other_charges=?
                        WHERE id=?""",
                     (f.get('invoice_date'), f.get('due_date'), f.get('payment_terms'), f.get('place_of_supply'),
                      f.get('remarks'), f.get('payment_status'), float(f.get('gst_rate') or 0), float(f.get('tds_rate') or 0),
                      float(f.get('loading_charges') or 0), float(f.get('other_charges') or 0), batch_id))
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_batches_list'))
    batch = conn.execute("""SELECT ib.*, p.name as party_name, v.name as vendor_name FROM invoice_batches ib
                            LEFT JOIN parties p ON ib.party_id=p.id LEFT JOIN vendors v ON ib.vendor_id=v.id
                            WHERE ib.id=?""", (batch_id,)).fetchone()
    conn.close()
    return render_template('invoice_batch_edit.html', b=batch, active='invoices')

@app.route('/invoices/generated/<int:batch_id>/pdf')
def invoice_batch_pdf(batch_id):
    from flask import send_file
    conn = get_db()
    batch = conn.execute("SELECT * FROM invoice_batches WHERE id=?", (batch_id,)).fetchone()
    trip_id_rows = conn.execute("SELECT trip_id FROM invoice_batch_trips WHERE invoice_batch_id=?", (batch_id,)).fetchall()
    trip_ids = [str(r['trip_id']) for r in trip_id_rows]
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if batch['invoice_type'] == 'vehicle_owner' and batch['vendor_id']:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (batch['vendor_id'],)).fetchone()
    elif batch['party_id']:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (batch['party_id'],)).fetchone()
    s = _get_invoice_settings(conn)
    conn.close()

    cgst_rate = sgst_rate = round((batch['gst_rate'] or 0) / 2, 4) if batch['invoice_type'] == 'tax' else 0
    buf = _build_invoice_pdf(trips, batch['invoice_type'], entity, s, batch['invoice_number'], batch['invoice_date'],
                              batch['due_date'], batch['payment_status'], batch['remarks'],
                              cgst_rate=cgst_rate, sgst_rate=sgst_rate,
                              extra_loading=batch['loading_charges'] or 0, extra_other=batch['other_charges'] or 0)
    return send_file(buf, as_attachment=True, download_name=f'{batch["invoice_number"].replace("/","-")}.pdf', mimetype='application/pdf')

@app.route('/invoices')
def invoices_search():
    lr_f = request.args.get('lr_number', '')
    conn = get_db()
    results = []
    if lr_f:
        results = conn.execute("""SELECT t.id, t.date, t.lr_number, v.vehicle_no, p.name as party_name,
                                  t.from_loc, t.to_loc, t.billed_amount
                                  FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                  LEFT JOIN parties p ON t.party_id=p.id
                                  WHERE t.lr_number LIKE ? ORDER BY t.date DESC""", (f"%{lr_f}%",)).fetchall()
    conn.close()
    return render_template('invoices_search.html', results=results, f_lr=lr_f, active='invoices')

@app.route('/invoices/<int:trip_id>')
def invoice_preview(trip_id):
    conn = get_db()
    t = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name
                        FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                        LEFT JOIN parties p ON t.party_id=p.id WHERE t.id=?""", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    conn.close()
    charges = [
        ('Driver Payment', t['driver_payment']), ('Detention Charges', t['detention_charges']),
        ('GPS Cost', t['gps_cost']), ('Loading Charge', t['loading_charge']),
        ('Unloading Charge', t['unloading_charge']), ('Police Charges', t['police_charges']),
        ('SIM Tracking', t['sim_tracking']), ('Union Charges', t['union_charges']),
        ('Weight Charges', t['weight_charges']), ('Other Charges', t['other_charges']),
    ]
    charges = [(l, v) for l, v in charges if v]
    charges += [(item['description'], item['amount']) for item in extra_items if item['item_type']=='charge']
    deductions = [
        ('Brokerage', t['brokerage']), ('Builty Commission', t['builty_commission']),
        ('Late Fees', t['late_fees']), ('Material Damage', t['material_damage']),
        ('Shortage Amount', t['shortage_amount']), ('TDS', t['tds']), ('Other Deductions', t['other_deductions']),
    ]
    deductions = [(l, v) for l, v in deductions if v]
    deductions += [(item['description'], item['amount']) for item in extra_items if item['item_type']=='deduction']
    freight = t['fixed_rate_amount'] if t['rate_type'] == 'FIXED' else (t['quantity'] or 0) * (t['rate'] or 0)
    extra_charges_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='charge')
    extra_deductions_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='deduction')
    invoice_total = (t['billed_amount'] or 0) + extra_charges_total - extra_deductions_total
    amount_paid = (t['payment_received'] or 0) + (t['party_advance'] or 0)
    balance_due = invoice_total - amount_paid
    return render_template('invoice_preview.html', t=t, inv=inv, charges=charges, deductions=deductions, freight=freight,
                            invoice_total=invoice_total, amount_paid=amount_paid, balance_due=balance_due, extra_items=extra_items, active='invoices')

@app.route('/invoices/<int:trip_id>/edit', methods=['GET', 'POST'])
def invoice_edit(trip_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        existing = conn.execute("SELECT id FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
        if existing:
            conn.execute("UPDATE invoices SET invoice_number=?, due_date=?, notes=? WHERE trip_id=?",
                         (f.get('invoice_number'), f.get('due_date'), f.get('notes'), trip_id))
        else:
            conn.execute("INSERT INTO invoices (trip_id, invoice_number, due_date, notes) VALUES (?,?,?,?)",
                         (trip_id, f.get('invoice_number'), f.get('due_date'), f.get('notes')))
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_preview', trip_id=trip_id))

    t = conn.execute("SELECT lr_number FROM trips WHERE id=?", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    conn.close()
    default_invoice_no = f"INV-{t['lr_number']}" if not (inv and inv['invoice_number']) else None
    return render_template('invoice_edit.html', t=t, inv=inv, default_invoice_no=default_invoice_no, trip_id=trip_id, extra_items=extra_items, active='invoices')

@app.route('/invoices/<int:trip_id>/items/add', methods=['POST'])
def invoice_item_add(trip_id):
    conn = get_db()
    f = request.form
    conn.execute("INSERT INTO invoice_items (trip_id, description, amount, item_type) VALUES (?,?,?,?)",
                 (trip_id, f.get('description'), float(f.get('amount') or 0), f.get('item_type')))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_edit', trip_id=trip_id))

@app.route('/invoices/items/<int:item_id>/delete', methods=['POST'])
def invoice_item_delete(item_id):
    conn = get_db()
    trip_id = conn.execute("SELECT trip_id FROM invoice_items WHERE id=?", (item_id,)).fetchone()[0]
    conn.execute("DELETE FROM invoice_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_edit', trip_id=trip_id))

@app.route('/invoices/<int:trip_id>/pdf')
def invoice_pdf(trip_id):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io

    conn = get_db()
    t = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name
                        FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                        LEFT JOIN parties p ON t.party_id=p.id WHERE t.id=?""", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    conn.close()

    invoice_number = (inv['invoice_number'] if inv and inv['invoice_number'] else f"INV-{t['lr_number']}")
    due_date = (inv['due_date'] if inv and inv['due_date'] else '')
    inv_notes = (inv['notes'] if inv and inv['notes'] else '')

    freight = t['fixed_rate_amount'] if t['rate_type'] == 'FIXED' else (t['quantity'] or 0) * (t['rate'] or 0)
    charges_total = sum(t[c] or 0 for c in ['driver_payment','detention_charges','gps_cost','loading_charge',
                        'unloading_charge','police_charges','sim_tracking','union_charges','weight_charges','other_charges'])
    deductions_total = sum(t[c] or 0 for c in ['brokerage','builty_commission','late_fees','material_damage',
                            'shortage_amount','tds','other_deductions'])
    extra_charges_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='charge')
    extra_deductions_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='deduction')
    charges_total += extra_charges_total
    deductions_total += extra_deductions_total
    amount_paid = (t['payment_received'] or 0) + (t['party_advance'] or 0)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Title'], fontSize=20, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'), alignment=1)
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'))
    story = []

    story.append(Paragraph("ANIL TRANSPORT SERVICE", title_style))
    story.append(Paragraph("Head Off.: Shop No. D/8, Nirmal Market Power House Road, Rourkela - 769001", sub_style))
    story.append(Paragraph("GSTIN No.: 21ABDPL6110E1ZG &nbsp;&nbsp;|&nbsp;&nbsp; Mob. +91 9437246272 &nbsp;&nbsp;|&nbsp;&nbsp; Ph./Fax: 0661-2501272", sub_style))
    story.append(Spacer(1, 6))
    line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
    line_table.setStyle(TableStyle([('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#1B2A4A'))]))
    story.append(line_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"<b>INVOICE</b>", styles['Heading2']))
    story.append(Spacer(1, 8))

    info_data = [
        ['Invoice No.', invoice_number, 'LR Number', t['lr_number'] or ''],
        ['Date', t['date'] or '', 'Due Date', due_date or '—'],
        ['Bill To', t['party_name'] or '', 'Vehicle No', t['vehicle_no'] or ''],
        ['From', t['from_loc'] or '', 'To', t['to_loc'] or ''],
        ['Material', t['material'] or '', 'Quantity (MT)', str(t['quantity'] or '')],
    ]
    info_table = Table(info_data, colWidths=[1*inch, 2.2*inch, 1*inch, 1.8*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 16))

    charge_rows = [['Description', 'Amount (Rs.)']]
    charge_rows.append(['Freight' + (' (Fixed)' if t['rate_type']=='FIXED' else f" ({t['quantity']} MT x Rs.{t['rate']})"), f"{freight:,.0f}"])
    if charges_total:
        charge_rows.append(['Total Charges (+)', f"{charges_total:,.0f}"])
    if deductions_total:
        charge_rows.append(['Total Deductions (-)', f"-{deductions_total:,.0f}"])
    invoice_total = (t['billed_amount'] or 0) + extra_charges_total - extra_deductions_total
    balance_due = invoice_total - amount_paid
    charge_rows.append(['TOTAL BILLED', f"{invoice_total:,.0f}"])
    if amount_paid:
        charge_rows.append(['Amount Already Paid', f"-{amount_paid:,.0f}"])
        charge_rows.append(['BALANCE DUE', f"{balance_due:,.0f}"])

    charge_table = Table(charge_rows, colWidths=[4*inch, 2*inch])
    charge_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor('#1B2A4A')),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#DDE3EC')),
    ]))
    story.append(charge_table)
    story.append(Spacer(1, 20))
    if inv_notes:
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"<b>Notes:</b> {inv_notes}", label_style))
    story.append(Spacer(1, 20))
    story.append(Paragraph("This is a system-generated invoice.", label_style))

    doc.build(story)
    buf.seek(0)
    safe_lr = "".join(c for c in (t['lr_number'] or 'invoice') if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'invoice_{safe_lr}.pdf', mimetype='application/pdf')

@app.route('/ledger/<entity_type>/<int:entity_id>/contact', methods=['POST'])
def update_contact(entity_type, entity_id):
    conn = get_db()
    f = request.form
    table = 'parties' if entity_type == 'party' else 'vendors'
    conn.execute(f"UPDATE {table} SET address=?, contact=?, email=?, credit_limit=? WHERE id=?",
                 (f.get('address'), f.get('contact'), f.get('email'), f.get('credit_limit') or None, entity_id))
    conn.commit()
    conn.close()
    if entity_type == 'party':
        return redirect(url_for('party_ledger', party_id=entity_id))
    return redirect(url_for('vendor_ledger', vendor_id=entity_id))

@app.route('/ledger/<entity_type>/<int:entity_id>/opening-balance', methods=['POST'])
def update_opening_balance(entity_type, entity_id):
    conn = get_db()
    f = request.form
    table = 'parties' if entity_type == 'party' else 'vendors'
    conn.execute(f"UPDATE {table} SET opening_balance=?, opening_balance_date=? WHERE id=?",
                 (float(f.get('opening_balance') or 0), f.get('opening_balance_date') or None, entity_id))
    conn.commit()
    conn.close()
    if entity_type == 'party':
        return redirect(url_for('party_ledger', party_id=entity_id))
    return redirect(url_for('vendor_ledger', vendor_id=entity_id))

def get_company_name():
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='company_name'").fetchone()
    conn.close()
    return row['value'] if row else 'ANIL TRANSPORT SERVICE'

@app.context_processor
def inject_company_name():
    try:
        return {'company_name': get_company_name()}
    except Exception:
        return {'company_name': 'ANIL TRANSPORT SERVICE'}

@app.before_request
def require_login():
    exempt = ['login', 'static', 'send_login_otp', 'verify_login_otp']
    if request.endpoint in exempt:
        return
    if not session.get('user_id'):
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    from werkzeug.security import check_password_hash
    error = None
    if request.method == 'POST':
        f = request.form
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (f.get('username'),)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], f.get('password') or ''):
            session.permanent = bool(f.get('remember_me'))
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user['is_admin']
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error, company_name=get_company_name())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def _send_otp_sms(s, phone, otp):
    from twilio.rest import Client
    client = Client(s['twilio_account_sid'], s['twilio_auth_token'])
    client.messages.create(
        body=f"Your {s.get('company_name') or 'Fleet App'} login OTP is {otp}. Valid for 5 minutes.",
        from_=s['twilio_from_number'],
        to=phone,
    )

@app.route('/login/otp/send', methods=['POST'])
def send_login_otp():
    import random, datetime, hashlib
    phone = (request.form.get('phone') or '').strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    s = _get_all_settings(conn)
    conn.close()

    if not phone:
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error='Enter a mobile number.')
    if not user:
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error='No account found with that mobile number.')
    if not (s['twilio_account_sid'] and s['twilio_auth_token'] and s['twilio_from_number']):
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error="SMS login isn't configured yet. Ask an admin to add Twilio details in Settings.")

    otp = f"{random.randint(0, 999999):06d}"
    session['otp_user_id'] = user['id']
    session['otp_phone'] = phone
    session['otp_hash'] = hashlib.sha256(otp.encode()).hexdigest()
    session['otp_expires'] = (datetime.datetime.now() + datetime.timedelta(minutes=5)).timestamp()

    try:
        _send_otp_sms(s, phone, otp)
    except Exception as e:
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error=f'Could not send OTP: {e}')

    return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                            otp_sent=True, otp_phone=phone)

@app.route('/login/otp/verify', methods=['POST'])
def verify_login_otp():
    import hashlib, datetime
    code = (request.form.get('otp') or '').strip()
    phone = request.form.get('phone') or ''

    if not session.get('otp_user_id') or session.get('otp_phone') != phone:
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error='Session expired. Please request a new OTP.')
    if datetime.datetime.now().timestamp() > session.get('otp_expires', 0):
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_error='OTP expired. Please request a new one.')
    if hashlib.sha256(code.encode()).hexdigest() != session.get('otp_hash'):
        return render_template('login.html', company_name=get_company_name(), otp_mode=True,
                                otp_sent=True, otp_phone=phone, otp_error='Incorrect OTP.')

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['otp_user_id'],)).fetchone()
    conn.close()
    for key in ('otp_user_id', 'otp_phone', 'otp_hash', 'otp_expires'):
        session.pop(key, None)
    if not user:
        return redirect(url_for('login'))
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['is_admin'] = user['is_admin']
    return redirect(url_for('dashboard'))

ALL_SETTING_KEYS = [
    'company_name', 'address', 'state', 'phone', 'email', 'gstin', 'pan', 'business_type', 'company_since', 'currency',
    'bank_name', 'account_holder', 'account_number', 'ifsc_code', 'branch', 'account_type',
    'default_invoice_type', 'default_payment_terms', 'default_place_of_supply', 'default_due_days',
    'show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words',
    'invoice_prefix', 'next_invoice_number',
    'cgst_rate', 'sgst_rate', 'igst_rate', 'reverse_charge_applicable', 'rcm_on_transport',
    'tds_applicable', 'tds_rate_default', 'eway_bill_mandatory', 'round_off_limit', 'rcm_clause',
    'twilio_account_sid', 'twilio_auth_token', 'twilio_from_number'
]

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    conn = get_db()
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        field_groups = {
            'company': ['company_name', 'address', 'state', 'phone', 'email', 'gstin', 'pan', 'business_type', 'company_since', 'currency'],
            'banking': ['bank_name', 'account_holder', 'account_number', 'ifsc_code', 'branch', 'account_type'],
            'invoice_prefs': ['default_invoice_type', 'default_payment_terms', 'default_place_of_supply', 'default_due_days',
                               'show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words'],
            'invoice_numbering': ['invoice_prefix', 'next_invoice_number'],
            'tax': ['cgst_rate', 'sgst_rate', 'igst_rate', 'reverse_charge_applicable', 'rcm_on_transport',
                    'tds_applicable', 'tds_rate_default', 'eway_bill_mandatory', 'round_off_limit'],
            'rcm': ['rcm_clause'],
            'sms': ['twilio_account_sid', 'twilio_auth_token', 'twilio_from_number'],
        }
        if form_type in field_groups:
            for key in field_groups[form_type]:
                if key in ['show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words',
                           'reverse_charge_applicable', 'rcm_on_transport', 'tds_applicable', 'eway_bill_mandatory']:
                    val = '1' if request.form.get(key) == 'on' else '0'
                else:
                    val = request.form.get(key, '')
                conn.execute("UPDATE settings SET value=? WHERE key=?", (val, key))
            conn.commit()

    users = conn.execute("SELECT id, username, role, is_admin, phone FROM users ORDER BY username").fetchall()
    s = _get_all_settings(conn)
    conn.close()

    fy_from = '2026-04-01'
    invoice_example = f"{s['invoice_prefix']}/2026/{int(s['next_invoice_number'] or 1):04d}" if s['invoice_prefix'] else ''
    return render_template('settings.html', company_name=s.get('company_name') or get_company_name(),
                            users=users, s=s, invoice_example=invoice_example, active='settings')

def _get_all_settings(conn):
    s = {}
    for key in ALL_SETTING_KEYS:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        s[key] = row['value'] if row else ''
    return s

@app.route('/settings/users/add', methods=['POST'])
def add_user():
    from werkzeug.security import generate_password_hash
    import datetime, sqlite3
    f = request.form
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password_hash, role, is_admin, phone, created_at) VALUES (?,?,?,?,?,?)",
                     (f.get('username'), generate_password_hash(f.get('password') or ''), f.get('role'),
                      1 if f.get('is_admin')=='on' else 0, (f.get('phone') or '').strip() or None,
                      datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    except sqlite3.IntegrityError:
        users = conn.execute("SELECT id, username, role, is_admin, phone FROM users ORDER BY username").fetchall()
        s = _get_all_settings(conn)
        invoice_example = f"{s['invoice_prefix']}/2026/{int(s['next_invoice_number'] or 1):04d}" if s['invoice_prefix'] else ''
        conn.close()
        return render_template('settings.html', company_name=s.get('company_name') or get_company_name(),
                                users=users, s=s, invoice_example=invoice_example, active='settings',
                                user_error=f"Username \"{f.get('username')}\" is already taken.")
    conn.close()
    return redirect(url_for('settings_page'))

@app.route('/settings/users/<int:user_id>/phone', methods=['POST'])
def update_user_phone(user_id):
    conn = get_db()
    conn.execute("UPDATE users SET phone=? WHERE id=?", ((request.form.get('phone') or '').strip() or None, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('settings_page'))

@app.route('/settings/users/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('settings_page'))

@app.route('/route-rates')
def route_rates():
    conn = get_db()
    from_f = request.args.get('from_loc', '')
    to_f = request.args.get('to_loc', '')
    type_f = request.args.get('type', '')

    LOCATION_ALIASES = {'RKL': 'ROURKELA', 'BBSR': 'BHUBANESWAR', 'SAGJOR': 'SAGJORE'}
    def clean_loc(raw):
        if not raw:
            return ''
        first_word = raw.split(' ')[0] if ' ' in raw else raw
        cleaned = first_word.upper().replace(',', '').strip()
        return LOCATION_ALIASES.get(cleaned, cleaned)

    trips = conn.execute("""SELECT from_loc, to_loc, type, rate FROM trips
                            WHERE rate_type='PER_MT' AND rate > 0
                            AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')""").fetchall()

    groups = {}
    for t in trips:
        cf, ct = clean_loc(t['from_loc']), clean_loc(t['to_loc'])
        key = (cf, ct, t['type'])
        groups.setdefault(key, []).append(t['rate'])

    filtered = []
    for (cf, ct, ttype), rates in groups.items():
        if from_f and from_f.upper() not in cf:
            continue
        if to_f and to_f.upper() not in ct:
            continue
        if type_f and ttype != type_f:
            continue
        filtered.append({'clean_from': cf, 'clean_to': ct, 'type': ttype,
                          'highest': max(rates), 'average': sum(rates)/len(rates),
                          'lowest': min(rates), 'trips': len(rates)})
    filtered.sort(key=lambda r: (r['clean_from'], r['clean_to']))

    # Summary stats across ALL routes (unfiltered), for the top cards
    all_rates_flat = [r for rates in groups.values() for r in rates]
    highest_entry = None
    lowest_entry = None
    for (cf, ct, ttype), rates in groups.items():
        h = max(rates)
        l = min(rates)
        if highest_entry is None or h > highest_entry[2]:
            highest_entry = (cf, ct, h)
        if lowest_entry is None or l < lowest_entry[2]:
            lowest_entry = (cf, ct, l)
    avg_rate_overall = round(sum(all_rates_flat) / len(all_rates_flat), 0) if all_rates_flat else 0
    total_routes = len(groups)

    # Rate breakdown: Line vs Local average
    line_rates = [r for (cf, ct, ttype), rates in groups.items() if ttype == 'Line' for r in rates]
    local_rates = [r for (cf, ct, ttype), rates in groups.items() if ttype == 'Local' for r in rates]
    line_avg = round(sum(line_rates) / len(line_rates), 0) if line_rates else 0
    local_avg = round(sum(local_rates) / len(local_rates), 0) if local_rates else 0
    line_pct = round(len(line_rates) / len(all_rates_flat) * 100, 0) if all_rates_flat else 0

    # Monthly average rate trend
    monthly_trips = conn.execute("""SELECT substr(date,1,7) as month, AVG(rate) as avg_rate FROM trips
                                    WHERE rate_type='PER_MT' AND rate > 0
                                    AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')
                                    GROUP BY month ORDER BY month""").fetchall()
    trend_points = [{'month': m['month'], 'rate': round(m['avg_rate'] or 0)} for m in monthly_trips]
    svg_points = ''
    svg_labels = []
    if trend_points:
        rates = [p['rate'] for p in trend_points]
        rmin, rmax = min(rates), max(rates)
        rrange = (rmax - rmin) or 1
        n = len(trend_points)
        chart_w, chart_h = 400, 140
        coords = []
        for i, p in enumerate(trend_points):
            x = (i / (n - 1) * chart_w) if n > 1 else chart_w / 2
            y = chart_h - ((p['rate'] - rmin) / rrange * (chart_h - 20)) - 10
            coords.append((round(x, 1), round(y, 1)))
        svg_points = ' '.join(f"{x},{y}" for x, y in coords)
        svg_labels = [p['month'][5:] + '/' + p['month'][2:4] for p in trend_points]

    conn.close()
    return render_template('route_rates.html', rows=filtered, f_from=from_f, f_to=to_f, f_type=type_f,
                            highest_entry=highest_entry, lowest_entry=lowest_entry, avg_rate_overall=avg_rate_overall,
                            total_routes=total_routes, line_avg=line_avg, local_avg=local_avg, line_pct=line_pct,
                            trend_points=trend_points, svg_points=svg_points, svg_labels=svg_labels, active='route-rates')

@app.route('/idle-tracker')
def idle_tracker():
    args = request.args.to_dict()
    return redirect(url_for('fleet_utilization', **args))

@app.route('/empty-runs')
def empty_runs():
    args = request.args.to_dict()
    return redirect(url_for('fleet_utilization', **args))

@app.route('/fleet-utilization')
def fleet_utilization():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2026-07-31')
    vehicle_f = request.args.get('vehicle', '')
    type_f = request.args.get('type', '')

    # From Date must never be after To Date — swap rather than let the math go negative.
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    d2 = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()

    # ---------- Idle tracker: active days = union of each trip's start..end_date span ----------
    query = "SELECT id, vehicle_no, type, registration_date FROM vehicles WHERE type IN ('Line','Local')"
    params = []
    if type_f:
        query += " AND type = ?"
        params.append(type_f)
    if vehicle_f:
        query += " AND vehicle_no = ?"
        params.append(vehicle_f)
    own_vehicles = conn.execute(query, params).fetchall()

    rows = []
    for v in own_vehicles:
        active_days, idle_days, total_days_v = _vehicle_active_idle_days(
            conn, v['id'], v['registration_date'], date_from, date_to)
        last_trip = conn.execute("SELECT MAX(date) FROM trips WHERE vehicle_id=?", (v['id'],)).fetchone()[0]
        status = 'Idle' if idle_days >= active_days else 'Active'
        empty_run_count = conn.execute("""SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND lr_number LIKE 'Empty%'
                                          AND date>=? AND date<=?""", (v['id'], date_from, date_to)).fetchone()[0]
        vehicle_trip_count = conn.execute("SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND date>=? AND date<=?",
                                           (v['id'], date_from, date_to)).fetchone()[0]
        empty_pct = round(empty_run_count / vehicle_trip_count * 100, 1) if vehicle_trip_count else 0
        idle_pct = round(idle_days / total_days_v * 100, 1) if total_days_v else 0
        rows.append({'vehicle_no': v['vehicle_no'], 'type': v['type'], 'group': v['type'],
                      'active_days': active_days, 'idle_days': idle_days, 'total_days': total_days_v,
                      'empty_run_count': empty_run_count, 'empty_pct': empty_pct, 'idle_pct': idle_pct,
                      'last_trip': last_trip, 'status': status})
    rows.sort(key=lambda r: r['idle_days'], reverse=True)

    total_vehicles = len(rows)
    active_vehicles = sum(1 for r in rows if r['status'] == 'Active')
    idle_vehicles = sum(1 for r in rows if r['status'] == 'Idle')
    active_pct = round((active_vehicles / total_vehicles * 100), 1) if total_vehicles > 0 else 0
    idle_vehicles_pct = round((idle_vehicles / total_vehicles * 100), 1) if total_vehicles > 0 else 0
    total_active_days = sum(r['active_days'] for r in rows)
    total_idle_days = sum(r['idle_days'] for r in rows)
    total_all_days = total_active_days + total_idle_days
    active_days_pct = round((total_active_days / total_all_days * 100), 1) if total_all_days > 0 else 0
    idle_days_pct = round((total_idle_days / total_all_days * 100), 1) if total_all_days > 0 else 0
    avg_idle_days = round(total_idle_days / total_vehicles, 1) if total_vehicles > 0 else 0
    most_idle = sorted(rows, key=lambda r: r['idle_days'], reverse=True)[:5]

    # ---------- Empty runs (same formulas as the old Empty Runs page — LR number starting with "Empty") ----------
    eq = """SELECT t.date, v.vehicle_no, t.from_loc, t.to_loc, t.fuel_amount, t.toll
            FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
            WHERE t.lr_number LIKE 'Empty%'"""
    eparams = []
    if date_from:
        eq += " AND t.date >= ?"; eparams.append(date_from)
    if date_to:
        eq += " AND t.date <= ?"; eparams.append(date_to)
    if vehicle_f:
        eq += " AND v.vehicle_no = ?"; eparams.append(vehicle_f)
    eq += " ORDER BY t.date DESC"
    empty_rows = conn.execute(eq, eparams).fetchall()
    total_fuel = sum(r['fuel_amount'] or 0 for r in empty_rows)
    total_toll = sum(r['toll'] or 0 for r in empty_rows)
    total_empty_cost = total_fuel + total_toll
    empty_run_trips = len(empty_rows)

    total_trips_period = conn.execute("SELECT COUNT(*) FROM trips WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    empty_run_pct_overall = round(empty_run_trips / total_trips_period * 100, 1) if total_trips_period else 0

    # ---------- 12-week idle trend (own vehicles, same corrected active/idle formula, bucketed weekly) ----------
    trend = []
    for i in range(11, -1, -1):
        w_end = d2 - datetime.timedelta(days=7 * i)
        w_start = w_end - datetime.timedelta(days=6)
        week_idle = 0
        for v in own_vehicles:
            _, w_idle, _ = _vehicle_active_idle_days(conn, v['id'], v['registration_date'],
                                                       w_start.isoformat(), w_end.isoformat())
            week_idle += w_idle
        trend.append({'label': w_start.strftime('%-d %b'), 'idle_days': week_idle})
    trend_max = max([t['idle_days'] for t in trend], default=1) or 1
    chart_w, chart_h, pad_l, pad_r, pad_t, pad_b = 700, 220, 30, 20, 20, 34
    plot_w, plot_h, n = chart_w - pad_l - pad_r, chart_h - pad_t - pad_b, len(trend)
    chart_bottom = pad_t + plot_h
    for i, t in enumerate(trend):
        t['x'] = round(pad_l + (i * plot_w / (n - 1) if n > 1 else 0), 1)
        t['y'] = round(pad_t + plot_h - (t['idle_days'] / trend_max * plot_h if trend_max else 0), 1)
    y_ticks = []
    for k in range(5):
        tick_val = round(trend_max * k / 4)
        y_ticks.append({'value': tick_val, 'y': round(pad_t + plot_h - (tick_val / trend_max * plot_h if trend_max else 0), 1)})

    all_vehicles_list = conn.execute("SELECT vehicle_no FROM vehicles WHERE type IN ('Line','Local') ORDER BY vehicle_no").fetchall()
    conn.close()
    return render_template('fleet_utilization.html',
        rows=rows, most_idle=most_idle, empty_rows=empty_rows, trend=trend, trend_max=trend_max,
        chart_bottom=chart_bottom, y_ticks=y_ticks,
        total_vehicles=total_vehicles, active_vehicles=active_vehicles, idle_vehicles=idle_vehicles,
        active_pct=active_pct, idle_vehicles_pct=idle_vehicles_pct,
        total_active_days=total_active_days, total_idle_days=total_idle_days, active_days_pct=active_days_pct, idle_days_pct=idle_days_pct,
        avg_idle_days=avg_idle_days, empty_run_trips=empty_run_trips, empty_run_pct_overall=empty_run_pct_overall,
        total_fuel=total_fuel, total_toll=total_toll, total_empty_cost=total_empty_cost,
        f_date_from=date_from, f_date_to=date_to, f_vehicle=vehicle_f, f_type=type_f, vehicles=all_vehicles_list,
        active='fleet-utilization')

@app.route('/fleet-utilization/export')
def export_fleet_utilization():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2026-07-31')
    vehicle_f = request.args.get('vehicle', '')
    type_f = request.args.get('type', '')
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    query = "SELECT id, vehicle_no, type, registration_date FROM vehicles WHERE type IN ('Line','Local')"
    params = []
    if type_f:
        query += " AND type = ?"; params.append(type_f)
    if vehicle_f:
        query += " AND vehicle_no = ?"; params.append(vehicle_f)
    own_vehicles = conn.execute(query, params).fetchall()

    rows = []
    for v in own_vehicles:
        active_days, idle_days, total_days_v = _vehicle_active_idle_days(
            conn, v['id'], v['registration_date'], date_from, date_to)
        last_trip = conn.execute("SELECT MAX(date) FROM trips WHERE vehicle_id=?", (v['id'],)).fetchone()[0]
        status = 'Idle' if idle_days >= active_days else 'Active'
        empty_run_count = conn.execute("""SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND lr_number LIKE 'Empty%'
                                          AND date>=? AND date<=?""", (v['id'], date_from, date_to)).fetchone()[0]
        rows.append((v['vehicle_no'], v['type'], active_days, idle_days, total_days_v, empty_run_count, last_trip or '', status))
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Fleet Utilization"
    headers = ["Vehicle No", "Type", "Active Days", "Idle Days", "Total Days", "Empty Run Trips", "Last Trip Date", "Status"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='fleet_utilization.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/best-routes')
def best_routes():
    conn = get_db()
    type_f = request.args.get('type', 'Line')

    LOCATION_ALIASES = {'RKL': 'ROURKELA', 'BBSR': 'BHUBANESWAR', 'SAGJOR': 'SAGJORE'}
    def clean_loc(raw):
        if not raw:
            return ''
        first_word = raw.split(' ')[0] if ' ' in raw else raw
        cleaned = first_word.upper().replace(',', '').strip()
        return LOCATION_ALIASES.get(cleaned, cleaned)

    trips = conn.execute("""SELECT from_loc, to_loc, billed_amount FROM trips
                            WHERE type=? AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')""", (type_f,)).fetchall()
    conn.close()
    groups = {}
    for t in trips:
        key = (clean_loc(t['from_loc']), clean_loc(t['to_loc']))
        groups.setdefault(key, []).append(t['billed_amount'] or 0)

    all_rows = []
    for (cf, ct), amounts in groups.items():
        all_rows.append({'clean_from': cf, 'clean_to': ct, 'trips': len(amounts),
                          'total_billed': sum(amounts), 'avg_billed': sum(amounts)/len(amounts)})
    all_rows.sort(key=lambda r: r['total_billed'], reverse=True)

    total_routes = len(all_rows)
    total_trips_all = sum(r['trips'] for r in all_rows)
    most_profitable = max(all_rows, key=lambda r: r['avg_billed'], default=None)
    highest_billed = all_rows[0] if all_rows else None

    page = max(1, int(request.args.get('page', 1)))
    per_page = 10
    total_pages = max(1, (total_routes + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = all_rows[(page-1)*per_page : page*per_page]

    return render_template('best_routes.html', rows=rows, f_type=type_f, total_routes=total_routes,
                            total_trips_all=total_trips_all, most_profitable=most_profitable, highest_billed=highest_billed,
                            page=page, total_pages=total_pages, active='best-routes')

@app.route('/trips/edit/<int:trip_id>', methods=['GET', 'POST'])
def edit_trip(trip_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        def n(key):
            return float(f.get(key) or 0)
        def get_or_create_vehicle(vno):
            if not vno or not vno.strip():
                return None
            vno = vno.strip()
            row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
            if row:
                return row[0]
            cur = conn.execute("INSERT INTO vehicles (vehicle_no) VALUES (?)", (vno,))
            return cur.lastrowid

        vehicle_id = get_or_create_vehicle(f.get('vehicle_no'))
        party_id = get_or_create_party(conn, f.get('party_name'))
        fuel_vendor_id = get_or_create_vendor(conn, f.get('fuel_vendor'))
        driveradv_vendor_id = get_or_create_vendor(conn, f.get('driver_adv_vendor'))

        quantity = n('quantity')
        rate = n('rate')
        rate_type = f.get('rate_type')
        fixed_rate_amount = n('fixed_rate_amount')
        freight = fixed_rate_amount if rate_type == 'FIXED' else quantity * rate
        total_charges = (n('driver_payment')+n('detention_charges')+n('gps_cost')+n('loading_charge')+
                          n('unloading_charge')+n('police_charges')+n('sim_tracking')+n('union_charges')+
                          n('weight_charges')+n('other_charges'))
        total_deductions = (n('brokerage')+n('builty_commission')+n('late_fees')+n('material_damage')+
                             n('shortage_amount')+n('tds')+n('other_deductions'))
        billed_amount = freight + total_charges - total_deductions

        owner_vendor_id = get_or_create_vendor(conn, f.get('owner_name')) if f.get('owner_name') else None

        conn.execute("""UPDATE trips SET
            date=?, lr_number=?, vehicle_id=?, type=?, party_id=?, from_loc=?, to_loc=?, quantity=?, rate=?,
            driver_name=?, material=?, rate_type=?, billed_amount=?,
            driver_payment=?, detention_charges=?, gps_cost=?, loading_charge=?, unloading_charge=?,
            police_charges=?, sim_tracking=?, union_charges=?, weight_charges=?, other_charges=?,
            brokerage=?, builty_commission=?, late_fees=?, material_damage=?, shortage_amount=?, shortage_qty=?, tds=?, other_deductions=?,
            fuel_amount=?, driver_adv_amount=?, party_advance=?, payment_received=?, fuel_vendor_id=?, driver_adv_vendor_id=?,
            owner_name=?, fixed_rate_amount=?, owner_rate=?, paid_to_owner=?, owner_vendor_id=?,
            agent_commission=?, builty_expense=?, conductor_expense=?, fine=?, labour_charges=?, parking=?, puncture=?,
            toll=?, urea=?, loading_expense=?, unloading_expense=?, wear_tear=?, weighbridge_charges=?, other_expense=?,
            lr_received=?
            WHERE id=?""",
            (f.get('date'), f.get('lr_number'), vehicle_id, f.get('type'), party_id, f.get('from_loc'), f.get('to_loc'),
             quantity, rate, f.get('driver_name'), f.get('material'), rate_type, billed_amount,
             n('driver_payment'), n('detention_charges'), n('gps_cost'), n('loading_charge'), n('unloading_charge'),
             n('police_charges'), n('sim_tracking'), n('union_charges'), n('weight_charges'), n('other_charges'),
             n('brokerage'), n('builty_commission'), n('late_fees'), n('material_damage'), n('shortage_amount'),
             n('shortage_qty'), n('tds'), n('other_deductions'),
             n('fuel_amount'), n('driver_adv_amount'), n('party_advance'), n('payment_received'), fuel_vendor_id, driveradv_vendor_id,
             f.get('owner_name'), fixed_rate_amount, n('owner_rate'), n('paid_to_owner'), owner_vendor_id,
             n('agent_commission'), n('builty_expense'), n('conductor_expense'), n('fine'), n('labour_charges'),
             n('parking'), n('puncture'), n('toll'), n('urea'), n('loading_expense'), n('unloading_expense'),
             n('wear_tear'), n('weighbridge_charges'), n('other_expense'),
             f.get('lr_received') or None, trip_id))
        conn.commit()
        conn.close()
        return redirect(url_for('trips_list'))

    trip = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name,
                           fv.name as fuel_vendor_name, dv.name as driveradv_vendor_name
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           WHERE t.id=?""", (trip_id,)).fetchone()
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn2 = get_db()
    employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
    conn2.close()
    return render_template('edit_trip.html', t=trip, combined_names=combined_names, employees=employees, active='trips')

@app.route('/business-performance')
def business_performance():
    conn = get_db()
    # Default to the last fully completed calendar month (standard CEO-dashboard convention).
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    default_from, default_to = _month_bounds(last_month_end.year, last_month_end.month)
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or default_to

    curr = _period_financials(conn, date_from, date_to)
    prev_from, prev_to = _shift_period_back(date_from, date_to)
    prev = _period_financials(conn, prev_from, prev_to)
    q_from, q_to = _quarter_bounds(datetime.datetime.strptime(date_to, '%Y-%m-%d').date())
    pq_from, pq_to = _shift_period_back(q_from, q_to)
    curr_q = _period_financials(conn, q_from, q_to)
    prev_q = _period_financials(conn, pq_from, pq_to)
    y_from, y_to = _shift_period_year(date_from, date_to)
    curr_y = _period_financials(conn, y_from, y_to)

    days_in_period = (datetime.datetime.strptime(date_to, '%Y-%m-%d').date() -
                       datetime.datetime.strptime(date_from, '%Y-%m-%d').date()).days + 1

    revenue_growth = _pct_growth(curr['revenue'], prev['revenue'])
    profit_growth = _pct_growth(curr['net_profit'], prev['net_profit'])
    expense_growth = _pct_growth(curr['total_expenses'], prev['total_expenses'])
    profit_growth_qoq = _pct_growth(curr_q['net_profit'], prev_q['net_profit'])
    profit_growth_yoy = _pct_growth(curr['net_profit'], curr_y['net_profit'])
    revenue_growth_qoq = _pct_growth(curr_q['revenue'], prev_q['revenue'])
    revenue_growth_yoy = _pct_growth(curr['revenue'], curr_y['revenue'])

    # Outstanding receivables / payables — all-time snapshot balances, same formula as accounts().
    party_rows = conn.execute("""SELECT p.id, p.name,
        (SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(payment_received)+SUM(party_advance),0) FROM trips WHERE party_id=p.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE party_id=p.id AND payment_type='received') +
        COALESCE(p.opening_balance,0) as balance
        FROM parties p""").fetchall()
    party_balance = {r['id']: (r['balance'] or 0) for r in party_rows}
    party_name = {r['id']: r['name'] for r in party_rows}
    total_receivables = sum(b for b in party_balance.values() if b > 0)

    vendor_rows = conn.execute("""SELECT v.id, v.name,
        (SELECT COALESCE(SUM(m.amount),0) FROM maintenance m WHERE m.vendor_id=v.id) +
        (SELECT COALESCE(SUM(fuel_amount),0) FROM trips WHERE fuel_vendor_id=v.id) +
        (SELECT COALESCE(SUM(driver_adv_amount),0) FROM trips WHERE driver_adv_vendor_id=v.id) +
        (SELECT COALESCE(SUM(CASE WHEN rate_type='FIXED' THEN fixed_rate_amount ELSE owner_rate*quantity END),0) FROM trips WHERE owner_vendor_id=v.id) -
        (SELECT COALESCE(SUM(m.paid_amount),0) FROM maintenance m WHERE m.vendor_id=v.id) -
        (SELECT COALESCE(SUM(paid_to_owner),0) FROM trips WHERE owner_vendor_id=v.id) -
        (SELECT COALESCE(SUM(amount-COALESCE(allocated_amount,0)),0) FROM payments WHERE vendor_id=v.id AND payment_type='paid') -
        COALESCE(v.opening_balance,0) as balance
        FROM vendors v WHERE v.linked_party_id IS NULL""").fetchall()
    total_payables = sum((r['balance'] or 0) for r in vendor_rows if (r['balance'] or 0) > 0)

    # Cash movement for the selected period.
    cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                   (date_from, date_to)).fetchone()[0]
    cash_collected += sum(t['party_advance'] or 0 for t in curr['trips'])
    cash_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='paid' AND date>=? AND date<=?",
                              (date_from, date_to)).fetchone()[0]
    cash_paid += curr['maint'] + curr['overheads'] + curr['salaries']

    prev_cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                        (prev_from, prev_to)).fetchone()[0]
    prev_cash_collected += sum(t['party_advance'] or 0 for t in prev['trips'])
    collection_growth = _pct_growth(cash_collected, prev_cash_collected)
    collection_efficiency = round(cash_collected / curr['revenue'] * 100, 2) if curr['revenue'] else 0

    avg_revenue_per_trip = round(curr['revenue'] / curr['trip_count'], 2) if curr['trip_count'] else 0
    avg_profit_per_trip = round(curr['net_profit'] / curr['trip_count'], 2) if curr['trip_count'] else 0
    operating_ratio = round(curr['total_expenses'] / curr['revenue'] * 100, 2) if curr['revenue'] else 0
    ebitda = curr['net_profit']  # no interest/tax/depreciation tracked separately, so this is profit-based estimate

    # Financial Overview / Monthly Profit Trend — trailing 6 months ending at date_to's month.
    end_d = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    trailing_months = []
    yy_, mm_ = end_d.year, end_d.month
    for i in range(5, -1, -1):
        mm = mm_ - i
        yy = yy_
        while mm <= 0:
            mm += 12
            yy -= 1
        trailing_months.append((yy, mm))
    monthly_rows = []
    for (yy, mm) in trailing_months:
        mf, mt = _month_bounds(yy, mm)
        mfin = _period_financials(conn, mf, mt)
        monthly_rows.append({'label': calendar.month_abbr[mm], 'revenue': mfin['revenue'],
                              'expenses': mfin['total_expenses'], 'net_profit': mfin['net_profit']})

    # Top customers by revenue (period).
    party_period = {}
    for t in curr['trips']:
        if not t['party_id']:
            continue
        d = party_period.setdefault(t['party_id'], {'revenue': 0, 'direct_cost': 0, 'trips': 0})
        d['revenue'] += t['billed_amount'] or 0
        d['trips'] += 1
        direct = (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0) + (t['toll'] or 0) + (t['parking'] or 0)
        if t['type'] == 'Market':
            direct += (t['fixed_rate_amount'] or 0) if t['rate_type'] == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        d['direct_cost'] += direct
    top_customers = []
    for pid, d in sorted(party_period.items(), key=lambda kv: kv[1]['revenue'], reverse=True)[:6]:
        top_customers.append({
            'name': party_name.get(pid, '—'), 'revenue': d['revenue'],
            'pct': round(d['revenue'] / curr['revenue'] * 100, 2) if curr['revenue'] else 0,
            'profit': d['revenue'] - d['direct_cost'],
            'outstanding': max(party_balance.get(pid, 0), 0),
        })
    customers_other_revenue = curr['revenue'] - sum(c['revenue'] for c in top_customers)
    customer_growth = _pct_growth(len(party_period), len(prev['party_ids']))

    # Expense breakdown (period).
    exp_cats = [
        ('Fuel', curr['fuel'], '#2a78d6'), ('Driver Advance', curr['driver_adv'], '#eb6834'),
        ('Maintenance', curr['maint'], '#1baf7a'), ('Salary', curr['salaries'], '#eda100'),
        ('Toll / Parking', curr['toll'] + curr['parking'], '#e87ba4'),
        ('Other Expenses', curr['misc'] + curr['overheads'] + curr['owner_cost'], '#008300'),
    ]
    exp_total = sum(v for _, v, _ in exp_cats) or 1
    expense_breakdown = [{'label': l, 'amount': v, 'color': c, 'pct': round(v / exp_total * 100, 2)} for l, v, c in exp_cats]

    # Collections analytics — age each party's *net* balance (party_balance, already netted against
    # opening balance and unallocated payments) off their oldest still-pending trip, so overdue_total
    # stays a subset of total_receivables instead of double-counting against raw per-trip pending.
    all_trips = conn.execute("SELECT id, date, lr_number, party_id, billed_amount, payment_received, party_advance FROM trips").fetchall()
    party_pending_trips = {}
    for t in all_trips:
        if not t['party_id'] or not t['date']:
            continue
        pending = (t['billed_amount'] or 0) - (t['payment_received'] or 0) - (t['party_advance'] or 0)
        if pending <= 0.01:
            continue
        party_pending_trips.setdefault(t['party_id'], []).append(t)

    overdue_total = 0
    oldest_outstanding = None
    for pid, bal in party_balance.items():
        if bal <= 0.01:
            continue
        trips_for_party = party_pending_trips.get(pid)
        if not trips_for_party:
            continue
        oldest_trip = min(trips_for_party, key=lambda t: t['date'])
        t_date = datetime.datetime.strptime(oldest_trip['date'], '%Y-%m-%d').date()
        age = (end_d - t_date).days
        if age > 30:
            overdue_total += bal
        if oldest_outstanding is None or t_date < oldest_outstanding['date']:
            oldest_outstanding = {'date': t_date, 'lr': oldest_trip['lr_number'] or f"Trip #{oldest_trip['id']}", 'age': age}

    alloc_rows = conn.execute("""SELECT pay.date as pay_date, t.date as trip_date FROM payment_allocations pa
                                 JOIN payments pay ON pa.payment_id = pay.id
                                 JOIN trips t ON pa.trip_id = t.id
                                 WHERE pay.date>=? AND pay.date<=?""", (date_from, date_to)).fetchall()
    if not alloc_rows:
        alloc_rows = conn.execute("""SELECT pay.date as pay_date, t.date as trip_date FROM payment_allocations pa
                                     JOIN payments pay ON pa.payment_id = pay.id
                                     JOIN trips t ON pa.trip_id = t.id""").fetchall()
    collection_days_list = []
    for r in alloc_rows:
        if not r['pay_date'] or not r['trip_date']:
            continue
        pd_ = datetime.datetime.strptime(r['pay_date'], '%Y-%m-%d').date()
        td_ = datetime.datetime.strptime(r['trip_date'], '%Y-%m-%d').date()
        collection_days_list.append((pd_ - td_).days)
    avg_collection_days = round(sum(collection_days_list) / len(collection_days_list), 1) if collection_days_list else None

    # Highest / lowest revenue & profit month across all months with data.
    all_months_rows = conn.execute("SELECT DISTINCT substr(date,1,7) as ym FROM trips ORDER BY ym").fetchall()
    month_fin = []
    for r in all_months_rows:
        yy, mm = int(r['ym'][:4]), int(r['ym'][5:7])
        mf, mt = _month_bounds(yy, mm)
        f = _period_financials(conn, mf, mt)
        month_fin.append({'label': f"{calendar.month_abbr[mm]} {yy}", 'revenue': f['revenue'], 'net_profit': f['net_profit']})
    highest_revenue_month = max(month_fin, key=lambda r: r['revenue']) if month_fin else None
    highest_profit_month = max(month_fin, key=lambda r: r['net_profit']) if month_fin else None
    lowest_profit_month = min(month_fin, key=lambda r: r['net_profit']) if month_fin else None

    # Highest collection / expense day within the selected period.
    day_collect = {}
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM payments WHERE payment_type='received' AND date>=? AND date<=? GROUP BY date",
                           (date_from, date_to)).fetchall():
        day_collect[r['date']] = day_collect.get(r['date'], 0) + (r['amt'] or 0)
    for t in curr['trips']:
        if t['party_advance']:
            day_collect[t['date']] = day_collect.get(t['date'], 0) + t['party_advance']
    highest_collection_day = max(day_collect.items(), key=lambda kv: kv[1]) if day_collect else None

    day_expense = {}
    for t in curr['trips']:
        direct = ((t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0) + (t['toll'] or 0) + (t['parking'] or 0) +
                  (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) + (t['fine'] or 0) +
                  (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) + (t['loading_expense'] or 0) +
                  (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) + (t['weighbridge_charges'] or 0) +
                  (t['other_expense'] or 0) + (t['permit_charges'] or 0))
        if t['type'] == 'Market':
            direct += (t['fixed_rate_amount'] or 0) if t['rate_type'] == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        day_expense[t['date']] = day_expense.get(t['date'], 0) + direct
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM maintenance WHERE date>=? AND date<=? GROUP BY date", (date_from, date_to)).fetchall():
        day_expense[r['date']] = day_expense.get(r['date'], 0) + (r['amt'] or 0)
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM overheads WHERE date>=? AND date<=? GROUP BY date", (date_from, date_to)).fetchall():
        day_expense[r['date']] = day_expense.get(r['date'], 0) + (r['amt'] or 0)
    highest_expense_day = max(day_expense.items(), key=lambda kv: kv[1]) if day_expense else None

    avg_daily_revenue = round(curr['revenue'] / days_in_period, 2) if days_in_period else 0
    avg_daily_profit = round(curr['net_profit'] / days_in_period, 2) if days_in_period else 0

    own_vehicle_count = conn.execute("SELECT COUNT(*) FROM vehicles WHERE type IN ('Line','Local')").fetchone()[0]
    own_revenue = sum(t['billed_amount'] or 0 for t in curr['trips'] if t['type'] in ('Line', 'Local'))
    avg_revenue_per_vehicle = round(own_revenue / own_vehicle_count, 2) if own_vehicle_count else 0
    employee_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    avg_revenue_per_employee = round(curr['revenue'] / employee_count, 2) if employee_count else 0

    invoice_count_curr = conn.execute("SELECT COUNT(*) FROM invoice_batches WHERE invoice_date>=? AND invoice_date<=?",
                                       (date_from, date_to)).fetchone()[0]
    invoice_count_prev = conn.execute("SELECT COUNT(*) FROM invoice_batches WHERE invoice_date>=? AND invoice_date<=?",
                                       (prev_from, prev_to)).fetchone()[0]
    invoice_growth = _pct_growth(invoice_count_curr, invoice_count_prev)
    avg_invoice_value = round(curr['revenue'] / invoice_count_curr, 2) if invoice_count_curr else avg_revenue_per_trip
    trip_growth = _pct_growth(curr['trip_count'], prev['trip_count'])

    # Rule-based ("AI") insights, generated from the numbers above — not a live model call.
    insights = []
    if revenue_growth is not None:
        if revenue_growth >= 0:
            insights.append({'kind': 'good', 'text': f"Revenue increased by {revenue_growth}% compared to last month."})
        else:
            insights.append({'kind': 'bad', 'text': f"Revenue dropped by {abs(revenue_growth)}% compared to last month."})
    if profit_growth is not None:
        if profit_growth >= 0:
            insights.append({'kind': 'good', 'text': f"Profit increased by {profit_growth}% due to better revenue and controlled expenses."})
        else:
            insights.append({'kind': 'bad', 'text': f"Profit fell by {abs(profit_growth)}% — expenses grew faster than revenue."})
    prev_cats = {
        'Fuel': prev['fuel'], 'Driver Advance': prev['driver_adv'], 'Maintenance': prev['maint'],
        'Salary': prev['salaries'], 'Toll / Parking': prev['toll'] + prev['parking'],
        'Other Expenses': prev['misc'] + prev['overheads'] + prev['owner_cost'],
    }
    cat_growth = []
    for cat in expense_breakdown:
        g = _pct_growth(cat['amount'], prev_cats.get(cat['label'], 0))
        if g is not None and g > 0:
            cat_growth.append((cat['label'], g))
    if cat_growth:
        top_cat, top_g = max(cat_growth, key=lambda x: x[1])
        insights.append({'kind': 'warn', 'text': f"{top_cat} expenses increased by {top_g}%. Review costs in this category."})
    if total_receivables > 0:
        insights.append({'kind': 'info', 'text': f"Outstanding receivables stand at ₹{total_receivables:,.0f}. Focus on collections."})
    if avg_collection_days is not None and avg_collection_days > 30:
        insights.append({'kind': 'bad', 'text': f"Average collection period is {avg_collection_days} days. Consider tighter follow-up on pending invoices."})

    # Business Health Score — five sub-scores, each 0-100, averaged.
    def clamp(v, lo=0, hi=100):
        return max(lo, min(hi, v))
    profitability_score = clamp(round(curr['margin'] / 30 * 100)) if curr['revenue'] else 0
    financial_score = clamp(round(200 - 2 * operating_ratio)) if curr['revenue'] else 0
    collection_score = clamp(round(100 - (overdue_total / total_receivables * 100))) if total_receivables > 0 else 100
    cashflow_score = clamp(round(50 + (cash_collected - cash_paid) / curr['revenue'] * 100)) if curr['revenue'] else 50
    growth_score = clamp(round(50 + (revenue_growth or 0) * 2))
    sub_scores = [
        {'label': 'Financial Health', 'score': financial_score},
        {'label': 'Profitability', 'score': profitability_score},
        {'label': 'Collection Health', 'score': collection_score},
        {'label': 'Cash Flow', 'score': cashflow_score},
        {'label': 'Growth', 'score': growth_score},
    ]
    overall_score = round(sum(s['score'] for s in sub_scores) / len(sub_scores))
    if overall_score >= 80:
        health_label, health_kind = 'Healthy', 'good'
    elif overall_score >= 60:
        health_label, health_kind = 'Stable', 'info'
    elif overall_score >= 40:
        health_label, health_kind = 'Needs Attention', 'warn'
    else:
        health_label, health_kind = 'Critical', 'bad'
    weakest = min(sub_scores, key=lambda s: s['score'])
    suggestions = []
    if weakest['score'] < 70:
        tips = {
            'Financial Health': 'Bring operating expenses down relative to revenue to strengthen financial health.',
            'Profitability': 'Review pricing on low-margin routes and trim direct trip costs to lift profitability.',
            'Collection Health': 'Chase overdue receivables past 30 days — this is dragging down collection health.',
            'Cash Flow': 'Cash paid is outpacing cash collected this period — prioritise follow-up on pending payments.',
            'Growth': 'Revenue growth has slowed — look for new routes or repeat business with top customers.',
        }
        suggestions.append(f"{weakest['label']} is your weakest score ({weakest['score']}). {tips.get(weakest['label'], '')}")
    if overall_score >= 80:
        suggestions.append('Great job! Your business is performing well. Keep focusing on collections and cost optimization.')

    fy_map = {'2026-04-01|2027-03-31': ('2026-04-01', '2027-03-31'), '2025-04-01|2026-03-31': ('2025-04-01', '2026-03-31')}
    f_fy = ''
    for key, (fy_from, fy_to) in fy_map.items():
        if date_from == fy_from and date_to == fy_to:
            f_fy = key
            break

    conn.close()
    return render_template('business_performance.html',
        f_date_from=date_from, f_date_to=date_to, f_fy=f_fy,
        total_revenue=curr['revenue'], net_profit=curr['net_profit'], profit_margin=curr['margin'],
        total_receivables=total_receivables, total_payables=total_payables,
        cash_collected=cash_collected, cash_paid=cash_paid, collection_efficiency=collection_efficiency,
        revenue_growth=revenue_growth, expense_growth=expense_growth, profit_growth=profit_growth,
        avg_revenue_per_trip=avg_revenue_per_trip, avg_profit_per_trip=avg_profit_per_trip,
        operating_ratio=operating_ratio, ebitda=ebitda, trip_count=curr['trip_count'],
        monthly_rows=monthly_rows,
        profit_growth_mom=profit_growth, profit_growth_qoq=profit_growth_qoq, profit_growth_yoy=profit_growth_yoy,
        revenue_growth_mom=revenue_growth, revenue_growth_qoq=revenue_growth_qoq, revenue_growth_yoy=revenue_growth_yoy,
        curr_revenue=curr['revenue'], prev_revenue=prev['revenue'], revenue_diff=curr['revenue'] - prev['revenue'],
        curr_q_revenue=curr_q['revenue'], prev_q_revenue=prev_q['revenue'], curr_y_revenue=curr_y['revenue'],
        top_customers=top_customers, customers_other_revenue=customers_other_revenue,
        expense_breakdown=expense_breakdown, exp_total=exp_total,
        overdue_total=overdue_total, oldest_outstanding=oldest_outstanding, avg_collection_days=avg_collection_days,
        customer_growth=customer_growth, trip_growth=trip_growth, invoice_growth=invoice_growth, collection_growth=collection_growth,
        highest_revenue_month=highest_revenue_month, highest_profit_month=highest_profit_month, lowest_profit_month=lowest_profit_month,
        highest_collection_day=highest_collection_day, highest_expense_day=highest_expense_day,
        avg_daily_revenue=avg_daily_revenue, avg_daily_profit=avg_daily_profit, avg_invoice_value=avg_invoice_value,
        avg_revenue_per_vehicle=avg_revenue_per_vehicle, avg_revenue_per_employee=avg_revenue_per_employee,
        insights=insights, sub_scores=sub_scores, overall_score=overall_score, health_label=health_label, health_kind=health_kind,
        suggestions=suggestions,
        active='business-performance')

@app.route('/export')
def export_excel():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io

    conn = get_db()
    wb = Workbook()

    navy = "1B2A4A"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=navy)

    # Trips sheet
    ws = wb.active
    ws.title = "Trips"
    headers = ["Date","LR Number","Vehicle","Party","From","To","Quantity","Rate","Billed Amount",
               "Fuel Amount","Fuel Vendor","Driver Adv Amount","Driver Adv Vendor","Party Advance","Payment Received"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows = conn.execute("""SELECT t.date, t.lr_number, v.vehicle_no, p.name, t.from_loc, t.to_loc,
                           t.quantity, t.rate, t.billed_amount, t.fuel_amount, fv.name, t.driver_adv_amount,
                           dv.name, t.party_advance, t.payment_received
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           ORDER BY t.date""").fetchall()
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)

    # Maintenance sheet
    ws2 = wb.create_sheet("Maintenance")
    headers2 = ["Date","Vehicle","Category","Amount","Paid Amount","Vendor","Notes"]
    for i, h in enumerate(headers2, 1):
        c = ws2.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows2 = conn.execute("""SELECT m.date, v.vehicle_no, m.category, m.amount, m.paid_amount, ve.name, m.notes
                            FROM maintenance m
                            LEFT JOIN vehicles v ON m.vehicle_id=v.id
                            LEFT JOIN vendors ve ON m.vendor_id=ve.id
                            ORDER BY m.date""").fetchall()
    for r_idx, row in enumerate(rows2, 2):
        for c_idx, val in enumerate(row, 1):
            ws2.cell(row=r_idx, column=c_idx, value=val)

    # Payments sheet
    ws3 = wb.create_sheet("Payments")
    headers3 = ["Date","Type","Amount","Party","Vendor","Mode"]
    for i, h in enumerate(headers3, 1):
        c = ws3.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows3 = conn.execute("""SELECT pay.date, pay.payment_type, pay.amount, p.name, v.name, pay.mode
                            FROM payments pay
                            LEFT JOIN parties p ON pay.party_id=p.id
                            LEFT JOIN vendors v ON pay.vendor_id=v.id
                            ORDER BY pay.date""").fetchall()
    for r_idx, row in enumerate(rows3, 2):
        for c_idx, val in enumerate(row, 1):
            ws3.cell(row=r_idx, column=c_idx, value=val)

    conn.close()

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='fleet_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    app.run(debug=True, port=5050)
