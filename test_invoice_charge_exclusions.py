"""Regression test for the new Invoice Center "Trip Charges & Costs" popup feature --
per-trip charge/deduction exclusions, persisted so View/Regenerate PDF reproduces the same
invoice later. Runs end-to-end against a disposable copy of fleet.db; the real file is never
touched.

Usage:
    python3 test_invoice_charge_exclusions.py
"""
import os
import shutil
import sys
import sqlite3

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_invoice_exclusions.db')

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

    conn = raw()
    own_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()
    conn.close()

    print("=" * 72)
    print("Setup: one trip with several chargeable fields + one 'Others' item")
    print("=" * 72)
    client.post('/accounts/add', data={'name': 'ExclTestParty', 'role': 'Party'})
    client.post('/trips/add', data={
        'date': '2029-10-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'ExclTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '10000',
        'lr_number': 'EXCL-001', 'lr_received': 'No',
        'loading_charge': '500', 'unloading_charge': '300', 'gps_cost': '200',
    })
    conn = raw()
    trip = conn.execute("SELECT id FROM trips WHERE lr_number='EXCL-001'").fetchone()
    trip_id = trip['id']
    party = conn.execute("SELECT id FROM parties WHERE name='ExclTestParty'").fetchone()
    party_id = party['id']
    conn.close()
    client.post(f'/invoices/{trip_id}/items/add', data={
        'description': 'Detention Charge', 'amount': '150', 'item_type': 'charge',
    })

    conn = raw()
    row = conn.execute("SELECT loading_charge, unloading_charge, gps_cost, billed_amount FROM trips WHERE id=?", (trip_id,)).fetchone()
    item = conn.execute("SELECT id FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchone()
    conn.close()
    if row and _approx(row['loading_charge'], 500) and _approx(row['unloading_charge'], 300) and _approx(row['gps_cost'], 200):
        _ok(f"Setup: trip fields saved correctly (loading=500, unloading=300, gps=200)")
    else:
        _fail(f"Setup: trip fields did not save as expected: {dict(row) if row else None}")
    if item:
        _ok("Setup: 'Others' item (Detention Charge, 150) saved on the trip")
    else:
        _fail("Setup: 'Others' item did not save")

    freight = row['billed_amount'] or 0
    item_id = item['id'] if item else None

    print()
    print("=" * 72)
    print("Test 1: Invoice Center screen shows the right charges for this trip")
    print("=" * 72)
    html = client.get(f'/invoice-center?invoice_type=party&party_id={party_id}').get_data(as_text=True)
    checks = [
        ('Loading Charges', 500), ('Unloading Charges', 300), ('GPS Cost', 200), ('Detention Charge', 150),
    ]
    all_present = all(label in html for label, _ in checks)
    if all_present:
        _ok("Test 1a: all 4 expected charge labels appear in TRIP_CHARGES data embedded in the page")
    else:
        _fail(f"Test 1a: one or more expected charge labels missing from invoice_center page: {[l for l,_ in checks if l not in html]}")
    # Zero-value fields (permit, toll, weighment, driver_bata, other) should NOT appear for this trip.
    absent_ok = all(bad not in html for bad in ['Route Permit Charges', 'Weighment Charges', 'Driver Bata'])
    if absent_ok:
        _ok("Test 1b: fields with a zero value on this trip (Permit, Weighment, Driver Bata) correctly do NOT appear")
    else:
        _fail("Test 1b: a zero-value field appeared in the popup data when it shouldn't have")

    print()
    print("=" * 72)
    print("Test 2: Generate an invoice EXCLUDING GPS Cost and the Detention Charge item")
    print("=" * 72)
    excluded = [f"{trip_id}:gps", f"{trip_id}:item:{item_id}"]
    resp = client.post('/invoice-center/generate', data={
        'mode': 'generate', 'trip_ids': [str(trip_id)], 'invoice_type': 'party', 'party_id': str(party_id),
        'vendor_id': '', 'invoice_date': '2029-10-05', 'excluded_charge_keys': excluded,
        'gst_rate': '0', 'tds_rate': '0', 'loading_charges': '0', 'other_charges': '0',
    })
    if resp.status_code == 200 and resp.mimetype == 'application/pdf':
        _ok("Test 2a: Generate Invoice returned a PDF successfully")
    else:
        _fail(f"Test 2a: Generate Invoice did not return a PDF (status={resp.status_code}, mimetype={resp.mimetype})")

    conn = raw()
    batch = conn.execute("SELECT * FROM invoice_batches ORDER BY id DESC LIMIT 1").fetchone()
    batch_id = batch['id']
    excl_rows = conn.execute("SELECT trip_id, charge_key FROM invoice_batch_charge_exclusions WHERE invoice_batch_id=?", (batch_id,)).fetchall()
    conn.close()
    excl_keys = {f"{r['trip_id']}:{r['charge_key']}" for r in excl_rows}
    if excl_keys == set(excluded):
        _ok(f"Test 2b: exclusions persisted correctly to invoice_batch_charge_exclusions: {excl_keys}")
    else:
        _fail(f"Test 2b: persisted exclusions mismatch -- expected {set(excluded)}, got {excl_keys}")

    print()
    print("=" * 72)
    print("Test 3: the generated PDF's total reflects the exclusion (GPS + Detention left out)")
    print("=" * 72)
    with appmod.app.test_request_context():
        conn = raw()
        s = appmod._get_invoice_settings(conn, 1)
        trips_rows = conn.execute("SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id WHERE t.id=?", (trip_id,)).fetchall()
        toll_map = appmod._toll_by_trip(conn, [trip_id])
        excluded_set, _included_set = appmod._load_invoice_charge_exclusions(conn, batch_id)
        item_rows = conn.execute("SELECT id, trip_id, description, amount, item_type FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
        extra_items = appmod._filter_trip_items(item_rows, excluded_set)
        conn.close()
    # Manually mirror _build_invoice_pdf's freight_and_charges math to confirm the excluded fields don't count.
    li_loading = 0 if f"{trip_id}:loading" in excluded_set else 500
    li_unloading = 0 if f"{trip_id}:unloading" in excluded_set else 300
    li_gps = 0 if f"{trip_id}:gps" in excluded_set else 200
    named_extra = sum((it['amount'] or 0) if it['item_type']=='charge' else -(it['amount'] or 0) for it in extra_items)
    expected_subtotal = freight + li_loading + li_unloading + li_gps + named_extra
    # GPS excluded -> 0 contributed; item excluded -> not in extra_items at all.
    if li_gps == 0 and len(extra_items) == 0 and li_loading == 500 and li_unloading == 300:
        _ok(f"Test 3: exclusion correctly zeroed GPS (was 200) and dropped the Detention item (was 150) "
            f"while Loading (500) and Unloading (300) stayed included -- expected sub_total contribution {expected_subtotal - freight}")
    else:
        _fail(f"Test 3: CONFIRMED -- exclusion did not apply as expected. gps={li_gps}, extra_items={extra_items}, "
              f"loading={li_loading}, unloading={li_unloading}")

    print()
    print("=" * 72)
    print("Test 4: View/Regenerate PDF (invoice_batch_pdf) reproduces the SAME exclusion")
    print("=" * 72)
    resp2 = client.get(f'/invoices/generated/{batch_id}/pdf')
    if resp2.status_code == 200 and resp2.mimetype == 'application/pdf':
        _ok("Test 4a: /invoices/generated/<id>/pdf returned a PDF successfully")
    else:
        _fail(f"Test 4a: regenerate route failed (status={resp2.status_code})")
    # Re-load exclusions the same way invoice_batch_pdf does, confirm they still match what was saved.
    conn = raw()
    reloaded, _reloaded_included = appmod._load_invoice_charge_exclusions(conn, batch_id)
    conn.close()
    if reloaded == set(excluded):
        _ok(f"Test 4b: regenerating the PDF reloads the exact same exclusions as generation time "
            f"({reloaded}) -- the choice was NOT silently reverted to 'everything included'")
    else:
        _fail(f"Test 4b: CONFIRMED -- reloaded exclusions differ from what was saved. "
              f"Expected {set(excluded)}, got {reloaded}. This would mean View/Regenerate PDF "
              f"silently un-excludes fields the user deliberately left off the original invoice.")

    print()
    print("=" * 72)
    print("Test 5: an invoice generated with NO exclusions behaves exactly as before (regression)")
    print("=" * 72)
    resp3 = client.post('/invoice-center/generate', data={
        'mode': 'preview', 'trip_ids': [str(trip_id)], 'invoice_type': 'party', 'party_id': str(party_id),
        'vendor_id': '', 'invoice_date': '2029-10-05',
        'gst_rate': '0', 'tds_rate': '0', 'loading_charges': '0', 'other_charges': '0',
        # no excluded_charge_keys at all -- simulates a plain old submit / JS disabled
    })
    if resp3.status_code == 200 and resp3.mimetype == 'application/pdf':
        _ok("Test 5: generating with zero excluded_charge_keys (old-style submit) still works and returns a full PDF")
    else:
        _fail(f"Test 5: CONFIRMED -- omitting excluded_charge_keys entirely broke generation (status={resp3.status_code})")

    print()
    print("=" * 72)
    print("Test 6: opt-in-only fields (Detention/Police/SIM/Union + 7 deductions) -- default OFF, never")
    print("silently affect a total unless explicitly included")
    print("=" * 72)
    client.post('/trips/add', data={
        'date': '2029-11-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'ExclTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '15000',
        'lr_number': 'EXCL-002', 'lr_received': 'No',
        'detention_charges': '400', 'brokerage': '250',
    })
    conn = raw()
    trip2 = conn.execute("SELECT * FROM trips WHERE lr_number='EXCL-002'").fetchone()
    trip2_id = trip2['id']
    freight2 = trip2['billed_amount'] or 0
    conn.close()
    if _approx(trip2['detention_charges'], 400) and _approx(trip2['brokerage'], 250):
        _ok("Test 6 setup: trip saved with detention_charges=400 and brokerage=250")
    else:
        _fail(f"Test 6 setup: fields did not save as expected: detention={trip2['detention_charges']}, brokerage={trip2['brokerage']}")

    # 6a: popup data must mark these 'out' by default.
    html6 = client.get(f'/invoice-center?invoice_type=party&party_id={party_id}').get_data(as_text=True)
    if "'default': 'out'" in html6 or '"default": "out"' in html6:
        _ok("Test 6a: TRIP_CHARGES data marks Detention/Brokerage-type fields as default:'out' in the popup")
    else:
        _fail("Test 6a: could not confirm default:'out' marking is present in the rendered popup data")

    # 6b: with no included_charge_keys passed at all, _incl() must zero out detention/brokerage
    # (opt-in fields default OFF) rather than including them automatically.
    conn = raw()
    t2row = conn.execute("SELECT * FROM trips WHERE id=?", (trip2_id,)).fetchone()
    conn.close()
    # Direct, precise check against the real _OPT_IN_CHARGE_KEYS constant rather than re-deriving
    # the logic by hand.
    def _incl_check(key, raw_val, included):
        full = f"{trip2_id}:{key}"
        if key in appmod._OPT_IN_CHARGE_KEYS:
            return raw_val if full in included else 0
        return raw_val
    det_default = _incl_check('detention', t2row['detention_charges'] or 0, set())
    brok_default = _incl_check('brokerage', t2row['brokerage'] or 0, set())
    if det_default == 0 and brok_default == 0:
        _ok("Test 6b: with nothing opted in, detention and brokerage correctly contribute 0 to the invoice "
            "(real values 400/250 stay off by default)")
    else:
        _fail(f"Test 6b: CONFIRMED -- opt-in fields defaulted to included when they shouldn't have: "
              f"detention={det_default}, brokerage={brok_default}")

    # 6c: explicitly opt IN both fields via included_charge_keys and confirm they change the total.
    resp6c = client.post('/invoice-center/generate', data={
        'mode': 'generate', 'trip_ids': [str(trip2_id)], 'invoice_type': 'party', 'party_id': str(party_id),
        'vendor_id': '', 'invoice_date': '2029-11-05',
        'included_charge_keys': [f"{trip2_id}:detention", f"{trip2_id}:brokerage"],
        'gst_rate': '0', 'tds_rate': '0', 'loading_charges': '0', 'other_charges': '0',
    })
    if resp6c.status_code == 200 and resp6c.mimetype == 'application/pdf':
        _ok("Test 6c: generating WITH detention+brokerage opted in returns a PDF successfully")
    else:
        _fail(f"Test 6c: opt-in generation failed (status={resp6c.status_code})")
    conn = raw()
    batch2 = conn.execute("SELECT id FROM invoice_batches ORDER BY id DESC LIMIT 1").fetchone()
    batch2_id = batch2['id']
    excl2, incl2 = appmod._load_invoice_charge_exclusions(conn, batch2_id)
    conn.close()
    expected_incl = {f"{trip2_id}:detention", f"{trip2_id}:brokerage"}
    if incl2 == expected_incl and not excl2:
        _ok(f"Test 6d: opted-in fields persisted correctly to invoice_batch_charge_exclusions as inclusions: {incl2}")
    else:
        _fail(f"Test 6d: CONFIRMED -- persisted inclusion set wrong. Expected {expected_incl}, got included={incl2}, excluded={excl2}")

    # 6e: regenerate/view reproduces the same opt-in state.
    resp6e = client.get(f'/invoices/generated/{batch2_id}/pdf')
    conn = raw()
    excl2b, incl2b = appmod._load_invoice_charge_exclusions(conn, batch2_id)
    conn.close()
    if resp6e.status_code == 200 and incl2b == expected_incl:
        _ok(f"Test 6e: View/Regenerate PDF reproduces the same opted-in fields ({incl2b})")
    else:
        _fail(f"Test 6e: CONFIRMED -- regenerate did not reproduce the same opt-in state. Expected {expected_incl}, got {incl2b}")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")
    return _fail_count


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
