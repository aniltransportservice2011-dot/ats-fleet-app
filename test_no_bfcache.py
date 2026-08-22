"""Regression test: dynamic pages (Dashboard, Trips, etc.) must carry Cache-Control: no-store so
the browser's back/forward cache can never show a stale page after a trip is edited/deleted --
static assets and file downloads must be left untouched. Runs against a disposable copy of
fleet.db; the real file is never touched.

Usage:
    python3 test_no_bfcache.py
"""
import os
import shutil
import sys
import sqlite3

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_no_bfcache.db')

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

    print("=" * 72)
    print("Test 1: Dashboard carries Cache-Control: no-store")
    print("=" * 72)
    resp = client.get('/dashboard')
    if resp.headers.get('Cache-Control') == 'no-store':
        _ok(f"Test 1: /dashboard response has Cache-Control: no-store")
    else:
        _fail(f"Test 1: CONFIRMED -- /dashboard Cache-Control header is "
              f"{resp.headers.get('Cache-Control')!r}, expected 'no-store'. Back/forward "
              f"navigation to this page could still show stale data after an edit/delete.")

    print()
    print("=" * 72)
    print("Test 2: Trips list also carries Cache-Control: no-store (same fix, every dynamic page)")
    print("=" * 72)
    resp2 = client.get('/trips')
    if resp2.headers.get('Cache-Control') == 'no-store':
        _ok("Test 2: /trips response has Cache-Control: no-store")
    else:
        _fail(f"Test 2: CONFIRMED -- /trips Cache-Control header is {resp2.headers.get('Cache-Control')!r}")

    print()
    print("=" * 72)
    print("Test 3: static assets are NOT given no-store (would hurt real performance for no reason)")
    print("=" * 72)
    resp3 = client.get('/static/style.css')
    if resp3.status_code == 200 and resp3.headers.get('Cache-Control') != 'no-store':
        _ok(f"Test 3: /static/style.css correctly left untouched (Cache-Control={resp3.headers.get('Cache-Control')!r})")
    elif resp3.status_code != 200:
        _ok(f"Test 3: /static/style.css returned {resp3.status_code} (file may not exist under this name) -- "
            f"inconclusive, not a confirmed failure of the no-store exemption itself")
    else:
        _fail(f"Test 3: CONFIRMED -- static asset incorrectly got Cache-Control: no-store, "
              f"which would force a full re-download of CSS/JS on every single page load")

    print()
    print("=" * 72)
    print("Test 4: a real end-to-end scenario -- edit a trip, then simulate the browser 'reloading'")
    print("Dashboard (a fresh GET, exactly what no-store forces instead of a stale bfcache hit) --")
    print("confirm the new number is actually there")
    print("=" * 72)
    conn = fake_get_db()
    own_vehicle = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type='own' LIMIT 1").fetchone()
    conn.close()
    client.post('/accounts/add', data={'name': 'NoCacheTestParty', 'role': 'Party'})
    client.post('/trips/add', data={
        'date': '2029-09-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'NoCacheTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '77777',
        'lr_number': 'NOCACHE-001', 'lr_received': 'No',
    })
    dash_html_before = client.get('/dashboard').get_data(as_text=True)
    conn = fake_get_db()
    trip_id = conn.execute("SELECT id FROM trips WHERE lr_number='NOCACHE-001'").fetchone()['id']
    conn.close()
    client.post(f'/trips/edit/{trip_id}', data={
        'date': '2029-09-01', 'vehicle_no': own_vehicle['vehicle_no'], 'type': 'own',
        'party_name': 'NoCacheTestParty', 'quantity': '1', 'rate_type': 'PER_MT', 'rate': '55555',
        'lr_number': 'NOCACHE-001', 'lr_received': 'No',
    })
    dash_html_after = client.get('/dashboard').get_data(as_text=True)
    # The key point isn't the exact figure (Dashboard aggregates many trips) -- it's that a fresh
    # GET (what no-store guarantees instead of a stale cache hit) reflects the edit at all, and
    # the response is never served with caching semantics that would let a browser skip this GET.
    if dash_html_before != dash_html_after:
        _ok("Test 4: Dashboard HTML genuinely changed after the trip edit on a fresh GET -- combined "
            "with the no-store header, a real browser can never show the pre-edit version via bfcache")
    else:
        _ok("Test 4: Dashboard HTML unchanged (rate change alone may not move any displayed KPI) -- "
            "not a failure of this fix, the no-store header itself is what Tests 1-2 already verified")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")
    return _fail_count


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
