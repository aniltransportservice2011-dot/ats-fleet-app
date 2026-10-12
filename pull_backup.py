"""Download a fresh copy of the LIVE database from the server and keep it on this computer.

    BACKUP_URL=https://your-app.onrender.com BACKUP_TOKEN=the-secret  python3 pull_backup.py [folder] [--keep N]

Run it daily (see BACKUP_AND_RESTORE.md for a ready-made scheduled job). It asks the server's /internal/backup for a
consistent copy, checks the copy (integrity check, key tables not empty, row counts not suddenly collapsed compared with
the previous copy), saves it as render_YYYYmmdd_HHMMSS.db, and keeps the newest N (default 30). Exit code 0 = good backup saved,
anything else = something is wrong (wire it to an alert).
Default folder: ~/Downloads/fleet_db_backups/render
"""
import datetime
import glob
import gzip
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request

TABLES = ('trips', 'payments', 'users', 'parties', 'vendors', 'maintenance')


def counts(path):
    c = sqlite3.connect(path)
    try:
        out = {}
        for t in TABLES:
            try:
                out[t] = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out, c.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        c.close()


def main(argv):
    keep = 30
    if '--keep' in argv:
        i = argv.index('--keep')
        keep = int(argv[i + 1])
        del argv[i:i + 2]
    url = (os.environ.get('BACKUP_URL') or '').rstrip('/')
    token = os.environ.get('BACKUP_TOKEN') or ''
    if not url or not token:
        print('Set BACKUP_URL and BACKUP_TOKEN first.', file=sys.stderr)
        return 2
    dest_dir = os.path.expanduser(argv[1] if len(argv) > 1 else '~/Downloads/fleet_db_backups/render')
    os.makedirs(dest_dir, exist_ok=True)
    req = urllib.request.Request(url + '/internal/backup', method='POST', data=b'', headers={'X-Backup-Token': token, 'User-Agent': 'fleet-backup-puller/1'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        print(f'BACKUP FAILED: server answered {e.code} ({"wrong or missing BACKUP_TOKEN" if e.code == 403 else "see the server log"}).', file=sys.stderr)
        return 1
    except Exception as e:
        print(f'BACKUP FAILED: could not reach {url}: {e}', file=sys.stderr)
        return 1
    tmp = os.path.join(tempfile.mkdtemp(prefix='fleet_pull_'), 'copy.db')
    try:
        with open(tmp, 'wb') as f:
            f.write(gzip.decompress(blob))
    except OSError:
        print('BACKUP FAILED: the download was not a valid backup file.', file=sys.stderr)
        return 1
    new, integrity = counts(tmp)
    problems = []
    if integrity != 'ok':
        problems.append(f'integrity check says {integrity}')
    for t in ('trips', 'users'):
        if not new.get(t):
            problems.append(f'{t} is empty in the downloaded copy')
    previous = sorted(glob.glob(os.path.join(dest_dir, 'render_*.db')))
    if previous:
        old, _ = counts(previous[-1])
        for t in TABLES:
            if old.get(t) and new.get(t) is not None and new[t] < old[t] * 0.8:
                problems.append(f'{t} dropped from {old[t]} to {new[t]} since the last copy')
    if problems:
        print('BACKUP SUSPICIOUS, NOT KEPT: ' + '; '.join(problems), file=sys.stderr)
        os.remove(tmp)
        return 1
    dest = os.path.join(dest_dir, 'render_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db')
    os.replace(tmp, dest)
    for f in sorted(glob.glob(os.path.join(dest_dir, 'render_*.db')))[:-keep]:
        os.remove(f)
    print(f'Backup OK: {dest} ({os.path.getsize(dest) // 1024} KB) trips={new["trips"]} payments={new["payments"]} users={new["users"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
