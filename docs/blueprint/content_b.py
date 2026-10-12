"""Part B: infrastructure, deployment, backups and outside services."""
from lib import *
import diagrams as D
import facts as F


def part_b():
    st = [NextPageTemplate('divider'), PageBreak(), PartDivider('B', 'Infrastructure and deployment', 'Where the app runs, how code gets there, what it does when it wakes up, and how the data is protected.',
                                                            ['Hosting on Render', 'Settings the app reads (environment variables)', 'From laptop to live site', 'What the app does by itself on every start', 'Backups and restore', 'Outside services and what happens when they fail']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('6. Hosting: one web service on Render'))
    st.append(P('The production system is a single Render <b>web service</b> that serves the website and the phone app from the same address (the live site is https://ats-fleet-app.onrender.com). Render supplies the machine, the HTTPS certificate, the proxy in front, the persistent disk and daily disk snapshots. Nothing else is needed to run it.', 'lead'))
    st.append(H2('6.1 How the web service starts'))
    st.append(code('web: gunicorn app:app --worker-class=gthread --workers=2 --threads=4 --timeout=60'))
    st += bullets(['<b>gunicorn</b> is the production web server. <font face="Courier">app:app</font> means "inside app.py, use the object named app" (the Flask application).',
                   '<b>--workers=2</b> starts two separate copies of the app. They do not share memory, which is why anything that must be shared (login attempt counters, SMS codes, the secret key) lives in the database, not in Python variables.',
                   '<b>--worker-class=gthread --threads=4</b> gives each worker four lanes, so 2 × 4 = <b>8 requests at the same time</b>. While one thread waits for the database or a PDF, the others keep serving.',
                   '<b>--timeout=60</b> kills a request that runs longer than 60 seconds (protects against a stuck report).',
                   'The Flask development server (<font face="Courier">python app.py</font>) is only for your own computer: it listens on 127.0.0.1:5050, has debug mode on by default and handles requests in threads without the gunicorn safety net.'])
    st.append(H2('6.2 Why one machine is enough, and when it is not'))
    st.append(P('SQLite allows many readers and one writer at a time; in WAL mode readers never wait for the writer. With 30 seconds of lock waiting, two people saving at the same instant simply queue for a moment. The load test (12,000 trips, ten simultaneous users, real gunicorn) kept the slowest 5% of requests under 1.7 seconds. The honest ceiling is roughly 25–30 people editing simultaneously or very heavy report use; the planned answer is PostgreSQL (Chapter 25), which the code and schema are already prepared for.'))
    st.append(callout('warn', 'The persistent disk is not optional', 'A Render web service starts from a clean file system every time it is deployed or restarted. The database file (and the uploaded documents) survive only if they live on a <b>Persistent Disk</b> mounted on the service, with <font face="Courier">DATABASE_PATH</font> pointing into it (for example /var/data/fleet.db). Render keeps an automatic snapshot of the disk every 24 hours for at least 7 days. A disk can only be reached by its one service, cannot be shared, and can be enlarged but never shrunk. <b>Status: to be confirmed on the live service.</b>'))
    st.append(PageBreak())

    st.append(H1('7. Settings the app reads from its environment'))
    st.append(P('"Environment variables" are named settings given to the program from outside (in Render\'s dashboard or the shell), so secrets never have to be written in the code. The table is generated from the real <font face="Courier">os.environ.get(...)</font> calls in app.py.'))
    envd = {
        'SECRET_KEY': ('Signs the login cookie. If not set, the app creates a private random key once and keeps it in the database (settings row _auto_secret_key). Set your own long random value on Render.', 'Render: set'),
        'DATABASE_PATH': ('Where fleet.db lives. Default: next to app.py. On Render it must point onto the persistent disk.', 'Render: set'),
        'RENDER': ('Set automatically by Render. Switches on Secure cookies and ProxyFix (so the real visitor address is known for the login limit).', 'automatic'),
        'FLASK_DEBUG': ('Only used by python app.py. Default 1 (debug on) for your laptop; use 0 whenever the machine is reachable by others (the LAN command in the runbook does).', 'local'),
        'FLASK_HOST': ('Which network address the development server binds to. 127.0.0.1 = this computer only; 0.0.0.0 = also other devices on the Wi-Fi.', 'local'),
        'FLASK_USE_RELOADER': ('1 turns on auto-restart on every file save (off by default so a phone being tested is not disconnected).', 'local'),
        'WERKZEUG_RUN_MAIN': ('Internal flag of the auto-reloader; lets the scheduler start only once.', 'internal'),
        'ALLOW_SELF_SIGNUP': ('1 reopens the public /app/signup. Default closed: only an Administrator creates users.', 'default off'),
        'DISABLE_ORIGIN_CHECK': ('1 disables the cross-site POST check. For diagnosing a proxy problem only.', 'default off'),
        'BACKUP_TOKEN': ('Secret that unlocks POST /internal/backup (database download). Without it the endpoint refuses everyone.', 'Render: set'),
        'INTERNAL_SYNC_TOKEN': ('Secret for POST /internal/sync-tick, the outside cron trigger for background syncs. Without it the endpoint refuses everyone.', 'Render: set if syncing'),
        'ENABLE_COMPLIANCE_SCHEDULER': ('1 starts the in-process APScheduler (local only).', 'default off'),
        'ENABLE_RC_SYNC': ('1 allows the paid eChallan RC sync job.', 'default off'),
        'ENABLE_CHALLAN_SYNC': ('1 allows the paid eChallan challan sync job.', 'default off'),
    }
    st.append(tbl([['Variable', 'What it does', 'Typical state']] + [[f'<font face="Courier">{k}</font>', envd.get(k, ('', ''))[0], envd.get(k, ('', ''))[1]] for k in F.ENV_VARS], [24, 61, 15]))
    st.append(Spacer(1, 6))
    st.append(P('Provider keys for the placeholder compliance providers (<font face="Courier">VAHAN_API_KEY</font>, <font face="Courier">INSURANCE_API_KEY</font>, <font face="Courier">PUC_API_KEY</font>) are read in <font face="Courier">config/apis.py</font>; until real keys exist those providers return clearly labelled mock data and the screens say so. The two real paid keys (eChallan RC lookup, MSG91) are not environment variables: the Administrator saves them in Settings, they are stored in the <font face="Courier">settings</font> table, shown masked, and never printed back into a page.'))
    st.append(PageBreak())

    st.append(H1('8. From the developer\'s laptop to the live site'))
    st.append(D.d_deploy())
    st.append(H2('8.1 The deployment discipline'))
    st += bullets(['<b>Test first, on copies.</b> Every change that touches data is tried on a disposable copy of the database (never the real fleet.db), with network calls blocked so a bug can never spend paid API credits or send a real SMS.',
                   '<b>Database and code are separate steps.</b> The rule in CLAUDE.md is to migrate and verify the database first, then deploy the code that needs it. Because the user deploys code without running SQL by hand, every new database change also ships a <i>guarded self-applying step</i> that runs at start-up (below), so shipping the code first can never take the site down — which is exactly what happened once.',
                   '<b>Back up before touching the live file.</b> Before the overhead-allocation change the app saves its own copy (<font face="Courier">fleet.db.bak.before_overhead_alloc_*</font>); manual changes are preceded by a SQLite online backup into ~/Downloads/fleet_db_backups.',
                   '<b>Branches.</b> The repository has main, maintenance (the current working branch), feature/ledger-redesign and chore/pycache-gitignore. The database file, uploads of documents and environment files are in .gitignore; eleven old database backups were committed in the past and are flagged in the readiness report for removal from history.'])
    st.append(H2('8.2 What the app does by itself every time it starts'))
    st.append(P('These steps run while app.py is being imported — once per gunicorn worker — before the first request. Each is written so that running it again, or running it in two workers at the same moment, does no harm. Progress is printed as <font face="Courier">[schema] ...</font> or <font face="Courier">[security] ...</font> lines in the server log.'))
    st.append(tbl([['#', 'Function (line)', 'What it does', 'Why it is safe to repeat'],
                   ['1', '<font face="Courier">_ensure_overhead_allocation_column</font> (129)', 'Adds payment_allocations.overhead_id (so payments can settle expense bills) and widens the "exactly one target" CHECK.', 'Saves a backup copy first; one BEGIN IMMEDIATE transaction; re-checks for the column inside it; verifies row counts.'],
                   ['2', '<font face="Courier">_ensure_performance_indexes</font> (245)', f'Creates the lookup indexes (the database holds {F.N_INDEXES} indexes in total) including partial indexes on mostly-empty allocation columns.', 'CREATE INDEX IF NOT EXISTS; a missing column is skipped, never fatal.'],
                   ['3', '<font face="Courier">_ensure_wal_mode</font> (276)', 'Switches the file to WAL journaling (readers never block the writer).', 'Reads the mode first; the setting is persistent.'],
                   ['4', '<font face="Courier">_ensure_auth_tables</font> (303)', 'Creates login_otps and auth_attempts.', 'CREATE TABLE IF NOT EXISTS.'],
                   ['5', '<font face="Courier">_ensure_secret_key</font> (344)', 'Uses SECRET_KEY if set; otherwise creates one random key and stores it in settings (_auto_secret_key).', 'INSERT OR IGNORE, so both workers end up with the same key.'],
                   ['6', '<font face="Courier">_ensure_single_admin_index</font> (372)', 'Creates the unique index that allows only one active Administrator.', 'If two active Administrators already exist it skips and warns instead of crashing.'],
                   ['7', '<font face="Courier">_remove_twilio_settings</font> (396)', 'Deletes any saved Twilio credentials left from the old SMS provider.', 'A plain DELETE of known keys; harmless when none exist.']], [4, 27, 38, 31], mono_cols=()))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'What the app does NOT do on start', 'It never deletes business data, never rewrites trips or payments, and never calls a paid outside service. Background syncs are opt-in (Chapter 20).'))
    st.append(PageBreak())

    st.append(H1('9. Backups and restore'))
    st.append(P('The business is one file, so protecting that file is the single most important operational duty. There are two independent safety nets (Figure 12) — and one honest gap.', 'lead'))
    st.append(D.d_backup())
    st.append(H2('9.1 Net one: Render\'s snapshots'))
    st.append(P('Automatic, every 24 hours, kept for at least 7 days, no configuration beyond having the persistent disk. Restore is all-or-nothing from the service\'s Disks page: the disk returns to that moment and everything after is lost. Use it for "everything went wrong; go back to yesterday".'))
    st.append(H2('9.2 Net two: your own verified daily copy'))
    st.append(tbl([['Piece', 'Where', 'What it does'],
                   ['POST /internal/backup', 'app.py line ~12427', 'Needs the secret in the X-Backup-Token header (BACKUP_TOKEN). Uses SQLite\'s online backup (safe while people work), integrity-checks the copy, gzips it and streams it. Wrong tries are counted per address; 5 wrong in 15 minutes locks that address out.'],
                   ['pull_backup.py', 'your Mac, run daily', 'Calls the endpoint, un-gzips, runs PRAGMA integrity_check, refuses a copy where trips or users are empty or any key table lost more than 20% of its rows since the last copy, saves render_YYYYmmdd_HHMMSS.db and keeps the newest 30.'],
                   ['restore_check.py', 'your Mac, monthly', 'Copies a backup to a scratch folder, health-checks it, starts the real app on it and opens login, dashboard, trips, ledger, invoice center, expenses and settings: prints PASS or FAIL. Never touches the live file.'],
                   ['backup_db.py', 'your Mac', 'Same safe backup for the local fleet.db (keeps 30).'],
                   ['db_health_check.py', 'anywhere', 'Read-only audit: integrity, schema drift versus schema.sql, tables missing company_id, unindexed lookup columns, business-data anomalies (over-received trips, duplicate LRs, desynced payments).']], [22, 20, 58]))
    st.append(Spacer(1, 6))
    st.append(callout('warn', 'The gap: uploaded files', 'Insurance papers, toll receipts and the logo are saved under static/uploads/ in the app folder. The database backup does not include them, and unless that folder is on the persistent disk a redeploy deletes them. The planned fix is to make the upload folder follow the disk, accept only PDF/JPG/PNG/WEBP up to about 8 MB, shrink photos on upload, and show disk usage to the Administrator.'))
    st.append(PageBreak())

    st.append(H1('10. Outside services and what happens when they fail'))
    st.append(P('The app calls five outside services. None is needed to open a screen or record a trip; each only powers one feature, and each has a defined failure behaviour.', 'lead'))
    st.append(tbl([['Service', 'Used for', 'How it is called', 'Cost', 'If it fails'],
                   ['<b>MSG91</b>', 'SMS login codes', 'POST https://control.msg91.com/api/v5/otp with the key in an <font face="Courier">authkey</font> header; params template_id, mobile (91 + 10 digits), otp, otp_length, otp_expiry. Success means JSON type = success even on HTTP 200.', 'a few paise per SMS (about Rs 0.15–0.25); needs DLT registration', 'User sees "Could not send the code"; the code is voided; password login still works.'],
                   ['<b>eChallan API</b>', 'Vehicle RC details, traffic challans', 'GET https://api.echallan.app/vahanfin/vehicle and /echallan, key in an <font face="Courier">X-API-Key</font> header (<font face="Courier">providers/echallan_client.py</font>)', 'paid per call; background jobs are off unless enabled', 'Existing cached data is kept; failures are logged in sync_log; nothing is overwritten.'],
                   ['<b>OSRM</b> (router.project-osrm.org)', 'Driving routes, distance, time', 'GET route with up to 3 alternatives; timeout 15 s', 'free, shared', 'Route Finder shows a plain message; rest of the app unaffected.'],
                   ['<b>Nominatim</b> (OpenStreetMap)', 'Turn typed text into a map point', 'GET search; timeout 8 s; limited to 1 request per second by the app', 'free; autocomplete is forbidden by policy, so Photon is used for typing', 'Asks the user to add a town/state or pick from suggestions.'],
                   ['<b>Photon</b> (photon.komoot.io)', 'Suggestions while typing a place', 'GET, India bounding box, country code IN, 6 s timeout, cached 1 hour', 'free', 'Suggestion list stays empty; the user can still type and Search.'],
                   ['<b>Overpass</b> (overpass-api.de, mirror overpass.kumi.systems)', 'Toll plazas, towns, petrol pumps, airports, junctions along a route', 'POST one query per route in ~50 km boxes; 22 s per call, 30 s total budget; tries main → mirror → main', 'free, shared and often busy (7–30 s typical)', 'Route shown without stops, "Try again" offered; failures are not cached.'],
                   ['<b>Google Fonts</b>', 'Poppins typeface on the phone app\'s sign-in, SMS-code and sign-up screens only', 'CSS link in those four templates', 'free', 'Browser falls back to a system font.']], [15, 15, 33, 17, 20]))
    st.append(Spacer(1, 6))
    st.append(P('<b>Protecting the free services:</b> the Route Finder allows at most 3 searches at once (a bounded semaphore) and caches results for an hour per worker; Nominatim is throttled to 1 request per second; every call has a hard timeout and a shared 30-second budget so one lookup can never hang a thread for long.'))
    st.append(PageBreak())
    return st
