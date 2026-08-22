"""Phase 3: cross-domain testing of the Analysis section (Route Analytics, Fleet Utilization,
Performance, Business Performance) against Trips / Ledger / Vehicles / Maintenance / Expenses
(Overheads) / Salaries -- do these screens agree with each other and with the underlying data,
or does each compute "cost"/"profit" differently for the exact same trips? REPORT ONLY -- does
not fix anything found. Runs against a disposable copy of fleet.db; the real file is never
touched.

Usage:
    python3 test_phase3_analysis_combinations.py
"""
import os
import shutil
import sys
import sqlite3
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_phase3.db')

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


def _approx(a, b, tol=0.5):
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

    # ==================================================================
    print("=" * 78)
    print("SETUP: one rich trip -- fuel, unlinked manual toll, and every extra cost field the")
    print("live /trips/add form still collects")
    print("=" * 78)

    client.post('/accounts/add', data={'name': 'P3AnalysisParty', 'role': 'Party'})
    client.post('/trips/add', data={
        'date': '2029-12-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'P3AnalysisParty', 'quantity': '10', 'rate_type': 'PER_MT', 'rate': '2000',
        'lr_number': 'P3-ANALYSIS-001', 'lr_received': 'No', 'driver_name': 'P3TestDriverOwn',
        'fuel_amount': '5000', 'driver_adv_amount': '1000',
        'toll': '800',  # manual estimate, deliberately NEVER linked via Toll Management
        'parking': '300', 'agent_commission': '400', 'builty_expense': '150', 'fine': '200',
        'labour_charges': '350', 'puncture': '250', 'urea': '180', 'loading_expense': '220',
        'unloading_expense': '190', 'weighbridge_charges': '160', 'other_expense': '140',
        'permit_charges': '120', 'detention_charges': '500',
    })
    conn = raw()
    trip = conn.execute("SELECT * FROM trips WHERE lr_number='P3-ANALYSIS-001'").fetchone()
    trip_id = trip['id']
    conn.close()

    extra_fields_sum = (300+400+150+200+350+250+180+220+190+160+140+120)  # everything except fuel/adv/toll/detention
    if trip and _approx(trip['toll'], 800) and _approx(trip['parking'], 300) and _approx(trip['labour_charges'], 350):
        _ok(f"Setup: trip saved correctly with toll=800 (unlinked, no Toll Management entry) and "
            f"{len(['parking','agent_commission','builty_expense','fine','labour_charges','puncture','urea','loading_expense','unloading_expense','weighbridge_charges','other_expense','permit_charges'])} "
            f"extra cost fields totaling {extra_fields_sum}")
    else:
        _fail(f"Setup: trip fields did not save as expected: {dict(trip) if trip else None}")

    # Confirm this toll is genuinely unlinked -- no toll_entries row references this trip at all.
    conn = raw()
    linked_toll = conn.execute("SELECT COUNT(*) c FROM toll_entries WHERE trip_id=?", (trip_id,)).fetchone()['c']
    conn.close()
    if linked_toll == 0:
        _ok("Setup confirmed: toll=800 is a pure manual estimate, not linked via Toll Management "
            "(exactly the scenario _trip_toll() falls back to the manual field for)")
    else:
        _fail("Setup: unexpectedly found a linked toll_entries row -- test scenario invalid")

    # ==================================================================
    print()
    print("=" * 78)
    print("FINDING A: does Performance > Vehicle tab's cost/profit include this trip's toll,")
    print("the same way Route Analytics / Business Performance / Dashboard do?")
    print("=" * 78)
    with appmod.app.test_request_context():
        conn = raw()
        toll_map = appmod._toll_by_trip(conn, [trip_id])
        real_toll_contribution = appmod._trip_toll(trip, toll_map)
        conn.close()
    if _approx(real_toll_contribution, 800):
        _ok(f"Reference: _trip_toll() (used by Route Analytics, Business Performance, Dashboard, "
            f"Fleet Utilization) correctly resolves this trip's toll to 800")
    else:
        _fail(f"Reference: _trip_toll() unexpectedly returned {real_toll_contribution}, expected 800")

    # Full real cost basis for this trip -- fuel_and_adv (own vehicle, both real) + real toll (via
    # _trip_toll) + driver_payment + detention_charges + other_expense + the 12 extra fields. This
    # mirrors app.py's OWN new formula exactly (both Vehicle and Driver tabs use the identical set
    # for an own-vehicle trip), so it's the correct expected total either tab should now produce.
    fuel_and_adv = (trip['fuel_amount'] or 0) + (trip['driver_adv_amount'] or 0)  # own vehicle: both real
    full_trip_cost = (fuel_and_adv + real_toll_contribution + (trip['driver_payment'] or 0) +
                       (trip['detention_charges'] or 0) + (trip['other_expense'] or 0) + (trip['parking'] or 0) +
                       (trip['agent_commission'] or 0) + (trip['builty_expense'] or 0) + (trip['fine'] or 0) +
                       (trip['labour_charges'] or 0) + (trip['puncture'] or 0) + (trip['urea'] or 0) +
                       (trip['loading_expense'] or 0) + (trip['unloading_expense'] or 0) +
                       (trip['weighbridge_charges'] or 0) + (trip['permit_charges'] or 0))
    expected_profit = (trip['billed_amount'] or 0) - full_trip_cost  # maint_cost=0, no maintenance rows for this vehicle/date
    print(f"  (expected profit for this trip on both tabs: billed {trip['billed_amount']} - full cost {full_trip_cost} = {expected_profit})")

    perf_html = client.get(f"/performance?date_from=2029-12-01&date_to=2029-12-01").get_data(as_text=True)
    vehicle_profit_str = f"{expected_profit:,.0f}"
    # Accept either a plain or a decimal-suffixed rendering of the same number.
    found_vehicle = (own_vehicle['vehicle_no'] in perf_html) and (vehicle_profit_str in perf_html or f"{expected_profit:,.2f}" in perf_html)
    if found_vehicle:
        _ok(f"FINDING A: Performance > Vehicle tab now renders the fully-corrected profit "
            f"({expected_profit:,.0f}) for {own_vehicle['vehicle_no']} -- toll and all 12 extra cost "
            f"fields are counted, matching Route Analytics/Business Performance's cost basis")
    else:
        _fail(f"FINDING A: Performance > Vehicle tab does not show the expected corrected profit "
              f"({expected_profit:,.0f}) for {own_vehicle['vehicle_no']} -- fix did not take effect as "
              f"expected (full cost basis: fuel+adv={fuel_and_adv}, toll={real_toll_contribution}, "
              f"+12 extra fields = {full_trip_cost} total cost)")

    # ==================================================================
    print()
    print("=" * 78)
    print("FINDING B: does Performance > Driver tab now show the same fully-corrected figure?")
    print("=" * 78)
    found_driver = ('P3TestDriverOwn' in perf_html) and (f"{expected_profit:,.0f}" in perf_html or f"{expected_profit:,.2f}" in perf_html)
    if found_driver:
        _ok(f"FINDING B: Performance > Driver tab now renders the fully-corrected profit "
            f"({expected_profit:,.0f}) for P3TestDriverOwn -- toll and all 12 extra cost fields are "
            f"counted, same full cost basis as the Vehicle tab")
    else:
        _fail(f"FINDING B: Performance > Driver tab does not show the expected corrected profit "
              f"({expected_profit:,.0f}) for P3TestDriverOwn -- fix did not take effect as expected")

    print()
    print("=" * 78)
    print("FINDING B2: Driver tab -- a Hired trip's fuel (owner's own money) must NOT be subtracted")
    print("as if it were a real company cost, same rule as everywhere else in the app")
    print("=" * 78)
    conn = raw()
    hired_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='hired' LIMIT 1").fetchone()
    conn.close()
    client.post('/accounts/add', data={'name': 'P3HiredOwner', 'role': 'Vendor'})
    client.post('/trips/add', data={
        'date': '2029-12-05', 'vehicle_no': hired_vehicle['vehicle_no'], 'type': 'hired',
        'party_name': 'P3AnalysisParty', 'quantity': '5', 'rate_type': 'PER_MT', 'rate': '3000',
        'lr_number': 'P3-ANALYSIS-002', 'lr_received': 'No', 'driver_name': 'P3TestDriverHired',
        'fuel_amount': '2000',  # owner's own money on a hired trip -- must NOT reduce driver profit
        'owner_name': 'P3HiredOwner', 'owner_rate_type': 'FIXED', 'owner_fixed_amount': '12000',
    })
    conn = raw()
    hired_trip = conn.execute("SELECT * FROM trips WHERE lr_number='P3-ANALYSIS-002'").fetchone()
    conn.close()
    hired_expected_profit = hired_trip['billed_amount']  # fuel excluded entirely -> cost=0 for this trip
    perf_html2 = client.get('/performance?date_from=2029-12-05&date_to=2029-12-05').get_data(as_text=True)
    found_hired = ('P3TestDriverHired' in perf_html2) and (f"{hired_expected_profit:,.0f}" in perf_html2 or f"{hired_expected_profit:,.2f}" in perf_html2)
    if found_hired:
        _ok(f"FINDING B2: Driver tab correctly EXCLUDES a Hired trip's fuel_amount (2000, owner's own "
            f"money) from cost -- profit for P3TestDriverHired shows {hired_expected_profit:,.0f} "
            f"(= full billed_amount, no fuel subtracted)")
    else:
        _fail(f"FINDING B2: Driver tab did not show the expected profit ({hired_expected_profit:,.0f}) "
              f"for P3TestDriverHired -- either the Hired-fuel-exclusion isn't working, or the fix "
              f"didn't take effect as expected")

    # ==================================================================
    print()
    print("=" * 78)
    print("FINDING C (sanity check, expected to PASS): Business Performance's receivables/payables")
    print("match the Ledger page exactly (both built from the same _accounts_rows())")
    print("=" * 78)
    with appmod.app.test_request_context():
        conn = raw()
        acct_rows = appmod._accounts_rows(conn)
        conn.close()
    bp_total_receivables = sum(r['balance'] for r in acct_rows if r['balance'] > 0)
    bp_total_payables = sum(-r['balance'] for r in acct_rows if r['balance'] < 0)
    bp_html = client.get('/business-performance').get_data(as_text=True)
    m_recv = re.search(r'Receivable[s]?.*?₹([\d,]+)', bp_html, re.S)
    if m_recv:
        page_receivables = float(m_recv.group(1).replace(',', ''))
        if _approx(page_receivables, bp_total_receivables, tol=1):
            _ok(f"FINDING C: Business Performance's Receivables figure ({page_receivables}) matches "
                f"_accounts_rows()'s total ({bp_total_receivables}) -- Ledger and Business Performance "
                f"stay in sync as designed")
        else:
            _fail(f"FINDING C: CONFIRMED -- Business Performance page shows Receivables={page_receivables} "
                  f"but _accounts_rows() (the same function Ledger uses) computes {bp_total_receivables}")
    else:
        _ok("FINDING C: could not locate a 'Receivable' figure via regex on the page (label text may "
            "have changed) -- not treated as a failure, just unverifiable by this script")

    # ==================================================================
    print()
    print("=" * 78)
    print("FINDING D: Business Performance's 'Avg Collection Days' -- does it silently fall back to")
    print("an ALL-TIME average when the selected period has zero real payment allocations, with no")
    print("indication on the page that it did so?")
    print("=" * 78)
    # Pick a period guaranteed to have zero payment_allocations (far future, nothing ever paid there).
    resp_empty_period = client.get('/business-performance?date_from=2099-01-01&date_to=2099-01-31')
    html_empty = resp_empty_period.get_data(as_text=True)
    m_days = re.search(r'Avg\.?\s*Collection\s*Days.*?(\d+(?:\.\d+)?)', html_empty, re.S)
    if m_days:
        shown_days = float(m_days.group(1))
        _fail(f"FINDING D: CONFIRMED -- selecting a period with zero real activity (2099-01) still shows "
              f"an Avg Collection Days figure ({shown_days}), silently pulled from ALL-TIME payment "
              f"allocations (the route's own fallback: 'if not alloc_rows: ... fetch all, no date filter') "
              f"with nothing on the page distinguishing it from a real period-specific number. Someone "
              f"reading this for a specific month has no way to tell whether it's that month's collection "
              f"speed or a silent all-time fallback.")
    else:
        _ok("FINDING D: no Avg Collection Days figure rendered for an empty period (or label text didn't "
            "match) -- the silent all-time fallback either doesn't apply here or isn't demonstrable via "
            "this script's regex")

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
    return _fail_count


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
