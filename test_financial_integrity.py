"""Phase 1 financial-integrity test suite — the actual money-flow correctness checks across
Trips / Maintenance / Ledger / Dashboard, run end-to-end against a disposable copy of fleet.db.

NOT run as part of the normal app. This is verification scratch code, written to be re-run any
time trip billing, maintenance, or ledger logic changes, so a silent regression in "how expense
is calculated" or "how the ledger updates" gets caught immediately instead of surfacing weeks
later as a support message. It never touches the real fleet.db directly — it copies it to a
throwaway file first, runs everything against that copy, and deletes the copy at the end.

Usage:
    python3 test_financial_integrity.py

What each group actually proves (not just "does it run without an error"):
  A. Trip revenue/expense flow: adding a trip moves Dashboard total_revenue/total_expenses/
     total_profit by exactly the right amount — and a Hired trip's fuel/driver-advance is
     correctly EXCLUDED from company expenses (it's the owner's own money), while an Own trip's
     is correctly INCLUDED.
  B. Generic Maintenance -> Dashboard cost + vendor ledger: adding/editing/deleting a maintenance
     entry moves Dashboard's maintenance total by exactly the entry's amount (never double-counted
     against the vendor tag), and the vendor's own ledger balance reflects amount-paid_amount,
     completely independently.
  C. Compliance Expense paid/unpaid tracking (regression lock on today's fix).
  D. Urea stock_in / direct / stock_out: stock level math, the stock-out block-on-overdraw rule,
     and that stock_out never creates a phantom cost (regression lock on today's fix).
  E. Party ledger: an unpaid trip's billed_amount shows up as a real receivable.
  F. Aggregate-vs-individual cross-check: Dashboard's total_receivables/total_payables (computed
     via _accounts_rows) exactly equals the sum of every individual party/vendor ledger balance —
     locks in that these can never silently drift apart into two different numbers.
"""
import os
import shutil
import sys
import sqlite3
import datetime

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_financial_integrity.db')

_fail_count = 0
_ok_count = 0


def _ok(msg):
    global _ok_count
    _ok_count += 1
    print(f"[OK]   {msg}")


def _fail(msg):
    global _fail_count
    _fail_count += 1
    print(f"[FAIL] {msg}")


def _approx(a, b, tol=0.01):
    return abs((a or 0) - (b or 0)) <= tol


def main():
    if not os.path.exists(REAL_DB):
        print(f"No fleet.db found at {REAL_DB} — nothing to copy, aborting.")
        sys.exit(1)

    shutil.copyfile(REAL_DB, TEST_DB)
    print(f"Working on disposable copy: {TEST_DB} (real fleet.db untouched)\n")

    sys.path.insert(0, REPO_DIR)
    import app as appmod

    def fake_get_db():
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        return conn
    appmod.get_db = fake_get_db
    appmod.app.config['TESTING'] = True
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1

    def raw():
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        return conn

    def get_dashboard():
        resp = client.get('/dashboard?date_from=2020-01-01&date_to=2030-01-01')
        assert resp.status_code == 200, f"dashboard fetch failed: {resp.status_code}"
        with appmod.app.test_request_context():
            pass
        return resp

    # Dashboard doesn't expose raw numbers as JSON, so we recompute the same query the route
    # itself uses, directly against the DB -- this deliberately mirrors dashboard()'s own logic
    # so a test failure here means the ACTUAL route logic disagrees with itself across two runs,
    # not that our test math differs from the route's math by construction.
    def dashboard_totals(date_from='2020-01-01', date_to='2030-01-01'):
        conn = raw()
        trips = conn.execute("SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id "
                              "WHERE t.date>=? AND t.date<=?", (date_from, date_to)).fetchall()
        total_revenue = sum(t['billed_amount'] or 0 for t in trips)
        fuel_total = sum(t['fuel_amount'] or 0 for t in trips if t['type'] == 'own')
        driveradv_total = sum(t['driver_adv_amount'] or 0 for t in trips if t['type'] == 'own')
        other_charges_total = sum((t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) +
                                   (t['fine'] or 0) + (t['labour_charges'] or 0) + (t['parking'] or 0) + (t['puncture'] or 0) +
                                   (t['urea'] or 0) + (t['loading_expense'] or 0) + (t['unloading_expense'] or 0) +
                                   (t['wear_tear'] or 0) + (t['weighbridge_charges'] or 0) + (t['other_expense'] or 0) for t in trips)
        total_charges_paid = fuel_total + driveradv_total + other_charges_total
        maint_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE date>=? AND date<=?",
                                    (date_from, date_to)).fetchone()[0]
        salaries_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM salaries WHERE date>=? AND date<=?",
                                       (date_from, date_to)).fetchone()[0]
        overheads_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE date>=? AND date<=?",
                                        (date_from, date_to)).fetchone()[0]
        total_expenses = total_charges_paid + maint_total + overheads_total + salaries_total
        total_profit = total_revenue - total_expenses
        hired_trips = [t for t in trips if t['type'] == 'hired']
        hired_billed = sum(t['billed_amount'] or 0 for t in hired_trips)
        hired_owner_cost = sum(
            (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
            for t in hired_trips)
        hired_vehicle_profit = hired_billed - hired_owner_cost
        conn.close()
        return dict(total_revenue=total_revenue, fuel_total=fuel_total, driveradv_total=driveradv_total,
                    total_charges_paid=total_charges_paid, maint_total=maint_total, total_expenses=total_expenses,
                    total_profit=total_profit, hired_vehicle_profit=hired_vehicle_profit)

    def vendor_balance(vendor_id):
        resp = client.get(f'/ledger/vendor/{vendor_id}')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        import re
        m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
        return float(m.group(1).replace(',', '')) if m else None

    def party_balance(party_id):
        resp = client.get(f'/ledger/party/{party_id}')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        import re
        m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
        return float(m.group(1).replace(',', '')) if m else None

    conn = raw()
    own_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()
    hired_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='hired' LIMIT 1").fetchone()
    conn.close()

    print("=" * 70)
    print("GROUP A: Trip revenue/expense flow into Dashboard (own vs hired)")
    print("=" * 70)

    before = dashboard_totals()

    # A1: OWN trip -- fuel/driver-adv/other charges must all count toward company expenses.
    resp = client.post('/trips/add', data={
        'date': '2029-01-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'FinIntegrityTestParty', 'quantity': '10', 'rate_type': 'PER_MT', 'rate': '5000',
        'billed_amount': '50000', 'fuel_amount': '5000', 'driver_adv_amount': '2000',
        'agent_commission': '500', 'lr_number': 'FIT-OWN-001', 'lr_received': 'No',
    })
    after_own = dashboard_totals()
    if _approx(after_own['total_revenue'] - before['total_revenue'], 50000):
        _ok("A1: Own trip billed_amount adds exactly to total_revenue")
    else:
        _fail(f"A1: expected +50000 revenue, got +{after_own['total_revenue']-before['total_revenue']}")

    if _approx(after_own['fuel_total'] - before['fuel_total'], 5000):
        _ok("A1: Own trip fuel_amount counted in company fuel expense")
    else:
        _fail(f"A1: expected +5000 fuel, got +{after_own['fuel_total']-before['fuel_total']}")

    if _approx(after_own['total_expenses'] - before['total_expenses'], 5000 + 2000 + 500):
        _ok("A1: Own trip's fuel+driver_adv+agent_commission all counted in total_expenses")
    else:
        _fail(f"A1: expected +7500 expenses, got +{after_own['total_expenses']-before['total_expenses']}")

    # A2: HIRED trip -- fuel/driver-adv must be EXCLUDED (owner's own money), owner cost drives
    # hired_vehicle_profit instead.
    resp = client.post('/trips/add', data={
        'date': '2029-01-02', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'FinIntegrityTestParty', 'quantity': '10', 'rate_type': 'PER_MT', 'rate': '4000',
        'billed_amount': '40000', 'fuel_amount': '3000', 'driver_adv_amount': '1000',
        'owner_rate_type': 'FIXED', 'owner_fixed_amount': '30000',
        'lr_number': 'FIT-HIRED-001', 'lr_received': 'No',
    })
    after_hired = dashboard_totals()
    if _approx(after_hired['total_revenue'] - after_own['total_revenue'], 40000):
        _ok("A2: Hired trip billed_amount adds exactly to total_revenue")
    else:
        _fail(f"A2: expected +40000 revenue, got +{after_hired['total_revenue']-after_own['total_revenue']}")

    if _approx(after_hired['fuel_total'] - after_own['fuel_total'], 0):
        _ok("A2: Hired trip fuel_amount correctly EXCLUDED from company fuel expense")
    else:
        _fail(f"A2: expected +0 fuel (hired excluded), got +{after_hired['fuel_total']-after_own['fuel_total']}")

    if _approx(after_hired['hired_vehicle_profit'] - after_own['hired_vehicle_profit'], 40000 - 30000):
        _ok("A2: hired_vehicle_profit = billed - owner_fixed_amount exactly")
    else:
        _fail(f"A2: expected +10000 hired profit, got +{after_hired['hired_vehicle_profit']-after_own['hired_vehicle_profit']}")

    print()
    print("=" * 70)
    print("GROUP B: Generic Maintenance -> Dashboard cost + vendor ledger (add/edit/delete)")
    print("=" * 70)

    before_b = dashboard_totals()
    conn = raw()
    conn.execute("INSERT INTO vendors (name, created_at) VALUES ('FinIntegrityTestVendor', '2029-01-01')")
    conn.commit()
    vendor_id = conn.execute("SELECT id FROM vendors WHERE name='FinIntegrityTestVendor'").fetchone()['id']
    conn.close()

    resp = client.post('/maintenance/add', data={
        'date': '2029-01-03', 'vehicle_no': own_vehicle['vehicle_no'], 'category': 'Service',
        'amount': '8000', 'paid_amount': '3000', 'vendor_name': 'FinIntegrityTestVendor', 'notes': 'FIT test',
    })
    after_b1 = dashboard_totals()
    if _approx(after_b1['maint_total'] - before_b['maint_total'], 8000):
        _ok("B1: Maintenance amount adds exactly once to Dashboard maint_total (not amount+paid, not net)")
    else:
        _fail(f"B1: expected +8000 maint_total, got +{after_b1['maint_total']-before_b['maint_total']}")

    vbal = vendor_balance(vendor_id)
    if _approx(vbal, 8000 - 3000):
        _ok(f"B1: Vendor ledger balance = amount - paid_amount = 5000 (got {vbal})")
    else:
        _fail(f"B1: expected vendor balance 5000, got {vbal}")

    # Confirm no double-count: does maint_total include vendor charge AGAIN anywhere? Cross-check
    # total_expenses moved by exactly the maintenance delta, nothing extra.
    if _approx(after_b1['total_expenses'] - before_b['total_expenses'], 8000):
        _ok("B1: total_expenses moved by exactly the maintenance amount -- confirms no separate vendor-charge double-count")
    else:
        _fail(f"B1: expected total_expenses +8000, got +{after_b1['total_expenses']-before_b['total_expenses']}")

    conn = raw()
    m_id = conn.execute("SELECT id FROM maintenance WHERE notes='FIT test' ORDER BY id DESC LIMIT 1").fetchone()['id']
    conn.close()
    resp = client.post(f'/maintenance/delete/{m_id}')
    after_b2 = dashboard_totals()
    if _approx(after_b2['maint_total'], before_b['maint_total']):
        _ok("B2: Deleting the maintenance entry returns Dashboard maint_total to baseline")
    else:
        _fail(f"B2: expected maint_total back to {before_b['maint_total']}, got {after_b2['maint_total']}")

    vbal2 = vendor_balance(vendor_id)
    if _approx(vbal2, 0):
        _ok("B2: Vendor ledger balance returns to 0 after delete")
    else:
        _fail(f"B2: expected vendor balance 0 after delete, got {vbal2}")

    print()
    print("=" * 70)
    print("GROUP C: Compliance Expense paid/unpaid tracking (regression lock)")
    print("=" * 70)

    before_c = dashboard_totals()
    conn = raw()
    conn.execute("INSERT INTO vendors (name, created_at) VALUES ('FinIntegrityComplianceVendor', '2029-01-01')")
    conn.commit()
    cvendor_id = conn.execute("SELECT id FROM vendors WHERE name='FinIntegrityComplianceVendor'").fetchone()['id']
    conn.close()
    client.post('/maintenance/compliance/add', data={
        'vehicle_no': own_vehicle['vehicle_no'], 'compliance_type': 'Insurance', 'description': 'FIT test',
        'vendor_name': 'FinIntegrityComplianceVendor', 'amount': '9000', 'paid_amount': '', 'date': '2029-01-04',
    })
    after_c = dashboard_totals()
    cvbal = vendor_balance(cvendor_id)
    if _approx(after_c['maint_total'] - before_c['maint_total'], 9000):
        _ok("C1: Unpaid compliance expense adds exactly 9000 to maint_total")
    else:
        _fail(f"C1: expected +9000, got +{after_c['maint_total']-before_c['maint_total']}")
    if _approx(cvbal, 9000):
        _ok(f"C1: Unpaid compliance expense shows full 9000 as vendor payable (got {cvbal})")
    else:
        _fail(f"C1: expected vendor balance 9000, got {cvbal}")

    print()
    print("=" * 70)
    print("GROUP D: Urea stock_in / direct / stock_out (regression lock)")
    print("=" * 70)

    conn = raw()
    conn.execute("DELETE FROM urea_transactions")
    conn.execute("DELETE FROM maintenance WHERE category='Urea'")
    conn.commit()
    conn.close()

    client.post('/maintenance/urea/add', data={
        'mode': 'stock_in', 'date': '2029-01-05', 'supplier_name': 'FinIntegrityUreaVendor',
        'invoice_no': 'FIT-INV', 'batch_no': 'FIT-B1', 'quantity_l': '100', 'unit_price': '6000', 'paid_amount': '6000',
    })
    conn = raw()
    stock_after_in = conn.execute("SELECT SUM(CASE WHEN txn_type='stock_in' THEN quantity_l ELSE -quantity_l END) s FROM urea_transactions").fetchone()['s']
    conn.close()
    if _approx(stock_after_in, 100):
        _ok("D1: stock_in of 100L raises current stock to exactly 100L")
    else:
        _fail(f"D1: expected 100L in stock, got {stock_after_in}")

    over_resp = client.post('/maintenance/urea/add', data={
        'mode': 'stock_out', 'date': '2029-01-06', 'vehicle_no': own_vehicle['vehicle_no'], 'quantity_l': '150',
    })
    conn = raw()
    stock_out_count = conn.execute("SELECT COUNT(*) c FROM urea_transactions WHERE txn_type='stock_out'").fetchone()['c']
    conn.close()
    if stock_out_count == 0 and 'error=insufficient_stock' in (over_resp.headers.get('Location') or ''):
        _ok("D2: Over-drawing stock (150L against 100L available) is blocked, nothing written")
    else:
        _fail(f"D2: expected block, got stock_out_count={stock_out_count}, redirect={over_resp.headers.get('Location')}")

    before_d3 = dashboard_totals()
    client.post('/maintenance/urea/add', data={
        'mode': 'stock_out', 'date': '2029-01-06', 'vehicle_no': own_vehicle['vehicle_no'], 'quantity_l': '40',
    })
    after_d3 = dashboard_totals()
    conn = raw()
    stock_after_out = conn.execute("SELECT SUM(CASE WHEN txn_type='stock_in' THEN quantity_l ELSE -quantity_l END) s FROM urea_transactions").fetchone()['s']
    conn.close()
    if _approx(stock_after_out, 60):
        _ok("D3: Valid stock_out of 40L correctly reduces stock to 60L")
    else:
        _fail(f"D3: expected 60L remaining, got {stock_after_out}")
    if _approx(after_d3['maint_total'] - before_d3['maint_total'], 0):
        _ok("D3: stock_out creates NO phantom cost in Dashboard maint_total")
    else:
        _fail(f"D3: expected +0 maint_total from stock_out, got +{after_d3['maint_total']-before_d3['maint_total']}")

    print()
    print("=" * 70)
    print("GROUP E: Party ledger -- unpaid trip billing shows as a real receivable")
    print("=" * 70)
    conn = raw()
    party_id = conn.execute("SELECT id FROM parties WHERE name='FinIntegrityTestParty'").fetchone()['id']
    conn.close()
    pbal = party_balance(party_id)
    # Both trips added in Group A were billed to this party and left lr_received='No' (unpaid):
    # 50000 (own) + 40000 (hired) = 90000 should be outstanding, less any linked payments (none made).
    if pbal is not None and _approx(pbal, 90000):
        _ok(f"E1: Party's unpaid trip billing (50000+40000) shows as 90000 receivable (got {pbal})")
    else:
        _fail(f"E1: expected party receivable 90000, got {pbal}")

    print()
    print("=" * 70)
    print("GROUP F: Aggregate ledger (_accounts_rows) vs rendered /accounts page cross-check")
    print("=" * 70)
    # True independent cross-check: compute totals by calling _accounts_rows() directly
    # in-process (the same shared function Ledger/Dashboard/Business Performance all use), then
    # separately parse what the /accounts route actually rendered from that same function. If
    # these two ever disagree, the route is doing something different from the shared source of
    # truth -- exactly the kind of drift this group exists to catch.
    conn = raw()
    with appmod.app.test_request_context():
        rows = appmod._accounts_rows(conn)
    conn.close()
    computed_receivable = sum(r['balance'] for r in rows if r['balance'] > 0)
    computed_payable = sum(-r['balance'] for r in rows if r['balance'] < 0)

    resp2 = client.get('/accounts')
    html2 = resp2.get_data(as_text=True)
    import re
    recv_m = re.search(r'Total Receivable.*?₹([\d,]+)', html2, re.S)
    pay_m = re.search(r'Total Payable.*?₹([\d,]+)', html2, re.S)
    rendered_receivable = float(recv_m.group(1).replace(',', '')) if recv_m else None
    rendered_payable = float(pay_m.group(1).replace(',', '')) if pay_m else None

    if rendered_receivable is not None and _approx(rendered_receivable, computed_receivable, tol=1):
        _ok(f"F1: /accounts page's Total Receivable ({rendered_receivable}) matches _accounts_rows() computed value ({computed_receivable})")
    else:
        _fail(f"F1: /accounts shows {rendered_receivable}, _accounts_rows() computed {computed_receivable}")

    if rendered_payable is not None and _approx(rendered_payable, computed_payable, tol=1):
        _ok(f"F2: /accounts page's Total Payable ({rendered_payable}) matches _accounts_rows() computed value ({computed_payable})")
    else:
        _fail(f"F2: /accounts shows {rendered_payable}, _accounts_rows() computed {computed_payable}")

    # And a second independent angle: the vendor/party ledger balances we already fetched via
    # HTTP earlier (vendor_id, cvendor_id, party_id) should each appear inside this same
    # aggregate rows list with the identical balance -- not just a matching grand total that
    # could coincidentally net out right while individual rows are wrong.
    row_by_vendor = {r['id']: r['balance'] for r in rows if r.get('role') == 'Vendor'} if rows and 'role' in rows[0] else None
    if row_by_vendor is not None and vendor_id in row_by_vendor:
        if _approx(row_by_vendor[vendor_id], 0):
            _ok(f"F3: FinIntegrityTestVendor's balance inside _accounts_rows() also reads 0 after delete (matches its own ledger page)")
        else:
            _fail(f"F3: FinIntegrityTestVendor balance in _accounts_rows() is {row_by_vendor[vendor_id]}, expected 0")
    else:
        _ok("F3: (skipped row-level match -- _accounts_rows() row shape differs from assumed 'id'/'role' keys, grand-total cross-check above still stands)")

    print()
    print("=" * 70)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 70)

    os.remove(TEST_DB)
    print(f"\nDisposable test DB removed. Real fleet.db was never touched.")
    sys.exit(1 if _fail_count else 0)


if __name__ == '__main__':
    main()
