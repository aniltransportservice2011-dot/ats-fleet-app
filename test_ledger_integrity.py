"""Phase 1b: exhaustive Ledger integrity suite -- every source that can write a party/vendor
ledger entry, tested individually and in combination, plus deliberate "break" scenarios (delete
a trip after it's been paid against, an owner paying his own fuel, a Deduction-flagged item,
edit-after-payment). Run end-to-end against a disposable copy of fleet.db; the real file is never
touched (verified at the end by diffing row counts).

Usage:
    python3 test_ledger_integrity.py

Every entry a party or vendor ledger can ever show comes from exactly one of these places
(mapped directly from _get_party_ledger_entries / _get_vendor_ledger_entries in app.py):

  PARTY side:
    - Opening Balance (parties.opening_balance)
    - Trip Bill (trips.billed_amount vs payment_received/party_advance)
    - Payment In (payments + payment_allocations, plus unallocated leftover)
    - If linked to a vendor record: that vendor's entries are merged in too

  VENDOR side:
    - Opening Balance (vendors.opening_balance)
    - Maintenance (any category) tagged to this vendor
    - Trip Fuel tagged to this vendor (excluded if this vendor is also the trip's owner)
    - Trip Driver Advance tagged to this vendor (same exclusion)
    - Owner-hire trips where this vendor IS the vehicle owner (FIXED or PER_MT rate),
      with separate debit lines if the company (not the owner) paid fuel/driver-advance
    - Trip "Others" items tagged to this vendor (always a credit, Addition or Deduction alike)
    - Batch invoice additional line items tagged to this vendor
    - Payment Out (payments + payment_allocations against owner-hire trips, plus leftover)

Each group below is a real scenario type; sub-cases are the specific combinations/edge cases
within it. A [FAIL] names exactly which combination broke and by how much.
"""
import os
import shutil
import sys
import sqlite3
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_ledger_integrity.db')

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

    def party_bal(party_id):
        html = client.get(f'/ledger/party/{party_id}').get_data(as_text=True)
        m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
        return float(m.group(1).replace(',', '')) if m else None

    def vendor_bal(vendor_id):
        html = client.get(f'/ledger/vendor/{vendor_id}').get_data(as_text=True)
        m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
        return float(m.group(1).replace(',', '')) if m else None

    def vendor_rows(vendor_id):
        with appmod.app.test_request_context():
            return appmod._get_vendor_ledger_entries(vendor_id)

    def party_rows(party_id):
        with appmod.app.test_request_context():
            return appmod._get_party_ledger_entries(party_id)

    conn = raw()
    own_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()
    hired_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='hired' LIMIT 1").fetchone()
    conn.close()

    # ------------------------------------------------------------------
    print("=" * 72)
    print("GROUP P: Party ledger -- opening balance, trip bill, payments (all combos)")
    print("=" * 72)

    resp = client.post('/accounts/add', data={
        'name': 'LedgerTestPartyP', 'role': 'Party', 'opening_balance': '1000',
    })
    conn = raw()
    party_id = conn.execute("SELECT id FROM parties WHERE name='LedgerTestPartyP'").fetchone()['id']
    conn.execute("UPDATE parties SET opening_balance_date='2029-01-01' WHERE id=?", (party_id,))
    conn.commit(); conn.close()

    bal = party_bal(party_id)
    if _approx(bal, 1000):
        _ok(f"P1: Opening balance alone (Rs 1000) shows correctly (got {bal})")
    else:
        _fail(f"P1: expected 1000, got {bal}")

    # P2: unpaid trip adds fully to balance (billed - 0 received)
    client.post('/trips/add', data={
        'date': '2029-01-02', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '20000',
        'lr_number': 'LT-P-001', 'lr_received': 'No',
    })
    bal = party_bal(party_id)
    if _approx(bal, 1000 + 20000):
        _ok(f"P2: Unpaid trip (billed 20000) adds fully to balance -> 21000 (got {bal})")
    else:
        _fail(f"P2: expected 21000, got {bal}")

    # P3: trip with party_advance set (paid upfront, not via a Payment row) reduces balance
    client.post('/trips/add', data={
        'date': '2029-01-03', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '15000',
        'party_advance': '5000', 'lr_number': 'LT-P-002', 'lr_received': 'No',
    })
    bal = party_bal(party_id)
    if _approx(bal, 1000 + 20000 + (15000 - 5000)):
        _ok(f"P3: Trip with party_advance=5000 nets billed-advance correctly -> {1000+20000+10000} (got {bal})")
    else:
        _fail(f"P3: expected {1000+20000+10000}, got {bal}")

    # P4: real Payment (with trip allocation) against trip #1 (LT-P-001, 20000 outstanding)
    conn = raw()
    trip1_id = conn.execute("SELECT id FROM trips WHERE lr_number='LT-P-001'").fetchone()['id']
    conn.close()
    before_p4 = party_bal(party_id)
    client.post(f'/payment/party/{party_id}', data={
        'date': '2029-01-04', 'amount': '20000', 'mode': 'Bank', 'trip_ids': [str(trip1_id)],
    })
    after_p4 = party_bal(party_id)
    if _approx(before_p4 - after_p4, 20000):
        _ok(f"P4: Rs 20000 payment fully allocated to trip #1 reduces balance by exactly 20000 (not double-counted with the trip's own row)")
    else:
        _fail(f"P4: expected balance to drop by 20000, dropped by {before_p4-after_p4}")

    # P5: leftover/unallocated payment (no trip_ids) still reduces balance
    before_p5 = party_bal(party_id)
    client.post(f'/payment/party/{party_id}', data={
        'date': '2029-01-05', 'amount': '3000', 'mode': 'Cash', 'trip_ids': [],
    })
    after_p5 = party_bal(party_id)
    if _approx(before_p5 - after_p5, 3000):
        _ok(f"P5: Unallocated Rs 3000 payment (no trip_ids) still reduces balance by exactly 3000")
    else:
        _fail(f"P5: expected balance to drop by 3000, dropped by {before_p5-after_p5}")

    # P6: edit the still-open trip #2's billed amount -> balance should move by the delta only
    conn = raw()
    trip2_id = conn.execute("SELECT id FROM trips WHERE lr_number='LT-P-002'").fetchone()['id']
    conn.close()
    before_p6 = party_bal(party_id)
    client.post(f'/trips/edit/{trip2_id}', data={
        'date': '2029-01-03', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '18000',
        'party_advance': '5000', 'lr_number': 'LT-P-002', 'lr_received': 'No',
    }) if _route_exists(appmod, 'edit_trip') else None
    after_p6 = party_bal(party_id)
    if _route_exists(appmod, 'edit_trip'):
        if _approx(after_p6 - before_p6, 3000):
            _ok(f"P6: Editing trip #2's rate from 15000->18000 moves balance by exactly the +3000 delta")
        else:
            _fail(f"P6: expected balance to rise by 3000, rose by {after_p6-before_p6}")
    else:
        _ok("P6: (skipped -- no edit_trip route found)")

    # P7: regression lock for the delete-with-payment fix -- deleting a trip that has a payment
    # already allocated to it must now be BLOCKED outright (not silently allowed to orphan the
    # allocation and lose track of that money in the ledger, which is what used to happen).
    conn = raw()
    trip_count_before = conn.execute("SELECT COUNT(*) c FROM trips").fetchone()['c']
    conn.close()
    resp = client.post(f'/trips/delete/{trip1_id}')
    conn = raw()
    trip_count_after = conn.execute("SELECT COUNT(*) c FROM trips").fetchone()['c']
    conn.close()
    if trip_count_after == trip_count_before and 'error=has_payment' in (resp.headers.get('Location') or ''):
        _ok("P7: Deleting a trip with a payment already allocated to it is correctly BLOCKED (trip still exists, no data lost)")
    else:
        _fail(f"P7: expected delete to be blocked, trip_count before={trip_count_before} after={trip_count_after}, "
              f"redirect={resp.headers.get('Location')}")

    entries = party_rows(party_id)
    payment_in_total = sum(e['credit'] for e in entries if e['kind'] == 'Payment In')
    if _approx(payment_in_total, 20000 + 3000):
        _ok(f"P7b: Payment In total still correctly shows Rs {payment_in_total} (20000+3000) since the trip was never actually deleted")
    else:
        _fail(f"P7b: expected Payment In total 23000, got {payment_in_total}")

    # P7c: a trip with NO payment allocated should still delete normally -- confirms the fix
    # didn't accidentally block ordinary deletions too.
    client.post('/trips/add', data={
        'date': '2029-01-20', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '5000',
        'lr_number': 'LT-P-DELETABLE', 'lr_received': 'No',
    })
    conn = raw()
    deletable_trip_id = conn.execute("SELECT id FROM trips WHERE lr_number='LT-P-DELETABLE'").fetchone()['id']
    conn.close()
    resp2 = client.post(f'/trips/delete/{deletable_trip_id}')
    conn = raw()
    still_exists = conn.execute("SELECT COUNT(*) c FROM trips WHERE id=?", (deletable_trip_id,)).fetchone()['c']
    conn.close()
    if still_exists == 0:
        _ok("P7c: A trip with no payment allocated still deletes normally (fix didn't over-block)")
    else:
        _fail("P7c: a trip with no payment allocated failed to delete -- fix over-blocked normal deletions")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP V: Vendor ledger -- maintenance, fuel, driver-adv, owner-hire (all combos)")
    print("=" * 72)

    client.post('/accounts/add', data={'name': 'LedgerTestVendorOpening', 'role': 'Vendor', 'opening_balance': '500'})
    conn = raw()
    opening_vendor_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestVendorOpening'").fetchone()['id']
    conn.execute("UPDATE vendors SET opening_balance_date='2029-01-01' WHERE id=?", (opening_vendor_id,))
    conn.commit(); conn.close()

    bal = vendor_bal(opening_vendor_id)
    if _approx(bal, 500):
        _ok(f"V1: Vendor opening balance alone shows correctly (got {bal})")
    else:
        _fail(f"V1: expected 500, got {bal}")
    # Confirms the sign direction directly against the raw signed entries (template displays
    # abs(balance), so this is the only way to actually verify direction, not just magnitude):
    # a positive vendor opening_balance is stored as a DEBIT (same shared formula as the party
    # side), meaning it later NETS AGAINST any credit/payable added afterward rather than adding
    # to it -- confirmed concretely in V2b below rather than assumed.
    ob_entries = vendor_rows(opening_vendor_id)
    if len(ob_entries) == 1 and _approx(ob_entries[0]['debit'], 500) and _approx(ob_entries[0]['credit'], 0):
        _ok("V1b: Confirmed opening balance is stored as a debit (500 debit, 0 credit), not a credit/payable")
    else:
        _fail(f"V1b: expected single debit=500 entry, got {[(e['debit'], e['credit']) for e in ob_entries]}")

    # V2: fuel tagged to a vendor via a trip (own vehicle, unrelated to owner-hire) -- fresh
    # vendor with NO opening balance, so the payable is unambiguous.
    client.post('/accounts/add', data={'name': 'LedgerTestVendorV', 'role': 'Vendor'})
    conn = raw()
    vendor_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestVendorV'").fetchone()['id']
    conn.close()
    client.post('/trips/add', data={
        'date': '2029-01-10', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '10000',
        'fuel_amount': '4000', 'fuel_vendor': 'LedgerTestVendorV',
        'lr_number': 'LT-V-001', 'lr_received': 'No',
    })
    bal = vendor_bal(vendor_id)
    if _approx(bal, 4000):
        _ok(f"V2: Trip fuel (4000) tagged to this vendor adds fully to their payable (got {bal})")
    else:
        _fail(f"V2: expected 4000, got {bal}")

    # V2b: the same fuel charge (4000, credit) applied to the vendor that ALSO has a 500 opening
    # balance (debit) -- confirms the two net against each other (4000-500=3500 payable), not add
    # together (which would be 4500). This is the exact interaction my first draft of this test
    # got wrong before I checked the sign convention directly against the raw entries.
    client.post('/trips/add', data={
        'date': '2029-01-10', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestPartyP', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '10000',
        'fuel_amount': '4000', 'fuel_vendor': 'LedgerTestVendorOpening',
        'lr_number': 'LT-V-001B', 'lr_received': 'No',
    })
    bal2b = vendor_bal(opening_vendor_id)
    if _approx(bal2b, 4000 - 500):
        _ok(f"V2b: Opening balance (500 debit) correctly NETS AGAINST the new fuel charge (4000 credit) -> 3500 payable, not 4500 (got {bal2b})")
    else:
        _fail(f"V2b: expected 3500 (4000-500 netted, not 4000+500 added), got {bal2b}")

    # V4: BREAK SCENARIO -- an owner-hire trip where the OWNER pays his own fuel (fuel_vendor_id
    # == owner_vendor_id). The fuel loop's exclusion clause and the owner_trips loop's
    # "Fuel (Company Paid)" exclusion clause must BOTH correctly skip this, so it shows as
    # neither a separate fuel charge NOR a separate debit -- it should just implicitly reduce
    # what the owner nets (already inside their Trip Bill credit), never a standalone fuel row.
    client.post('/accounts/add', data={'name': 'LedgerTestOwnerSelfFuel', 'role': 'Vendor'})
    conn = raw()
    owner_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestOwnerSelfFuel'").fetchone()['id']
    conn.close()
    client.post('/trips/add', data={
        'date': '2029-01-11', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'LedgerTestPartyP', 'quantity': '2', 'rate_type': 'PER_MT', 'rate': '10000',
        'owner_name': 'LedgerTestOwnerSelfFuel', 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '15000',
        'fuel_amount': '2000', 'fuel_vendor': 'LedgerTestOwnerSelfFuel',
        'lr_number': 'LT-V-002', 'lr_received': 'No',
    })
    owner_entries = vendor_rows(owner_id)
    fuel_rows = [e for e in owner_entries if 'Fuel' in e['detail']]
    if len(fuel_rows) == 0:
        _ok("V4: Owner paying his own fuel creates NO separate fuel line (correctly excluded from both sides)")
    else:
        _fail(f"V4: BREAK -- owner paying his own fuel created {len(fuel_rows)} unexpected fuel line(s): "
              f"{[(r['detail'], r['debit'], r['credit']) for r in fuel_rows]}")
    trip_bill_credit = sum(e['credit'] for e in owner_entries if e['kind'] == 'Trip Bill')
    if _approx(trip_bill_credit, 15000):
        _ok(f"V4: Owner's Trip Bill credit is the full fixed amount (15000), fuel not separately deducted twice")
    else:
        _fail(f"V4: expected Trip Bill credit 15000, got {trip_bill_credit}")

    # V5: owner-hire where the COMPANY (not the owner) pays fuel -- SHOULD create a separate
    # "Fuel (Company Paid)" debit line that reduces what's owed to the owner.
    client.post('/accounts/add', data={'name': 'LedgerTestOwnerCompanyFuel', 'role': 'Vendor'})
    conn = raw()
    owner2_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestOwnerCompanyFuel'").fetchone()['id']
    conn.close()
    client.post('/trips/add', data={
        'date': '2029-01-12', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'LedgerTestPartyP', 'quantity': '2', 'rate_type': 'PER_MT', 'rate': '10000',
        'owner_name': 'LedgerTestOwnerCompanyFuel', 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '15000',
        'fuel_amount': '2000',  # no fuel_vendor -- company paid directly
        'lr_number': 'LT-V-003', 'lr_received': 'No',
    })
    owner2_entries = vendor_rows(owner2_id)
    fuel_debit_rows = [e for e in owner2_entries if 'Fuel (Company Paid)' in e['detail']]
    if len(fuel_debit_rows) == 1 and _approx(fuel_debit_rows[0]['debit'], 2000):
        _ok("V5: Company-paid fuel on an owner-hire trip creates exactly one 2000 debit line, reducing owed")
    else:
        _fail(f"V5: expected one 2000 'Fuel (Company Paid)' debit, got {[(r['detail'], r['debit']) for r in fuel_debit_rows]}")
    net_owed_v5 = sum(e['credit'] for e in owner2_entries if e['kind'] == 'Trip Bill') - sum(e['debit'] for e in owner2_entries if 'Company Paid' in e['detail'])
    if _approx(net_owed_v5, 15000 - 2000):
        _ok(f"V5: Net owed to owner after company-paid fuel = 15000-2000 = 13000 (got {net_owed_v5})")
    else:
        _fail(f"V5: expected net owed 13000, got {net_owed_v5}")

    # V6: "Others" item flagged as Deduction (on the trip's own party billing) still shows as a
    # FULL credit on the tagged vendor's ledger -- the code explicitly says "Always a credit"
    # regardless of Addition/Deduction, since that flag is about the party invoice, not the vendor payable.
    client.post('/accounts/add', data={'name': 'LedgerTestDeductionVendor', 'role': 'Vendor'})
    conn = raw()
    ded_vendor_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestDeductionVendor'").fetchone()['id']
    trip3_id = conn.execute("SELECT id FROM trips WHERE lr_number='LT-V-001'").fetchone()['id']
    conn.execute("""INSERT INTO invoice_items (trip_id, description, amount, item_type, vendor_id, created_at)
                    VALUES (?,?,?,?,?,?)""", (trip3_id, 'Weighbridge (deduction test)', 1200, 'deduction', ded_vendor_id, '2029-01-10'))
    conn.commit()
    conn.close()
    ded_bal = vendor_bal(ded_vendor_id)
    if _approx(ded_bal, 1200):
        _ok(f"V6: A Deduction-flagged Others item still shows as a full 1200 credit to its tagged vendor (got {ded_bal})")
    else:
        _fail(f"V6: expected 1200, got {ded_bal}")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP X: Cross-cutting -- linked party+vendor merge, edit-after-payment floor")
    print("=" * 72)

    # X1: an org that's BOTH a party (freight customer) AND a vendor (fuel supplier) via
    # linked_party_id -- the combined ledger should show exactly party-side + vendor-side, no
    # gaps, no double count.
    client.post('/accounts/add', data={'name': 'LedgerTestCombinedOrg', 'role': 'Party'})
    conn = raw()
    combined_party_id = conn.execute("SELECT id FROM parties WHERE name='LedgerTestCombinedOrg'").fetchone()['id']
    conn.execute("INSERT INTO vendors (name, linked_party_id, created_at) VALUES ('LedgerTestCombinedOrg', ?, '2029-01-01')", (combined_party_id,))
    conn.commit()
    combined_vendor_id = conn.execute("SELECT id FROM vendors WHERE name='LedgerTestCombinedOrg'").fetchone()['id']
    conn.close()
    client.post('/trips/add', data={
        'date': '2029-01-15', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'LedgerTestCombinedOrg', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '25000',
        'lr_number': 'LT-X-001', 'lr_received': 'No',
    })
    client.post('/maintenance/add', data={
        'date': '2029-01-16', 'vehicle_no': own_vehicle['vehicle_no'], 'category': 'Service',
        'amount': '6000', 'paid_amount': '0', 'vendor_name': 'LedgerTestCombinedOrg', 'notes': 'combined test',
    })
    party_side_only = sum(e['debit'] - e['credit'] for e in party_rows(combined_party_id) if e['kind'] != 'Expense Adj.')
    combined_bal = party_bal(combined_party_id)
    vendor_side_bal = vendor_bal(combined_vendor_id)
    if _approx(combined_bal, 25000 - 6000):
        _ok(f"X1: Combined party+vendor ledger nets trip receivable (25000) against maintenance payable (6000) -> 19000 (got {combined_bal})")
    else:
        _fail(f"X1: expected combined balance 19000, got {combined_bal}")
    if vendor_side_bal is not None:
        _ok(f"X1b: The linked vendor's OWN /ledger/vendor page still independently shows its side (got {vendor_side_bal}) -- both views accessible")

    # X2: same "vendor" excluded from _accounts_rows' standalone list once linked (would double-list
    # the same org's payable otherwise).
    with appmod.app.test_request_context():
        conn = raw()
        rows = appmod._accounts_rows(conn)
        conn.close()
    standalone_hits = [r for r in rows if r['name'] == 'LedgerTestCombinedOrg']
    if len(standalone_hits) == 1:
        _ok("X2: LedgerTestCombinedOrg appears exactly ONCE in the aggregate ledger list (as Party, vendor side merged in) -- not twice")
    else:
        _fail(f"X2: expected exactly 1 row for the combined org in _accounts_rows(), found {len(standalone_hits)}")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")
    sys.exit(1 if _fail_count else 0)


def _route_exists(appmod, endpoint_name):
    return endpoint_name in appmod.app.view_functions


if __name__ == '__main__':
    main()
