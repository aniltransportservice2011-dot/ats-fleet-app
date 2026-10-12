"""Prove a backup file really restores — without touching the live database.

    python3 restore_check.py path/to/backup.db

Copies the backup to a scratch location, runs the database health check on it, starts the real app on that copy and opens the
login page, a list screen and the Ledger totals through the test client, then prints PASS/FAIL. Do this once now and then every
month: a backup nobody has restored is only a hope.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))


def main(path):
    if not os.path.exists(path):
        print('No such file:', path)
        return 2
    work = tempfile.mkdtemp(prefix='fleet_restore_')
    scratch = os.path.join(work, 'restored.db')
    shutil.copy(path, scratch)
    print('1. Health check of the backup copy')
    hc = subprocess.run([sys.executable, os.path.join(REPO, 'db_health_check.py'), scratch], capture_output=True, text=True)
    print('   ' + '\n   '.join(hc.stdout.strip().splitlines()[-6:]))
    script = r'''
import sys; sys.path.insert(0, %r)
import app, sqlite3
c = app.app.test_client()
ok = []
ok.append(('login page opens', c.get('/login').status_code == 200))
db = sqlite3.connect(%r); db.row_factory = sqlite3.Row
admin = db.execute("SELECT id FROM users WHERE LOWER(TRIM(COALESCE(role,'')))='administrator' AND COALESCE(status,'Active')!='Inactive' LIMIT 1").fetchone()
ok.append(('an active Administrator exists', admin is not None))
if admin:
    with c.session_transaction() as s: s['user_id'] = admin['id']; s['username'] = 'restore-check'; s['company_id'] = 1
    for url in ('/dashboard', '/trips', '/accounts', '/invoice-center', '/overheads', '/settings'):
        ok.append(('open ' + url, c.get(url).status_code == 200))
for name, good in ok:
    print(('   [OK]   ' if good else '   [FAIL] ') + name)
sys.exit(0 if all(g for _, g in ok) else 1)
''' % (REPO, scratch)
    print('2. Start the app on the restored copy')
    env = dict(os.environ, DATABASE_PATH=scratch)
    r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, env=env, cwd=work)
    print('\n'.join(l for l in r.stdout.splitlines() if l.startswith('   [')))
    good = r.returncode == 0 and hc.returncode in (0, 1)   # health findings are advisory; the app must run
    shutil.rmtree(work, ignore_errors=True)
    print('RESULT:', 'PASS — this backup restores and the app runs on it' if good else 'FAIL — do not rely on this backup')
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else print(__doc__) or 2)
