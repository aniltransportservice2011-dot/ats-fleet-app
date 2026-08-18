"""Phase 1c: Maintenance sub-tabs (Tyre/Battery/Service/Insurance) vs Ledger integrity suite.
Covers what test_financial_integrity.py and test_ledger_integrity.py didn't: the tab-specific
add flows that each independently INSERT into the shared `maintenance` table with their own
field names and their own special cases -- Tyre's buy-into-stock-then-install path (must not
bill the vendor twice for the same physical tyre), Service's itemized-parts-vs-typed-total
relationship, and whether each tab's Paid Amount actually reaches the vendor ledger.

Run end-to-end against a disposable copy of fleet.db; the real file is never touched.

Usage:
    python3 test_maintenance_ledger.py
"""
import os
import shutil
import sys
import sqlite3
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_maintenance_ledger.db')

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


def _warn(msg):
    print(f"[WARN] {msg}")


def _approx(a, b, tol=0.01):
    return abs((a or 0) - (b or 0)) <= tol


def main():
    if not os.path.exists(REAL_DB):
        print(f"No fleet.db found at {REAL_DB} -- aborting.")
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

    def vendor_bal(vendor_id):
        html = client.get(f'/ledger/vendor/{vendor_id}').get_data(as_text=True)
        m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
        return float(m.group(1).replace(',', '')) if m else None

    def new_vendor(name):
        client.post('/accounts/add', data={'name': name, 'role': 'Vendor'})
        conn = raw()
        vid = conn.execute("SELECT id FROM vendors WHERE name=?", (name,)).fetchone()['id']
        conn.close()
        return vid

    conn = raw()
    own_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()
    conn.close()

    # ------------------------------------------------------------------
    print("=" * 72)
    print("GROUP M-TYRE: direct purchase, and buy-into-stock -> install (no double-bill)")
    print("=" * 72)

    tyre_vendor = new_vendor('MLedgerTyreVendor')
    client.post('/maintenance/tyre/add', data={
        'date': '2029-02-01', 'vehicle_no': own_vehicle['vehicle_no'], 'amount': '8000',
        'paid_amount': '3000', 'vendor_name': 'MLedgerTyreVendor', 'tyre_action': 'New Tyre Fitted',
        'tyre_position': 'FL', 'tyre_id': 'MLT-001',
    })
    bal = vendor_bal(tyre_vendor)
    if _approx(bal, 8000 - 3000):
        _ok(f"M-TYRE-1: Direct tyre purchase (8000, paid 3000) shows correct 5000 payable (got {bal})")
    else:
        _fail(f"M-TYRE-1: expected 5000, got {bal}")

    # Buy a tyre into stock (no vehicle yet) -- creates the vendor payable at PURCHASE time.
    stock_vendor = new_vendor('MLedgerTyreStockVendor')
    client.post('/maintenance/tyre/stock/add', data={
        'purchase_date': '2029-02-02', 'purchase_cost': '9000', 'paid_amount': '0',
        'vendor_name': 'MLedgerTyreStockVendor', 'tyre_id': 'MLT-STOCK-001', 'brand': 'TestBrand', 'tyre_type': 'New',
    })
    bal_after_purchase = vendor_bal(stock_vendor)
    if _approx(bal_after_purchase, 9000):
        _ok(f"M-TYRE-2: Buying a tyre into stock (unpaid) creates the full 9000 payable at purchase time (got {bal_after_purchase})")
    else:
        _fail(f"M-TYRE-2: expected 9000, got {bal_after_purchase}")

    conn = raw()
    stock_row = conn.execute("SELECT id FROM tyre_stock WHERE tyre_id='MLT-STOCK-001'").fetchone()
    stock_id = stock_row['id']
    m_count_before_install = conn.execute("SELECT COUNT(*) c FROM maintenance WHERE vendor_id=?", (stock_vendor,)).fetchone()['c']
    conn.close()

    # Now INSTALL that same stock tyre onto a vehicle -- must NOT create a second charge.
    client.post(f'/maintenance/tyre/stock/{stock_id}/install', data={
        'vehicle_no': own_vehicle['vehicle_no'], 'position': 'RR1', 'install_date': '2029-02-05',
    })
    bal_after_install = vendor_bal(stock_vendor)
    conn = raw()
    m_count_after_install = conn.execute("SELECT COUNT(*) c FROM maintenance WHERE vendor_id=?", (stock_vendor,)).fetchone()['c']
    conn.close()

    if _approx(bal_after_install, bal_after_purchase):
        _ok(f"M-TYRE-3: Installing that same stock tyre does NOT change the vendor's payable (still {bal_after_install}) -- no double-bill")
    else:
        _fail(f"M-TYRE-3: BREAK -- installing stock tyre changed vendor balance from {bal_after_purchase} to {bal_after_install}, "
              f"expected no change (same physical tyre, already billed at purchase)")

    if m_count_after_install == m_count_before_install:
        _ok(f"M-TYRE-3b: Installing reused the SAME maintenance row (still {m_count_after_install} row(s) for this vendor), not a new one")
    else:
        _fail(f"M-TYRE-3b: expected {m_count_before_install} maintenance row(s), got {m_count_after_install} -- a second row was created")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP M-BATTERY: purchase/install, paid/unpaid")
    print("=" * 72)
    batt_vendor = new_vendor('MLedgerBatteryVendor')
    client.post('/maintenance/battery/add', data={
        'mode': 'install', 'vehicle_no': own_vehicle['vehicle_no'], 'purchase_date': '2029-02-06',
        'purchase_price': '7000', 'paid_amount': '7000', 'vendor_name': 'MLedgerBatteryVendor',
    })
    bal = vendor_bal(batt_vendor)
    if _approx(bal, 0):
        _ok(f"M-BATTERY-1: Fully-paid battery purchase (7000/7000) shows 0 payable (got {bal})")
    else:
        _fail(f"M-BATTERY-1: expected 0, got {bal}")

    batt_vendor2 = new_vendor('MLedgerBatteryVendor2')
    client.post('/maintenance/battery/add', data={
        'mode': 'install', 'vehicle_no': own_vehicle['vehicle_no'], 'purchase_date': '2029-02-06',
        'purchase_price': '7000', 'paid_amount': '1000', 'vendor_name': 'MLedgerBatteryVendor2',
    })
    bal2 = vendor_bal(batt_vendor2)
    if _approx(bal2, 6000):
        _ok(f"M-BATTERY-2: Partially-paid battery purchase (7000/1000) shows correct 6000 payable (got {bal2})")
    else:
        _fail(f"M-BATTERY-2: expected 6000, got {bal2}")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP M-SERVICE: itemized parts vs. the manually-typed Total Amount field")
    print("=" * 72)
    svc_vendor = new_vendor('MLedgerServiceVendor')
    # Items sum to 2*500 + 1*3000 = 4000, but Total Amount is typed as 2500 -- a real mismatch a
    # user could easily create, since the UI never auto-sums items into the total (confirmed by
    # reading _maintenance_service_modals.html's mtRecalcItem, which only updates each row's own
    # display text, never the Total Amount field).
    client.post('/maintenance/service/add', data={
        'date': '2029-02-10', 'vehicle_no': own_vehicle['vehicle_no'], 'service_type': 'General Service',
        'amount': '2500', 'paid_amount': '2500', 'vendor_name': 'MLedgerServiceVendor',
        'item_name': ['Oil Filter', 'Labour'], 'item_category': ['Parts', 'Labour'],
        'item_qty': ['2', '1'], 'item_unit': ['pcs', 'job'], 'item_rate': ['500', '3000'],
    })
    conn = raw()
    m_row = conn.execute("SELECT id, amount FROM maintenance WHERE category='General Service' ORDER BY id DESC LIMIT 1").fetchone()
    items_sum = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM maintenance_items WHERE maintenance_id=?", (m_row['id'],)).fetchone()['s']
    conn.close()
    bal = vendor_bal(svc_vendor)
    if _approx(items_sum, 4000) and _approx(m_row['amount'], 2500) and _approx(bal, 2500 - 2500):
        _warn(f"M-SERVICE-1: CONFIRMED DESIGN GAP -- itemized parts/labour sum to Rs {items_sum}, but the vendor "
              f"is only charged Rs {m_row['amount']} (the separately-typed Total Amount field). The UI never "
              f"reconciles these two numbers, so a typo in Total Amount silently under- or over-bills the vendor "
              f"relative to what the item breakdown itself says was done. Not a crash, not incorrectly computed --"
              f" the app faithfully bills exactly what was typed in Total Amount; the risk is that field can quietly"
              f" disagree with the parts/labour list right below it.")
        _ok("M-SERVICE-1: (documented above, not counted as fail -- confirms Total Amount is what actually bills the vendor, as designed)")
    else:
        _fail(f"M-SERVICE-1: unexpected state -- items_sum={items_sum}, maintenance.amount={m_row['amount']}, vendor bal={bal}")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP M-INSURANCE: paid/unpaid tracking")
    print("=" * 72)
    ins_vendor = new_vendor('MLedgerInsuranceVendor')
    client.post('/maintenance/insurance/add', data={
        'vehicle_no': own_vehicle['vehicle_no'], 'insurer_name': 'MLedgerInsuranceVendor',
        'premium_amount': '15000', 'paid_amount': '', 'start_date': '2029-02-15', 'policy_number': 'POL-TEST-001',
    })
    bal = vendor_bal(ins_vendor)
    if _approx(bal, 15000):
        _ok(f"M-INSURANCE-1: Unpaid premium (paid_amount left blank) now correctly shows the full 15000 as a real payable (got {bal}) -- regression fixed")
    else:
        _fail(f"M-INSURANCE-1: expected 15000 payable for an unpaid premium, got {bal}")

    # M-INSURANCE-2: partial payment via Add
    ins_vendor2 = new_vendor('MLedgerInsuranceVendor2')
    client.post('/maintenance/insurance/add', data={
        'vehicle_no': own_vehicle['vehicle_no'], 'insurer_name': 'MLedgerInsuranceVendor2',
        'premium_amount': '20000', 'paid_amount': '5000', 'start_date': '2029-02-16', 'policy_number': 'POL-TEST-002',
    })
    bal2 = vendor_bal(ins_vendor2)
    if _approx(bal2, 15000):
        _ok(f"M-INSURANCE-2: Partially-paid premium (20000/5000) shows correct 15000 payable (got {bal2})")
    else:
        _fail(f"M-INSURANCE-2: expected 15000, got {bal2}")

    # M-INSURANCE-3: edit_insurance also honors paid_amount, not just add_insurance
    conn = raw()
    policy_id = conn.execute("SELECT id FROM insurance_policies WHERE policy_number='POL-TEST-002'").fetchone()['id']
    conn.close()
    client.post(f'/maintenance/insurance/{policy_id}/edit', data={
        'vehicle_no': own_vehicle['vehicle_no'], 'insurance_type': 'Comprehensive', 'insurer_name': 'MLedgerInsuranceVendor2',
        'policy_number': 'POL-TEST-002', 'start_date': '2029-02-16', 'expiry_date': '2030-02-16',
        'premium_amount': '20000', 'paid_amount': '20000', 'reminder_days': '30',
    })
    bal3 = vendor_bal(ins_vendor2)
    if _approx(bal3, 0):
        _ok(f"M-INSURANCE-3: Editing the policy to mark it fully paid (20000/20000) updates the ledger to 0 payable (got {bal3}) -- edit_insurance fix confirmed too")
    else:
        _fail(f"M-INSURANCE-3: expected 0 after marking fully paid via edit, got {bal3}")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")
    sys.exit(1 if _fail_count else 0)


if __name__ == '__main__':
    main()
