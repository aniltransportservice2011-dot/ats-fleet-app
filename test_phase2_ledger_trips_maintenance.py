"""Phase 2: combination testing across Ledger x Trips x Maintenance -- the three genuine
intersection points identified by reading the code first:

  G1) toll_entries -- the ONE table that literally links a Trip (trip_id) and a Maintenance
      row (maintenance_id) at the same time, and feeds period-level expense reporting.
  G2) A vendor who plays multiple simultaneous roles (trip fuel/advance vendor AND a
      maintenance vendor) -- stresses whether _get_vendor_ledger_entries' several independent
      SQL loops can ever cross-contaminate or double count.
  G3) A single vehicle with a Trip + a Maintenance entry + a linked Toll all in the same
      period -- does the vehicle/period-level cost rollup (_period_financials) combine all
      three without gaps or double-counting.

REPORT ONLY -- does not fix anything found. Runs against a disposable copy of fleet.db; the
real file is never touched.

Usage:
    python3 test_phase2_ledger_trips_maintenance.py
"""
import os
import shutil
import sys
import sqlite3
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_phase2.db')

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
    own_vehicle2 = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1 OFFSET 1").fetchone()
    conn.close()

    # ==================================================================
    print("=" * 78)
    print("GROUP G1: toll_entries -- the real Trip<->Maintenance link")
    print("=" * 78)

    client.post('/trips/add', data={
        'date': '2029-05-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'P2Party', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '30000',
        'lr_number': 'P2-TOLL-001', 'lr_received': 'No',
        'fuel_amount': '5000', 'driver_adv_amount': '1000',
    })
    conn = raw()
    trip_id = conn.execute("SELECT id FROM trips WHERE lr_number='P2-TOLL-001'").fetchone()['id']
    conn.close()

    # G1a: add a Toll entry linked to this trip via trip_lr, same date as the trip.
    client.post('/maintenance/toll/add', data={
        'date': '2029-05-01', 'vehicle_no': own_vehicle['vehicle_no'], 'toll_plaza': 'TestPlaza',
        'amount': '800', 'source': 'manual', 'trip_lr': 'P2-TOLL-001',
    })
    conn = raw()
    toll_row = conn.execute("SELECT * FROM toll_entries WHERE toll_plaza='TestPlaza'").fetchone()
    maint_row = conn.execute("SELECT * FROM maintenance WHERE id=?", (toll_row['maintenance_id'],)).fetchone() if toll_row else None
    conn.close()

    if toll_row and toll_row['trip_id'] == trip_id:
        _ok(f"G1a-1: Toll entry correctly linked to trip via trip_lr (trip_id={toll_row['trip_id']})")
    else:
        _fail(f"G1a-1: Toll entry trip_id linkage failed -- expected {trip_id}, got {toll_row['trip_id'] if toll_row else 'no row'}")
    if maint_row and _approx(maint_row['amount'], 800) and _approx(maint_row['paid_amount'], 800):
        _ok(f"G1a-2: Linked maintenance row auto-created correctly (amount=800, paid_amount=800)")
    else:
        _fail(f"G1a-2: Linked maintenance row wrong or missing: {dict(maint_row) if maint_row else None}")

    # Does the period-level expense total (_period_financials, used by Dashboard) count this
    # toll exactly once via the maintenance category='Toll' bucket, and NOT also via any
    # trip-level 'misc' aggregate (trips.toll column was never set on this trip -- it should
    # stay 0, since the linked total lives only in toll_entries/maintenance)?
    conn = raw()
    pf = appmod._period_financials(conn, '2029-05-01', '2029-05-01')
    conn.close()
    if _approx(pf['toll'], 800):
        _ok(f"G1a-3: _period_financials toll bucket correctly picks up the linked toll (800)")
    else:
        _fail(f"G1a-3: _period_financials toll bucket expected 800, got {pf['toll']}")
    # total_expenses should include toll exactly once -- reconstruct expected total manually.
    expected_total = 5000 + 1000 + 800 + 0 + 0 + 0 + 0 + 0 + 0  # fuel+adv+toll+parking+misc+owner+maint+overheads+salaries
    if _approx(pf['total_expenses'], expected_total):
        _ok(f"G1a-4: total_expenses correctly counts the linked toll exactly once ({pf['total_expenses']})")
    else:
        _fail(f"G1a-4: total_expenses mismatch -- expected {expected_total} (fuel 5000 + adv 1000 + toll 800), "
              f"got {pf['total_expenses']} -- possible double-count or drop of the linked toll")

    # G1b: does business-performance / route-analytics per-trip toll (_trip_toll) agree with the
    # same 800, via the SAME linked entry (not double-sourced from trips.toll)?
    conn = raw()
    toll_map = appmod._toll_by_trip(conn, [trip_id])
    trip_row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    per_trip_toll = appmod._trip_toll(trip_row, toll_map)
    conn.close()
    if _approx(per_trip_toll, 800):
        _ok(f"G1b: Per-trip toll figure (used in Business Performance/Route Analytics/invoice) correctly shows 800")
    else:
        _fail(f"G1b: Per-trip toll figure expected 800, got {per_trip_toll}")

    # G1c: THE CROSS-COMBINATION CHECK -- delete the trip. Does delete_trip guard against
    # orphaning toll_entries.trip_id the same way it already guards payment_allocations.trip_id?
    resp = client.post(f'/trips/delete/{trip_id}', follow_redirects=False)
    conn = raw()
    trip_still_exists = conn.execute("SELECT COUNT(*) c FROM trips WHERE id=?", (trip_id,)).fetchone()['c']
    toll_after = conn.execute("SELECT * FROM toll_entries WHERE toll_plaza='TestPlaza'").fetchone()
    conn.close()
    if trip_still_exists:
        _ok(f"G1c: delete_trip blocked the delete because a linked toll_entries row exists "
            f"(consistent with the existing payment_allocations guard) -- trip still present")
    else:
        _fail(f"G1c: CONFIRMED -- delete_trip deleted trip #{trip_id} with NO check for toll_entries.trip_id "
              f"referencing it (unlike its existing payment_allocations guard, which explicitly blocks deletion "
              f"in the same situation for a Payment). The toll_entries row (id={toll_after['id'] if toll_after else '?'}) "
              f"now has trip_id={toll_after['trip_id'] if toll_after else '?'} pointing at a trip that no longer exists -- "
              f"a dangling reference. The real-world toll cost itself is NOT lost (its own `maintenance` row is untouched, "
              f"so it still counts correctly in Dashboard/_period_financials totals), but the 'which trip incurred this "
              f"toll' link is silently and permanently destroyed with zero warning to the user, and any future per-trip "
              f"report (Route Analytics, Business Performance, invoice) that tries to look this toll up by trip_id will "
              f"simply never find it again -- same bug class as the trip/payment_allocations issue already fixed this "
              f"session, just for a different foreign key on the same `trips` table.")

    # ------------------------------------------------------------------
    # G1d: date-mismatch between a toll entry and the trip it's linked to -- does this create an
    # inconsistency between which REPORTING PERIOD each view attributes the same real cost to?
    conn = raw()
    trip2_exists = conn.execute("SELECT COUNT(*) c FROM trips WHERE id=?", (trip_id,)).fetchone()['c']
    conn.close()
    if not trip2_exists:
        # trip was actually deleted above (G1c broke) -- recreate a fresh one for this sub-test
        client.post('/trips/add', data={
            'date': '2029-06-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
            'party_name': 'P2Party', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '30000',
            'lr_number': 'P2-TOLL-002', 'lr_received': 'No',
        })
        conn = raw()
        trip_id_d = conn.execute("SELECT id FROM trips WHERE lr_number='P2-TOLL-002'").fetchone()['id']
        conn.close()
    else:
        trip_id_d = trip_id

    # Toll dated a month AFTER the trip (a very real scenario -- a toll receipt/FASTag entry
    # often gets logged days or weeks after the trip itself happened).
    client.post('/maintenance/toll/add', data={
        'date': '2029-07-15', 'vehicle_no': own_vehicle['vehicle_no'], 'toll_plaza': 'LateEntryPlaza',
        'amount': '650', 'source': 'manual', 'trip_lr': 'P2-TOLL-002' if trip2_exists else 'P2-TOLL-002',
    })
    conn = raw()
    pf_trip_month = appmod._period_financials(conn, '2029-06-01', '2029-06-30')  # trip's own month
    pf_toll_month = appmod._period_financials(conn, '2029-07-01', '2029-07-31')  # toll's own month
    toll_map2 = appmod._toll_by_trip(conn, [trip_id_d])
    conn.close()
    per_trip_toll2 = toll_map2.get(trip_id_d, 0)
    if _approx(pf_trip_month['toll'], 0) and _approx(pf_toll_month['toll'], 650) and _approx(per_trip_toll2, 650):
        _fail(f"G1d: CONFIRMED INCONSISTENCY -- toll entry dated 2029-07-15 but linked to a trip dated 2029-06-01. "
              f"_period_financials (Dashboard/company P&L) attributes the Rs 650 toll cost to JULY (the toll's own "
              f"date), but any per-trip report (Route Analytics, Business Performance, a trip-level invoice) that "
              f"uses _trip_toll()/_toll_by_trip() attributes the SAME Rs 650 to the trip's period, JUNE -- so a "
              f"per-trip P&L for June shows this cost while the company-wide Dashboard for June does not (it shows "
              f"in July instead). The money isn't duplicated or lost, but two legitimate reports in this app can "
              f"disagree about which month a real cost belongs to, for any toll logged on a different date than "
              f"its trip.")
    else:
        _ok(f"G1d: no reporting-period inconsistency detected for a toll dated later than its linked trip "
            f"(trip-month toll={pf_trip_month['toll']}, toll-month toll={pf_toll_month['toll']}, per-trip={per_trip_toll2})")

    # ==================================================================
    print()
    print("=" * 78)
    print("GROUP G2: vendor with overlapping roles (trip fuel-vendor AND maintenance vendor)")
    print("=" * 78)

    vname = 'P2MultiRoleVendor'
    client.post('/accounts/add', data={'name': vname, 'role': 'Vendor'})
    conn = raw()
    vid = conn.execute("SELECT id FROM vendors WHERE name=?", (vname,)).fetchone()['id']
    conn.close()

    # This vendor supplies fuel for one trip...
    client.post('/trips/add', data={
        'date': '2029-08-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'P2Party', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '20000',
        'lr_number': 'P2-MULTI-001', 'lr_received': 'No',
        'fuel_amount': '3000', 'fuel_vendor': vname,
    })
    # ...AND separately does a Tyre job on a different vehicle as a maintenance vendor.
    client.post('/maintenance/tyre/add', data={
        'date': '2029-08-05', 'vehicle_no': own_vehicle2['vehicle_no'] if own_vehicle2 else own_vehicle['vehicle_no'],
        'tyre_action': 'Replace', 'amount': '4000', 'paid_amount': '1500', 'vendor_name': vname,
    })
    conn = raw()
    fuel_check = conn.execute("SELECT fuel_vendor_id FROM trips WHERE lr_number='P2-MULTI-001'").fetchone()
    maint_check = conn.execute("SELECT vendor_id, amount, paid_amount FROM maintenance WHERE vendor_id=? AND category='Tyres'", (vid,)).fetchone()
    conn.close()

    if fuel_check and fuel_check['fuel_vendor_id'] == vid:
        _ok("G2a-1: Trip fuel_vendor_id correctly tagged to the multi-role vendor")
    else:
        _fail(f"G2a-1: Trip fuel_vendor_id not tagged correctly: {dict(fuel_check) if fuel_check else None}")
    if maint_check:
        _ok(f"G2a-2: Maintenance (Tyre) row correctly tagged to the same vendor (amount=4000, paid=1500)")
    else:
        _fail("G2a-2: Maintenance row for multi-role vendor not found/tagged correctly")

    html = client.get(f'/ledger/vendor/{vid}').get_data(as_text=True)
    has_fuel_line = 'Fuel' in html and 'P2-MULTI-001' in html
    has_maint_line = 'Tyre' in html or 'Maintenance' in html
    m_bal = re.search(r'Net Balance.*?₹([\d,.\-]+)', html, re.S)
    # Expected: fuel is a pure expense credit (owe vendor 3000, no payment made) = credit 3000;
    # maintenance is amount=4000 credit, paid_amount=1500 debit -> net credit 2500 outstanding.
    # Combined outstanding-to-vendor = 3000 + 2500 = 5500 (vendor is owed this much overall).
    if has_fuel_line and has_maint_line:
        _ok("G2a-3: Vendor ledger page shows BOTH the Fuel trip line and the Tyre maintenance line "
            "(both roles visible together, neither hidden by the other)")
    else:
        _fail(f"G2a-3: Vendor ledger missing one of the two roles -- fuel line present={has_fuel_line}, "
              f"maintenance line present={has_maint_line}")

    with appmod.app.test_request_context():
        entries = appmod._get_vendor_ledger_entries(vid)
    total_credit = sum(e['credit'] for e in entries)
    total_debit = sum(e['debit'] for e in entries)
    net = total_credit - total_debit
    expected_net = 3000 + (4000 - 1500)
    if _approx(net, expected_net):
        _ok(f"G2a-4: Combined vendor balance correctly sums BOTH roles with no cross-contamination "
            f"(expected outstanding {expected_net}, got {net})")
    else:
        _fail(f"G2a-4: CONFIRMED -- combined vendor balance wrong when the same vendor plays both a trip-fuel "
              f"role and a maintenance-vendor role simultaneously. Expected net outstanding {expected_net} "
              f"(3000 fuel + 2500 tyre balance), got {net} -- one role's entries may be dropping or double-counting "
              f"against the other.")

    # G2b: delete the maintenance (tyre) entry -- confirm this does NOT touch the trip's fuel
    # entry for the same vendor (no cross-deletion / cascade leakage between the two roles).
    conn = raw()
    maint_id = conn.execute("SELECT id FROM maintenance WHERE vendor_id=? AND category='Tyres'", (vid,)).fetchone()['id']
    conn.close()
    client.post(f'/maintenance/delete/{maint_id}')
    conn = raw()
    trip_after_maint_delete = conn.execute("SELECT fuel_vendor_id, fuel_amount FROM trips WHERE lr_number='P2-MULTI-001'").fetchone()
    conn.close()
    if trip_after_maint_delete and trip_after_maint_delete['fuel_vendor_id'] == vid and _approx(trip_after_maint_delete['fuel_amount'], 3000):
        _ok("G2b: Deleting the maintenance (Tyre) entry left the trip's Fuel role for the same vendor completely "
            "untouched (no cross-role cascade)")
    else:
        _fail(f"G2b: Deleting the maintenance entry unexpectedly affected the trip's fuel data for the same vendor: "
              f"{dict(trip_after_maint_delete) if trip_after_maint_delete else None}")

    # ==================================================================
    print()
    print("=" * 78)
    print("GROUP G3: single vehicle, one period -- Trip + Maintenance + linked Toll cost rollup")
    print("=" * 78)

    veh3 = own_vehicle2 if own_vehicle2 else own_vehicle
    client.post('/trips/add', data={
        'date': '2029-09-01', 'vehicle_no': veh3['vehicle_no'], 'type': 'own',
        'party_name': 'P2Party', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '25000',
        'lr_number': 'P2-VEH-001', 'lr_received': 'No',
        'fuel_amount': '4000', 'driver_adv_amount': '500',
    })
    client.post('/maintenance/tyre/add', data={
        'date': '2029-09-10', 'vehicle_no': veh3['vehicle_no'],
        'tyre_action': 'Replace', 'amount': '6000', 'paid_amount': '6000',
    })
    client.post('/maintenance/toll/add', data={
        'date': '2029-09-01', 'vehicle_no': veh3['vehicle_no'], 'toll_plaza': 'VehRollupPlaza',
        'amount': '300', 'source': 'manual', 'trip_lr': 'P2-VEH-001',
    })
    conn = raw()
    pf3 = appmod._period_financials(conn, '2029-09-01', '2029-09-30')
    conn.close()
    # Expected: fuel 4000 + adv 500 + toll 300 (linked) + maint(tyre, excl toll) 6000 = 10800
    expected3 = 4000 + 500 + 300 + 6000
    if _approx(pf3['total_expenses'], expected3):
        _ok(f"G3: Combined vehicle-period rollup (Trip fuel/adv + linked Toll + Tyre maintenance) correctly "
            f"totals {pf3['total_expenses']} (expected {expected3}) with no gap or double-count across the three areas")
    else:
        _fail(f"G3: CONFIRMED -- combined vehicle-period rollup wrong. Expected {expected3} "
              f"(fuel 4000 + adv 500 + linked toll 300 + tyre maintenance 6000), got {pf3['total_expenses']} "
              f"-- breakdown: fuel={pf3['fuel']}, driver_adv={pf3['driver_adv']}, toll={pf3['toll']}, maint={pf3['maint']}")

    print()
    print("=" * 78)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 78)
    if _findings:
        print("\nFindings (NOT fixed, for review):")
        for i, f in enumerate(_findings, 1):
            print(f"\n{i}. {f}")

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")

    conn = raw() if False else None  # no-op, keep structure


if __name__ == '__main__':
    main()
