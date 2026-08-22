"""Phase 1e: edit_trip floor-protection regression check -- the exact "past bug class" the
code's own comments flag: editing a trip via a stale form (payment_received/paid_to_owner=0,
e.g. an old page that doesn't know a real Payment was recorded elsewhere) must not silently
erase money that a genuine Payment + payment_allocations row already recorded. REPORT ONLY --
does not fix anything found.

Run end-to-end against a disposable copy of fleet.db; the real file is never touched.

Usage:
    python3 test_floor_protection.py
"""
import os
import shutil
import sys
import sqlite3

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_floor_protection.db')

_fail_count = 0
_ok_count = 0
_findings = []


def _ok(msg):
    global _ok_count
    _ok_count += 1
    print(f"[OK]   {msg}")


def _fail(msg):
    global _fail_count
    _fail_count += 1
    print(f"[FAIL] {msg}")
    _findings.append(msg)


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
    hired_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='hired' LIMIT 1").fetchone()
    conn.close()

    print("=" * 72)
    print("GROUP F: edit_trip floor-protection (party payment_received side)")
    print("=" * 72)

    client.post('/trips/add', data={
        'date': '2029-04-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'FloorTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '20000',
        'lr_number': 'FLOOR-P-001', 'lr_received': 'No',
    })
    conn = raw()
    trip_id = conn.execute("SELECT id FROM trips WHERE lr_number='FLOOR-P-001'").fetchone()['id']
    party_id = conn.execute("SELECT id FROM parties WHERE name='FloorTestParty'").fetchone()['id']
    conn.close()

    # Real Payment, fully allocated to this trip -- this is what a genuine "money received" event
    # looks like, creating a payment_allocations row that the ledger depends on.
    client.post(f'/payment/party/{party_id}', data={
        'date': '2029-04-05', 'amount': '20000', 'mode': 'Bank', 'trip_ids': [str(trip_id)],
    })
    conn = raw()
    before_edit = conn.execute("SELECT payment_received FROM trips WHERE id=?", (trip_id,)).fetchone()['payment_received']
    conn.close()
    if _approx(before_edit, 20000):
        _ok(f"F1: Real payment correctly set trips.payment_received to 20000 (got {before_edit})")
    else:
        _fail(f"F1: expected payment_received 20000 after real payment, got {before_edit}")

    # Now simulate a STALE edit -- someone (or an old cached form) re-saves this trip with
    # payment_received explicitly blank/0, the exact scenario the floor exists to protect against.
    client.post(f'/trips/edit/{trip_id}', data={
        'date': '2029-04-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'FloorTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '20000',
        'lr_number': 'FLOOR-P-001', 'lr_received': 'No', 'payment_received': '0',
    })
    conn = raw()
    after_edit = conn.execute("SELECT payment_received FROM trips WHERE id=?", (trip_id,)).fetchone()['payment_received']
    conn.close()
    if _approx(after_edit, 20000):
        _ok(f"F2: Floor protection held -- stale edit submitting payment_received=0 did NOT erase the real 20000 already paid (still {after_edit})")
    else:
        _fail(f"F2: CONFIRMED BREAK -- stale edit with payment_received=0 dropped trips.payment_received to {after_edit}, "
              f"erasing a real Rs 20000 payment that a genuine Payment record still references via payment_allocations. "
              f"This would make the party's ledger show the trip as newly unpaid again while the Payment row still "
              f"exists, double-counting/desyncing exactly as the code's own comment warns about.")

    # F3: does the party's actual ledger balance also stay correct through this (not just the
    # raw column)?
    html = client.get(f'/ledger/party/{party_id}').get_data(as_text=True)
    import re
    m = re.search(r'Net Balance.*?₹([\d,]+)', html, re.S)
    bal = float(m.group(1).replace(',', '')) if m else None
    if _approx(bal, 0):
        _ok(f"F3: Party ledger balance correctly stays at 0 (fully paid) after the stale edit (got {bal})")
    else:
        _fail(f"F3: expected party balance 0 after stale edit (trip fully paid), got {bal}")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP F-OWNER: edit_trip floor-protection (owner paid_to_owner side)")
    print("=" * 72)

    owner_vendor_name = 'FloorTestOwner'
    client.post('/accounts/add', data={'name': owner_vendor_name, 'role': 'Vendor'})
    client.post('/trips/add', data={
        'date': '2029-04-10', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'FloorTestParty', 'quantity': '2', 'rate_type': 'PER_MT', 'rate': '10000',
        'owner_name': owner_vendor_name, 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '15000',
        'lr_number': 'FLOOR-O-001', 'lr_received': 'No',
    })
    conn = raw()
    trip2_id = conn.execute("SELECT id FROM trips WHERE lr_number='FLOOR-O-001'").fetchone()['id']
    owner_vendor_id = conn.execute("SELECT id FROM vendors WHERE name=?", (owner_vendor_name,)).fetchone()['id']
    conn.close()

    client.post(f'/payment/vendor/{owner_vendor_id}', data={
        'date': '2029-04-12', 'amount': '15000', 'mode': 'Cash', 'trip_ids': [str(trip2_id)],
    })
    conn = raw()
    before_edit2 = conn.execute("SELECT paid_to_owner FROM trips WHERE id=?", (trip2_id,)).fetchone()['paid_to_owner']
    conn.close()
    if _approx(before_edit2, 15000):
        _ok(f"F4: Real vendor payment correctly set trips.paid_to_owner to 15000 (got {before_edit2})")
    else:
        _fail(f"F4: expected paid_to_owner 15000, got {before_edit2}")

    client.post(f'/trips/edit/{trip2_id}', data={
        'date': '2029-04-10', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'FloorTestParty', 'quantity': '2', 'rate_type': 'PER_MT', 'rate': '10000',
        'owner_name': owner_vendor_name, 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '15000',
        'lr_number': 'FLOOR-O-001', 'lr_received': 'No', 'paid_to_owner': '0',
    })
    conn = raw()
    after_edit2 = conn.execute("SELECT paid_to_owner FROM trips WHERE id=?", (trip2_id,)).fetchone()['paid_to_owner']
    conn.close()
    if _approx(after_edit2, 15000):
        _ok(f"F5: Floor protection held on the owner side too -- stale edit with paid_to_owner=0 did NOT erase the real 15000 (still {after_edit2})")
    else:
        _fail(f"F5: CONFIRMED BREAK -- stale edit with paid_to_owner=0 dropped trips.paid_to_owner to {after_edit2}, "
              f"erasing a real Rs 15000 vendor payment still referenced by payment_allocations.")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)
    if _findings:
        print("\nFindings (not fixed, for review):")
        for f in _findings:
            print(f" - {f}")

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")


if __name__ == '__main__':
    main()
