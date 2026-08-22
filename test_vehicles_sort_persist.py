"""Regression test: the Vehicles page sidebar link (bare /vehicles, no query string) should
restore the last sort/filter used on that page instead of silently resetting to defaults, while
every explicit link (sort, filter, Clear) keeps working exactly as typed. Runs against a
disposable copy of fleet.db; the real file is never touched.

Usage:
    python3 test_vehicles_sort_persist.py
"""
import os
import shutil
import sys
import sqlite3

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_vehicles_sort.db')

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
    print("Test 1: first-ever bare /vehicles hit (no session state yet) just renders normally")
    print("=" * 72)
    resp1 = client.get('/vehicles')
    if resp1.status_code == 200:
        _ok("Test 1: bare /vehicles with no prior session state returns 200 (no redirect loop, no crash)")
    else:
        _fail(f"Test 1: expected 200, got {resp1.status_code}")

    print()
    print("=" * 72)
    print("Test 2: visiting with an explicit sort applies it and remembers it")
    print("=" * 72)
    resp2 = client.get('/vehicles?sort=age&dir=desc')
    html2 = resp2.get_data(as_text=True)
    with client.session_transaction() as sess:
        remembered = sess.get('vehicles_last_query')
    if remembered == 'sort=age&dir=desc':
        _ok(f"Test 2: explicit sort link correctly saved to session ({remembered!r})")
    else:
        _fail(f"Test 2: CONFIRMED -- expected session to remember 'sort=age&dir=desc', got {remembered!r}")

    print()
    print("=" * 72)
    print("Test 3: a later BARE /vehicles hit (simulating the sidebar link) restores that sort")
    print("=" * 72)
    resp3 = client.get('/vehicles', follow_redirects=False)
    if resp3.status_code in (301, 302) and 'sort=age' in (resp3.headers.get('Location') or ''):
        _ok(f"Test 3: bare /vehicles correctly redirected to the remembered query "
            f"({resp3.headers.get('Location')}) -- sort survives a tab-switch-and-back")
    else:
        _fail(f"Test 3: CONFIRMED BUG STILL PRESENT -- bare /vehicles did not restore the last sort. "
              f"status={resp3.status_code}, location={resp3.headers.get('Location')}")

    print()
    print("=" * 72)
    print("Test 4: the Clear link (?tab=all) is respected as-is, not overridden by the remembered sort")
    print("=" * 72)
    resp4 = client.get('/vehicles?tab=all', follow_redirects=False)
    if resp4.status_code == 200:
        _ok("Test 4: ?tab=all (Clear link) renders directly with no redirect -- explicit query "
            "strings are never intercepted, even a 'clearing' one")
    else:
        _fail(f"Test 4: CONFIRMED -- Clear link got redirected/broken instead of rendering directly "
              f"(status={resp4.status_code})")

    print()
    print("=" * 72)
    print("Test 5: after Clear, a later bare hit restores the CLEARED state, not the old sort")
    print("=" * 72)
    resp5 = client.get('/vehicles', follow_redirects=False)
    loc5 = resp5.headers.get('Location') or ''
    if resp5.status_code in (301, 302) and loc5.endswith('tab=all') and 'sort=age' not in loc5:
        _ok(f"Test 5: bare /vehicles after Clear correctly restores the cleared state ({loc5}), "
            f"not the earlier sort -- most-recent-query-wins behaves as expected")
    else:
        _fail(f"Test 5: CONFIRMED -- expected redirect ending in 'tab=all' with no leftover sort, got "
              f"status={resp5.status_code}, location={loc5!r}")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")
    return _fail_count


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
