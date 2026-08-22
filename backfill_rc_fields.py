"""One-time backfill: apply the now-widened _backfill_from_rc() (scheduler.py) against every
vehicle that ALREADY has rc_synced_data cached from a previous RC lookup — fills
chassis_number, engine_number, fitness_expiry, puc_valid_upto, permit_valid_upto from that
already-fetched data, wherever the real column is currently blank. No new API calls made; this
only uses data already sitting in the database. Never overwrites a manually-entered value (see
_backfill_from_rc's own guard).

Safe to re-run: any field already filled (by this script or manually) is left untouched.

Usage:
    python3 backfill_rc_fields.py            # dry run against fleet.db, reports what WOULD change
    python3 backfill_rc_fields.py --apply     # actually applies to fleet.db
"""
import os
import sys
import json
import sqlite3
import shutil
import datetime

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')

sys.path.insert(0, REPO_DIR)
from scheduler import _backfill_from_rc


def run(db_path, dry_run=True):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    vehicles = conn.execute("SELECT id, vehicle_no, rc_synced_data FROM vehicles WHERE rc_synced_data IS NOT NULL").fetchall()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    changed = 0
    for v in vehicles:
        before = conn.execute("""SELECT registration_date, chassis_number, engine_number,
                                 fitness_expiry, puc_valid_upto, permit_valid_upto, body_type, capacity_mt
                                 FROM vehicles WHERE id=?""", (v['id'],)).fetchone()
        try:
            rc_data = json.loads(v['rc_synced_data'])
        except (ValueError, TypeError):
            continue
        _backfill_from_rc(conn, v['id'], v['vehicle_no'], rc_data, now, created_by=None)
        after = conn.execute("""SELECT registration_date, chassis_number, engine_number,
                                fitness_expiry, puc_valid_upto, permit_valid_upto, body_type, capacity_mt
                                FROM vehicles WHERE id=?""", (v['id'],)).fetchone()
        diffs = [c for c in before.keys() if before[c] != after[c]]
        if diffs:
            changed += 1
            print(f"  {v['vehicle_no']}: filled {diffs} -> " +
                  ", ".join(f"{c}={after[c]!r}" for c in diffs))
    if dry_run:
        conn.rollback()
        print(f"\n[DRY RUN] {changed} of {len(vehicles)} RC-synced vehicles would be updated. "
              f"No changes written. Re-run with --apply to commit.")
    else:
        conn.commit()
        print(f"\n[APPLIED] {changed} of {len(vehicles)} RC-synced vehicles updated and committed.")
    conn.close()
    return changed


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    if not apply:
        print(f"Running DRY RUN against {REAL_DB} (no changes will be saved)...\n")
        run(REAL_DB, dry_run=True)
    else:
        print(f"Applying to real {REAL_DB}...\n")
        run(REAL_DB, dry_run=False)
