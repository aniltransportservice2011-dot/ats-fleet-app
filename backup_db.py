"""Safe backup of the fleet database.

    python3 backup_db.py [destination_folder] [--keep N]

Uses SQLite's own online backup, so it is consistent even while the app is running (a plain `cp` of a WAL-mode database
can give a broken copy). Verifies the copy (integrity check + row counts), keeps the newest N copies (default 30) and
deletes older ones. Run it daily, e.g. from cron / launchd, and copy the folder somewhere off this machine.
Default folder: ~/Downloads/fleet_db_backups
"""
import datetime
import glob
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get('DATABASE_PATH') or os.path.join(REPO, 'fleet.db')


def main(argv):
    keep = 30
    if '--keep' in argv:
        i = argv.index('--keep')
        keep = int(argv[i + 1])
        del argv[i:i + 2]
    dest_dir = os.path.expanduser(argv[1] if len(argv) > 1 else '~/Downloads/fleet_db_backups')
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, 'fleet_daily_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db')
    src = sqlite3.connect(f'file:{SRC}?mode=ro', uri=True)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    problems = []
    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        problems.append('integrity check failed')
    for t in ('trips', 'payments', 'users', 'parties', 'vendors', 'maintenance'):
        try:
            a = src.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            b = dst.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            if a != b:
                problems.append(f'{t}: {a} rows in the database, {b} in the copy')
        except sqlite3.OperationalError:
            pass
    src.close()
    dst.close()
    if problems:
        os.remove(dest)
        print('BACKUP FAILED, copy removed: ' + '; '.join(problems), file=sys.stderr)
        return 1
    old = sorted(glob.glob(os.path.join(dest_dir, 'fleet_daily_*.db')))[:-keep]
    for f in old:
        os.remove(f)
    print(f'Backup OK: {dest} ({os.path.getsize(dest) // 1024} KB); kept newest {keep}, removed {len(old)} older')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
