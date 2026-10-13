from flask import Flask, render_template, request, redirect, url_for, session, g, jsonify, send_from_directory
import sqlite3
import sys
import datetime
import calendar
import os
import json
import re
import hashlib
import threading
import time as _time
from werkzeug.utils import secure_filename
import compliance_service as cs
from providers.echallan_client import fetch_rc, parse_challan_date, parse_rc_date
from providers import osm_routes
import db

app = Flask(__name__)
# Falls back to the same literal value that used to be hardcoded here — local dev behaviour
# (including any session someone's currently logged into) is unchanged unless SECRET_KEY is
# actually set. A real deployment MUST set SECRET_KEY to something random and never commit it;
# a leaked/guessable secret key lets someone forge login sessions.
app.secret_key = os.environ.get('SECRET_KEY', 'fleet-local-app-anil-transport-secret-key-2026')
app.permanent_session_lifetime = datetime.timedelta(days=30)
# Security defaults. SameSite=Lax stops a cookie being sent with a cross-site POST (the usual forged-form attack);
# Secure (https only) is switched on when running on Render, which always serves https; the upload cap stops one
# giant request filling memory. Behind Render's proxy the real visitor address comes from X-Forwarded-For — needed so
# the login throttle counts the visitor, not Render's proxy.
_ON_RENDER = bool(os.environ.get('RENDER'))
app.config.update(SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=_ON_RENDER, MAX_CONTENT_LENGTH=16 * 1024 * 1024)
if _ON_RENDER:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Was a bare relative path ('fleet.db') — only resolves correctly if the process happens to be
# started from exactly this directory, which is an easy thing to get wrong under a process
# manager (systemd/gunicorn) on a real server. Getting it wrong wouldn't even error — sqlite3
# would silently create a new, empty database file wherever the process actually started from,
# which looks exactly like "all my data disappeared" even though the real file is sitting
# untouched right here. Now anchored to this file's own directory (same pattern the upload
# directories below already use), so it resolves to the same file regardless of working
# directory — and still overridable via DATABASE_PATH for a deployment that wants the DB
# somewhere else (e.g. a separate mounted volume). Locally this resolves to the exact same
# fleet.db that's always been here — zero behavior change.
DB = os.environ.get('DATABASE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fleet.db'))
INSURANCE_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'insurance')
os.makedirs(INSURANCE_UPLOAD_DIR, exist_ok=True)
TOLL_RECEIPT_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'toll_receipts')
os.makedirs(TOLL_RECEIPT_UPLOAD_DIR, exist_ok=True)

# Vehicle ownership type — every vehicle (and every trip, which copies its vehicle's type at
# creation) is either company-owned ("Own") or hired-in from an outside owner ("Hired"). This
# used to be a 3-way free-text field (Line/Local/Market) with "Line" and "Local" both meaning
# "own fleet" and only "Market" meaning "hired" — collapsed here to the 2 options that actually
# matter for every piece of business logic in this app. The stored value is this stable lowercase
# CODE, never the display word — so renaming the word shown in a dropdown later (e.g. "Own" ->
# "Company Fleet") only ever means editing VEHICLE_TYPE_LABELS below, not touching a single query
# or template comparison. Same pattern this codebase already uses for rate_type (PER_MT/FIXED)
# and item_type (charge/deduction): the code is what's stored and compared, the label is what's
# shown.
VEHICLE_TYPE_OWN = 'own'
VEHICLE_TYPE_HIRED = 'hired'
VEHICLE_TYPE_LABELS = {VEHICLE_TYPE_OWN: 'Own', VEHICLE_TYPE_HIRED: 'Hired'}

# The software product's own name — distinct from `company_name` (the settings.company_name row,
# a per-tenant business name like "Anil Transport" used on invoices/documents, editable per company
# in Settings). This is the platform's own identity: shown on the app's cold-open splash, where
# showing the currently-logged-in company's business name instead would be wrong the same way a
# phone's own lock-screen carrier name isn't a random app's own branding. One constant so a future
# rename only ever means editing this one line, not hunting down every place it appears.
APP_NAME = 'RATH'

# A username IS a mobile number or email — not a separate free-choice name (see app_signup()
# below). Mobile means a real Indian mobile number: 10 digits, first digit 6-9 (TRAI's own
# allocated range — landline/other numbers, and junk like "0000000000", are rejected), same shape
# the OTP login form already expects (templates/app/login_otp.html's own pattern="[0-9]{10}",
# which is only ever reachable by first passing this same check at signup).
USERNAME_MOBILE_RE = re.compile(r'^[6-9]\d{9}$')
USERNAME_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Enforced at Sign Up unconditionally, and at Login for every username EXCEPT this fixed set —
# the real accounts that predate this rule and still have a plain-name username (admin, Priyanka,
# Prakash, Muna), grandfathered in by explicit instruction rather than forced to change their
# login just because a validation rule was added after their accounts already existed. Abinash is
# deliberately NOT here any more — his username was renamed to his real mobile number (9668348621,
# by explicit instruction), so it already satisfies the format on its own merit; keeping him in
# this list too would just be redundant, not wrong, but the list should reflect who actually still
# needs the exemption. Lowercase, matched case-insensitively against the submitted username below
# — usernames themselves are stored COLLATE NOCASE (schema.sql), so this mirrors that same
# case-insensitivity.
LOGIN_USERNAME_FORMAT_EXEMPT = {'admin', 'priyanka', 'prakash', 'muna'}

def _username_login_format_ok(username):
    """True if `username` is allowed to attempt login — either it matches the mobile/email format
    every new Sign Up is required to use, or it's one of the fixed pre-existing exempt accounts
    above. Checked BEFORE the password lookup in login()/app_login(), so a bad-format username is
    rejected with a clear message instead of silently falling through to 'Invalid username or
    password' (which is also still what a right-format-but-wrong-password attempt gets — this
    check is purely about shape, never a substitute for the real credential check)."""
    if (username or '').strip().lower() in LOGIN_USERNAME_FORMAT_EXEMPT:
        return True
    return bool(USERNAME_MOBILE_RE.match(username or '') or USERNAME_EMAIL_RE.match(username or ''))

def get_db():
    # timeout=30: wait up to 30 s for a lock instead of SQLite's default 5 s, so two people saving at the
    # same moment queue up briefly instead of one getting a "database is locked" 500 error.
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")      # safe with WAL (see _ensure_wal_mode); far fewer disk syncs per save
    # Wrapped so a future database swap (see db.py's docstring) only ever means changing what
    # happens in this one function — every one of the ~475 call sites elsewhere that just do
    # conn.execute(sql, params) stay exactly as they are. Zero behavior change today: db.Connection
    # is a pure passthrough on the sqlite backend.
    wrapper = db.Connection(conn, backend='sqlite')
    # Remember every connection a request opens so teardown below can close any a route left open
    # when it raised — otherwise a 500 leaves the SQLite file locked for the next request.
    from flask import has_request_context
    if has_request_context():
        g.setdefault('_open_db_conns', []).append(wrapper)
    return wrapper

# ---- self-applying schema step: payment_allocations.overhead_id ----------------------------------------
# migrate_payment_allocation_overhead.sql is the documented, hand-runnable version of this. It is also
# applied automatically, ONCE, the first time the app starts against a database that lacks the column,
# so shipping the code before the database change can never take the site down (that is exactly what
# happened on the first Render deploy). Safe by construction: a copy of the database file is saved first,
# the whole change runs inside one BEGIN IMMEDIATE transaction (a second gunicorn worker starting at the
# same moment simply waits, then sees the column and does nothing), and it re-checks for the column
# inside the transaction.
def _ensure_overhead_allocation_column(db_path=None):
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        probe = sqlite3.connect(path, timeout=30)
        try:
            has_table = probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_allocations'").fetchone()
            cols = [r[1] for r in probe.execute("PRAGMA table_info(payment_allocations)").fetchall()] if has_table else []
        finally:
            probe.close()
        if not has_table or 'overhead_id' in cols:
            return 'already up to date'
        backup = f"{path}.bak.before_overhead_alloc_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not any(f.startswith(os.path.basename(path) + '.bak.before_overhead_alloc_') for f in os.listdir(os.path.dirname(os.path.abspath(path)) or '.')):
            _src = sqlite3.connect(path, timeout=30)             # SQLite's own online backup: a consistent copy even if something is writing
            _dst = sqlite3.connect(backup)
            try:
                _src.backup(_dst)
            finally:
                _dst.close()
                _src.close()
        conn = sqlite3.connect(path, timeout=60, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(payment_allocations)").fetchall()]
            if 'overhead_id' in cols:                      # another worker got there first
                conn.execute("ROLLBACK")
                return 'already up to date'
            conn.execute("""CREATE TABLE payment_allocations_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id     INTEGER NOT NULL REFERENCES payments(id),
                trip_id        INTEGER REFERENCES trips(id),
                maintenance_id INTEGER REFERENCES maintenance(id),
                overhead_id    INTEGER REFERENCES overheads(id),
                amount         REAL NOT NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT,
                updated_by INTEGER REFERENCES users(id),
                updated_at TEXT,
                company_id            INTEGER NOT NULL DEFAULT 1,
                CHECK ((trip_id IS NOT NULL) + (maintenance_id IS NOT NULL) + (overhead_id IS NOT NULL) = 1)
            )""")
            has_maint = 'maintenance_id' in cols
            conn.execute(f"""INSERT INTO payment_allocations_new (id, payment_id, trip_id, maintenance_id, overhead_id, amount, created_by, created_at, updated_by, updated_at, company_id)
                             SELECT id, payment_id, trip_id, {'maintenance_id' if has_maint else 'NULL'}, NULL, amount, created_by, created_at, updated_by, updated_at,
                                    {'company_id' if 'company_id' in cols else '1'}
                             FROM payment_allocations""")
            before = conn.execute("SELECT COUNT(*), COALESCE(ROUND(SUM(amount),2),0) FROM payment_allocations").fetchone()
            after = conn.execute("SELECT COUNT(*), COALESCE(ROUND(SUM(amount),2),0) FROM payment_allocations_new").fetchone()
            if before != after:                            # never swap tables unless every row came across
                conn.execute("ROLLBACK")
                return f'aborted: row check failed {before} vs {after}'
            conn.execute("DROP TABLE payment_allocations")
            conn.execute("ALTER TABLE payment_allocations_new RENAME TO payment_allocations")
            conn.execute("COMMIT")
            return f'applied (backup: {os.path.basename(backup)}, {after[0]} allocations kept)'
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except Exception as e:                                 # never stop the app from starting
        print(f'[schema] could not apply payment_allocations.overhead_id automatically: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] payment_allocations.overhead_id:', _ensure_overhead_allocation_column(), file=sys.stderr)

# ---- speed: database indexes -----------------------------------------------------------------------------
# The tables had no indexes beyond a few dates, so every "this party's trips" / "this vendor's payments" lookup
# read the whole table (12,000 trips: ~0.4 ms each, times ~9,000 lookups on the Dashboard). These are created with
# IF NOT EXISTS the first time the app starts (instant when they already exist) and are also in schema.sql.
# Partial indexes (WHERE ... IS NOT NULL) are used for allocation columns that are mostly empty.
_PERFORMANCE_INDEXES = [
    ('idx_trips_party', 'trips(party_id)'),
    ('idx_trips_vehicle', 'trips(vehicle_id)'),
    ('idx_trips_date', 'trips(date)'),
    ('idx_trips_owner_vendor', 'trips(owner_vendor_id)'),
    ('idx_trips_fuel_vendor', 'trips(fuel_vendor_id)'),
    ('idx_trips_adv_vendor', 'trips(driver_adv_vendor_id)'),
    ('idx_trips_misc_vendor', 'trips(misc_vendor_id)'),
    ('idx_trips_lr', 'trips(lr_number)'),
    ('idx_payments_party', 'payments(party_id)'),
    ('idx_payments_vendor', 'payments(vendor_id)'),
    ('idx_payments_date', 'payments(date)'),
    ('idx_payalloc_payment', 'payment_allocations(payment_id)'),
    ('idx_payalloc_trip', 'payment_allocations(trip_id)'),
    ('idx_payalloc_maint', 'payment_allocations(maintenance_id) WHERE maintenance_id IS NOT NULL'),
    ('idx_payalloc_overhead', 'payment_allocations(overhead_id) WHERE overhead_id IS NOT NULL'),
    ('idx_maint_vendor', 'maintenance(vendor_id)'),
    ('idx_maint_vehicle', 'maintenance(vehicle_id)'),
    ('idx_maint_date', 'maintenance(date)'),
    ('idx_maint_category', 'maintenance(category)'),
    ('idx_overheads_date', 'overheads(date)'),
    ('idx_invoice_items_trip', 'invoice_items(trip_id)'),
    ('idx_invoice_items_vendor', 'invoice_items(vendor_id)'),
    ('idx_invoices_trip', 'invoices(trip_id)'),
    ('idx_ibt_batch', 'invoice_batch_trips(invoice_batch_id)'),
    ('idx_ibt_trip', 'invoice_batch_trips(trip_id)'),
    ('idx_ibi_batch', 'invoice_batch_items(invoice_batch_id)'),
    ('idx_ibi_vendor', 'invoice_batch_items(vendor_id)'),
    ('idx_salaries_employee_id', 'salaries(employee_id)'),
    ('idx_salaries_date', 'salaries(date)'),
    ('idx_vendors_linked_party', 'vendors(linked_party_id)'),
    ('idx_toll_trip', 'toll_entries(trip_id)'),
    ('idx_urea_vehicle', 'urea_transactions(vehicle_id)'),
    ('idx_batteries_vehicle', 'batteries(vehicle_id)'),
    ('idx_insurance_vehicle', 'insurance_policies(vehicle_id)'),
    ('idx_advances_employee_nocase', 'advances(employee COLLATE NOCASE)'),
    ('idx_salaries_employee_nocase', 'salaries(employee COLLATE NOCASE)'),
]

def _ensure_performance_indexes(db_path=None):
    path = db_path or DB
    made = 0
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
            for name, definition in _PERFORMANCE_INDEXES:
                table = definition.split('(')[0]
                if name in existing or table not in tables:
                    continue
                try:
                    conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
                    made += 1
                except sqlite3.OperationalError:
                    pass                      # e.g. a column an older database does not have — skip, never fail startup
            if made:
                conn.execute("ANALYZE")
            conn.commit()
        finally:
            conn.close()
        return f'{made} created' if made else 'already in place'
    except Exception as e:
        print(f'[schema] could not create performance indexes: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] performance indexes:', _ensure_performance_indexes(), file=sys.stderr)

def _ensure_wal_mode(db_path=None):
    """Switch the database to WAL journaling (a persistent setting of the file). In the default mode a long
    read (a 0.5 s dashboard) blocks every writer and writers block readers, so under a dozen people the
    saves started failing with 'database is locked'. In WAL mode readers never block the writer and the
    writer never blocks readers. Note for backups: the database is then the .db file PLUS its -wal file; copy it
    with SQLite's backup (sqlite3 file '.backup x') or after the app has stopped."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != 'wal':
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            return f'journal mode {mode}'
        finally:
            conn.close()
    except Exception as e:
        print(f'[schema] could not switch to WAL: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] ', _ensure_wal_mode(), file=sys.stderr)

# ---------------------------------------------------------------------------------------------------------------
# Security: server-side one-time codes, login throttling, and a secret key that is not in the source code.
# ---------------------------------------------------------------------------------------------------------------
def _ensure_auth_tables(db_path=None):
    """login_otps / auth_attempts — created once, never touches existing tables (CREATE ... IF NOT EXISTS), so it is
    safe to run on every start, from several workers at once."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS login_otps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phone TEXT NOT NULL,
                otp_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                used INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                company_id INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_login_otps_user ON login_otps(user_id, used);
            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                at REAL NOT NULL,
                company_id INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_auth_attempts_lookup ON auth_attempts(kind, key, at);
            """)
            conn.commit()
        finally:
            conn.close()
        return 'ready'
    except Exception as e:
        print(f'[schema] could not create auth tables: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] auth tables:', _ensure_auth_tables(), file=sys.stderr)

def _ensure_secret_key(db_path=None):
    """Session cookies are signed with app.secret_key — anyone who knows it can forge a login. Use the SECRET_KEY
    environment variable when set (best); otherwise generate a random key ONCE and keep it in the database settings, so it
    is private to this installation and shared by every gunicorn worker. The old fixed key that used to be written in
    this file is never used any more."""
    import secrets
    if os.environ.get('SECRET_KEY'):
        return 'from SECRET_KEY'
    path = db_path or DB
    try:
        if os.path.exists(path):
            conn = sqlite3.connect(path, timeout=60)
            try:
                conn.execute("INSERT OR IGNORE INTO settings (key, value, company_id) VALUES ('_auto_secret_key', ?, 1)", (secrets.token_hex(32),))
                conn.commit()
                row = conn.execute("SELECT value FROM settings WHERE key='_auto_secret_key'").fetchone()
            finally:
                conn.close()
            if row and row[0]:
                app.secret_key = row[0]
                return 'generated once and kept in the database (set SECRET_KEY to use your own)'
    except Exception as e:
        print(f'[security] could not keep a generated secret key: {e!r}', file=sys.stderr)
    app.secret_key = secrets.token_hex(32)
    return 'random for this process only (no database yet)'

print('[security] secret key:', _ensure_secret_key(), file=sys.stderr)

def _ensure_single_admin_index(db_path=None):
    """Database-level guarantee behind the "only one active Administrator" rule: a partial unique index, so even a bug or
    two simultaneous requests cannot create a second one. Skipped (with a warning, never a crash) if the data already
    has more than one active Administrator — resolve that first, the index is created at the next start."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_administrator ON users(company_id)
                            WHERE LOWER(TRIM(COALESCE(role,'')))='administrator' AND COALESCE(status,'Active')!='Inactive'""")
            conn.commit()
            return 'enforced'
        except sqlite3.IntegrityError:
            return 'SKIPPED — more than one active Administrator already exists; make all but one a Manager, then restart'
        finally:
            conn.close()
    except Exception as e:
        print(f'[schema] could not create the single-Administrator index: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[security] one active Administrator:', _ensure_single_admin_index(), file=sys.stderr)

_USER_EXTRA_COLUMNS = (
    ('hide_trip_amounts', 'INTEGER NOT NULL DEFAULT 0'),   # Trips (View only): stars instead of rates, bills and payments
    ('salary_view_ids', 'TEXT'),                           # Salaries (View only): salary of OTHER employees this user may also see: '*' = everyone, or '3,5,9'
)


def _ensure_user_extra_columns(db_path=None):
    """The per-user switches above, added by plain ALTER TABLE ... ADD COLUMN (default 0 / empty) — touches no existing row and changes
    nobody's access. Each column is skipped when it is already there, so this is safe on every start and with two workers starting together."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if not cols:
                return 'no users table yet'
            added = []
            for name, ddl in _USER_EXTRA_COLUMNS:
                if name in cols:
                    continue
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
                    conn.commit()
                    added.append(name)
                except sqlite3.OperationalError:
                    # a second worker added it between our check and our ALTER
                    if name not in [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]:
                        raise
            msg = ('added ' + ', '.join(added)) if added else 'already up to date'
            legacy = [c for c in ('salary_view_own', 'salary_view_drivers', 'employee_id') if c in cols]
            if legacy:
                # interim designs (own / drivers tick boxes, an explicit employee link) are replaced by salary_view_ids and by matching the login's
                # mobile number to the employee's: carry any "drivers" setting over, then drop those columns — after an online copy of the database,
                # inside one transaction.
                import datetime as _dt
                bak_dir = os.path.join(os.path.dirname(os.path.abspath(path)), 'db_backups')       # git-ignored folder: a database copy must never be committed
                os.makedirs(bak_dir, exist_ok=True)
                bak = os.path.join(bak_dir, f"{os.path.basename(path)}.before_salary_scope_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}")
                dst = sqlite3.connect(bak)
                try:
                    conn.backup(dst)
                finally:
                    dst.close()
                conn.execute('BEGIN IMMEDIATE')
                try:
                    if 'salary_view_drivers' in cols:
                        conn.execute("""UPDATE users SET salary_view_ids = (SELECT group_concat(id) FROM employees WHERE LOWER(TRIM(COALESCE(type,'')))='driver')
                                        WHERE salary_view_drivers=1 AND salary_view_ids IS NULL""")
                    for c in legacy:
                        conn.execute(f'ALTER TABLE users DROP COLUMN {c}')
                    conn.commit()
                    msg += f'; interim salary columns converted and dropped (copy: db_backups/{os.path.basename(bak)})'
                except Exception:
                    conn.rollback()
                    raise
            return msg
        finally:
            conn.close()
    except Exception as e:
        print(f'[schema] could not add the per-user switch columns: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] users extra columns:', _ensure_user_extra_columns(), file=sys.stderr)

_BILLING_EXTRA_COLUMNS = (                                  # (table, column, definition) — exactly what migrate_guarantee_qty.sql / migrate_invoice_batch_items_vendor.sql add
    ('trips', 'guarantee_qty', 'REAL DEFAULT 0'),                                # Minimum Guarantee Weight; 0 = none, so every existing trip bills exactly as before
    ('invoice_batch_items', 'vendor_id', 'INTEGER REFERENCES vendors(id)'),      # which vendor an invoice's extra line belongs to; empty for existing lines
)
_BILLING_CHARGE_EXCLUSIONS_DDL = (                          # same as migrate_invoice_charge_exclusions.sql
    """CREATE TABLE IF NOT EXISTS invoice_batch_charge_exclusions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_batch_id  INTEGER NOT NULL REFERENCES invoice_batches(id),
        trip_id           INTEGER NOT NULL REFERENCES trips(id),
        charge_key        TEXT NOT NULL,
        company_id        INTEGER NOT NULL DEFAULT 1,
        created_by        INTEGER REFERENCES users(id),
        created_at        TEXT,
        UNIQUE(invoice_batch_id, trip_id, charge_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_invoice_batch_charge_exclusions_batch ON invoice_batch_charge_exclusions(invoice_batch_id)",
)


def _ensure_billing_columns(db_path=None):
    """Invoices read trips.guarantee_qty, invoice_batch_items.vendor_id and the invoice_batch_charge_exclusions table. A database that was never given
    those migrations (an older copy running on the server) answered every invoice generate / preview with "Internal Server Error". They are purely
    additive — a new column with an empty default, a new empty table — so the app adds any that are missing itself, on start-up, and touches no
    existing row. Each step is skipped when already present, so it is safe on every start and with two workers starting together."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            done = []
            for table, column, ddl in _BILLING_EXTRA_COLUMNS:
                if table not in tables:
                    continue
                if column in [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]:
                    continue
                try:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
                    conn.commit()
                    done.append(f'{table}.{column}')
                except sqlite3.OperationalError:
                    if column not in [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]:      # a second worker may have added it first
                        raise
            if 'invoice_batches' in tables and 'trips' in tables and 'invoice_batch_charge_exclusions' not in tables:
                for stmt in _BILLING_CHARGE_EXCLUSIONS_DDL:
                    conn.execute(stmt)
                conn.commit()
                done.append('invoice_batch_charge_exclusions')
            return ('added ' + ', '.join(done)) if done else 'already up to date'
        finally:
            conn.close()
    except Exception as e:
        print(f'[schema] could not add the invoice columns: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[schema] invoice columns:', _ensure_billing_columns(), file=sys.stderr)

def _remove_twilio_settings(db_path=None):
    """Twilio is no longer used. Delete any credentials saved for it (account SID, auth token, from number, provider choice)
    so no secret sits unused in the database. Safe to run on every start."""
    path = db_path or DB
    try:
        if not os.path.exists(path):
            return 'no database file'
        conn = sqlite3.connect(path, timeout=60)
        try:
            n = conn.execute("DELETE FROM settings WHERE key IN ('twilio_account_sid','twilio_auth_token','twilio_from_number','sms_provider')").rowcount
            conn.commit()
            return f'removed {n} saved Twilio setting(s)' if n else 'none saved'
        finally:
            conn.close()
    except Exception as e:
        print(f'[security] could not remove Twilio settings: {e!r}', file=sys.stderr)
        return f'failed: {e!r}'

print('[security] Twilio settings:', _remove_twilio_settings(), file=sys.stderr)

# How many failures / requests are allowed in a window: (limit, seconds)
_AUTH_LIMITS = {'backup_fail_ip': (5, 15 * 60), 'login_fail_user': (5, 15 * 60), 'login_fail_ip': (30, 15 * 60),
                'otp_send_phone': (3, 15 * 60), 'otp_send_ip': (10, 60 * 60)}

def _client_ip():
    return (request.remote_addr or 'unknown')[:64]

def _auth_wait_seconds(conn, kind, key):
    """0 if allowed, else how many seconds until the oldest counted attempt leaves the window."""
    limit, window = _AUTH_LIMITS[kind]
    now = datetime.datetime.now().timestamp()
    rows = conn.execute("SELECT at FROM auth_attempts WHERE kind=? AND key=? AND at>? ORDER BY at", (kind, key, now - window)).fetchall()
    if len(rows) < limit:
        return 0
    return max(1, int(rows[len(rows) - limit]['at'] + window - now))

def _auth_record(conn, kind, key):
    now = datetime.datetime.now().timestamp()
    conn.execute("INSERT INTO auth_attempts (kind, key, at) VALUES (?,?,?)", (kind, key, now))
    if int(now) % 50 == 0:   # housekeeping: forget anything older than a day
        conn.execute("DELETE FROM auth_attempts WHERE at<?", (now - 86400,))

def _auth_clear(conn, kind, key):
    conn.execute("DELETE FROM auth_attempts WHERE kind=? AND key=?", (kind, key))

def _wait_text(seconds):
    m = (seconds + 59) // 60
    return f'{m} minute{"s" if m != 1 else ""}'

def _login_throttle_message(conn, username):
    """Message to show instead of checking the password when this username or this address has failed too often."""
    w = max(_auth_wait_seconds(conn, 'login_fail_user', username.lower()), _auth_wait_seconds(conn, 'login_fail_ip', _client_ip()))
    return f'Too many failed sign-in attempts. Please try again in {_wait_text(w)}.' if w else None

def _login_failed(conn, username):
    _auth_record(conn, 'login_fail_user', username.lower())
    _auth_record(conn, 'login_fail_ip', _client_ip())
    conn.commit()

def _otp_hash(otp_id, code):
    import hmac, hashlib
    return hmac.new(app.secret_key.encode(), f'{otp_id}:{code}'.encode(), hashlib.sha256).hexdigest()

OTP_MAX_WRONG = 5

def _otp_issue(conn, user, phone):
    """Creates a one-time code for `user` and stores only its keyed hash, server side (a cookie holding the hash
    could be read and the 6 digits worked out offline). Returns (code, row_id). Commits."""
    import secrets
    now = datetime.datetime.now().timestamp()
    conn.execute("UPDATE login_otps SET used=1 WHERE user_id=? AND used=0", (user['id'],))
    cur = conn.execute("INSERT INTO login_otps (user_id, phone, otp_hash, expires_at, created_at) VALUES (?,?,?,?,?)",
                       (user['id'], phone, '', now + 300, now))
    otp_id = cur.lastrowid
    code = f"{secrets.randbelow(1000000):06d}"
    conn.execute("UPDATE login_otps SET otp_hash=? WHERE id=?", (_otp_hash(otp_id, code), otp_id))
    conn.commit()
    return code, otp_id

def _otp_check(conn, otp_id, phone, code):
    """(user_id, None) when the code is right; otherwise (None, message). At most OTP_MAX_WRONG wrong tries per code."""
    import hmac
    row = conn.execute("SELECT * FROM login_otps WHERE id=?", (otp_id,)).fetchone() if otp_id else None
    if not row or row['used'] or row['phone'] != phone:
        return None, 'Session expired. Please request a new OTP.'
    if datetime.datetime.now().timestamp() > row['expires_at']:
        return None, 'OTP expired. Please request a new one.'
    # count the try first (atomically) so parallel guesses can't beat the limit
    cur = conn.execute("UPDATE login_otps SET attempts=attempts+1 WHERE id=? AND attempts<?", (otp_id, OTP_MAX_WRONG))
    conn.commit()
    if cur.rowcount == 0:
        return None, 'Too many incorrect codes. Please request a new OTP.'
    if not hmac.compare_digest(row['otp_hash'], _otp_hash(otp_id, code)):
        return None, 'Incorrect OTP. Please try again.'
    conn.execute("UPDATE login_otps SET used=1 WHERE id=?", (otp_id,))
    conn.commit()
    return row['user_id'], None

@app.errorhandler(ValueError)
@app.errorhandler(OverflowError)
@app.errorhandler(sqlite3.IntegrityError)
def _bad_form_input(e):
    """A number, date or required field typed into a form that could not be understood (e.g. 'abc'
    in an amount, a date like 2026-13-45, a missing required value) used to surface as a bare
    500 'Internal Server Error'. It is the user's input that is wrong, not the server — answer 400
    with a readable message instead (JSON for the mobile app's fetch() calls, a small page for
    normal form posts). Nothing was saved: the request's database work is rolled back when the
    connection is closed at teardown. The original error is still logged for diagnosis."""
    app.logger.warning('Rejected bad form input on %s %s: %r', request.method, request.path, e)
    message = "That couldn't be saved — a number, date or required field in the form isn't valid. Nothing was changed; please check the values and try again."
    wants_html = 'text/html' in (request.headers.get('Accept') or '')
    if request.path.startswith('/app/') and not wants_html:
        return jsonify({'ok': False, 'error': message}), 400
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Could not save</title><body style="font-family:system-ui,sans-serif;max-width:34rem;margin:12vh auto;padding:0 1rem;line-height:1.5">'
            f'<h2>Could not save</h2><p>{message}</p><p><a href="javascript:history.back()">&larr; Go back</a></p></body>', 400)

_NUMERIC_FIELD_HINT = re.compile(r'(amount|rate|qty|quantity|price|charge|cost|paid|advance|balance|salary|liters|_km|km_|tds|gst|premium|capacity|'
                                 r'voltage|health|temp|commission|fees|damage|brokerage|expense|toll|urea|parking|puncture|fine|labour|weight|'
                                 r'shortage|deduction|received|fixed|owner_rate|limit|odometer|mileage|value)', re.I)

@app.before_request
def _reject_non_finite_numbers():
    """'nan', 'inf', '1e999' or an absurdly large number typed into any amount-like field would be
    stored as NULL / infinity / nonsense and then poison every total built from that column. Catch
    them once, here, for every form in the app — the individual routes' own float() calls only
    guard against text, not against these. Only fields whose NAME looks numeric are checked, so a
    vendor called 'Inf' or a remark that says 'nan' is never affected."""
    if request.method != 'POST' or not request.form:
        return None
    for key in request.form:
        if not _NUMERIC_FIELD_HINT.search(key):
            continue
        for raw in request.form.getlist(key):
            t = (raw or '').strip().replace(',', '')
            if not t or t[0] not in '+-.0123456789iInN':
                continue
            try:
                v = float(t)
            except ValueError:
                continue
            if v != v or v in (float('inf'), float('-inf')) or abs(v) > 1e12:
                return _bad_form_input(ValueError(f'non-finite or absurd number in field {key!r}'))
    return None

@app.teardown_request
def _close_leaked_db_conns(exc):
    for c in g.pop('_open_db_conns', []):
        try:
            c.close()
        except Exception:
            pass

@app.context_processor
def inject_vehicle_type_labels():
    """Makes the own/hired codes + their display labels available to every template (dropdown
    options, badges, read-only display text) without every route that touches a vehicle/trip
    type needing to pass them individually. See the VEHICLE_TYPE_* comment near the top of this
    file for why the stored value and the shown word are kept separate."""
    return {'VEHICLE_TYPE_OWN': VEHICLE_TYPE_OWN, 'VEHICLE_TYPE_HIRED': VEHICLE_TYPE_HIRED,
            'VEHICLE_TYPE_LABELS': VEHICLE_TYPE_LABELS}

@app.context_processor
def inject_asset_version():
    """Makes `asset_v(filename)` available to every template — appends that static file's own
    mtime as a `?v=` cache-busting query param, so a CSS/JS fix actually shows up the next time a
    browser (especially a phone that's had the app open a while, or a PWA that rarely re-fetches)
    loads the page, instead of silently keeping whatever copy of app.css it fetched once and never
    re-requested. Falls back to the plain URL if the file can't be stat'd (e.g. running somewhere
    static/ isn't on local disk) rather than breaking the page over a cosmetic version tag."""
    def asset_v(filename):
        url = url_for('static', filename=filename)
        try:
            mtime = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
            return f'{url}?v={mtime}'
        except OSError:
            return url
    return {'asset_v': asset_v}

@app.context_processor
def inject_site_logo():
    """Makes the uploaded company logo available to every template as `site_logo_path` (used for
    the browser tab favicon in base.html) without every single route needing to fetch and pass it
    itself — same file Settings > Company Profile already manages, so it updates automatically
    whenever that's changed."""
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='logo_path'").fetchone()
        conn.close()
        return {'site_logo_path': row['value'] if row and row['value'] else None}
    except sqlite3.Error:
        return {'site_logo_path': None}

def _vehicle_active_idle_days(conn, vehicle_id, registration_date, date_from, date_to):
    """Active days = union of calendar days spanned by each trip's start..end_date (a trip stays
    'active' from when it's entered until it's explicitly ended, not just on its start date).
    Idle days = the remainder of the window, anchored no earlier than the vehicle's registration
    date so a newly bought vehicle isn't counted idle for days before it existed."""
    anchor = registration_date if registration_date and registration_date > date_from else date_from
    if anchor > date_to:
        return 0, 0, 0
    total_days_v = (datetime.datetime.strptime(date_to, '%Y-%m-%d').date() -
                     datetime.datetime.strptime(anchor, '%Y-%m-%d').date()).days + 1
    trip_rows = conn.execute("""SELECT date, end_date FROM trips WHERE vehicle_id=? AND date<=?
                                AND (end_date IS NULL OR end_date>=?) ORDER BY date""",
                              (vehicle_id, date_to, anchor)).fetchall()
    active_dates = set()
    for t in trip_rows:
        start = t['date']
        end = t['end_date'] or t['date']
        if end < start:
            end = start
        s = max(start, anchor)
        e = min(end, date_to)
        if s > e:
            continue
        d = datetime.datetime.strptime(s, '%Y-%m-%d').date()
        e_d = datetime.datetime.strptime(e, '%Y-%m-%d').date()
        while d <= e_d:
            active_dates.add(d.isoformat())
            d += datetime.timedelta(days=1)
    active_days = len(active_dates)
    idle_days = max(total_days_v - active_days, 0)
    return active_days, idle_days, total_days_v

def _toll_by_trip(conn, trip_ids):
    """Real Toll Management amount linked to each trip_id, for the (usually few) trips that have
    one — a BOSS/FASTag import almost never carries a trip number, so most trips won't appear
    here. Callers needing a PER-TRIP figure (an invoice line, a customer's profit calc) should
    fall back to that trip's own manual `toll` field when its id isn't in the returned dict,
    rather than treating an unlinked trip as zero toll cost."""
    trip_ids = [t for t in trip_ids if t]
    if not trip_ids:
        return {}
    placeholders = ','.join('?' * len(trip_ids))
    rows = conn.execute(f"SELECT trip_id, SUM(amount) as total FROM toll_entries WHERE trip_id IN ({placeholders}) GROUP BY trip_id",
                         trip_ids).fetchall()
    return {r['trip_id']: r['total'] for r in rows}

def _trip_toll(t, toll_map):
    """Real per-trip toll cost: the linked Toll Management total if this trip has one, else its
    own manual estimate — never both, so the same real-world toll is never counted twice."""
    linked = toll_map.get(t['id'])
    return linked if linked is not None else (t['toll'] or 0)

def _period_financials(conn, date_from, date_to, vehicle_no=''):
    """Revenue/expense/profit breakdown for a date range, using the same charge
    columns as dashboard()/monthly_summary() so the numbers agree app-wide.
    vehicle_no is optional (default '' = all vehicles, every existing caller is
    unaffected) and — when given — filters the same two query sets dashboard()'s
    own vehicle_f filter does (trips, maintenance); overheads/salaries have no
    vehicle column and stay unfiltered there too, so this can never disagree with
    the website's own per-vehicle dashboard filter."""
    if vehicle_no:
        trips = conn.execute("""SELECT t.* FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                WHERE t.date>=? AND t.date<=? AND v.vehicle_no=?""",
                              (date_from, date_to, vehicle_no)).fetchall()
    else:
        trips = conn.execute("SELECT * FROM trips WHERE date>=? AND date<=?", (date_from, date_to)).fetchall()
    revenue = sum(t['billed_amount'] or 0 for t in trips)
    # Fuel / driver advance recorded on a HIRED trip is already part of the owner's contracted
    # freight (owner_cost below) — counting it again would charge the same money twice. So, exactly
    # like the Dashboard, they only count for own-vehicle trips. A hired trip's profit is therefore
    # always: billed amount - owner freight (- its other trip charges).
    fuel = sum(t['fuel_amount'] or 0 for t in trips if t['type'] != VEHICLE_TYPE_HIRED)
    driver_adv = sum(t['driver_adv_amount'] or 0 for t in trips if t['type'] != VEHICLE_TYPE_HIRED)
    parking = sum(t['parking'] or 0 for t in trips)
    misc = sum((t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) +
               (t['fine'] or 0) + (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) +
               (t['loading_expense'] or 0) + (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) +
               (t['weighbridge_charges'] or 0) + (t['other_expense'] or 0) + (t['permit_charges'] or 0) for t in trips)
    owner_cost = sum((t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
                      for t in trips if t['type'] == VEHICLE_TYPE_HIRED)
    if vehicle_no:
        maint_all = conn.execute("""SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id
                                    WHERE m.date>=? AND m.date<=? AND v.vehicle_no=?""", (date_from, date_to, vehicle_no)).fetchone()[0]
        # Toll Management (Maintenance > Toll) is the source of truth for period-level toll cost now —
        # 'maint' below means "maintenance excluding toll" so a breakdown chart showing both a
        # 'Maintenance' slice and a 'Toll' slice never double-counts the same rupee in both.
        toll = conn.execute("""SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id
                               WHERE m.category='Toll' AND m.date>=? AND m.date<=? AND v.vehicle_no=?""",
                            (date_from, date_to, vehicle_no)).fetchone()[0]
    else:
        maint_all = conn.execute("SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
        toll = conn.execute("SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE category='Toll' AND date>=? AND date<=?",
                            (date_from, date_to)).fetchone()[0]
    maint = maint_all - toll
    overheads = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    salaries = conn.execute("SELECT COALESCE(SUM(amount),0) FROM salaries WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    total_expenses = fuel + driver_adv + toll + parking + misc + owner_cost + maint + overheads + salaries
    net_profit = revenue - total_expenses
    margin = round(net_profit / revenue * 100, 2) if revenue else 0
    party_ids = set(t['party_id'] for t in trips if t['party_id'])
    return {
        'trips': trips, 'trip_count': len(trips), 'revenue': revenue, 'fuel': fuel, 'driver_adv': driver_adv,
        'toll': toll, 'parking': parking, 'misc': misc, 'owner_cost': owner_cost, 'maint': maint,
        'overheads': overheads, 'salaries': salaries, 'total_expenses': total_expenses,
        'net_profit': net_profit, 'margin': margin, 'party_ids': party_ids,
    }

def _pct_growth(curr, prev):
    if prev:
        return round((curr - prev) / abs(prev) * 100, 2)
    return None

def _shift_period_back(date_from, date_to):
    """Previous period of the same length, immediately before date_from."""
    d_from = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
    d_to = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    length = (d_to - d_from).days + 1
    prev_to = d_from - datetime.timedelta(days=1)
    prev_from = prev_to - datetime.timedelta(days=length - 1)
    return prev_from.isoformat(), prev_to.isoformat()

def _shift_period_year(date_from, date_to):
    def back_a_year(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:
            return d - datetime.timedelta(days=365)
    d_from = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
    d_to = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    return back_a_year(d_from).isoformat(), back_a_year(d_to).isoformat()

def _month_bounds(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1).isoformat(), datetime.date(year, month, last_day).isoformat()

def _normalized_date_range(date_from, date_to):
    """Every 'From Date'/'To Date' filter in the app — Trips, Tyres, Battery, Urea, Maintenance,
    Expenses, Salary, Employee Ledger, and the Select Period custom-range used by Dashboard/Toll/
    Route Analytics/Fleet Utilization/Performance/Business Performance — had no guard against a
    From Date entered after the To Date; that silently produced a WHERE date>=from AND date<=to
    with no rows possible, not an error. This is the one shared fix: if both are real YYYY-MM-DD
    values and From is after To, swap them so the range is always valid, whichever end the two
    values actually came in on (typed by hand, or a URL/query-string edited directly rather than
    picked through the calendar). Deliberately silent (a swap, not a rejected request) — same
    graceful-default philosophy every other filter in this app already uses (an unrecognized
    period falls back to a sane default rather than erroring); leaves both alone if either is
    missing or not a plain date string, no different from today's behavior in that case."""
    if date_from and date_to and re.match(r'^\d{4}-\d{2}-\d{2}$', date_from) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_to) and date_from > date_to:
        return date_to, date_from
    return date_from, date_to

def _quarter_bounds(d):
    q_start_month = (d.month - 1) // 3 * 3 + 1
    start = datetime.date(d.year, q_start_month, 1)
    end_month = q_start_month + 2
    last_day = calendar.monthrange(d.year, end_month)[1]
    end = datetime.date(d.year, end_month, last_day)
    return start.isoformat(), end.isoformat()

def _paginate(page_raw, per_page_raw, total_count, per_page_options=(10, 25, 50, 100), default_per_page=50):
    """Shared page/per_page/total_pages math so every list page paginates identically."""
    try:
        per_page = int(per_page_raw) if per_page_raw else default_per_page
    except (TypeError, ValueError):
        per_page = default_per_page
    if per_page not in per_page_options:
        per_page = default_per_page
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    try:
        page = max(1, int(page_raw or 1))
    except (TypeError, ValueError):
        page = 1
    page = min(page, total_pages)
    return page, per_page, total_pages

def _page_tokens(page, total_pages):
    """Windowed page-number list with ellipses, e.g. [1, '...', 6, 7, 8, '...', 42]."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    tokens = [1]
    if page > 3:
        tokens.append('...')
    for p in range(max(2, page - 1), min(total_pages, page + 1) + 1):
        tokens.append(p)
    if page < total_pages - 2:
        tokens.append('...')
    if total_pages not in tokens:
        tokens.append(total_pages)
    return tokens

_LOCATION_ALIASES = {'RKL': 'ROURKELA', 'BBSR': 'BHUBANESWAR', 'SAGJOR': 'SAGJORE'}
def _clean_loc(raw):
    if not raw:
        return ''
    first_word = raw.split(' ')[0] if ' ' in raw else raw
    cleaned = first_word.upper().replace(',', '').strip()
    return _LOCATION_ALIASES.get(cleaned, cleaned)

# RC body-type descriptions come back verbose ("ARTICULATED TRAILER", "OPEN BODY TRUCK") — this
# picks out just the recognizable body-type word so the Vehicles list shows "Trailer"/"Truck",
# not the full RC string. Checked longest-keyword-first so "TRAILER" doesn't win over a more
# specific match if one's ever added later.
_BODY_TYPE_KEYWORDS = ['ARTICULATED TRAILER', 'TRAILER', 'TANKER', 'CONTAINER', 'TIPPER',
                       'FLATBED', 'PICKUP', 'TRUCK', 'VAN', 'BUS', 'CAR',
                       'THREE WHEELER', '3 WHEELER', 'AUTO RICKSHAW', 'TEMPO', 'E-RICKSHAW',
                       'TWO WHEELER', '2 WHEELER', 'MOTORCYCLE', 'SCOOTER', 'BIKE']
# Keywords that don't map cleanly onto their own title-cased text collapse to one canonical label.
_BODY_TYPE_ALIASES = {'ARTICULATED TRAILER': 'Trailer', 'TWO WHEELER': '2 Wheeler', '2 WHEELER': '2 Wheeler',
                       'MOTORCYCLE': '2 Wheeler', 'SCOOTER': '2 Wheeler', 'BIKE': '2 Wheeler',
                       'THREE WHEELER': '3 Wheeler', '3 WHEELER': '3 Wheeler', 'AUTO RICKSHAW': '3 Wheeler',
                       'TEMPO': '3 Wheeler', 'E-RICKSHAW': '3 Wheeler'}
def _short_body_type(raw):
    if not raw:
        return ''
    raw_upper = raw.upper()
    for kw in _BODY_TYPE_KEYWORDS:
        if kw in raw_upper:
            return _BODY_TYPE_ALIASES.get(kw, kw.title())
    # No known keyword matched — fall back to the raw value itself, title-cased, rather than
    # hiding it entirely (covers a manual entry that used different wording).
    return raw.strip().title()

# One fixed color per recognized body type, drawn from the same palette already used for
# category colors elsewhere in the app (Expense Breakdown, etc.) so it reads as one system.
# Anything not in this map (an unrecognized manual entry) falls back to a neutral grey.
_BODY_TYPE_COLORS = {
    'Trailer': '#2a78d6', 'Truck': '#1a9c5b', 'Tanker': '#eda100', 'Container': '#4a3aa7',
    'Tipper': '#e34948', 'Flatbed': '#7a5ad6', 'Pickup': '#eb6834', 'Van': '#0891b2', 'Bus': '#c026d3',
    'Car': '#16a34a', '2 Wheeler': '#f59e0b', '3 Wheeler': '#d97706',
}
def _body_type_color(short):
    return _BODY_TYPE_COLORS.get(short, '#64748b')

# One representative emoji per recognized body type — used only on the Vehicles list/detail
# pages, where the icon stands in for one specific vehicle's actual type, not as a generic
# "vehicles" section icon (those elsewhere in the app stay the plain truck emoji on purpose).
_BODY_TYPE_ICONS = {
    'Trailer': '\U0001F69B', 'Truck': '\U0001F69A', 'Tanker': '\U0001F69B', 'Container': '\U0001F69B',
    'Tipper': '\U0001F69B', 'Flatbed': '\U0001F69A', 'Pickup': '\U0001F6FB', 'Van': '\U0001F690',
    'Bus': '\U0001F68C', 'Car': '\U0001F697', '2 Wheeler': '\U0001F3CD️', '3 Wheeler': '\U0001F6FA',
}
def _body_type_icon(short):
    return _BODY_TYPE_ICONS.get(short, '\U0001F69A')

# Body type -> wheeler count, for the Maintenance > Tyres "Vehicle Tyre Layout" diagram, so it
# shows the right number of tyre positions instead of always the same fixed 6-wheeler layout.
# This is a best-effort default keyed off body_type (RC-synced or manually set on the vehicle) --
# not a precise per-vehicle wheel count (a real "Truck" could be a 6/10/12/14-wheeler in
# reality), but body_type is the only signal already on the vehicle record, and most of this
# fleet's body_type is unset anyway, in which case DEFAULT_WHEELER_COUNT (6) applies, matching
# the layout every vehicle showed before this existed.
_BODY_TYPE_WHEELER_COUNT = {
    '2 Wheeler': 2, '3 Wheeler': 3, 'Car': 4, 'Pickup': 4, 'Van': 4,
    'Truck': 6, 'Tanker': 6, 'Container': 6, 'Tipper': 6, 'Flatbed': 6, 'Trailer': 6, 'Bus': 6,
}
DEFAULT_WHEELER_COUNT = 6

def _vehicle_wheeler_count(body_type_raw):
    return _BODY_TYPE_WHEELER_COUNT.get(_short_body_type(body_type_raw), DEFAULT_WHEELER_COUNT)

def _tyre_positions_for_wheeler_count(n):
    """Front axle is a single wheel (no left/right) below 4 wheels -- a 2-wheeler is F/R, a
    3-wheeler is F + a rear pair. From 4 wheels up the front axle is a pair (FL/FR) and the
    remaining wheels fill rear axle pairs (RL1/RR1, RL2/RR2, ...); an odd leftover becomes a
    lone rear-center position (RCn) rather than a fabricated left/right split. n=6 reproduces
    today's exact fixed layout, so every vehicle without a distinguishing body_type (the
    majority right now) looks exactly like it did before this existed."""
    if not n or n < 2:
        n = DEFAULT_WHEELER_COUNT
    if n == 2:
        return ['F', 'R', 'Spare']
    if n == 3:
        return ['F', 'RL1', 'RR1', 'Spare']
    positions = ['FL', 'FR']
    remaining = n - 2
    row = 1
    while remaining > 0:
        if remaining >= 2:
            positions += [f'RL{row}', f'RR{row}']
            remaining -= 2
        else:
            positions.append(f'RC{row}')
            remaining -= 1
        row += 1
    positions.append('Spare')
    return positions

def get_or_create_party(conn, name):
    if not name or not name.strip():
        return None
    name = name.strip()
    row = conn.execute("SELECT id FROM parties WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("INSERT INTO parties (name, created_by, created_at) VALUES (?,?,?)",
                        (name, session.get('user_id'), now))
    party_id = cur.lastrowid
    # A vendor record with the same name may already exist (e.g. created first via
    # Maintenance/Fuel). Link it here too, so both sides share one combined ledger
    # instead of the org getting a second, disconnected balance.
    vendor_match = conn.execute(
        "SELECT id FROM vendors WHERE name = ? COLLATE NOCASE AND linked_party_id IS NULL", (name,)).fetchone()
    if vendor_match:
        conn.execute("UPDATE vendors SET linked_party_id = ?, updated_by=?, updated_at=? WHERE id = ?",
                     (party_id, session.get('user_id'), now, vendor_match[0]))
    return party_id

def get_or_create_vendor(conn, name):
    if not name or not name.strip():
        return None
    name = name.strip()
    row = conn.execute("SELECT id FROM vendors WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # If this name already exists as a Party, link this vendor record to that party
    # instead of creating a fully independent one — keeps ledgers combined.
    party_match = conn.execute("SELECT id FROM parties WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if party_match:
        existing_link = conn.execute("SELECT id FROM vendors WHERE linked_party_id = ?", (party_match[0],)).fetchone()
        if existing_link:
            return existing_link[0]
        cur = conn.execute("INSERT INTO vendors (name, linked_party_id, created_by, created_at) VALUES (?,?,?,?)",
                            (name, party_match[0], session.get('user_id'), now))
        return cur.lastrowid
    cur = conn.execute("INSERT INTO vendors (name, created_by, created_at) VALUES (?,?,?)",
                        (name, session.get('user_id'), now))
    return cur.lastrowid

def get_or_create_vehicle(conn, vno, type_hint=None):
    """Shared by add_trip/edit_trip (previously two separately hand-written copies of this same
    lookup). type_hint is only ever used the moment a brand-new vehicle_no is typed straight into
    the Trip form and doesn't exist yet — its own/hired Type field is still editable in that one
    case (see trip_form.html), and whatever was picked there becomes that new vehicle's permanent
    type. An already-existing vehicle keeps its own type untouched; _resolve_trip_type() below is
    what actually decides the trip's own type value, not this function."""
    if not vno or not vno.strip():
        return None
    vno = vno.strip()
    row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
    if row:
        return row[0]
    vtype = type_hint if type_hint in (VEHICLE_TYPE_OWN, VEHICLE_TYPE_HIRED) else VEHICLE_TYPE_OWN
    cur = conn.execute("INSERT INTO vehicles (vehicle_no, type, created_by, created_at) VALUES (?,?,?,?)",
                        (vno, vtype, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return cur.lastrowid

def _get_or_create_vehicle_no_type(conn, vno):
    """Extracted from 13 byte-for-byte-identical nested closures (Tyre/Battery/Urea/Toll/
    Compliance/Service's own insert-from-form functions each hand-rolled the exact same lookup).
    Deliberately a SEPARATE function from get_or_create_vehicle() just above, not a call into it
    with a default — that one sets a real `type` (own/hired) on a newly-created vehicle, these 13
    call sites never did (a vehicle auto-created from typing a new number into e.g. Add Tyre gets
    a NULL type, same as it always has); pointing them at get_or_create_vehicle() would silently
    change that. Same SQL, same behavior as every one of those 13 copies, just one copy of it."""
    if not vno or not vno.strip():
        return None
    vno = vno.strip()
    row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE", (vno,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO vehicles (vehicle_no, created_by, created_at) VALUES (?,?,?)",
                        (vno, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return cur.lastrowid

def _resolve_trip_type(conn, vehicle_id, posted_type):
    """A trip's own/hired type always mirrors its vehicle's own type — never an independent
    choice — so this re-reads the vehicle's actual stored type (the authoritative value) rather
    than trusting whatever the form posted. The Type field is read-only in the UI once a vehicle
    is selected (JS-driven, trip_form.html); this is the server-side backstop for that rule, not
    just a client-side nicety someone could bypass by editing the page. Only a brand-new vehicle
    (created by this very save, nothing to inherit from yet) falls back to the posted value — the
    same one get_or_create_vehicle() just stamped onto that new row above."""
    if not vehicle_id:
        return posted_type if posted_type in (VEHICLE_TYPE_OWN, VEHICLE_TYPE_HIRED) else None
    row = conn.execute("SELECT type FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    return row['type'] if row and row['type'] else posted_type

def _accounts_rows_by_entries(conn):
    """The ORIGINAL per-entity implementation of _accounts_rows(): builds every party's and vendor's full
    ledger and sums it (about 9,000 queries for 250 accounts). Kept only so the test suite can prove the
    fast bulk version below returns exactly the same numbers. Same docstring as the fast one:
    Shared party+vendor balance list used by the Ledger page, its export, Dashboard, and
    Business Performance. Each row's balance is computed by running the exact same transactional
    entries the individual ledger detail page uses (_get_party_ledger_entries /
    _get_vendor_ledger_entries) — not a separate hand-written SQL aggregate. That used to be two
    different formulas that could (and did) disagree: the old aggregate here had no idea a party
    could have a linked vendor record (fuel/maintenance/owner-hire bought under the same name),
    so a party who was also a heavy vendor-side payable showed up here as Receivable while their
    own detail page — which does merge both sides — correctly showed Payable. Computing every
    row from the same entries the detail page reads makes that class of mismatch impossible.
    """
    parties = conn.execute("""SELECT id, name, contact, email, address, credit_limit, category, gstin, status
        FROM parties ORDER BY name""").fetchall()
    vendors = conn.execute("""SELECT id, name, contact, email, address, credit_limit, category, gstin, status
        FROM vendors WHERE linked_party_id IS NULL ORDER BY name""").fetchall()

    def _entry_balance(entries):
        # Same convention as the ledger detail page: debit - credit, positive = receivable,
        # negative = payable (see _get_party_ledger_entries / _get_vendor_ledger_entries).
        return sum(e['debit'] for e in entries) - sum(e['credit'] for e in entries)

    rows = []
    for p in parties:
        # conn=conn — reuses this function's own already-open connection for every party/vendor
        # instead of each call opening (and closing) a brand new one; with 70+ parties and 36+
        # vendors that was ~106 separate SQLite connections for one page load (Ledger/Dashboard/
        # Business Performance all hit this). Same queries, same balances either way.
        balance = _entry_balance(_get_party_ledger_entries(p['id'], conn=conn))
        rows.append({'id': p['id'], 'name': p['name'], 'role': 'Party', 'balance': balance,
                      'contact': p['contact'], 'email': p['email'], 'address': p['address'], 'credit_limit': p['credit_limit'],
                      'group': p['category'] or '', 'gstin': p['gstin'], 'status': p['status'] or 'Active'})
    for v in vendors:
        balance = _entry_balance(_get_vendor_ledger_entries(v['id'], conn=conn))
        rows.append({'id': v['id'], 'name': v['name'], 'role': 'Vendor', 'balance': balance,
                      'contact': v['contact'], 'email': v['email'], 'address': v['address'], 'credit_limit': v['credit_limit'],
                      'group': v['category'] or '', 'gstin': v['gstin'], 'status': v['status'] or 'Active'})
    rows.sort(key=lambda r: r['name'])
    return rows

def _bulk_balances(conn):
    """Every party's and vendor's closing balance in a handful of queries instead of ~35 per account.
    This is a straight re-statement of the arithmetic in _get_party_ledger_entries() /
    _get_vendor_ledger_entries() (debit - credit summed over every entry), computed from whole tables
    grouped in Python. test_balance_audit.py and test_load_equivalence compare it with the original
    per-entity code, so the two can never drift apart unnoticed. Returns ({party_id: balance}, {vendor_id: balance});
    a vendor linked to a party is included in the vendor dict AND added into that party's balance, exactly as the
    party ledger merges it."""
    def groups(sql, key, params=()):
        out = {}
        for r in conn.execute(sql, params).fetchall():
            out.setdefault(r[key], []).append(r)
        return out

    trip_alloc = {r['trip_id']: r['amt'] or 0 for r in conn.execute(
        "SELECT pa.trip_id, SUM(pa.amount) AS amt FROM payment_allocations pa JOIN trips t ON pa.trip_id=t.id GROUP BY pa.trip_id").fetchall()}
    maint_alloc = {r['maintenance_id']: r['amt'] or 0 for r in conn.execute(
        "SELECT pa.maintenance_id, SUM(pa.amount) AS amt FROM payment_allocations pa JOIN maintenance m ON pa.maintenance_id=m.id GROUP BY pa.maintenance_id").fetchall()}
    over_alloc = {r['overhead_id']: r['amt'] or 0 for r in conn.execute(
        "SELECT pa.overhead_id, SUM(pa.amount) AS amt FROM payment_allocations pa JOIN overheads o ON pa.overhead_id=o.id GROUP BY pa.overhead_id").fetchall()}
    # per payment: what was applied to trips / maintenance / expenses (only rows whose target still exists, like the ledger's JOINs)
    pay_trip = {r['payment_id']: (r['amt'] or 0, r['n']) for r in conn.execute(
        "SELECT pa.payment_id, SUM(pa.amount) AS amt, COUNT(*) AS n FROM payment_allocations pa JOIN trips t ON pa.trip_id=t.id GROUP BY pa.payment_id").fetchall()}
    pay_maint = {r['payment_id']: (r['amt'] or 0, r['n']) for r in conn.execute(
        "SELECT pa.payment_id, SUM(pa.amount) AS amt, COUNT(*) AS n FROM payment_allocations pa JOIN maintenance m ON pa.maintenance_id=m.id GROUP BY pa.payment_id").fetchall()}
    pay_over = {r['payment_id']: (r['amt'] or 0, r['n']) for r in conn.execute(
        "SELECT pa.payment_id, SUM(pa.amount) AS amt, COUNT(*) AS n FROM payment_allocations pa JOIN overheads o ON pa.overhead_id=o.id GROUP BY pa.payment_id").fetchall()}

    # ---------------- vendors
    vendors = {r['id']: r for r in conn.execute("SELECT id, name, opening_balance, linked_party_id FROM vendors").fetchall()}
    vbal = {vid: (v['opening_balance'] or 0) for vid, v in vendors.items()}      # opening: debit max(ob,0) - credit max(-ob,0) == ob
    for m in conn.execute("SELECT id, vendor_id, amount, paid_amount FROM maintenance WHERE vendor_id IS NOT NULL").fetchall():
        if m['vendor_id'] in vbal:
            vbal[m['vendor_id']] += max((m['paid_amount'] or 0) - maint_alloc.get(m['id'], 0), 0) - (m['amount'] or 0)
    for t in conn.execute("""SELECT id, owner_vendor_id, fuel_vendor_id, fuel_amount, driver_adv_vendor_id, driver_adv_amount, owner_rate_type,
                                    owner_fixed_amount, owner_rate, quantity, paid_to_owner FROM trips
                             WHERE owner_vendor_id IS NOT NULL OR fuel_vendor_id IS NOT NULL OR driver_adv_vendor_id IS NOT NULL""").fetchall():
        owner, fv, av = t['owner_vendor_id'], t['fuel_vendor_id'], t['driver_adv_vendor_id']
        if fv in vbal and (owner is None or owner != fv):          # fuel paid out of the owner's own freight is already inside his Trip Bill
            vbal[fv] -= (t['fuel_amount'] or 0)
        if av in vbal and (owner is None or owner != av):
            vbal[av] -= (t['driver_adv_amount'] or 0)
        if owner in vbal:
            owed = (t['owner_fixed_amount'] if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0))
            if owed:
                vbal[owner] += max((t['paid_to_owner'] or 0) - trip_alloc.get(t['id'], 0), 0) - owed
                if fv != owner and t['fuel_amount']:
                    vbal[owner] += t['fuel_amount']
                if av != owner and t['driver_adv_amount']:
                    vbal[owner] += t['driver_adv_amount']
    for r in conn.execute("SELECT ii.vendor_id, ii.amount FROM invoice_items ii JOIN trips t ON ii.trip_id=t.id WHERE ii.vendor_id IS NOT NULL").fetchall():
        if r['vendor_id'] in vbal:
            vbal[r['vendor_id']] -= (r['amount'] or 0)
    for r in conn.execute("SELECT ibi.vendor_id, ibi.amount FROM invoice_batch_items ibi JOIN invoice_batches ib ON ibi.invoice_batch_id=ib.id WHERE ibi.vendor_id IS NOT NULL").fetchall():
        if r['vendor_id'] in vbal:
            vbal[r['vendor_id']] -= (r['amount'] or 0)
    by_name = {}
    for vid, v in vendors.items():
        by_name.setdefault((v['name'] or '').strip().lower(), []).append(vid)
    for o in conn.execute("SELECT id, vendor, amount, COALESCE(status,'Paid') AS status FROM overheads WHERE TRIM(COALESCE(vendor,'')) != ''").fetchall():
        alloc_o = over_alloc.get(o['id'], 0)
        if o['status'] != 'Pending' and alloc_o <= 0.004:
            continue
        for vid in by_name.get((o['vendor'] or '').strip().lower(), []):
            vbal[vid] += (max((o['amount'] or 0) - alloc_o, 0) if o['status'] == 'Paid' else 0) - (o['amount'] or 0)
    for p in conn.execute("SELECT id, vendor_id, amount, allocated_amount FROM payments WHERE vendor_id IS NOT NULL AND payment_type='paid'").fetchall():
        if p['vendor_id'] not in vbal:
            continue
        at, nt = pay_trip.get(p['id'], (0, 0))
        am, nm = pay_maint.get(p['id'], (0, 0))
        ao, no = pay_over.get(p['id'], (0, 0))
        vbal[p['vendor_id']] += at + am + ao
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        if leftover > 0.004 or (nt + nm + no) == 0:
            vbal[p['vendor_id']] += max(leftover, 0)

    # ---------------- parties
    parties = {r['id']: r for r in conn.execute("SELECT id, opening_balance FROM parties").fetchall()}
    pbal = {pid: (p['opening_balance'] or 0) for pid, p in parties.items()}
    for t in conn.execute("SELECT id, party_id, billed_amount, payment_received, party_advance FROM trips WHERE party_id IS NOT NULL").fetchall():
        if t['party_id'] in pbal:
            original_received = max((t['payment_received'] or 0) - trip_alloc.get(t['id'], 0), 0)
            pbal[t['party_id']] += (t['billed_amount'] or 0) - (original_received + (t['party_advance'] or 0))
    for p in conn.execute("SELECT id, party_id, amount, allocated_amount FROM payments WHERE party_id IS NOT NULL AND payment_type='received'").fetchall():
        if p['party_id'] not in pbal:
            continue
        at, nt = pay_trip.get(p['id'], (0, 0))
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        credit = at + (max(leftover, 0) if (leftover > 0.004 or nt == 0) else 0)
        pbal[p['party_id']] -= credit
    linked = {}
    for vid, v in vendors.items():
        if v['linked_party_id'] is not None and v['linked_party_id'] not in linked:
            linked[v['linked_party_id']] = vid
    for pid, vid in linked.items():
        if pid in pbal:
            pbal[pid] += vbal[vid]
    return pbal, vbal

def _accounts_rows(conn):
    """Shared party+vendor balance list used by the Ledger page, its export, Dashboard, and Business
    Performance — the same numbers each party's / vendor's own ledger page ends on, now computed for
    everyone at once by _bulk_balances() (a few queries) instead of building ~250 full ledgers (about
    9,000 queries, ~4 s with 12,000 trips). _accounts_rows_by_entries() is the original slow
    implementation, kept so tests can prove the two agree."""
    parties = conn.execute("""SELECT id, name, contact, email, address, credit_limit, category, gstin, status
        FROM parties ORDER BY name""").fetchall()
    vendors = conn.execute("""SELECT id, name, contact, email, address, credit_limit, category, gstin, status
        FROM vendors WHERE linked_party_id IS NULL ORDER BY name""").fetchall()
    pbal, vbal = _bulk_balances(conn)
    rows = []
    for p in parties:
        rows.append({'id': p['id'], 'name': p['name'], 'role': 'Party', 'balance': pbal.get(p['id'], 0),
                      'contact': p['contact'], 'email': p['email'], 'address': p['address'], 'credit_limit': p['credit_limit'],
                      'group': p['category'] or '', 'gstin': p['gstin'], 'status': p['status'] or 'Active'})
    for v in vendors:
        rows.append({'id': v['id'], 'name': v['name'], 'role': 'Vendor', 'balance': vbal.get(v['id'], 0),
                      'contact': v['contact'], 'email': v['email'], 'address': v['address'], 'credit_limit': v['credit_limit'],
                      'group': v['category'] or '', 'gstin': v['gstin'], 'status': v['status'] or 'Active'})
    rows.sort(key=lambda r: r['name'])
    return rows

def _filter_accounts_rows(rows, search_f, role_f, group_f, status_f=''):
    if search_f:
        s = search_f.lower()
        rows = [r for r in rows if s in r['name'].lower() or s in (r['contact'] or '').lower() or s in (r['email'] or '').lower()]
    if role_f:
        rows = [r for r in rows if r['role'] == role_f]
    if group_f:
        rows = [r for r in rows if r['group'] == group_f]
    if status_f:
        rows = [r for r in rows if r['status'].lower() == status_f.lower()]
    return rows

@app.route('/accounts')
def accounts():
    conn = get_db()
    search_f = request.args.get('search', '')
    role_f = request.args.get('role', '')
    group_f = request.args.get('group', '')
    status_f = request.args.get('status', '')

    all_rows = _accounts_rows(conn)
    groups = sorted(set(r['group'] for r in all_rows if r['group']))
    total_parties = sum(1 for r in all_rows if r['role'] == 'Party')
    active_parties = sum(1 for r in all_rows if r['role'] == 'Party' and r['status'] == 'Active')
    total_vendors = sum(1 for r in all_rows if r['role'] == 'Vendor')
    active_vendors = sum(1 for r in all_rows if r['role'] == 'Vendor' and r['status'] == 'Active')
    total_receivable = sum(r['balance'] for r in all_rows if r['balance'] > 0)
    receivable_from = sum(1 for r in all_rows if r['balance'] > 0)
    total_payable = sum(-r['balance'] for r in all_rows if r['balance'] < 0)
    payable_to = sum(1 for r in all_rows if r['balance'] < 0)
    net_balance = sum(r['balance'] for r in all_rows)

    rows = _filter_accounts_rows(all_rows, search_f, role_f, group_f, status_f)
    conn.close()

    total_count = len(rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)
    # Tabs (All/Parties/Vendors) each set their own `role` — strip any existing role from the
    # querystring they build on top of, otherwise clicking a tab appends a second role= param
    # and request.args.get('role') keeps returning the *first* one, so tabs stop switching.
    tab_params = dict(base_params)
    tab_params.pop('role', None)
    tab_qs = urlencode(tab_params)

    return render_template('home.html', rows=page_rows, total_count=total_count,
                            total_parties=total_parties, active_parties=active_parties,
                            total_vendors=total_vendors, active_vendors=active_vendors,
                            total_receivable=total_receivable, receivable_from=receivable_from,
                            total_payable=total_payable, payable_to=payable_to, net_balance=net_balance,
                            groups=groups, page=page, per_page=per_page, total_pages=total_pages,
                            page_tokens=page_tokens, base_qs=base_qs, tab_qs=tab_qs,
                            f_search=search_f, f_role=role_f, f_group=group_f, f_status=status_f, active='accounts')

@app.route('/accounts/export')
def export_accounts():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    rows = _filter_accounts_rows(_accounts_rows(conn), request.args.get('search', ''),
                                  request.args.get('role', ''), request.args.get('group', ''), request.args.get('status', ''))
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    headers = ["Name", "Type", "Group / Category", "Phone", "Email", "GSTIN", "Receivable", "Payable", "Balance", "Status"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, r in enumerate(rows, 2):
        receivable = r['balance'] if r['balance'] > 0 else 0
        payable = -r['balance'] if r['balance'] < 0 else 0
        for c_idx, val in enumerate([r['name'], r['role'], r['group'], r['contact'] or '', r['email'] or '', r['gstin'] or '',
                                      receivable, payable, r['balance'], r['status']], 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='ledger_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def _create_account_from_form(conn, f):
    """Shared by add_account() (website, /accounts/add) and app_add_account() (mobile,
    /app/accounts/add) — the exact same Party/Vendor creation logic, not two hand-written copies
    that could quietly drift apart. Returns True if a row was actually inserted, False if the
    name field was blank (nothing to do) or a duplicate name hit the table's UNIQUE constraint —
    either way the caller just redirects back to its own list, same as this always did."""
    name = (f.get('name') or '').strip()
    if not name:
        return False
    role = f.get('role', 'Party')
    contact = f.get('contact') or None
    email = f.get('email') or None
    address = f.get('address') or None
    category = f.get('category') or None
    gstin = f.get('gstin') or None
    credit_limit, err1 = _parse_money(f.get('credit_limit'), 'Credit limit')
    opening_balance, err2 = _parse_money(f.get('opening_balance'), 'Opening balance', allow_negative=True)
    if err1 or err2:
        return False          # a typed-nonsense amount must not crash the page or be stored
    credit_limit = credit_limit or None
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        if role == 'Vendor':
            conn.execute("""INSERT INTO vendors (name, category, contact, email, address, credit_limit, opening_balance, gstin, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (name, category, contact, email, address, credit_limit, opening_balance, gstin, session.get('user_id'), now))
        else:
            conn.execute("""INSERT INTO parties (name, contact, email, address, credit_limit, opening_balance, category, gstin, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (name, contact, email, address, credit_limit, opening_balance, category, gstin, session.get('user_id'), now))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

@app.route('/accounts/add', methods=['POST'])
def add_account():
    conn = get_db()
    _create_account_from_form(conn, request.form)
    conn.close()
    return redirect(url_for('accounts'))

@app.route('/accounts/<role>/<int:account_id>/toggle-status', methods=['POST'])
def toggle_account_status(role, account_id):
    conn = get_db()
    table = 'vendors' if role == 'vendor' else 'parties'
    current = conn.execute(f"SELECT status FROM {table} WHERE id=?", (account_id,)).fetchone()
    new_status = 'Inactive' if (current and current['status'] == 'Active') else 'Active'
    conn.execute(f"UPDATE {table} SET status=?, updated_by=?, updated_at=? WHERE id=?",
                 (new_status, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), account_id))
    conn.commit()
    conn.close()
    return redirect(url_for('accounts', **request.args.to_dict()))

@app.route('/')
def home():
    return redirect(url_for('dashboard'))

def _dashboard_financials_data(conn, date_from, date_to, vehicle_f=''):
    """Revenue/expense/profit core for the Dashboard — extracted unchanged from dashboard()
    (website) so app_dashboard() (mobile) can show the exact same Business Overview numbers and
    the exact same Top Expenses breakdown, never a second hand-written copy that could quietly
    drift. It follows the same rule as _period_financials() (Business Performance/Route Analytics):
    own-vehicle trips cost their fuel / driver advance / other charges, hired-vehicle trips cost
    what the owner is paid (billed - owner cost = the trip's profit; the fuel/advance on a hired
    trip sit inside that owner freight and are not counted again). Returns a dict; does not close conn."""
    trip_query = "SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id WHERE t.date>=? AND t.date<=?"
    params = [date_from, date_to]
    if vehicle_f:
        trip_query += " AND v.vehicle_no=?"
        params.append(vehicle_f)
    trips = conn.execute(trip_query, params).fetchall()

    total_revenue = sum(t['billed_amount'] or 0 for t in trips)
    trip_count = len(trips)
    # Toll is deliberately NOT in this sum — Maintenance > Toll (toll_entries/maintenance
    # category='Toll') is the source of truth for toll cost now, folded into maint_total below.
    # Adding the old per-trip manual `toll` field here too would double-count the same real-world
    # toll charge once it's been synced/imported through Toll Management.
    # Fuel/driver-advance recorded on a Hired trip is the owner's own money (covered by the
    # contracted freight, same rule as the vendor ledger and invoice center) — not a real company
    # expense, so it's excluded here. Everything else in this sum applies regardless of vehicle type.
    # What the owner of a hired vehicle is paid IS the cost of a hired trip (same figure as the
    # 'Hired Vehicle Profit' tile = billed - owner cost, and as Business Performance), so it belongs
    # in total expenses — otherwise Total Profit would count a hired trip's whole bill as profit.
    hired_owner_cost = sum((t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED'
                           else (t['owner_rate'] or 0) * (t['quantity'] or 0)
                           for t in trips if t['type'] == VEHICLE_TYPE_HIRED)
    fuel_total = sum(t['fuel_amount'] or 0 for t in trips if t['type'] == VEHICLE_TYPE_OWN)
    driveradv_total = sum(t['driver_adv_amount'] or 0 for t in trips if t['type'] == VEHICLE_TYPE_OWN)
    other_charges_total = sum((t['agent_commission'] or 0)+(t['builty_expense'] or 0)+(t['conductor_expense'] or 0)+
                              (t['fine'] or 0)+(t['labour_charges'] or 0)+(t['parking'] or 0)+(t['puncture'] or 0)+
                              (t['urea'] or 0)+(t['loading_expense'] or 0)+(t['unloading_expense'] or 0)+
                              (t['wear_tear'] or 0)+(t['weighbridge_charges'] or 0)+(t['other_expense'] or 0) for t in trips)
    total_charges_paid = fuel_total + driveradv_total + other_charges_total
    adj_revenue = total_revenue - total_charges_paid

    maint_total_query = "SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id WHERE m.date>=? AND m.date<=?"
    maint_toll_query = "SELECT COALESCE(SUM(m.amount),0) FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id WHERE m.category='Toll' AND m.date>=? AND m.date<=?"
    maint_total_params = [date_from, date_to]
    if vehicle_f:
        maint_total_query += " AND v.vehicle_no=?"
        maint_toll_query += " AND v.vehicle_no=?"
        maint_total_params.append(vehicle_f)
    maint_total = conn.execute(maint_total_query, maint_total_params).fetchone()[0]
    maint_toll_total = conn.execute(maint_toll_query, maint_total_params).fetchone()[0]
    salaries_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM salaries WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    overheads_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM overheads WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    total_expenses = total_charges_paid + maint_total + overheads_total + salaries_total + hired_owner_cost
    total_profit = total_revenue - total_expenses

    all_vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE type IS NOT NULL").fetchall()
    running_vnos = set(t['vehicle_no'] for t in trips if t['vehicle_no'])
    running_count = len(running_vnos)
    idle_count = len(all_vehicles) - running_count

    own_vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type = 'own'").fetchall()
    own_running_ids = set(t['vehicle_id'] for t in trips if t['type'] == VEHICLE_TYPE_OWN)
    own_idle_count = sum(1 for v in own_vehicles if v['id'] not in own_running_ids)
    own_vehicle_count = len(own_vehicles)

    toll_total = maint_toll_total  # real Toll Management figure, not the old per-trip manual field
    maint_other_total = maint_total - maint_toll_total  # rest of Maintenance, so this chart's slices don't overlap
    other_exp_total = total_charges_paid - fuel_total - driveradv_total

    exp_items = [('Fuel', fuel_total, '#2a78d6'), ('Driver Advance', driveradv_total, '#eb6834'),
                 ('Toll', toll_total, '#eda100'), ('Maintenance', maint_other_total, '#e34948'),
                 ('Salaries', salaries_total, '#1a9c5b'), ('Expenses', overheads_total, '#7a5ad6'),
                 ('Hired Vehicle Owners', hired_owner_cost, '#0e8a8a'), ('Other', other_exp_total, '#4a3aa7')]
    exp_max = max([v for _, v, _ in exp_items], default=1) or 1
    exp_breakdown = [{'label': l, 'value': v, 'color': c, 'pct': round((v/exp_max)*100, 1)} for l, v, c in exp_items]
    # Same items, sorted purely by rupee value descending — for a "Top N expense heads" widget
    # (native Dashboard) where display order should follow size, not the fixed category order
    # exp_breakdown keeps for its bar-chart legend.
    exp_breakdown_ranked = sorted(exp_breakdown, key=lambda e: -e['value'])

    return {
        'trips': trips, 'total_revenue': total_revenue, 'trip_count': trip_count,
        'fuel_total': fuel_total, 'driveradv_total': driveradv_total, 'other_charges_total': other_charges_total,
        'total_charges_paid': total_charges_paid, 'adj_revenue': adj_revenue,
        'maint_total': maint_total, 'maint_toll_total': maint_toll_total, 'toll_total': toll_total,
        'maint_other_total': maint_other_total, 'salaries_total': salaries_total, 'overheads_total': overheads_total,
        'other_exp_total': other_exp_total, 'hired_owner_cost': hired_owner_cost, 'total_expenses': total_expenses, 'total_profit': total_profit,
        'running_count': running_count, 'idle_count': idle_count, 'own_vehicle_count': own_vehicle_count,
        'own_idle_count': own_idle_count, 'exp_breakdown': exp_breakdown, 'exp_breakdown_ranked': exp_breakdown_ranked,
    }

@app.route('/dashboard')
def dashboard():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    date_from, date_to = _normalized_date_range(date_from, date_to)      # a From date after the To date is swapped, like every other page
    vehicle_f = request.args.get('vehicle', '')

    dfin = _dashboard_financials_data(conn, date_from, date_to, vehicle_f)
    trips = dfin['trips']
    total_revenue, trip_count = dfin['total_revenue'], dfin['trip_count']
    fuel_total, driveradv_total = dfin['fuel_total'], dfin['driveradv_total']
    total_charges_paid, adj_revenue = dfin['total_charges_paid'], dfin['adj_revenue']
    maint_total, salaries_total = dfin['maint_total'], dfin['salaries_total']
    total_expenses, total_profit = dfin['total_expenses'], dfin['total_profit']
    running_count, idle_count = dfin['running_count'], dfin['idle_count']
    own_vehicle_count, own_idle_count = dfin['own_vehicle_count'], dfin['own_idle_count']
    toll_total, other_exp_total, exp_breakdown = dfin['toll_total'], dfin['other_exp_total'], dfin['exp_breakdown']

    # Vehicle-wise revenue, own vehicles only
    veh_revenue = {}
    for t in trips:
        if t['type'] == VEHICLE_TYPE_OWN and t['vehicle_no']:
            veh_revenue[t['vehicle_no']] = veh_revenue.get(t['vehicle_no'], 0) + (t['billed_amount'] or 0)
    veh_rev_list = sorted(veh_revenue.items(), key=lambda x: x[1], reverse=True)[:10]
    veh_rev_max = max([v for _, v in veh_rev_list], default=1) or 1
    vehicle_revenue = [{'vehicle_no': vn, 'revenue': rev, 'pct': round((rev/veh_rev_max)*100, 1)} for vn, rev in veh_rev_list]

    # Same rows the Ledger page shows — calling _accounts_rows() directly (rather than a separate
    # hand-written query) guarantees this can never drift out of sync with the Ledger or with any
    # individual party/vendor's own detail page ever again. Split by balance sign only, same as
    # accounts()'s own total_receivable/total_payable — not restricted by role, since an
    # overpaid party (balance<0) is a real payable and an overpaid vendor (balance>0) is a real
    # receivable, exactly as the Ledger page itself treats them.
    _acct_rows = _accounts_rows(conn)
    receivables = sorted([r for r in _acct_rows if r['balance'] > 0], key=lambda r: -r['balance'])
    total_receivables = sum(r['balance'] for r in receivables)
    payables = sorted([r for r in _acct_rows if r['balance'] < 0], key=lambda r: r['balance'])
    total_payables = sum(-r['balance'] for r in payables)

    own_vehicle_turnover = sum(t['billed_amount'] or 0 for t in trips if t['type'] == VEHICLE_TYPE_OWN)

    hired_trips = [t for t in trips if t['type'] == VEHICLE_TYPE_HIRED]
    hired_trip_count = len(hired_trips)
    hired_billed = sum(t['billed_amount'] or 0 for t in hired_trips)
    # Owner's actual contracted cost (fixed rate, or owner_rate x quantity) — not what's been paid
    # to them so far, so the margin holds regardless of whether that payment is still pending.
    hired_owner_cost = sum(
        (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        for t in hired_trips)
    hired_vehicle_profit = hired_billed - hired_owner_cost

    hired_veh_data = {}
    for t in hired_trips:
        vn = t['vehicle_no'] or 'Unassigned'
        if vn not in hired_veh_data:
            hired_veh_data[vn] = {'revenue': 0, 'trips': 0}
        hired_veh_data[vn]['revenue'] += t['billed_amount'] or 0
        hired_veh_data[vn]['trips'] += 1
    hired_veh_list = sorted(hired_veh_data.items(), key=lambda x: x[1]['revenue'], reverse=True)[:10]
    hired_veh_max = max([d['revenue'] for _, d in hired_veh_list], default=1) or 1
    hired_vehicle_chart = [{'vehicle_no': vn, 'revenue': d['revenue'], 'trips': d['trips'],
                              'pct': round((d['revenue']/hired_veh_max)*100, 1)} for vn, d in hired_veh_list]

    recent_trips = conn.execute("""SELECT t.date, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, p.name as party_name,
                                    t.driver_name, t.billed_amount
                                    FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                    LEFT JOIN parties p ON t.party_id=p.id
                                    ORDER BY t.date DESC LIMIT 8""").fetchall()

    vehicles_list = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active' ORDER BY vehicle_no").fetchall()

    fy_map = {'2026-04-01|2027-03-31': ('2026-04-01','2027-03-31'), '2025-04-01|2026-03-31': ('2025-04-01','2026-03-31')}
    f_fy = ''
    for key, (fy_from, fy_to) in fy_map.items():
        if date_from == fy_from and date_to == fy_to:
            f_fy = key
            break

    maint_query = """SELECT m.category, SUM(m.amount) as total FROM maintenance m
                     LEFT JOIN vehicles v ON m.vehicle_id=v.id
                     WHERE m.date>=? AND m.date<=?"""
    maint_params = [date_from, date_to]
    if vehicle_f:
        maint_query += " AND v.vehicle_no=?"
        maint_params.append(vehicle_f)
    maint_query += " GROUP BY m.category ORDER BY total DESC LIMIT 8"
    maint_by_category = conn.execute(maint_query, maint_params).fetchall()
    maint_cat_max = max([m['total'] for m in maint_by_category], default=1) or 1
    maint_by_category = [{'category': m['category'], 'total': m['total'],
                           'pct': round((m['total'] / maint_cat_max) * 100, 1)} for m in maint_by_category]

    # Fleet status donut (own vehicles): Running / Idle
    fleet_total = own_vehicle_count or 1
    fleet_running_pct = round((own_vehicle_count - own_idle_count) / fleet_total * 100, 1)

    # Top performing vehicles table (own vehicles, by revenue)
    top_vehicles = []
    for vn, rev in veh_rev_list[:5]:
        vtrips = sum(1 for t in trips if t['vehicle_no'] == vn)
        top_vehicles.append({'vehicle_no': vn, 'trips': vtrips, 'revenue': rev})

    # Real alerts from real data. Compliance alerts are a pure DB read (cs.refresh_compliance
    # inside get_compliance_alerts never calls a provider) — safe on every page load.
    alerts = []
    for r in receivables[:3]:
        alerts.append({'text': f"{r['name']} balance overdue", 'sub': f"₹{r['balance']:,.0f} pending"})
    if own_idle_count > 0:
        alerts.append({'text': f"{own_idle_count} own vehicle(s) currently idle", 'sub': 'Check Idle Tracker for details'})
    for a in cs.get_compliance_alerts(conn)[:5]:
        verb = 'expired' if a['status'] == 'Expired' else f"expires in {a['days_left']}d"
        alerts.append({'text': f"{a['vehicle_no']} — {a['label']} {verb}", 'sub': f"Expiry {a['expiry']} — renew from Vehicles > Edit > Compliance"})

    # ============================================================================
    # Lower dashboard section — the six panels below the KPI row (Freight Revenue Trend,
    # Expense Breakdown [unchanged, see above], Recent Activities, Trip Status,
    # Payables & Receivables, Top Performing Vehicles). Everything here respects the same
    # date_from/date_to/vehicle filters as the rest of the page wherever the metric is
    # period-based; Recent Activities is deliberately a live "what just happened" feed instead,
    # same idea as the reference design's timestamped list, not scoped to the filter.
    # ============================================================================
    d_from = datetime.date.fromisoformat(date_from)
    d_to = datetime.date.fromisoformat(date_to)
    period_days = (d_to - d_from).days + 1
    today = datetime.date.today()
    today_str = today.isoformat()

    # 1. Freight Revenue Trend — monthly buckets for the selected period, plus the same-length
    # immediately-preceding period as a lighter "Last Period" comparison line.
    def _month_range(d1, d2):
        keys = []
        cur = d1.replace(day=1)
        while cur <= d2:
            keys.append(cur.strftime('%Y-%m'))
            cur = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return keys

    this_month_keys = _month_range(d_from, d_to)
    this_period_buckets = {k: 0 for k in this_month_keys}
    for t in trips:
        k = (t['date'] or '')[:7]
        if k in this_period_buckets:
            this_period_buckets[k] += t['billed_amount'] or 0

    prev_to = d_from - datetime.timedelta(days=1)
    prev_from = prev_to - datetime.timedelta(days=period_days - 1)
    prev_trip_query = "SELECT t.date, t.billed_amount FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id WHERE t.date>=? AND t.date<=?"
    prev_params = [prev_from.isoformat(), prev_to.isoformat()]
    if vehicle_f:
        prev_trip_query += " AND v.vehicle_no=?"
        prev_params.append(vehicle_f)
    prev_trips = conn.execute(prev_trip_query, prev_params).fetchall()
    prev_month_keys = _month_range(prev_from, prev_to)
    prev_period_buckets = {k: 0 for k in prev_month_keys}
    for t in prev_trips:
        k = (t['date'] or '')[:7]
        if k in prev_period_buckets:
            prev_period_buckets[k] += t['billed_amount'] or 0
    prev_vals = list(prev_period_buckets.values())

    # Paired by relative position (month 1 of this period vs month 1 of last period), not by
    # calendar month — the two periods rarely share actual months.
    freight_trend = []
    for i, k in enumerate(this_month_keys):
        label = datetime.datetime.strptime(k, '%Y-%m').strftime('%b %Y')
        prev_val = prev_vals[i] if i < len(prev_vals) else 0
        freight_trend.append({'label': label, 'value': this_period_buckets[k], 'prev_value': prev_val})
    trend_max = max([max(f['value'], f['prev_value']) for f in freight_trend], default=1) or 1

    # Pixel coords for the two-line (this period vs last period) SVG — same 700x220 viewport /
    # padding convention as _trend_chart_coords, extended to place two series on one shared scale.
    _ft_w, _ft_h, _ft_pl, _ft_pr, _ft_pt, _ft_pb = 700, 220, 34, 12, 16, 30
    _ft_plot_w, _ft_plot_h, _ft_n = _ft_w - _ft_pl - _ft_pr, _ft_h - _ft_pt - _ft_pb, len(freight_trend)
    freight_chart_bottom = _ft_pt + _ft_plot_h
    for i, f in enumerate(freight_trend):
        f['x'] = round(_ft_pl + (i * _ft_plot_w / (_ft_n - 1) if _ft_n > 1 else 0), 1)
        f['y'] = round(_ft_pt + _ft_plot_h - (f['value'] / trend_max * _ft_plot_h), 1)
        f['py'] = round(_ft_pt + _ft_plot_h - (f['prev_value'] / trend_max * _ft_plot_h), 1)
    freight_y_ticks = []
    for k in range(4):
        tick_val = round(trend_max * k / 3)
        freight_y_ticks.append({'value': tick_val, 'y': round(_ft_pt + _ft_plot_h - (tick_val / trend_max * _ft_plot_h), 1)})

    # 2. Trip Status — Completed / In-Transit / Upcoming, from the already-filtered `trips`.
    completed_trips = [t for t in trips if t['end_date']]
    in_transit_trips = [t for t in trips if not t['end_date'] and (t['date'] or '') <= today_str]
    upcoming_trips = [t for t in trips if not t['end_date'] and (t['date'] or '') > today_str]
    trip_status = {
        'completed': len(completed_trips), 'in_transit': len(in_transit_trips), 'upcoming': len(upcoming_trips),
        'completed_pct': round(len(completed_trips) / trip_count * 100, 1) if trip_count else 0,
        'in_transit_pct': round(len(in_transit_trips) / trip_count * 100, 1) if trip_count else 0,
        'upcoming_pct': round(len(upcoming_trips) / trip_count * 100, 1) if trip_count else 0,
    }

    # 3. Recent Activities — a real unified feed of meaningful business events, drawn from every
    # table's own created_at/updated_at (the audit-columns work earlier this session), combined
    # and sorted — not a filtered/period metric, this is "what just happened" regardless of the
    # date range above, same as the reference design's always-live activity list.
    activities = []
    for r in conn.execute("SELECT invoice_number, created_at FROM invoice_batches WHERE created_at IS NOT NULL ORDER BY created_at DESC LIMIT 5").fetchall():
        activities.append({'icon': 'invoice', 'text': f"Invoice {r['invoice_number']} generated", 'ts': r['created_at']})
    for r in conn.execute("""SELECT p.payment_type, p.amount, p.created_at, pt.name as party_name, v.name as vendor_name
                             FROM payments p LEFT JOIN parties pt ON p.party_id=pt.id LEFT JOIN vendors v ON p.vendor_id=v.id
                             WHERE p.created_at IS NOT NULL ORDER BY p.created_at DESC LIMIT 5""").fetchall():
        who = r['party_name'] or r['vendor_name'] or 'account'
        verb = 'received from' if r['payment_type'] == 'received' else 'made to'
        activities.append({'icon': 'payment', 'text': f"Payment ₹{r['amount'] or 0:,.0f} {verb} {who}", 'ts': r['created_at']})
    for r in conn.execute("""SELECT t.lr_number, v.vehicle_no, t.updated_at FROM trips t
                             LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.end_date IS NOT NULL AND t.updated_at IS NOT NULL ORDER BY t.updated_at DESC LIMIT 5""").fetchall():
        activities.append({'icon': 'trip', 'text': f"Vehicle {r['vehicle_no'] or ''} completed trip {r['lr_number'] or ''}".strip(), 'ts': r['updated_at']})
    for r in conn.execute("SELECT lr_number, created_at FROM trips WHERE created_at IS NOT NULL ORDER BY created_at DESC LIMIT 5").fetchall():
        activities.append({'icon': 'trip', 'text': f"New trip created — {r['lr_number'] or 'no LR'}", 'ts': r['created_at']})
    for r in conn.execute("""SELECT m.category, v.vehicle_no, m.created_at FROM maintenance m
                             LEFT JOIN vehicles v ON m.vehicle_id=v.id
                             WHERE m.created_at IS NOT NULL ORDER BY m.created_at DESC LIMIT 5""").fetchall():
        activities.append({'icon': 'maintenance', 'text': f"Maintenance recorded — {r['category'] or 'Service'} ({r['vehicle_no'] or 'N/A'})", 'ts': r['created_at']})
    for r in conn.execute("SELECT category, amount, created_at FROM overheads WHERE created_at IS NOT NULL ORDER BY created_at DESC LIMIT 3").fetchall():
        activities.append({'icon': 'expense', 'text': f"Expense recorded — {r['category'] or 'Overhead'} (₹{r['amount'] or 0:,.0f})", 'ts': r['created_at']})
    for r in (conn.execute("""SELECT employee, updated_at FROM salaries WHERE payment_status='paid' AND updated_at IS NOT NULL
                             ORDER BY updated_at DESC LIMIT 3""").fetchall() if _salary_scope_ids(conn) is None else []):   # a restricted salary viewer is not told who was paid
        activities.append({'icon': 'salary', 'text': f"Salary paid — {r['employee']}", 'ts': r['updated_at']})
    for r in conn.execute("""SELECT v.vehicle_no, vc.compliance_type, vc.updated_at FROM vehicle_compliance vc
                             LEFT JOIN vehicles v ON vc.vehicle_id=v.id
                             WHERE vc.updated_at IS NOT NULL ORDER BY vc.updated_at DESC LIMIT 3""").fetchall():
        activities.append({'icon': 'compliance', 'text': f"{r['vehicle_no'] or 'Vehicle'} — {(r['compliance_type'] or 'compliance').title()} updated", 'ts': r['updated_at']})
    def _norm_ts(raw):
        """Most created_at/updated_at values are '%Y-%m-%d %H:%M:%S'. A handful of older
        invoice_batches rows were saved with the compact %Y%m%d%H%M%S format instead (a
        pre-existing bug in invoice_center_generate, fixed alongside this dashboard — see the
        created_at=audit_now change above). Comparing those two formats as plain strings sorts
        wrong (the dash in the normal format always sorts "earlier" than any digit), so this
        normalizes both to the same real datetime before anything sorts or displays them."""
        if not raw:
            return None
        try:
            return datetime.datetime.strptime(raw[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 14:
            try:
                return datetime.datetime.strptime(digits[:14], '%Y%m%d%H%M%S')
            except ValueError:
                pass
        return None

    for a in activities:
        a['_dt'] = _norm_ts(a['ts'])
    activities = [a for a in activities if a['_dt']]
    activities.sort(key=lambda a: a['_dt'], reverse=True)
    activities = activities[:6]
    for a in activities:
        a['time_label'] = a['_dt'].strftime('%d %b, %I:%M %p')

    # 4. Payables & Receivables — overdue approximation: an account's own most recent trip
    # activity older than 30 days while it still carries an outstanding balance. Not exact
    # invoice-level AR/AP aging (no per-invoice due-date ledger exists yet), but a real,
    # data-derived signal rather than a made-up number.
    overdue_cutoff = (today - datetime.timedelta(days=30)).isoformat()
    overdue_receivables = 0
    for r in receivables:
        last = conn.execute("SELECT MAX(date) as d FROM trips WHERE party_id=?", (r['id'],)).fetchone()['d']
        if last and last < overdue_cutoff:
            overdue_receivables += r['balance']
    overdue_payables = 0
    for r in payables:
        last = conn.execute("""SELECT MAX(date) as d FROM trips
                               WHERE owner_vendor_id=? OR fuel_vendor_id=? OR driver_adv_vendor_id=? OR misc_vendor_id=?""",
                            (r['id'], r['id'], r['id'], r['id'])).fetchone()['d']
        if last and last < overdue_cutoff:
            overdue_payables += -r['balance']

    # 5. Top Performing Vehicles — enrich the existing top_vehicles with avg freight/trip, a
    # real utilization % (days that vehicle actually ran / days in the selected period), and
    # its current status.
    veh_status = {v['vehicle_no']: v['status'] for v in conn.execute("SELECT vehicle_no, status FROM vehicles").fetchall()}
    for tv in top_vehicles:
        v_trip_dates = set(t['date'] for t in trips if t['vehicle_no'] == tv['vehicle_no'] and t['date'])
        tv['avg_freight'] = tv['revenue'] / tv['trips'] if tv['trips'] else 0
        tv['utilization_pct'] = round(len(v_trip_dates) / period_days * 100, 1) if period_days else 0
        tv['status'] = veh_status.get(tv['vehicle_no']) or 'Active'

    conn.close()
    return render_template('dashboard.html',
        total_revenue=total_revenue, adj_revenue=adj_revenue, total_charges_paid=total_charges_paid,
        total_expenses=total_expenses, total_profit=total_profit, trip_count=trip_count,
        running_count=running_count, idle_count=idle_count, own_vehicle_turnover=own_vehicle_turnover,
        hired_trip_count=hired_trip_count, hired_vehicle_profit=hired_vehicle_profit, hired_vehicle_chart=hired_vehicle_chart,
        own_idle_count=own_idle_count, own_vehicle_count=own_vehicle_count,
        fuel_total=fuel_total, driveradv_total=driveradv_total, toll_total=toll_total, other_exp_total=other_exp_total,
        maint_total=maint_total, salaries_total=salaries_total,
        receivables=receivables[:5], total_receivables=total_receivables,
        payables=payables[:5], total_payables=total_payables,
        recent_trips=recent_trips, vehicles=vehicles_list,
        f_date_from=date_from, f_date_to=date_to, f_vehicle=vehicle_f, f_fy=f_fy,
        maint_by_category=maint_by_category, exp_breakdown=exp_breakdown, vehicle_revenue=vehicle_revenue,
        fleet_running_pct=fleet_running_pct, top_vehicles=top_vehicles, alerts=alerts,
        freight_trend=freight_trend, trend_max=trend_max, freight_chart_bottom=freight_chart_bottom,
        freight_y_ticks=freight_y_ticks, trip_status=trip_status, activities=activities,
        overdue_receivables=overdue_receivables, overdue_payables=overdue_payables, active='dashboard')

@app.route('/vehicles/compliance/sync-all', methods=['POST'])
def vehicles_compliance_sync_all():
    """Syncs every own-fleet vehicle's Fitness/PUC/Permit against the mock providers, then
    goes straight back to the existing All Vehicles tab — no separate page for this."""
    conn = get_db()
    summary = cs.sync_all_vehicles(conn)
    conn.close()
    return redirect(url_for('vehicles_list', tab='all', synced=summary['synced'],
                             changed=summary['changed'], failed=summary['failed']))

@app.route('/vehicles/<int:v_id>/rc-lookup')
def vehicle_rc_lookup(v_id):
    """Read-only RC detail view — reads whatever the last automatic sync cached into
    vehicles.rc_synced_data/rc_last_synced. Does NOT call the live eChallan API on click
    anymore; the API is only ever called by the scheduled 15-day sync job (see
    scheduler.py's run_rc_sync + providers/echallan_client.py), so opening this view is
    always instant and free. If nothing has been synced yet, this shows that honestly
    rather than fabricating data or silently hitting the API."""
    conn = get_db()
    v = conn.execute("SELECT id, vehicle_no, rc_synced_data, rc_last_synced FROM vehicles WHERE id=?", (v_id,)).fetchone()
    conn.close()
    if not v:
        return redirect(url_for('vehicles_list'))
    if v['rc_synced_data']:
        try:
            result = {'ok': True, 'error': None, 'data': json.loads(v['rc_synced_data'])}
        except ValueError:
            result = {'ok': False, 'error': 'Cached RC data could not be read — it will be refreshed on the next automatic sync.', 'data': None}
    else:
        result = {'ok': False, 'error': 'Not synced yet. This vehicle will be fetched automatically on the next scheduled sync (every 15 days).', 'data': None}
    return render_template('vehicle_rc_view.html', v=v, result=result, active='vehicles', rc_last_synced=v['rc_last_synced'])

@app.route('/vehicles/<int:v_id>/rc-lookup/download')
def vehicle_rc_download(v_id):
    """Downloads the cached RC data (same source as vehicle_rc_lookup above) as a plain
    one-page PDF, rather than relying on the browser's print-to-PDF dialog."""
    conn = get_db()
    v = conn.execute("SELECT id, vehicle_no, rc_synced_data, rc_last_synced FROM vehicles WHERE id=?", (v_id,)).fetchone()
    conn.close()
    if not v or not v['rc_synced_data']:
        return redirect(url_for('vehicle_rc_lookup', v_id=v_id))
    try:
        data = json.loads(v['rc_synced_data'])
    except ValueError:
        return redirect(url_for('vehicle_rc_lookup', v_id=v_id))
    return _build_rc_pdf(v, data)

def _build_rc_pdf(v, d):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    from xml.sax.saxutils import escape as esc
    import io

    conn = get_db()
    s = _get_invoice_settings(conn, session.get('company_id', 1))
    conn.close()
    company_name = s['company_name'] or get_company_name(session.get('company_id', 1))
    company_addr_line = s['address'] or ''
    company_contact_line = ' &nbsp;|&nbsp; '.join(x for x in [
        (f"GSTIN No.: {esc(s['gstin'])}" if s['gstin'] else ''),
        (f"Mob. {esc(s['phone'])}" if s['phone'] else '')] if x)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.35*inch, bottomMargin=0.35*inch,
                             leftMargin=0.5*inch, rightMargin=0.5*inch)
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=14, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=7.6, textColor=colors.HexColor('#5A6B8C'), alignment=1)
    title_style = ParagraphStyle('T', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#1B2A4A'), spaceAfter=0)
    src_style = ParagraphStyle('SRC', parent=styles['Normal'], fontSize=7.4, textColor=colors.HexColor('#5A6B8C'))
    sec_style = ParagraphStyle('SEC', parent=styles['Normal'], fontSize=8.2, textColor=colors.white, fontName='Helvetica-Bold')
    lbl_style = ParagraphStyle('LB', parent=styles['Normal'], fontSize=6.4, textColor=colors.HexColor('#5A6B8C'), leading=7.6)
    val_style = ParagraphStyle('VA', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#1A1A1A'), fontName='Helvetica-Bold', leading=9.6)
    foot_style = ParagraphStyle('FT', parent=styles['Normal'], fontSize=6.8, textColor=colors.HexColor('#8A93A6'), alignment=1)

    def g(key, suffix='', default='—'):
        raw = d.get(key)
        if raw in (None, '', 'null'):
            return default
        return f"{raw}{suffix}"

    def fld(label, value):
        return [Paragraph(esc(label.upper()), lbl_style), Paragraph(esc(str(value)), val_style)]

    CONTENT_W = 7.5 * inch
    cell_w = CONTENT_W / 4

    def section(title, pairs):
        hdr = Table([[Paragraph(esc(title.upper()), sec_style)]], colWidths=[CONTENT_W])
        hdr.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#1B2A4A')),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ]))
        grid_rows = []
        for i in range(0, len(pairs), 2):
            row = fld(*pairs[i])
            row += fld(*pairs[i + 1]) if i + 1 < len(pairs) else ['', '']
            grid_rows.append(row)
        grid = Table(grid_rows, colWidths=[cell_w * 0.85, cell_w * 1.15, cell_w * 0.85, cell_w * 1.15])
        grid.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, colors.HexColor('#E2E6EE')),
        ]))
        return [hdr, grid, Spacer(1, 6)]

    story = [
        Paragraph(esc(company_name), company_style),
        Paragraph(esc(company_addr_line), sub_style),
        Paragraph(company_contact_line, sub_style),
        Spacer(1, 5),
    ]
    line = Table([['']], colWidths=[CONTENT_W], rowHeights=[2])
    line.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 1.4, colors.HexColor('#1B2A4A'))]))
    story.append(line)
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"RC Details &nbsp;—&nbsp; {esc(v['vehicle_no'])}", title_style))
    if v['rc_last_synced']:
        story.append(Paragraph(f"Source: eChallan Vehicle RC Lookup API &nbsp;|&nbsp; Synced: {esc(v['rc_last_synced'])}", src_style))
    story.append(Spacer(1, 8))

    story += section('Registration & Status', [
        ('Registration No.', g('rc_regn_no')), ('Status', g('rc_status')),
        ('Status As On', g('rc_status_as_on')), ('Registration Date', g('rc_regn_dt')),
        ('Registration Valid Upto', g('rc_regn_upto')), ('RTO Code', g('rto_cd')),
        ('State', g('state_cd')), ('Blacklist Status', g('rc_blacklist_status', default='None')),
    ])
    story += section('Owner & Financer', [
        ('Owner Name', g('rc_owner_name')), ('Owner Serial No.', g('rc_owner_sr')),
        ('Owner Category', g('rc_owner_cd_desc')), ('Ownership Type', g('rc_own_catg_desc')),
        ('Financer', g('rc_financer', default='None')), ('Owner Address', g('rc_present_address')),
        ('Permanent Address', g('rc_permanent_address')),
    ])
    story += section('Vehicle Specifications', [
        ('Maker', g('rc_maker_desc')), ('Model', g('rc_maker_model')),
        ('Manufactured', g('rc_manu_month_yr')), ('Body Type', g('rc_body_type_desc')),
        ('Colour', g('rc_color')), ('Fuel Type', g('rc_fuel_desc')),
        ('Cubic Capacity', g('rc_cubic_cap', ' cc')), ('No. of Cylinders', g('rc_no_cyl')),
        ('Seating Capacity', g('rc_seat_cap')), ('Sleeper Capacity', g('rc_sleeper_cap')),
        ('Standing Capacity', g('rc_stand_cap')), ('Unladen Weight', g('rc_unld_wt', ' kg')),
        ('Gross Vehicle Weight', g('rc_gvw', ' kg')), ('Wheelbase', g('rc_wheelbase', ' mm')),
        ('No. of Axles', g('rc_no_of_axle')), ('Vehicle Class', g('rc_vh_class_desc')),
        ('Vehicle Category', g('rc_vch_catg_desc')), ('Emission Norms', g('rc_norms_desc')),
    ])
    story += section('Compliance Dates', [
        ('Fitness Valid Upto', g('rc_fit_upto')), ('Insurance Valid Upto', g('rc_insurance_upto')),
        ('Insurance Company', g('rc_insurance_comp')), ('PUC Valid Upto', g('rc_pucc_upto')),
        ('PUC No.', g('rc_pucc_no')), ('Road Tax Valid Upto', g('rc_tax_upto')),
    ])
    story += section('Permit Details', [
        ('Permit No.', g('rc_permit_no')), ('Permit Type', g('rc_permit_type')),
        ('Issuing Authority', g('rc_permit_issuing_authority')), ('Valid From', g('rc_permit_valid_from')),
        ('Valid Upto', g('rc_permit_valid_upto')), ('National Permit Upto', g('rc_np_upto')),
    ])
    story.append(Paragraph("This information is provided by the VAHAN/eChallan system. Please verify for accuracy.", foot_style))

    doc.build(story)
    buf.seek(0)
    safe_no = "".join(c for c in (v['vehicle_no'] or 'vehicle') if c.isalnum() or c in " _-")[:30]
    return send_file(buf, as_attachment=True, download_name=f'RC_{safe_no}.pdf', mimetype='application/pdf')

@app.route('/vehicles/<int:v_id>/challans')
def vehicle_challan_detail(v_id):
    """Old standalone Challan Details panel — superseded by the eChallan tab on the vehicle
    detail page (see vehicle_detail() + its 'echallan' tab). Kept as a redirect so any
    old bookmark/link still lands somewhere useful."""
    return redirect(url_for('vehicle_detail', v_id=v_id, tab='echallan'))

def _rc_month_year(value):
    """eChallan's rc_manu_month_yr comes as 'M/YYYY' (e.g. '2/2023') — just the year for the
    header's 'Year' field, since that's the only part actually meaningful there."""
    if not value or '/' not in value:
        return None
    return value.split('/')[-1]

def _vehicle_detail_data(conn, v_id):
    """Shared by vehicle_detail() (website, full 6-tab page with add/edit forms) and
    app_vehicle_detail() (mobile, read-only summary + Challans/History) — every real field/row
    either page needs about one vehicle, computed once so the two can never disagree. Returns
    None if the vehicle doesn't exist. Every tab reads straight from tables already populated
    elsewhere in the app (vehicle_compliance, insurance_policies, vehicle_challans, the eChallan
    RC cache) — never calls a live API itself; only the "Sync Latest Data" button does, same
    cache-then-read pattern as the rest of this integration."""
    v = conn.execute("SELECT * FROM vehicles WHERE id=?", (v_id,)).fetchone()
    if not v:
        return None

    rc_data = json.loads(v['rc_synced_data']) if v['rc_synced_data'] else None
    age_years = _vehicle_age_years(v['registration_date'])
    body_type_short = _short_body_type(v['body_type'] or (rc_data.get('rc_body_type_desc') if rc_data else None))
    body_type_icon = _body_type_icon(body_type_short)

    comp_row = None
    if v['type'] == VEHICLE_TYPE_OWN:
        comp_row = cs.refresh_compliance(conn)
        comp_row = next((c for c in comp_row if c['vehicle_id'] == v_id), None)

    # paid_amount lives on the linked maintenance row, not insurance_policies itself -- joined in
    # here so the Insurance form on this page can prefill/show it, same reasoning as Urea's
    # paid_amount join elsewhere.
    insurance_rows = conn.execute("""SELECT ip.*, ve.name as insurer_name, m2.paid_amount as paid_amount
                                     FROM insurance_policies ip
                                     LEFT JOIN vendors ve ON ip.insurer_id=ve.id
                                     LEFT JOIN maintenance m2 ON ip.maintenance_id=m2.id
                                     WHERE ip.vehicle_id=? ORDER BY COALESCE(ip.expiry_date,'') DESC, ip.id DESC""", (v_id,)).fetchall()
    # "Active" = the one whose expiry is furthest out / most current — insurance_rows is already
    # sorted that way, so the first row (if any) is the one to show as the current policy.
    active_insurance = insurance_rows[0] if insurance_rows else None

    challan_rows = conn.execute("SELECT * FROM vehicle_challans WHERE vehicle_id=? ORDER BY challan_date_time DESC", (v_id,)).fetchall()
    challans = []
    for c in challan_rows:
        d = dict(c)
        date_iso, time_str = parse_challan_date(d.get('challan_date_time'))
        d['date_display'] = date_iso or (d.get('challan_date_time') or '—')
        d['time_display'] = time_str
        try:
            d['offence_list'] = json.loads(d['offence_details']) if d.get('offence_details') else []
        except ValueError:
            d['offence_list'] = []
        challans.append(d)
    challan_pending_amount = sum(c['fine_imposed'] or 0 for c in challans if (c['challan_status'] or '').lower() == 'pending')
    challan_paid_amount = sum(c['fine_imposed'] or 0 for c in challans if (c['challan_status'] or '').lower() != 'pending')
    challan_pending_n = sum(1 for c in challans if (c['challan_status'] or '').lower() == 'pending')
    challan_paid_n = len(challans) - challan_pending_n

    # Upcoming Reminders — every tracked expiry with a real date, soonest first.
    reminders = []
    if comp_row:
        for ctype, label in [('insurance', 'Insurance Expiry'), ('permit', 'Permit Expiry'),
                              ('fitness', 'Fitness Expiry'), ('puc', 'PUC Expiry')]:
            info = comp_row[ctype]
            if info['expiry'] and info['days_left'] is not None:
                reminders.append({'label': label, 'expiry': info['expiry'], 'days_left': info['days_left']})
        reminders.sort(key=lambda r: r['days_left'])

    # History — a real, factual timeline from whatever timestamps this app actually has;
    # nothing invented. Insurance policy start/end dates + each compliance type's last sync +
    # the RC cache's own last-synced time.
    history = []
    for ip in insurance_rows:
        if ip['start_date']:
            history.append({'date': ip['start_date'], 'text': f"Insurance policy started — {ip['insurer_name'] or 'Unknown insurer'} ({ip['policy_number'] or 'no policy no.'})"})
        if ip['expiry_date']:
            history.append({'date': ip['expiry_date'], 'text': f"Insurance policy {'expires' if ip['expiry_date'] >= datetime.date.today().isoformat() else 'expired'} — {ip['insurer_name'] or 'Unknown insurer'}"})
    if comp_row:
        for ctype, label in [('fitness', 'Fitness'), ('puc', 'PUC'), ('permit', 'Permit')]:
            info = comp_row[ctype]
            if info.get('last_sync_time'):
                history.append({'date': info['last_sync_time'][:10], 'text': f"{label} record synced ({info.get('sync_status') or 'Synced'})"})
    if v['rc_last_synced']:
        history.append({'date': v['rc_last_synced'][:10], 'text': 'eChallan RC data synced'})
    history.sort(key=lambda h: h['date'], reverse=True)

    company_name_row = conn.execute("SELECT value FROM settings WHERE key='company_name'").fetchone()
    company_name = company_name_row['value'] if company_name_row else 'Anil Transport Service'

    return {'v': v, 'rc_data': rc_data, 'age_years': age_years,
            'comp_row': comp_row, 'insurance_rows': insurance_rows, 'active_insurance': active_insurance,
            'challans': challans, 'challan_pending_amount': challan_pending_amount, 'challan_paid_amount': challan_paid_amount,
            'challan_pending_n': challan_pending_n, 'challan_paid_n': challan_paid_n,
            'reminders': reminders, 'history': history, 'body_type_icon': body_type_icon,
            'company_name': company_name, 'rc_year': _rc_month_year(rc_data.get('rc_manu_month_yr')) if rc_data else None,
            'today_str': datetime.date.today().isoformat()}

@app.route('/vehicles/<int:v_id>')
def vehicle_detail(v_id):
    """Full per-vehicle detail page — everything about one vehicle in one place, replacing the
    old modal-based row view. See _vehicle_detail_data() above for what's computed; this route
    just adds the tab param and renders."""
    conn = get_db()
    data = _vehicle_detail_data(conn, v_id)
    if data is None:
        conn.close()
        return redirect(url_for('vehicles_list'))
    conn.close()

    tab = request.args.get('tab', 'insurance')
    if tab not in ('insurance', 'permit', 'fitness', 'puc', 'echallan', 'history'):
        tab = 'insurance'
    return render_template('vehicle_detail.html', tab=tab, active='vehicles', **data)

@app.route('/vehicles/<int:v_id>/sync-now', methods=['POST'])
def vehicle_sync_now(v_id):
    """The detail page's single "Sync Latest Data" button — a real, on-demand, one-vehicle
    call that fetches BOTH RC and eChallan data together (previously two separate buttons/
    routes — "Sync Latest Data" for RC and "Sync eChallan Data" for challans — merged into
    one, since a user asking to refresh a vehicle means everything about that vehicle, not
    just half of it). Reuses the exact same backfill/replace logic the scheduled 15-day RC
    job and 3-day challan job use (scheduler.py), so a manual sync leaves the data in an
    identical shape to a scheduled one, and updates every screen that reads it (RC modal,
    Insurance/Fitness/Permit/PUC tabs, eChallan tab, the vehicles list, nav warnings).
    Only ever touches THIS vehicle — never loops over the fleet. Hired vehicles don't carry
    RC/challan data at all (see the "vehicle type = own" rule elsewhere in this file), so
    the route no-ops for them even if called directly."""
    from scheduler import _backfill_from_rc
    from providers.echallan_client import fetch_challans
    conn = get_db()
    v = conn.execute("SELECT id, vehicle_no, type FROM vehicles WHERE id=?", (v_id,)).fetchone()
    if not v:
        conn.close()
        return redirect(url_for('vehicles_list'))
    return_to = request.form.get('return_to') or url_for('vehicle_detail', v_id=v_id)
    if v['type'] == VEHICLE_TYPE_HIRED:
        conn.close()
        return redirect(return_to)
    key_row = conn.execute("SELECT value FROM settings WHERE key='rc_lookup_api_key'").fetchone()
    api_key = key_row['value'] if key_row else ''
    if api_key:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rc_result = fetch_rc(v['vehicle_no'], api_key)
        if rc_result['ok']:
            conn.execute("UPDATE vehicles SET rc_synced_data=?, rc_last_synced=?, updated_by=?, updated_at=? WHERE id=?",
                         (json.dumps(rc_result['data']), now, session.get('user_id'), now, v_id))
            _backfill_from_rc(conn, v_id, v['vehicle_no'], rc_result['data'], now)
            conn.commit()

        challan_result = fetch_challans(v['vehicle_no'], api_key)
        if challan_result['ok']:
            data = challan_result['data']
            challans = data.get('challans') or []
            conn.execute("DELETE FROM vehicle_challans WHERE vehicle_id=?", (v_id,))
            pending_amount = 0.0
            for c in challans:
                fine = c.get('fine_imposed')
                try:
                    fine_val = float(fine) if fine not in (None, '') else None
                except (TypeError, ValueError):
                    fine_val = None
                if (c.get('challan_status') or '').lower() == 'pending' and fine_val:
                    pending_amount += fine_val
                conn.execute("""INSERT INTO vehicle_challans
                    (vehicle_id, api_id, challan_no, challan_date_time, challan_place, challan_status,
                     fine_imposed, amount_of_fine_imposed, department, driver_name, name_of_violator,
                     owner_name, dl_no, document_impounded, remark, rto_distric_name, state_code,
                     court_name, court_address, date_of_proceeding, sent_to_court_on, sent_to_reg_court,
                     sent_to_virtual_court, offence_details, source_created_at, source_updated_at, last_synced,
                     created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (v_id, c.get('_id'), c.get('challan_no'), c.get('challan_date_time'), c.get('challan_place'),
                     c.get('challan_status'), fine_val, c.get('amount_of_fine_imposed'), c.get('department'),
                     c.get('driver_name'), c.get('name_of_violator'), c.get('owner_name'), c.get('dl_no'),
                     c.get('document_impounded'), c.get('remark'), c.get('rto_distric_name'), c.get('state_code'),
                     c.get('court_name'), c.get('court_address'), c.get('date_of_proceeding'),
                     c.get('sent_to_court_on'), c.get('sent_to_reg_court'), c.get('sent_to_virtual_court'),
                     json.dumps(c.get('offence_details') or []), c.get('createdAt'), c.get('updatedAt'), now,
                     session.get('user_id'), now))
            pending_count = data.get('pending_count', sum(1 for c in challans if (c.get('challan_status') or '').lower() == 'pending'))
            conn.execute("UPDATE vehicles SET challan_count=?, challan_amount=?, challan_last_synced=?, updated_by=?, updated_at=? WHERE id=?",
                         (pending_count, pending_amount, now, session.get('user_id'), now, v_id))
            conn.commit()
    conn.close()
    return redirect(return_to)

def _rc_cache_ref(vehicle_row, field):
    """Pulls one field out of a vehicle's cached eChallan RC data (vehicles.rc_synced_data),
    for the read-only "eChallan reference" line shown on the Insurance and Permit & Fitness
    tabs. Never the authoritative value — those tabs' own tables (insurance_policies /
    vehicle_compliance) stay the real source; this is a cross-check display, not a second
    place edits are made. Returns None if nothing has synced yet."""
    raw = vehicle_row['rc_synced_data'] if vehicle_row else None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data.get(field)

@app.route('/vehicles')
def vehicles_list():
    # The old Insurance / Permit & Fitness sub-tabs on this list page are gone — that detail
    # now lives on the per-vehicle detail page (see vehicle_detail() below). Any bookmarked
    # link to the old tabs just falls through to the plain list.
    #
    # The sidebar's "Vehicles" link is a plain /vehicles with no query string at all — sort and
    # every filter live only in the URL, so switching to another tab and clicking back in used to
    # silently reset everything to defaults. A completely bare hit (no query string whatsoever)
    # now restores whatever sort/filter was last used on this page instead. Any real query string
    # — even just the "Clear" link's own ?tab=all — is respected exactly as given and never
    # overridden, so Clear (and every sort/filter link, which always carries the others forward)
    # keeps working exactly as before.
    if not request.query_string and session.get('vehicles_last_query'):
        return redirect(f"/vehicles?{session['vehicles_last_query']}")
    conn = get_db()
    type_f = request.args.get('type', '')
    status_f = request.args.get('status', '')
    vehicle_f = request.args.get('vehicle', '')
    age_f = request.args.get('age', '')
    query = """SELECT v.id, v.vehicle_no, v.type, v.registration_date, v.capacity_mt,
               v.insurance_expiry, v.fitness_expiry, v.puc_valid_upto, v.permit_valid_upto,
               v.status, v.body_type, v.notes, v.chassis_number, v.engine_number,
               v.rc_last_synced, v.rc_synced_data, v.challan_count, v.challan_amount,
               (SELECT COUNT(*) FROM trips WHERE vehicle_id=v.id) as trip_count,
               (SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE vehicle_id=v.id) as total_billed
               FROM vehicles v WHERE v.type IS NOT NULL"""
    params = []
    if type_f:
        query += " AND v.type = ?"
        params.append(type_f)
    if status_f:
        query += " AND COALESCE(v.status,'Active') = ?"
        params.append(status_f)
    if vehicle_f:
        query += " AND v.vehicle_no = ?"
        params.append(vehicle_f)
    query += " ORDER BY v.type, v.vehicle_no"
    all_rows_raw = conn.execute(query, params).fetchall()

    # Age is computed, never stored — attach it to a plain dict per row so the template/filter
    # can use it like any other column. Age-bucket filter applied here (post-query) since it's
    # derived, not a real SQL column.
    all_rows = []
    for r in all_rows_raw:
        d = dict(r)
        d['age_years'] = _vehicle_age_years(d['registration_date'])
        if age_f == 'under3' and not (d['age_years'] is not None and d['age_years'] < 3):
            continue
        if age_f == '3to6' and not (d['age_years'] is not None and 3 <= d['age_years'] < 6):
            continue
        if age_f == 'over6' and not (d['age_years'] is not None and d['age_years'] >= 6):
            continue
        # Road Tax — not a separately-tracked table like Insurance/Fitness/PUC/Permit; read
        # straight from the eChallan RC cache (rc_tax_upto), same as the Age column reads
        # registration_date. No cache yet -> shown as "No Data", same convention as the other
        # compliance columns for an un-synced vehicle.
        road_tax_upto = None
        rc_body_type = None
        if d.get('rc_synced_data'):
            try:
                rc_data = json.loads(d['rc_synced_data'])
                road_tax_upto = parse_rc_date(rc_data.get('rc_tax_upto'))
                rc_body_type = rc_data.get('rc_body_type_desc')
            except ValueError:
                road_tax_upto = None
        d['road_tax_upto'] = road_tax_upto
        # Manual entry (body_type column) wins if set; otherwise fall back to whatever the last
        # RC sync cached. Both go through the same shortener so the list always shows a short
        # word ("Trailer"/"Truck"), never the raw verbose RC string.
        d['body_type_short'] = _short_body_type(d.get('body_type') or rc_body_type)
        d['body_type_color'] = _body_type_color(d['body_type_short'])
        d['body_type_icon'] = _body_type_icon(d['body_type_short'])
        d['road_tax_status'] = {'ok': 'Valid', 'expiring': 'Expiring Soon', 'expired': 'Expired', None: 'Unknown'}[_expiry_bucket(road_tax_upto)]
        d['road_tax_days_left'] = (datetime.datetime.strptime(road_tax_upto, '%Y-%m-%d').date() - datetime.date.today()).days if road_tax_upto else None
        all_rows.append(d)

    # Column-header sorting (Age / Challan) — both computed/derived fields, so sorted here in
    # Python rather than in SQL. Rows with no age on file always sort to the bottom regardless
    # of direction, since "unknown" isn't meaningfully "oldest" or "youngest".
    sort_f = request.args.get('sort', '')
    dir_f = request.args.get('dir', 'asc')
    if sort_f == 'age':
        known = [r for r in all_rows if r['age_years'] is not None]
        unknown = [r for r in all_rows if r['age_years'] is None]
        known.sort(key=lambda r: r['age_years'], reverse=(dir_f == 'desc'))
        all_rows = known + unknown
    elif sort_f == 'challan':
        all_rows.sort(key=lambda r: (r['challan_amount'] or 0), reverse=(dir_f == 'desc'))

    # Real, vehicle-level compliance snapshot for the overview cards + sidebar — every count
    # below is derived straight from the (filtered) vehicles table, never fabricated.
    total_vehicles = len(all_rows)
    active_count = sum(1 for r in all_rows if (r['status'] or 'Active') == 'Active')
    maint_count = sum(1 for r in all_rows if r['status'] == 'In Maintenance')
    inactive_count = sum(1 for r in all_rows if r['status'] == 'Inactive')

    def _bucket_counts(field):
        expired = expiring = 0
        for r in all_rows:
            b = _expiry_bucket(r[field])
            if b == 'expired':
                expired += 1
            elif b == 'expiring':
                expiring += 1
        return expired, expiring

    ins_expired, ins_expiring = _bucket_counts('insurance_expiry')
    puc_expired, puc_expiring = _bucket_counts('puc_valid_upto')
    permit_expired, permit_expiring = _bucket_counts('permit_valid_upto')
    fit_expired, fit_expiring = _bucket_counts('fitness_expiry')
    compliance_overview = [
        {'label': 'Insurance', 'expired': ins_expired, 'expiring': ins_expiring},
        {'label': 'PUC', 'expired': puc_expired, 'expiring': puc_expiring},
        {'label': 'Fitness', 'expired': fit_expired, 'expiring': fit_expiring},
        {'label': 'Permits', 'expired': permit_expired, 'expiring': permit_expiring},
    ]

    body_type_counts = {}
    for r in all_rows:
        bt = r['body_type'] or 'Unspecified'
        body_type_counts[bt] = body_type_counts.get(bt, 0) + 1
    body_type_dist = sorted(body_type_counts.items(), key=lambda kv: -kv[1])

    # Challan alert strip — real counts from vehicles.challan_count/challan_amount (populated by
    # the 15-day auto-sync, see scheduler.py's run_rc_sync). Zero/empty on every vehicle until
    # that sync has actually run at least once — the banner below only renders when > 0, so a
    # fresh install never shows a fabricated "pending challans" claim.
    challan_vehicles_count = sum(1 for r in all_rows if (r['challan_count'] or 0) > 0)
    challan_total_amount = sum(r['challan_amount'] or 0 for r in all_rows if (r['challan_count'] or 0) > 0)

    total_count = len(all_rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = all_rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    all_vehicle_nos = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active' ORDER BY vehicle_no").fetchall()

    # Compliance badges (Insurance/Fitness/PUC/Permit) — own fleet only, see compliance_service's
    # module docstring for why Hired vehicles are excluded. Keyed by vehicle_id for O(1) lookup
    # in the template; a Hired vehicle simply has no entry and renders "—" for every badge.
    compliance_by_vehicle = {c['vehicle_id']: c for c in cs.refresh_compliance(conn)}

    conn.close()
    # Remember this exact URL for next time — a later bare /vehicles hit (see the redirect at the
    # top of this route) restores it instead of resetting to defaults.
    if request.query_string:
        session['vehicles_last_query'] = request.query_string.decode('utf-8')
    return render_template('vehicles_list.html', tab='all', rows=page_rows, f_type=type_f, f_status=status_f,
                            f_vehicle=vehicle_f, f_age=age_f, f_sort=sort_f, f_dir=dir_f, all_vehicle_nos=all_vehicle_nos, active='vehicles',
                            total_vehicles=total_vehicles, active_count=active_count, maint_count=maint_count,
                            inactive_count=inactive_count, ins_expiring=ins_expiring, puc_expiring=puc_expiring,
                            permit_expiring=permit_expiring, fit_expiring=fit_expiring,
                            compliance_overview=compliance_overview, body_type_dist=body_type_dist,
                            compliance_by_vehicle=compliance_by_vehicle,
                            challan_vehicles_count=challan_vehicles_count, challan_total_amount=challan_total_amount,
                            page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs)

EMPLOYEE_AVATAR_PALETTE = [('#eaf1fb', '#2a78d6'), ('#fff3e8', 'var(--accent)'), ('#e8f5ee', 'var(--green)'),
                           ('rgba(74,58,167,0.1)', '#4a3aa7'), ('#fdecea', 'var(--red)')]

def _employee_avatar_colors(seed):
    return EMPLOYEE_AVATAR_PALETTE[(seed or 0) % len(EMPLOYEE_AVATAR_PALETTE)]

def _employee_initials(name):
    parts = (name or '').split()
    if not parts:
        return '??'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()

def _employee_outstanding_advance(conn, name):
    """given - repaid, PLUS any carried-over opening balance — same formula
    `_employee_ledger_entries()`'s running advance_balance uses (with no date filter), so this
    number always matches what the employee's own Ledger page shows as 'Advance outstanding'."""
    given = conn.execute("SELECT COALESCE(SUM(amount),0) FROM advances WHERE employee=? COLLATE NOCASE AND type='given'", (name,)).fetchone()[0]
    repaid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM advances WHERE employee=? COLLATE NOCASE AND type='repaid'", (name,)).fetchone()[0]
    emp_row = conn.execute("SELECT opening_balance FROM employees WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    opening_balance = (emp_row['opening_balance'] or 0) if emp_row else 0
    return opening_balance + given - repaid

def _employee_attendance_stats(conn, employee_id, date_from, date_to):
    """(present, absent, leave, half_day, working_days, pct) for one employee over an arbitrary
    date range (not just a calendar month) — working_days is every day that got a status marked
    at all (Present/Absent/Leave/Half Day) within the range, not the range's full length, since
    not every day is necessarily marked yet."""
    rows = conn.execute("SELECT status FROM attendance WHERE employee_id=? AND date>=? AND date<=?",
                        (employee_id, date_from, date_to)).fetchall()
    present = sum(1 for r in rows if r['status'] == 'Present')
    absent = sum(1 for r in rows if r['status'] == 'Absent')
    leave = sum(1 for r in rows if r['status'] == 'Leave')
    half = sum(1 for r in rows if r['status'] == 'Half Day')
    working = len(rows)
    pct = round((present + 0.5 * half) / working * 100, 1) if working else None
    return {'present': present, 'absent': absent, 'leave': leave, 'half_day': half,
            'working_days': working, 'pct': pct}

def _employee_month_calendar(conn, employee_id, month_key):
    """Weeks of 7 day-cells (Mon-Sun, blank-padded) for the attendance drawer's calendar grid,
    each cell tagged with a one-letter status class the template maps to a colour: p/a/l/h/n."""
    year, month = int(month_key[:4]), int(month_key[5:7])
    first = datetime.date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    status_by_day = {r['date']: r for r in conn.execute(
        "SELECT date, status, in_time, out_time FROM attendance WHERE employee_id=? AND substr(date,1,7)=?",
        (employee_id, month_key)).fetchall()}
    class_map = {'Present': 'p', 'Absent': 'a', 'Leave': 'l', 'Half Day': 'h'}
    letter_map = {'Present': 'P', 'Absent': 'A', 'Leave': 'L', 'Half Day': 'HD'}
    weeks = []
    week = [None] * first.weekday()  # Monday=0
    for d in range(1, days_in_month + 1):
        date_str = f"{year:04d}-{month:02d}-{d:02d}"
        rec = status_by_day.get(date_str)
        week.append({'day': d, 'date': date_str,
                      'cls': class_map.get(rec['status'], 'n') if rec else 'n',
                      'letter': letter_map.get(rec['status'], '') if rec else '',
                      'status': rec['status'] if rec else None,
                      'in_time': rec['in_time'] if rec else None, 'out_time': rec['out_time'] if rec else None})
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append(None)
        weeks.append(week)
    return weeks

@app.route('/salaries')
def salaries_list():
    conn = get_db()
    tab = request.args.get('tab', 'overview')
    if tab == 'salary':
        return _employees_salary_tab(conn)
    if tab == 'attendance':
        return _employees_attendance_tab(conn)
    return _employees_overview_tab(conn)

def _employees_overview_tab(conn):
    default_from = datetime.date.today().replace(day=1).isoformat()
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or datetime.date.today().isoformat()
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    today = datetime.date.today().isoformat()

    employees = _salary_visible_employees(conn)
    total_employees = len(employees)
    drivers = sum(1 for e in employees if (e['type'] or '') == 'Driver')
    office_staff = total_employees - drivers

    today_rows = {r['employee_id']: r['status'] for r in
                  conn.execute("SELECT employee_id, status FROM attendance WHERE date=?", (today,)).fetchall()}
    _sids = _salary_scope_ids(conn)
    if _sids is not None:
        today_rows = {k: v for k, v in today_rows.items() if k in _sids}
    present_today = sum(1 for s in today_rows.values() if s == 'Present')
    on_leave_today = sum(1 for s in today_rows.values() if s == 'Leave')

    period_pcts = []
    salary_status_by_emp = {}
    latest_salary_by_emp = {}
    for e in employees:
        stats = _employee_attendance_stats(conn, e['id'], date_from, date_to)
        if stats['pct'] is not None:
            period_pcts.append(stats['pct'])
        sal_rows = conn.execute("SELECT net_salary FROM salaries WHERE employee_id=? AND date IS NOT NULL AND date>=? AND date<=?",
                                (e['id'], date_from, date_to)).fetchall()
        salary_status_by_emp[e['id']] = 'paid' if sal_rows else None
        latest_salary_by_emp[e['id']] = sum(s['net_salary'] or 0 for s in sal_rows)
    overall_attendance_pct = round(sum(period_pcts) / len(period_pcts), 1) if period_pcts else 0

    period_sal = conn.execute(f"""SELECT COALESCE(SUM(net_salary),0), COALESCE(SUM(CASE WHEN payment_status='paid' THEN net_salary ELSE 0 END),0)
                                 FROM salaries WHERE date IS NOT NULL AND date>=? AND date<=?
                                 {'' if _sids is None else 'AND employee_id IN ' + _salary_ids_sql(_sids)}""", (date_from, date_to)).fetchone()
    total_payroll = period_sal[0] or 0
    paid_payroll = period_sal[1] or 0
    pending_payroll = total_payroll - paid_payroll
    total_outstanding_advances = sum(_employee_outstanding_advance(conn, e['name']) for e in employees)

    # Department distribution + Drivers vs Office Staff share the same underlying split, shown two ways.
    dept_counts = {}
    for e in employees:
        d = e['role'] or e['type'] or 'Other'
        dept_counts[d] = dept_counts.get(d, 0) + 1
    dept_dist = sorted([{'label': k, 'count': v} for k, v in dept_counts.items()], key=lambda x: -x['count'])
    dept_max = max([d['count'] for d in dept_dist], default=1) or 1

    # Salary distribution — net salary paid per employee within the selected period, top 6.
    sal_dist = sorted([{'name': e['name'], 'amount': latest_salary_by_emp.get(e['id']) or 0} for e in employees
                        if latest_salary_by_emp.get(e['id'])], key=lambda x: -x['amount'])[:6]
    sal_dist_max = max([s['amount'] for s in sal_dist], default=1) or 1

    # 6-month attendance trend (fleet average %, per calendar month — independent of the
    # date_from/date_to filter above, which is a fixed trailing trend not a range selector).
    trend = []
    y_, m_ = datetime.date.today().year, datetime.date.today().month
    for i in range(5, -1, -1):
        mm = m_ - i; yy = y_
        while mm <= 0:
            mm += 12; yy -= 1
        mf, mt = _month_bounds(yy, mm)
        pcts = [p for e in employees if (p := _employee_attendance_stats(conn, e['id'], mf, mt)['pct']) is not None]
        trend.append({'label': calendar.month_abbr[mm], 'pct': round(sum(pcts) / len(pcts), 1) if pcts else 0})
    trend_max = max([t['pct'] for t in trend], default=1) or 1

    rows = []
    for e in employees:
        stats = _employee_attendance_stats(conn, e['id'], date_from, date_to)
        rows.append({
            'id': e['id'], 'name': e['name'], 'employee_code': e['employee_code'] or f"EMP-{e['id']:03d}",
            'role': e['role'] or e['type'] or '—', 'joining_date': e['joining_date'] or '—',
            'attendance_pct': stats['pct'], 'salary_status': salary_status_by_emp.get(e['id']),
            'net_salary': latest_salary_by_emp.get(e['id']),
            'outstanding': _employee_outstanding_advance(conn, e['name']),
            'status': e['status'] or 'Active',
            'initials': _employee_initials(e['name']), 'avatar_colors': _employee_avatar_colors(e['id']),
            # Full record, for the Edit Employee modal (pre-filled from this same row).
            'type': e['type'], 'raw_role': e['role'], 'mobile': e['mobile'], 'email': e['email'],
            'address': e['address'], 'date_of_birth': e['date_of_birth'], 'bank_account': e['bank_account'],
            'ifsc_code': e['ifsc_code'], 'upi_id': e['upi_id'], 'emergency_contact': e['emergency_contact'],
            'aadhaar': e['aadhaar'], 'pan': e['pan'], 'driving_license': e['driving_license'],
            'basic_salary_raw': e['basic_salary'] or 0,
        })
        if _sids is not None:        # restricted viewer: salary figures yes, personal / bank / ID details no
            for k in ('mobile', 'email', 'address', 'date_of_birth', 'bank_account', 'ifsc_code', 'upi_id', 'emergency_contact', 'aadhaar', 'pan', 'driving_license'):
                rows[-1][k] = None

    total_count = len(rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(5, 10, 25, 50), default_per_page=5)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None); base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    conn.close()
    return render_template('employees.html', tab='overview',
        total_employees=total_employees, drivers=drivers, office_staff=office_staff,
        present_today=present_today, on_leave_today=on_leave_today, overall_attendance_pct=overall_attendance_pct,
        total_payroll=total_payroll, paid_payroll=paid_payroll, pending_payroll=pending_payroll,
        total_outstanding_advances=total_outstanding_advances,
        dept_dist=dept_dist, dept_max=dept_max, sal_dist=sal_dist, sal_dist_max=sal_dist_max,
        trend=trend, trend_max=trend_max, drivers_pct=round(drivers/total_employees*100,1) if total_employees else 0,
        rows=page_rows, total_count=total_count, page=page, total_pages=total_pages, per_page=per_page,
        page_tokens=page_tokens, base_qs=base_qs, f_date_from=date_from, f_date_to=date_to, active='salaries')

def _employees_salary_data(conn):
    """Real filters (from request.args) + real filtered rows + real KPI stats for the Salary tab
    — shared by _employees_salary_tab() (website) and app_salary() (mobile). date_from/date_to/
    role/search are all the website's own real filters. Returns a dict; does not close conn."""
    default_from = datetime.date.today().replace(day=1).isoformat()
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or datetime.date.today().isoformat()
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    role_f = request.args.get('role', '')
    search_f = request.args.get('search', '')

    employees = _salary_visible_employees(conn)
    if role_f:
        employees = [e for e in employees if (e['role'] or e['type']) == role_f]
    if search_f:
        s = search_f.lower()
        employees = [e for e in employees if s in (e['name'] or '').lower() or s in (e['employee_code'] or '').lower()]

    rows = []
    for e in employees:
        sal_rows = conn.execute("""SELECT net_salary, date FROM salaries
                                   WHERE employee_id=? AND date IS NOT NULL AND date>=? AND date<=? ORDER BY date DESC""",
                                (e['id'], date_from, date_to)).fetchall()
        rows.append({
            'id': e['id'], 'name': e['name'], 'employee_code': e['employee_code'] or f"EMP-{e['id']:03d}",
            'role': e['role'] or e['type'] or '—', 'total_paid': sum(s['net_salary'] or 0 for s in sal_rows),
            'payment_count': len(sal_rows), 'last_paid_date': sal_rows[0]['date'] if sal_rows else None,
            'outstanding': _employee_outstanding_advance(conn, e['name']),
            'initials': _employee_initials(e['name']), 'avatar_colors': _employee_avatar_colors(e['id']),
        })

    total_payroll = sum(r['total_paid'] for r in rows)
    employees_paid = sum(1 for r in rows if r['payment_count'] > 0)
    total_outstanding_advances = sum(r['outstanding'] for r in rows)
    avg_salary = round(total_payroll / employees_paid, 0) if employees_paid else 0

    roles = sorted(set((e['role'] or e['type'] or '') for e in _salary_visible_employees(conn) if (e['role'] or e['type'])))

    return {'rows': rows, 'total_count': len(rows), 'total_payroll': total_payroll,
            'employees_paid': employees_paid, 'total_employees_all': len(employees),
            'avg_salary': avg_salary, 'total_outstanding_advances': total_outstanding_advances,
            'roles': roles, 'f_date_from': date_from, 'f_date_to': date_to, 'f_role': role_f, 'f_search': search_f}

def _employees_salary_tab(conn):
    """Salary is a plain summary/overview now — pay salaries, give advances, and record
    repayments all happen on the classic per-employee Ledger page (/employee/<name>), the same
    proven flow this always used. This tab just reports what happened in the selected period."""
    d = _employees_salary_data(conn)
    rows, total_count = d['rows'], d['total_count']

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(5, 10, 25, 50), default_per_page=5)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None); base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    conn.close()
    return render_template('employees.html', tab='salary',
        rows=page_rows, total_count=total_count, total_payroll=d['total_payroll'],
        employees_paid=d['employees_paid'], total_employees_all=d['total_employees_all'],
        avg_salary=d['avg_salary'], total_outstanding_advances=d['total_outstanding_advances'],
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], roles=d['roles'], f_role=d['f_role'], f_search=d['f_search'],
        active='salaries')

def _employees_attendance_tab(conn):
    default_from = datetime.date.today().replace(day=1).isoformat()
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or datetime.date.today().isoformat()
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    role_f = request.args.get('role', '')
    status_f = request.args.get('status', '')
    search_f = request.args.get('search', '')
    today = datetime.date.today().isoformat()

    employees = _salary_visible_employees(conn)
    if role_f:
        employees = [e for e in employees if (e['role'] or e['type']) == role_f]
    if search_f:
        s = search_f.lower()
        employees = [e for e in employees if s in (e['name'] or '').lower() or s in (e['employee_code'] or '').lower()]

    today_rows = {r['employee_id']: r['status'] for r in
                  conn.execute("SELECT employee_id, status FROM attendance WHERE date=?", (today,)).fetchall()}
    _sids = _salary_scope_ids(conn)
    if _sids is not None:
        today_rows = {k: v for k, v in today_rows.items() if k in _sids}
    present_kpi = sum(1 for s in today_rows.values() if s == 'Present')
    absent_kpi = sum(1 for s in today_rows.values() if s == 'Absent')
    leave_kpi = sum(1 for s in today_rows.values() if s == 'Leave')
    half_kpi = sum(1 for s in today_rows.values() if s == 'Half Day')
    # "Late" isn't its own status (Mark Attendance only offers Present/Absent/Leave/Half Day) — it's
    # a Present row whose logged in_time is after this cutoff, a simple fixed threshold rather than
    # a per-employee shift schedule this app doesn't model.
    LATE_CUTOFF = '09:30'
    late_kpi = conn.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present' AND in_time IS NOT NULL AND in_time > ?"
                            + ('' if _sids is None else ' AND employee_id IN ' + _salary_ids_sql(_sids)),
                            (today, LATE_CUTOFF)).fetchone()[0]

    rows = []
    all_pcts = []
    for e in employees:
        stats = _employee_attendance_stats(conn, e['id'], date_from, date_to)
        if stats['pct'] is not None:
            all_pcts.append(stats['pct'])
        band = ('Good' if stats['pct'] is None or stats['pct'] >= 95 else
                'Average' if stats['pct'] >= 85 else str(int(stats['pct'])) + '%' if stats['pct'] is not None else '—')
        row = {'id': e['id'], 'name': e['name'], 'employee_code': e['employee_code'] or f"EMP-{e['id']:03d}",
               'role': e['role'] or e['type'] or '—', 'initials': _employee_initials(e['name']),
               'avatar_colors': _employee_avatar_colors(e['id']), 'today_status': today_rows.get(e['id']), **stats, 'band': band}
        if status_f and row['today_status'] != status_f:
            continue
        rows.append(row)
    overall_pct = round(sum(all_pcts) / len(all_pcts), 1) if all_pcts else 0

    total_count = len(rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(6, 10, 25, 50), default_per_page=6)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None); base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    roles = sorted(set((e['role'] or e['type'] or '') for e in _salary_visible_employees(conn) if (e['role'] or e['type'])))
    all_employees_list = [{'id': e['id'], 'name': e['name'], 'employee_code': e['employee_code'] or f"EMP-{e['id']:03d}"} for e in employees]
    # The drawer's calendar is a fixed month grid — it can't render an arbitrary multi-month range,
    # so it shows the calendar month containing the end of the selected period.
    calendar_month_key = date_to[:7]

    for r in page_rows:
        r['calendar'] = _employee_month_calendar(conn, r['id'], calendar_month_key)
        r['recent_history'] = conn.execute(
            "SELECT date, status, in_time, out_time, remarks FROM attendance WHERE employee_id=? ORDER BY date DESC LIMIT 10",
            (r['id'],)).fetchall()

    conn.close()
    return render_template('employees.html', tab='attendance',
        rows=page_rows, total_count=total_count, present_kpi=present_kpi, absent_kpi=absent_kpi,
        leave_kpi=leave_kpi, half_kpi=half_kpi, late_kpi=late_kpi, overall_pct=overall_pct,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        f_date_from=date_from, f_date_to=date_to, calendar_month_key=calendar_month_key,
        roles=roles, f_role=role_f, f_status=status_f, f_search=search_f,
        all_employees_list=all_employees_list, today=today, active='salaries')

# Icon/color per category — matched by keyword against whatever free-text category the user
# typed (this app never restricts categories to a fixed list), falling back to a plain
# "receipt" icon for anything unrecognized. icon key matches an <svg> defined once in
# overheads_list.html rather than duplicating markup per row.
_EXPENSE_CATEGORY_ICONS = [
    (('rent',), 'building', '#eaf1fb', '#2a78d6'),
    (('electric', 'power'), 'bolt', '#fff6e8', '#eda100'),
    (('internet', 'wifi', 'broadband'), 'wifi', '#eaf1fb', '#2a78d6'),
    (('supplies', 'stationery'), 'box', '#e8f5ee', '#1a9c5b'),
    (('software', 'subscription', 'saas', 'zoho', 'license'), 'monitor', '#f1ecfb', '#7a5ad6'),
    (('fuel', 'diesel', 'petrol', 'generator'), 'fuel', '#fdecea', '#e34948'),
    (('tea', 'snack', 'pantry', 'food'), 'cup', '#fff6e8', '#eda100'),
    (('bank', 'emi', 'loan', 'interest', 'charges'), 'bank', '#eceff3', '#5a6478'),
    (('toll',), 'road', '#eaf1fb', '#2a78d6'),
    (('travel', 'conveyance', 'taxi'), 'car', '#f1ecfb', '#7a5ad6'),
    (('phone', 'mobile', 'telephone'), 'phone', '#eaf1fb', '#2a78d6'),
]
_EXPENSE_ICON_DEFAULT = ('receipt', '#eceff3', '#5a6478')
EXPENSE_PALETTE = ['#2a78d6', '#1a9c5b', '#7a5ad6', '#eda100', '#e34948', '#17a2b8', '#c05621', '#5a6478']


def _expense_category_visual(cat):
    c = (cat or '').lower()
    for keys, icon, bg, color in _EXPENSE_CATEGORY_ICONS:
        if any(k in c for k in keys):
            return icon, bg, color
    return _EXPENSE_ICON_DEFAULT


def _conic_gradient(segments):
    """segments: [(label, amount, color), ...]. Returns (css_gradient_string, legend) where
    legend adds each segment's % share — same conic-gradient-donut technique already used on
    Dashboard/Route Analytics, just generalized here from 2 fixed segments to N dynamic ones."""
    total = sum(s[1] for s in segments) or 1
    stops = []
    legend = []
    cum = 0.0
    for label, amt, color in segments:
        start = cum / total * 100
        cum += amt
        end = cum / total * 100
        stops.append(f"{color} {start:.2f}% {end:.2f}%")
        legend.append({'label': label, 'amount': amt, 'pct': round(amt / total * 100, 1), 'color': color})
    gradient = 'conic-gradient(' + ', '.join(stops) + ')' if any(s[1] > 0 for s in segments) else 'conic-gradient(var(--paper) 0% 100%)'
    return gradient, legend


def _overhead_list_data(conn):
    """Real filters (from request.args) + real filtered rows + real KPI stats for the Expenses
    page — shared by overheads_list() (website, full page with donuts/trend/vendor+category
    summary tabs) and app_expenses() (mobile, KPIs + list only). category/vendor/status/date_from/
    date_to are the website's own real filters; search is new (the website itself has no search
    box on this page) — added here as a plain substring match over category/vendor/description,
    same "layer a search convenience on top of otherwise-real filters" call already made for
    Ledger, so it's additive for the website (a query string it never sends) rather than a
    behavior change. Returns a dict; does not close conn."""
    tab = request.args.get('tab', 'all')
    cat_f = request.args.get('category', '')
    vendor_f = request.args.get('vendor', '')
    status_f = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    search_f = request.args.get('search', '')

    query = """SELECT id, date, category, amount, notes, payment_mode, receipt_number,
               vendor, description, COALESCE(status,'Paid') as status, COALESCE(is_recurring,0) as is_recurring,
               recurring_frequency, due_date FROM overheads WHERE 1=1"""
    params = []
    if cat_f:
        query += " AND category = ?"
        params.append(cat_f)
    if vendor_f:
        query += " AND vendor = ?"
        params.append(vendor_f)
    if status_f:
        query += " AND COALESCE(status,'Paid') = ?"
        params.append(status_f)
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    if tab == 'recurring':
        query += " AND COALESCE(is_recurring,0) = 1"
    query += " ORDER BY date DESC, id DESC"
    raw_rows = conn.execute(query, params).fetchall()

    today = datetime.date.today()
    rows = []
    for r in raw_rows:
        d = dict(r)
        icon, bg, color = _expense_category_visual(d['category'])
        d['icon'], d['icon_bg'], d['icon_color'] = icon, bg, color
        days_left = None
        if d['status'] == 'Pending' and (d['due_date'] or d['date']):
            try:
                dd = datetime.datetime.strptime(d['due_date'] or d['date'], '%Y-%m-%d').date()
                days_left = (dd - today).days
            except ValueError:
                days_left = None
        d['days_left'] = days_left
        rows.append(d)

    if search_f:
        s = search_f.lower()
        rows = [r for r in rows if s in (r['category'] or '').lower() or s in (r['vendor'] or '').lower()
                or s in (r['description'] or '').lower()]

    categories = conn.execute("SELECT DISTINCT category FROM overheads WHERE category IS NOT NULL AND category != '' ORDER BY category").fetchall()
    # Real vendors table, not "whatever vendor name has already been typed into an overhead
    # entry" — the old query (SELECT DISTINCT vendor FROM overheads) returns nothing at all until
    # at least one overhead has a vendor on it, which is exactly why this field read as broken:
    # none of this company's real overhead rows have one yet. This is the same "suggest from the
    # real table, not from your own history" fix already applied to Tyres/Battery/Compliance's
    # own vendor fields — shared by overheads_list() (website) and app_expenses() (mobile), so
    # both get real suggestions, not an empty datalist, the moment this lands.
    vendors = conn.execute("SELECT name AS vendor FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()

    all_rows = conn.execute("""SELECT o.date, o.amount, o.category, o.vendor, o.payment_mode, COALESCE(o.status,'Paid') as status,
                               COALESCE(o.is_recurring,0) as is_recurring, o.recurring_frequency, o.receipt_number, o.due_date,
                               COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.overhead_id=o.id),0) AS allocated
                               FROM overheads o""").fetchall()

    total_amount = sum(r['amount'] or 0 for r in all_rows)
    total_count = len(all_rows)

    this_month_key = today.strftime('%Y-%m')
    this_month_amount = sum(r['amount'] or 0 for r in all_rows if (r['date'] or '')[:7] == this_month_key)

    pending_rows_all = [r for r in all_rows if r['status'] == 'Pending' and (r['amount'] or 0) - r['allocated'] > 0.004]
    # still unpaid = bill amount minus what ledger payments already covered (part-paid bills count their remainder)
    pending_amount = sum((r['amount'] or 0) - r['allocated'] for r in pending_rows_all)
    pending_count = len(pending_rows_all)

    recurring_rows = [r for r in all_rows if r['is_recurring']]
    recurring_monthly_amount = sum(r['amount'] or 0 for r in recurring_rows if (r['recurring_frequency'] or 'Monthly') == 'Monthly')
    recurring_count = len(recurring_rows)

    # Last 6 calendar months (this month + previous 5), for the running average.
    month_keys = []
    y, m = today.year, today.month
    for _ in range(6):
        month_keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    last6_amount = sum(r['amount'] or 0 for r in all_rows if (r['date'] or '')[:7] in month_keys)
    avg_monthly_expense = last6_amount / 6

    active_categories = len(categories)

    return {'rows': rows, 'categories': categories, 'vendors': vendors,
            'total_amount': total_amount, 'total_count': total_count, 'this_month_amount': this_month_amount,
            'pending_amount': pending_amount, 'pending_count': pending_count,
            'recurring_monthly_amount': recurring_monthly_amount, 'recurring_count': recurring_count,
            'avg_monthly_expense': avg_monthly_expense, 'active_categories': active_categories,
            'f_category': cat_f, 'f_vendor': vendor_f, 'f_status': status_f,
            'f_date_from': date_from, 'f_date_to': date_to, 'f_search': search_f}

@app.route('/overheads')
def overheads_list():
    conn = get_db()
    d = _overhead_list_data(conn)
    rows, categories, vendors = d['rows'], d['categories'], d['vendors']
    total_amount, total_count, this_month_amount = d['total_amount'], d['total_count'], d['this_month_amount']
    pending_amount, pending_count = d['pending_amount'], d['pending_count']
    recurring_monthly_amount, recurring_count = d['recurring_monthly_amount'], d['recurring_count']
    avg_monthly_expense, active_categories = d['avg_monthly_expense'], d['active_categories']
    cat_f, vendor_f, status_f, date_from, date_to = d['f_category'], d['f_vendor'], d['f_status'], d['f_date_from'], d['f_date_to']

    tab = request.args.get('tab', 'all')
    today = datetime.date.today()
    this_month_key = today.strftime('%Y-%m')
    raw_rows = rows  # kept unfiltered-by-search since the website itself never sends ?search=
    all_rows = conn.execute("""SELECT o.date, o.amount, o.category, o.vendor, o.payment_mode, COALESCE(o.status,'Paid') as status,
                               COALESCE(o.is_recurring,0) as is_recurring, o.recurring_frequency, o.receipt_number, o.due_date,
                               COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.overhead_id=o.id),0) AS allocated
                               FROM overheads o""").fetchall()
    pending_rows_all = [r for r in all_rows if r['status'] == 'Pending' and (r['amount'] or 0) - r['allocated'] > 0.004]

    # Upcoming Payments — pending bills, soonest due date first.
    upcoming = []
    for r in pending_rows_all:
        due = r['due_date'] or r['date']
        try:
            dd = datetime.datetime.strptime(due, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            continue
        icon, bg, color = _expense_category_visual(r['category'])
        upcoming.append({'category': r['category'], 'vendor': r['vendor'], 'amount': (r['amount'] or 0) - r['allocated'],
                          'due_date': due, 'days_left': (dd - today).days,
                          'icon': icon, 'icon_bg': bg, 'icon_color': color})
    upcoming.sort(key=lambda u: u['days_left'])

    # Top Expense Categories (this month) — donut + legend, top 4 + "Others".
    month_rows = [r for r in all_rows if (r['date'] or '')[:7] == this_month_key]
    cat_totals = {}
    for r in month_rows:
        key = r['category'] or 'Uncategorized'
        cat_totals[key] = cat_totals.get(key, 0) + (r['amount'] or 0)
    cat_sorted = sorted(cat_totals.items(), key=lambda kv: -kv[1])
    cat_segments = [(label, amt, EXPENSE_PALETTE[i % len(EXPENSE_PALETTE)]) for i, (label, amt) in enumerate(cat_sorted[:4])]
    if len(cat_sorted) > 4:
        others_amt = sum(amt for _, amt in cat_sorted[4:])
        cat_segments.append(('Others', others_amt, '#c9cfdc'))
    top_categories_gradient, top_categories_legend = _conic_gradient(cat_segments)
    top_categories_total = sum(amt for _, amt in cat_sorted)

    # Recent Bills / Attachments — most recent entries that have a receipt/invoice number on file.
    recent_bills = [r for r in raw_rows if r['receipt_number']][:3]

    # Expense Trend — monthly totals for the current calendar year.
    year = today.year
    trend_totals = {f"{year:04d}-{mo:02d}": 0.0 for mo in range(1, 13)}
    for r in all_rows:
        key = (r['date'] or '')[:7]
        if key in trend_totals:
            trend_totals[key] += (r['amount'] or 0)
    month_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    trend_points = [{'label': month_labels[i], 'amount': trend_totals[f"{year:04d}-{i+1:02d}"]} for i in range(12)]

    # Expense by Status — donut, Paid vs Pending.
    paid_amount = total_amount - pending_amount
    status_segments = [('Paid', paid_amount, '#1a9c5b'), ('Pending', pending_amount, '#eda100')]
    status_gradient, status_legend = _conic_gradient(status_segments)

    # Vendor Summary / Category Summary tabs — simple aggregates, same all-time rows.
    vendor_summary = {}
    for r in all_rows:
        key = r['vendor'] or 'Unspecified'
        if key not in vendor_summary:
            vendor_summary[key] = {'vendor': key, 'amount': 0, 'count': 0, 'last_date': None}
        vendor_summary[key]['amount'] += (r['amount'] or 0)
        vendor_summary[key]['count'] += 1
        if not vendor_summary[key]['last_date'] or (r['date'] or '') > vendor_summary[key]['last_date']:
            vendor_summary[key]['last_date'] = r['date']
    vendor_summary_rows = sorted(vendor_summary.values(), key=lambda v: -v['amount'])

    category_summary = {}
    for r in all_rows:
        key = r['category'] or 'Uncategorized'
        if key not in category_summary:
            icon, bg, color = _expense_category_visual(key)
            category_summary[key] = {'category': key, 'amount': 0, 'count': 0, 'icon': icon, 'icon_bg': bg, 'icon_color': color}
        category_summary[key]['amount'] += (r['amount'] or 0)
        category_summary[key]['count'] += 1
    category_summary_rows = sorted(category_summary.values(), key=lambda c: -c['amount'])
    for c in category_summary_rows:
        c['pct'] = round(c['amount'] / total_amount * 100, 1) if total_amount else 0

    total_count_filtered = len(rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count_filtered,
                                             per_page_options=(8, 25, 50, 100), default_per_page=8)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    conn.close()
    return render_template(
        'overheads_list.html', rows=page_rows, categories=categories, vendors=vendors,
        tab=tab, f_category=cat_f, f_vendor=vendor_f, f_status=status_f,
        f_date_from=date_from, f_date_to=date_to,
        total_amount=total_amount, total_count=total_count, this_month_amount=this_month_amount,
        pending_amount=pending_amount, pending_count=pending_count,
        recurring_monthly_amount=recurring_monthly_amount, recurring_count=recurring_count,
        avg_monthly_expense=avg_monthly_expense, active_categories=active_categories,
        upcoming=upcoming[:4], top_categories_gradient=top_categories_gradient,
        top_categories_legend=top_categories_legend, top_categories_total=top_categories_total,
        recent_bills=recent_bills, trend_points=trend_points,
        status_gradient=status_gradient, status_legend=status_legend,
        vendor_summary_rows=vendor_summary_rows, category_summary_rows=category_summary_rows,
        page=page, per_page=per_page, total_pages=total_pages, page_tokens=page_tokens, base_qs=base_qs,
        total_count_filtered=total_count_filtered,
        current_year=today.year, today_str=today.isoformat(), month_labels_full=today.strftime('%b %Y'),
        active='overheads')

@app.route('/overheads/export')
def export_overheads():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    cat_f = request.args.get('category', '')
    vendor_f = request.args.get('vendor', '')
    mode_f = request.args.get('payment_mode', '')
    status_f = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    tab = request.args.get('tab', 'all')
    query = """SELECT date, category, vendor, description, payment_mode, COALESCE(status,'Paid') as status,
               amount, receipt_number, COALESCE(is_recurring,0) as is_recurring, recurring_frequency
               FROM overheads WHERE 1=1"""
    params = []
    if cat_f: query += " AND category = ?"; params.append(cat_f)
    if vendor_f: query += " AND vendor = ?"; params.append(vendor_f)
    if mode_f: query += " AND payment_mode = ?"; params.append(mode_f)
    if status_f: query += " AND COALESCE(status,'Paid') = ?"; params.append(status_f)
    if date_from: query += " AND date >= ?"; params.append(date_from)
    if date_to: query += " AND date <= ?"; params.append(date_to)
    if tab == 'recurring': query += " AND COALESCE(is_recurring,0) = 1"
    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    headers = ["Date", "Category", "Vendor", "Description", "Payment Mode", "Status", "Amount",
               "Receipt / Invoice No.", "Recurring", "Frequency"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, r in enumerate(rows, 2):
        ws.cell(row=r_idx, column=1, value=r['date'])
        ws.cell(row=r_idx, column=2, value=r['category'])
        ws.cell(row=r_idx, column=3, value=r['vendor'])
        ws.cell(row=r_idx, column=4, value=r['description'])
        ws.cell(row=r_idx, column=5, value=r['payment_mode'])
        ws.cell(row=r_idx, column=6, value=r['status'])
        ws.cell(row=r_idx, column=7, value=r['amount'])
        ws.cell(row=r_idx, column=8, value=r['receipt_number'])
        ws.cell(row=r_idx, column=9, value='Yes' if r['is_recurring'] else 'No')
        ws.cell(row=r_idx, column=10, value=r['recurring_frequency'])
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='expenses_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/trips')
def trips_list():
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status_f = request.args.get('status', '')
    lr_f = request.args.get('lr_received', '')
    lr_number_f = request.args.get('lr_number', '')
    from_f = request.args.get('from_loc', '')
    to_f = request.args.get('to_loc', '')
    type_f = request.args.get('type', '')
    sort_f = request.args.get('sort', '')
    dir_f = request.args.get('dir', 'desc')

    query = """SELECT t.id, t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received, t.type,
               t.payment_received, t.party_advance,
               t.end_date, t.end_time, t.actual_km, t.shortage_qty, t.shortage_unit, t.shortage_amount, t.shortage_remarks, t.remarks
               FROM trips t
               LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id
               WHERE 1=1"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no LIKE ?"
        params.append(f"%{vehicle_f}%")
    if party_f:
        query += " AND p.name LIKE ?"
        params.append(f"%{party_f}%")
    if date_from:
        query += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND t.date <= ?"
        params.append(date_to)
    if status_f == 'active':
        query += " AND t.end_date IS NULL"
    elif status_f == 'completed':
        query += " AND t.end_date IS NOT NULL"
    if lr_f == 'received':
        query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received':
        query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    if lr_number_f:
        query += " AND t.lr_number LIKE ?"
        params.append(f"%{lr_number_f}%")
    if from_f:
        query += " AND t.from_loc LIKE ?"
        params.append(f"%{from_f}%")
    if to_f:
        query += " AND t.to_loc LIKE ?"
        params.append(f"%{to_f}%")
    if type_f:
        query += " AND t.type = ?"
        params.append(type_f)

    where_clause = query[query.find("WHERE"):]

    total_shown = conn.execute(f"SELECT COALESCE(SUM(t.billed_amount),0) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    total_count = conn.execute(f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    pending_total = conn.execute(f"SELECT COALESCE(SUM(t.billed_amount-COALESCE(t.payment_received,0)-COALESCE(t.party_advance,0)),0) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause}", params).fetchone()[0]
    active_count = conn.execute(f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause} AND t.end_date IS NULL", params).fetchone()[0]
    lr_received_count = conn.execute(f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id {where_clause} AND t.lr_received='Yes'", params).fetchone()[0]
    lr_pending_count = total_count - lr_received_count

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=50)
    offset = (page - 1) * per_page

    # Whitelisted column map — sort_f/dir_f come straight from the query string, so they're
    # never interpolated directly into SQL; only the vetted values on the right ever reach it.
    sort_columns = {'date': 't.date', 'party': 'p.name'}
    order_col = sort_columns.get(sort_f, 't.date')
    order_dir = 'ASC' if dir_f == 'asc' else 'DESC'
    query += f" ORDER BY {order_col} {order_dir}, t.date DESC LIMIT {per_page} OFFSET {offset}"

    trips = conn.execute(query, params).fetchall()
    if _hide_trip_amounts():
        trips = [_mask_trip_dict(r) for r in trips]
        total_shown = pending_total = 0
    vehicles = conn.execute("""SELECT DISTINCT vehicle_no FROM vehicles
                               WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active'
                               ORDER BY vehicle_no""").fetchall()
    parties = conn.execute("SELECT DISTINCT name FROM parties WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    conn.close()

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)
    page_tokens = _page_tokens(page, total_pages)

    # Same filters, minus sort/dir — sort-column links in the template build off this so
    # clicking "Date" or "Party" toggles just the sort, keeping every active filter intact.
    filter_params = dict(base_params)
    filter_params.pop('sort', None)
    filter_params.pop('dir', None)
    filter_qs = urlencode(filter_params)

    return render_template('trips_list.html', trips=trips, total_shown=total_shown, total_count=total_count,
                            filter_qs=filter_qs,
                            pending_total=pending_total, active_count=active_count,
                            lr_received_count=lr_received_count, lr_pending_count=lr_pending_count,
                            page=page, total_pages=total_pages, per_page=per_page, base_qs=base_qs, page_tokens=page_tokens,
                            vehicles=vehicles, parties=parties,
                            f_vehicle=vehicle_f, f_party=party_f, f_date_from=date_from, f_date_to=date_to,
                            f_status=status_f, f_lr=lr_f, f_lr_number=lr_number_f, f_from=from_f, f_to=to_f, f_type=type_f,
                            f_sort=sort_f, f_dir=dir_f,
                            end_error=request.args.get('end_error', ''), delete_error=request.args.get('error', ''), delete_error_amount=request.args.get('amount', ''),
                            delete_error_lr=request.args.get('lr', ''), delete_error_toll_count=request.args.get('toll_count', ''),
                            active='trips')

def _duplicate_lr_trip(conn, lr_number, exclude_trip_id=None):
    """Real-world LR numbers are serially-unique documents (Lorry Receipt) — two trips sharing
    one is a data-integrity problem (duplicate billing, reconciliation confusion), not a
    legitimate case like two parties sharing a name. Returns the conflicting trip row
    (id, date, vehicle_no) if lr_number is already used by another trip, else None. Shared by
    add_trip()/app_add_trip() (exclude_trip_id=None) and edit_trip()/app_edit_trip()
    (exclude_trip_id=that trip's own id, so re-saving a trip without changing its LR Number
    doesn't trip over itself) — same check, same exclusion rule, everywhere."""
    lr_number = (lr_number or '').strip()
    if not lr_number:
        return None
    query = """SELECT t.id, t.date, v.vehicle_no FROM trips t
              LEFT JOIN vehicles v ON t.vehicle_id=v.id
              WHERE t.lr_number = ? COLLATE NOCASE AND TRIM(t.lr_number) != ''"""
    params = [lr_number]
    if exclude_trip_id is not None:
        query += " AND t.id != ?"
        params.append(exclude_trip_id)
    return conn.execute(query, params).fetchone()

def _trip_freight(quantity, rate, rate_type, fixed_rate_amount, guarantee_qty=0):
    """Party-side freight for one trip — the single formula every place in the app that bills a
    trip's party (insert, update, invoice preview/PDF, invoice batch line items) calls, so a
    Guarantee Quantity (Minimum Guarantee Weight) trip is billed identically no matter which page
    shows it. PER_MT bills off guarantee_qty whenever one is filled in (non-zero) on the trip,
    regardless of whether it's higher or lower than the actual quantity moved — trips only ever
    get a guarantee_qty typed in when it actually applies; when the party is only paying actual
    this time (even below what the guarantee would have covered), the field is simply left blank,
    which is exactly what makes this fall through to plain quantity * rate below. guarantee_qty
    defaults to 0 (the schema default, and every trip that existed before this feature) so this is
    a strict no-op for those. FIXED-rate trips ignore quantity/guarantee entirely, same as always —
    a flat amount doesn't depend on what was loaded.

    Deliberately NOT applied to owner-side freight (what the vehicle owner is paid) — the owner is
    paid for what they actually moved; the guarantee premium is the transporter's own margin for
    having arranged the capacity, not something owed on to the owner."""
    if rate_type == 'FIXED':
        return fixed_rate_amount or 0
    billed_qty = guarantee_qty if guarantee_qty else quantity
    return (billed_qty or 0) * (rate or 0)

_TRIP_MONEY_FIELDS = (
    'quantity', 'guarantee_qty', 'rate', 'fixed_rate_amount', 'detention_charges', 'gps_cost', 'loading_charge',
    'unloading_charge', 'police_charges', 'sim_tracking', 'union_charges', 'weight_charges', 'other_charges',
    'brokerage', 'builty_commission', 'late_fees', 'material_damage', 'shortage_amount', 'shortage_qty', 'tds',
    'other_deductions', 'fuel_amount', 'fuel_price', 'fuel_liters', 'driver_adv_amount', 'party_advance',
    'payment_received', 'owner_rate', 'owner_fixed_amount', 'paid_to_owner', 'agent_commission', 'builty_expense',
    'fine', 'labour_charges', 'parking', 'puncture', 'toll', 'urea', 'loading_expense', 'unloading_expense',
    'weighbridge_charges', 'other_expense')
_TRIP_FIELD_LABELS = {'tds': 'TDS', 'gps_cost': 'GPS cost', 'sim_tracking': 'SIM tracking', 'fuel_liters': 'Fuel liters'}

def _trip_form_error(f, check_advance=True):
    """Server-side check of a submitted trip form: every amount/quantity must be a real,
    non-negative number (so 'abc' / '-10' / NaN can't crash the page or save a nonsense bill), the
    computed bill can't be negative, and (on a NEW trip) the party advance can't exceed the bill — an
    existing trip may legitimately end up with more received than billed after a rate/shortage
    correction, so edits don't enforce that. Returns an error
    message, or None when the form is fine."""
    for key in _TRIP_MONEY_FIELDS:
        _v, err = _parse_money(f.get(key), _TRIP_FIELD_LABELS.get(key, key.replace('_', ' ').capitalize()))
        if err:
            return err

    def n(key):             # fields were validated above; use the exact typed value (quantity 7.333 must not be rounded to 7.33)
        try:
            return float((f.get(key) or '0').replace(',', '').strip() or 0)
        except ValueError:
            return 0.0
    freight = _trip_freight(n('quantity'), n('rate'), f.get('rate_type'), n('fixed_rate_amount'), n('guarantee_qty'))
    charges = sum(n(k) for k in ('detention_charges', 'gps_cost', 'loading_charge', 'unloading_charge', 'police_charges',
                                  'sim_tracking', 'union_charges', 'weight_charges', 'other_charges'))
    deductions = sum(n(k) for k in ('brokerage', 'builty_commission', 'late_fees', 'material_damage', 'shortage_amount',
                                     'tds', 'other_deductions'))
    billed = freight + charges - deductions
    if billed < -0.005:
        return 'Deductions are more than the freight — the trip bill would be negative.'
    if check_advance and n('party_advance') > billed + 0.011:      # a paisa of slack: a bill of 4526.505 rounds its 100% advance up to 4526.51
        return 'Party advance cannot be more than the trip bill.'
    return None

def _insert_trip_from_form(conn, f):
    """Builds and inserts one trips row from a submitted form — same field list, same computed
    billed_amount, same get_or_create_*/vendor resolution used by both add_trip() (website) and
    app_add_trip() (mobile app), extracted here so the two can never quietly compute a different
    billed_amount or accept a different set of fields (exactly the kind of drift
    get_or_create_vehicle above was already extracted to avoid). Caller must run
    _duplicate_lr_trip() first — this function always inserts, unconditionally. Returns the new
    trip's id; does not commit or close conn."""
    def n(key):
        return float(f.get(key) or 0)

    vehicle_id = get_or_create_vehicle(conn, f.get('vehicle_no'), f.get('type'))
    trip_type = _resolve_trip_type(conn, vehicle_id, f.get('type'))
    party_id = get_or_create_party(conn, f.get('party_name'))
    fuel_vendor_id = get_or_create_vendor(conn, f.get('fuel_vendor'))
    driveradv_vendor_id = get_or_create_vendor(conn, f.get('driver_adv_vendor'))
    owner_vendor_id = get_or_create_vendor(conn, f.get('owner_name')) if f.get('owner_name') else None
    misc_vendor_id = get_or_create_vendor(conn, f.get('misc_vendor'))

    quantity = n('quantity')
    guarantee_qty = n('guarantee_qty')
    rate = n('rate')
    rate_type = f.get('rate_type')
    fixed_rate_amount = n('fixed_rate_amount')
    owner_rate_type = f.get('owner_rate_type') or 'PER_MT'
    owner_fixed_amount = n('owner_fixed_amount')

    freight = _trip_freight(quantity, rate, rate_type, fixed_rate_amount, guarantee_qty)

    # Note: driver_payment ("Driver Bata") is no longer collected from this form — Driver Advance
    # already covers driver payments, so this legacy field is left at its schema default (0) for
    # new trips instead of being asked for twice.
    total_charges = (n('detention_charges')+n('gps_cost')+n('loading_charge')+
                      n('unloading_charge')+n('police_charges')+n('sim_tracking')+n('union_charges')+
                      n('weight_charges')+n('other_charges'))
    total_deductions = (n('brokerage')+n('builty_commission')+n('late_fees')+n('material_damage')+
                         n('shortage_amount')+n('tds')+n('other_deductions'))
    billed_amount = freight + total_charges - total_deductions

    # conductor_expense/wear_tear are no longer collected from this form (dropped in favor of
    # Fuel Liters/Fuel Price below) — simply left out of the INSERT so they take their schema
    # default for every new trip, same "stop collecting, don't touch history" pattern as
    # driver_payment above.
    cols = ['date','lr_number','vehicle_id','type','party_id','from_loc','to_loc','quantity','guarantee_qty','rate',
            'driver_name','material','rate_type','billed_amount',
            'detention_charges','gps_cost','loading_charge','unloading_charge',
            'police_charges','sim_tracking','union_charges','weight_charges','other_charges',
            'brokerage','builty_commission','late_fees','material_damage','shortage_amount','shortage_qty','tds','other_deductions',
            'fuel_amount','fuel_vendor_id','fuel_liters','fuel_price','driver_adv_amount','driver_adv_vendor_id','party_advance','payment_received',
            'owner_name','fixed_rate_amount','owner_rate','owner_rate_type','owner_fixed_amount','paid_to_owner','owner_vendor_id',
            'agent_commission','builty_expense','fine','labour_charges','parking','puncture',
            'toll','urea','loading_expense','unloading_expense','weighbridge_charges','other_expense','misc_vendor_id',
            'lr_received','is_empty']
    vals = [f.get('date'), f.get('lr_number'), vehicle_id, trip_type, party_id, f.get('from_loc'), f.get('to_loc'),
            quantity, guarantee_qty, rate, f.get('driver_name'), f.get('material'), rate_type, billed_amount,
            n('detention_charges'), n('gps_cost'), n('loading_charge'), n('unloading_charge'),
            n('police_charges'), n('sim_tracking'), n('union_charges'), n('weight_charges'), n('other_charges'),
            n('brokerage'), n('builty_commission'), n('late_fees'), n('material_damage'), n('shortage_amount'),
            n('shortage_qty'), n('tds'), n('other_deductions'),
            n('fuel_amount'), fuel_vendor_id, f.get('fuel_liters') or None, n('fuel_price'), n('driver_adv_amount'), driveradv_vendor_id, n('party_advance'), n('payment_received'),
            f.get('owner_name'), fixed_rate_amount, n('owner_rate'), owner_rate_type, owner_fixed_amount, n('paid_to_owner'), owner_vendor_id,
            n('agent_commission'), n('builty_expense'), n('fine'), n('labour_charges'),
            n('parking'), n('puncture'), n('toll'), n('urea'), n('loading_expense'), n('unloading_expense'),
            n('weighbridge_charges'), n('other_expense'), misc_vendor_id,
            f.get('lr_received') or None, 1 if f.get('is_empty') else 0]
    cols.extend(['created_by', 'created_at'])
    vals.extend([session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    placeholders = ','.join('?' * len(cols))
    cur = conn.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({placeholders})", vals)
    _save_trip_custom_items(conn, cur.lastrowid, f)
    return cur.lastrowid

def _update_trip_from_form(conn, trip_id, f):
    """Builds and runs one trips UPDATE from a submitted form — the exact same real logic
    edit_trip() (website) has always used, extracted here so app_edit_trip() (mobile) can share
    it rather than risk drifting apart. Same field set as _insert_trip_from_form() above (every
    field trip_add.html's wizard actually collects, including the 12 "Other Expenses" fields —
    agent_commission through other_expense — which the wizard already has, unlike the truly-
    dropped driver_payment/conductor_expense/wear_tear, which stay untouched here exactly like
    edit_trip() already leaves them). Caller must run _duplicate_lr_trip(conn, lr, trip_id) first
    — this function always updates, unconditionally. The vehicle on a trip is locked at creation
    and never changed here, same rule edit_trip() enforces. Returns (ok, error_message_or_None);
    does not commit or close conn."""
    existing = conn.execute("SELECT vehicle_id FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not existing:
        return False, 'Trip not found.'

    def n(key):
        return float(f.get(key) or 0)

    vehicle_id = existing['vehicle_id']
    trip_type = _resolve_trip_type(conn, vehicle_id, f.get('type'))
    party_id = get_or_create_party(conn, f.get('party_name'))
    fuel_vendor_id = get_or_create_vendor(conn, f.get('fuel_vendor'))
    driveradv_vendor_id = get_or_create_vendor(conn, f.get('driver_adv_vendor'))
    misc_vendor_id = get_or_create_vendor(conn, f.get('misc_vendor'))
    owner_vendor_id = get_or_create_vendor(conn, f.get('owner_name')) if f.get('owner_name') else None

    quantity = n('quantity')
    guarantee_qty = n('guarantee_qty')
    rate = n('rate')
    rate_type = f.get('rate_type')
    fixed_rate_amount = n('fixed_rate_amount')
    owner_rate_type = f.get('owner_rate_type') or 'PER_MT'
    owner_fixed_amount = n('owner_fixed_amount')
    freight = _trip_freight(quantity, rate, rate_type, fixed_rate_amount, guarantee_qty)
    total_charges = (n('detention_charges')+n('gps_cost')+n('loading_charge')+
                      n('unloading_charge')+n('police_charges')+n('sim_tracking')+n('union_charges')+
                      n('weight_charges')+n('other_charges'))
    total_deductions = (n('brokerage')+n('builty_commission')+n('late_fees')+n('material_damage')+
                         n('shortage_amount')+n('tds')+n('other_deductions'))
    billed_amount = freight + total_charges - total_deductions

    # Same floor as edit_trip() — never let a plain re-save silently lower payment_received/
    # paid_to_owner below what a real Payment has already allocated to this trip (see the long
    # comment on edit_trip() itself for why: that desync is exactly what made a ledger's "Trip
    # Bill" line look like it was double-subtracting an already-made payment).
    received_floor = conn.execute("""SELECT COALESCE(SUM(pa.amount),0) as amt FROM payment_allocations pa
                                     JOIN payments p ON pa.payment_id=p.id
                                     WHERE pa.trip_id=? AND p.payment_type='received' AND p.party_id IS NOT NULL""",
                                  (trip_id,)).fetchone()['amt']
    paid_owner_floor = conn.execute("""SELECT COALESCE(SUM(pa.amount),0) as amt FROM payment_allocations pa
                                       JOIN payments p ON pa.payment_id=p.id
                                       WHERE pa.trip_id=? AND p.payment_type='paid' AND p.vendor_id IS NOT NULL""",
                                    (trip_id,)).fetchone()['amt']
    payment_received_val = max(n('payment_received'), received_floor)
    paid_to_owner_val = max(n('paid_to_owner'), paid_owner_floor)

    conn.execute("""UPDATE trips SET
        date=?, lr_number=?, vehicle_id=?, type=?, party_id=?, from_loc=?, to_loc=?, quantity=?, guarantee_qty=?, rate=?,
        driver_name=?, material=?, rate_type=?, billed_amount=?,
        detention_charges=?, gps_cost=?, loading_charge=?, unloading_charge=?,
        police_charges=?, sim_tracking=?, union_charges=?, weight_charges=?, other_charges=?,
        brokerage=?, builty_commission=?, late_fees=?, material_damage=?, shortage_amount=?, shortage_qty=?, tds=?, other_deductions=?,
        fuel_amount=?, fuel_liters=?, fuel_price=?, driver_adv_amount=?, party_advance=?, payment_received=?, fuel_vendor_id=?, driver_adv_vendor_id=?,
        owner_name=?, fixed_rate_amount=?, owner_rate=?, owner_rate_type=?, owner_fixed_amount=?, paid_to_owner=?, owner_vendor_id=?,
        agent_commission=?, builty_expense=?, fine=?, labour_charges=?, parking=?, puncture=?,
        toll=?, urea=?, loading_expense=?, unloading_expense=?, weighbridge_charges=?, other_expense=?, misc_vendor_id=?,
        lr_received=?, is_empty=?, updated_by=?, updated_at=?
        WHERE id=?""",
        (f.get('date'), f.get('lr_number'), vehicle_id, trip_type, party_id, f.get('from_loc'), f.get('to_loc'),
         quantity, guarantee_qty, rate, f.get('driver_name'), f.get('material'), rate_type, billed_amount,
         n('detention_charges'), n('gps_cost'), n('loading_charge'), n('unloading_charge'),
         n('police_charges'), n('sim_tracking'), n('union_charges'), n('weight_charges'), n('other_charges'),
         n('brokerage'), n('builty_commission'), n('late_fees'), n('material_damage'), n('shortage_amount'),
         n('shortage_qty'), n('tds'), n('other_deductions'),
         n('fuel_amount'), f.get('fuel_liters') or None, n('fuel_price'), n('driver_adv_amount'), n('party_advance'), payment_received_val, fuel_vendor_id, driveradv_vendor_id,
         f.get('owner_name'), fixed_rate_amount, n('owner_rate'), owner_rate_type, owner_fixed_amount, paid_to_owner_val, owner_vendor_id,
         n('agent_commission'), n('builty_expense'), n('fine'), n('labour_charges'),
         n('parking'), n('puncture'), n('toll'), n('urea'), n('loading_expense'), n('unloading_expense'),
         n('weighbridge_charges'), n('other_expense'), misc_vendor_id,
         f.get('lr_received') or None, 1 if f.get('is_empty') else 0,
         session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), trip_id))
    _save_trip_custom_items(conn, trip_id, f)
    return True, None

@app.route('/trips/add', methods=['GET', 'POST'])
def add_trip():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        dup = _duplicate_lr_trip(conn, f.get('lr_number'))
        form_error = None if dup else _trip_form_error(f)
        if dup or form_error:
            conn.close()
            vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
            conn2 = get_db()
            employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
            vehicle_type_map = _vehicle_type_map(conn2)
            conn2.close()
            error = form_error or (f"LR Number \"{(f.get('lr_number') or '').strip()}\" is already used on trip #{dup['id']} "
                                    f"({dup['date']}, {dup['vehicle_no'] or 'no vehicle'}).")
            return render_template('trip_form.html', mode='add', t=f, custom_items=[],
                                    vehicles=vehicles, parties=parties, vendors=vendors, combined_names=combined_names,
                                    employees=employees, vehicle_type_map=vehicle_type_map,
                                    return_to=f.get('return_to', ''), active='trips', error=error)
        _insert_trip_from_form(conn, f)
        conn.commit()
        conn.close()
        return redirect(url_for('trips_list'))
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn2 = get_db()
    employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
    vehicle_type_map = _vehicle_type_map(conn2)
    conn2.close()
    return render_template('trip_form.html', mode='add', t={}, custom_items=[],
                            vehicles=vehicles, parties=parties, vendors=vendors, combined_names=combined_names,
                            employees=employees, vehicle_type_map=vehicle_type_map, return_to='', active='trips')

def _save_trip_custom_items(conn, trip_id, form):
    """Replace a trip's custom "Others" line items (stored in invoice_items, the same table the
    per-trip invoice editor already uses) from the submitted other_desc/other_type/other_rate/other_qty
    rows. Amount stored is rate*qty — the invoice-facing table only keeps the final amount.
    An item can optionally be tagged with a Vendor (other_vendor) — e.g. diesel bought from a
    second fuel vendor mid-trip, or a cash advance handled by a different party — in which case it
    still appears on the trip's invoice exactly as before, but ALSO counts toward what that vendor
    is owed, the same way fuel_amount/driver_adv_amount already do (see _accounts_rows,
    _get_vendor_ledger_entries, and the matching Dashboard/Business Performance queries)."""
    conn.execute("DELETE FROM invoice_items WHERE trip_id=?", (trip_id,))
    descs = form.getlist('other_desc')
    types = form.getlist('other_type')
    rates = form.getlist('other_rate')
    qtys = form.getlist('other_qty')
    vendor_names = form.getlist('other_vendor')
    for i, desc in enumerate(descs):
        desc = (desc or '').strip()
        if not desc:
            continue
        try:
            rate = float(rates[i]) if i < len(rates) and rates[i] else 0
        except ValueError:
            rate = 0
        try:
            qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 1
        except ValueError:
            qty = 1
        item_type = types[i] if i < len(types) and types[i] in ('charge', 'deduction') else 'charge'
        vendor_name = (vendor_names[i].strip() if i < len(vendor_names) and vendor_names[i] else '')
        vendor_id = get_or_create_vendor(conn, vendor_name) if vendor_name else None
        conn.execute("""INSERT INTO invoice_items (trip_id, description, amount, item_type, vendor_id, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?)""",
                     (trip_id, desc, rate * (qty or 1), item_type, vendor_id, session.get('user_id'),
                      datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

def _save_invoice_batch_items(conn, batch_id, form):
    """Replace a generated invoice's Additional Line Items (invoice_batch_items) from the
    submitted item_desc/item_type/item_amount/item_vendor rows — same wholesale-replace pattern
    as _save_trip_custom_items above, so nothing about these items touches the database until
    the whole Edit Invoice form is actually saved. Previously Add/Delete were each their own
    immediate POST, so navigating away mid-edit could leave a stray saved item behind even
    though "Save Changes" was never clicked."""
    conn.execute("DELETE FROM invoice_batch_items WHERE invoice_batch_id=?", (batch_id,))
    descs = form.getlist('item_desc')
    types = form.getlist('item_type')
    amounts = form.getlist('item_amount')
    vendor_names = form.getlist('item_vendor')
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for i, desc in enumerate(descs):
        desc = (desc or '').strip()
        if not desc:
            continue
        amount = (_parse_money(amounts[i], 'Amount')[0] or 0) if i < len(amounts) and amounts[i] else 0
        item_type = types[i] if i < len(types) and types[i] in ('charge', 'deduction') else 'charge'
        vendor_name = (vendor_names[i].strip() if i < len(vendor_names) and vendor_names[i] else '')
        vendor_id = get_or_create_vendor(conn, vendor_name) if vendor_name else None
        conn.execute("""INSERT INTO invoice_batch_items (invoice_batch_id, description, amount, item_type, vendor_id, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?)""",
                     (batch_id, desc, amount, item_type, vendor_id, session.get('user_id'), now))

def _get_autocomplete_lists():
    conn = get_db()
    vehicles = conn.execute("""SELECT vehicle_no FROM vehicles
                               WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active'
                               ORDER BY vehicle_no""").fetchall()
    parties = conn.execute("SELECT name FROM parties WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    vendors = conn.execute("SELECT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    # A linked party+vendor pair is the same real organization — only a vendor with no linked party
    # contributes a name here beyond what the parties list already covers, so the combined
    # suggestion list never shows the same organization twice under two slightly different records.
    unlinked_vendor_names = conn.execute("""SELECT name FROM vendors
                                            WHERE linked_party_id IS NULL AND COALESCE(status,'Active')='Active'
                                            ORDER BY name""").fetchall()
    combined_names = sorted(set([p['name'] for p in parties] + [v['name'] for v in unlinked_vendor_names]))
    conn.close()
    return vehicles, parties, vendors, combined_names

def _get_route_locations(conn):
    """Real, deduplicated From/To location suggestions for the Trip form — every distinct
    non-blank value already sitting in trips.from_loc and trips.to_loc, combined into one list
    (a location that was once a "From" is exactly as valid a "To" suggestion and vice versa, same
    reasoning _get_autocomplete_lists() already applies to parties+vendors sharing one combined
    list). No new table or column: from_loc/to_loc already exist on trips and already hold every
    real location anyone has ever typed, so this is genuinely just a query over data that's
    already there — a location typed once (no matter how it was entered) is available as a
    suggestion on every trip added after it, with nothing new to maintain or keep in sync."""
    rows = conn.execute("""
        SELECT from_loc AS loc FROM trips WHERE from_loc IS NOT NULL AND TRIM(from_loc) != ''
        UNION
        SELECT to_loc AS loc FROM trips WHERE to_loc IS NOT NULL AND TRIM(to_loc) != ''
        ORDER BY loc COLLATE NOCASE
    """).fetchall()
    return [r['loc'] for r in rows]

def _vehicle_type_map(conn):
    """vehicle_no -> own/hired type code, for the Trip form's JS: selecting a vehicle that
    already exists auto-fills and locks the Type field from here instead of leaving it an
    independent, editable choice. All vehicles regardless of Active/Inactive status (unlike
    _get_autocomplete_lists' vehicle list) — editing an old trip against a now-inactive vehicle
    should still show that vehicle's real type, not fall back to an empty/editable field."""
    rows = conn.execute("SELECT vehicle_no, type FROM vehicles WHERE vehicle_no IS NOT NULL").fetchall()
    return {r['vehicle_no']: r['type'] for r in rows if r['type']}

MAINTENANCE_CHECKLIST = ['Engine Oil', 'Oil Filter', 'Air Filter', 'Diesel Filter', 'Greasing',
    'Brake Adjustment', 'Clutch Adjustment', 'Air Leak Fixed', 'Electrical Check', 'Suspension Check',
    'Wheel Alignment', 'Coolant Change', 'Battery Check', 'Lights Check', 'Road Test']

MAINTENANCE_TAB_LABELS = {'tyres': 'Tyres', 'battery': 'Battery', 'insurance': 'Insurance',
                           'permit': 'Permit & Fitness', 'urea': 'Urea'}
BATTERY_TYPES = ['Tubular', 'Flat Plate', 'Maintenance Free', 'SMF']

def _maintenance_classify(category, service_type=None):
    """Which Maintenance tab an entry belongs on. Entries created through the new Add Service form
    always carry service_type, so those are unambiguous; older free-text entries fall back to
    keyword-matching their category — this is the one place that logic lives, reused everywhere
    (Overview cards, category tabs, the Service tab itself) so they never disagree with each other."""
    if service_type:
        return 'Service'
    c = (category or '').lower()
    if 'tyre' in c or 'tire' in c or 'punt' in c or 'punc' in c:
        return 'Tyres'
    if 'battery' in c:
        return 'Battery'
    if 'insur' in c:
        return 'Insurance'
    if 'fitness' in c or 'permit' in c or 'pollution' in c or 'puc' in c:
        return 'Permit & Fitness'
    if 'urea' in c or 'adblue' in c or 'def' in c:
        return 'Urea'
    if 'toll' in c:
        return 'Toll'
    if 'service' in c:
        return 'Service'
    return 'Other'

TYRE_ACTIONS = ['New Tyre Fitted', 'Tyre Replacement', 'Tyre Resole', 'Puncture Repair']
# Superset of every position code any 2-7 wheeler layout can produce, for the Position filter
# dropdown and the Add/Edit Tyre modal (both list positions across all vehicles, not just one) --
# the per-vehicle Vehicle Tyre Layout diagram uses _tyre_positions_for_wheeler_count() instead,
# scoped to just the selected vehicle's actual wheel count.
TYRE_POSITIONS = []
for _n in range(2, 8):
    for _p in _tyre_positions_for_wheeler_count(_n):
        if _p != 'Spare' and _p not in TYRE_POSITIONS:
            TYRE_POSITIONS.append(_p)
TYRE_POSITIONS.append('Spare')
del _n, _p

@app.route('/maintenance')
def maintenance_list():
    conn = get_db()
    tab = request.args.get('tab', 'overview')
    # Insurance and Permit & Fitness now live on the Vehicles page — send old links there
    # instead of rendering them here, so nothing bookmarked to /maintenance?tab=insurance breaks.
    if tab in ('insurance', 'permit'):
        conn.close()
        return redirect(url_for('vehicles_list', tab=tab))
    if tab == 'service':
        return _maintenance_service_tab(conn)
    if tab == 'tyres':
        return _maintenance_tyres_tab(conn)
    if tab == 'battery':
        return _maintenance_battery_tab(conn)
    if tab == 'urea':
        return _maintenance_urea_tab(conn)
    if tab == 'toll':
        return _maintenance_toll_tab(conn)
    if tab == 'compliance-expenses':
        return _maintenance_compliance_tab(conn)
    if tab in MAINTENANCE_TAB_LABELS:
        return _maintenance_category_tab(conn, tab)
    return _maintenance_overview_tab(conn)

def _maintenance_overview_data(conn, date_from, date_to):
    """All the real computation behind the Maintenance Overview tab — stat cards, category
    cards, fleet health bands, action-required list, top spenders, 6-month cost trend — factored
    out of _maintenance_overview_tab() so the mobile app's /app/maintenance can reuse the exact
    same numbers (same 'own' vehicle filter, same spend-based health-band proxy, same
    90-day-stale threshold) without a second hand-written copy that could quietly drift from the
    website's. Returns one dict; the website route below unpacks it into its own render_template
    call exactly as before this refactor — zero behavior change there."""
    own_vehicles = conn.execute("SELECT id, vehicle_no, registration_date FROM vehicles WHERE type = 'own' ORDER BY vehicle_no").fetchall()
    total_fleet = len(own_vehicles)

    period_entries = conn.execute("""SELECT m.id, m.date, m.vehicle_id, m.category, m.service_type, m.amount, m.paid_amount, v.vehicle_no
                                     FROM maintenance m JOIN vehicles v ON m.vehicle_id=v.id
                                     WHERE v.type = 'own' AND m.date>=? AND m.date<=?
                                     ORDER BY m.date DESC""", (date_from, date_to)).fetchall()
    total_cost = sum(e['amount'] or 0 for e in period_entries)
    total_paid = sum(e['paid_amount'] or 0 for e in period_entries)
    total_unpaid = total_cost - total_paid
    vehicles_serviced = len(set(e['vehicle_id'] for e in period_entries))

    # Fitness & Permit are tracked on the Vehicles page's own Compliance system now (with their own
    # renew/sync flow and cost path) — no longer duplicated here as a Maintenance Overview card.
    # Insurance removed from this card row too, per the same reasoning.
    cat_labels = ['Service', 'Tyres', 'Battery', 'Urea', 'Toll']
    cat_slugs = {'Service': 'service', 'Tyres': 'tyres', 'Battery': 'battery',
                 'Urea': 'urea', 'Toll': 'toll'}
    cat_data = {k: {'vehicles': set(), 'count': 0, 'cost': 0} for k in cat_labels}
    for e in period_entries:
        k = _maintenance_classify(e['category'], e['service_type'])
        if k in cat_data:
            cat_data[k]['vehicles'].add(e['vehicle_id'])
            cat_data[k]['count'] += 1
            cat_data[k]['cost'] += e['amount'] or 0
    category_cards = [{'label': k, 'slug': cat_slugs[k], 'vehicles_done': len(cat_data[k]['vehicles']),
                        'count': cat_data[k]['count'], 'cost': cat_data[k]['cost']} for k in cat_labels]

    km_row = conn.execute("""SELECT COALESCE(SUM(t.actual_km),0) as km FROM trips t JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE v.type = 'own' AND t.date>=? AND t.date<=? AND t.actual_km IS NOT NULL""",
                           (date_from, date_to)).fetchone()
    total_km = km_row['km'] or 0
    avg_cost_km = round(total_cost / total_km, 2) if total_km > 0 else None

    active_count = 0
    for v in own_vehicles:
        active_days, idle_days, _ = _vehicle_active_idle_days(conn, v['id'], v['registration_date'], date_from, date_to)
        if active_days > 0:
            active_count += 1
    fleet_availability = round(active_count / total_fleet * 100, 1) if total_fleet else 0

    pending_by_vehicle = {}
    for e in period_entries:
        pend = (e['amount'] or 0) - (e['paid_amount'] or 0)
        if pend > 0.01:
            pending_by_vehicle[e['vehicle_no']] = pending_by_vehicle.get(e['vehicle_no'], 0) + pend
    pending_count = len(pending_by_vehicle)

    # Fleet health bands — derived from each vehicle's period maintenance spend relative to the
    # fleet average (no mechanical condition data exists to score against, so this is explicitly
    # a spend-based proxy: heavier-than-average repair spend this period = worse band).
    fleet_avg_cost = (total_cost / total_fleet) if total_fleet else 0
    health_bands = {'Excellent': 0, 'Good': 0, 'Average': 0, 'Poor': 0, 'Critical': 0}
    for v in own_vehicles:
        v_cost = sum(e['amount'] or 0 for e in period_entries if e['vehicle_id'] == v['id'])
        if v_cost <= 0:
            band = 'Excellent'
        else:
            ratio = v_cost / fleet_avg_cost if fleet_avg_cost else 0
            if ratio < 0.5: band = 'Good'
            elif ratio < 1.0: band = 'Average'
            elif ratio < 2.0: band = 'Poor'
            else: band = 'Critical'
        health_bands[band] += 1

    actions = []
    for vno, amt in sorted(pending_by_vehicle.items(), key=lambda x: -x[1])[:4]:
        actions.append({'icon': '\U0001F4B0', 'label': 'Pending Payment', 'vehicle': vno, 'detail': f"₹{amt:,.0f} unpaid", 'kind': 'bad'})
    last_maint = {}
    for row in conn.execute("SELECT vehicle_id, MAX(date) as last_date FROM maintenance WHERE vehicle_id IS NOT NULL GROUP BY vehicle_id").fetchall():
        last_maint[row['vehicle_id']] = row['last_date']
    d2 = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    stale = []
    for v in own_vehicles:
        last_date = last_maint.get(v['id'])
        if last_date:
            days_since = (d2 - datetime.datetime.strptime(last_date, '%Y-%m-%d').date()).days
            if days_since > 90:
                stale.append((v['vehicle_no'], days_since))
        else:
            stale.append((v['vehicle_no'], None))
    # One consolidated card per category instead of one card per vehicle — with several vehicles
    # missing history at once, six near-identical "No Maintenance History" cards was just noise;
    # a single card naming how many (and which) says the same thing without cluttering the panel.
    stale.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
    no_history_vnos = [vno for vno, days in stale if days is None]
    overdue = [(vno, days) for vno, days in stale if days is not None]

    def _vehicle_list_label(vnos, limit=3):
        shown = ', '.join(vnos[:limit])
        return shown + (f" +{len(vnos) - limit} more" if len(vnos) > limit else '')

    if no_history_vnos:
        actions.append({'icon': '⚠️', 'label': 'No Maintenance History', 'vehicle': _vehicle_list_label(no_history_vnos),
                         'detail': f"{len(no_history_vnos)} vehicle{'s' if len(no_history_vnos) != 1 else ''} — no records yet", 'kind': 'warn'})
    if overdue:
        overdue_vnos = [vno for vno, _ in overdue]
        actions.append({'icon': '⏱', 'label': 'No Recent Maintenance', 'vehicle': _vehicle_list_label(overdue_vnos),
                         'detail': f"{len(overdue)} vehicle{'s' if len(overdue) != 1 else ''} — over 90 days since last entry", 'kind': 'warn'})

    recent_entries = period_entries[:8]
    spend_by_vehicle = {}
    for e in period_entries:
        if e['vehicle_no']:
            spend_by_vehicle[e['vehicle_no']] = spend_by_vehicle.get(e['vehicle_no'], 0) + (e['amount'] or 0)
    top_spend = sorted(spend_by_vehicle.items(), key=lambda x: -x[1])[:5]

    trend = []
    end_d = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    y_, m_ = end_d.year, end_d.month
    for i in range(5, -1, -1):
        mm = m_ - i
        yy = y_
        while mm <= 0:
            mm += 12
            yy -= 1
        mf, mt = _month_bounds(yy, mm)
        cost = conn.execute("""SELECT COALESCE(SUM(m.amount),0) FROM maintenance m JOIN vehicles v ON m.vehicle_id=v.id
                               WHERE v.type = 'own' AND m.date>=? AND m.date<=?""", (mf, mt)).fetchone()[0]
        trend.append({'label': calendar.month_abbr[mm], 'cost': cost})
    trend_max = max([t['cost'] for t in trend], default=1) or 1
    chart_w, chart_h = 640, 150
    n = len(trend)
    for i, t in enumerate(trend):
        t['x'] = round((i / (n - 1) * (chart_w - 40) + 30) if n > 1 else chart_w / 2, 1)
        t['y'] = round(chart_h - (t['cost'] / trend_max * (chart_h - 30)) - 15, 1)

    return {
        'total_fleet': total_fleet, 'vehicles_serviced': vehicles_serviced, 'pending_count': pending_count,
        'total_cost': total_cost, 'total_unpaid': total_unpaid, 'avg_cost_km': avg_cost_km, 'fleet_availability': fleet_availability,
        'category_cards': category_cards, 'health_bands': health_bands, 'actions': actions,
        'recent_entries': recent_entries, 'top_spend': top_spend, 'trend': trend, 'trend_max': trend_max,
        'chart_w': chart_w, 'chart_h': chart_h, 'date_from': date_from, 'date_to': date_to,
    }

def _maintenance_overview_tab(conn):
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    default_from, default_to = _month_bounds(last_month_end.year, last_month_end.month)
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or default_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    d = _maintenance_overview_data(conn, date_from, date_to)
    conn.close()
    return render_template('maintenance.html', tab='overview',
        total_fleet=d['total_fleet'], vehicles_serviced=d['vehicles_serviced'], pending_count=d['pending_count'],
        total_cost=d['total_cost'], total_unpaid=d['total_unpaid'], avg_cost_km=d['avg_cost_km'], fleet_availability=d['fleet_availability'],
        category_cards=d['category_cards'], health_bands=d['health_bands'], actions=d['actions'],
        recent_entries=d['recent_entries'], top_spend=d['top_spend'], trend=d['trend'], trend_max=d['trend_max'],
        chart_w=d['chart_w'], chart_h=d['chart_h'], f_date_from=date_from, f_date_to=date_to, active='maintenance')

UREA_LOW_STOCK_THRESHOLD_L = 200  # default reorder point per location — no Settings field for
                                   # this yet, so it's a documented constant rather than a
                                   # silently-invented one; easy to move into Settings later.

def _urea_management_data(conn, location_f='', supplier_f='', type_f='', search_f='', date_from='', date_to=''):
    """Real Urea (AdBlue/DEF) stock ledger data — extracted out of _maintenance_urea_tab() (website)
    so the mobile app_urea() can share the exact same numbers, same reasoning already applied to
    Battery/Tyres this pass. Every row is a real transaction (a purchase into stock, or a
    consumption out of it), with a running balance computed live from the ledger, not a
    separately-maintained counter that could drift. Cost only enters the maintenance table (and
    therefore the Overview 'Urea' card) at the moment stock is purchased or a direct/off-stock
    top-up happens — using stock you already paid for isn't a second expense. Does not close conn
    or handle pagination — callers own both, same split _fleet_utilization_data() etc. use."""
    date_from, date_to = _normalized_date_range(date_from, date_to)
    # paid_amount only ever lives on the linked maintenance row (a stock_out row has none --
    # consuming already-paid-for stock is never a new cost) -- joined in here so the list can
    # show a real pending Balance per entry instead of just the total.
    query = """SELECT u.*, ve.name as supplier_name, v.vehicle_no, m2.paid_amount as paid_amount
               FROM urea_transactions u LEFT JOIN vendors ve ON u.supplier_id=ve.id
               LEFT JOIN vehicles v ON u.vehicle_id=v.id
               LEFT JOIN maintenance m2 ON u.maintenance_id=m2.id WHERE 1=1"""
    params = []
    if location_f:
        query += " AND u.location=?"; params.append(location_f)
    if supplier_f:
        query += " AND ve.name=?"; params.append(supplier_f)
    if type_f:
        query += " AND u.txn_type=?"; params.append(type_f)
    if search_f:
        query += " AND (u.batch_no LIKE ? OR u.invoice_no LIKE ? OR v.vehicle_no LIKE ?)"
        params += [f"%{search_f}%"] * 3
    if date_from:
        query += " AND u.date>=?"; params.append(date_from)
    if date_to:
        query += " AND u.date<=?"; params.append(date_to)
    query += " ORDER BY u.date DESC, u.id DESC"
    all_rows = conn.execute(query, params).fetchall()

    # KPIs are computed from the *unfiltered* full ledger — a filtered view shouldn't make the
    # stock level look different from reality.
    full_ledger = conn.execute("SELECT * FROM urea_transactions ORDER BY date, id").fetchall()
    current_stock = 0.0
    stock_in_qty = stock_in_value = 0.0
    for t in full_ledger:
        if t['txn_type'] == 'stock_in':
            current_stock += t['quantity_l']
            stock_in_qty += t['quantity_l']
            stock_in_value += t['total_value'] or 0
        elif t['source'] == 'stock':
            current_stock -= t['quantity_l']
    avg_unit_cost = (stock_in_value / stock_in_qty) if stock_in_qty else 0
    total_value = current_stock * avg_unit_cost

    today = datetime.date.today().isoformat()
    month_start = datetime.date.today().replace(day=1).isoformat()
    today_usage = sum(t['quantity_l'] for t in full_ledger if t['txn_type'] == 'stock_out' and t['date'] == today)
    month_usage = sum(t['quantity_l'] for t in full_ledger if t['txn_type'] == 'stock_out' and t['date'] >= month_start)

    # Consumption (L/100km) needs real distance driven — the own fleet's actual_km already
    # logged on trips this month, not an invented figure.
    month_km = conn.execute("""SELECT COALESCE(SUM(t.actual_km),0) FROM trips t JOIN vehicles v ON t.vehicle_id=v.id
                               WHERE v.type = 'own' AND t.date>=? AND t.actual_km IS NOT NULL""",
                            (month_start,)).fetchone()[0]
    avg_consumption = round(month_usage / month_km * 100, 2) if month_km else None

    # Low stock is evaluated per location, since that's the level stock is actually held/reordered at.
    locations = sorted({t['location'] for t in full_ledger if t['location']})
    low_stock_alerts = 0
    for loc in locations:
        bal = 0.0
        for t in full_ledger:
            if t['location'] != loc:
                continue
            if t['txn_type'] == 'stock_in':
                bal += t['quantity_l']
            elif t['source'] == 'stock':
                bal -= t['quantity_l']
        if bal < UREA_LOW_STOCK_THRESHOLD_L:
            low_stock_alerts += 1

    # Stock Trend (last 30 days) — running balance sampled at each transaction date.
    trend_cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    trend_rows = []
    running = 0.0
    for t in full_ledger:
        if t['txn_type'] == 'stock_in':
            running += t['quantity_l']
        elif t['source'] == 'stock':
            running -= t['quantity_l']
        if t['date'] >= trend_cutoff:
            trend_rows.append({'date': t['date'], 'balance': running})
    trend_max = max([t['balance'] for t in trend_rows], default=1) or 1

    # Top vehicles by usage (last 30 days, stock_out only).
    usage_by_vehicle = {}
    for t in full_ledger:
        if t['txn_type'] == 'stock_out' and t['date'] >= trend_cutoff and t['vehicle_id']:
            usage_by_vehicle.setdefault(t['vehicle_id'], [None, 0.0])
            usage_by_vehicle[t['vehicle_id']][1] += t['quantity_l']
    vname_lookup = {r['vehicle_id']: r['vehicle_no'] for r in all_rows if r['vehicle_id']}
    # Using stock is never a new cost event (see add_urea's stock_out branch), so there's no real
    # per-use price to sum. This estimates what each vehicle's usage was worth at the current
    # weighted-average stock cost (avg_unit_cost, computed above) — informational only, never
    # written back as a transaction amount anywhere.
    top_vehicles = sorted([{'vehicle_no': vname_lookup.get(vid, '—'), 'qty': q[1],
                             'est_value': round(q[1] * avg_unit_cost, 2)}
                            for vid, q in usage_by_vehicle.items()], key=lambda x: -x['qty'])[:5]

    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    suppliers = conn.execute("SELECT DISTINCT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()

    return {
        'all_rows': all_rows, 'current_stock': current_stock, 'total_value': total_value,
        'avg_unit_cost': avg_unit_cost, 'today_usage': today_usage, 'month_usage': month_usage,
        'avg_consumption': avg_consumption, 'low_stock_alerts': low_stock_alerts, 'locations': locations,
        'trend_rows': trend_rows, 'trend_max': trend_max, 'top_vehicles': top_vehicles,
        'vehicles': vehicles, 'suppliers': suppliers, 'combined_names': combined_names,
    }

def _insert_urea_from_form(conn, f):
    """Builds and inserts one Urea transaction — 'stock_in' (pure stock purchase, a real cost
    event), 'stock_out' (consume already-paid-for stock for a vehicle, no new cost), or 'direct'
    (buy and use in the same moment, bypassing tracked stock — its own real cost event, like
    Tyre's 'New Tyre' mode) — from a submitted form. Same 3 modes, same stock-can-never-go-
    negative check, used by both add_urea() (website) and app_add_urea() (mobile) so the two can
    never quietly accept a different set of fields or disagree on the stock math. Returns
    (True, None) on success, or (False, (kind, detail)) on failure — kind is 'insufficient_stock'
    (detail = the real quantity actually available) or 'validation' (detail = a message) — does
    not commit or close conn."""
    mode = f.get('mode')
    if mode not in ('stock_in', 'stock_out', 'direct'):
        return False, ('validation', 'Mode is required.')
    date = f.get('date') or datetime.date.today().isoformat()
    location = f.get('location') or None
    notes = f.get('notes') or None
    qty, qty_err = _parse_money(f.get('quantity_l'), 'Quantity (L)', required=True, allow_zero=False)
    if qty_err:
        return False, ('validation', qty_err)
    # Quantity and Price are independent, deliberately not multiplied together — the form's
    # "Price" field (still the unit_price column/param for schema compatibility) is the real
    # total amount paid, typed directly off the invoice. Multiplying by Quantity used to mean a
    # slightly-off or missing Quantity silently corrupted the real total (even zeroed it out if
    # Quantity was left blank), when the actual paid amount was never in question.
    unit_price, price_err = _parse_money(f.get('unit_price'), 'Price')
    if price_err:
        return False, ('validation', price_err)
    total_value = round(unit_price, 2)
    paid_in, paid_err = _parse_money(f.get('paid_amount'), 'Paid amount')
    if paid_err:
        return False, ('validation', paid_err)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if mode == 'stock_in':
        supplier_id = get_or_create_vendor(conn, f.get('supplier_name'))
        cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount,
                                vendor_id, notes, invoice_no, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (date, None, 'Urea', total_value, paid_in,
                               supplier_id, notes, f.get('invoice_no'), session.get('user_id'), now))
        conn.execute("""INSERT INTO urea_transactions (date, txn_type, source, batch_no, supplier_id,
                        invoice_no, quantity_l, unit_price, total_value, location, notes, maintenance_id, created_at,
                        created_by)
                        VALUES (?, 'stock_in', 'stock', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (date, f.get('batch_no'), supplier_id, f.get('invoice_no'), qty, unit_price,
                      total_value, location, notes, cur_m.lastrowid, now, session.get('user_id')))
    elif mode == 'stock_out':
        # Using stock that's already been paid for is never a new cost — no Price field on this
        # mode's form, no maintenance row created here (matching stock_in/direct, which do both).
        # Stock can never go negative: block the entry outright if it would draw more than what's
        # actually on hand, same running-balance formula the Current Stock KPI itself uses.
        full_ledger_check = conn.execute("SELECT txn_type, source, quantity_l FROM urea_transactions").fetchall()
        current_stock = 0.0
        for t in full_ledger_check:
            if t['txn_type'] == 'stock_in':
                current_stock += t['quantity_l']
            elif t['source'] == 'stock':
                current_stock -= t['quantity_l']
        # Litres are kept to 0.01 L; compare at that precision so float noise (333.33 - 111.11 =
        # 222.21999999999997) can't refuse taking out exactly what is left, or let 0.01 L extra through.
        current_stock = round(current_stock, 2)
        if qty > current_stock:
            return False, ('insufficient_stock', current_stock)
        vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
        conn.execute("""INSERT INTO urea_transactions (date, txn_type, source, batch_no, vehicle_id,
                        quantity_l, unit_price, total_value, location, odometer_km, notes, created_at, created_by)
                        VALUES (?, 'stock_out', 'stock', ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)""",
                     (date, f.get('batch_no'), vehicle_id, qty, location,
                      float(f.get('odometer_km')) if f.get('odometer_km') else None, notes, now, session.get('user_id')))
    elif mode == 'direct':
        vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
        supplier_id = get_or_create_vendor(conn, f.get('supplier_name')) if f.get('supplier_name') else None
        cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount,
                                vendor_id, notes, invoice_no, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (date, vehicle_id, 'Urea', total_value, paid_in,
                               supplier_id, notes, f.get('invoice_no'), session.get('user_id'), now))
        conn.execute("""INSERT INTO urea_transactions (date, txn_type, source, supplier_id, invoice_no,
                        vehicle_id, quantity_l, unit_price, total_value, location, odometer_km, notes,
                        maintenance_id, created_at, created_by)
                        VALUES (?, 'stock_out', 'direct', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (date, supplier_id, f.get('invoice_no'), vehicle_id, qty, unit_price, total_value,
                      location, float(f.get('odometer_km')) if f.get('odometer_km') else None, notes,
                      cur_m.lastrowid, now, session.get('user_id')))
    return True, None

def _maintenance_urea_tab(conn):
    """Urea (AdBlue/DEF) stock ledger tab — real numbers from _urea_management_data() (shared
    with the mobile app_urea()), this function just owns the website's own query-param reading,
    pagination and template rendering on top of it."""
    location_f = request.args.get('location', '')
    supplier_f = request.args.get('supplier', '')
    type_f = request.args.get('type', '')
    search_f = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    stock_error = request.args.get('error', '')
    stock_error_available = request.args.get('available', '')

    d = _urea_management_data(conn, location_f, supplier_f, type_f, search_f, date_from, date_to)
    all_rows = d['all_rows']

    total_count = len(all_rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=8)
    page_rows = all_rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    conn.close()
    return render_template('maintenance.html', tab='urea',
        rows=page_rows, total_count=total_count, current_stock=d['current_stock'], total_value=d['total_value'],
        avg_unit_cost=d['avg_unit_cost'], today_usage=d['today_usage'], month_usage=d['month_usage'],
        avg_consumption=d['avg_consumption'], low_stock_alerts=d['low_stock_alerts'], locations=d['locations'],
        trend_rows=d['trend_rows'], top_vehicles=d['top_vehicles'],
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=d['vehicles'], suppliers=d['suppliers'], combined_names=d['combined_names'],
        f_location=location_f, f_supplier=supplier_f, f_type=type_f, f_search=search_f,
        f_date_from=date_from, f_date_to=date_to, stock_error=stock_error,
        stock_error_available=stock_error_available, active='maintenance')

TOLL_STATUS_COLORS = {  # background, text — same style as the compliance badges on Vehicles
    'synced':   ('#e8f5ee', 'var(--green)'),
    'approved': ('#eaf1fb', '#2a78d6'),
    'pending':  ('#fff3e8', 'var(--accent)'),
    'rejected': ('#fdecea', 'var(--red)'),
}

# Compliance Expenses tab — own-fleet only, same scope reasoning as Toll/Urea/compliance_service.py
# (a hired vehicle's insurance, PUC, permit, fitness or loan EMI isn't this company's expense).
COMPLIANCE_EXPENSE_TYPES = ['Insurance', 'PUC', 'Permit', 'Fitness', 'EMI & Loans', 'Other']
COMPLIANCE_EXPENSE_COLORS = {
    'Insurance': '#2a78d6', 'PUC': '#1a9c5b', 'Permit': '#eda100',
    'Fitness': '#c05621', 'EMI & Loans': '#4a3aa7', 'Other': '#8a8f98',
}
# A handful of real Odisha/national-highway toll plazas this fleet's own trips actually pass —
# used only by the mock FASTag sync below, never shown as if it came from a live feed.
FASTAG_MOCK_PLAZAS = [
    ('Chandikhole Toll Plaza', 'NH-16', 'Odisha'), ('Sambalpur Toll Plaza', 'NH-53', 'Odisha'),
    ('Rengali Toll Plaza', 'NH-520', 'Odisha'), ('Khordha Toll Plaza', 'NH-16', 'Odisha'),
    ('Cuttack Toll Plaza', 'NH-16', 'Odisha'), ('Jharsuguda Toll Plaza', 'NH-49', 'Odisha'),
]
_TOLL_IMPORT_STASH = {}  # excel-preview token -> {'rows': [...], 'ts': datetime} — see toll_excel_preview

def _save_toll_receipt(file_storage, company_id=1):
    """company_id defaults to 1 (see the note on _get_invoice_settings) — every caller passes
    session.get('company_id', 1), so this saves to uploads/toll_receipts/1/... today exactly
    like uploads/toll_receipts/... before this change, just one directory level deeper. Once a
    real second company exists, its uploads land in their own uploads/toll_receipts/<id>/
    folder automatically, with no risk of two companies' files colliding — existing files
    already saved under the old flat path are untouched and keep serving from their
    already-stored path, nothing here retroactively moves them."""
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    unique = f"toll_{int(datetime.datetime.now().timestamp() * 1000)}_{filename}"
    company_dir = os.path.join(TOLL_RECEIPT_UPLOAD_DIR, str(company_id))
    os.makedirs(company_dir, exist_ok=True)
    file_storage.save(os.path.join(company_dir, unique))
    return f"uploads/toll_receipts/{company_id}/{unique}"

def _toll_tab_base_context(conn, date_from=None, date_to=None):
    """Everything the Toll tab needs to render — factored out from _maintenance_toll_tab so the
    Excel preview/import routes (which also re-render this same tab, with extra wizard state
    layered on top) build it identically instead of duplicating the query/KPI logic. Caller owns
    the connection (opens and closes it) since the excel routes need it open a bit longer.
    date_from/date_to default to reading straight from request.args (the website's own filter
    bar posts them there) — the mobile app_toll() route passes them in explicitly instead, computed
    from its own "This Month/Last Month/..." period preset, same override pattern
    _urea_management_data() already uses so one shared function serves a raw date range either
    way without the website's own behavior changing at all."""
    vehicle_f = request.args.get('vehicle', '')
    source_f = request.args.get('source', '')
    status_f = request.args.get('status', '')
    search_f = request.args.get('search', '')
    date_from = date_from if date_from is not None else request.args.get('date_from', '')
    date_to = date_to if date_to is not None else request.args.get('date_to', '')

    # Toll Management is own-fleet only (Line/Local) — same scope as Compliance and Urea — a
    # hired vehicle's toll isn't this company's cost to track.
    query = """SELECT te.*, v.vehicle_no, t.lr_number
               FROM toll_entries te JOIN vehicles v ON te.vehicle_id=v.id
               LEFT JOIN trips t ON te.trip_id=t.id
               WHERE v.type = 'own'"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no=?"; params.append(vehicle_f)
    if source_f:
        query += " AND te.source=?"; params.append(source_f)
    if status_f:
        query += " AND te.status=?"; params.append(status_f)
    if search_f:
        query += " AND (te.toll_plaza LIKE ? OR te.highway LIKE ? OR v.vehicle_no LIKE ?)"
        params += [f"%{search_f}%"] * 3
    if date_from:
        query += " AND te.date>=?"; params.append(date_from)
    if date_to:
        query += " AND te.date<=?"; params.append(date_to)
    query += " ORDER BY te.date DESC, COALESCE(te.time,'') DESC, te.id DESC"
    all_rows = conn.execute(query, params).fetchall()

    # KPIs come from the *unfiltered* own-fleet ledger — narrowing the table below with a filter
    # shouldn't make the stat cards lie about the real totals.
    full_ledger = conn.execute("""SELECT te.*, v.vehicle_no FROM toll_entries te
                                  JOIN vehicles v ON te.vehicle_id=v.id
                                  WHERE v.type = 'own'""").fetchall()
    today = datetime.date.today().isoformat()
    month_start = datetime.date.today().replace(day=1).isoformat()
    today_rows = [r for r in full_ledger if r['date'] == today]
    month_rows = [r for r in full_ledger if r['date'] >= month_start]
    today_total = sum(r['amount'] or 0 for r in today_rows)
    month_total = sum(r['amount'] or 0 for r in month_rows)
    fastag_rows = [r for r in full_ledger if r['source'] == 'fastag']
    manual_rows = [r for r in full_ledger if r['source'] == 'manual']
    fastag_total = sum(r['amount'] or 0 for r in fastag_rows)
    manual_total = sum(r['amount'] or 0 for r in manual_rows)
    grand_total = fastag_total + manual_total
    fastag_pct = round(fastag_total / grand_total * 100, 1) if grand_total else 0
    manual_pct = round(manual_total / grand_total * 100, 1) if grand_total else 0

    trips_with_toll = len(set(r['trip_id'] for r in full_ledger if r['trip_id']))
    denom = trips_with_toll or len(full_ledger)
    avg_per_trip = round(grand_total / denom, 2) if denom else 0

    # This-month-vs-last-month comparison — for the mobile app's KPI trend arrows only (the
    # website's own Toll tab doesn't show these). Same "unique trip if this row has one, else
    # just count the row" denominator as avg_per_trip above, just scoped to one calendar month at
    # a time instead of the whole own-fleet ledger.
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    prev_month_from, prev_month_to = _month_bounds(last_month_end.year, last_month_end.month)
    prev_month_rows = [r for r in full_ledger if prev_month_from <= r['date'] <= prev_month_to]
    prev_month_total = sum(r['amount'] or 0 for r in prev_month_rows)
    prev_month_count = len(prev_month_rows)
    month_trips = len(set(r['trip_id'] for r in month_rows if r['trip_id']))
    month_denom = month_trips or len(month_rows)
    month_avg_per_trip = round(month_total / month_denom, 2) if month_denom else 0
    prev_month_trips = len(set(r['trip_id'] for r in prev_month_rows if r['trip_id']))
    prev_month_denom = prev_month_trips or len(prev_month_rows)
    prev_month_avg_per_trip = round(prev_month_total / prev_month_denom, 2) if prev_month_denom else 0

    def _pct_change(cur, prev):
        """None when there's no real previous-month figure to compare against (never fabricate a
        0% or 100% swing out of a missing baseline) — the template shows no arrow in that case."""
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)
    month_total_pct = _pct_change(month_total, prev_month_total)
    month_count_pct = _pct_change(len(month_rows), prev_month_count)
    month_avg_pct = _pct_change(month_avg_per_trip, prev_month_avg_per_trip)

    plaza_totals = {}
    for r in full_ledger:
        plaza_totals[r['toll_plaza']] = plaza_totals.get(r['toll_plaza'], 0) + (r['amount'] or 0)
    highest_plaza, highest_plaza_amt = max(plaza_totals.items(), key=lambda x: x[1]) if plaza_totals else (None, 0)
    top_plazas = sorted([{'plaza': k, 'amount': v} for k, v in plaza_totals.items()], key=lambda x: -x['amount'])[:6]
    plaza_max = max([p['amount'] for p in top_plazas], default=1) or 1

    # Monthly trend, last 6 months — same month-bucket pattern as the Overview tab's cost trend.
    # 'count' (transactions that month) is additive — only the mobile app's dual-bar trend chart
    # uses it, the website's own single-line trend chart still just reads .amount/.label.
    trend = []
    end_d = datetime.date.today()
    y_, m_ = end_d.year, end_d.month
    for i in range(5, -1, -1):
        mm = m_ - i; yy = y_
        while mm <= 0:
            mm += 12; yy -= 1
        mf, mt = _month_bounds(yy, mm)
        month_bucket = [r for r in full_ledger if mf <= r['date'] <= mt]
        cost = sum(r['amount'] or 0 for r in month_bucket)
        trend.append({'label': calendar.month_abbr[mm], 'amount': cost, 'count': len(month_bucket)})
    trend_max = max([t['amount'] for t in trend], default=1) or 1
    trend_count_max = max([t['count'] for t in trend], default=1) or 1

    hwy_totals = {}
    for r in full_ledger:
        hwy = r['highway'] or 'Unspecified'
        hwy_totals[hwy] = hwy_totals.get(hwy, 0) + (r['amount'] or 0)
    hwy_dist = sorted([{'highway': k, 'amount': v, 'pct': round(v / grand_total * 100, 1) if grand_total else 0}
                        for k, v in hwy_totals.items()], key=lambda x: -x['amount'])[:6]

    veh_totals = {}
    for r in full_ledger:
        veh_totals[r['vehicle_no']] = veh_totals.get(r['vehicle_no'], 0) + (r['amount'] or 0)
    top_vehicles = sorted([{'vehicle_no': k, 'amount': v} for k, v in veh_totals.items()], key=lambda x: -x['amount'])[:6]
    veh_max = max([v['amount'] for v in top_vehicles], default=1) or 1

    total_count = len(all_rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = all_rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None); base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    own_vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type = 'own' ORDER BY vehicle_no").fetchall()
    last_sync_row = conn.execute("SELECT value FROM settings WHERE key='toll_fastag_last_sync'").fetchone()

    return dict(
        rows=page_rows, all_rows=all_rows, total_count=total_count,
        today_total=today_total, today_count=len(today_rows), month_total=month_total, month_count=len(month_rows),
        fastag_total=fastag_total, fastag_count=len(fastag_rows), fastag_pct=fastag_pct,
        manual_total=manual_total, manual_count=len(manual_rows), manual_pct=manual_pct,
        avg_per_trip=avg_per_trip, highest_plaza=highest_plaza, highest_plaza_amt=highest_plaza_amt,
        trend=trend, trend_max=trend_max, trend_count_max=trend_count_max, top_plazas=top_plazas,
        plaza_max=plaza_max, hwy_dist=hwy_dist,
        top_vehicles=top_vehicles, veh_max=veh_max, grand_total=grand_total,
        prev_month_total=prev_month_total, prev_month_count=prev_month_count,
        month_avg_per_trip=month_avg_per_trip, prev_month_avg_per_trip=prev_month_avg_per_trip,
        month_total_pct=month_total_pct, month_count_pct=month_count_pct, month_avg_pct=month_avg_pct,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=own_vehicles, last_sync_at=(last_sync_row['value'] if last_sync_row else None),
        f_vehicle=vehicle_f, f_source=source_f, f_status=status_f, f_search=search_f,
        f_date_from=date_from, f_date_to=date_to, status_colors=TOLL_STATUS_COLORS)

def _maintenance_toll_tab(conn):
    """Toll Management — own fleet only. Every real toll charge (FASTag-synced or manually
    logged) also lands in the shared `maintenance` table (category='Toll') so it flows into the
    Overview cost rollup and Ledger automatically, same as Urea/Battery/etc. already do."""
    ctx = _toll_tab_base_context(conn)
    conn.close()
    return render_template('maintenance.html', tab='toll', active='maintenance', **ctx)

def _compliance_expense_tab_context(conn):
    """Everything the Compliance Expenses tab needs to render. Own-fleet only, same scope
    reasoning as Toll. Queries compliance_expenses directly (not maintenance) — see
    migrate_compliance_expenses.sql for why this is a dedicated table rather than a maintenance
    category filter."""
    vehicle_f = request.args.get('vehicle', '')
    type_f = request.args.get('compliance_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    # Search is new (the website itself has no search box on this tab) — added as a plain
    # substring match over vehicle/description/vendor, same "layer a search convenience on top of
    # otherwise-real filters" call already made for Expenses/Ledger (see _overhead_list_data()'s
    # own docstring): additive for the website (a query string it never sends), not a behavior
    # change there.
    search_f = request.args.get('search', '')

    query = """SELECT ce.*, v.vehicle_no, ve.name as vendor_name
               FROM compliance_expenses ce JOIN vehicles v ON ce.vehicle_id=v.id
               LEFT JOIN vendors ve ON ce.vendor_id=ve.id
               WHERE v.type = 'own'"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no=?"; params.append(vehicle_f)
    if type_f:
        query += " AND ce.compliance_type=?"; params.append(type_f)
    if date_from:
        query += " AND ce.date>=?"; params.append(date_from)
    if date_to:
        query += " AND ce.date<=?"; params.append(date_to)
    query += " ORDER BY ce.date DESC, ce.id DESC"
    all_rows = conn.execute(query, params).fetchall()
    if search_f:
        s = search_f.lower()
        all_rows = [r for r in all_rows if s in (r['vehicle_no'] or '').lower()
                    or s in (r['description'] or '').lower() or s in (r['vendor_name'] or '').lower()]

    # KPIs come from the *unfiltered* own-fleet ledger, same reasoning as Toll's KPIs — a table
    # filter below shouldn't make the stat cards lie about the real totals.
    full_ledger = conn.execute("""SELECT ce.* FROM compliance_expenses ce
                                  JOIN vehicles v ON ce.vehicle_id=v.id
                                  WHERE v.type = 'own'""").fetchall()
    today = datetime.date.today()
    month_start = today.replace(day=1).isoformat()
    month_rows = [r for r in full_ledger if r['date'] >= month_start]
    month_total = sum(r['amount'] or 0 for r in month_rows)
    month_count = len(month_rows)

    prev_month_end = today.replace(day=1) - datetime.timedelta(days=1)
    prev_from, prev_to = _month_bounds(prev_month_end.year, prev_month_end.month)
    prev_rows = [r for r in full_ledger if prev_from <= r['date'] <= prev_to]
    prev_total = sum(r['amount'] or 0 for r in prev_rows)
    prev_count = len(prev_rows)
    total_change_pct = round((month_total - prev_total) / prev_total * 100, 2) if prev_total else None
    count_change_pct = round((month_count - prev_count) / prev_count * 100, 2) if prev_count else None

    days_elapsed = (today - today.replace(day=1)).days + 1
    avg_per_day = round(month_total / days_elapsed, 2) if days_elapsed else 0
    prev_days = (datetime.date.fromisoformat(prev_to) - datetime.date.fromisoformat(prev_from)).days + 1
    prev_avg_per_day = round(prev_total / prev_days, 2) if prev_days else 0
    avg_change_pct = round((avg_per_day - prev_avg_per_day) / prev_avg_per_day * 100, 2) if prev_avg_per_day else None

    highest = max(month_rows, key=lambda r: r['amount'] or 0, default=None)
    highest_vehicle_no = None
    if highest:
        hv = conn.execute("SELECT vehicle_no FROM vehicles WHERE id=?", (highest['vehicle_id'],)).fetchone()
        highest_vehicle_no = hv['vehicle_no'] if hv else None

    # Expenses by Compliance Type — full ledger, all-time (not scoped to this month), matching
    # the reference design's donut total.
    type_totals = {}
    for r in full_ledger:
        ct = r['compliance_type'] or 'Other'
        type_totals[ct] = type_totals.get(ct, 0) + (r['amount'] or 0)
    grand_total = sum(type_totals.values())
    type_breakdown = sorted(
        [{'type': k, 'amount': v, 'pct': round(v / grand_total * 100, 1) if grand_total else 0,
          'color': COMPLIANCE_EXPENSE_COLORS.get(k, '#8a8f98')} for k, v in type_totals.items()],
        key=lambda x: -x['amount'])

    # Monthly trend, last 6 months — same month-bucket pattern as Toll's own trend chart.
    trend = []
    y_, m_ = today.year, today.month
    for i in range(5, -1, -1):
        mm = m_ - i; yy = y_
        while mm <= 0:
            mm += 12; yy -= 1
        mf, mt = _month_bounds(yy, mm)
        cost = sum(r['amount'] or 0 for r in full_ledger if mf <= r['date'] <= mt)
        trend.append({'label': calendar.month_abbr[mm], 'amount': cost})
    trend_max = max([t['amount'] for t in trend], default=1) or 1

    total_count = len(all_rows)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = all_rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None); base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    own_vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type = 'own' ORDER BY vehicle_no").fetchall()
    vendor_names = [r['name'] for r in conn.execute("SELECT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()]

    return dict(
        rows=page_rows, all_rows=all_rows, total_count=total_count,
        month_total=month_total, month_count=month_count, total_change_pct=total_change_pct, count_change_pct=count_change_pct,
        avg_per_day=avg_per_day, avg_change_pct=avg_change_pct,
        highest_amount=(highest['amount'] if highest else 0), highest_vehicle_no=highest_vehicle_no,
        highest_type=(highest['compliance_type'] if highest else None),
        type_breakdown=type_breakdown, grand_total=grand_total,
        trend=trend, trend_max=trend_max,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=own_vehicles, vendor_names=vendor_names, compliance_types=COMPLIANCE_EXPENSE_TYPES,
        f_vehicle=vehicle_f, f_compliance_type=type_f, f_date_from=date_from, f_date_to=date_to, f_search=search_f)

def _maintenance_compliance_tab(conn):
    """Compliance Expenses — own fleet only. Every entry also lands in the shared `maintenance`
    table (category=compliance_type) so it flows into the Overview cost rollup automatically,
    same as Toll already does — see migrate_compliance_expenses.sql for the full reasoning."""
    ctx = _compliance_expense_tab_context(conn)
    conn.close()
    return render_template('maintenance.html', tab='compliance-expenses', active='maintenance', **ctx)

def _maintenance_category_tab(conn, tab_slug, template='maintenance.html', active='maintenance', base_path='/maintenance'):
    label = MAINTENANCE_TAB_LABELS[tab_slug]
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    vehicle_f = request.args.get('vehicle', '')

    query = """SELECT m.id, m.date, v.vehicle_no, m.category, m.service_type, m.amount, m.paid_amount, ve.name as vendor_name
               FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id
               WHERE 1=1"""
    params = []
    if date_from:
        query += " AND m.date>=?"; params.append(date_from)
    if date_to:
        query += " AND m.date<=?"; params.append(date_to)
    if vehicle_f:
        query += " AND v.vehicle_no=?"; params.append(vehicle_f)
    query += " ORDER BY m.date DESC"
    all_rows = conn.execute(query, params).fetchall()
    rows = [r for r in all_rows if _maintenance_classify(r['category'], r['service_type']) == label]

    total_count = len(rows)
    total_amount = sum(r['amount'] or 0 for r in rows)
    paid_total = sum(r['paid_amount'] or 0 for r in rows)
    unpaid_total = total_amount - paid_total
    vehicles_involved = len(set(r['vehicle_no'] for r in rows if r['vehicle_no']))

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=25)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    all_vehicles = conn.execute("SELECT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active' ORDER BY vehicle_no").fetchall()
    conn.close()
    return render_template(template, tab=tab_slug, tab_label=label,
                            rows=page_rows, total_count=total_count, total_amount=total_amount,
                            paid_total=paid_total, unpaid_total=unpaid_total, vehicles_involved=vehicles_involved,
                            page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
                            f_date_from=date_from, f_date_to=date_to, f_vehicle=vehicle_f, vehicles=all_vehicles,
                            base_path=base_path, active=active)

def _tyre_management_data(conn):
    """Real filters (from request.args) + real filtered entry rows + real stat totals + real Tyre
    Stock rows for the Tyres tab — shared by _maintenance_tyres_tab() (website, full page with
    charts/vehicle-layout diagram) and app_tyres() (mobile, KPIs + stock + entries list only).
    Same filter semantics as _maintenance_service_rows(): a vehicle/vendor pins the SQL WHERE,
    action/position/search apply after (they depend on _maintenance_classify() / free-text
    matching, not a plain indexed column). Returns a dict; does not close conn."""
    vehicle_f = request.args.get('vehicle', '')
    action_f = request.args.get('action', '')
    position_f = request.args.get('position', '')
    vendor_f = request.args.get('vendor', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    search_f = request.args.get('search', '')

    query = """SELECT m.*, v.vehicle_no, ve.name as vendor_name FROM maintenance m
               LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id
               WHERE 1=1"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no=?"; params.append(vehicle_f)
    if vendor_f:
        query += " AND ve.name=?"; params.append(vendor_f)
    if date_from:
        query += " AND m.date>=?"; params.append(date_from)
    if date_to:
        query += " AND m.date<=?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    all_rows = conn.execute(query, params).fetchall()

    rows = [r for r in all_rows if _maintenance_classify(r['category'], r['service_type']) == 'Tyres']
    if action_f:
        rows = [r for r in rows if (r['tyre_action'] or r['category'] or '') == action_f]
    if position_f:
        rows = [r for r in rows if (r['tyre_position'] or '') == position_f]
    if search_f:
        s = search_f.lower()
        rows = [r for r in rows if s in (r['vehicle_no'] or '').lower() or s in (r['tyre_id'] or '').lower()
                or s in (r['tyre_brand'] or '').lower() or s in (r['invoice_no'] or '').lower() or s in (r['vendor_name'] or '').lower()]

    total_count = len(rows)
    total_cost = sum(r['amount'] or 0 for r in rows)
    paid_total = sum(r['paid_amount'] or 0 for r in rows)
    unpaid_total = total_cost - paid_total
    vehicles_covered = len(set(r['vehicle_no'] for r in rows if r['vehicle_no']))
    puncture_count = sum(1 for r in rows if any(k in (r['tyre_action'] or r['category'] or '').lower() for k in ('punt', 'punc')))
    avg_cost = round(total_cost / total_count, 0) if total_count else 0

    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    tyre_vendors = conn.execute("SELECT DISTINCT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    tyre_brands = sorted(set(r['tyre_brand'] for r in all_rows if r['tyre_brand']))

    # Tyre Stock: tyres bought ahead of use. Installing one links its existing purchase
    # record to a vehicle/position instead of billing the vendor a second time.
    stock_rows = conn.execute("""SELECT ts.*, ve.name as vendor_name, iv.vehicle_no as installed_vehicle_no
                                  FROM tyre_stock ts LEFT JOIN vendors ve ON ts.vendor_id=ve.id
                                  LEFT JOIN vehicles iv ON ts.installed_vehicle_id=iv.id
                                  ORDER BY (ts.status='In Stock') DESC, ts.purchase_date DESC, ts.id DESC""").fetchall()
    stock_in_count = sum(1 for s in stock_rows if s['status'] == 'In Stock')
    stock_new_count = sum(1 for s in stock_rows if s['status'] == 'In Stock' and (s['tyre_type'] or '') == 'New')
    stock_resole_count = sum(1 for s in stock_rows if s['status'] == 'In Stock' and (s['tyre_type'] or '') == 'Resole')
    stock_value = sum(s['purchase_cost'] or 0 for s in stock_rows if s['status'] == 'In Stock')

    return {'all_rows': all_rows, 'rows': rows, 'total_count': total_count, 'total_cost': total_cost,
            'unpaid_total': unpaid_total, 'vehicles_covered': vehicles_covered, 'puncture_count': puncture_count,
            'avg_cost': avg_cost, 'vehicles': vehicles, 'tyre_vendors': tyre_vendors, 'combined_names': combined_names,
            'tyre_brands': tyre_brands, 'stock_rows': stock_rows, 'stock_in_count': stock_in_count,
            'stock_new_count': stock_new_count, 'stock_resole_count': stock_resole_count, 'stock_value': stock_value,
            'f_vehicle': vehicle_f, 'f_action': action_f, 'f_position': position_f, 'f_vendor': vendor_f,
            'f_date_from': date_from, 'f_date_to': date_to, 'f_search': search_f}

def _maintenance_tyres_tab(conn):
    d = _tyre_management_data(conn)
    all_rows, rows = d['all_rows'], d['rows']
    total_count, total_cost, unpaid_total = d['total_count'], d['total_cost'], d['unpaid_total']
    vehicles_covered, puncture_count, avg_cost = d['vehicles_covered'], d['puncture_count'], d['avg_cost']
    vehicles, tyre_vendors, combined_names, tyre_brands = d['vehicles'], d['tyre_vendors'], d['combined_names'], d['tyre_brands']
    stock_rows, stock_in_count, stock_new_count, stock_resole_count, stock_value = (
        d['stock_rows'], d['stock_in_count'], d['stock_new_count'], d['stock_resole_count'], d['stock_value'])
    vehicle_f, action_f, position_f, vendor_f = d['f_vehicle'], d['f_action'], d['f_position'], d['f_vendor']
    date_from, date_to, search_f = d['f_date_from'], d['f_date_to'], d['f_search']

    action_totals = {}
    for r in rows:
        a = r['tyre_action'] or r['category'] or 'Other'
        action_totals[a] = action_totals.get(a, 0) + 1
    action_breakdown = sorted(action_totals.items(), key=lambda x: -x[1])[:6]

    today = datetime.date.today()
    trend = []
    end_ref = datetime.datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else today
    y_, m_ = end_ref.year, end_ref.month
    for i in range(5, -1, -1):
        mm = m_ - i
        yy = y_
        while mm <= 0:
            mm += 12
            yy -= 1
        mf, mt = _month_bounds(yy, mm)
        month_rows = [r for r in all_rows if _maintenance_classify(r['category'], r['service_type']) == 'Tyres'
                      and r['date'] and mf <= r['date'] <= mt]
        trend.append({'label': calendar.month_abbr[mm], 'cost': sum(r['amount'] or 0 for r in month_rows)})
    trend_max = max([t['cost'] for t in trend], default=1) or 1
    chart_w, chart_h = 400, 140
    n = len(trend)
    for i, t in enumerate(trend):
        t['x'] = round((i / (n - 1) * chart_w) if n > 1 else chart_w / 2, 1)
        t['y'] = round(chart_h - (t['cost'] / trend_max * (chart_h - 20)) - 10, 1)

    cost_by_vehicle = {}
    for r in rows:
        if r['vehicle_no']:
            cost_by_vehicle[r['vehicle_no']] = cost_by_vehicle.get(r['vehicle_no'], 0) + (r['amount'] or 0)
    top_costly = sorted(cost_by_vehicle.items(), key=lambda x: -x[1])[:5]

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    # Vehicle Tyre Layout: for the picked vehicle, the latest real entry logged against each
    # position — no fabricated fitment/stock data, just "what does the record actually say".
    global_rows = conn.execute("""SELECT m.category, m.service_type, m.date, m.id, v.vehicle_no
                                   FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id
                                   WHERE v.vehicle_no IS NOT NULL""").fetchall()
    tyre_last_by_vehicle = {}
    for r in global_rows:
        if _maintenance_classify(r['category'], r['service_type']) == 'Tyres':
            key = (r['date'] or '', r['id'])
            if r['vehicle_no'] not in tyre_last_by_vehicle or key > tyre_last_by_vehicle[r['vehicle_no']]:
                tyre_last_by_vehicle[r['vehicle_no']] = key
    default_lv = max(tyre_last_by_vehicle, key=lambda vn: tyre_last_by_vehicle[vn]) if tyre_last_by_vehicle \
        else (vehicles[0]['vehicle_no'] if vehicles else '')
    lv = request.args.get('lv') or default_lv

    layout_rows = conn.execute("""SELECT m.*, v.vehicle_no, ve.name as vendor_name, ts.tyre_type FROM maintenance m
                                   LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id
                                   LEFT JOIN tyre_stock ts ON ts.maintenance_id=m.id
                                   WHERE v.vehicle_no=? ORDER BY m.date, m.id""", (lv,)).fetchall() if lv else []
    layout_tyre_rows = [r for r in layout_rows if _maintenance_classify(r['category'], r['service_type']) == 'Tyres']
    # Layout is scoped to the selected vehicle's own wheel count (2/3/4/5/6/7-wheeler etc, off
    # its body_type) so a 3-wheeler tempo shows 3 tyre positions, not the same fixed 6-position
    # truck layout every vehicle showed before.
    lv_vehicle_row = conn.execute("SELECT body_type FROM vehicles WHERE vehicle_no=?", (lv,)).fetchone() if lv else None
    lv_wheeler_count = _vehicle_wheeler_count(lv_vehicle_row['body_type'] if lv_vehicle_row else None)
    layout_tyre_positions = _tyre_positions_for_wheeler_count(lv_wheeler_count)
    layout_positions = {}
    for pos in layout_tyre_positions:
        matches = [r for r in layout_tyre_rows if (r['tyre_position'] or '') == pos]
        layout_positions[pos] = matches[-1] if matches else None
    positions_recorded = sum(1 for pos in layout_tyre_positions if layout_positions[pos])
    vehicle_tyre_spend = sum(r['amount'] or 0 for r in layout_tyre_rows)
    vehicle_last_updated = max((r['date'] for r in layout_tyre_rows if r['date']), default=None)

    conn.close()

    return render_template('maintenance.html', tab='tyres',
        rows=page_rows, total_count=total_count, total_cost=total_cost, unpaid_total=unpaid_total,
        vehicles_covered=vehicles_covered, puncture_count=puncture_count, avg_cost=avg_cost,
        action_breakdown=action_breakdown, trend=trend, trend_max=trend_max, chart_w=chart_w, chart_h=chart_h, top_costly=top_costly,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=vehicles, tyre_vendors=tyre_vendors, combined_names=combined_names,
        tyre_actions=TYRE_ACTIONS, tyre_positions=TYRE_POSITIONS, tyre_brands=tyre_brands,
        lv=lv, layout_positions=layout_positions, layout_tyre_positions=layout_tyre_positions,
        lv_wheeler_count=lv_wheeler_count, positions_recorded=positions_recorded,
        vehicle_tyre_spend=vehicle_tyre_spend, vehicle_last_updated=vehicle_last_updated,
        stock_rows=stock_rows, stock_in_count=stock_in_count, stock_new_count=stock_new_count,
        stock_resole_count=stock_resole_count, stock_value=stock_value,
        f_vehicle=vehicle_f, f_action=action_f, f_position=position_f, f_vendor=vendor_f,
        f_date_from=date_from, f_date_to=date_to, f_search=search_f, active='maintenance')

def _battery_status(b):
    """Good/Weak/Replace Soon/Dead bands off the latest logged health% — the same honest,
    threshold-based approach used for Fleet Health on Overview. status_override lets a real
    'Mark as Dead' action win outright, since that's an asserted fact, not a derived one."""
    if b['status_override']:
        return b['status_override']
    if not b['vehicle_id']:
        return 'In Stock'
    h = b['health_pct']
    if h is None:
        return 'Good'
    if h >= 70:
        return 'Good'
    if h >= 40:
        return 'Weak'
    if h >= 20:
        return 'Replace Soon'
    return 'Dead'

def _battery_warranty_upto(b):
    base = b['install_date'] or b['purchase_date']
    if not base or not b['warranty_months']:
        return None
    try:
        d = datetime.datetime.strptime(base, '%Y-%m-%d').date()
    except ValueError:
        return None
    total_month = d.month - 1 + int(b['warranty_months'])
    year = d.year + total_month // 12
    month = total_month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day).isoformat()

def _battery_enrich(r):
    d = dict(r)
    d['status'] = _battery_status(r)
    wu = _battery_warranty_upto(r)
    d['warranty_upto'] = wu
    d['warranty_days_left'] = None
    if wu:
        try:
            d['warranty_days_left'] = (datetime.datetime.strptime(wu, '%Y-%m-%d').date() - datetime.date.today()).days
        except ValueError:
            pass
    return d

def _next_battery_no(conn):
    row = conn.execute("SELECT battery_no FROM batteries WHERE battery_no LIKE 'BAT-%' ORDER BY id DESC LIMIT 1").fetchone()
    n = 0
    if row and row['battery_no']:
        try:
            n = int(row['battery_no'].split('-')[-1])
        except ValueError:
            n = 0
    return f"BAT-{n + 1:05d}"

def _battery_management_data(conn):
    """Real filters (from request.args) + real filtered rows + real KPI stats + real Tyre-Stock-
    style "batteries currently in stock" list for the Battery tab — shared by
    _maintenance_battery_tab() (website, full page with charts/warranty/low-health panels) and
    app_battery() (mobile, KPIs + list only). Same filter semantics as
    _tyre_management_data()/_maintenance_service_rows(): every field here is a plain column or a
    value _battery_enrich() derives, so unlike Tyres/Service nothing needs classify()-style
    post-filtering. Returns a dict; does not close conn."""
    vehicle_f = request.args.get('vehicle', '')
    brand_f = request.args.get('brand', '')
    status_f = request.args.get('status', '')
    type_f = request.args.get('battery_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    search_f = request.args.get('search', '')

    raw_rows = conn.execute("""SELECT b.*, v.vehicle_no, ve.name as vendor_name FROM batteries b
                               LEFT JOIN vehicles v ON b.vehicle_id=v.id LEFT JOIN vendors ve ON b.vendor_id=ve.id
                               ORDER BY b.id DESC""").fetchall()
    all_rows = [_battery_enrich(r) for r in raw_rows]

    rows = all_rows
    if vehicle_f:
        rows = [r for r in rows if r['vehicle_no'] == vehicle_f]
    if brand_f:
        rows = [r for r in rows if (r['brand'] or '') == brand_f]
    if type_f:
        rows = [r for r in rows if (r['battery_type'] or '') == type_f]
    if status_f:
        rows = [r for r in rows if r['status'] == status_f]
    if date_from:
        rows = [r for r in rows if (r['install_date'] or r['purchase_date'] or '') >= date_from]
    if date_to:
        rows = [r for r in rows if (r['install_date'] or r['purchase_date'] or '') <= date_to]
    if search_f:
        s = search_f.lower()
        rows = [r for r in rows if s in (r['battery_no'] or '').lower() or s in (r['brand'] or '').lower()
                or s in (r['vehicle_no'] or '').lower() or s in (r['serial_no'] or '').lower()]

    total_batteries = len(all_rows)
    in_use = sum(1 for r in all_rows if r['vehicle_id'] and r['status'] == 'Good')
    in_stock = sum(1 for r in all_rows if not r['vehicle_id'] and r['status'] == 'In Stock')
    weak_soon = sum(1 for r in all_rows if r['vehicle_id'] and r['status'] in ('Weak', 'Replace Soon'))
    dead_count = sum(1 for r in all_rows if r['status'] in ('Dead', 'Scrapped'))

    healths = [r['health_pct'] for r in all_rows if r['health_pct'] is not None]
    avg_health_pct = round(sum(healths) / len(healths)) if healths else None

    total_count = len(rows)
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    battery_vendors = conn.execute("SELECT DISTINCT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    battery_brands = sorted(set(r['brand'] for r in all_rows if r['brand']))
    stock_batteries = [r for r in all_rows if not r['vehicle_id'] and r['status'] == 'In Stock']

    return {'all_rows': all_rows, 'rows': rows, 'total_count': total_count,
            'total_batteries': total_batteries, 'in_use': in_use, 'in_stock': in_stock,
            'weak_soon': weak_soon, 'dead_count': dead_count, 'avg_health_pct': avg_health_pct,
            'vehicles': vehicles, 'battery_vendors': battery_vendors, 'combined_names': combined_names,
            'battery_brands': battery_brands, 'stock_batteries': stock_batteries,
            'f_vehicle': vehicle_f, 'f_brand': brand_f, 'f_status': status_f, 'f_battery_type': type_f,
            'f_date_from': date_from, 'f_date_to': date_to, 'f_search': search_f}

def _maintenance_battery_tab(conn):
    d = _battery_management_data(conn)
    all_rows, rows = d['all_rows'], d['rows']
    total_count = d['total_count']
    total_batteries, in_use, in_stock, weak_soon, dead_count = (
        d['total_batteries'], d['in_use'], d['in_stock'], d['weak_soon'], d['dead_count'])
    avg_health_pct = d['avg_health_pct']
    vehicles, battery_vendors, combined_names, battery_brands, stock_batteries = (
        d['vehicles'], d['battery_vendors'], d['combined_names'], d['battery_brands'], d['stock_batteries'])
    vehicle_f, brand_f, status_f, type_f = d['f_vehicle'], d['f_brand'], d['f_status'], d['f_battery_type']
    date_from, date_to, search_f = d['f_date_from'], d['f_date_to'], d['f_search']

    today = datetime.date.today()
    ages = []
    for r in all_rows:
        if r['install_date']:
            try:
                dd = datetime.datetime.strptime(r['install_date'], '%Y-%m-%d').date()
                ages.append((today - dd).days / 30.44)
            except ValueError:
                pass
    avg_life_months = round(sum(ages) / len(ages)) if ages else None

    status_counts = {'Good': 0, 'Weak': 0, 'Replace Soon': 0, 'Dead': 0, 'In Stock': 0}
    for r in all_rows:
        status_counts[r['status']] = status_counts.get(r['status'], 0) + 1

    warranty_list = [r for r in all_rows if r['warranty_upto'] and r['warranty_days_left'] is not None
                      and r['warranty_days_left'] >= 0 and r['status'] not in ('Dead', 'Scrapped')]
    warranty_list.sort(key=lambda r: r['warranty_upto'])
    warranty_list = warranty_list[:6]

    low_health = sorted([r for r in all_rows if r['health_pct'] is not None and r['health_pct'] < 70 and r['vehicle_id']],
                         key=lambda r: r['health_pct'])[:6]

    this_year = str(today.year)
    total_cost_year = sum(r['purchase_price'] or 0 for r in all_rows if (r['purchase_date'] or '').startswith(this_year))
    replaced_year = sum(1 for r in all_rows if r['status'] == 'Dead' and (r['last_checked_date'] or r['install_date'] or '').startswith(this_year))
    cost_per_battery = round(total_cost_year / total_batteries) if total_batteries else 0

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    checks_by_battery = {}
    if page_rows:
        ids = [r['id'] for r in page_rows]
        placeholders = ','.join('?' * len(ids))
        for c in conn.execute(f"SELECT * FROM battery_checks WHERE battery_id IN ({placeholders}) ORDER BY date DESC, id DESC", ids).fetchall():
            checks_by_battery.setdefault(c['battery_id'], []).append(c)
    conn.close()

    return render_template('maintenance.html', tab='battery',
        rows=page_rows, total_count=total_count,
        total_batteries=total_batteries, in_use=in_use, in_stock=in_stock, weak_soon=weak_soon, dead_count=dead_count,
        avg_life_months=avg_life_months, avg_health_pct=avg_health_pct,
        status_counts=status_counts, warranty_list=warranty_list, low_health=low_health,
        total_cost_year=total_cost_year, replaced_year=replaced_year, cost_per_battery=cost_per_battery,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=vehicles, battery_vendors=battery_vendors, combined_names=combined_names,
        battery_brands=battery_brands, battery_types=BATTERY_TYPES, stock_batteries=stock_batteries,
        checks_by_battery=checks_by_battery,
        f_vehicle=vehicle_f, f_brand=brand_f, f_status=status_f, f_battery_type=type_f,
        f_date_from=date_from, f_date_to=date_to, f_search=search_f, active='maintenance')

INSURANCE_TYPES = ['Comprehensive', 'Third Party', 'Transit Insurance']

def _vehicle_age_years(registration_date):
    """Age in years (1 decimal place) computed from vehicles.registration_date, or None if that
    field is blank/unparseable — never fabricated, the Age column just shows '—' in that case."""
    if not registration_date:
        return None
    try:
        d = datetime.datetime.strptime(registration_date, '%Y-%m-%d').date()
    except ValueError:
        return None
    days = (datetime.date.today() - d).days
    if days < 0:
        return None
    return round(days / 365.25, 1)

def _expiry_bucket(date_str):
    """'expired' / 'expiring' (within 30 days) / 'ok' / None for any yyyy-mm-dd expiry-style date
    field — the same 30-day window used for insurance policies, applied to vehicle-level dates
    (insurance_expiry, puc_valid_upto, permit_valid_upto, fitness_expiry) for the Vehicles overview."""
    if not date_str:
        return None
    try:
        d = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None
    today = datetime.date.today()
    if d < today:
        return 'expired'
    if (d - today).days <= 30:
        return 'expiring'
    return 'ok'

def _insurance_status(p):
    """Active/Expiring Soon/Expired are always date-derived so they can't go stale; 'Cancelled'
    is the one real-world fact that has to be asserted rather than computed, so it wins outright."""
    if p['status_override'] == 'Cancelled':
        return 'Cancelled'
    if not p['expiry_date']:
        return 'Active'
    try:
        exp = datetime.datetime.strptime(p['expiry_date'], '%Y-%m-%d').date()
    except ValueError:
        return 'Active'
    today = datetime.date.today()
    if exp < today:
        return 'Expired'
    if (exp - today).days <= 30:
        return 'Expiring Soon'
    return 'Active'

def _save_insurance_doc(file_storage, prefix, company_id=1):
    """company_id defaults to 1 — see the note on _save_toll_receipt above, same reasoning."""
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    unique = f"{prefix}_{int(datetime.datetime.now().timestamp() * 1000)}_{filename}"
    company_dir = os.path.join(INSURANCE_UPLOAD_DIR, str(company_id))
    os.makedirs(company_dir, exist_ok=True)
    file_storage.save(os.path.join(company_dir, unique))
    return f"uploads/insurance/{company_id}/{unique}"

def _maintenance_service_rows(conn):
    """Real filters (from request.args) + real filtered rows + real stat totals for the Service
    tab — shared by _maintenance_service_tab() (website, full page with charts/pagination) and
    app_maintenance()'s Service tab (mobile, stats + list only). Same filter semantics
    everywhere: a vehicle/workshop pins the SQL WHERE, service type/status/search apply after
    (since they depend on _maintenance_classify(), not a plain column). Returns a dict; does not
    close conn."""
    vehicle_f = request.args.get('vehicle', '')
    workshop_f = request.args.get('workshop', '')
    stype_f = request.args.get('service_type', '')
    status_f = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    search_f = request.args.get('search', '')

    query = """SELECT m.*, v.vehicle_no, ve.name as vendor_name FROM maintenance m
               LEFT JOIN vehicles v ON m.vehicle_id=v.id LEFT JOIN vendors ve ON m.vendor_id=ve.id
               WHERE 1=1"""
    params = []
    if vehicle_f:
        query += " AND v.vehicle_no=?"; params.append(vehicle_f)
    if workshop_f:
        query += " AND ve.name=?"; params.append(workshop_f)
    if date_from:
        query += " AND m.date>=?"; params.append(date_from)
    if date_to:
        query += " AND m.date<=?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    all_rows = conn.execute(query, params).fetchall()

    rows = [r for r in all_rows if _maintenance_classify(r['category'], r['service_type']) == 'Service']
    if stype_f:
        rows = [r for r in rows if (r['service_type'] or r['category'] or '') == stype_f]
    if status_f:
        rows = [r for r in rows if (r['status'] or 'Completed') == status_f]
    if search_f:
        s = search_f.lower()
        rows = [r for r in rows if s in (r['vehicle_no'] or '').lower() or s in (r['service_type'] or r['category'] or '').lower()
                or s in (r['invoice_no'] or '').lower() or s in (r['vendor_name'] or '').lower()]

    total_count = len(rows)
    total_cost = sum(r['amount'] or 0 for r in rows)
    open_jobs = sum(1 for r in rows if (r['status'] or 'Completed') == 'Open')
    completed_jobs = sum(1 for r in rows if (r['status'] or 'Completed') == 'Completed')
    breakdown_count = sum(1 for r in rows if 'breakdown' in (r['service_type'] or r['category'] or '').lower())
    avg_cost = round(total_cost / total_count, 0) if total_count else 0

    today = datetime.date.today()
    due_soon = 0
    for r in rows:
        if r['next_service_date']:
            try:
                nd = datetime.datetime.strptime(r['next_service_date'], '%Y-%m-%d').date()
                if today <= nd <= today + datetime.timedelta(days=7):
                    due_soon += 1
            except ValueError:
                pass

    return {'all_rows': all_rows, 'rows': rows, 'total_count': total_count, 'total_cost': total_cost,
            'open_jobs': open_jobs, 'completed_jobs': completed_jobs, 'breakdown_count': breakdown_count,
            'due_soon': due_soon, 'avg_cost': avg_cost,
            'f_vehicle': vehicle_f, 'f_workshop': workshop_f, 'f_service_type': stype_f, 'f_status': status_f,
            'f_date_from': date_from, 'f_date_to': date_to, 'f_search': search_f}

def _maintenance_service_tab(conn):
    d = _maintenance_service_rows(conn)
    all_rows, rows = d['all_rows'], d['rows']
    total_count, total_cost = d['total_count'], d['total_cost']
    open_jobs, completed_jobs, breakdown_count, due_soon, avg_cost = (
        d['open_jobs'], d['completed_jobs'], d['breakdown_count'], d['due_soon'], d['avg_cost'])
    vehicle_f, workshop_f, stype_f, status_f, date_from, date_to, search_f = (
        d['f_vehicle'], d['f_workshop'], d['f_service_type'], d['f_status'], d['f_date_from'], d['f_date_to'], d['f_search'])
    today = datetime.date.today()

    ids = [r['id'] for r in rows]
    items_by_id = {}
    if ids:
        placeholders = ','.join('?' * len(ids))
        for it in conn.execute(f"SELECT * FROM maintenance_items WHERE maintenance_id IN ({placeholders})", ids).fetchall():
            items_by_id.setdefault(it['maintenance_id'], []).append(it)

    type_totals = {}
    for r in rows:
        t = r['service_type'] or r['category'] or 'Other'
        type_totals[t] = type_totals.get(t, 0) + 1
    type_breakdown = sorted(type_totals.items(), key=lambda x: -x[1])[:6]

    trend = []
    end_ref = datetime.datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else today
    y_, m_ = end_ref.year, end_ref.month
    for i in range(5, -1, -1):
        mm = m_ - i
        yy = y_
        while mm <= 0:
            mm += 12
            yy -= 1
        mf, mt = _month_bounds(yy, mm)
        month_rows = [r for r in all_rows if _maintenance_classify(r['category'], r['service_type']) == 'Service'
                      and r['date'] and mf <= r['date'] <= mt]
        trend.append({'label': calendar.month_abbr[mm], 'cost': sum(r['amount'] or 0 for r in month_rows)})
    trend_max = max([t['cost'] for t in trend], default=1) or 1
    chart_w, chart_h = 400, 140
    n = len(trend)
    for i, t in enumerate(trend):
        t['x'] = round((i / (n - 1) * chart_w) if n > 1 else chart_w / 2, 1)
        t['y'] = round(chart_h - (t['cost'] / trend_max * (chart_h - 20)) - 10, 1)

    cost_by_vehicle = {}
    for r in rows:
        if r['vehicle_no']:
            cost_by_vehicle[r['vehicle_no']] = cost_by_vehicle.get(r['vehicle_no'], 0) + (r['amount'] or 0)
    top_costly = sorted(cost_by_vehicle.items(), key=lambda x: -x[1])[:5]

    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)
    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    workshops = conn.execute("SELECT DISTINCT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    service_types = sorted(set((r['service_type'] or r['category'] or '') for r in all_rows
                                if _maintenance_classify(r['category'], r['service_type']) == 'Service' and (r['service_type'] or r['category'])))
    conn.close()

    return render_template('maintenance.html', tab='service',
        rows=page_rows, items_by_id=items_by_id, total_count=total_count, total_cost=total_cost,
        open_jobs=open_jobs, completed_jobs=completed_jobs, breakdown_count=breakdown_count, due_soon=due_soon, avg_cost=avg_cost,
        type_breakdown=type_breakdown, trend=trend, trend_max=trend_max, chart_w=chart_w, chart_h=chart_h, top_costly=top_costly,
        page=page, total_pages=total_pages, per_page=per_page, page_tokens=page_tokens, base_qs=base_qs,
        vehicles=vehicles, workshops=workshops, combined_names=combined_names, service_types=service_types,
        checklist_items=MAINTENANCE_CHECKLIST,
        f_vehicle=vehicle_f, f_workshop=workshop_f, f_service_type=stype_f, f_status=status_f,
        f_date_from=date_from, f_date_to=date_to, f_search=search_f, active='maintenance')

@app.route('/maintenance/add', methods=['GET', 'POST'])
def add_maintenance():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
        vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
        conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (f.get('date'), vehicle_id, f.get('category'), float(f.get('amount') or 0),
                      float(f.get('paid_amount') or 0), vendor_id, f.get('notes'), session.get('user_id'),
                      datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        return redirect(url_for('maintenance_list'))
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    return render_template('add_maintenance.html', vehicles=vehicles, vendors=vendors, combined_names=combined_names, active='maintenance')

def _save_service_items(conn, service_id, form):
    conn.execute("DELETE FROM maintenance_items WHERE maintenance_id=?", (service_id,))
    names = form.getlist('item_name')
    cats = form.getlist('item_category')
    qtys = form.getlist('item_qty')
    units = form.getlist('item_unit')
    rates = form.getlist('item_rate')
    for i, name in enumerate(names):
        name = (name or '').strip()
        if not name:
            continue
        qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
        rate = float(rates[i]) if i < len(rates) and rates[i] else 0
        cat = cats[i] if i < len(cats) else ''
        unit = units[i] if i < len(units) else ''
        conn.execute("""INSERT INTO maintenance_items (maintenance_id, item_name, category, qty, unit, rate, amount, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (service_id, name, cat, qty, unit, rate, qty * rate, session.get('user_id'),
                      datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

def _insert_service_from_form(conn, f):
    """Builds and inserts one Service maintenance row from a submitted form — same fields, same
    checklist join, same get_or_create_vendor() workshop resolution used by both add_service()
    (website) and app_add_service() (mobile), extracted here so the two can never quietly accept
    a different set of fields. Returns (ok, error_message_or_new_service_id) — does not commit or
    close conn."""
    if not (f.get('vehicle_no') or '').strip():
        return False, 'Vehicle is required.'
    if not (f.get('service_type') or '').strip():
        return False, 'Service Type is required.'
    if not f.get('date'):
        return False, 'Service Date is required.'
    # Vendor/Workshop is optional (matches the website form and the same mtSyncPaid pattern used
    # by Compliance's own vendor field) — a service done in-house or paid on the spot with no
    # workshop to track has nothing to reconcile against a vendor ledger later.
    if not f.get('amount'):
        return False, 'Total Amount is required.'

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    # Workshop/Vendor uses the exact same combined party+vendor lookup as every other vendor field
    # in the app, so a workshop typed here links up with that same organization's ledger elsewhere.
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    amount = float(f.get('amount') or 0)
    paid_amount = float(f.get('paid_amount') or 0)
    checklist = ','.join(f.getlist('checklist'))
    cur = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, service_type, amount, paid_amount, vendor_id, notes,
                          km_reading, next_due_km, next_service_date, invoice_no, invoice_date, status, checklist_done, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f.get('date'), vehicle_id, f.get('service_type'), f.get('service_type'), amount, paid_amount, vendor_id,
         f.get('notes') or None, float(f.get('km_reading') or 0) or None, float(f.get('next_due_km') or 0) or None,
         f.get('next_service_date') or None, f.get('invoice_no') or None, f.get('invoice_date') or None,
         f.get('status') or 'Completed', checklist or None, session.get('user_id'),
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    service_id = cur.lastrowid
    _save_service_items(conn, service_id, f)
    return True, service_id

@app.route('/maintenance/service/add', methods=['POST'])
def add_service():
    conn = get_db()
    _insert_service_from_form(conn, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='service'))

def _update_service_from_form(conn, m_id, f):
    """Shared by edit_service() (website) and app_edit_service() (mobile) — the exact same
    field set _insert_service_from_form() writes on create (date/vehicle/service_type/amount/
    paid_amount/vendor/notes/km_reading/next_due_km/next_service_date/invoice_no/invoice_date/
    status/checklist), so editing a service record can never silently drop a field the create
    form itself collects. Does not commit or close conn."""
    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    amount = float(f.get('amount') or 0)
    paid_amount = float(f.get('paid_amount') or 0)
    checklist = ','.join(f.getlist('checklist'))
    conn.execute("""UPDATE maintenance SET date=?, vehicle_id=?, category=?, service_type=?, amount=?, paid_amount=?, vendor_id=?,
                    notes=?, km_reading=?, next_due_km=?, next_service_date=?, invoice_no=?, invoice_date=?, status=?, checklist_done=?,
                    updated_by=?, updated_at=?
                    WHERE id=?""",
        (f.get('date'), vehicle_id, f.get('service_type'), f.get('service_type'), amount, paid_amount, vendor_id,
         f.get('notes') or None, float(f.get('km_reading') or 0) or None, float(f.get('next_due_km') or 0) or None,
         f.get('next_service_date') or None, f.get('invoice_no') or None, f.get('invoice_date') or None,
         f.get('status') or 'Completed', checklist or None, session.get('user_id'),
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m_id))
    _enforce_maintenance_paid_floor(conn, m_id)
    _save_service_items(conn, m_id, f)

@app.route('/maintenance/service/edit/<int:m_id>', methods=['POST'])
def edit_service(m_id):
    conn = get_db()
    _update_service_from_form(conn, m_id, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='service'))

def _insert_tyre_from_form(conn, f):
    """Builds and inserts one direct (not from-stock) Tyre entry from a submitted form — same
    fields, same required-field set (Vehicle/Action/Date/Vendor/Cost, matching the website's own
    #addTyreModal "New Tyre" mode — see templates/partials/_maintenance_tyre_modals.html) and same
    get_or_create_vendor() supplier resolution used by both add_tyre() (website) and app_add_tyre()
    (mobile), extracted here so the two can never quietly accept a different set of fields. Does
    not cover the "install an existing stock tyre" shortcut (stock_id) — that stays a website-only
    path (add_tyre() checks it before ever calling this); the mobile Add Tyre modal offers all 3
    real modes (New Tyre / Add to Stock / Install from Stock) but each posts to its own dedicated
    route — see app_add_tyre() / app_add_tyre_stock() / app_install_tyre_stock(). Returns
    (ok, error_message_or_new_entry_id) — does not commit or close conn."""
    if not (f.get('vehicle_no') or '').strip():
        return False, 'Vehicle is required.'
    if not (f.get('tyre_action') or '').strip():
        return False, 'Action is required.'
    if not f.get('date'):
        return False, 'Date is required.'
    if not (f.get('vendor_name') or '').strip():
        return False, 'Supplier / Vendor is required.'
    if not f.get('amount'):
        return False, 'Amount is required.'

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    # Same combined party+vendor lookup used everywhere else — a tyre supplier typed here
    # links up with that same organization's ledger, so the cost flows straight into it.
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    amount = float(f.get('amount') or 0)
    paid_amount = float(f.get('paid_amount') or 0)
    cur = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes,
                    km_reading, invoice_no, invoice_date, tyre_action, tyre_id, tyre_brand, tyre_position, status,
                    created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f.get('date'), vehicle_id, 'Tyres', amount, paid_amount, vendor_id, f.get('notes') or None,
         float(f.get('km_reading') or 0) or None, f.get('invoice_no') or None, f.get('invoice_date') or None,
         f.get('tyre_action') or None, f.get('tyre_id') or None, f.get('tyre_brand') or None,
         f.get('tyre_position') or None, 'Completed', session.get('user_id'),
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return True, cur.lastrowid

@app.route('/maintenance/tyre/add', methods=['POST'])
def add_tyre():
    conn = get_db()
    f = request.form
    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))

    stock_id = f.get('stock_id')
    if stock_id:
        # Reusing a tyre already bought into stock — update its existing purchase/ledger
        # row in place rather than billing the supplier a second time for the same tyre.
        stock = conn.execute("SELECT * FROM tyre_stock WHERE id=? AND status='In Stock'", (stock_id,)).fetchone()
        if stock:
            # The ledger date stays the purchase date — the cost was incurred then, not now —
            # matching the Tyre Stock table's own Install action.
            km_reading = float(f.get('km_reading') or 0) or None
            conn.execute("UPDATE maintenance SET vehicle_id=?, tyre_position=?, tyre_action=?, km_reading=?, updated_by=?, updated_at=? WHERE id=?",
                (vehicle_id, f.get('tyre_position') or None, f.get('tyre_action') or 'New Tyre Fitted',
                 km_reading, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stock['maintenance_id']))
            conn.execute("""UPDATE tyre_stock SET status='Installed', installed_vehicle_id=?, installed_position=?,
                            installed_date=?, updated_by=?, updated_at=? WHERE id=?""",
                (vehicle_id, f.get('tyre_position') or None, f.get('date'),
                 session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stock['id']))
            conn.commit()
            conn.close()
            return redirect(url_for('maintenance_list', tab='tyres'))

    _insert_tyre_from_form(conn, f)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

@app.route('/maintenance/tyre/edit/<int:m_id>', methods=['POST'])
def edit_tyre(m_id):
    conn = get_db()
    f = request.form
    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    amount = float(f.get('amount') or 0)
    paid_amount = float(f.get('paid_amount') or 0)
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""UPDATE maintenance SET date=?, vehicle_id=?, amount=?, paid_amount=?, vendor_id=?, notes=?,
                    km_reading=?, invoice_no=?, invoice_date=?, tyre_action=?, tyre_id=?, tyre_brand=?, tyre_position=?,
                    updated_by=?, updated_at=?
                    WHERE id=?""",
        (f.get('date'), vehicle_id, amount, paid_amount, vendor_id, f.get('notes') or None,
         float(f.get('km_reading') or 0) or None, f.get('invoice_no') or None, f.get('invoice_date') or None,
         f.get('tyre_action') or None, f.get('tyre_id') or None, f.get('tyre_brand') or None,
         f.get('tyre_position') or None, session.get('user_id'), _now, m_id))
    _enforce_maintenance_paid_floor(conn, m_id)
    # This maintenance row may be the installed side of a Tyre Stock purchase (add_tyre()'s own
    # stock_id branch and install_tyre_stock() both link one via tyre_stock.maintenance_id) — if
    # so, mirror the vehicle/position change there too, or "Vehicle Tyre Layout" (reads
    # maintenance.tyre_position) and the Tyre Stock tab (reads tyre_stock.installed_position
    # directly) can show the same physical tyre in two different places after a routine edit here.
    # No-op for a tyre that was never bought from stock (add_tyre_from_form's own path) — the
    # UPDATE below just matches zero rows.
    conn.execute("""UPDATE tyre_stock SET installed_vehicle_id=?, installed_position=?, updated_by=?, updated_at=?
                    WHERE maintenance_id=?""",
                 (vehicle_id, f.get('tyre_position') or None, session.get('user_id'), _now, m_id))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

def _insert_tyre_stock_from_form(conn, f):
    """Builds and inserts one "bought ahead of use" Tyre Stock row (+ its ledger entry) from a
    submitted form — same fields used by both add_tyre_stock() (website) and app_add_tyre_stock()
    (mobile). Returns (ok, error_message_or_new_stock_id) — does not commit or close conn."""
    if not f.get('purchase_date'):
        return False, 'Purchase Date is required.'
    if not (f.get('vendor_name') or '').strip():
        return False, 'Supplier / Vendor is required.'
    if not f.get('purchase_cost'):
        return False, 'Purchase Cost is required.'

    # A stock purchase is its own maintenance/ledger entry (no vehicle yet) — installing it later
    # updates this same row instead of billing the supplier a second time for the same tyre.
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    cost = float(f.get('purchase_cost') or 0)
    paid_amount = float(f.get('paid_amount') or 0)
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes,
                          invoice_no, tyre_action, tyre_id, tyre_brand, status, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f.get('purchase_date'), None, 'Tyres', cost, paid_amount, vendor_id, f.get('notes') or None,
         f.get('invoice_no') or None, 'Stock Purchase', f.get('tyre_id') or None, f.get('brand') or None, 'Completed',
         session.get('user_id'), _now))
    maintenance_id = cur.lastrowid
    cur2 = conn.execute("""INSERT INTO tyre_stock (maintenance_id, tyre_id, brand, tyre_type, purchase_date, purchase_cost,
                    vendor_id, invoice_no, status, notes, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (maintenance_id, f.get('tyre_id') or None, f.get('brand') or None, f.get('tyre_type') or 'New',
         f.get('purchase_date'), cost, vendor_id, f.get('invoice_no') or None, 'In Stock', f.get('notes') or None,
         session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return True, cur2.lastrowid

@app.route('/maintenance/tyre/stock/add', methods=['POST'])
def add_tyre_stock():
    conn = get_db()
    _insert_tyre_stock_from_form(conn, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

def _install_tyre_stock_from_form(conn, stock_id, f):
    """Links an existing In Stock tyre to a vehicle/position — same fields/logic used by both
    install_tyre_stock() (website) and app_install_tyre_stock() (mobile). Returns
    (ok, error_message_or_None) — does not commit or close conn."""
    if not (f.get('vehicle_no') or '').strip():
        return False, 'Vehicle is required.'
    if not (f.get('position') or '').strip():
        return False, 'Position is required.'
    stock = conn.execute("SELECT * FROM tyre_stock WHERE id=?", (stock_id,)).fetchone()
    if not stock:
        return False, 'Stock tyre not found.'
    if stock['status'] != 'In Stock':
        return False, 'This tyre is no longer in stock.'

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    install_date = f.get('install_date') or stock['purchase_date']
    km_reading = float(f.get('km_reading') or 0) or None
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE maintenance SET vehicle_id=?, tyre_position=?, tyre_action=?, km_reading=?, updated_by=?, updated_at=? WHERE id=?",
        (vehicle_id, f.get('position'), 'New Tyre Fitted', km_reading, session.get('user_id'), _now, stock['maintenance_id']))
    conn.execute("""UPDATE tyre_stock SET status='Installed', installed_vehicle_id=?, installed_position=?,
                    installed_date=?, updated_by=?, updated_at=? WHERE id=?""",
        (vehicle_id, f.get('position'), install_date,
         session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stock_id))
    return True, None

@app.route('/maintenance/tyre/stock/<int:stock_id>/install', methods=['POST'])
def install_tyre_stock(stock_id):
    conn = get_db()
    _install_tyre_stock_from_form(conn, stock_id, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

@app.route('/maintenance/tyre/stock/<int:stock_id>/scrap', methods=['POST'])
def scrap_tyre_stock(stock_id):
    conn = get_db()
    conn.execute("UPDATE tyre_stock SET status='Scrapped', updated_by=?, updated_at=? WHERE id=? AND status='In Stock'",
                 (session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stock_id))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

@app.route('/maintenance/tyre/stock/<int:stock_id>/delete', methods=['POST'])
def delete_tyre_stock(stock_id):
    conn = get_db()
    stock = conn.execute("SELECT * FROM tyre_stock WHERE id=?", (stock_id,)).fetchone()
    if stock and stock['status'] == 'In Stock':
        # Child row before the parent maintenance row it references — see delete_battery()'s own
        # comment on why the reverse order is a real (if currently dormant) bug.
        conn.execute("DELETE FROM tyre_stock WHERE id=?", (stock_id,))
        _delete_maintenance_row(conn, stock['maintenance_id'])
        conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='tyres'))

def _insert_battery_from_form(conn, f):
    """Builds and inserts one battery — "Install to Vehicle" or "Add to Stock" mode (mode='install'
    default, matching the website's own hidden #batModeField) — from a submitted form. Same
    fields, same required set as the website's own #addBatteryModal core wrap (Brand/Capacity/
    Battery Type always required; Vehicle/Install Date only required in 'install' mode; Purchase
    Date/Warranty/Price/Vendor always required), used by both add_battery() (website) and
    app_add_battery() (mobile). Returns (ok, error_message_or_new_battery_id) — does not commit or
    close conn."""
    mode = f.get('mode', 'install')
    if not (f.get('brand') or '').strip():
        return False, 'Brand is required.'
    if not f.get('capacity_ah'):
        return False, 'Capacity (AH) is required.'
    if not (f.get('battery_type') or '').strip():
        return False, 'Battery Type is required.'
    if mode == 'install':
        if not (f.get('vehicle_no') or '').strip():
            return False, 'Vehicle is required.'
        if not f.get('install_date'):
            return False, 'Installation Date is required.'
    if not f.get('purchase_date'):
        return False, 'Purchase Date is required.'
    if not f.get('warranty_months'):
        return False, 'Warranty (Months) is required.'
    if not f.get('purchase_price'):
        return False, 'Purchase Price is required.'
    if not (f.get('vendor_name') or '').strip():
        return False, 'Vendor / Dealer is required.'

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no')) if mode == 'install' else None
    # Same purchase-record-becomes-ledger-entry pattern as Tyres: the cost posts once, here,
    # whether the battery goes straight onto a vehicle or sits in stock first.
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    price = float(f.get('purchase_price') or 0)
    battery_no = _next_battery_no(conn)
    health = float(f.get('health_pct') or 0) or None
    voltage = float(f.get('voltage') or 0) or None
    temp_c = float(f.get('temp_c') or 0) or None

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes, invoice_no, status, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (f.get('purchase_date'), vehicle_id, 'Battery', price, float(f.get('paid_amount') or 0), vendor_id,
         f.get('notes') or None, f.get('invoice_no') or None, 'Completed', session.get('user_id'), now))
    maintenance_id = cur.lastrowid

    install_date = f.get('install_date') if mode == 'install' else None
    cur2 = conn.execute("""INSERT INTO batteries (battery_no, brand, model, capacity_ah, battery_type, voltage_rating, serial_no,
                           vehicle_id, installed_location, install_date, purchase_date, purchase_price, vendor_id, invoice_no,
                           warranty_months, maintenance_id, health_pct, voltage, temp_c, last_checked_date, notes,
                           created_by, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (battery_no, f.get('brand') or None, f.get('model') or None, float(f.get('capacity_ah') or 0) or None,
         f.get('battery_type') or None, f.get('voltage_rating') or None, f.get('serial_no') or None,
         vehicle_id, f.get('installed_location') or None, install_date, f.get('purchase_date'), price, vendor_id,
         f.get('invoice_no') or None, int(f.get('warranty_months') or 0) or None, maintenance_id,
         health, voltage, temp_c, install_date or f.get('purchase_date'), f.get('notes') or None,
         session.get('user_id'), now))
    battery_id = cur2.lastrowid

    conn.execute("INSERT INTO battery_checks (battery_id, date, event, created_by, created_at) VALUES (?,?,?,?,?)",
                 (battery_id, f.get('purchase_date'), 'Purchased', session.get('user_id'), now))
    if mode == 'install' and install_date:
        conn.execute("""INSERT INTO battery_checks (battery_id, date, event, health_pct, voltage, temp_c, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (battery_id, install_date, f"Installed in {f.get('vehicle_no')}", health, voltage, temp_c,
                      session.get('user_id'), now))
    return True, battery_id

@app.route('/maintenance/battery/add', methods=['POST'])
def add_battery():
    conn = get_db()
    _insert_battery_from_form(conn, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='battery'))

def _install_battery_from_form(conn, battery_id, f):
    """Links an existing In Stock battery to a vehicle — same fields/logic used by both
    install_battery() (website) and app_install_battery() (mobile). Returns
    (ok, error_message_or_None) — does not commit or close conn."""
    if not (f.get('vehicle_no') or '').strip():
        return False, 'Vehicle is required.'
    if not f.get('install_date'):
        return False, 'Install Date is required.'
    battery = conn.execute("SELECT * FROM batteries WHERE id=?", (battery_id,)).fetchone()
    if not battery:
        return False, 'Battery not found.'
    if battery['vehicle_id']:
        return False, 'This battery is already installed.'

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    install_date = f.get('install_date')
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""UPDATE batteries SET vehicle_id=?, installed_location=?, install_date=?, last_checked_date=?,
                    updated_by=?, updated_at=? WHERE id=?""",
                 (vehicle_id, f.get('installed_location') or None, install_date, install_date,
                  session.get('user_id'), now, battery_id))
    if battery['maintenance_id']:
        conn.execute("UPDATE maintenance SET vehicle_id=?, updated_by=?, updated_at=? WHERE id=?",
                     (vehicle_id, session.get('user_id'), now, battery['maintenance_id']))
    conn.execute("INSERT INTO battery_checks (battery_id, date, event, created_by, created_at) VALUES (?,?,?,?,?)",
                 (battery_id, install_date, f"Installed in {f.get('vehicle_no')}", session.get('user_id'), now))
    return True, None

@app.route('/maintenance/battery/<int:battery_id>/install', methods=['POST'])
def install_battery(battery_id):
    conn = get_db()
    _install_battery_from_form(conn, battery_id, request.form)
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='battery'))

@app.route('/maintenance/battery/<int:battery_id>/check', methods=['POST'])
def check_battery(battery_id):
    conn = get_db()
    f = request.form
    health = float(f.get('health_pct') or 0) or None
    voltage = float(f.get('voltage') or 0) or None
    temp_c = float(f.get('temp_c') or 0) or None
    date = f.get('date') or datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE batteries SET health_pct=?, voltage=?, temp_c=?, last_checked_date=?, updated_by=?, updated_at=? WHERE id=?",
                 (health, voltage, temp_c, date, session.get('user_id'), now, battery_id))
    conn.execute("""INSERT INTO battery_checks (battery_id, date, event, health_pct, voltage, temp_c, remarks, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (battery_id, date, 'Health Check', health, voltage, temp_c, f.get('remarks') or None,
                  session.get('user_id'), now))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='battery'))

@app.route('/maintenance/battery/<int:battery_id>/dead', methods=['POST'])
def mark_battery_dead(battery_id):
    conn = get_db()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE batteries SET status_override='Dead', updated_by=?, updated_at=? WHERE id=?",
                 (session.get('user_id'), now, battery_id))
    conn.execute("INSERT INTO battery_checks (battery_id, date, event, created_by, created_at) VALUES (?,?,?,?,?)",
                 (battery_id, today, 'Marked Dead', session.get('user_id'), now))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='battery'))

@app.route('/maintenance/battery/<int:battery_id>/delete', methods=['POST'])
def delete_battery(battery_id):
    conn = get_db()
    battery = conn.execute("SELECT * FROM batteries WHERE id=?", (battery_id,)).fetchone()
    if battery and not battery['vehicle_id']:
        # Child rows before the parent maintenance row they reference via maintenance_id — the
        # reverse order (parent first) is currently harmless only because get_db()'s real
        # connections never turn foreign_keys enforcement on for SQLite (schema.sql declares
        # `PRAGMA foreign_keys = ON` as the intent, but that pragma is per-connection, not
        # persisted in the file); with it actually on — as a stricter test harness or the
        # planned Postgres migration (which enforces FKs unconditionally) would — deleting the
        # parent first raises a FOREIGN KEY constraint failure and 500s the request instead of
        # deleting anything. Same fix applied to delete_urea/delete_toll/
        # delete_compliance_expense/delete_tyre_stock/delete_insurance, which had the identical
        # ordering.
        conn.execute("DELETE FROM battery_checks WHERE battery_id=?", (battery_id,))
        conn.execute("DELETE FROM batteries WHERE id=?", (battery_id,))
        if battery['maintenance_id']:
            _delete_maintenance_row(conn, battery['maintenance_id'])
        conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='battery'))

@app.route('/maintenance/urea/add', methods=['POST'])
def add_urea():
    """One endpoint for all 3 Add Urea modes (mirrors the Tyre/Battery unified-modal pattern):
    'stock_in' (pure stock purchase — the real cost event), 'stock_out' (consume already-paid-for
    stock for a vehicle — no new cost), 'direct' (buy and use in the same moment, bypassing
    tracked stock — its own real cost event, like Tyre's 'New Tyre' mode). Insert logic lives in
    _insert_urea_from_form(), shared with the mobile app_add_urea(), same reasoning already
    applied to Tyres/Battery this pass."""
    conn = get_db()
    ok, err = _insert_urea_from_form(conn, request.form)
    if not ok:
        conn.close()
        kind, detail = err
        if kind == 'insufficient_stock':
            return redirect(url_for('maintenance_list', tab='urea', error='insufficient_stock', available=detail))
        return redirect(url_for('maintenance_list', tab='urea'))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='urea'))

@app.route('/maintenance/urea/<int:txn_id>/edit', methods=['GET', 'POST'])
def edit_urea(txn_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        qty = float(f.get('quantity_l') or 0)
        # Same independent Quantity/Price as add_urea above — total_value is the Price field
        # entered directly, not Quantity x Price.
        unit_price = float(f.get('unit_price') or 0)
        total_value = round(unit_price, 2)
        _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""UPDATE urea_transactions SET date=?, batch_no=?, quantity_l=?, unit_price=?,
                        total_value=?, location=?, odometer_km=?, notes=?, updated_by=?, updated_at=? WHERE id=?""",
                     (f.get('date'), f.get('batch_no'), qty, unit_price, total_value, f.get('location'),
                      float(f.get('odometer_km')) if f.get('odometer_km') else None, f.get('notes'),
                      session.get('user_id'), _now, txn_id))
        # Keep the linked cost entry (if any) in sync so Overview's Urea card never disagrees
        # with what this ledger shows.
        txn = conn.execute("SELECT maintenance_id FROM urea_transactions WHERE id=?", (txn_id,)).fetchone()
        if txn and txn['maintenance_id']:
            conn.execute("UPDATE maintenance SET date=?, amount=?, notes=?, updated_by=?, updated_at=? WHERE id=?",
                         (f.get('date'), total_value, f.get('notes'), session.get('user_id'), _now, txn['maintenance_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('maintenance_list', tab='urea'))
    conn.close()
    return redirect(url_for('maintenance_list', tab='urea'))

@app.route('/maintenance/urea/<int:txn_id>/delete', methods=['POST'])
def delete_urea(txn_id):
    conn = get_db()
    txn = conn.execute("SELECT maintenance_id FROM urea_transactions WHERE id=?", (txn_id,)).fetchone()
    # Child row before the parent maintenance row it references — see delete_battery()'s own
    # comment on why the reverse order is a real (if currently dormant) bug.
    conn.execute("DELETE FROM urea_transactions WHERE id=?", (txn_id,))
    if txn and txn['maintenance_id']:
        _delete_maintenance_row(conn, txn['maintenance_id'])
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='urea'))

def _insert_toll_from_form(conn, f, receipt_file, company_id=1):
    """Shared by the website's add_toll() and the mobile app_add_toll() — same real insert either
    way: a manual toll entry always also lands in the shared `maintenance` table (category='Toll')
    so it flows into Maintenance Overview's cost rollup, exactly like it already did before this
    was extracted. Returns (True, None) on success, or (False, error_message) if the vehicle
    number given isn't a real own-fleet vehicle — Toll Management is own-fleet only (unlike Urea/
    Battery), so this never silently creates a new vehicle record from a typo'd or hired number."""
    row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE AND type = 'own'",
                        ((f.get('vehicle_no') or '').strip(),)).fetchone()
    vehicle_id = row['id'] if row else None
    if not vehicle_id:
        return False, 'No own-fleet vehicle found with that number.'

    trip_id = None
    lr = (f.get('trip_lr') or '').strip()
    if lr:
        lr_key = lr.split('|')[0].strip()  # datalist value is "LR_NUMBER | vehicle_no | date"
        trow = conn.execute("SELECT id FROM trips WHERE lr_number = ? COLLATE NOCASE ORDER BY date DESC LIMIT 1", (lr_key,)).fetchone()
        trip_id = trow['id'] if trow else None

    date = f.get('date') or datetime.date.today().isoformat()
    time_ = f.get('time') or None
    amount = float(f.get('amount') or 0)
    source = f.get('source') if f.get('source') in ('fastag', 'manual') else 'manual'
    payment_mode = f.get('payment_mode') or None
    status = 'synced' if source == 'fastag' else 'pending'
    notes = f.get('notes') or None
    receipt_path = _save_toll_receipt(receipt_file, company_id)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, notes, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?)""",
                          (date, vehicle_id, 'Toll', amount, amount, notes, session.get('user_id'), now))
    conn.execute("""INSERT INTO toll_entries (date, time, vehicle_id, trip_id, toll_plaza, highway, state,
                    amount, source, payment_mode, status, receipt_path, notes, maintenance_id, created_at,
                    created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (date, time_, vehicle_id, trip_id, f.get('toll_plaza'), f.get('highway') or None,
                  f.get('state') or None, amount, source, payment_mode, status, receipt_path, notes,
                  cur_m.lastrowid, now, session.get('user_id')))
    conn.commit()
    return True, None

@app.route('/maintenance/toll/add', methods=['POST'])
def add_toll():
    conn = get_db()
    _insert_toll_from_form(conn, request.form, request.files.get('receipt'), session.get('company_id', 1))
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll'))

@app.route('/maintenance/toll/<int:entry_id>/delete', methods=['POST'])
def delete_toll(entry_id):
    conn = get_db()
    e = conn.execute("SELECT maintenance_id FROM toll_entries WHERE id=?", (entry_id,)).fetchone()
    # Child row before the parent maintenance row it references — see delete_battery()'s own
    # comment on why the reverse order is a real (if currently dormant) bug.
    conn.execute("DELETE FROM toll_entries WHERE id=?", (entry_id,))
    if e and e['maintenance_id']:
        _delete_maintenance_row(conn, e['maintenance_id'])
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll'))

def _insert_compliance_expense_from_form(conn, f):
    """Shared by the website's add_compliance_expense() and the mobile app_add_compliance() —
    same real insert either way, same own-fleet-only rule Toll's own _insert_toll_from_form()
    uses (see that function's docstring). Returns (True, None) on success, or (False,
    error_message) if the vehicle number given isn't a real own-fleet vehicle — never silently
    creates a new vehicle record from a typo'd or hired number. Does not commit or close conn —
    same convention _insert_toll_from_form() uses, caller owns the transaction."""
    row = conn.execute("SELECT id FROM vehicles WHERE vehicle_no = ? COLLATE NOCASE AND type = 'own'",
                        ((f.get('vehicle_no') or '').strip(),)).fetchone()
    vehicle_id = row['id'] if row else None
    if not vehicle_id:
        return False, 'No own-fleet vehicle found with that number.'

    compliance_type = f.get('compliance_type') if f.get('compliance_type') in COMPLIANCE_EXPENSE_TYPES else 'Other'
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
    date = f.get('date') or datetime.date.today().isoformat()
    amount = float(f.get('amount') or 0)
    # Paid Amount is separate from Amount, same as Tyre/Battery — blank/0 means still payable to
    # the vendor, not automatically fully paid. Previously this was hardcoded to `amount`, which
    # silently marked every compliance expense as paid in full and made the vendor ledger net to
    # zero (debit == credit) no matter what was actually paid.
    paid_amount = float(f.get('paid_amount') or 0)
    payment_mode = f.get('payment_mode') or None
    description = f.get('description') or None
    notes = f.get('notes') or None
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Mirrored into the shared maintenance table (category=compliance_type) purely so this flows
    # into the Maintenance Overview's own-fleet cost rollup automatically, same as Toll already
    # does — see migrate_compliance_expenses.sql for the full reasoning.
    cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                          (date, vehicle_id, compliance_type, amount, paid_amount, vendor_id, description, session.get('user_id'), now))
    conn.execute("""INSERT INTO compliance_expenses (date, vehicle_id, compliance_type, description, vendor_id,
                    amount, payment_mode, maintenance_id, notes, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (date, vehicle_id, compliance_type, description, vendor_id, amount, payment_mode,
                  cur_m.lastrowid, notes, session.get('user_id'), now))
    conn.commit()
    return True, None

@app.route('/maintenance/compliance/add', methods=['POST'])
def add_compliance_expense():
    conn = get_db()
    _insert_compliance_expense_from_form(conn, request.form)
    conn.close()
    return redirect(url_for('maintenance_list', tab='compliance-expenses'))

@app.route('/maintenance/compliance/<int:entry_id>/delete', methods=['POST'])
def delete_compliance_expense(entry_id):
    conn = get_db()
    e = conn.execute("SELECT maintenance_id FROM compliance_expenses WHERE id=?", (entry_id,)).fetchone()
    # Child row before the parent maintenance row it references — see delete_battery()'s own
    # comment on why the reverse order is a real (if currently dormant) bug.
    conn.execute("DELETE FROM compliance_expenses WHERE id=?", (entry_id,))
    if e and e['maintenance_id']:
        _delete_maintenance_row(conn, e['maintenance_id'])
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='compliance-expenses'))

@app.route('/maintenance/toll/<int:entry_id>/approve', methods=['POST'])
def approve_toll(entry_id):
    conn = get_db()
    conn.execute("UPDATE toll_entries SET status='approved', updated_by=?, updated_at=? WHERE id=? AND status='pending'",
                 (session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), entry_id))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll'))

@app.route('/maintenance/toll/<int:entry_id>/reject', methods=['POST'])
def reject_toll(entry_id):
    conn = get_db()
    conn.execute("UPDATE toll_entries SET status='rejected', updated_by=?, updated_at=? WHERE id=? AND status='pending'",
                 (session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), entry_id))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll'))

@app.route('/maintenance/toll/sync-fastag', methods=['POST'])
def sync_fastag_toll():
    """Mock FASTag sync — no live FASTag/bank API is wired up yet, so this fills in a small batch
    of plausible-looking new transactions the same deterministic-mock way the Compliance providers
    do (providers/*.py): reseeded once per hour, so clicking again inside the same hour is a no-op
    instead of piling up duplicate rows — an earlier mock provider polluted the live DB twice before
    this guard existed (see compliance_service.py). Swap this for a real BOSS/FASTag statement feed
    (or just use the Excel import above, which already reads real BOSS exports) once one exists."""
    import random
    conn = get_db()
    vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type = 'own' ORDER BY vehicle_no").fetchall()
    if not vehicles:
        conn.close()
        return redirect(url_for('maintenance_list', tab='toll'))
    now = datetime.datetime.now()
    # Anchored to the top of the hour (not the exact click time) so every field the ref_no is
    # built from — including txn_time below — is byte-identical across repeated clicks inside the
    # same hour. Deriving txn_time from wall-clock `now` instead of this anchor was the bug: it
    # made ref_no change every second, so the dedup check below never matched and every click kept
    # inserting a fresh-looking "new" batch — this already happened live, see the cleanup note in
    # the route's docstring history / conversation log.
    hour_anchor = now.replace(minute=0, second=0, microsecond=0)
    rnd = random.Random(int(now.strftime('%Y%m%d%H')))
    inserted = 0
    for _ in range(rnd.randint(2, 5)):
        v = rnd.choice(vehicles)
        plaza, hwy, state = rnd.choice(FASTAG_MOCK_PLAZAS)
        amount = rnd.choice([65, 85, 110, 145, 175, 210, 280, 350])
        txn_time = hour_anchor - datetime.timedelta(minutes=rnd.randint(5, 600))
        ref_no = f"FT{txn_time.strftime('%y%m%d%H%M%S')}{v['id']}"
        if conn.execute("SELECT 1 FROM toll_entries WHERE reference_no=?", (ref_no,)).fetchone():
            continue
        cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, created_by, created_at)
                                VALUES (?,?,?,?,?,?,?)""",
                             (txn_time.strftime('%Y-%m-%d'), v['id'], 'Toll', amount, amount,
                              session.get('user_id'), now.strftime('%Y-%m-%d %H:%M:%S')))
        conn.execute("""INSERT INTO toll_entries (date, time, vehicle_id, toll_plaza, highway, state, amount,
                        source, payment_mode, status, reference_no, maintenance_id, created_at, created_by)
                        VALUES (?,?,?,?,?,?,?, 'fastag', 'FASTag Wallet', 'synced', ?, ?, ?, ?)""",
                     (txn_time.strftime('%Y-%m-%d'), txn_time.strftime('%H:%M'), v['id'], plaza, hwy, state,
                      amount, ref_no, cur_m.lastrowid, now.strftime('%Y-%m-%d %H:%M:%S'), session.get('user_id')))
        inserted += 1
    conn.execute("""INSERT OR REPLACE INTO settings (key, value, created_by, created_at, updated_by, updated_at)
                    VALUES ('toll_fastag_last_sync', ?, ?, ?, ?, ?)""",
                 (now.strftime('%Y-%m-%d %H:%M:%S'), session.get('user_id'), now.strftime('%Y-%m-%d %H:%M:%S'),
                  session.get('user_id'), now.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll', synced=inserted))

@app.route('/maintenance/toll/template')
def toll_excel_template():
    from flask import send_file
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = 'Toll Entries'
    headers = ['Vehicle Number', 'Trip Number', 'Date', 'Time', 'Toll Plaza', 'Highway', 'State', 'Amount',
               'Source', 'Payment Mode', 'Reference Number', 'Notes']
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1A1A1A')
    ws.append(['OD14AD0117', 'TRIP-1524', '2026-08-07', '10:35', 'Chandikhole Toll Plaza', 'NH-16', 'Odisha',
                425, 'FASTag', '', 'FT260807103512', ''])
    ws.append(['OD14AE1122', '', '2026-08-06', '23:05', 'Durg Expressway Toll', 'NH-53', 'Chhattisgarh',
                560, 'Manual', 'Cash', '', ''])
    widths = [16, 14, 12, 10, 26, 10, 14, 10, 10, 14, 18, 22]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='Toll_Entry_Template.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# Transaction-type keywords that mean "money moved but a vehicle did NOT cross a toll plaza" —
# wallet top-ups, internal transfers between accounts, recoveries/reversals of an earlier
# misdirected transfer, refunds, manual balance adjustments, and the statement's own running-total
# rows. None of these are a toll charge and must never be counted as one.
def _parse_toll_excel(file_storage, conn):
    """Auto-detects and parses either of two supported layouts:
      1. A BlackBuck BOSS account statement export (Transaction Time / Nature (C/D) / Amount /
         From/To / Description / Truck Number / TransactionId / Opening Balance / Closing Balance).
         BlackBuck's Top-Up Plan makes the SAME toll amount show up three times in this ledger —
         once as the real charge, and twice more as bookkeeping around it:
           (a) Debit, From/To=Wallet, Description contains "FasTag Recharge" — the vehicle's
               actual toll cost. This is the ONLY row type counted as toll spend.
           (b) Credit, From/To=BlackBuck, Description contains "BlackBuck transfer" — a temporary
               advance funding (a), not a second toll. Always ignored.
           (c) Debit, From/To=BOSS Account, Description contains "Recovery of Amount transferred"
               — BOSS recovering the advance from (b) back out. Also not a toll. Always ignored.
         Also ignored: UPI Top-Up, Wallet Transfer, Refund, Adjustment, Opening/Closing Balance
         rows, and any row with no Truck Number — none of those are a vehicle's toll cost either.
      2. The plain manual template this page's 'Download Template' link provides (Vehicle Number /
         Trip Number / Date / Time / Toll Plaza / Highway / State / Amount / Source / Payment Mode /
         Reference Number / Notes).
    Returns (fmt, parsed_rows, skipped_count, skipped_amount) — parsed_rows only contains rows that
    passed the toll/non-toll classification; everything else (BlackBuck advances, BOSS recoveries,
    top-ups, transfers, refunds, balance rows) is excluded outright, never entering Vehicle Toll
    Cost / Monthly Toll Reports / Fleet Analytics, matching 'never count these as toll expense'."""
    import openpyxl
    wb = openpyxl.load_workbook(file_storage, data_only=True)
    ws = wb.active

    own_vehicles = {v['vehicle_no'].upper(): v['id'] for v in
                     conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type = 'own'").fetchall()}
    # Keyed (not just a set) so a re-import can tell whether a duplicate TransactionId's row
    # actually changed since it was saved — a re-upload with a corrected amount/date/etc. should
    # offer to override the existing record, not just get silently skipped as "already imported".
    existing_by_ref = {r['reference_no']: dict(r) for r in conn.execute(
        "SELECT id, reference_no, vehicle_id, date, time, toll_plaza, amount FROM toll_entries WHERE reference_no IS NOT NULL AND reference_no != ''").fetchall()}
    existing_keys = {(r['vehicle_id'], r['date'], r['toll_plaza'], round(r['amount'] or 0, 2)) for r in
                      conn.execute("SELECT vehicle_id, date, toll_plaza, amount FROM toll_entries").fetchall()}
    # Rows already seen earlier IN THIS SAME FILE — grows as we classify, so a repeated
    # TransactionId within one upload is caught too, not just duplicates of already-saved data.
    seen_refs_in_batch = set()

    def _dup_and_changed(ref_no, vehicle_id, date_str, time_str, plaza, amount):
        """Returns (is_dup, changed, existing_id) for a row's reference number."""
        existing = existing_by_ref.get(ref_no) if ref_no else None
        if existing:
            changed = (existing['vehicle_id'] != vehicle_id or existing['date'] != date_str or
                       existing['time'] != time_str or existing['toll_plaza'] != plaza or
                       round(existing['amount'] or 0, 2) != round(amount, 2))
            return True, changed, existing['id']
        if ref_no and ref_no in seen_refs_in_batch:
            return True, False, None  # dup within this same file — nothing saved to diff against
        return False, False, None

    boss_header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=40, values_only=True), 1):
        cells = [str(c).strip().lower() if c is not None else '' for c in row]
        if 'transaction time' in cells and any('nature' in c for c in cells):
            boss_header_row = i
            break

    parsed = []
    skipped_count = 0
    skipped_amount = 0.0

    if boss_header_row:
        fmt = 'boss'
        for row in ws.iter_rows(min_row=boss_header_row + 1, values_only=True):
            if row is None or row[0] is None:
                continue
            txn_time_raw, nature, amount_raw, from_to, desc, truck_no = row[0], row[1], row[2], row[3], row[4], row[5]
            txn_id = row[6] if len(row) > 6 else None
            nature = str(nature).strip().lower() if nature else ''
            desc = str(desc or '').strip()
            desc_l = desc.lower()
            from_to_l = str(from_to or '').strip().lower()
            truck_no = str(truck_no or '').strip().upper()
            try:
                amount = float(amount_raw or 0)
            except (TypeError, ValueError):
                amount = 0

            # The ONE allow-listed shape of a real toll charge — every other combination
            # (BlackBuck advance credits, BOSS recovery debits, top-ups, transfers, refunds,
            # adjustments, balance rows, anything with no truck number) is excluded, not just
            # hidden from the total.
            is_toll_charge = (nature == 'debit' and from_to_l == 'wallet' and 'fastag recharge' in desc_l
                               and bool(truck_no) and amount > 0)
            if not is_toll_charge:
                skipped_count += 1
                if nature == 'debit':
                    skipped_amount += amount  # Recovery-type debits still counted as excluded spend
                continue

            try:
                dt = datetime.datetime.strptime(str(txn_time_raw).strip(), '%d %b %y %I:%M %p')
                date_str, time_str = dt.strftime('%Y-%m-%d'), dt.strftime('%H:%M')
            except (ValueError, TypeError):
                date_str, time_str = '', ''
            vehicle_id = own_vehicles.get(truck_no)
            ref_no = str(txn_id or '').strip()
            toll_plaza = desc or 'FASTag Recharge'

            entry = {'vehicle_no': truck_no, 'vehicle_id': vehicle_id, 'trip_no': '', 'date': date_str,
                     'time': time_str, 'toll_plaza': toll_plaza, 'highway': '', 'state': '',
                     'amount': amount, 'source': 'fastag', 'payment_mode': 'FASTag Wallet',
                     'reference_no': ref_no, 'notes': desc}
            errs = []
            if not vehicle_id: errs.append('Vehicle not in Fleet ERP')
            if not date_str: errs.append('Unrecognised transaction time')
            entry['errors'] = errs
            # Duplicate key is TransactionId alone, per spec — if it already exists (in the DB, or
            # earlier in this same file), skip it rather than ever double-counting a toll. If the
            # existing saved row's own fields differ from this one, flag it as 'changed' so the
            # import step can offer to override instead of just leaving the stale data in place.
            is_dup, changed, existing_id = _dup_and_changed(ref_no, vehicle_id, date_str, time_str, toll_plaza, amount)
            entry['is_dup'] = is_dup
            entry['changed'] = changed
            entry['existing_id'] = existing_id
            entry['valid'] = (len(errs) == 0)
            if entry['valid'] and not entry['is_dup'] and ref_no:
                seen_refs_in_batch.add(ref_no)
            parsed.append(entry)
    else:
        fmt = 'template'
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), [])
        header = [str(c).strip().lower() if c is not None else '' for c in header_row]
        colmap = {h: i for i, h in enumerate(header)}
        def get(row, key):
            idx = colmap.get(key)
            return row[idx] if idx is not None and idx < len(row) else None
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row is None or all(c in (None, '') for c in row):
                continue
            vno = str(get(row, 'vehicle number') or '').strip()
            trip_no = str(get(row, 'trip number') or '').strip()
            date_val = get(row, 'date')
            date_str = date_val.strftime('%Y-%m-%d') if hasattr(date_val, 'strftime') else str(date_val or '').strip()
            time_val = get(row, 'time')
            time_str = time_val.strftime('%H:%M') if hasattr(time_val, 'strftime') else str(time_val or '').strip()
            plaza = str(get(row, 'toll plaza') or '').strip()
            highway = str(get(row, 'highway') or '').strip()
            state = str(get(row, 'state') or '').strip()
            try:
                amount = float(get(row, 'amount') or 0)
            except (TypeError, ValueError):
                amount = 0
            source_raw = str(get(row, 'source') or 'Manual').strip()
            source = 'fastag' if source_raw.lower().startswith('fast') else 'manual'
            payment_mode = str(get(row, 'payment mode') or '').strip()
            ref_no = str(get(row, 'reference number') or '').strip()
            notes = str(get(row, 'notes') or '').strip()

            # Rule: zero/blank amount is ignored outright, same as the BOSS-format branch.
            if amount <= 0:
                skipped_count += 1
                continue

            vehicle_id = own_vehicles.get(vno.upper())
            entry = {'vehicle_no': vno, 'vehicle_id': vehicle_id, 'trip_no': trip_no, 'date': date_str,
                     'time': time_str, 'toll_plaza': plaza, 'highway': highway, 'state': state, 'amount': amount,
                     'source': source, 'payment_mode': payment_mode, 'reference_no': ref_no, 'notes': notes}
            errs = []
            if not vno: errs.append('Missing vehicle number')
            elif not vehicle_id: errs.append('Vehicle not in Fleet ERP')
            if not date_str: errs.append('Missing date')
            if not plaza: errs.append('Missing toll plaza (Warning)')  # non-blocking, see below
            blocking_errs = [e for e in errs if not e.endswith('(Warning)')]
            legacy_key = (vehicle_id, date_str, plaza, round(amount, 2))
            is_dup, changed, existing_id = _dup_and_changed(ref_no, vehicle_id, date_str, time_str, plaza, amount)
            if not is_dup and vehicle_id and date_str and plaza and legacy_key in existing_keys:
                is_dup = True  # no reference number to compare — same old best-effort key match
            entry['errors'] = errs
            entry['is_dup'] = is_dup
            entry['changed'] = changed
            entry['existing_id'] = existing_id
            entry['valid'] = (len(blocking_errs) == 0)
            if entry['valid'] and not entry['is_dup'] and ref_no:
                seen_refs_in_batch.add(ref_no)
            parsed.append(entry)

    return fmt, parsed, skipped_count, skipped_amount

@app.route('/maintenance/toll/excel/preview', methods=['POST'])
def toll_excel_preview():
    import uuid
    conn = get_db()
    file_storage = request.files.get('excel_file')
    if not file_storage or not file_storage.filename:
        conn.close()
        return redirect(url_for('maintenance_list', tab='toll'))
    try:
        fmt, parsed, credit_excl_count, credit_excl_amount = _parse_toll_excel(file_storage, conn)
    except Exception:
        ctx = _toll_tab_base_context(conn)
        conn.close()
        return render_template('maintenance.html', tab='toll', active='maintenance',
            excel_error="Couldn't read that file — make sure it's a .xlsx export from BOSS FASTag, or the downloaded template.",
            **ctx)

    token = uuid.uuid4().hex
    _TOLL_IMPORT_STASH[token] = {'rows': parsed, 'ts': datetime.datetime.now()}
    stale = [k for k, v in _TOLL_IMPORT_STASH.items() if (datetime.datetime.now() - v['ts']).total_seconds() > 1800]
    for k in stale:
        _TOLL_IMPORT_STASH.pop(k, None)

    valid_count = sum(1 for r in parsed if r['valid'] and not r['is_dup'])
    dup_count = sum(1 for r in parsed if r['is_dup'])
    changed_count = sum(1 for r in parsed if r['is_dup'] and r.get('changed'))
    invalid_count = sum(1 for r in parsed if not r['valid'])
    valid_amount = sum(r['amount'] for r in parsed if r['valid'] and not r['is_dup'])

    ctx = _toll_tab_base_context(conn)
    conn.close()
    return render_template('maintenance.html', tab='toll', active='maintenance',
        excel_token=token, excel_format=fmt, excel_rows=parsed[:10], excel_row_count=len(parsed),
        excel_valid_count=valid_count, excel_dup_count=dup_count, excel_changed_count=changed_count,
        excel_invalid_count=invalid_count, excel_valid_amount=valid_amount, excel_credit_excl_count=credit_excl_count,
        excel_credit_excl_amount=credit_excl_amount, **ctx)

@app.route('/maintenance/toll/excel/import', methods=['POST'])
def toll_excel_import():
    token = request.form.get('token')
    auto_link_trips = request.form.get('auto_link_trips') == 'on'
    auto_approve = request.form.get('auto_approve') == 'on'
    # A duplicate TransactionId is NEVER re-inserted as a second row — that would double-count a
    # toll (or a recharge) that already exists. The only choice a re-import offers is whether a
    # duplicate whose own fields actually changed since it was saved (amount corrected, wrong
    # vehicle fixed, etc.) should overwrite the existing record, or be left alone.
    override_changed = request.form.get('override_changed') == 'on'
    stash = _TOLL_IMPORT_STASH.pop(token, None)
    conn = get_db()
    imported = 0
    updated = 0
    if stash:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for row in stash['rows']:
            if not row['valid']:
                continue
            trip_id = None
            if auto_link_trips and row.get('trip_no'):
                trow = conn.execute("SELECT id FROM trips WHERE lr_number=? COLLATE NOCASE ORDER BY date DESC LIMIT 1",
                                     (row['trip_no'],)).fetchone()
                trip_id = trow['id'] if trow else None

            if row['is_dup']:
                if row.get('changed') and override_changed and row.get('existing_id'):
                    existing = conn.execute("SELECT maintenance_id FROM toll_entries WHERE id=?",
                                             (row['existing_id'],)).fetchone()
                    conn.execute("""UPDATE toll_entries SET date=?, time=?, vehicle_id=?, trip_id=?, toll_plaza=?,
                                    highway=?, state=?, amount=?, payment_mode=?, notes=?, updated_by=?, updated_at=? WHERE id=?""",
                                 (row['date'], row['time'] or None, row['vehicle_id'], trip_id, row['toll_plaza'],
                                  row['highway'] or None, row['state'] or None, row['amount'],
                                  row['payment_mode'] or None, row['notes'] or None,
                                  session.get('user_id'), now, row['existing_id']))
                    if existing and existing['maintenance_id']:
                        conn.execute("""UPDATE maintenance SET date=?, vehicle_id=?, amount=?, paid_amount=?, notes=?,
                                        updated_by=?, updated_at=?
                                        WHERE id=?""",
                                     (row['date'], row['vehicle_id'], row['amount'], row['amount'],
                                      row['notes'] or None, session.get('user_id'), now, existing['maintenance_id']))
                    updated += 1
                continue  # unchanged duplicate, or changed-but-override-not-requested: leave as-is

            status = 'synced' if row['source'] == 'fastag' else ('approved' if auto_approve else 'pending')
            cur_m = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, notes, created_by, created_at)
                                    VALUES (?,?,?,?,?,?,?,?)""",
                                  (row['date'], row['vehicle_id'], 'Toll', row['amount'], row['amount'], row['notes'] or None,
                                   session.get('user_id'), now))
            conn.execute("""INSERT INTO toll_entries (date, time, vehicle_id, trip_id, toll_plaza, highway, state,
                            amount, source, payment_mode, status, reference_no, notes, maintenance_id, created_at,
                            created_by)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (row['date'], row['time'] or None, row['vehicle_id'], trip_id, row['toll_plaza'],
                          row['highway'] or None, row['state'] or None, row['amount'], row['source'],
                          row['payment_mode'] or None, status, row['reference_no'] or None, row['notes'] or None,
                          cur_m.lastrowid, now, session.get('user_id')))
            imported += 1
        conn.commit()
    conn.close()
    return redirect(url_for('maintenance_list', tab='toll', imported=imported, updated=updated))

@app.route('/maintenance/insurance/add', methods=['POST'])
def add_insurance():
    conn = get_db()
    f = request.form
    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    # Insurers are companies we pay premiums to, same as any other vendor — reusing the vendor
    # table means the premium lands straight in that insurer's ledger, same as every other cost.
    insurer_id = get_or_create_vendor(conn, f.get('insurer_name'))
    premium = float(f.get('premium_amount') or 0)
    # Paid Amount is separate from Premium, same as Tyre/Battery/Compliance — blank/0 means still
    # payable to the insurer, not automatically fully paid. Previously hardcoded to `premium`,
    # which silently marked every policy as paid in full regardless of reality (premiums are
    # commonly paid via EMI/installments) and made the insurer's ledger balance always read 0.
    paid_amount = float(f.get('paid_amount') or 0)

    cur = conn.execute("""INSERT INTO maintenance (date, vehicle_id, category, amount, paid_amount, vendor_id, notes, status, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f.get('start_date'), vehicle_id, 'Insurance', premium, paid_amount, insurer_id, f.get('notes') or None, 'Completed',
         session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    maintenance_id = cur.lastrowid

    _cid = session.get('company_id', 1)
    policy_doc = _save_insurance_doc(request.files.get('policy_doc'), 'policy', _cid)
    invoice_doc = _save_insurance_doc(request.files.get('invoice_doc'), 'invoice', _cid)
    rc_doc = _save_insurance_doc(request.files.get('rc_doc'), 'rc', _cid)
    status_override = 'Cancelled' if f.get('policy_status') == 'Cancelled' else None

    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""INSERT INTO insurance_policies (vehicle_id, insurance_type, insurer_id, policy_number, start_date,
                    expiry_date, premium_amount, idv, ncb_pct, gst_included, agent_name, agent_contact, agent_email,
                    reminder_days, notes, status_override, policy_doc_path, invoice_doc_path, rc_doc_path, maintenance_id,
                    created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (vehicle_id, f.get('insurance_type'), insurer_id, f.get('policy_number') or None, f.get('start_date'),
         f.get('expiry_date'), premium, float(f.get('idv') or 0) or None, float(f.get('ncb_pct') or 0) or None,
         f.get('gst_included') or None, f.get('agent_name') or None, f.get('agent_contact') or None,
         f.get('agent_email') or None, int(f.get('reminder_days') or 30), f.get('notes') or None, status_override,
         policy_doc, invoice_doc, rc_doc, maintenance_id, session.get('user_id'), _now))

    # Keep the vehicle's own insurance_expiry (shown on the Vehicles page) in sync with this policy.
    if vehicle_id and f.get('expiry_date'):
        conn.execute("UPDATE vehicles SET insurance_expiry=?, updated_by=?, updated_at=? WHERE id=?",
                     (f.get('expiry_date'), session.get('user_id'), _now, vehicle_id))
    conn.commit()
    conn.close()
    return redirect(_safe_next(request.form.get('return_to'), url_for('vehicles_list')))

@app.route('/maintenance/insurance/<int:policy_id>/edit', methods=['POST'])
def edit_insurance(policy_id):
    conn = get_db()
    f = request.form
    policy = conn.execute("SELECT * FROM insurance_policies WHERE id=?", (policy_id,)).fetchone()
    if not policy:
        conn.close()
        return redirect(url_for('vehicles_list', tab='insurance'))

    vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
    insurer_id = get_or_create_vendor(conn, f.get('insurer_name'))
    premium = float(f.get('premium_amount') or 0)
    paid_amount = float(f.get('paid_amount') or 0)

    _cid = session.get('company_id', 1)
    policy_doc = _save_insurance_doc(request.files.get('policy_doc'), 'policy', _cid) or policy['policy_doc_path']
    invoice_doc = _save_insurance_doc(request.files.get('invoice_doc'), 'invoice', _cid) or policy['invoice_doc_path']
    rc_doc = _save_insurance_doc(request.files.get('rc_doc'), 'rc', _cid) or policy['rc_doc_path']
    status_override = 'Cancelled' if f.get('policy_status') == 'Cancelled' else None

    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""UPDATE insurance_policies SET vehicle_id=?, insurance_type=?, insurer_id=?, policy_number=?,
                    start_date=?, expiry_date=?, premium_amount=?, idv=?, ncb_pct=?, gst_included=?, agent_name=?,
                    agent_contact=?, agent_email=?, reminder_days=?, notes=?, status_override=?, policy_doc_path=?,
                    invoice_doc_path=?, rc_doc_path=?, updated_by=?, updated_at=? WHERE id=?""",
        (vehicle_id, f.get('insurance_type'), insurer_id, f.get('policy_number') or None, f.get('start_date'),
         f.get('expiry_date'), premium, float(f.get('idv') or 0) or None, float(f.get('ncb_pct') or 0) or None,
         f.get('gst_included') or None, f.get('agent_name') or None, f.get('agent_contact') or None,
         f.get('agent_email') or None, int(f.get('reminder_days') or 30), f.get('notes') or None, status_override,
         policy_doc, invoice_doc, rc_doc, session.get('user_id'), _now, policy_id))

    if policy['maintenance_id']:
        conn.execute("UPDATE maintenance SET date=?, vehicle_id=?, amount=?, paid_amount=?, vendor_id=?, notes=?, updated_by=?, updated_at=? WHERE id=?",
            (f.get('start_date'), vehicle_id, premium, paid_amount, insurer_id, f.get('notes') or None,
             session.get('user_id'), _now, policy['maintenance_id']))
        _enforce_maintenance_paid_floor(conn, policy['maintenance_id'])
    if vehicle_id and f.get('expiry_date'):
        conn.execute("UPDATE vehicles SET insurance_expiry=?, updated_by=?, updated_at=? WHERE id=?",
                     (f.get('expiry_date'), session.get('user_id'), _now, vehicle_id))
    conn.commit()
    conn.close()
    return redirect(_safe_next(request.form.get('return_to'), url_for('vehicles_list')))

@app.route('/maintenance/insurance/<int:policy_id>/delete', methods=['POST'])
def delete_insurance(policy_id):
    conn = get_db()
    policy = conn.execute("SELECT * FROM insurance_policies WHERE id=?", (policy_id,)).fetchone()
    if policy:
        # Child row before the parent maintenance row it references — see delete_battery()'s own
        # comment on why the reverse order is a real (if currently dormant) bug.
        conn.execute("DELETE FROM insurance_policies WHERE id=?", (policy_id,))
        if policy['maintenance_id']:
            _delete_maintenance_row(conn, policy['maintenance_id'])
        conn.commit()
    conn.close()
    return redirect(url_for('vehicles_list', tab='insurance'))


def _filter_entries_by_date(entries, date_from, date_to):
    if date_from:
        entries = [e for e in entries if (e['date'] or '') >= date_from]
    if date_to:
        entries = [e for e in entries if (e['date'] or '') <= date_to]
    return entries

def _filter_ledger_entries(entries, date_from, date_to, kind_f='', vtype_f=''):
    """Same date filtering as before, plus the new Transaction Type (kind) and vehicle Type filters —
    purely narrowing which already-computed entries are shown; none of their debit/credit/balance
    math is touched here."""
    entries = _filter_entries_by_date(entries, date_from, date_to)
    if kind_f:
        entries = [e for e in entries if e.get('kind') == kind_f]
    if vtype_f:
        entries = [e for e in entries if e.get('vehicle_type') == vtype_f]
    return entries

def _ledger_export_entries(all_entries):
    """Applies the same date/Transaction-Type/vehicle-Type/tab filters the page view uses, so an
    export always matches whatever the user currently has filtered — reads straight from the
    querystring since every export route is a plain GET with the same filter param names as the page."""
    entries = _filter_ledger_entries(all_entries, request.args.get('date_from', ''), request.args.get('date_to', ''),
                                      request.args.get('txn_type', ''), request.args.get('type', ''))
    entries = _tab_filter_entries(entries, request.args.get('tab', ''))
    return entries

def _tab_filter_entries(entries, tab_f):
    if tab_f == 'receivables':
        return [e for e in entries if e['debit'] > 0]
    if tab_f == 'payables':
        return [e for e in entries if e['credit'] > 0]
    return entries

def _ledger_page_context(all_entries):
    """Shared filter/tab/pagination handling for both party_ledger() and vendor_ledger()."""
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    kind_f = request.args.get('txn_type', '')
    vtype_f = request.args.get('type', '')
    tab_f = request.args.get('tab', '')

    entries = _filter_ledger_entries(all_entries, date_from, date_to, kind_f, vtype_f)
    entries = _tab_filter_entries(entries, tab_f)

    total_count = len(entries)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_count,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    page_entries = entries[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)
    tab_params = dict(base_params)
    tab_params.pop('tab', None)
    tab_qs = urlencode(tab_params)

    return {
        'entries': page_entries, 'total_count': total_count, 'page': page, 'per_page': per_page,
        'total_pages': total_pages, 'page_tokens': page_tokens, 'base_qs': base_qs, 'tab_qs': tab_qs,
        'f_date_from': date_from, 'f_date_to': date_to, 'f_txn_type': kind_f, 'f_type': vtype_f, 'f_tab': tab_f or 'all',
    }

@app.route('/ledger/party/<int:party_id>')
def party_ledger(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    pending_trips = _party_pending_trips(conn, party_id)
    conn.close()
    total_pending_trips = sum(t['pending'] for t in pending_trips)
    all_entries = _get_party_ledger_entries(party_id)
    final_balance = all_entries[0]['balance'] if all_entries else 0
    ctx = _ledger_page_context(all_entries)

    return render_template('ledger.html', name=party['name'], role='Party',
                            final_balance=final_balance, payment_url=f'/payment/party/{party_id}', export_url=f'/ledger/party/{party_id}/export',
                            entity_id=party_id, entity_type='party', address=party['address'], contact=party['contact'],
                            email=party['email'], credit_limit=party['credit_limit'], since_date=party['since_date'],
                            opening_balance=party['opening_balance'], opening_balance_date=party['opening_balance_date'],
                            gstin=party['gstin'], category=party['category'], status=party['status'] or 'Active',
                            pending_trips=pending_trips, total_pending_trips=total_pending_trips,
                            return_to=request.full_path, active='accounts', **ctx)

@app.route('/ledger/vendor/<int:vendor_id>')
def vendor_ledger(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if vendor and vendor['linked_party_id']:
        conn.close()
        return redirect(url_for('party_ledger', party_id=vendor['linked_party_id']))
    pending_trips = _vendor_pending_trips(conn, vendor_id)
    pending_maint_total = (sum(m['pending'] for m in _vendor_pending_maintenance(conn, vendor_id))
                           + sum(o['pending'] for o in _vendor_pending_overheads(conn, vendor_id)))
    conn.close()
    total_pending_trips = sum(t['pending'] for t in pending_trips)
    all_entries = _get_vendor_ledger_entries(vendor_id)
    final_balance = all_entries[0]['balance'] if all_entries else 0
    ctx = _ledger_page_context(all_entries)

    return render_template('ledger.html', name=vendor['name'], role='Vendor',
                            final_balance=final_balance, payment_url=f'/payment/vendor/{vendor_id}', export_url=f'/ledger/vendor/{vendor_id}/export',
                            entity_id=vendor_id, entity_type='vendor', address=vendor['address'], contact=vendor['contact'],
                            email=vendor['email'], credit_limit=vendor['credit_limit'], since_date=vendor['since_date'],
                            opening_balance=vendor['opening_balance'], opening_balance_date=vendor['opening_balance_date'],
                            gstin=vendor['gstin'], category=vendor['category'], status=vendor['status'] or 'Active',
                            pending_trips=pending_trips, total_pending_trips=total_pending_trips,
                            pending_maint_total=pending_maint_total,
                            return_to=request.full_path, active='accounts', **ctx)

def _export_ledger_entries(name, entries):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from flask import send_file
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    headers = ["Date","Type","Detail","Ref / Document","Debit","Credit","Balance"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    # Every column wraps within its own cell instead of Excel's default single-line display, which
    # was crowding/overlapping neighbouring columns whenever Detail (or anything else) ran long.
    wrap_top = Alignment(wrap_text=True, vertical='top')
    wrap_top_right = Alignment(wrap_text=True, vertical='top', horizontal='right')
    for r_idx, e in enumerate(entries, 2):
        ws.cell(row=r_idx, column=1, value=e['date']).alignment = wrap_top
        ws.cell(row=r_idx, column=2, value=e.get('kind', '')).alignment = wrap_top
        ws.cell(row=r_idx, column=3, value=e['detail']).alignment = wrap_top
        ws.cell(row=r_idx, column=4, value=e.get('ref', '')).alignment = wrap_top
        ws.cell(row=r_idx, column=5, value=e['debit'] or None).alignment = wrap_top_right
        ws.cell(row=r_idx, column=6, value=e['credit'] or None).alignment = wrap_top_right
        bal_suffix = ' Dr' if e['balance'] > 0 else ' Cr' if e['balance'] < 0 else ''
        ws.cell(row=r_idx, column=7, value=f"{abs(e['balance']):,.0f}{bal_suffix}").alignment = wrap_top_right
    widths = {'A': 12, 'B': 14, 'C': 46, 'D': 20, 'E': 14, 'F': 14, 'G': 14}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'ledger_{safe_name}.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def _lr_label(lr_number, trip_id):
    """LR numbers in this data are sometimes stored already prefixed with '#' — avoid stacking a second one."""
    val = lr_number or trip_id
    val = str(val)
    return val if val.startswith('#') else f"#{val}"

def _payment_base_detail(p, label):
    parts = [label]
    if p['mode']:
        parts.append(f"({p['mode']})")
    detail = ' '.join(parts)
    if p['reference_id']:
        detail += f" — Ref: {p['reference_id']}"
    if p['remarks']:
        detail += f" | {p['remarks']}"
    return detail

def _get_party_ledger_entries(party_id, conn=None):
    # Optional conn — lets a caller that already has a connection open (e.g. _accounts_rows(),
    # looping over every party+vendor to build the Ledger/Dashboard summary) reuse it instead of
    # this function opening a brand new SQLite connection every single call. Same queries, same
    # results either way; every existing call site (which never passes one) is unaffected — conn
    # is opened and closed here exactly as it always was. Only closed at the end if THIS call
    # opened it, and only after the linked-vendor sub-call below (also passed the same conn), so
    # one party with a linked vendor now costs at most one connection instead of two.
    owns_conn = conn is None
    if owns_conn:
        conn = get_db()
    party = conn.execute("SELECT opening_balance, opening_balance_date, since_date FROM parties WHERE id=?", (party_id,)).fetchone()
    trips = conn.execute("""SELECT id, date, lr_number, from_loc, to_loc, billed_amount, payment_received, party_advance, type
                             FROM trips WHERE party_id=? ORDER BY date""", (party_id,)).fetchall()
    # How much of each trip's payment_received came from a ledger payment allocation (vs. being
    # recorded directly on the trip itself) — so the trip's own row isn't inflated with money
    # that actually arrived, and should be shown, on a later payment date.
    trip_alloc = {}
    for row in conn.execute("""SELECT pa.trip_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN trips t ON pa.trip_id=t.id WHERE t.party_id=? GROUP BY pa.trip_id""", (party_id,)).fetchall():
        trip_alloc[row['trip_id']] = row['amt'] or 0
    # Real invoice number for a trip, if one's been generated yet — shown in Ref/Document ahead
    # of the LR number, since that's the more official document once it exists.
    trip_invoice_no = {}
    for row in conn.execute("SELECT trip_id, invoice_number FROM invoices WHERE trip_id IS NOT NULL").fetchall():
        if row['invoice_number']:
            trip_invoice_no[row['trip_id']] = row['invoice_number']
    payments = conn.execute("""SELECT id, date, amount, allocated_amount, mode, reference_id, remarks FROM payments
                                WHERE party_id=? AND payment_type='received' ORDER BY date""", (party_id,)).fetchall()
    linked_vendor = conn.execute("SELECT id FROM vendors WHERE linked_party_id=?", (party_id,)).fetchone()
    entries = []
    if party and party['opening_balance']:
        ob = party['opening_balance']
        entries.append({'date': party['opening_balance_date'] or party['since_date'] or '', 'detail': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0), 'kind': 'Opening Balance', 'ref': '', 'vehicle_type': ''})
    for t in trips:
        # Floored at 0 — same reasoning as the vendor-side owner_trips loop in
        # _get_vendor_ledger_entries: a negative value here is just payment_received having
        # fallen out of sync with (behind) what a real Payment allocation already recorded for
        # this trip, not a real amount. Left un-floored it would double-subtract a payment
        # that's already correctly shown via its own "Payment received" row below.
        original_received = max((t['payment_received'] or 0) - trip_alloc.get(t['id'], 0), 0)
        # Short place names (same _clean_loc used on Route Analytics), not the full stored address
        # — a trip's from/to can be a 200+ character pasted address, which has no business being
        # spelled out in full on every ledger row.
        entries.append({'date': t['date'], 'detail': f"Trip: {_lr_label(t['lr_number'], t['id'])} — {_clean_loc(t['from_loc'])} → {_clean_loc(t['to_loc'])}",
                         'debit': t['billed_amount'] or 0, 'credit': original_received + (t['party_advance'] or 0),
                         'kind': 'Trip Bill', 'ref': trip_invoice_no.get(t['id']) or t['lr_number'] or '', 'vehicle_type': t['type'] or '',
                         'link': url_for('trip_view', trip_id=t['id'])})
    for p in payments:
        base_detail = _payment_base_detail(p, 'Payment received')
        allocs = conn.execute("""SELECT t.lr_number, pa.amount FROM payment_allocations pa
                                 JOIN trips t ON pa.trip_id=t.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to {a['lr_number'] or 'trip'}",
                             'debit': 0, 'credit': a['amount'], 'kind': 'Payment In', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        if leftover > 0.004 or not allocs:
            entries.append({'date': p['date'], 'detail': base_detail, 'debit': 0, 'credit': max(leftover, 0),
                             'kind': 'Payment In', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
    if linked_vendor:
        # Same organization also acts as a vendor (fuel/maintenance/owner-hire) — pull those in too,
        # so this one ledger reflects everything, instead of splitting across two disconnected
        # records. Passes this same conn through — still open at this point regardless of who
        # opened it — so this costs no extra connection beyond the one already in use.
        vendor_entries = _get_vendor_ledger_entries(linked_vendor['id'], conn=conn)
        for ve in vendor_entries:
            entries.append({'date': ve['date'], 'detail': ve['detail'],
                             'debit': ve['debit'], 'credit': ve['credit'],
                             'kind': ve.get('kind', 'Expense Adj.'), 'ref': ve.get('ref', ''), 'vehicle_type': ve.get('vehicle_type', ''),
                             'link': ve.get('link')})
    if owns_conn:
        conn.close()
    entries.sort(key=lambda x: x['date'] or '')
    balance = 0
    for e in entries:
        balance += e['debit'] - e['credit']
        e['balance'] = balance
    entries.reverse()
    return entries

def _get_vendor_ledger_entries(vendor_id, conn=None):
    # Same optional-conn reuse as _get_party_ledger_entries() just above — see its own comment.
    owns_conn = conn is None
    if owns_conn:
        conn = get_db()
    vendor = conn.execute("SELECT opening_balance, opening_balance_date, since_date FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    maint = conn.execute("""SELECT m.id, m.date, m.category, m.service_type, m.tyre_action, m.tyre_id, m.invoice_no,
                             m.amount, m.paid_amount, v.vehicle_no, b.battery_no, ip.policy_number
                             FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id=v.id
                             LEFT JOIN batteries b ON b.maintenance_id=m.id
                             LEFT JOIN insurance_policies ip ON ip.maintenance_id=m.id
                             WHERE m.vendor_id=? ORDER BY m.date""", (vendor_id,)).fetchall()
    # Skip trips where this same vendor is also the vehicle owner (owner_vendor_id) on the same
    # trip — that means the fuel/driver advance was paid by the owner out of their own freight
    # money, not a separate expense we owe them for. It's already inside the Trip Bill credit
    # below; counting it again here would double it.
    fuel = conn.execute("""SELECT t.id, t.date, t.lr_number, t.fuel_amount, t.type, v.vehicle_no
                           FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           WHERE t.fuel_vendor_id=? AND (t.owner_vendor_id IS NULL OR t.owner_vendor_id != t.fuel_vendor_id)
                           ORDER BY t.date""", (vendor_id,)).fetchall()
    adv = conn.execute("""SELECT t.id, t.date, t.lr_number, t.driver_adv_amount, t.type, v.vehicle_no
                          FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                          WHERE t.driver_adv_vendor_id=? AND (t.owner_vendor_id IS NULL OR t.owner_vendor_id != t.driver_adv_vendor_id)
                          ORDER BY t.date""", (vendor_id,)).fetchall()
    owner_trips = conn.execute("""SELECT t.id, t.date, t.lr_number, t.rate_type, t.fixed_rate_amount, t.owner_rate,
                                  t.owner_rate_type, t.owner_fixed_amount,
                                  t.quantity, t.paid_to_owner, t.type, v.vehicle_no,
                                  t.fuel_vendor_id, t.fuel_amount, t.driver_adv_vendor_id, t.driver_adv_amount
                                  FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                  WHERE t.owner_vendor_id=? ORDER BY t.date""", (vendor_id,)).fetchall()
    # Trip "Others" items explicitly tagged with this vendor (e.g. a second fuel top-up or an
    # advance handled by someone other than the trip's usual fuel/advance vendor) — same table the
    # trip's own invoice reads from, just also attributed to a vendor here.
    other_items = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, t.date, t.lr_number, t.id as trip_id,
                                  v.vehicle_no, t.type FROM invoice_items ii JOIN trips t ON ii.trip_id=t.id
                                  LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                  WHERE ii.vendor_id=? ORDER BY t.date""", (vendor_id,)).fetchall()
    # Same idea, but for a generated (batch/consolidated) invoice's own "Additional Line Items" —
    # added from the Edit Invoice screen, e.g. "Detention charge" tagged to a specific vendor.
    batch_other_items = conn.execute("""SELECT ibi.description, ibi.amount, ibi.item_type, ib.invoice_date, ib.invoice_number,
                                        ib.id as batch_id FROM invoice_batch_items ibi
                                        JOIN invoice_batches ib ON ibi.invoice_batch_id=ib.id
                                        WHERE ibi.vendor_id=? ORDER BY ib.invoice_date""", (vendor_id,)).fetchall()
    # Ledger-allocated portion of each owner-hire trip's paid_to_owner (see party-side comment above).
    trip_alloc = {}
    for row in conn.execute("""SELECT pa.trip_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN trips t ON pa.trip_id=t.id WHERE t.owner_vendor_id=? GROUP BY pa.trip_id""", (vendor_id,)).fetchall():
        trip_alloc[row['trip_id']] = row['amt'] or 0
    # Same idea for maintenance bills: the part of m.paid_amount that came from a ledger payment is
    # shown on that payment's own "Applied to" row, so the bill row only carries what was paid up front.
    maint_alloc = {}
    for row in conn.execute("""SELECT pa.maintenance_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN maintenance m ON pa.maintenance_id=m.id WHERE m.vendor_id=? GROUP BY pa.maintenance_id""", (vendor_id,)).fetchall():
        maint_alloc[row['maintenance_id']] = row['amt'] or 0
    # Expense bills (Expenses page) naming this vendor: a Pending bill is something we owe. Shown as a
    # credit; the part settled by ledger payments is shown on that payment's own "Applied to" row, and
    # a bill marked Paid by hand is shown as fully debited — so the bill, once settled either way,
    # nets to zero and never leaves the vendor looking overpaid.
    overhead_alloc = {}
    for row in conn.execute("""SELECT pa.overhead_id, SUM(pa.amount) as amt FROM payment_allocations pa
                               JOIN overheads o ON pa.overhead_id=o.id
                               WHERE LOWER(TRIM(COALESCE(o.vendor,'')))=LOWER(TRIM(COALESCE((SELECT name FROM vendors WHERE id=?),'')))
                               GROUP BY pa.overhead_id""", (vendor_id,)).fetchall():
        overhead_alloc[row['overhead_id']] = row['amt'] or 0
    vendor_overheads = conn.execute("""SELECT o.id, o.date, o.category, o.description, o.amount, COALESCE(o.status,'Paid') AS status, o.receipt_number
                                       FROM overheads o
                                       WHERE LOWER(TRIM(COALESCE(o.vendor,'')))=LOWER(TRIM(COALESCE((SELECT name FROM vendors WHERE id=?),'')))
                                         AND TRIM(COALESCE(o.vendor,'')) != ''
                                       ORDER BY o.date""", (vendor_id,)).fetchall()
    # Real invoice number for a trip, if one's been generated — same lookup the party side uses.
    # Fuel/Driver Advance/owner-hire rows below identify themselves by vehicle number first (the
    # more useful identifier day-to-day); when a trip has no vehicle logged, the invoice number is
    # shown instead so the row is never left with no way to trace it back to a real trip.
    trip_invoice_no = {}
    for row in conn.execute("SELECT trip_id, invoice_number FROM invoices WHERE trip_id IS NOT NULL").fetchall():
        if row['invoice_number']:
            trip_invoice_no[row['trip_id']] = row['invoice_number']
    payments = conn.execute("""SELECT id, date, amount, allocated_amount, mode, reference_id, remarks FROM payments
                                WHERE vendor_id=? AND payment_type='paid' ORDER BY date""", (vendor_id,)).fetchall()
    entries = []
    if vendor and vendor['opening_balance']:
        ob = vendor['opening_balance']
        entries.append({'date': vendor['opening_balance_date'] or vendor['since_date'] or '', 'detail': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0), 'kind': 'Opening Balance', 'ref': '', 'vehicle_type': ''})
    for m in maint:
        # Just what's needed to identify the entry — category (Battery/Tyre/Service/etc.), which
        # vehicle, and the one specific reference that applies (a tyre position, a battery number,
        # a policy number — never more than one, since a row is only ever one of those). The
        # invoice number goes in Ref, not piled into Detail — that column already exists for it.
        label = m['service_type'] or m['tyre_action'] or m['category'] or 'Maintenance'
        detail = f"Maintenance: {label}" + (f" — {m['vehicle_no']}" if m['vehicle_no'] else '')
        specific_ref = (f"Tyre {m['tyre_id']}" if m['tyre_id'] else
                        m['battery_no'] if m['battery_no'] else
                        f"Policy {m['policy_number']}" if m['policy_number'] else '')
        if specific_ref:
            detail += f" ({specific_ref})"
        entries.append({'date': m['date'], 'detail': detail,
                         'debit': max((m['paid_amount'] or 0) - maint_alloc.get(m['id'], 0), 0), 'credit': m['amount'] or 0,
                         'kind': 'Expense Adj.', 'ref': m['invoice_no'] or '', 'vehicle_type': '',
                         'link': url_for('maintenance_view', m_id=m['id'])})
    for o in vendor_overheads:
        alloc_o = overhead_alloc.get(o['id'], 0)
        if o['status'] != 'Pending' and alloc_o <= 0.004:
            continue            # paid directly when it was entered — nothing owed, nothing to show here
        label = o['category'] or 'Expense'
        entries.append({'date': o['date'], 'detail': f"Expense: {label}" + (f" — {o['description']}" if o['description'] else ''),
                         'debit': max((o['amount'] or 0) - alloc_o, 0) if o['status'] == 'Paid' else 0, 'credit': o['amount'] or 0,
                         'kind': 'Expense Adj.', 'ref': o['receipt_number'] or '', 'vehicle_type': '',
                         'link': url_for('overheads_list')})
    for f in fuel:
        detail = f"Trip: {_lr_label(f['lr_number'], f['id'])} — Fuel"
        entries.append({'date': f['date'], 'detail': detail, 'debit': 0, 'credit': f['fuel_amount'] or 0,
                         'kind': 'Expense Adj.', 'ref': trip_invoice_no.get(f['id'], ''), 'vehicle_type': f['type'] or '',
                         'link': url_for('trip_view', trip_id=f['id'])})
    for a in adv:
        detail = f"Trip: {_lr_label(a['lr_number'], a['id'])} — Driver Advance"
        entries.append({'date': a['date'], 'detail': detail, 'debit': 0, 'credit': a['driver_adv_amount'] or 0,
                         'kind': 'Expense Adj.', 'ref': trip_invoice_no.get(a['id'], ''), 'vehicle_type': a['type'] or '',
                         'link': url_for('trip_view', trip_id=a['id'])})
    for o in owner_trips:
        owed = o['owner_fixed_amount'] if (o['owner_rate_type'] or 'PER_MT')=='FIXED' else (o['owner_rate'] or 0) * (o['quantity'] or 0)
        if owed:
            # Floored at 0 — a negative value here doesn't represent any real amount owed or
            # paid, it's an artifact of paid_to_owner having fallen out of sync with (behind)
            # what's actually recorded via real Payment allocations for this trip (e.g. someone
            # re-saved the trip via Edit Trip after a Payment had already set paid_to_owner,
            # silently resetting it — see edit_trip's floor-protection below, which stops this
            # going forward). Left un-floored, this line double-subtracts the same payment that's
            # already correctly credited via its own "Payment Out" row elsewhere in this same
            # ledger, making the running balance look like more is still owed than really is.
            original_paid = max((o['paid_to_owner'] or 0) - trip_alloc.get(o['id'], 0), 0)
            detail = f"Trip: {_lr_label(o['lr_number'], o['id'])} — vehicle hire"
            entries.append({'date': o['date'], 'detail': detail,
                             'debit': original_paid, 'credit': owed,
                             'kind': 'Trip Bill', 'ref': trip_invoice_no.get(o['id']) or o['lr_number'] or '', 'vehicle_type': o['type'] or '',
                             'link': url_for('trip_view', trip_id=o['id'])})
            # Fuel/driver advance is deducted from what the owner's owed whenever it wasn't the
            # owner's own money — i.e. anything != the owner himself, which includes a blank vendor
            # (untagged means the company paid it directly, no vendor to track) as well as a real
            # different vendor (who also gets their own payable via the fuel/adv loops above). The
            # ONLY case that's excluded is the vendor field explicitly set to the owner himself —
            # that's the deliberate signal that he paid it out of his own freight money.
            if o['fuel_vendor_id'] != vendor_id and o['fuel_amount']:
                fdetail = f"Trip: {_lr_label(o['lr_number'], o['id'])} — Fuel (Company Paid)"
                entries.append({'date': o['date'], 'detail': fdetail,
                                 'debit': o['fuel_amount'], 'credit': 0,
                                 'kind': 'Expense Adj.', 'ref': trip_invoice_no.get(o['id']) or o['lr_number'] or '', 'vehicle_type': o['type'] or '',
                                 'link': url_for('trip_view', trip_id=o['id'])})
            if o['driver_adv_vendor_id'] != vendor_id and o['driver_adv_amount']:
                adetail = f"Trip: {_lr_label(o['lr_number'], o['id'])} — Driver Advance (Company Paid)"
                entries.append({'date': o['date'], 'detail': adetail,
                                 'debit': o['driver_adv_amount'], 'credit': 0,
                                 'kind': 'Expense Adj.', 'ref': trip_invoice_no.get(o['id']) or o['lr_number'] or '', 'vehicle_type': o['type'] or '',
                                 'link': url_for('trip_view', trip_id=o['id'])})
    for it in other_items:
        detail = f"Trip: {_lr_label(it['lr_number'], it['trip_id'])} — {it['description']}"
        amt = it['amount'] or 0
        # Always a credit — a vendor tagged on an "Others" item supplied something real for the
        # trip (fuel, a service, etc.), so we owe them for it regardless of how that same item is
        # classified on the trip's own side. 'Addition'/'Deduction' on the trip form only answers
        # whether this item raises or lowers the TRIP's own profit/party billing — a completely
        # separate question from whether the vendor is owed money, and reusing that flag here used
        # to make a vendor-supplied cost read as reducing (or even reversing) what we owe them.
        entries.append({'date': it['date'], 'detail': detail,
                         'debit': 0, 'credit': amt,
                         'kind': 'Expense Adj.', 'ref': trip_invoice_no.get(it['trip_id']) or it['lr_number'] or '',
                         'vehicle_type': it['type'] or '', 'link': url_for('trip_view', trip_id=it['trip_id'])})
    for it in batch_other_items:
        # Same "always a credit" reasoning as the trip-level Others items above — a vendor tagged
        # on a generated invoice's Additional Line Item supplied something real for that invoice,
        # so it's owed regardless of whether the item reads as a Charge or Deduction on the
        # invoice's own total.
        detail = f"Invoice: {it['invoice_number']} — {it['description']}"
        entries.append({'date': it['invoice_date'], 'detail': detail,
                         'debit': 0, 'credit': it['amount'] or 0,
                         'kind': 'Expense Adj.', 'ref': it['invoice_number'] or '',
                         'vehicle_type': '', 'link': url_for('invoice_batch_edit', batch_id=it['batch_id'])})
    for p in payments:
        base_detail = _payment_base_detail(p, 'Payment made')
        allocs = conn.execute("""SELECT t.lr_number, pa.amount FROM payment_allocations pa
                                 JOIN trips t ON pa.trip_id=t.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to {a['lr_number'] or 'trip'}",
                             'debit': a['amount'], 'credit': 0, 'kind': 'Payment Out', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
        m_allocs = conn.execute("""SELECT m.category, m.service_type, m.tyre_action, pa.amount FROM payment_allocations pa
                                   JOIN maintenance m ON pa.maintenance_id=m.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in m_allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to Maintenance: {_maintenance_label(a)}",
                             'debit': a['amount'], 'credit': 0, 'kind': 'Payment Out', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
        o_allocs = conn.execute("""SELECT o.category, pa.amount FROM payment_allocations pa
                                   JOIN overheads o ON pa.overhead_id=o.id WHERE pa.payment_id=? ORDER BY pa.id""", (p['id'],)).fetchall()
        for a in o_allocs:
            entries.append({'date': p['date'], 'detail': f"{base_detail} — Applied to Expense: {a['category'] or 'bill'}",
                             'debit': a['amount'], 'credit': 0, 'kind': 'Payment Out', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
        allocs = list(allocs) + list(m_allocs) + list(o_allocs)
        leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
        if leftover > 0.004 or not allocs:
            entries.append({'date': p['date'], 'detail': base_detail, 'debit': max(leftover, 0), 'credit': 0,
                             'kind': 'Payment Out', 'ref': p['reference_id'] or '', 'vehicle_type': '',
                             'link': url_for('payment_view', payment_id=p['id'])})
    if owns_conn:
        conn.close()
    entries.sort(key=lambda x: x['date'] or '')
    balance = 0
    for e in entries:
        balance += e['debit'] - e['credit']
        e['balance'] = balance
    entries.reverse()
    return entries

@app.route('/ledger/party/<int:party_id>/export')
def export_party_ledger(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    conn.close()
    entries = _get_party_ledger_entries(party_id)
    entries = _ledger_export_entries(entries)
    return _export_ledger_entries(party['name'], entries)

@app.route('/ledger/vendor/<int:vendor_id>/export')
def export_vendor_ledger(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    conn.close()
    entries = _get_vendor_ledger_entries(vendor_id)
    entries = _ledger_export_entries(entries)
    return _export_ledger_entries(vendor['name'], entries)

def _export_ledger_pdf(name, entries, role='', contact='', email='', address=''):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    from xml.sax.saxutils import escape as esc
    import io

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=17, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=8.5, textColor=colors.HexColor('#5A6B8C'), alignment=1)
    title_style = ParagraphStyle('T', parent=styles['Heading2'], fontSize=13, textColor=colors.HexColor('#1B2A4A'))
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'))
    # Every text column below is wrapped in this instead of passed as a raw string — a raw string
    # in a reportlab Table cell never wraps, it just overflows into the next column (that was the
    # "Detail overriding the other columns" bug), so a long trip detail line or party name needs
    # this to stay inside its own column and grow the row's height instead.
    cell_style = ParagraphStyle('CE', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#1A1A1A'), leading=10)
    num_style = ParagraphStyle('CN', parent=cell_style, alignment=2)  # right-aligned, same wrap behaviour
    def cell(text):
        return Paragraph(esc(str(text)), cell_style) if text else ''
    def num_cell(text):
        return Paragraph(esc(str(text)), num_style) if text else ''

    conn = get_db()
    s = _get_invoice_settings(conn, session.get('company_id', 1))
    conn.close()
    company_name = s['company_name'] or get_company_name(session.get('company_id', 1))
    company_contact_line = ' &nbsp;|&nbsp; '.join(x for x in [
        (f"GSTIN No.: {esc(s['gstin'])}" if s['gstin'] else ''),
        (f"Mob. {esc(s['phone'])}" if s['phone'] else '')] if x)

    story = [Paragraph(esc(company_name), company_style),
             Paragraph(esc(s['address'] or ''), sub_style),
             Paragraph(company_contact_line, sub_style),
             Spacer(1, 4)]
    line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
    line_table.setStyle(TableStyle([('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#1B2A4A'))]))
    story.append(line_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"Statement of Account — {esc(name)}", title_style))
    contact_line = " &nbsp;|&nbsp; ".join([esc(p) for p in [contact, email, address] if p])
    if contact_line:
        story.append(Paragraph(contact_line, label_style))
    story.append(Spacer(1, 14))

    rows = [['Date', 'Type', 'Detail', 'Ref', 'Debit (Rs.)', 'Credit (Rs.)', 'Balance (Rs.)']]
    for e in entries:
        bal_suffix = ' Dr' if e['balance'] > 0 else ' Cr' if e['balance'] < 0 else ''
        rows.append([cell(e['date'] or ''), cell(e.get('kind', '')), cell(e['detail'] or ''), cell(e.get('ref', '')),
                     num_cell(f"{e['debit']:,.0f}") if e['debit'] else '', num_cell(f"{e['credit']:,.0f}") if e['credit'] else '',
                     num_cell(f"{abs(e['balance']):,.0f}{bal_suffix}")])
    t = Table(rows, colWidths=[0.75*inch, 0.85*inch, 2.15*inch, 0.85*inch, 0.85*inch, 0.85*inch, 0.9*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (4,0), (6,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'ledger_{safe_name}.pdf', mimetype='application/pdf')

@app.route('/ledger/party/<int:party_id>/export/pdf')
def export_party_ledger_pdf(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    conn.close()
    entries = _get_party_ledger_entries(party_id)
    entries = _ledger_export_entries(entries)
    return _export_ledger_pdf(party['name'], entries, role='Party', contact=party['contact'] or '', email=party['email'] or '', address=party['address'] or '')

@app.route('/ledger/vendor/<int:vendor_id>/export/pdf')
def export_vendor_ledger_pdf(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    conn.close()
    entries = _get_vendor_ledger_entries(vendor_id)
    entries = _ledger_export_entries(entries)
    return _export_ledger_pdf(vendor['name'], entries, role='Vendor', contact=vendor['contact'] or '', email=vendor['email'] or '', address=vendor['address'] or '')

# ---- shared money parsing + pending-item helpers (used by the payment engine and the popups) ----
_MAX_MONEY = 10_000_000_000      # ₹1,000 crore — anything above is a typo, not a payment

def _parse_money(raw, label, required=False, allow_zero=True, allow_negative=False):
    """One strict place that turns a typed amount into a number. Returns (value, error_or_None).
    Rejects text, NaN/inf, 1e400, negatives (unless allowed), zero (unless allowed) and absurdly
    large values, and rounds to paise — so a bad amount becomes a readable message, never a 500
    and never a silently-recorded nonsense payment. Commas and a leading ₹ are tolerated."""
    s = ('' if raw is None else str(raw)).strip().replace(',', '').replace('₹', '').strip()
    if s == '':
        return (None, f'{label} is required.') if required else (0.0, None)
    try:
        v = float(s)
    except ValueError:
        return None, f'{label} must be a number.'
    if v != v or v in (float('inf'), float('-inf')):
        return None, f'{label} must be a real number.'
    v = round(v, 2)
    if v < 0 and not allow_negative:
        return None, f'{label} cannot be negative.'
    if v == 0 and not allow_zero:
        return None, f'{label} must be greater than zero.'
    if abs(v) > _MAX_MONEY:
        return None, f'{label} is too large.'
    return v, None

def _invoice_numbers_error(f):
    """Loading/Other charges, TDS % and GST % typed on an invoice form: real non-negative numbers
    (rates 0-100) or an error message — never a 500 and never a nonsense invoice."""
    for key, label, cap in (('loading_charges', 'Loading charges', None), ('other_charges', 'Other charges', None),
                            ('tds_rate', 'TDS %', 100), ('gst_rate', 'GST %', 100)):
        v, err = _parse_money(f.get(key), label)
        if err:
            return err
        if cap is not None and v > cap:
            return f'{label} cannot be more than {cap}.'
    return None

_OWNER_OWED_SQL = "(CASE WHEN t.owner_rate_type='FIXED' THEN COALESCE(t.owner_fixed_amount,0) ELSE COALESCE(t.owner_rate,0)*COALESCE(t.quantity,0) END)"
# Fuel / driver advance the COMPANY paid on an owner-hire trip is deducted from what the owner is owed
# (see _get_vendor_ledger_entries) — the payment popup has to net it too or it over-states the dues.
_OWNER_COMPANY_PAID_SQL = ("(CASE WHEN COALESCE(t.fuel_vendor_id,0) != t.owner_vendor_id THEN COALESCE(t.fuel_amount,0) ELSE 0 END"
                           " + CASE WHEN COALESCE(t.driver_adv_vendor_id,0) != t.owner_vendor_id THEN COALESCE(t.driver_adv_amount,0) ELSE 0 END)")

def _party_pending_trips(conn, party_id):
    """Trips this party still owes on, oldest first — the rows the Add Payment popup offers."""
    rows = conn.execute("""SELECT id, date, lr_number, from_loc, to_loc, billed_amount,
                           (billed_amount - COALESCE(payment_received,0) - COALESCE(party_advance,0)) as pending
                           FROM trips WHERE party_id=?
                           AND (billed_amount - COALESCE(payment_received,0) - COALESCE(party_advance,0)) > 0.004
                           ORDER BY date, id""", (party_id,)).fetchall()
    return [{'id': t['id'], 'date': t['date'], 'lr_number': t['lr_number'], 'from_loc': t['from_loc'],
             'to_loc': t['to_loc'], 'vehicle_no': None, 'billed_amount': t['billed_amount'] or 0,
             'pending': t['pending'], 'paid': (t['billed_amount'] or 0) - t['pending']} for t in rows]

def _vendor_pending_trips(conn, vendor_id):
    """Owner-hire trips this vendor is still owed on (net of what we already paid the owner and of
    fuel/driver advance the company paid on his behalf), oldest first."""
    rows = conn.execute(f"""SELECT t.id, t.date, t.lr_number, t.from_loc, t.to_loc, v.vehicle_no,
                            {_OWNER_OWED_SQL} as billed_amount,
                            ({_OWNER_OWED_SQL} - COALESCE(t.paid_to_owner,0) - {_OWNER_COMPANY_PAID_SQL}) as pending
                            FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id WHERE t.owner_vendor_id=?
                            AND ({_OWNER_OWED_SQL} - COALESCE(t.paid_to_owner,0) - {_OWNER_COMPANY_PAID_SQL}) > 0.004
                            ORDER BY t.date, t.id""", (vendor_id,)).fetchall()
    return [{'id': t['id'], 'date': t['date'], 'lr_number': t['lr_number'], 'from_loc': t['from_loc'],
             'to_loc': t['to_loc'], 'vehicle_no': t['vehicle_no'], 'billed_amount': t['billed_amount'] or 0,
             'pending': t['pending'], 'paid': (t['billed_amount'] or 0) - t['pending']} for t in rows]

def _vendor_pending_maintenance(conn, vendor_id):
    """Maintenance / tyre / battery / service / insurance bills from this vendor that aren't fully paid."""
    return conn.execute("""SELECT m.id, m.date, m.category, m.service_type, m.tyre_action, m.amount, m.paid_amount,
                           (COALESCE(m.amount,0) - COALESCE(m.paid_amount,0)) as pending
                           FROM maintenance m WHERE m.vendor_id=? AND (COALESCE(m.amount,0) - COALESCE(m.paid_amount,0)) > 0.004
                           ORDER BY m.date, m.id""", (vendor_id,)).fetchall()

def _overhead_alloc_sum(conn, o_id):
    return conn.execute("SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE overhead_id=?", (o_id,)).fetchone()[0]

def _vendor_pending_overheads(conn, vendor_id):
    """Expense bills (Expenses page) entered as Pending against this vendor's name that still have
    something left to pay, oldest first. 'Left to pay' = amount minus whatever ledger payments were
    already applied to the bill."""
    v = conn.execute("SELECT name FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if not v:
        return []
    rows = conn.execute("""SELECT o.id, o.date, o.category, o.description, o.amount,
                           COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.overhead_id=o.id),0) AS allocated
                           FROM overheads o
                           WHERE COALESCE(o.status,'Paid')='Pending' AND LOWER(TRIM(COALESCE(o.vendor,'')))=LOWER(TRIM(?))
                           ORDER BY o.date, o.id""", (v['name'],)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['pending'] = (d['amount'] or 0) - d['allocated']
        if d['pending'] > 0.004:
            out.append(d)
    return out

def _settle_overhead_status(conn, o_id):
    """A bill whose ledger payments now cover its whole amount is Paid — keeps the Expenses page's
    Pending Bills in step with the vendor's ledger."""
    row = conn.execute("SELECT amount, COALESCE(status,'Paid') AS status FROM overheads WHERE id=?", (o_id,)).fetchone()
    if row and row['status'] == 'Pending' and (row['amount'] or 0) > 0 and _overhead_alloc_sum(conn, o_id) >= row['amount'] - 0.004:
        conn.execute("UPDATE overheads SET status='Paid', due_date=NULL WHERE id=?", (o_id,))

def _delete_overhead_row(conn, o_id):
    """Delete an expense without orphaning a ledger payment applied to it: the payment stays
    recorded and simply becomes (part) unallocated again."""
    for pa in conn.execute("SELECT payment_id, amount FROM payment_allocations WHERE overhead_id=?", (o_id,)).fetchall():
        conn.execute("UPDATE payments SET allocated_amount = MAX(COALESCE(allocated_amount,0) - ?, 0) WHERE id=?",
                     (pa['amount'], pa['payment_id']))
    conn.execute("DELETE FROM payment_allocations WHERE overhead_id=?", (o_id,))
    conn.execute("DELETE FROM overheads WHERE id=?", (o_id,))

def _company_paid_on_hired_trip(t, kind):
    """Fuel ('fuel') or driver advance ('driver_adv') the COMPANY paid on a hired-vehicle trip, i.e.
    money to take off what the owner is owed. Counts when the recorded vendor is blank (the company
    paid directly) or a third party (the company owes that vendor); it does NOT count when the
    vendor is the owner himself — that is his own money, inside his freight. This is the single rule
    the vendor ledger, the Add Payment popup and the owner (Freight Bill) invoice all follow."""
    col = 'fuel' if kind == 'fuel' else 'driver_adv'
    amount = t[f'{col}_amount'] or 0
    if not amount:
        return 0
    return amount if t[f'{col}_vendor_id'] != t['owner_vendor_id'] else 0

def _maintenance_label(row):
    return row['service_type'] or row['tyre_action'] or row['category'] or 'Maintenance'

def _delete_maintenance_row(conn, m_id):
    """Delete a maintenance row without orphaning a ledger payment that was applied to it: the
    payment stays recorded, it just becomes (part) unallocated again."""
    for pa in conn.execute("SELECT payment_id, amount FROM payment_allocations WHERE maintenance_id=?", (m_id,)).fetchall():
        conn.execute("UPDATE payments SET allocated_amount = MAX(COALESCE(allocated_amount,0) - ?, 0) WHERE id=?",
                     (pa['amount'], pa['payment_id']))
    conn.execute("DELETE FROM payment_allocations WHERE maintenance_id=?", (m_id,))
    conn.execute("DELETE FROM maintenance WHERE id=?", (m_id,))

def _enforce_maintenance_paid_floor(conn, m_id):
    """After any edit of a maintenance row: paid_amount may be raised freely, but never left below
    what ledger payments have already been applied to it (otherwise the same money is counted
    twice — once as the payment, once as the bill being 'paid')."""
    conn.execute("""UPDATE maintenance SET paid_amount = MAX(COALESCE(paid_amount,0),
                       COALESCE((SELECT SUM(amount) FROM payment_allocations WHERE maintenance_id=maintenance.id),0))
                    WHERE id=?""", (m_id,))

def _payment_is_duplicate(conn, column, entity_id, ptype, f, amount):
    """The same payment (same party/vendor, date, amount, mode and reference) submitted again within
    seconds is a double-click / double-tap, not a second payment."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')
    row = conn.execute(f"""SELECT id FROM payments WHERE {column}=? AND payment_type=? AND date=? AND amount=?
                           AND COALESCE(mode,'')=? AND COALESCE(reference_id,'')=? AND created_at>=? LIMIT 1""",
                       (entity_id, ptype, f.get('date'), amount, f.get('mode') or '', f.get('reference_id') or '', cutoff)).fetchone()
    return row is not None

def _validated_payment_basics(f):
    """Date / amount / mode checks shared by both payment directions. Returns (amount, error)."""
    d = (f.get('date') or '').strip()
    if not d:
        return None, 'Payment Date is required.'
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        return None, 'Payment Date is not a valid date.'
    amount, err = _parse_money(f.get('amount'), 'Payment Amount', required=True, allow_zero=False)
    if err:
        return None, err
    if not (f.get('mode') or '').strip():
        return None, 'Payment Mode is required.'
    return amount, None

def _ticked_ids(f):
    out, seen = [], set()
    for raw in f.getlist('trip_ids'):
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out

def _insert_party_payment_from_form(conn, party_id, f):
    """Records one payment received from a party and applies it to the party's trips. Trips the
    user ticked are paid first (in ticked order, capped at each trip's pending); whatever is left
    — or the whole amount if nothing was ticked — then flows to the party's other pending trips,
    oldest first, so the Trips screen and the Ledger can't drift apart. Only a genuine surplus
    (more than every trip owes — e.g. an advance or an opening-balance settlement) stays
    unallocated. Returns (ok, error_message_or_None) — does not commit or close conn."""
    if not conn.execute("SELECT 1 FROM parties WHERE id=?", (party_id,)).fetchone():
        return False, 'Party not found.'
    total_amount, err = _validated_payment_basics(f)
    if err:
        return False, err
    if _payment_is_duplicate(conn, 'party_id', party_id, 'received', f, total_amount):
        return False, 'This exact payment was just recorded — not adding it twice.'

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""INSERT INTO payments (date, payment_type, amount, party_id, mode, reference_id, remarks, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?)""",
                        (f.get('date'), 'received', total_amount, party_id, f.get('mode'),
                         f.get('reference_id') or None, f.get('remarks') or None, session.get('user_id'), now))
    payment_id = cur.lastrowid
    # Pending is computed live (billed_amount - payment_received - party_advance) since it isn't a
    # stored column. Ticked trips must belong to THIS party; anything else is ignored.
    pending = {t['id']: t['pending'] for t in _party_pending_trips(conn, party_id)}
    order = [tid for tid in _ticked_ids(f) if tid in pending]
    order += [tid for tid in pending if tid not in order]
    remaining = total_amount
    allocated_total = 0
    for tid in order:
        if remaining <= 0.004:
            break
        alloc = round(min(pending[tid], remaining), 2)
        conn.execute("UPDATE trips SET payment_received = COALESCE(payment_received,0) + ?, updated_by=?, updated_at=? WHERE id=?",
                     (alloc, session.get('user_id'), now, tid))
        conn.execute("INSERT INTO payment_allocations (payment_id, trip_id, amount, created_by, created_at) VALUES (?,?,?,?,?)",
                     (payment_id, tid, alloc, session.get('user_id'), now))
        remaining -= alloc
        allocated_total += alloc
    conn.execute("UPDATE payments SET allocated_amount=?, updated_by=?, updated_at=? WHERE id=?",
                 (allocated_total, session.get('user_id'), now, payment_id))
    return True, None

def _redirect_payment_error(endpoint, error, **kw):
    return redirect(url_for(endpoint, pay_error=error, **kw))

@app.route('/payment/party/<int:party_id>', methods=['GET', 'POST'])
def add_party_payment(party_id):
    conn = get_db()
    party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    if not party:
        conn.close()
        return redirect(url_for('accounts'))
    if request.method == 'POST':
        ok, error = _insert_party_payment_from_form(conn, party_id, request.form)
        if ok:
            conn.commit()
        conn.close()
        if not ok:
            return _redirect_payment_error('party_ledger', error, party_id=party_id)
        return redirect(url_for('party_ledger', party_id=party_id))
    conn.close()
    return render_template('add_payment.html', name=party['name'], role='Party', action_url=f'/payment/party/{party_id}', active='accounts')

def _insert_vendor_payment_from_form(conn, vendor_id, f):
    """Records one payment made to a vendor and applies it: ticked owner-hire trips first (capped
    at what is still owed on each, net of fuel/advance the company paid), then the vendor's other
    pending owner-hire trips, then the vendor's unpaid maintenance / tyre / battery / service /
    insurance bills, then the vendor's Pending expense bills (oldest first) — so Maintenance's
    "unpaid", the Expenses page's "Pending Bills" and the Ledger all agree. Only a
    genuine surplus stays unallocated. Returns (ok, error_message_or_None) — does not commit or
    close conn."""
    if not conn.execute("SELECT 1 FROM vendors WHERE id=?", (vendor_id,)).fetchone():
        return False, 'Vendor not found.'
    total_amount, err = _validated_payment_basics(f)
    if err:
        return False, err
    if _payment_is_duplicate(conn, 'vendor_id', vendor_id, 'paid', f, total_amount):
        return False, 'This exact payment was just recorded — not adding it twice.'

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""INSERT INTO payments (date, payment_type, amount, vendor_id, mode, reference_id, remarks, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?)""",
                        (f.get('date'), 'paid', total_amount, vendor_id, f.get('mode'),
                         f.get('reference_id') or None, f.get('remarks') or None, session.get('user_id'), now))
    payment_id = cur.lastrowid
    pending = {t['id']: t['pending'] for t in _vendor_pending_trips(conn, vendor_id)}
    order = [tid for tid in _ticked_ids(f) if tid in pending]
    order += [tid for tid in pending if tid not in order]
    remaining = total_amount
    allocated_total = 0
    for tid in order:
        if remaining <= 0.004:
            break
        alloc = round(min(pending[tid], remaining), 2)
        conn.execute("UPDATE trips SET paid_to_owner = COALESCE(paid_to_owner,0) + ?, updated_by=?, updated_at=? WHERE id=?",
                     (alloc, session.get('user_id'), now, tid))
        conn.execute("INSERT INTO payment_allocations (payment_id, trip_id, amount, created_by, created_at) VALUES (?,?,?,?,?)",
                     (payment_id, tid, alloc, session.get('user_id'), now))
        remaining -= alloc
        allocated_total += alloc
    for m in _vendor_pending_maintenance(conn, vendor_id):
        if remaining <= 0.004:
            break
        alloc = round(min(m['pending'], remaining), 2)
        conn.execute("UPDATE maintenance SET paid_amount = COALESCE(paid_amount,0) + ?, updated_by=?, updated_at=? WHERE id=?",
                     (alloc, session.get('user_id'), now, m['id']))
        conn.execute("INSERT INTO payment_allocations (payment_id, maintenance_id, amount, created_by, created_at) VALUES (?,?,?,?,?)",
                     (payment_id, m['id'], alloc, session.get('user_id'), now))
        remaining -= alloc
        allocated_total += alloc
    for o in _vendor_pending_overheads(conn, vendor_id):
        if remaining <= 0.004:
            break
        alloc = round(min(o['pending'], remaining), 2)
        conn.execute("INSERT INTO payment_allocations (payment_id, overhead_id, amount, created_by, created_at) VALUES (?,?,?,?,?)",
                     (payment_id, o['id'], alloc, session.get('user_id'), now))
        _settle_overhead_status(conn, o['id'])
        remaining -= alloc
        allocated_total += alloc
    conn.execute("UPDATE payments SET allocated_amount=?, updated_by=?, updated_at=? WHERE id=?",
                 (allocated_total, session.get('user_id'), now, payment_id))
    return True, None

@app.route('/payment/vendor/<int:vendor_id>', methods=['GET', 'POST'])
def add_vendor_payment(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if not vendor:
        conn.close()
        return redirect(url_for('accounts'))
    if request.method == 'POST':
        ok, error = _insert_vendor_payment_from_form(conn, vendor_id, request.form)
        if ok:
            conn.commit()
        conn.close()
        if not ok:
            return _redirect_payment_error('vendor_ledger', error, vendor_id=vendor_id)
        return redirect(url_for('vendor_ledger', vendor_id=vendor_id))
    conn.close()
    return render_template('add_payment.html', name=vendor['name'], role='Vendor', action_url=f'/payment/vendor/{vendor_id}', active='accounts')

@app.route('/payments/<int:payment_id>/view')
def payment_view(payment_id):
    """Read-only payment detail — opened from a Ledger row's Payment In/Out entry in the slide-in
    side panel. Payments have no dedicated edit page (they're recorded once via Add Payment and
    never revised), so this is purely a look-don't-touch summary plus the trips it was allocated
    against, with a link back to that party/vendor's full ledger."""
    conn = get_db()
    p = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        conn.close()
        return redirect(url_for('accounts'))
    entity_name = None
    entity_type = None
    entity_id = None
    if p['party_id']:
        row = conn.execute("SELECT name FROM parties WHERE id=?", (p['party_id'],)).fetchone()
        entity_name = row['name'] if row else None
        entity_type, entity_id = 'party', p['party_id']
    elif p['vendor_id']:
        row = conn.execute("SELECT name FROM vendors WHERE id=?", (p['vendor_id'],)).fetchone()
        entity_name = row['name'] if row else None
        entity_type, entity_id = 'vendor', p['vendor_id']
    allocations = conn.execute("""SELECT pa.amount, t.lr_number, t.id as trip_id, t.from_loc, t.to_loc
                                  FROM payment_allocations pa JOIN trips t ON pa.trip_id=t.id
                                  WHERE pa.payment_id=? ORDER BY pa.id""", (payment_id,)).fetchall()
    allocations = [dict(a) for a in allocations]
    for a in conn.execute("""SELECT pa.amount, m.category, m.service_type, m.tyre_action
                             FROM payment_allocations pa JOIN maintenance m ON pa.maintenance_id=m.id
                             WHERE pa.payment_id=? ORDER BY pa.id""", (payment_id,)).fetchall():
        allocations.append({'amount': a['amount'], 'lr_number': 'Maintenance: ' + _maintenance_label(a),
                            'trip_id': None, 'from_loc': '', 'to_loc': ''})
    for a in conn.execute("""SELECT pa.amount, o.category FROM payment_allocations pa JOIN overheads o ON pa.overhead_id=o.id
                             WHERE pa.payment_id=? ORDER BY pa.id""", (payment_id,)).fetchall():
        allocations.append({'amount': a['amount'], 'lr_number': 'Expense: ' + (a['category'] or 'bill'),
                            'trip_id': None, 'from_loc': '', 'to_loc': ''})
    conn.close()
    leftover = (p['amount'] or 0) - (p['allocated_amount'] or 0)
    return render_template('payment_view.html', p=p, entity_name=entity_name, entity_type=entity_type,
                            entity_id=entity_id, allocations=allocations, leftover=max(leftover, 0), active='accounts')


def _insert_overhead_from_form(conn, f):
    """Builds and inserts one Expense row from a submitted form — same fields, same required set
    (Date/Category/Amount, matching the website's own #addModal — see templates/overheads_list.html)
    used by both add_overhead() (website) and app_add_overhead() (mobile). Returns
    (ok, error_message_or_new_overhead_id) — does not commit or close conn."""
    if not f.get('date'):
        return False, 'Date is required.'
    if not (f.get('category') or '').strip():
        return False, 'Category is required.'
    amount, amt_err = _parse_money(f.get('amount'), 'Amount', required=True, allow_zero=False)
    if amt_err:
        return False, amt_err

    is_recurring = 1 if f.get('is_recurring') == 'on' else 0
    status = f.get('status') or 'Paid'
    if status == 'Pending' and (f.get('vendor') or '').strip():
        get_or_create_vendor(conn, f.get('vendor'))      # a bill we owe a vendor must have a ledger to land in
    cur = conn.execute("""INSERT INTO overheads (date, category, amount, notes, payment_mode, receipt_number,
                    vendor, description, status, is_recurring, recurring_frequency, due_date, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (f.get('date'), f.get('category'), amount, f.get('notes'),
                  f.get('payment_mode'), f.get('receipt_number'), f.get('vendor'), f.get('description'),
                  status, is_recurring, f.get('recurring_frequency') if is_recurring else None,
                  f.get('due_date') if status == 'Pending' else None, session.get('user_id'),
                  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return True, cur.lastrowid

@app.route('/overheads/add', methods=['GET', 'POST'])
def add_overhead():
    conn = get_db()
    if request.method == 'POST':
        ok, error = _insert_overhead_from_form(conn, request.form)
        if ok:
            conn.commit()
        conn.close()
        return redirect(url_for('overheads_list', **({} if ok else {'exp_error': error})))
    conn.close()
    return render_template('add_overhead.html', active='overheads')

def _update_overhead_from_form(conn, o_id, f):
    """Shared by edit_overhead() (website) and app_edit_overhead() (mobile) — the exact same field
    set _insert_overhead_from_form() writes on create (date/category/amount/notes/payment_mode/
    receipt_number/vendor/description/status/is_recurring/recurring_frequency/due_date), so editing
    an expense can never silently drop a field the create form itself collects. Returns
    (ok, error_message_or_None); does not commit or close conn."""
    amount, amt_err = _parse_money(f.get('amount'), 'Amount', required=True, allow_zero=False)
    if amt_err:
        return False, amt_err
    existing = conn.execute("SELECT vendor FROM overheads WHERE id=?", (o_id,)).fetchone()
    if not existing:
        return False, 'Expense not found.'
    paid_via_ledger = _overhead_alloc_sum(conn, o_id)
    if paid_via_ledger > 0.004:
        # ledger payments were applied to this bill under this vendor's name — changing the vendor
        # or lowering the amount below what was paid would orphan that money
        if (f.get('vendor') or '').strip().lower() != (existing['vendor'] or '').strip().lower():
            return False, f"₹{paid_via_ledger:,.2f} of this bill was already paid from the vendor's ledger, so its vendor can't be changed."
        if amount < paid_via_ledger - 0.004:
            return False, f"₹{paid_via_ledger:,.2f} of this bill was already paid from the vendor's ledger, so the amount can't be lower than that."
    is_recurring = 1 if f.get('is_recurring') == 'on' else 0
    status = f.get('status') or 'Paid'
    if status == 'Pending' and (f.get('vendor') or '').strip():
        get_or_create_vendor(conn, f.get('vendor'))
    conn.execute("""UPDATE overheads SET date=?, category=?, amount=?, notes=?, payment_mode=?, receipt_number=?,
                    vendor=?, description=?, status=?, is_recurring=?, recurring_frequency=?, due_date=?,
                    updated_by=?, updated_at=? WHERE id=?""",
                 (f.get('date'), f.get('category'), amount, f.get('notes'),
                  f.get('payment_mode'), f.get('receipt_number'), f.get('vendor'), f.get('description'),
                  status, is_recurring, f.get('recurring_frequency') if is_recurring else None,
                  f.get('due_date') if status == 'Pending' else None, session.get('user_id'),
                  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), o_id))
    _settle_overhead_status(conn, o_id)
    return True, None

@app.route('/overheads/edit/<int:o_id>', methods=['POST'])
def edit_overhead(o_id):
    conn = get_db()
    ok, error = _update_overhead_from_form(conn, o_id, request.form)
    if ok:
        conn.commit()
    conn.close()
    return redirect(url_for('overheads_list', **({} if ok else {'exp_error': error})))

@app.route('/trips/delete/<int:trip_id>', methods=['POST'])
def delete_trip(trip_id):
    conn = get_db()
    # A trip with a real Payment already allocated to it (from either side — a party's payment
    # against billed_amount, or a vendor/owner's payment against paid_to_owner) must not be
    # deleted: payment_allocations.trip_id has no cleanup path, so the row would become orphaned
    # and the ledger (which joins allocations back to trips) would silently lose track of that
    # money ever being received/paid — the party or vendor's balance would look wrong with no
    # error and no trace of why. Blocking here forces the payment to be reversed/reallocated
    # first, a deliberate choice instead of silent data loss.
    alloc = conn.execute("""SELECT COUNT(*) as c, COALESCE(SUM(pa.amount),0) as amt
                            FROM payment_allocations pa WHERE pa.trip_id=?""", (trip_id,)).fetchone()
    if alloc['c']:
        trip = conn.execute("SELECT lr_number FROM trips WHERE id=?", (trip_id,)).fetchone()
        conn.close()
        return redirect(url_for('trips_list', error='has_payment', amount=round(alloc['amt'], 2),
                                 lr=(trip['lr_number'] if trip else '') or f'#{trip_id}'))
    # Same reasoning as the payment_allocations guard above, for a different foreign key on this
    # same trips table: a real Toll Management entry linked to this trip (toll_entries.trip_id)
    # has no cleanup path either — deleting the trip would leave that toll entry pointing at a
    # trip that no longer exists. The toll's own cost isn't lost (its mirrored maintenance row is
    # untouched), but the "which trip incurred this toll" link would be destroyed silently, and
    # any future per-trip report (Route Analytics, Business Performance, an invoice) that looks
    # this toll up by trip_id would simply never find it again. Blocking here forces the toll
    # entry to be unlinked or deleted first, instead of silently orphaning it.
    toll_ct = conn.execute("SELECT COUNT(*) as c FROM toll_entries WHERE trip_id=?", (trip_id,)).fetchone()
    if toll_ct['c']:
        trip = conn.execute("SELECT lr_number FROM trips WHERE id=?", (trip_id,)).fetchone()
        conn.close()
        return redirect(url_for('trips_list', error='has_toll', toll_count=toll_ct['c'],
                                 lr=(trip['lr_number'] if trip else '') or f'#{trip_id}'))
    conn.execute("DELETE FROM trips WHERE id=?", (trip_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('trips_list'))

def _end_trip_from_form(conn, trip_id, f):
    """Shared by end_trip() (website, per-row modal on trips_list.html) and app_end_trip()
    (mobile modal) — the exact same "mark this trip complete" write, unchanged. Returns
    (ok, error_message_or_None). No-ops (ok=False) on a missing trip or one already ended —
    once ended, no further edits are allowed, same rule the website has always enforced."""
    trip = conn.execute("SELECT id, end_date FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not trip:
        return False, 'Trip not found.'
    if trip['end_date']:
        return False, 'This trip has already been ended — no further edits allowed.'
    if not f.get('end_date'):
        return False, 'End Date is required.'

    values = {}
    for key, label in (('freight_amount', 'Freight Amount'), ('shortage_qty', 'Shortage Qty'),
                       ('shortage_amount', 'Shortage Amount'), ('actual_km', 'Actual KM')):
        values[key], err = _parse_money(f.get(key), label)
        if err:
            return False, err

    def n(key):
        return values[key] if key in values else float(f.get(key) or 0)

    end_date = f.get('end_date')
    freight_amount = n('freight_amount')
    shortage_qty = n('shortage_qty')
    shortage_amount = n('shortage_amount')
    # The Freight Amount box is pre-filled with the trip's GROSS freight (the bill before any
    # shortage — see trips_list.html / app/trip_detail.html), so the shortage is taken off exactly
    # once, here.
    new_billed_amount = freight_amount - shortage_amount
    if new_billed_amount < -0.005:
        return False, 'Shortage is more than the freight — the trip bill would be negative.'

    conn.execute("""UPDATE trips SET end_date=?, end_time=?, actual_km=?, lr_received=?,
                    shortage_qty=?, shortage_unit=?, shortage_amount=?, shortage_date=?, shortage_remarks=?,
                    remarks=?, billed_amount=?, updated_by=?, updated_at=?
                    WHERE id=?""",
                 (end_date, f.get('end_time') or None, n('actual_km') or None, f.get('lr_received') or None,
                  shortage_qty or None, f.get('shortage_unit') or None, shortage_amount,
                  end_date if shortage_amount else None, f.get('shortage_remarks') or None,
                  f.get('remarks') or None, new_billed_amount, session.get('user_id'),
                  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), trip_id))
    conn.commit()
    return True, None

@app.route('/trips/<int:trip_id>/end', methods=['POST'])
def end_trip(trip_id):
    conn = get_db()
    ok, error = _end_trip_from_form(conn, trip_id, request.form)
    conn.close()
    args = request.args.to_dict()
    if not ok:
        args['end_error'] = error
    return redirect(url_for('trips_list', **args))

@app.route('/maintenance/delete/<int:m_id>', methods=['POST'])
def delete_maintenance(m_id):
    conn = get_db()
    _delete_maintenance_row(conn, m_id)
    conn.execute("DELETE FROM maintenance_items WHERE maintenance_id=?", (m_id,))
    conn.commit()
    conn.close()
    tab = request.args.get('tab') or request.form.get('tab') or 'service'
    if tab in ('insurance', 'permit'):
        return redirect(url_for('vehicles_list', tab=tab))
    return redirect(url_for('maintenance_list', tab=tab))

@app.route('/maintenance/edit/<int:m_id>', methods=['GET', 'POST'])
def edit_maintenance(m_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        vehicle_id = _get_or_create_vehicle_no_type(conn, f.get('vehicle_no'))
        vendor_id = get_or_create_vendor(conn, f.get('vendor_name'))
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""UPDATE maintenance SET date=?, vehicle_id=?, category=?, amount=?, paid_amount=?, vendor_id=?, notes=?,
                        updated_by=?, updated_at=?
                        WHERE id=?""",
                     (f.get('date'), vehicle_id, f.get('category'), float(f.get('amount') or 0),
                      float(f.get('paid_amount') or 0), vendor_id, f.get('notes'),
                      session.get('user_id'), now, m_id))
        _enforce_maintenance_paid_floor(conn, m_id)
        conn.commit()
        conn.close()
        return_to = f.get('return_to')
        return redirect(return_to) if return_to else redirect(url_for('maintenance_list'))

    m = conn.execute("""SELECT mt.*, v.vehicle_no, ve.name as vendor_name FROM maintenance mt
                        LEFT JOIN vehicles v ON mt.vehicle_id=v.id
                        LEFT JOIN vendors ve ON mt.vendor_id=ve.id WHERE mt.id=?""", (m_id,)).fetchone()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn.close()
    return render_template('edit_maintenance.html', m=m, vehicles=vehicles, combined_names=combined_names,
                            return_to=request.args.get('return_to', ''), active='maintenance')

@app.route('/maintenance/<int:m_id>/view')
def maintenance_view(m_id):
    """Read-only maintenance detail — opened from a Ledger row in a centered popup (see
    openRecordModal in base.html) instead of routing to the editable maintenance form."""
    conn = get_db()
    m = conn.execute("""SELECT mt.*, v.id as veh_id, v.vehicle_no, v.status as veh_status,
                        v.body_type as veh_body_type, ve.name as vendor_name FROM maintenance mt
                        LEFT JOIN vehicles v ON mt.vehicle_id=v.id
                        LEFT JOIN vendors ve ON mt.vendor_id=ve.id WHERE mt.id=?""", (m_id,)).fetchone()
    conn.close()
    if not m:
        return redirect(url_for('maintenance_list'))
    return render_template('maintenance_view.html', m=m, active='maintenance')

@app.route('/salaries/delete/<int:s_id>', methods=['POST'])
def delete_salary(s_id):
    conn = get_db()
    conn.execute("DELETE FROM salary_items WHERE salary_id=?", (s_id,))
    conn.execute("DELETE FROM salaries WHERE id=?", (s_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries_list'))

@app.route('/employees/<int:employee_id>/edit', methods=['POST'])
def edit_employee(employee_id):
    conn = get_db()
    f = request.form
    mobile, mob_err = _employee_mobile(conn, f.get('mobile'), exclude_id=employee_id)
    if mob_err:
        conn.close()
        if _wants_json():
            return jsonify({'ok': False, 'error': mob_err}), 400
        return redirect(url_for('salaries_list', tab=request.form.get('return_tab') or 'overview', emp_error=mob_err))
    conn.execute("""UPDATE employees SET name=?, type=?, role=?, mobile=?, email=?, address=?, joining_date=?,
                    date_of_birth=?, bank_account=?, ifsc_code=?, upi_id=?, emergency_contact=?, aadhaar=?, pan=?,
                    driving_license=?, basic_salary=?, updated_by=?, updated_at=? WHERE id=?""",
                 (f.get('name'), f.get('type'), f.get('role'), mobile, f.get('email') or None,
                  f.get('address') or None, f.get('joining_date') or None, f.get('date_of_birth') or None,
                  f.get('bank_account') or None, f.get('ifsc_code') or None, f.get('upi_id') or None,
                  f.get('emergency_contact') or None, f.get('aadhaar') or None, f.get('pan') or None,
                  f.get('driving_license') or None, float(f.get('basic_salary') or 0),
                  session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), employee_id))
    conn.commit()
    conn.close()
    if _wants_json():
        return jsonify({'ok': True})
    return redirect(url_for('salaries_list', tab=request.form.get('return_tab') or 'overview'))

@app.route('/employees/<int:employee_id>/deactivate', methods=['POST'])
def deactivate_employee(employee_id):
    conn = get_db()
    row = conn.execute("SELECT status FROM employees WHERE id=?", (employee_id,)).fetchone()
    new_status = 'Inactive' if (row and row['status'] != 'Inactive') else 'Active'
    conn.execute("UPDATE employees SET status=?, updated_by=?, updated_at=? WHERE id=?",
                 (new_status, session.get('user_id'),
                  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), employee_id))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries_list'))


@app.route('/employees/<int:employee_id>/advance/add', methods=['POST'])
def add_employee_advance(employee_id):
    """Give Advance / Record Repayment, callable from any Employees-module drawer — same
    'advances' table the original standalone Advances page already used, so both stay in sync."""
    conn = get_db()
    f = request.form
    emp = conn.execute("SELECT name FROM employees WHERE id=?", (employee_id,)).fetchone()
    adv_amount, adv_err = _parse_money(f.get('amount'), 'Amount', required=True, allow_zero=False)
    adv_date = (f.get('date') or '').strip() or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(adv_date)
    except ValueError:
        adv_err = adv_err or 'Date is not a valid date.'
    if emp and not adv_err:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO advances (employee, date, amount, type, notes, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                     (emp['name'], adv_date, adv_amount,
                      f.get('type') if f.get('type') in ('given', 'repaid') else 'given', f.get('notes') or None,
                      now, session.get('user_id')))
        conn.commit()
    conn.close()
    return redirect(url_for('salaries_list', tab=f.get('return_tab') or 'overview'))


@app.route('/attendance/mark', methods=['POST'])
def mark_attendance():
    """Single-employee, single-date attendance entry — UNIQUE(employee_id, date) means re-marking
    the same day just overwrites it instead of creating a second row."""
    conn = get_db()
    f = request.form
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""INSERT INTO attendance (employee_id, date, status, in_time, out_time, remarks, marked_by, created_at,
                    created_by)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(employee_id, date) DO UPDATE SET status=excluded.status, in_time=excluded.in_time,
                    out_time=excluded.out_time, remarks=excluded.remarks, marked_by=excluded.marked_by,
                    updated_by=excluded.created_by, updated_at=excluded.created_at""",
                 (f.get('employee_id'), f.get('date'), f.get('status'), f.get('in_time') or None, f.get('out_time') or None,
                  f.get('remarks') or None, 'Admin', now, session.get('user_id')))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries_list', tab='attendance', month=(f.get('date') or '')[:7]))

@app.route('/attendance/bulk-mark', methods=['POST'])
def bulk_mark_attendance():
    """Bulk toolbar action — marks the same status for every selected employee on one date, e.g.
    marking a whole crew Present for today in one click."""
    conn = get_db()
    f = request.form
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    date = f.get('date') or datetime.date.today().isoformat()
    status = f.get('status')
    emp_ids = f.getlist('employee_ids')
    for eid in emp_ids:
        conn.execute("""INSERT INTO attendance (employee_id, date, status, marked_by, created_at, created_by) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(employee_id, date) DO UPDATE SET status=excluded.status, marked_by=excluded.marked_by,
                        updated_by=excluded.created_by, updated_at=excluded.created_at""",
                     (eid, date, status, 'Admin', now, session.get('user_id')))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries_list', tab='attendance', month=date[:7]))

@app.route('/salaries/<int:salary_id>/payslip')
def download_payslip(salary_id):
    from flask import send_file
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from xml.sax.saxutils import escape as esc
    import io

    conn = get_db()
    sal = conn.execute("""SELECT s.*, e.name, e.employee_code, e.role, e.type, e.bank_account, e.ifsc_code
                          FROM salaries s JOIN employees e ON s.employee_id=e.id WHERE s.id=?""", (salary_id,)).fetchone()
    if not sal:
        conn.close()
        return redirect(url_for('salaries_list', tab='salary'))
    _sids = _salary_scope_ids(conn)
    if _sids is not None and sal['employee_id'] not in _sids:
        conn.close()
        return _salary_denied()
    items = conn.execute("SELECT item_type, description, amount FROM salary_items WHERE salary_id=?", (salary_id,)).fetchall()
    company_name = get_company_name(session.get('company_id', 1))
    conn.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=17, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    title_style = ParagraphStyle('T', parent=styles['Heading2'], fontSize=13, textColor=colors.HexColor('#1B2A4A'))
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=9.5, textColor=colors.HexColor('#5A6B8C'))

    story = [Paragraph(esc(company_name), company_style), Spacer(1, 6),
             Paragraph(f"Payslip — {sal['month_key']}", title_style), Spacer(1, 10),
             Paragraph(f"<b>{esc(sal['name'])}</b> ({esc(sal['employee_code'] or '')}) — {esc(sal['role'] or sal['type'] or '')}", label_style),
             Spacer(1, 14)]

    rows = [['Basic Salary', f"Rs. {sal['basic_salary'] or 0:,.0f}"]]
    for it in items:
        prefix = '+ ' if it['item_type'] == 'allowance' else '- '
        rows.append([esc(it['description']), f"{prefix}Rs. {it['amount']:,.0f}"])
    if sal['advance_recovery']:
        rows.append(['Advance Recovery', f"- Rs. {sal['advance_recovery']:,.0f}"])
    rows.append(['NET SALARY', f"Rs. {sal['net_salary'] or 0:,.0f}"])
    t = Table(rows, colWidths=[4.5*inch, 2.4*inch])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9.5), ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#EAF6EE')), ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,-1), (-1,-1), 11), ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))
    story.append(Paragraph(f"Payment Status: <b>{esc((sal['payment_status'] or 'pending').title())}</b>" +
                            (f" &nbsp;|&nbsp; Paid on {esc(sal['payment_date'])}" if sal['payment_date'] else ''), label_style))
    if sal['bank_account']:
        story.append(Paragraph(f"Bank A/C: {esc(sal['bank_account'])} &nbsp;|&nbsp; IFSC: {esc(sal['ifsc_code'] or '')}", label_style))
    doc.build(story)
    buf.seek(0)
    safe_name = "".join(c for c in sal['name'] if c.isalnum() or c in " _-")[:30]
    return send_file(buf, as_attachment=True, download_name=f'payslip_{safe_name}_{sal["month_key"]}.pdf', mimetype='application/pdf')

@app.route('/overheads/delete/<int:o_id>', methods=['POST'])
def delete_overhead(o_id):
    conn = get_db()
    _delete_overhead_row(conn, o_id)
    conn.commit()
    conn.close()
    return redirect(url_for('overheads_list'))

def _mirror_vehicle_compliance_from_form(conn, vehicle_id, vtype, f, now):
    """Keeps vehicle_compliance (the real source refresh_compliance()/the detail page read — see
    that function's own comment) from silently drifting behind whatever Fitness/PUC/Permit dates
    were just typed into the Add/Edit Vehicle form. Same source='manual'/sync_status='Not Synced'
    convention vehicle_compliance_renew() already uses for its own manual edits — this is the
    other direction of that same mirroring, closing the one real gap that let vehicle OD14J0117's
    permit date drift to two different values in two different places (Edit Vehicle overwrote
    vehicles.permit_valid_upto directly, with nothing telling vehicle_compliance a newer date
    existed). Own-fleet only — vehicle_compliance doesn't track Hired vehicles at all (see
    compliance_service.py's own module docstring), so a Hired vehicle's dates stay exactly where
    they've always lived, on the vehicles row itself.

    Deliberately skips any field left BLANK on this form — unlike the Compliance tab's own
    single-purpose renewal form (submitting that IS the "renew this" action), Add/Edit Vehicle is
    a general form where Fitness/PUC/Permit are just 3 fields among many; leaving one blank here
    usually means "wasn't editing that today," not "clear it," so an existing, possibly richer
    (document number, sync history) vehicle_compliance record is never blanked out by omission."""
    if vtype != VEHICLE_TYPE_OWN:
        return
    for ctype, field_name in (('fitness', 'fitness_expiry'), ('puc', 'puc_valid_upto'), ('permit', 'permit_valid_upto')):
        valid_upto = f.get(field_name)
        if not valid_upto:
            continue
        existing_row = conn.execute("SELECT id FROM vehicle_compliance WHERE vehicle_id=? AND compliance_type=?",
                                     (vehicle_id, ctype)).fetchone()
        if existing_row:
            conn.execute("""UPDATE vehicle_compliance SET valid_upto=?, source='manual', sync_status='Not Synced',
                            updated_at=?, updated_by=? WHERE id=?""",
                         (valid_upto, now, session.get('user_id'), existing_row['id']))
        else:
            conn.execute("""INSERT INTO vehicle_compliance
                            (vehicle_id, compliance_type, valid_upto, source, sync_status, created_at, updated_at, created_by)
                            VALUES (?,?,?,'manual','Not Synced',?,?,?)""",
                         (vehicle_id, ctype, valid_upto, now, now, session.get('user_id')))

def _insert_vehicle_from_form(conn, f):
    """Shared by add_vehicle() (website) and app_add_vehicle() (mobile modal) — the same
    insert-or-update-by-vehicle_no logic, unchanged. Every column add_vehicle() ever wrote here
    too, or re-saving an existing vehicle_no through one surface would silently null out columns
    only the other surface knows about (see add_vehicle.html's own comment about this exact bug).
    Returns (ok, error_message_or_None)."""
    vno = (f.get('vehicle_no') or '').strip()
    if not vno:
        return False, 'Vehicle No is required.'
    vtype = f.get('type')
    existing = conn.execute("SELECT id FROM vehicles WHERE vehicle_no=?", (vno,)).fetchone()
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if existing:
        vehicle_id = existing[0]
        # insurance_expiry is deliberately NOT in this column list — insurance_policies (Vehicles
        # > Insurance tab) is its single source of truth (add_insurance()/edit_insurance() are the
        # only writers), so this UPDATE must never touch it, even to a blank value, or a re-save
        # through this shared insert-or-update path (e.g. re-adding an existing vehicle_no on
        # mobile) would silently wipe out a real policy's expiry that this form doesn't even ask
        # about anymore. Same reasoning fitness/puc/permit already get via _mirror_vehicle_
        # compliance_from_form() below — that one still writes forward, this one just never writes
        # to that column here at all.
        conn.execute("""UPDATE vehicles SET type=?, registration_date=?, capacity_mt=?,
                        fitness_expiry=?, puc_valid_upto=?, permit_valid_upto=?,
                        status=?, body_type=?, chassis_number=?, engine_number=?, notes=?, updated_by=?, updated_at=? WHERE id=?""",
                     (vtype, f.get('registration_date'), f.get('capacity_mt') or None,
                      f.get('fitness_expiry'), f.get('puc_valid_upto'),
                      f.get('permit_valid_upto'), f.get('status') or 'Active', f.get('body_type'),
                      f.get('chassis_number') or None, f.get('engine_number') or None,
                      f.get('notes'), session.get('user_id'), _now, vehicle_id))
    else:
        # No insurance_expiry here either, same reasoning as the UPDATE branch above — a brand
        # new vehicle has no policy yet; add one via the Insurance tab once it exists.
        cur = conn.execute("""INSERT INTO vehicles (vehicle_no, type, registration_date, capacity_mt,
                        fitness_expiry, puc_valid_upto, permit_valid_upto,
                        status, body_type, chassis_number, engine_number, notes, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (vno, vtype, f.get('registration_date'), f.get('capacity_mt') or None,
                      f.get('fitness_expiry'), f.get('puc_valid_upto'),
                      f.get('permit_valid_upto'), f.get('status') or 'Active', f.get('body_type'),
                      f.get('chassis_number') or None, f.get('engine_number') or None,
                      f.get('notes'), session.get('user_id'), _now))
        vehicle_id = cur.lastrowid
    _mirror_vehicle_compliance_from_form(conn, vehicle_id, vtype, f, _now)
    conn.commit()
    return True, None

@app.route('/vehicles/add', methods=['GET', 'POST'])
def add_vehicle():
    conn = get_db()
    if request.method == 'POST':
        _insert_vehicle_from_form(conn, request.form)
        conn.close()
        return redirect(url_for('vehicles_list'))
    conn.close()
    return render_template('add_vehicle.html', active='vehicles')

@app.route('/trips/export')
def export_trips():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status_f = request.args.get('status', '')
    lr_f = request.args.get('lr_received', '')
    lr_number_f = request.args.get('lr_number', '')
    query = """SELECT t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received, t.end_date
               FROM trips t LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if party_f: query += " AND p.name LIKE ?"; params.append(f"%{party_f}%")
    if date_from: query += " AND t.date >= ?"; params.append(date_from)
    if date_to: query += " AND t.date <= ?"; params.append(date_to)
    if status_f == 'active': query += " AND t.end_date IS NULL"
    elif status_f == 'completed': query += " AND t.end_date IS NOT NULL"
    if lr_f == 'received': query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received': query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    if lr_number_f: query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_number_f}%")
    query += " ORDER BY t.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Trips"
    headers = ["Date","LR Number","Vehicle","Party","From","To","Billed Amount","LR Received","Trip Status"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        vals = list(row)[:-2] + ['Yes' if row['lr_received'] == 'Yes' else 'No', 'Completed' if row['end_date'] else 'Active']
        if _hide_trip_amounts():
            vals[6] = '****'           # the Billed Amount column (a "hide amounts" user never gets the real figure in a file)
        for c_idx, val in enumerate(vals, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='trips_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/trips/export/pdf')
def export_trips_pdf():
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    party_f = request.args.get('party', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status_f = request.args.get('status', '')
    lr_f = request.args.get('lr_received', '')
    lr_number_f = request.args.get('lr_number', '')
    query = """SELECT t.date, t.lr_number, v.vehicle_no, p.name as party_name,
               t.from_loc, t.to_loc, t.billed_amount, t.lr_received, t.end_date
               FROM trips t LEFT JOIN vehicles v ON t.vehicle_id = v.id
               LEFT JOIN parties p ON t.party_id = p.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if party_f: query += " AND p.name LIKE ?"; params.append(f"%{party_f}%")
    if date_from: query += " AND t.date >= ?"; params.append(date_from)
    if date_to: query += " AND t.date <= ?"; params.append(date_to)
    if status_f == 'active': query += " AND t.end_date IS NULL"
    elif status_f == 'completed': query += " AND t.end_date IS NOT NULL"
    if lr_f == 'received': query += " AND t.lr_received = 'Yes'"
    elif lr_f == 'not_received': query += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    if lr_number_f: query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_number_f}%")
    query += " ORDER BY t.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], fontSize=15, textColor=colors.HexColor('#1B2A4A'))
    story = [Paragraph("Trips — Filtered View", title_style), Spacer(1, 10)]

    table_rows = [['Date', 'LR Number', 'Vehicle', 'Party', 'From', 'To', 'Billed (Rs.)', 'LR Recv.', 'Status']]
    total_billed = 0
    for r in rows:
        total_billed += r['billed_amount'] or 0
        table_rows.append([r['date'] or '', r['lr_number'] or '', r['vehicle_no'] or '', r['party_name'] or '',
                            r['from_loc'] or '', r['to_loc'] or '', '****' if _hide_trip_amounts() else f"{r['billed_amount'] or 0:,.0f}",
                            'Yes' if r['lr_received'] == 'Yes' else 'No',
                            'Completed' if r['end_date'] else 'Active'])
    t = Table(table_rows, colWidths=[0.75*inch, 0.9*inch, 0.8*inch, 1.4*inch, 1.5*inch, 1.5*inch, 0.9*inch, 0.7*inch, 0.8*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (6,0), (6,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Total Trips: {len(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; Total Billed: {'****' if _hide_trip_amounts() else f'Rs. {total_billed:,.0f}'}",
                            ParagraphStyle('F', parent=styles['Normal'], fontSize=10)))
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='trips_export.pdf', mimetype='application/pdf')

@app.route('/maintenance/export')
def export_maintenance():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    vendor_f = request.args.get('vendor', '')
    category_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = """SELECT m.date, v.vehicle_no, m.category, m.amount, ve.name as vendor_name
               FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id = v.id
               LEFT JOIN vendors ve ON m.vendor_id = ve.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if vendor_f: query += " AND ve.name LIKE ?"; params.append(f"%{vendor_f}%")
    if category_f: query += " AND m.category = ?"; params.append(category_f)
    if date_from: query += " AND m.date >= ?"; params.append(date_from)
    if date_to: query += " AND m.date <= ?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Maintenance"
    headers = ["Date","Vehicle","Category","Amount","Vendor"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='maintenance_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/maintenance/export/pdf')
def export_maintenance_pdf():
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    import io
    conn = get_db()
    vehicle_f = request.args.get('vehicle', '')
    vendor_f = request.args.get('vendor', '')
    category_f = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = """SELECT m.date, v.vehicle_no, m.category, m.amount, ve.name as vendor_name
               FROM maintenance m LEFT JOIN vehicles v ON m.vehicle_id = v.id
               LEFT JOIN vendors ve ON m.vendor_id = ve.id WHERE 1=1"""
    params = []
    if vehicle_f: query += " AND v.vehicle_no LIKE ?"; params.append(f"%{vehicle_f}%")
    if vendor_f: query += " AND ve.name LIKE ?"; params.append(f"%{vendor_f}%")
    if category_f: query += " AND m.category = ?"; params.append(category_f)
    if date_from: query += " AND m.date >= ?"; params.append(date_from)
    if date_to: query += " AND m.date <= ?"; params.append(date_to)
    query += " ORDER BY m.date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Heading1'], fontSize=15, textColor=colors.HexColor('#1B2A4A'))
    story = [Paragraph("Maintenance — Filtered View", title_style), Spacer(1, 10)]

    table_rows = [['Date', 'Vehicle', 'Category', 'Vendor', 'Amount (Rs.)']]
    total_amount = 0
    for r in rows:
        total_amount += r['amount'] or 0
        table_rows.append([r['date'] or '', r['vehicle_no'] or '', r['category'] or '', r['vendor_name'] or '—',
                            f"{r['amount'] or 0:,.0f}"])
    t = Table(table_rows, colWidths=[1.1*inch, 1.3*inch, 2*inch, 2*inch, 1.3*inch], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (4,0), (4,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDE3EC')),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Total Entries: {len(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; Total Amount: Rs. {total_amount:,.0f}",
                            ParagraphStyle('F', parent=styles['Normal'], fontSize=10)))
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='maintenance_export.pdf', mimetype='application/pdf')

@app.route('/advances')
def advances_list():
    conn = get_db()
    rows = conn.execute("""SELECT employee,
        SUM(CASE WHEN type='given' THEN amount ELSE 0 END) as total_given,
        SUM(CASE WHEN type='repaid' THEN amount ELSE 0 END) as total_repaid,
        SUM(CASE WHEN type='given' THEN amount ELSE -amount END) as outstanding
        FROM advances GROUP BY employee ORDER BY employee""").fetchall()
    entries = conn.execute("SELECT employee, date, amount, type, notes FROM advances ORDER BY date DESC").fetchall()
    if _salary_scope_ids(conn) is not None:
        ok_names = {(e['name'] or '').strip().lower() for e in _salary_visible_employees(conn)}
        rows = [r for r in rows if (r['employee'] or '').strip().lower() in ok_names]
        entries = [r for r in entries if (r['employee'] or '').strip().lower() in ok_names]
    conn.close()
    return render_template('advances_list.html', rows=rows, entries=entries, active='salaries')

@app.route('/advances/add', methods=['GET', 'POST'])
def add_advance():
    import datetime
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO advances (employee, date, amount, type, notes, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                     (f.get('employee'), f.get('date'), float(f.get('amount') or 0), f.get('type'), f.get('notes'),
                      now, session.get('user_id')))
        conn.commit()
        conn.close()
        return redirect(url_for('advances_list'))
    conn.close()
    return render_template('add_advance.html', active='salaries')

@app.route('/vehicles/edit/<int:vehicle_id>', methods=['GET', 'POST'])
def edit_vehicle(vehicle_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # insurance_expiry deliberately NOT in this column list — see the identical comment on
        # _insert_vehicle_from_form()'s own UPDATE branch; insurance_policies is its single source
        # of truth, and the field is now read-only on edit_vehicle.html so nothing real is ever
        # submitted here anyway, but this is the actual backend gate that matters.
        conn.execute("""UPDATE vehicles SET vehicle_no=?, type=?, registration_date=?, capacity_mt=?,
                        fitness_expiry=?, puc_valid_upto=?, permit_valid_upto=?,
                        status=?, body_type=?, chassis_number=?, engine_number=?, notes=?, updated_by=?, updated_at=? WHERE id=?""",
                     (f.get('vehicle_no'), f.get('type'), f.get('registration_date'), f.get('capacity_mt') or None,
                      f.get('fitness_expiry'), f.get('puc_valid_upto'),
                      f.get('permit_valid_upto'), f.get('status') or 'Active', f.get('body_type'),
                      f.get('chassis_number') or None, f.get('engine_number') or None,
                      f.get('notes'), session.get('user_id'),
                      _now, vehicle_id))
        # Same mirroring _insert_vehicle_from_form() does — this is the OTHER real edit path
        # (the website's own full-page Edit Vehicle, not the Add-Vehicle-or-update-by-number
        # modal both surfaces share) that was writing Fitness/PUC/Permit dates straight into
        # vehicles.* with nothing telling vehicle_compliance a newer date existed. See
        # _mirror_vehicle_compliance_from_form()'s own comment for the full story (this exact gap
        # is what let vehicle OD14J0117's permit date drift to two different values).
        _mirror_vehicle_compliance_from_form(conn, vehicle_id, f.get('type'), f, _now)
        conn.commit()
        conn.close()
        return redirect(url_for('vehicles_list'))
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    conn.close()
    return render_template('edit_vehicle.html', v=vehicle, active='vehicles')

@app.route('/vehicles/toggle_status/<int:vehicle_id>', methods=['POST'])
def toggle_vehicle_status(vehicle_id):
    """Quick Active/Inactive flip from the vehicle list row — a safer everyday action than
    deleting a vehicle outright, which would also wipe its trip/maintenance history."""
    conn = get_db()
    v = conn.execute("SELECT status FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if v:
        new_status = 'Inactive' if (v['status'] or 'Active') == 'Active' else 'Active'
        conn.execute("UPDATE vehicles SET status=?, updated_by=?, updated_at=? WHERE id=?",
                     (new_status, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), vehicle_id))
        conn.commit()
    conn.close()
    tab = request.args.get('tab') or 'all'
    return redirect(url_for('vehicles_list', tab=tab))

@app.route('/vehicles/delete/<int:vehicle_id>', methods=['POST'])
def delete_vehicle(vehicle_id):
    conn = get_db()
    conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('vehicles_list'))

@app.route('/vehicles/<int:vehicle_id>/compliance/<ctype>/renew', methods=['POST'])
def vehicle_compliance_renew(vehicle_id, ctype):
    """Manual renewal — an office user typing in a new certificate/permit number and expiry
    directly, independent of any provider sync. source='manual' distinguishes this from a
    provider-synced record without overwriting whatever the last sync found."""
    if ctype not in ('fitness', 'puc', 'permit'):
        return redirect(url_for('vehicles_list'))
    f = request.form
    conn = get_db()
    now = cs._now()
    existing = conn.execute("SELECT id FROM vehicle_compliance WHERE vehicle_id=? AND compliance_type=?",
                             (vehicle_id, ctype)).fetchone()
    # Only 'permit' has a subtype field on its edit form (National/State Permit etc.) — for
    # fitness/puc this is just absent from the submitted form, so COALESCE keeps whatever
    # a prior sync already set instead of blanking it out.
    permit_subtype = f.get('permit_subtype') if ctype == 'permit' else None
    if existing:
        if permit_subtype is not None:
            conn.execute("""UPDATE vehicle_compliance SET document_number=?, valid_upto=?, permit_subtype=?,
                            source='manual', sync_status='Not Synced', updated_at=?, updated_by=? WHERE id=?""",
                         (f.get('document_number') or None, f.get('valid_upto') or None, permit_subtype or None,
                          now, session.get('user_id'), existing['id']))
        else:
            conn.execute("""UPDATE vehicle_compliance SET document_number=?, valid_upto=?, source='manual',
                            sync_status='Not Synced', updated_at=?, updated_by=? WHERE id=?""",
                         (f.get('document_number') or None, f.get('valid_upto') or None, now, session.get('user_id'), existing['id']))
    else:
        conn.execute("""INSERT INTO vehicle_compliance
                        (vehicle_id, compliance_type, document_number, valid_upto, permit_subtype, source,
                         sync_status, created_at, updated_at, created_by)
                        VALUES (?,?,?,?,?,'manual','Not Synced',?,?,?)""",
                     (vehicle_id, ctype, f.get('document_number') or None, f.get('valid_upto') or None,
                      permit_subtype or None, now, now, session.get('user_id')))
    # Keep the vehicle's own quick-glance column in sync too, same pattern already used for
    # Insurance's expiry — so the plain date field in the Edit Vehicle form never disagrees
    # with what Compliance shows.
    col = {'fitness': 'fitness_expiry', 'puc': 'puc_valid_upto', 'permit': 'permit_valid_upto'}[ctype]
    if f.get('valid_upto'):
        conn.execute(f"UPDATE vehicles SET {col}=?, updated_by=?, updated_at=? WHERE id=?",
                     (f.get('valid_upto'), session.get('user_id'), now, vehicle_id))
    conn.commit()
    conn.close()
    # This route is posted to from two different places (the old Edit Vehicle modal on the
    # list, and the vehicle detail page's Edit Fitness & Permit modal) — return_to lets each
    # one send the user back to where they actually were, instead of always landing on the
    # list, which is what made a save on the detail page look like it hadn't done anything.
    return redirect(_safe_next(request.form.get('return_to'), url_for('vehicles_list', tab='all')))

@app.route('/vehicles/<int:vehicle_id>/compliance/sync', methods=['POST'])
def vehicle_compliance_sync(vehicle_id):
    """Manual per-vehicle 'Sync Now' — calls the same mock providers the nightly job uses,
    just for one vehicle, so a user doesn't have to wait for 2 AM to see it work."""
    conn = get_db()
    cs.sync_vehicle(conn, vehicle_id, created_by=session.get('user_id'))
    conn.commit()
    conn.close()
    return redirect(url_for('vehicles_list', tab='all'))

def _employee_ledger_entries(conn, employee_name, date_from='', date_to=''):
    """Opening Balance / Salary Paid / Advance Given / Advance Repaid, running-balance ledger for
    one employee (by name, matching how `salaries`/`advances` are still keyed) — the same proven
    formula the standalone Employee Ledger page has always used, now reusable from the Employees
    module's drawers too instead of only living on that separate page."""
    date_from, date_to = _normalized_date_range(date_from, date_to)
    # The WHOLE history is always read, so every row's running "Advance Balance" and the "Advance outstanding"
    # figure are true balances. Only the rows SHOWN (and the salary total) are limited to the date range.
    # (It used to read only the rows inside the range, so a one-month view restarted the balance from the
    # opening balance and disagreed with the Salary tab: e.g. 9,000 for March when 13,000 was really outstanding.)
    # COALESCE(date, payment_date, substr(created_at,1,10)) rather than a bare `date` column --
    # a salary row can have a NULL `date` (e.g. one left over from the old bulk-generate flow
    # that's since been removed) while still genuinely being paid on a real payment_date.
    salaries = conn.execute("SELECT COALESCE(date, payment_date, substr(created_at,1,10)) as date, amount, created_at "
                            "FROM salaries WHERE employee=? COLLATE NOCASE", (employee_name,)).fetchall()
    advances = conn.execute("SELECT date, amount, type, notes, created_at FROM advances WHERE employee=? COLLATE NOCASE", (employee_name,)).fetchall()
    emp_row = conn.execute("SELECT opening_balance, opening_balance_date FROM employees WHERE name=? COLLATE NOCASE",
                           (employee_name,)).fetchone()

    entries = []
    if emp_row and emp_row['opening_balance']:
        ob = emp_row['opening_balance']
        entries.append({'date': emp_row['opening_balance_date'] or '', 'entry_type': 'Opening Balance',
                         'debit': max(ob, 0), 'credit': max(-ob, 0), 'notes': 'Carried over balance',
                         'created_at': '', 'affects_advance': True})
    # Salary is a payment of wages — it NEVER changes the advance balance (affects_advance=False). Only the
    # opening balance, "Advance Given" and "Advance Repaid" move it.
    for s_ in salaries:
        if s_['amount']:
            entries.append({'date': s_['date'], 'entry_type': 'Salary Paid', 'debit': 0, 'credit': s_['amount'] or 0,
                             'notes': '', 'created_at': s_['created_at'], 'affects_advance': False})
    for a in advances:
        if a['type'] == 'given':
            entries.append({'date': a['date'], 'entry_type': 'Advance Given', 'debit': a['amount'] or 0, 'credit': 0,
                             'notes': a['notes'] or '', 'created_at': a['created_at'], 'affects_advance': True})
        else:
            entries.append({'date': a['date'], 'entry_type': 'Advance Repaid', 'debit': 0, 'credit': a['amount'] or 0,
                             'notes': a['notes'] or '', 'created_at': a['created_at'], 'affects_advance': True})
    entries.sort(key=lambda e: e['date'] or '')

    advance_balance = 0
    brought_forward = 0
    closing = 0
    for e in entries:
        if e['affects_advance']:
            advance_balance += e['debit'] - e['credit']
        e['running_advance_balance'] = advance_balance
        d = e['date'] or ''
        if e['affects_advance'] and date_from and d < date_from:
            brought_forward = advance_balance
        if e['affects_advance'] and (not date_to or d <= date_to):
            closing = advance_balance

    def in_range(e):
        d = e['date'] or ''
        return (not date_from or d >= date_from) and (not date_to or d <= date_to)
    shown = [e for e in entries if in_range(e)]
    shown.reverse()
    total_salary_paid = sum(e['credit'] for e in shown if e['entry_type'] == 'Salary Paid')
    return {'entries': shown, 'advance_balance': closing, 'brought_forward': brought_forward, 'total_salary_paid': total_salary_paid,
            'opening_balance': emp_row['opening_balance'] if emp_row else 0,
            'opening_balance_date': emp_row['opening_balance_date'] if emp_row else ''}

def _insert_employee_payment_from_form(conn, employee, f):
    """Builds and inserts one Salary Paid / Advance Given / Advance Repaid entry for one employee
    — same fields, same entry_kind branching used by both employee_ledger() (website) and
    app_add_employee_payment() (mobile). Returns (ok, error_message_or_None) — does not commit or
    close conn."""
    entry_kind = f.get('entry_kind')
    if entry_kind not in ('salary', 'given', 'repaid'):
        return False, 'Type is required.'
    if not f.get('date'):
        return False, 'Date is required.'
    tx_date = f.get('date').strip()
    try:
        datetime.date.fromisoformat(tx_date)
    except ValueError:
        return False, 'Date is not a valid date.'
    amount, err = _parse_money(f.get('amount'), 'Amount', required=True, allow_zero=False)
    if err:
        return False, err
    if not conn.execute("SELECT 1 FROM employees WHERE name=? COLLATE NOCASE", (employee,)).fetchone():
        return False, 'Employee not found.'
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if entry_kind == 'salary':
        month_label = datetime.datetime.strptime(tx_date, '%Y-%m-%d').strftime('%b %Y') if tx_date else ''
        month_key = tx_date[:7] if tx_date else ''
        emp_row = conn.execute("SELECT id, basic_salary FROM employees WHERE name=? COLLATE NOCASE", (employee,)).fetchone()
        emp_id = emp_row['id'] if emp_row else None
        basic = emp_row['basic_salary'] if emp_row and emp_row['basic_salary'] else amount
        conn.execute("""INSERT INTO salaries
                         (employee, month, amount, date, created_at,
                          employee_id, month_key, basic_salary, gross_salary, total_deductions,
                          advance_recovery, net_salary, payment_status, payment_date, payment_mode, remarks,
                          created_by)
                         VALUES (?,?,?,?,?, ?,?,?,?,0, 0,?,'paid',?,?,?, ?)""",
                     (employee, month_label, amount, tx_date, now,
                      emp_id, month_key, basic, amount, amount, tx_date,
                      f.get('payment_mode') or '', f.get('notes') or '', session.get('user_id')))
    else:
        conn.execute("INSERT INTO advances (employee, date, amount, type, notes, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                     (employee, tx_date, amount, entry_kind, f.get('notes'), now, session.get('user_id')))
    return True, None

@app.route('/employee/<employee>', methods=['GET', 'POST'])
def employee_ledger(employee):
    conn = get_db()
    if not _salary_name_allowed(conn, employee):
        conn.close()
        return _salary_denied()
    if request.method == 'POST':
        f = request.form
        from_tab = f.get('from_tab', 'overview')
        ok, error = _insert_employee_payment_from_form(conn, employee, f)
        if ok:
            conn.commit()
        conn.close()
        return redirect(url_for('employee_ledger', employee=employee, from_tab=from_tab, **({} if ok else {'pay_error': error})))

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    from_tab = request.args.get('from_tab', 'overview')
    led = _employee_ledger_entries(conn, employee, date_from, date_to)
    conn.close()
    return render_template('employee_ledger.html', employee=employee, entries=led['entries'],
                            advance_balance=led['advance_balance'], brought_forward=led['brought_forward'], total_salary_paid=led['total_salary_paid'],
                            opening_balance=led['opening_balance'],
                            opening_balance_date=led['opening_balance_date'],
                            f_date_from=date_from, f_date_to=date_to, from_tab=from_tab, active='salaries')

def _update_employee_opening_balance_from_form(conn, employee, f):
    """Sets/replaces the carried-over opening advance balance for one employee — same logic used
    by both update_employee_opening_balance() (website) and app_update_employee_opening_balance()
    (mobile). Returns (ok, error_message_or_None) — does not commit or close conn."""
    ob, err = _parse_money(f.get('opening_balance'), 'Opening Advance Balance', required=True, allow_negative=True)
    if err:
        return False, err
    ob_date = (f.get('opening_balance_date') or '').strip() or None
    if ob_date:
        try:
            datetime.date.fromisoformat(ob_date)
        except ValueError:
            return False, 'As Of Date is not a valid date.'
    if not conn.execute("SELECT 1 FROM employees WHERE name=? COLLATE NOCASE", (employee,)).fetchone():
        return False, 'Employee not found.'
    conn.execute("UPDATE employees SET opening_balance=?, opening_balance_date=?, updated_by=?, updated_at=? WHERE name=? COLLATE NOCASE",
                 (ob, ob_date, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), employee))
    return True, None

@app.route('/employee/<employee>/opening-balance', methods=['POST'])
def update_employee_opening_balance(employee):
    conn = get_db()
    f = request.form
    from_tab = f.get('from_tab', 'overview')
    ok, error = _update_employee_opening_balance_from_form(conn, employee, f)
    if ok:
        conn.commit()
    conn.close()
    return redirect(url_for('employee_ledger', employee=employee, from_tab=from_tab, **({} if ok else {'pay_error': error})))

@app.route('/driver-performance')
def driver_performance():
    return redirect(url_for('performance', **request.args.to_dict()))

def _trend_chart_coords(monthly, value_key):
    """Shared pixel-coordinate helper for the small SVG trend charts on the Performance page."""
    chart_w, chart_h, pad_l, pad_r, pad_t, pad_b = 700, 220, 34, 20, 20, 34
    plot_w, plot_h, n = chart_w - pad_l - pad_r, chart_h - pad_t - pad_b, len(monthly)
    chart_bottom = pad_t + plot_h
    vmax = max([m[value_key] for m in monthly], default=1) or 1
    for i, m in enumerate(monthly):
        m['x'] = round(pad_l + (i * plot_w / (n - 1) if n > 1 else 0), 1)
        m['y'] = round(pad_t + plot_h - (m[value_key] / vmax * plot_h), 1)
    y_ticks = []
    for k in range(5):
        tick_val = round(vmax * k / 4)
        y_ticks.append({'value': tick_val, 'y': round(pad_t + plot_h - (tick_val / vmax * plot_h), 1)})
    return chart_bottom, y_ticks

def _performance_data(conn, date_from, date_to):
    """Real driver + vehicle performance rollup — shared by performance() (website) and
    app_performance() (mobile). Same full real cost basis (see the big comment blocks below) for
    both driver and vehicle profit. Returns a dict of the two unpaginated row sets plus their real
    totals/trends; does not close conn."""

    # ---------- Driver Performance (same calculation as the original Driver Performance page) ----------
    # Full real cost basis, matching Route Analytics/Business Performance/Dashboard exactly — this
    # used to only subtract driver_payment+detention_charges+other_expense, silently missing toll
    # and 11 other real trip-level expense fields, and unconditionally subtracted fuel_amount even
    # on a Hired trip (where fuel is the owner's own money, not this company's cost, unless a
    # different vendor is explicitly tagged — same rule as everywhere else). total_fuel below stays
    # a purely informational "how much fuel these trips involved" figure (all trips, unconditional)
    # and is intentionally NOT part of the profit formula — profit uses trip_cost instead, which
    # applies the Hired exclusion correctly. Toll uses the exact same _trip_toll()/_toll_by_trip()
    # every other screen uses: the real linked Toll Management amount if one exists, else the
    # trip's own manual estimate — never both, so it can never be double-counted here either.
    driver_trip_query = """SELECT id, driver_name, type, billed_amount, fuel_amount, driver_adv_amount,
        driver_payment, detention_charges, other_expense, parking, agent_commission, builty_expense,
        fine, labour_charges, puncture, urea, loading_expense, unloading_expense, weighbridge_charges,
        permit_charges, toll, date
        FROM trips WHERE driver_name IS NOT NULL AND driver_name != ''"""
    dparams = []
    if date_from:
        driver_trip_query += " AND date >= ?"; dparams.append(date_from)
    if date_to:
        driver_trip_query += " AND date <= ?"; dparams.append(date_to)
    driver_trips_raw = conn.execute(driver_trip_query, dparams).fetchall()
    driver_toll_map = _toll_by_trip(conn, [t['id'] for t in driver_trips_raw])

    driver_groups = {}
    for t in driver_trips_raw:
        g = driver_groups.setdefault(t['driver_name'], {'trip_count': 0, 'total_billed': 0, 'total_fuel': 0,
                                                          'trip_cost': 0, 'last_trip': None})
        g['trip_count'] += 1
        g['total_billed'] += t['billed_amount'] or 0
        g['total_fuel'] += t['fuel_amount'] or 0
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        g['trip_cost'] += (fuel_and_adv + _trip_toll(t, driver_toll_map) + (t['driver_payment'] or 0) +
                            (t['detention_charges'] or 0) + (t['other_expense'] or 0) + (t['parking'] or 0) +
                            (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['fine'] or 0) +
                            (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) +
                            (t['loading_expense'] or 0) + (t['unloading_expense'] or 0) +
                            (t['weighbridge_charges'] or 0) + (t['permit_charges'] or 0))
        if not g['last_trip'] or (t['date'] or '') > g['last_trip']:
            g['last_trip'] = t['date']

    driver_rows = []
    for name, g in driver_groups.items():
        profit = g['total_billed'] - g['trip_cost']
        driver_rows.append({'name': name, 'trip_count': g['trip_count'], 'total_billed': g['total_billed'],
                             'total_fuel': g['total_fuel'], 'profit': profit, 'last_trip': g['last_trip']})
    driver_rows.sort(key=lambda r: r['total_billed'], reverse=True)

    total_drivers = len(driver_rows)
    driver_total_trips = sum(r['trip_count'] for r in driver_rows)
    driver_total_billed = sum(r['total_billed'] for r in driver_rows)
    driver_total_fuel = sum(r['total_fuel'] for r in driver_rows)
    driver_total_profit = sum(r['profit'] for r in driver_rows)
    driver_avg_trips = round(driver_total_trips / total_drivers, 1) if total_drivers else 0
    top5_drivers = sorted(driver_rows, key=lambda r: r['trip_count'], reverse=True)[:5]
    top5_drivers_max = max([d['trip_count'] for d in top5_drivers], default=1) or 1
    for d in top5_drivers:
        d['pct'] = round(d['trip_count'] / top5_drivers_max * 100, 1)

    dmonth_q = """SELECT substr(date,1,7) as month, COUNT(*) as trips, COALESCE(SUM(billed_amount),0) as billed
                  FROM trips WHERE driver_name IS NOT NULL AND driver_name != ''"""
    if date_from:
        dmonth_q += " AND date >= ?"
    if date_to:
        dmonth_q += " AND date <= ?"
    dmonth_q += " GROUP BY month ORDER BY month"
    driver_monthly = [{'label': m['month'], 'trips': m['trips'], 'billed': m['billed']}
                       for m in conn.execute(dmonth_q, dparams).fetchall()]
    driver_chart_bottom, driver_y_ticks = _trend_chart_coords(driver_monthly, 'trips')
    driver_trend_max = max([m['trips'] for m in driver_monthly], default=1) or 1

    # ---------- Vehicle Performance (new, mirrors the driver calculation style) ----------
    # Full real cost basis, matching Route Analytics/Business Performance/Dashboard exactly — this
    # used to only subtract driver_payment+detention_charges+other_expense+maint_cost, silently
    # missing toll (whenever it was only a manual estimate, never linked via Toll Management — the
    # old comment's "maint_cost already includes Toll Management's real cost" is only true for a
    # LINKED toll; an unlinked trips.toll estimate never creates a maintenance row at all, so it was
    # never counted anywhere here) and 11 other real trip-level expense fields. Toll is now handled
    # per-trip via the same _trip_toll()/_toll_by_trip() every other screen uses (real linked amount
    # if one exists, else the manual estimate, never both) — and maint_q below now excludes the
    # Toll category specifically, so a LINKED toll's mirrored maintenance row is never ALSO summed
    # in maint_cost on top of being counted via _trip_toll() (the same maint/toll split
    # _period_financials already uses, so this stays consistent with Dashboard/Business Performance).
    vehicle_trip_query = """SELECT t.id, t.type, t.billed_amount, t.fuel_amount, t.driver_adv_amount,
        t.driver_payment, t.detention_charges, t.other_expense, t.parking, t.agent_commission,
        t.builty_expense, t.fine, t.labour_charges, t.puncture, t.urea, t.loading_expense,
        t.unloading_expense, t.weighbridge_charges, t.permit_charges, t.toll, t.date,
        v.id as vid, v.vehicle_no, v.type as vtype
        FROM trips t JOIN vehicles v ON t.vehicle_id=v.id WHERE v.type = 'own'"""
    vparams = []
    if date_from:
        vehicle_trip_query += " AND t.date >= ?"; vparams.append(date_from)
    if date_to:
        vehicle_trip_query += " AND t.date <= ?"; vparams.append(date_to)
    vehicle_trips_raw = conn.execute(vehicle_trip_query, vparams).fetchall()
    vehicle_toll_map = _toll_by_trip(conn, [t['id'] for t in vehicle_trips_raw])

    vehicle_groups = {}
    for t in vehicle_trips_raw:
        g = vehicle_groups.setdefault(t['vid'], {'vehicle_no': t['vehicle_no'], 'type': t['vtype'],
                                                   'trip_count': 0, 'total_billed': 0, 'total_fuel': 0,
                                                   'trip_cost': 0, 'last_trip': None})
        g['trip_count'] += 1
        g['total_billed'] += t['billed_amount'] or 0
        g['total_fuel'] += t['fuel_amount'] or 0
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        g['trip_cost'] += (fuel_and_adv + _trip_toll(t, vehicle_toll_map) + (t['driver_payment'] or 0) +
                            (t['detention_charges'] or 0) + (t['other_expense'] or 0) + (t['parking'] or 0) +
                            (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['fine'] or 0) +
                            (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) +
                            (t['loading_expense'] or 0) + (t['unloading_expense'] or 0) +
                            (t['weighbridge_charges'] or 0) + (t['permit_charges'] or 0))
        if not g['last_trip'] or (t['date'] or '') > g['last_trip']:
            g['last_trip'] = t['date']

    vehicle_rows = []
    for vid, g in vehicle_groups.items():
        maint_q = "SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE vehicle_id=? AND COALESCE(category,'')!='Toll'"
        maint_params = [vid]
        if date_from:
            maint_q += " AND date >= ?"; maint_params.append(date_from)
        if date_to:
            maint_q += " AND date <= ?"; maint_params.append(date_to)
        maint_cost = conn.execute(maint_q, maint_params).fetchone()[0]
        profit = g['total_billed'] - g['trip_cost'] - maint_cost
        vehicle_rows.append({'name': g['vehicle_no'], 'type': g['type'], 'trip_count': g['trip_count'],
                              'total_billed': g['total_billed'], 'total_fuel': g['total_fuel'],
                              'maint_cost': maint_cost, 'profit': profit, 'last_trip': g['last_trip']})
    vehicle_rows.sort(key=lambda r: r['total_billed'], reverse=True)

    total_vehicles = len(vehicle_rows)
    vehicle_total_trips = sum(r['trip_count'] for r in vehicle_rows)
    vehicle_total_billed = sum(r['total_billed'] for r in vehicle_rows)
    vehicle_total_fuel = sum(r['total_fuel'] for r in vehicle_rows)
    vehicle_total_maint = sum(r['maint_cost'] for r in vehicle_rows)
    vehicle_total_profit = sum(r['profit'] for r in vehicle_rows)
    vehicle_avg_trips = round(vehicle_total_trips / total_vehicles, 1) if total_vehicles else 0
    top5_vehicles = sorted(vehicle_rows, key=lambda r: r['trip_count'], reverse=True)[:5]
    top5_vehicles_max = max([v['trip_count'] for v in top5_vehicles], default=1) or 1
    for v in top5_vehicles:
        v['pct'] = round(v['trip_count'] / top5_vehicles_max * 100, 1)

    vmonth_q = """SELECT substr(t.date,1,7) as month, COUNT(*) as trips, COALESCE(SUM(t.billed_amount),0) as billed
                  FROM trips t JOIN vehicles v ON t.vehicle_id=v.id WHERE v.type = 'own'"""
    if date_from:
        vmonth_q += " AND t.date >= ?"
    if date_to:
        vmonth_q += " AND t.date <= ?"
    vmonth_q += " GROUP BY month ORDER BY month"
    vehicle_monthly = [{'label': m['month'], 'trips': m['trips'], 'billed': m['billed']}
                        for m in conn.execute(vmonth_q, vparams).fetchall()]
    vehicle_chart_bottom, vehicle_y_ticks = _trend_chart_coords(vehicle_monthly, 'trips')
    vehicle_trend_max = max([m['trips'] for m in vehicle_monthly], default=1) or 1

    conn.close()
    return {'driver_rows': driver_rows, 'total_drivers': total_drivers, 'driver_total_trips': driver_total_trips,
        'driver_total_billed': driver_total_billed, 'driver_total_fuel': driver_total_fuel, 'driver_total_profit': driver_total_profit,
        'driver_avg_trips': driver_avg_trips, 'top5_drivers': top5_drivers,
        'driver_monthly': driver_monthly, 'driver_trend_max': driver_trend_max,
        'driver_chart_bottom': driver_chart_bottom, 'driver_y_ticks': driver_y_ticks,
        'vehicle_rows': vehicle_rows, 'total_vehicles': total_vehicles, 'vehicle_total_trips': vehicle_total_trips,
        'vehicle_total_billed': vehicle_total_billed, 'vehicle_total_fuel': vehicle_total_fuel, 'vehicle_total_maint': vehicle_total_maint,
        'vehicle_total_profit': vehicle_total_profit, 'vehicle_avg_trips': vehicle_avg_trips, 'top5_vehicles': top5_vehicles,
        'vehicle_monthly': vehicle_monthly, 'vehicle_trend_max': vehicle_trend_max,
        'vehicle_chart_bottom': vehicle_chart_bottom, 'vehicle_y_ticks': vehicle_y_ticks,
        'f_date_from': date_from, 'f_date_to': date_to}

@app.route('/performance')
def performance():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    d = _performance_data(conn, date_from, date_to)

    # ---------- Pagination (client-independent, shared page-size convention) ----------
    per_page_raw = request.args.get('per_page')
    d_page, per_page, d_total_pages = _paginate(request.args.get('d_page'), per_page_raw, d['total_drivers'],
                                                 per_page_options=(5, 10, 25, 50), default_per_page=5)
    driver_page_rows = d['driver_rows'][(d_page - 1) * per_page: d_page * per_page]

    v_page, per_page, v_total_pages = _paginate(request.args.get('v_page'), per_page_raw, d['total_vehicles'],
                                                 per_page_options=(5, 10, 25, 50), default_per_page=5)
    vehicle_page_rows = d['vehicle_rows'][(v_page - 1) * per_page: v_page * per_page]
    d_page_tokens = _page_tokens(d_page, d_total_pages)
    v_page_tokens = _page_tokens(v_page, v_total_pages)

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    for key in ('d_page', 'v_page', 'per_page', 'tab'):
        base_params.pop(key, None)
    base_qs = urlencode(base_params)

    return render_template('performance.html', base_qs=base_qs,
        driver_rows=driver_page_rows, total_drivers=d['total_drivers'], driver_total_trips=d['driver_total_trips'],
        driver_total_billed=d['driver_total_billed'], driver_total_fuel=d['driver_total_fuel'], driver_total_profit=d['driver_total_profit'],
        driver_avg_trips=d['driver_avg_trips'], top5_drivers=d['top5_drivers'],
        driver_monthly=d['driver_monthly'], driver_trend_max=d['driver_trend_max'], driver_chart_bottom=d['driver_chart_bottom'], driver_y_ticks=d['driver_y_ticks'],
        d_page=d_page, d_total_pages=d_total_pages, d_page_tokens=d_page_tokens,
        vehicle_rows=vehicle_page_rows, total_vehicles=d['total_vehicles'], vehicle_total_trips=d['vehicle_total_trips'],
        vehicle_total_billed=d['vehicle_total_billed'], vehicle_total_fuel=d['vehicle_total_fuel'], vehicle_total_maint=d['vehicle_total_maint'],
        vehicle_total_profit=d['vehicle_total_profit'], vehicle_avg_trips=d['vehicle_avg_trips'], top5_vehicles=d['top5_vehicles'],
        vehicle_monthly=d['vehicle_monthly'], vehicle_trend_max=d['vehicle_trend_max'], vehicle_chart_bottom=d['vehicle_chart_bottom'], vehicle_y_ticks=d['vehicle_y_ticks'],
        v_page=v_page, v_total_pages=v_total_pages, v_page_tokens=v_page_tokens,
        per_page=per_page, f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], active='performance')

@app.route('/performance/export')
def export_performance():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')

    driver_query = """SELECT driver_name, COUNT(*) as trip_count, SUM(billed_amount) as total_billed,
        SUM(fuel_amount) as total_fuel,
        SUM(COALESCE(driver_payment,0)+COALESCE(detention_charges,0)+COALESCE(other_expense,0)) as total_other_costs,
        MAX(date) as last_trip
        FROM trips WHERE driver_name IS NOT NULL AND driver_name != ''"""
    dparams = []
    if date_from:
        driver_query += " AND date >= ?"; dparams.append(date_from)
    if date_to:
        driver_query += " AND date <= ?"; dparams.append(date_to)
    driver_query += " GROUP BY driver_name ORDER BY total_billed DESC"
    driver_raw = conn.execute(driver_query, dparams).fetchall()

    # Toll excluded from both queries above/below — Toll Management can't be attributed per driver,
    # and the per-vehicle maint_cost query already carries it (unfiltered maintenance sum), so
    # adding trips.toll here would either be unattributable or double-counted.
    vehicle_query = """SELECT v.id, v.vehicle_no, v.type, COUNT(t.id) as trip_count,
        COALESCE(SUM(t.billed_amount),0) as total_billed, COALESCE(SUM(t.fuel_amount),0) as total_fuel,
        COALESCE(SUM(COALESCE(t.driver_payment,0)+COALESCE(t.detention_charges,0)+COALESCE(t.other_expense,0)),0) as total_other_costs,
        MAX(t.date) as last_trip
        FROM trips t JOIN vehicles v ON t.vehicle_id=v.id WHERE v.type = 'own'"""
    vparams = []
    if date_from:
        vehicle_query += " AND t.date >= ?"; vparams.append(date_from)
    if date_to:
        vehicle_query += " AND t.date <= ?"; vparams.append(date_to)
    vehicle_query += " GROUP BY v.id ORDER BY total_billed DESC"
    vehicle_raw = conn.execute(vehicle_query, vparams).fetchall()

    vehicle_rows = []
    for r in vehicle_raw:
        maint_q = "SELECT COALESCE(SUM(amount),0) FROM maintenance WHERE vehicle_id=?"
        maint_params = [r['id']]
        if date_from:
            maint_q += " AND date >= ?"; maint_params.append(date_from)
        if date_to:
            maint_q += " AND date <= ?"; maint_params.append(date_to)
        maint_cost = conn.execute(maint_q, maint_params).fetchone()[0]
        profit = (r['total_billed'] or 0) - (r['total_fuel'] or 0) - (r['total_other_costs'] or 0) - maint_cost
        vehicle_rows.append((r['vehicle_no'], r['type'], r['trip_count'], r['total_billed'] or 0,
                              r['total_fuel'] or 0, maint_cost, profit, r['last_trip'] or ''))
    conn.close()

    navy = "1B2A4A"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=navy)

    wb = Workbook()
    ws = wb.active
    ws.title = "Drivers"
    headers = ["Driver", "Trips", "Total Billed", "Fuel Cost", "Other Costs", "Profit", "Last Trip"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, r in enumerate(driver_raw, 2):
        profit = (r['total_billed'] or 0) - (r['total_fuel'] or 0) - (r['total_other_costs'] or 0)
        row = (r['driver_name'], r['trip_count'], r['total_billed'] or 0, r['total_fuel'] or 0,
               r['total_other_costs'] or 0, profit, r['last_trip'] or '')
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)

    ws2 = wb.create_sheet("Vehicles")
    headers2 = ["Vehicle No", "Type", "Trips", "Total Billed", "Fuel Cost", "Maintenance Cost", "Profit", "Last Trip"]
    for i, h in enumerate(headers2, 1):
        c = ws2.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, row in enumerate(vehicle_rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws2.cell(row=r_idx, column=c_idx, value=val)

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='performance_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def _employee_mobile(conn, raw, exclude_id=None):
    """(ten_digit_mobile, None) or (None, message). An employee's mobile number is mandatory: it is how the app recognises the same person's
    login (the login's mobile number is matched to it) when it decides whose salary that user may see. Must be a valid 10-digit Indian
    mobile and unique among employees."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if not digits:
        return None, 'Mobile number is required.'
    if not USERNAME_MOBILE_RE.match(digits):
        return None, 'Mobile number must be a valid 10-digit Indian mobile number.'
    for r in conn.execute("SELECT id, name, mobile FROM employees").fetchall():
        if r['id'] != exclude_id and _digits10(r['mobile']) == digits:
            return None, f'Mobile number {digits} is already used by employee "{r["name"]}".'
    return digits, None


def _insert_employee_from_form(conn, f):
    """Builds and inserts one new employee — same fields, same required set (Name/Type), same
    duplicate-name no-op (an existing employee with that name just isn't re-created — matching
    the website's own silent-skip behavior) and same auto-generated employee_code fallback used
    by both add_employee() (website) and app_add_employee() (mobile). Returns
    (ok, error_message_or_new_employee_id_or_None) — does not commit or close conn."""
    name = (f.get('name') or '').strip()
    etype = f.get('type')
    if not name:
        return False, 'Employee Name is required.'
    if etype not in ('Driver', 'Staff'):
        return False, 'Type is required.'

    basic_salary, err = _parse_money(f.get('basic_salary'), 'Basic salary')
    if err:
        return False, err
    existing = conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if existing:
        return True, None
    mobile, err = _employee_mobile(conn, f.get('mobile'))
    if err:
        return False, err
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""INSERT INTO employees (name, type, employee_code, role, mobile, email, address,
                          joining_date, date_of_birth, bank_account, ifsc_code, upi_id, emergency_contact,
                          aadhaar, pan, driving_license, status, basic_salary, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'Active', ?, ?, ?)""",
                        (name, etype, f.get('employee_code') or None, f.get('role') or etype,
                         mobile, f.get('email') or None, f.get('address') or None,
                         f.get('joining_date') or None, f.get('date_of_birth') or None,
                         f.get('bank_account') or None, f.get('ifsc_code') or None, f.get('upi_id') or None,
                         f.get('emergency_contact') or None, f.get('aadhaar') or None, f.get('pan') or None,
                         f.get('driving_license') or None, basic_salary,
                         session.get('user_id'), now))
    new_id = cur.lastrowid
    if not f.get('employee_code'):
        conn.execute("UPDATE employees SET employee_code=?, updated_by=?, updated_at=? WHERE id=?",
                     (f"{'DR' if etype=='Driver' else 'ST'}-{new_id:03d}", session.get('user_id'), now, new_id))
    return True, new_id

@app.route('/employees/add', methods=['GET', 'POST'])
def add_employee():
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        name = (f.get('name') or '').strip()
        ok, result = _insert_employee_from_form(conn, f)
        if not ok:
            conn.close()
            if _wants_json():
                return jsonify({'ok': False, 'error': result}), 400
            return redirect(url_for('salaries_list', tab='overview', emp_error=result))
        conn.commit()
        # 'next' lets the Add Employee modal (which lives on the tabbed Employees page) return the
        # admin to that page instead of the old standalone employee_ledger detail view.
        conn.close()
        if _wants_json():
            return jsonify({'ok': True})
        if request.form.get('next') == 'employees':
            return redirect(url_for('salaries_list', tab='overview'))
        return redirect(url_for('employee_ledger', employee=name))
    conn.close()
    return render_template('add_employee.html', active='salaries')

# Standard GST state-code prefix (first 2 digits of a GSTIN) → state name. Public, fixed mapping —
# used only to derive a "State" display from a real GSTIN already on file, never fabricated data.
GST_STATE_CODES = {
    '01':'Jammu & Kashmir','02':'Himachal Pradesh','03':'Punjab','04':'Chandigarh','05':'Uttarakhand',
    '06':'Haryana','07':'Delhi','08':'Rajasthan','09':'Uttar Pradesh','10':'Bihar','11':'Sikkim',
    '12':'Arunachal Pradesh','13':'Nagaland','14':'Manipur','15':'Mizoram','16':'Tripura','17':'Meghalaya',
    '18':'Assam','19':'West Bengal','20':'Jharkhand','21':'Odisha','22':'Chhattisgarh','23':'Madhya Pradesh',
    '24':'Gujarat','25':'Daman & Diu','26':'Dadra & Nagar Haveli','27':'Maharashtra','28':'Andhra Pradesh (Old)',
    '29':'Karnataka','30':'Goa','31':'Lakshadweep','32':'Kerala','33':'Tamil Nadu','34':'Puducherry',
    '35':'Andaman & Nicobar','36':'Telangana','37':'Andhra Pradesh','38':'Ladakh',
}
def _gstin_state(gstin):
    if not gstin or len(gstin) < 2:
        return ''
    return GST_STATE_CODES.get(gstin[:2], '')

@app.route('/invoice-center')
def invoice_center():
    conn = get_db()
    invoice_type = request.args.get('invoice_type', 'party')
    party_id = request.args.get('party_id', '')
    vendor_id = request.args.get('vendor_id', '')
    vehicle_f = request.args.get('vehicle', '')
    lr_f = request.args.get('lr_number', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status_f = request.args.get('invoice_status', '')

    parties = conn.execute("SELECT id, name, contact, gstin FROM parties WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    vendors = conn.execute("SELECT id, name, contact, linked_party_id, gstin FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()

    # Combined owner picker for Hired Vehicle Invoices: every vendor, plus every party not already
    # linked to a vendor — so any organization can be picked as an owner regardless of which
    # table it currently lives in. A party-only pick has no vendor row yet, so resolve/create
    # one and redirect to a plain vendor_id before anything downstream touches it.
    linked_party_ids = {v['linked_party_id'] for v in vendors if v['linked_party_id']}
    owner_options = [{'ref': f"v:{v['id']}", 'name': v['name']} for v in vendors]
    owner_options += [{'ref': f"p:{p['id']}", 'name': p['name']} for p in parties if p['id'] not in linked_party_ids]
    owner_options.sort(key=lambda o: o['name'].lower())

    if invoice_type == 'vehicle_owner' and vendor_id.startswith('p:'):
        p_row = conn.execute("SELECT name FROM parties WHERE id=?", (vendor_id[2:],)).fetchone()
        resolved_id = get_or_create_vendor(conn, p_row['name']) if p_row else None
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_center', invoice_type=invoice_type, vendor_id=resolved_id or ''))
    elif vendor_id.startswith('v:'):
        vendor_id = vendor_id[2:]

    selected_party = None
    selected_vendor = None
    trips = []
    # Which trips already sit in some previously-generated invoice batch — real, derived from
    # invoice_batch_trips, used for the Invoice Status filter/badge (not a stored flag on trips).
    invoiced_trip_ids = {r['trip_id'] for r in conn.execute("SELECT DISTINCT trip_id FROM invoice_batch_trips").fetchall()}

    _CHARGE_COLS = """t.loading_charge, t.unloading_charge, t.permit_charges, t.weight_charges,
                   t.driver_payment, t.gps_cost, t.other_charges, t.fuel_amount, t.driver_adv_amount, t.toll,
                   t.detention_charges, t.police_charges, t.sim_tracking, t.union_charges,
                   t.brokerage, t.builty_commission, t.late_fees, t.material_damage, t.shortage_amount,
                   t.tds, t.other_deductions,"""
    if invoice_type in ('party', 'tax', 'bill') and party_id:
        selected_party = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
        query = f"""SELECT t.id, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, t.date, t.billed_amount,
                   {_CHARGE_COLS}
                   t.party_id
                   FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                   WHERE t.party_id=?"""
        params = [party_id]
        if vehicle_f:
            query += " AND v.vehicle_no=?"; params.append(vehicle_f)
        if lr_f:
            query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_f}%")
        if date_from:
            query += " AND t.date>=?"; params.append(date_from)
        if date_to:
            query += " AND t.date<=?"; params.append(date_to)
        query += " ORDER BY t.date DESC LIMIT 200"
        trips = conn.execute(query, params).fetchall()
    elif invoice_type == 'vehicle_owner' and vendor_id:
        selected_vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        query = f"""SELECT t.id, t.lr_number, v.vehicle_no, t.from_loc, t.to_loc, t.date,
                   CASE WHEN t.owner_rate_type='FIXED' THEN t.owner_fixed_amount ELSE t.owner_rate*t.quantity END as billed_amount,
                   {_CHARGE_COLS}
                   t.fuel_vendor_id, t.driver_adv_vendor_id, t.owner_vendor_id
                   FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                   WHERE t.owner_vendor_id=?"""
        params = [vendor_id]
        if vehicle_f:
            query += " AND v.vehicle_no=?"; params.append(vehicle_f)
        if lr_f:
            query += " AND t.lr_number LIKE ?"; params.append(f"%{lr_f}%")
        if date_from:
            query += " AND t.date>=?"; params.append(date_from)
        if date_to:
            query += " AND t.date<=?"; params.append(date_to)
        query += " ORDER BY t.date DESC LIMIT 200"
        trips = conn.execute(query, params).fetchall()

    # Every field/item eligible for the "Trip Charges & Costs" popup, per trip — only fields this
    # trip actually has a non-zero value in are included, so a field the trip never used never
    # shows up as an empty toggle. Toll uses the real linked Toll Management total (same source
    # _build_invoice_pdf itself uses) rather than any raw trip column, so the popup can never show
    # a different toll figure than what actually ends up on the PDF. Fuel/Driver Advance only ever
    # appear on a vehicle_owner invoice (they net off what's still owed to the owner — meaningless
    # on a party invoice, which never bills these to the party at all).
    toll_map = _toll_by_trip(conn, [t['id'] for t in trips]) if trips else {}
    # 'default':'in' fields (or omitted, same thing) already flow into every invoice today —
    # checked by default, so nothing changes unless deliberately unchecked. 'default':'out' fields
    # have NEVER appeared on any Invoice Center invoice before (they only ever fed into
    # trips.billed_amount at trip-creation time, a figure the actual generated PDF never reads) —
    # unchecked by default, so no existing or future invoice total shifts unless deliberately opted in.
    _CHARGE_FIELD_LABELS = [
        ('loading', 'Loading Charges', 'loading_charge', 'in'), ('unloading', 'Unloading Charges', 'unloading_charge', 'in'),
        ('permit', 'Route Permit Charges', 'permit_charges', 'in'), ('weighment', 'Weighment Charges', 'weight_charges', 'in'),
        ('driver_bata', 'Driver Bata', 'driver_payment', 'in'), ('gps', 'GPS Cost', 'gps_cost', 'in'),
        ('other', 'Other Expenses', 'other_charges', 'in'),
        ('detention', 'Detention Charges', 'detention_charges', 'out'), ('police', 'Police Charges', 'police_charges', 'out'),
        ('sim_tracking', 'SIM Tracking', 'sim_tracking', 'out'), ('union', 'Union Charges', 'union_charges', 'out'),
    ]
    _DEDUCTION_FIELD_LABELS = [
        ('brokerage', 'Brokerage', 'brokerage'), ('builty_commission', 'Builty Commission', 'builty_commission'),
        ('late_fees', 'Late Fees', 'late_fees'), ('material_damage', 'Material Damage', 'material_damage'),
        ('shortage_amount', 'Shortage Amount', 'shortage_amount'), ('trip_tds', 'TDS (Trip)', 'tds'),
        ('other_deductions', 'Other Deductions', 'other_deductions'),
    ]
    trip_rows = []
    for t in trips:
        is_invoiced = t['id'] in invoiced_trip_ids
        if status_f == 'invoiced' and not is_invoiced:
            continue
        if status_f == 'not_invoiced' and is_invoiced:
            continue
        charges = []
        for key, label, col, default in _CHARGE_FIELD_LABELS:
            amt = t[col] or 0
            if amt:
                charges.append({'key': key, 'label': label, 'amount': amt, 'kind': 'charge', 'default': default})
        toll_amt = _trip_toll(t, toll_map)
        if toll_amt:
            charges.append({'key': 'toll', 'label': 'Toll Charges', 'amount': toll_amt, 'kind': 'charge', 'default': 'in'})
        # All 7 deduction fields have never fed into any Invoice Center total before (see comment
        # above) — always 'out' by default, same reasoning as Detention/Police/SIM/Union.
        for key, label, col in _DEDUCTION_FIELD_LABELS:
            amt = t[col] or 0
            if amt:
                charges.append({'key': key, 'label': label, 'amount': amt, 'kind': 'deduction', 'default': 'out'})
        if invoice_type == 'vehicle_owner':
            # only fuel / advance the company really paid is deducted from the owner (see _company_paid_on_hired_trip)
            if _company_paid_on_hired_trip(t, 'fuel'):
                charges.append({'key': 'fuel_deduction', 'label': 'Fuel Advance', 'amount': _company_paid_on_hired_trip(t, 'fuel'), 'kind': 'owner_deduction', 'default': 'in'})
            if _company_paid_on_hired_trip(t, 'driver_adv'):
                charges.append({'key': 'driver_adv_deduction', 'label': 'Driver Advance', 'amount': _company_paid_on_hired_trip(t, 'driver_adv'), 'kind': 'owner_deduction', 'default': 'in'})
        items = conn.execute("SELECT id, description, amount, item_type FROM invoice_items WHERE trip_id=?", (t['id'],)).fetchall()
        for it in items:
            charges.append({'key': f"item:{it['id']}", 'label': it['description'] or 'Other', 'amount': it['amount'] or 0, 'kind': it['item_type'], 'default': 'in'})
        trip_rows.append({'t': t, 'is_invoiced': is_invoiced, 'charges': charges})

    # Plain {trip_id: [charges]} map for the Trip Charges & Costs popup's JS — built here rather
    # than reached for via Jinja attribute access, since sqlite3.Row doesn't support dotted
    # attribute lookup the way a plain dict/object would.
    trip_charges_map = {row['t']['id']: row['charges'] for row in trip_rows}

    entity_gstin = (selected_vendor['gstin'] if selected_vendor else (selected_party['gstin'] if selected_party else '')) or ''
    entity_state = _gstin_state(entity_gstin)
    invoice_settings = _get_invoice_settings(conn, session.get('company_id', 1))

    vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL AND COALESCE(status,'Active')='Active' ORDER BY vehicle_no").fetchall()
    conn.close()
    import datetime as _dt
    today_str = _dt.date.today().strftime('%Y-%m-%d')
    return render_template('invoice_center.html', invoice_type=invoice_type, parties=parties, vendors=vendors,
                            owner_options=owner_options,
                            selected_party=selected_party, selected_vendor=selected_vendor, trip_rows=trip_rows,
                            trip_charges_map=trip_charges_map,
                            vehicles=vehicles, entity_gstin=entity_gstin, entity_state=entity_state,
                            invoice_settings=invoice_settings, today=today_str,
                            f_party_id=party_id, f_vendor_id=vendor_id, f_vehicle=vehicle_f, f_lr=lr_f,
                            f_date_from=date_from, f_date_to=date_to, f_status=status_f, active='invoices')

@app.route('/invoice-center/review', methods=['POST'])
def invoice_center_review():
    trip_ids = request.form.getlist('trip_ids')
    invoice_type = request.form.get('invoice_type')
    party_id = request.form.get('party_id') or None
    vendor_id = request.form.get('vendor_id') or None

    conn = get_db()
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if invoice_type == 'vehicle_owner' and vendor_id:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    elif party_id:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()

    _row_bank_name = conn.execute("SELECT value FROM settings WHERE key='bank_name'").fetchone()
    bank_name = _row_bank_name['value'] if _row_bank_name else ''
    _row_account_number = conn.execute("SELECT value FROM settings WHERE key='account_number'").fetchone()
    account_number = _row_account_number['value'] if _row_account_number else ''
    _row_ifsc_code = conn.execute("SELECT value FROM settings WHERE key='ifsc_code'").fetchone()
    ifsc_code = _row_ifsc_code['value'] if _row_ifsc_code else ''
    _row_account_holder = conn.execute("SELECT value FROM settings WHERE key='account_holder'").fetchone()
    account_holder = _row_account_holder['value'] if _row_account_holder else ''
    _row_rcm_clause = conn.execute("SELECT value FROM settings WHERE key='rcm_clause'").fetchone()
    rcm_clause = _row_rcm_clause['value'] if _row_rcm_clause else ''
    conn.close()

    line_items = []
    for t in trips:
        if invoice_type == 'vehicle_owner':
            freight = t['owner_fixed_amount'] if (t['owner_rate_type'] or 'PER_MT')=='FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
            # What is still owed to the owner = freight - cash already paid to him - fuel / driver
            # advance the COMPANY paid on his trip. Fuel/advance the owner paid himself (vendor =
            # the owner) is inside his freight and is never deducted. Same rule as the vendor ledger.
            net = (freight - (t['paid_to_owner'] or 0)
                   - _company_paid_on_hired_trip(t, 'fuel') - _company_paid_on_hired_trip(t, 'driver_adv'))
        else:
            freight = _trip_freight(t['quantity'], t['rate'], t['rate_type'], t['fixed_rate_amount'], t['guarantee_qty'])
            net = t['billed_amount'] or 0
        line_items.append({'trip': t, 'freight': freight, 'net': net})

    total_freight = sum(li['freight'] for li in line_items)
    total_trips = len(line_items)

    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    return render_template('invoice_review.html', line_items=line_items, entity=entity, invoice_type=invoice_type,
                            party_id=party_id, vendor_id=vendor_id, total_freight=total_freight, total_trips=total_trips,
                            bank_name=bank_name, account_number=account_number, ifsc_code=ifsc_code,
                            account_holder=account_holder, rcm_clause=rcm_clause, today=today, active='invoices')

def number_to_words_inr(n):
    n = int(round(n))
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
            'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
    def two_digit(num):
        if num < 20:
            return ones[num]
        return tens[num // 10] + (' ' + ones[num % 10] if num % 10 else '')
    def three_digit(num):
        if num >= 100:
            return ones[num // 100] + ' Hundred' + (' ' + two_digit(num % 100) if num % 100 else '')
        return two_digit(num)
    if n == 0:
        return 'Zero'
    parts = []
    crore = n // 10000000; n %= 10000000
    lakh = n // 100000; n %= 100000
    thousand = n // 1000; n %= 1000
    hundred = n
    if crore: parts.append(two_digit(crore) + ' Crore')
    if lakh: parts.append(two_digit(lakh) + ' Lakh')
    if thousand: parts.append(two_digit(thousand) + ' Thousand')
    if hundred: parts.append(three_digit(hundred))
    return ' '.join(parts)

INVOICE_SETTINGS_KEYS = ['company_name', 'address', 'gstin', 'pan', 'phone', 'email', 'bank_name', 'account_holder',
                         'account_number', 'ifsc_code', 'branch', 'rcm_clause', 'cgst_rate', 'sgst_rate',
                         'invoice_prefix', 'next_invoice_number', 'logo_path', 'show_company_logo']
LOGO_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'logo')
os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)

def _get_invoice_settings(conn, company_id=1):
    """company_id defaults to 1 — today that's the only company that will ever exist (see
    migrate_company_id.sql), so every call site passing session.get('company_id', 1) behaves
    identically to before this parameter existed. The moment a real multi-company login exists
    (Step B of the multi-tenancy plan), session will actually carry a real company_id and every
    one of these call sites starts reading the right company's settings with no further changes
    needed here."""
    s = {}
    for key in INVOICE_SETTINGS_KEYS:
        row = conn.execute("SELECT value FROM settings WHERE key=? AND company_id=?", (key, company_id)).fetchone()
        s[key] = row['value'] if row else ''
    return s

LOGO_MAX_SIDE = 600          # px: the logo is shown at ~50 px on screen and ~0.45 inch on an invoice, so more detail than this is wasted bytes
LOGO_BAD_FILE = ('That file could not be read as a picture. Please choose a JPG, PNG, GIF, WEBP, BMP or TIFF image '
                 '(a photo saved as HEIC or a vector SVG must be saved as JPG or PNG first).')


def _normalise_logo_image(data):
    """Turns an uploaded picture of ANY common format (JPG/JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO ...) into one clean PNG: (png_bytes, None) or
    (None, message). The file is judged by what it really contains, not by its extension, so a JPEG named .png (or the reverse) works; a
    text file or a truncated image is refused instead of being stored as a broken logo. A phone photo's rotation flag is applied, an
    animated GIF/WEBP keeps its first frame, CMYK / palette / 16-bit pictures are converted, and a big picture is shrunk to LOGO_MAX_SIDE."""
    import io
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, 'Picture support is not installed on this server (Pillow). Ask your administrator.'
    if not data:
        return None, 'That file is empty.'
    # No "looks like SVG" pre-check on purpose: a real picture's metadata (PNG text chunks, XMP) can contain the text "<svg", and
    # Pillow cannot open a genuine SVG anyway, so that case lands in the except below with the same message.
    try:
        im = Image.open(io.BytesIO(data))
        im.load()                                   # decode every pixel now, so a truncated file is caught here
    except Exception:                               # unknown format, corrupt data, decompression bomb ...
        return None, LOGO_BAD_FILE
    try:
        im.seek(0)                                  # animated file: first frame
    except EOFError:
        pass
    try:
        im = ImageOps.exif_transpose(im)            # honour the camera's rotation flag
    except Exception:
        pass
    try:
        has_alpha = im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info) or 'transparency' in im.info
        im = im.convert('RGBA' if has_alpha else 'RGB')
    except Exception:
        return None, LOGO_BAD_FILE
    if min(im.size) < 8:
        return None, 'That picture is too small to be a logo. Please choose a larger image.'
    if max(im.size) > LOGO_MAX_SIDE:
        im.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, 'PNG', optimize=True)
    return out.getvalue(), None


def _trim_logo_margins(im, pad_frac=0.04):
    """Crops the empty border (transparent, or the same colour as the corners) off a logo so the mark itself, not a wide white frame, fills the
    space it is printed in. Returns the picture unchanged when there is nothing to trim or the whole picture is one colour."""
    from PIL import Image, ImageChops
    try:
        if im.mode == 'RGBA' and im.getchannel('A').getextrema()[0] < 250:
            box = im.getchannel('A').point(lambda v: 255 if v > 12 else 0).getbbox()
        else:
            rgb = im.convert('RGB')
            bg = Image.new('RGB', rgb.size, rgb.getpixel((0, 0)))
            box = ImageChops.difference(rgb, bg).convert('L').point(lambda v: 255 if v > 28 else 0).getbbox()
        if not box:
            return im
        padx, pady = int((box[2] - box[0]) * pad_frac), int((box[3] - box[1]) * pad_frac)
        box = (max(0, box[0] - padx), max(0, box[1] - pady), min(im.size[0], box[2] + padx), min(im.size[1], box[3] + pady))
        return im.crop(box)
    except Exception:
        return im


@app.route('/settings/logo/upload', methods=['POST'])
def upload_company_logo():
    """Company logo shown on every generated invoice PDF — uploaded once here, reused everywhere _build_invoice_pdf runs.
    Any common picture format is accepted (see _normalise_logo_image); it is stored as a clean PNG under uploads/logo/<company_id>/."""
    f = request.files.get('logo')
    error = None
    rel_path = None
    if not (f and f.filename):
        error = 'Choose a picture file first.'
    else:
        png, error = _normalise_logo_image(f.read())
        if not error:
            company_id = session.get('company_id', 1)
            unique = f"logo_{int(datetime.datetime.now().timestamp() * 1000)}.png"
            company_dir = os.path.join(LOGO_UPLOAD_DIR, str(company_id))
            os.makedirs(company_dir, exist_ok=True)
            with open(os.path.join(company_dir, unique), 'wb') as fh:
                fh.write(png)
            rel_path = f"uploads/logo/{company_id}/{unique}"
            _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn = get_db()
            conn.execute("""INSERT OR REPLACE INTO settings (key, value, company_id, created_by, created_at, updated_by, updated_at)
                            VALUES ('logo_path', ?, ?, ?, ?, ?, ?)""",
                         (rel_path, company_id, session.get('user_id'), _now, session.get('user_id'), _now))
            conn.commit()
            conn.close()
    if _wants_json():
        if error:
            return jsonify({'ok': False, 'error': error}), 400
        return jsonify({'ok': True, 'logo_url': '/static/' + rel_path})
    if error:
        return redirect(url_for('settings_page', tab='company', logo_error=error))
    return redirect(url_for('settings_page', tab='company'))


# Fields that have NEVER fed into any Invoice Center total before this popup existed — they only
# ever affected trips.billed_amount at trip-creation time, a figure the generated PDF never reads.
# Opt-IN only (see _incl below): including one changes a real invoice total, so it takes a
# deliberate tick in the popup rather than defaulting on, unlike every field that was already live.
_OPT_IN_CHARGE_KEYS = {'detention', 'police', 'sim_tracking', 'union',
                       'brokerage', 'builty_commission', 'late_fees', 'material_damage',
                       'shortage_amount', 'trip_tds', 'other_deductions'}

def _build_invoice_pdf(trips, invoice_type, entity, s, invoice_number, invoice_date, due_date, payment_status, remarks,
                        cgst_rate=0, sgst_rate=0, extra_loading=0, extra_other=0, tds_rate=0, extra_items=None, toll_map=None,
                        excluded_charge_keys=None, included_charge_keys=None):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    import io, os

    # Per-trip, per-field toggle from Invoice Center's "Trip Charges & Costs" popup — a trip's own
    # charge/deduction fields are zeroed out (or zeroed unless opted in) here rather than filtered
    # further up, so every downstream total (freight_and_charges, additional_charges,
    # total_already_given) is automatically correct with no separate bookkeeping. Two distinct
    # sets because two distinct defaults: excluded_charge_keys holds fields that were already on
    # every invoice and got deliberately unchecked (opt-OUT); included_charge_keys holds fields
    # that were never on any invoice before and got deliberately checked ON (opt-IN, see
    # _OPT_IN_CHARGE_KEYS). Both empty/None changes nothing, matching every invoice ever generated
    # before this feature existed.
    excluded_charge_keys = excluded_charge_keys or set()
    included_charge_keys = included_charge_keys or set()
    def _incl(trip_id, key, val):
        full = f"{trip_id}:{key}"
        if key in _OPT_IN_CHARGE_KEYS:
            return val if full in included_charge_keys else 0
        return 0 if full in excluded_charge_keys else val

    # Per-trip freight and charge breakdown
    line_items = []
    for t in trips:
        if invoice_type == 'vehicle_owner':
            freight = t['owner_fixed_amount'] if (t['owner_rate_type'] or 'PER_MT')=='FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        else:
            freight = _trip_freight(t['quantity'], t['rate'], t['rate_type'], t['fixed_rate_amount'], t['guarantee_qty'])
        tid = t['id']
        line_items.append({
            'trip': t, 'freight': freight,
            'loading': _incl(tid, 'loading', t['loading_charge'] or 0),
            'unloading': _incl(tid, 'unloading', t['unloading_charge'] or 0),
            # Real Toll Management amount for this trip if it has one linked, else the trip's own
            # manual estimate — never both, so the same real toll isn't billed twice.
            'permit': _incl(tid, 'permit', t['permit_charges'] or 0),
            'toll': _incl(tid, 'toll', _trip_toll(t, toll_map or {})),
            'weighment': _incl(tid, 'weighment', t['weight_charges'] or 0),
            'driver_bata': _incl(tid, 'driver_bata', t['driver_payment'] or 0),
            'gps': _incl(tid, 'gps', t['gps_cost'] or 0),
            'other': _incl(tid, 'other', t['other_charges'] or 0),
            # Opt-in only — see _OPT_IN_CHARGE_KEYS above.
            'detention': _incl(tid, 'detention', t['detention_charges'] or 0),
            'police': _incl(tid, 'police', t['police_charges'] or 0),
            'sim_tracking': _incl(tid, 'sim_tracking', t['sim_tracking'] or 0),
            'union': _incl(tid, 'union', t['union_charges'] or 0),
            'brokerage': _incl(tid, 'brokerage', t['brokerage'] or 0),
            'builty_commission': _incl(tid, 'builty_commission', t['builty_commission'] or 0),
            'late_fees': _incl(tid, 'late_fees', t['late_fees'] or 0),
            'material_damage': _incl(tid, 'material_damage', t['material_damage'] or 0),
            'shortage_amount': _incl(tid, 'shortage_amount', t['shortage_amount'] or 0),
            'trip_tds': _incl(tid, 'trip_tds', t['tds'] or 0),
            'other_deductions': _incl(tid, 'other_deductions', t['other_deductions'] or 0),
            # fuel_deduction/driver_adv_deduction only ever matter on a vehicle_owner invoice
            # (they net off what the company paid on the owner's behalf) — excluding one there means "pay the
            # owner in full for this trip, don't net this advance off". They're never part of
            # freight_and_charges/additional_charges on ANY invoice type, so leaving them at their
            # real value on a party invoice changes nothing a party ever sees.
            'fuel_given': _incl(tid, 'fuel_deduction', _company_paid_on_hired_trip(t, 'fuel')),
            'driver_adv_given': _incl(tid, 'driver_adv_deduction', _company_paid_on_hired_trip(t, 'driver_adv')),
            'paid_to_owner': t['paid_to_owner'] or 0,
        })

    total_freight = sum(li['freight'] for li in line_items)
    total_loading = sum(li['loading'] for li in line_items) + extra_loading
    total_unloading = sum(li['unloading'] for li in line_items)
    total_permit = sum(li['permit'] for li in line_items)
    total_toll = sum(li['toll'] for li in line_items)
    total_weighment = sum(li['weighment'] for li in line_items)
    total_driver_bata = sum(li['driver_bata'] for li in line_items)
    total_gps = sum(li['gps'] for li in line_items)
    total_other = sum(li['other'] for li in line_items) + extra_other
    total_detention = sum(li['detention'] for li in line_items)
    total_police = sum(li['police'] for li in line_items)
    total_sim = sum(li['sim_tracking'] for li in line_items)
    total_union = sum(li['union'] for li in line_items)
    total_brokerage = sum(li['brokerage'] for li in line_items)
    total_builty_commission = sum(li['builty_commission'] for li in line_items)
    total_late_fees = sum(li['late_fees'] for li in line_items)
    total_material_damage = sum(li['material_damage'] for li in line_items)
    total_shortage = sum(li['shortage_amount'] for li in line_items)
    total_trip_tds = sum(li['trip_tds'] for li in line_items)
    total_other_deductions = sum(li['other_deductions'] for li in line_items)

    # Named ad-hoc charges/deductions (per-trip "Others" items + Invoice Center extra rows), kept
    # separate from the anonymous Other Charges total above so each one can be printed on the
    # invoice by its own description (e.g. "Detention") rather than disappearing into one lump sum.
    extra_items = extra_items or []
    named_extra_total = sum((it['amount'] or 0) if it['item_type'] == 'charge' else -(it['amount'] or 0) for it in extra_items)

    freight_and_charges = total_freight + total_loading + total_unloading + total_permit + total_toll
    # Charges add, deductions subtract — same signed-sum convention named_extra_total already
    # uses, so "TOTAL ADDITIONAL CHARGES" on the PDF always matches exactly what's listed beneath it.
    additional_charges = (total_weighment + total_driver_bata + total_gps + total_other + named_extra_total +
                           total_detention + total_police + total_sim + total_union -
                           total_brokerage - total_builty_commission - total_late_fees - total_material_damage -
                           total_shortage - total_trip_tds - total_other_deductions)
    sub_total = freight_and_charges + additional_charges

    cgst_amount = round(sub_total * cgst_rate / 100, 2)
    sgst_amount = round(sub_total * sgst_rate / 100, 2)
    # TDS was previously recorded on every invoice batch (tds_rate) but never actually applied to
    # the printed total — this makes it real. Gated on tds_rate>0 (and passed 0 by any caller that
    # doesn't set it) so an un-set TDS rate changes nothing about the total or the layout below.
    tds_amount = round(sub_total * tds_rate / 100, 2) if tds_rate else 0
    pre_round = sub_total + cgst_amount + sgst_amount - tds_amount
    grand_total = round(pre_round)
    round_off = round(grand_total - pre_round, 2)

    total_fuel_given = sum(li['fuel_given'] for li in line_items)
    total_driver_adv_given = sum(li['driver_adv_given'] for li in line_items)
    total_paid_to_owner = sum(li['paid_to_owner'] for li in line_items)
    total_already_given = total_fuel_given + total_driver_adv_given + total_paid_to_owner
    net_payable = grand_total - total_already_given if invoice_type == 'vehicle_owner' else grand_total

    total_weight = sum(li['trip']['quantity'] or 0 for li in line_items)
    total_vehicles = len(set(li['trip']['vehicle_no'] for li in line_items if li['trip']['vehicle_no']))
    dates = [li['trip']['date'] for li in line_items if li['trip']['date']]
    trip_period = f"{min(dates)} to {max(dates)}" if dates else ''

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.4*inch, bottomMargin=0.4*inch, leftMargin=0.45*inch, rightMargin=0.45*inch)
    # Every full-width element below (header, item table, totals box, footer...) is sized off
    # this one constant so their left/right edges always line up with each other — no more
    # tables that are each "approximately" full width but a few points off from their neighbours.
    CONTENT_W = 6.9 * inch
    # The Additional Charges box and the Sub Total/Grand Total box sit side by side and share this
    # same gap treatment as Bill To / Trip Details, so the two never read as one fused block.
    COMBO_GAP = 0.15 * inch
    COMBO_L = 4.2 * inch - COMBO_GAP / 2
    COMBO_R = 2.7 * inch - COMBO_GAP / 2
    styles = getSampleStyleSheet()
    BLACK = colors.HexColor('#1A1A1A')
    GREY = colors.HexColor('#5A5A5A')
    LINE = colors.HexColor('#333333')
    LIGHTBG = colors.HexColor('#EFF3FA')
    company_style = ParagraphStyle('C', parent=styles['Title'], fontSize=16, textColor=BLACK, alignment=0, leading=19)
    tagline_style = ParagraphStyle('TL', parent=styles['Normal'], fontSize=7.5, textColor=GREY, leading=10)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=8, textColor=GREY, leading=11)
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=8.5, textColor=GREY, leading=12)
    section_head_style = ParagraphStyle('SH', parent=styles['Normal'], fontSize=8.5, textColor=BLACK, fontName='Helvetica-Bold')
    # Table-cell text that can genuinely run long in real data (route names, material
    # descriptions, LR numbers) — wrapped in Paragraph so it wraps inside its column instead of
    # overflowing into the next cell, which is the exact "long text overriding other text" bug
    # plain strings in a reportlab Table cell cause.
    cell_style = ParagraphStyle('CE', parent=styles['Normal'], fontSize=7.3, textColor=BLACK, leading=9)
    cell_val_style = ParagraphStyle('CV', parent=styles['Normal'], fontSize=8.3, textColor=BLACK, leading=10.5)
    def cell(text):
        """Wrap any table-cell value that might be long real data (route names, material,
        LR numbers) so ReportLab wraps it instead of letting it spill into the next column."""
        return Paragraph(esc(text) if text not in (None, '') else '—', cell_style)

    def esc(text):
        """Escape real user-entered text (names, addresses, remarks) before it goes inside a
        Paragraph's mini-XML markup — an unescaped '&' or '<' in a real company/party name
        (e.g. "R&B Transport") would otherwise break the PDF build or render garbled."""
        from xml.sax.saxutils import escape
        return escape(str(text)) if text else ''

    invoice_titles = {'party': 'INVOICE', 'vehicle_owner': 'FREIGHT BILL', 'tax': 'INVOICE', 'bill': 'INVOICE'}
    is_single = len(line_items) == 1

    def section_box(title, rows_data, col_widths, header_bg=LIGHTBG):
        head = Table([[Paragraph(f"<b>{title}</b>", section_head_style)]], colWidths=[sum(col_widths)])
        head.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), header_bg), ('BOX', (0,0), (-1,-1), 0.7, LINE),
                                    ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5), ('LEFTPADDING', (0,0), (-1,-1), 8)]))
        body = Table(rows_data, colWidths=col_widths)
        body.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.7, LINE), ('LINEBELOW', (0,0), (-1,-2), 0.3, colors.HexColor('#DDDDDD')),
            ('FONTSIZE', (0,0), (-1,-1), 8.5), ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
        ]))
        return [head, body]

    # ---------- Header ----------
    company_block = [
        Paragraph(f"<b>{esc(s['company_name'])}</b>", company_style),
        Paragraph("SAFE | RELIABLE | ON TIME", tagline_style),
        Spacer(1, 3),
        Paragraph(esc(s['address']), sub_style),
    ]
    if invoice_type == 'tax':
        company_block.append(Paragraph(f"GSTIN: {esc(s['gstin'])} &nbsp;&nbsp; PAN: {esc(s['pan'])}", sub_style))
    company_block.append(Paragraph(f"&#9742; {esc(s['phone'])} &nbsp;&nbsp; &#9993; {esc(s['email'])}", sub_style))

    # Company logo, if one's been uploaded (Settings > Company Profile) — sized modestly and
    # placed beside the company name, never dominating the header. Falls back silently to the
    # text-only header if no logo is set, or the stored file has gone missing on disk.
    # The "Show Company Logo on Invoice" switch (Settings > Invoice) is honoured: only an explicit OFF hides it.
    logo_path = s.get('logo_path') if str(s.get('show_company_logo') or '1') != '0' else None
    logo_full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', logo_path) if logo_path else None
    if logo_full_path and os.path.isfile(logo_full_path):
        try:
            # Fully decode the pixel data now (not just read the header/dimensions) so a
            # truncated or corrupt upload is caught here and falls back cleanly, rather than
            # crashing later inside doc.build() once it's too late to recover gracefully.
            from PIL import Image as PILImage
            with PILImage.open(logo_full_path) as pil_img:
                pil_img.load()
                iw, ih = pil_img.size
                # The logo prints about 0.45 inch tall, so embed a 300 px copy (~650 dpi): the stored 600 px file would add ~300 KB to every invoice for no visible gain.
                small = _trim_logo_margins(pil_img.copy())
                iw, ih = small.size
                small.thumbnail((300, 300), PILImage.LANCZOS)
                logo_bytes = io.BytesIO(); small.save(logo_bytes, 'PNG', optimize=True); logo_bytes.seek(0)
            max_h = 0.62 * inch
            logo_w = min(max_h * iw / ih, 1.4 * inch)                   # a very wide banner logo is capped so the company text keeps its room
            logo_img = Image(logo_bytes, width=logo_w, height=logo_w * ih / iw)
            header_left_w = CONTENT_W - 2.4*inch
            header_left = Table([[logo_img, company_block]], colWidths=[logo_w + 10, header_left_w - (logo_w + 10)])
            header_left.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (0, 0), 0),
                                              ('RIGHTPADDING', (0, 0), (0, 0), 8), ('LEFTPADDING', (1, 0), (1, 0), 0)]))
        except Exception:
            header_left = company_block  # a corrupt/unreadable logo file never blocks invoice generation
    else:
        header_left = company_block

    # Big bold open title (no heavy box), matching the reference's "INVOICE" treatment — a thin
    # rule under the title separates it from the metadata rather than a full border box.
    big_title_style = ParagraphStyle('BT', parent=styles['Heading1'], fontSize=18, textColor=BLACK,
                                      alignment=2, leading=20, fontName='Helvetica-Bold', spaceAfter=0)
    meta_label_style = ParagraphStyle('ML', parent=styles['Normal'], fontSize=8, textColor=GREY, alignment=2, leading=11)
    meta_val_style = ParagraphStyle('MV', parent=styles['Normal'], fontSize=9.5, textColor=BLACK, alignment=2,
                                     leading=12, fontName='Helvetica-Bold')

    status_color = colors.HexColor('#2E7D32') if payment_status == 'PAID' else colors.HexColor('#B8860B')
    status_bg = colors.HexColor('#E8F5E9') if payment_status == 'PAID' else colors.HexColor('#FFF8E1')
    status_table = Table([[payment_status]], colWidths=[1.1*inch])
    status_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), status_bg), ('TEXTCOLOR', (0,0), (-1,-1), status_color),
                                       ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 8.5),
                                       ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))

    invoice_box_rows = [
        [Paragraph(invoice_titles.get(invoice_type, 'INVOICE'), big_title_style)],
        [Spacer(1, 6)],
        [Paragraph('INVOICE NO.', meta_label_style)],
        [Paragraph(esc(invoice_number), meta_val_style)],
        [Paragraph(f"Date: {esc(invoice_date)}", meta_label_style)],
        [Spacer(1, 4)],
        [status_table],
    ]
    invoice_box = Table(invoice_box_rows, colWidths=[2.4*inch])
    invoice_box.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,0), 1.4, BLACK), ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 1), ('BOTTOMPADDING', (0,0), (-1,-1), 1),
    ]))

    header_table = Table([[header_left, invoice_box]], colWidths=[CONTENT_W - 2.4*inch, 2.4*inch])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story = [header_table, Spacer(1, 10)]

    # ---------- Bill To + Trip Details/Summary ----------
    bill_name_lines = [f"<b>{esc(entity['name'])}</b>" if entity else '—']
    if entity and entity['address']:
        bill_name_lines.append(esc(entity['address']))
    # A vehicle owner bill runs the opposite direction of every other invoice type — the owner is
    # who WE owe money to, not who owes us — so "BILL TO" (implying they're being asked to pay)
    # is backwards here. "PAY TO" makes the direction of money correct at a glance.
    bill_box_label = 'PAYABLE TO' if invoice_type == 'vehicle_owner' else 'BILL TO'
    def make_bill_box(width):
        body = Table([[Paragraph('<br/>'.join(bill_name_lines), cell_val_style)]], colWidths=[width - 0.2*inch])
        body.setStyle(TableStyle([('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        return section_box(bill_box_label, [[body]], [width])

    if is_single:
        t = line_items[0]['trip']
        col_gap = 0.15 * inch  # visible breathing room between Bill To and Trip Details, not flush-joined
        half_w = (CONTENT_W - col_gap) / 2
        trip_rows = [
            ['LR Number', ':', cell(t['lr_number'])], ['Trip Date', ':', t['date'] or ''],
            ['Vehicle No.', ':', cell(t['vehicle_no'])],
            ['From', ':', cell(t['from_loc'])], ['To', ':', cell(t['to_loc'])],
            ['Material', ':', cell(t['material'])], ['Weight', ':', f"{t['quantity'] or 0} MT"],
        ]
        trip_body = Table(trip_rows, colWidths=[1.1*inch, 0.15*inch, half_w - 1.25*inch - 0.2*inch])
        trip_body.setStyle(TableStyle([('FONTSIZE',(0,0),(-1,-1),8.3), ('TOPPADDING',(0,0),(-1,-1),2.5), ('BOTTOMPADDING',(0,0),(-1,-1),2.5),
                                        ('TEXTCOLOR',(0,0),(0,-1),GREY)]))
        trip_box = section_box('TRIP DETAILS', [[trip_body]], [half_w])
        two_col = Table([[make_bill_box(half_w), '', trip_box]], colWidths=[half_w, col_gap, half_w])
        two_col.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(1,0),(1,-1),0), ('RIGHTPADDING',(1,0),(1,-1),0)]))
        story.append(two_col)
    else:
        summary_rows = [[
            Paragraph(f"Trip Period<br/><b>{trip_period}</b>", label_style),
            Paragraph(f"Total Trips<br/><b>{len(line_items)}</b>", label_style),
            Paragraph(f"Total Vehicles<br/><b>{total_vehicles}</b>", label_style),
            Paragraph(f"Total Weight<br/><b>{total_weight:.3f} MT</b>", label_style),
        ]]
        summary_body = Table(summary_rows, colWidths=[CONTENT_W/4]*4)
        summary_body.setStyle(TableStyle([('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        summary_box = section_box('TRIP SUMMARY', [[summary_body]], [CONTENT_W])
        story.extend(make_bill_box(CONTENT_W))
        story.append(Spacer(1, 8))
        story.extend(summary_box)
    story.append(Spacer(1, 12))

    # ---------- Freight Details (single) / Trips table (multi) ----------
    if is_single:
        li = line_items[0]
        t = li['trip']
        # Owner invoices ("vehicle_owner") are billed against the vehicle owner's own Charge Type
        # (owner_rate_type/owner_fixed_amount), which is tracked independently of the party's —
        # everything else (party invoices, tax invoices) uses the party's rate_type as before.
        if invoice_type == 'vehicle_owner':
            is_fixed = (t['owner_rate_type'] or 'PER_MT') == 'FIXED'
            rate_basis = 'Per Trip' if is_fixed else f"{t['quantity']} MT"
            rate_val = t['owner_fixed_amount'] if is_fixed else t['owner_rate']
        else:
            is_fixed = t['rate_type'] == 'FIXED'
            rate_basis = 'Per Trip' if is_fixed else f"{t['quantity']} MT"
            rate_val = t['fixed_rate_amount'] if is_fixed else t['rate']
        freight_desc = 'Freight Charges' + (' (Fixed)' if is_fixed else '')
        freight_rows = [['#', 'DESCRIPTION', 'RATE (Rs.)', 'QTY / BASIS', 'AMOUNT (Rs.)'],
                         ['1', freight_desc, f"{rate_val or 0:,.2f}", rate_basis, f"{li['freight']:,.2f}"]]
        n = 2
        for label, val in [('Loading Charges', li['loading']), ('Unloading Charges', li['unloading']),
                            ('Permit Charges', li['permit']), ('Toll Charges (Estimate)', li['toll'])]:
            if val:
                freight_rows.append([str(n), label, f"{val:,.2f}", 'Per Trip', f"{val:,.2f}"])
                n += 1
        ft = Table(freight_rows, colWidths=[0.3*inch, 2.5*inch, 1.3*inch, 1.5*inch, 1.3*inch], repeatRows=1)
        ft.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLACK), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8.3), ('ALIGN', (2,0), (4,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDDDDD')),
            ('TOPPADDING', (0,0), (-1,-1), 4.5), ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
        ]))
        story.append(ft)
        tot_row = Table([['TOTAL FREIGHT & CHARGES', f"Rs. {freight_and_charges:,.2f}"]], colWidths=[5.6*inch, 1.3*inch])
        tot_row.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                      ('FONTSIZE',(0,0),(-1,-1),9), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                      ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(tot_row)
        story.append(Spacer(1, 12))

        addl_rows = [['#', 'DESCRIPTION', 'BASIS', 'AMOUNT (Rs.)']]
        n = 1
        for label, val in [('Weighment Charges', li['weighment']), ('Driver Bata', li['driver_bata']),
                            ('GPS Charges', li['gps']), ('Other Charges', li['other']),
                            ('Detention Charges', li['detention']), ('Police Charges', li['police']),
                            ('SIM Tracking', li['sim_tracking']), ('Union Charges', li['union'])]:
            if val:
                addl_rows.append([str(n), label, 'Per Trip', f"{val:,.2f}"])
                n += 1
        for label, val in [('Brokerage', li['brokerage']), ('Builty Commission', li['builty_commission']),
                            ('Late Fees', li['late_fees']), ('Material Damage', li['material_damage']),
                            ('Shortage Amount', li['shortage_amount']), ('TDS (Trip)', li['trip_tds']),
                            ('Other Deductions', li['other_deductions'])]:
            if val:
                addl_rows.append([str(n), label, 'Deduction', f"- {val:,.2f}"])
                n += 1
        for it in extra_items:
            addl_rows.append([str(n), cell(it['desc']), 'Deduction' if it['item_type'] == 'deduction' else 'Charge',
                               f"- {it['amount']:,.2f}" if it['item_type'] == 'deduction' else f"{it['amount']:,.2f}"])
            n += 1
        if len(addl_rows) > 1:
            addl_left = Table(addl_rows, colWidths=[0.3*inch, 1.9*inch, 1*inch, COMBO_L - 3.2*inch])
            addl_left.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#EEEEEE')), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                ('FONTSIZE',(0,0),(-1,-1),8.3), ('ALIGN',(3,0),(3,-1),'RIGHT'), ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#DDDDDD')),
                ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
            ]))
            addl_box_head = Table([[Paragraph('<b>ADDITIONAL CHARGES</b>', section_head_style)]], colWidths=[COMBO_L])
            addl_box_head.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                                ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),8)]))
            addl_tot = Table([['TOTAL ADDITIONAL CHARGES', f"Rs. {additional_charges:,.2f}"]], colWidths=[COMBO_L - 1*inch, 1*inch])
            addl_tot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                           ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                           ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))

            sub_total_lines = [['Sub Total', f"Rs. {sub_total:,.2f}"]]
            if invoice_type == 'tax':
                sub_total_lines.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
                sub_total_lines.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
            if tds_rate:
                sub_total_lines.append([f'TDS ({tds_rate:g}%)', f"- Rs. {tds_amount:,.2f}"])
            if invoice_type == 'tax' or tds_rate:
                sub_total_lines.append(['Round Off', f"Rs. {round_off:,.2f}"])
            sub_total_lines.append(['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"])
            # One bordered box with every row (Sub Total through Grand Total) instead of a plain
            # floating list plus a separately-boxed final row — same "one continuous box, distinct
            # rows" pattern as TOTAL FREIGHT & CHARGES above it.
            right_stack = Table(sub_total_lines, colWidths=[COMBO_R - 1.1*inch, 1.1*inch])
            right_stack.setStyle(TableStyle([
                ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'),
                ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4), ('LEFTPADDING',(0,0),(-1,-1),8),
                ('BOX',(0,0),(-1,-1),0.5,LINE), ('LINEBELOW',(0,0),(-1,-2),0.3,colors.HexColor('#DDDDDD')),
                ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'), ('FONTSIZE',(0,-1),(-1,-1),10.5), ('BACKGROUND',(0,-1),(-1,-1),LIGHTBG),
            ]))

            left_col = [addl_box_head, addl_left, addl_tot]
            combo = Table([[left_col, '', right_stack]], colWidths=[COMBO_L, COMBO_GAP, COMBO_R])
            combo.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(1,0),(1,-1),0), ('RIGHTPADDING',(1,0),(1,-1),0)]))
            story.append(combo)
        else:
            sub_total_lines = [['Sub Total', f"Rs. {sub_total:,.2f}"]]
            if invoice_type == 'tax':
                sub_total_lines.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
                sub_total_lines.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
            if tds_rate:
                sub_total_lines.append([f'TDS ({tds_rate:g}%)', f"- Rs. {tds_amount:,.2f}"])
            if invoice_type == 'tax' or tds_rate:
                sub_total_lines.append(['Round Off', f"Rs. {round_off:,.2f}"])
            sub_total_lines.append(['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"])
            tt = Table(sub_total_lines, colWidths=[5.6*inch, 1.3*inch])
            tt.setStyle(TableStyle([('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'), ('FONTSIZE',(0,-1),(-1,-1),10.5),
                                     ('BACKGROUND',(0,-1),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                     ('LINEBELOW',(0,0),(-1,-2),0.3,colors.HexColor('#DDDDDD')),
                                     ('ALIGN',(1,0),(1,-1),'RIGHT'), ('FONTSIZE',(0,0),(-2,-2),8.5),
                                     ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
                                     ('LEFTPADDING',(0,0),(-1,-1),8)]))
            story.append(tt)
    else:
        item_rows = [['#', 'LR NUMBER', 'TRIP DATE', 'VEHICLE NO.', 'FROM', 'TO', 'MATERIAL', 'WEIGHT (MT)', 'FREIGHT (Rs.)']]
        for i, li in enumerate(line_items, 1):
            t = li['trip']
            item_rows.append([str(i), cell(t['lr_number']), t['date'] or '', cell(t['vehicle_no']),
                               cell(t['from_loc']), cell(t['to_loc']), cell(t['material']),
                               f"{t['quantity'] or 0:.3f}", f"{li['freight']:,.2f}"])
        item_rows.append(['', '', '', '', '', '', 'TOTAL', f"{total_weight:.3f}", f"{total_freight:,.2f}"])
        item_table = Table(item_rows, colWidths=[0.25*inch, 0.75*inch, 0.65*inch, 0.75*inch, 1.1*inch, 1.1*inch, 0.8*inch, 0.65*inch, 0.85*inch], repeatRows=1)
        item_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLACK), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'), ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#EEEEEE')),
            ('FONTSIZE', (0,0), (-1,-1), 7.3), ('ALIGN', (7,0), (8,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#DDDDDD')),
            ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(item_table)
        story.append(Spacer(1, 12))

        multi_additional_total = sub_total - total_freight
        addl_rows = [['DESCRIPTION', 'BASIS', 'AMOUNT (Rs.)']]
        for label, val in [('Loading Charges', total_loading), ('Unloading Charges', total_unloading),
                            ('Permit Charges', total_permit), ('Toll Charges (Actual)', total_toll),
                            ('Weighment Charges', total_weighment), ('Driver Bata', total_driver_bata),
                            ('GPS Charges', total_gps), ('Other Charges', total_other),
                            ('Detention Charges', total_detention), ('Police Charges', total_police),
                            ('SIM Tracking', total_sim), ('Union Charges', total_union)]:
            if val:
                addl_rows.append([label, 'Per Trip', f"{val:,.2f}"])
        for label, val in [('Brokerage', total_brokerage), ('Builty Commission', total_builty_commission),
                            ('Late Fees', total_late_fees), ('Material Damage', total_material_damage),
                            ('Shortage Amount', total_shortage), ('TDS (Trip)', total_trip_tds),
                            ('Other Deductions', total_other_deductions)]:
            if val:
                addl_rows.append([label, 'Deduction', f"- {val:,.2f}"])
        for it in extra_items:
            addl_rows.append([cell(it['desc']), 'Deduction' if it['item_type'] == 'deduction' else 'Charge',
                               f"- {it['amount']:,.2f}" if it['item_type'] == 'deduction' else f"{it['amount']:,.2f}"])
        if len(addl_rows) > 1:
            addl_left = Table(addl_rows, colWidths=[2.2*inch, 1*inch, COMBO_L - 3.2*inch])
            addl_left.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#EEEEEE')), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                ('FONTSIZE',(0,0),(-1,-1),8), ('ALIGN',(2,0),(2,-1),'RIGHT'), ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#DDDDDD')),
                ('TOPPADDING',(0,0),(-1,-1),3.5), ('BOTTOMPADDING',(0,0),(-1,-1),3.5),
            ]))
            addl_head = Table([[Paragraph('<b>ADDITIONAL CHARGES (SUMMARY)</b>', section_head_style)]], colWidths=[COMBO_L])
            addl_head.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                            ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),8)]))
            addl_tot = Table([['TOTAL ADDITIONAL CHARGES', f"{multi_additional_total:,.2f}"]], colWidths=[COMBO_L - 1*inch, 1*inch])
            addl_tot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                           ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                           ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            left_col = [addl_head, addl_left, addl_tot]
        else:
            left_col = ''

        r_rows = [['Total Freight', f"Rs. {total_freight:,.2f}"], ['Total Additional Charges', f"Rs. {multi_additional_total:,.2f}"],
                   ['Sub Total', f"Rs. {sub_total:,.2f}"]]
        if invoice_type == 'tax':
            r_rows.append([f'CGST ({cgst_rate:g}%)', f"Rs. {cgst_amount:,.2f}"])
            r_rows.append([f'SGST ({sgst_rate:g}%)', f"Rs. {sgst_amount:,.2f}"])
        if tds_rate:
            r_rows.append([f'TDS ({tds_rate:g}%)', f"- Rs. {tds_amount:,.2f}"])
        if invoice_type == 'tax' or tds_rate:
            r_rows.append(['Round Off', f"Rs. {round_off:,.2f}"])
        r_rows.append(['GRAND TOTAL (Rs.)', f"{grand_total:,.2f}"])
        # Same single-box, every-row-visible pattern as the single-trip layout above.
        right_stack = Table(r_rows, colWidths=[COMBO_R - 1.1*inch, 1.1*inch])
        right_stack.setStyle(TableStyle([
            ('FONTSIZE',(0,0),(-1,-1),8.3), ('ALIGN',(1,0),(1,-1),'RIGHT'),
            ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4), ('LEFTPADDING',(0,0),(-1,-1),8),
            ('BOX',(0,0),(-1,-1),0.5,LINE), ('LINEBELOW',(0,0),(-1,-2),0.3,colors.HexColor('#DDDDDD')),
            ('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#E8F5E9')), ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),
            ('FONTSIZE',(0,-1),(-1,-1),10.5), ('TEXTCOLOR',(1,-1),(1,-1),colors.HexColor('#2E7D32')),
        ]))
        if left_col and len(addl_rows) > 14:
            # Many extra charge lines (e.g. an invoice for 200 trips that each carry a detention line): the
            # side-by-side layout is ONE table row, and a single row cannot be split across pages — ReportLab
            # raised LayoutError and the invoice failed with a 500. Stack the pieces full-width instead so the
            # charges table flows (and repeats its header) over as many pages as it needs.
            wide = Table(addl_rows, colWidths=[CONTENT_W - 3.2*inch, 1.2*inch, 2.0*inch], repeatRows=1)
            wide.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#EEEEEE')), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                ('FONTSIZE',(0,0),(-1,-1),8), ('ALIGN',(2,0),(2,-1),'RIGHT'), ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#DDDDDD')),
                ('TOPPADDING',(0,0),(-1,-1),3.5), ('BOTTOMPADDING',(0,0),(-1,-1),3.5),
            ]))
            wide_head = Table([[Paragraph('<b>ADDITIONAL CHARGES (SUMMARY)</b>', section_head_style)]], colWidths=[CONTENT_W])
            wide_head.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                            ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),8)]))
            wide_tot = Table([['TOTAL ADDITIONAL CHARGES', f"{multi_additional_total:,.2f}"]], colWidths=[CONTENT_W - 2.0*inch, 2.0*inch])
            wide_tot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHTBG), ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                           ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'), ('BOX',(0,0),(-1,-1),0.5,LINE),
                                           ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            story.extend([wide_head, wide, wide_tot, Spacer(1, 10)])
            combo = Table([['', right_stack]], colWidths=[CONTENT_W - COMBO_R, COMBO_R])
            combo.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(0,0),0), ('RIGHTPADDING',(0,0),(0,0),0)]))
            story.append(combo)
        else:
            combo = Table([[left_col, '', right_stack]], colWidths=[COMBO_L, COMBO_GAP, COMBO_R])
            combo.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(1,0),(1,-1),0), ('RIGHTPADDING',(1,0),(1,-1),0)]))
            story.append(combo)

    if invoice_type == 'vehicle_owner' and total_already_given:
        deduction_rows = [['Gross Bill Amount', f"Rs. {grand_total:,.2f}"]]
        if total_fuel_given:
            deduction_rows.append(['Fuel Advance', f"Rs. {total_fuel_given:,.2f}"])
        if total_driver_adv_given:
            deduction_rows.append(['Driver Advance', f"Rs. {total_driver_adv_given:,.2f}"])
        if total_paid_to_owner:
            deduction_rows.append(['Advance Paid', f"Rs. {total_paid_to_owner:,.2f}"])
        deduction_rows.append(['NET PAYABLE (Rs.)', f"{net_payable:,.2f}"])
        # Same bordered-bar treatment as "TOTAL FREIGHT & CHARGES" above (BOX + row rules +
        # highlighted final row) instead of plain floating text, so this reads as one continuous
        # pattern with the totals above it rather than a visually distinct, "distorted" block.
        deduction_table = Table(deduction_rows, colWidths=[5.6*inch, 1.3*inch])
        deduction_table.setStyle(TableStyle([
            ('FONTSIZE',(0,0),(-1,-1),8.5), ('ALIGN',(1,0),(1,-1),'RIGHT'),
            ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
            ('LEFTPADDING',(0,0),(-1,-1),8), ('BOX',(0,0),(-1,-1),0.5,LINE),
            ('LINEBELOW',(0,0),(-1,-2),0.3,colors.HexColor('#DDDDDD')),
            ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'), ('FONTSIZE',(0,-1),(-1,-1),10.5),
            ('BACKGROUND',(0,-1),(-1,-1),LIGHTBG),
        ]))
        story.append(Spacer(1, 8))
        story.append(deduction_table)

    story.append(Spacer(1, 8))
    # Constrained to CONTENT_W (not the full page frame, which is wider) so this line's left/right
    # edges match every box above it instead of running further right and reading "out of line".
    words_table = Table([[Paragraph(f"<b>Amount In Words:</b> Rupees {number_to_words_inr(net_payable)} Only", label_style)]],
                         colWidths=[CONTENT_W])
    words_table.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0),
                                      ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    story.append(words_table)
    story.append(Spacer(1, 16))

    # ---------- Footer: Payment Details + Terms (left) / Signature (right) ----------
    # A fixed 2-column layout — left content always starts flush left, signature always sits
    # flush right — instead of the previous 3-column table that padded a blank cell onto the
    # front whenever Payment Details didn't apply, which pushed Terms & Conditions into the
    # middle column and left the true left edge empty.
    left_blocks = []
    if invoice_type == 'tax' and s['bank_name']:
        bank_lines = ["<b>PAYMENT DETAILS</b>", f"Bank Name : {esc(s['bank_name'])}", f"A/C Number : {esc(s['account_number'])}",
                      f"IFSC Code : {esc(s['ifsc_code'])}", f"Branch : {esc(s['branch'])}"]
        left_blocks.append(Paragraph('<br/>'.join(bank_lines), label_style))
        left_blocks.append(Spacer(1, 10))
    terms_lines = ["<b>TERMS &amp; CONDITIONS</b>", "1. Payment should be made within due date.",
                   "2. Interest @ 18% p.a. will be charged on overdue.", "3. All disputes subject to Rourkela Jurisdiction."]
    left_blocks.append(Paragraph('<br/>'.join(terms_lines), label_style))
    signature_block = Paragraph(f"<b>FOR {esc(s['company_name'])}</b><br/><br/><br/>Authorised Signatory", label_style)
    footer_table = Table([[left_blocks, signature_block]], colWidths=[4.6*inch, 2.3*inch])
    footer_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'), ('LINEABOVE', (0,0), (-1,0), 0.7, LINE),
                                       ('TOPPADDING', (0,0), (-1,-1), 8), ('LEFTPADDING', (0,0), (0,0), 0)]))
    story.append(footer_table)

    if invoice_type == 'tax' and s['rcm_clause']:
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"<b>RCM Note:</b> {esc(s['rcm_clause'])}", label_style))
    if remarks:
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"<b>Remarks:</b> {esc(remarks)}", label_style))

    story.append(Spacer(1, 14))
    story.append(Paragraph("Thank you for your business!", ParagraphStyle('TY', parent=styles['Normal'], fontSize=9, alignment=1, fontName='Helvetica-Oblique')))

    doc.build(story)
    buf.seek(0)
    return buf

def _filter_trip_items(rows, excluded_charge_keys):
    """Each selected trip's own "Others" line items, minus whichever ones were unchecked in
    Invoice Center's Trip Charges & Costs popup — rows need id+trip_id+description+amount+item_type
    (as returned by a SELECT against invoice_items). Kept as named entries (not folded into one
    number) so each one still prints on the invoice by its own description."""
    out = []
    for it in rows:
        if f"{it['trip_id']}:item:{it['id']}" in excluded_charge_keys:
            continue
        out.append({'desc': it['description'] or 'Other', 'amount': it['amount'] or 0, 'item_type': it['item_type']})
    return out

def _save_invoice_charge_exclusions(conn, batch_id, excluded_charge_keys, included_charge_keys, user_id, now):
    """Persist Invoice Center's per-trip charge toggle choices against a just-created invoice
    batch, so View/Regenerate PDF later reproduces the same invoice instead of reverting to
    defaults. Both params hold raw "<trip_id>:<charge_key>" strings — opt-in keys (see
    _OPT_IN_CHARGE_KEYS) are stored with a '+' prefix on charge_key so _load_invoice_charge_exclusions
    can tell the two apart again without a second table."""
    def _save(keys, prefix):
        for key in keys:
            trip_id_str, _, charge_key = key.partition(':')
            if not trip_id_str.isdigit() or not charge_key:
                continue
            conn.execute("""INSERT OR IGNORE INTO invoice_batch_charge_exclusions
                            (invoice_batch_id, trip_id, charge_key, company_id, created_by, created_at)
                            VALUES (?,?,?,?,?,?)""",
                         (batch_id, int(trip_id_str), prefix + charge_key, 1, user_id, now))
    _save(excluded_charge_keys, '')
    _save(included_charge_keys, '+')

def _load_invoice_charge_exclusions(conn, batch_id):
    """The saved counterpart of _save_invoice_charge_exclusions — returns (excluded, included) as
    two "<trip_id>:<charge_key>" string sets, reloaded for View/Regenerate PDF."""
    excluded, included = set(), set()
    for r in conn.execute("SELECT trip_id, charge_key FROM invoice_batch_charge_exclusions WHERE invoice_batch_id=?",
                          (batch_id,)).fetchall():
        if r['charge_key'].startswith('+'):
            included.add(f"{r['trip_id']}:{r['charge_key'][1:]}")
        else:
            excluded.add(f"{r['trip_id']}:{r['charge_key']}")
    return excluded, included

@app.route('/invoice-center/generate', methods=['POST'])
def invoice_center_generate():
    from flask import send_file
    import datetime

    f = request.form
    mode = f.get('mode') or 'generate'  # 'generate' | 'draft' | 'preview'
    trip_ids = f.getlist('trip_ids')
    invoice_type = f.get('invoice_type')
    party_id = f.get('party_id') or None
    vendor_id = f.get('vendor_id') or None
    invoice_date = f.get('invoice_date') or datetime.datetime.now().strftime('%Y-%m-%d')
    due_date = f.get('due_date') or ''
    payment_terms = f.get('payment_terms') or ''
    place_of_supply = f.get('place_of_supply') or ''
    remarks = f.get('remarks') or ''
    payment_status = f.get('payment_status') or 'PENDING'
    # Saving an invoice (generate / draft) requires that the user opened the LR link of EVERY selected trip to
    # review its charges (Invoice Center posts reviewed_trip_ids). Preview is exempt.
    if mode in ('generate', 'draft'):
        reviewed = set(f.getlist('reviewed_trip_ids'))
        unreviewed = [tid for tid in trip_ids if tid not in reviewed]
        if unreviewed:
            return redirect(url_for('invoice_center', invoice_type=invoice_type or 'party', party_id=party_id or '', vendor_id=vendor_id or '',
                                    inv_error=f"{'Generate' if mode == 'generate' else 'Save as Draft'} cannot be done until the charges of every selected trip are reviewed (click each LR number). "
                                              f"{len(unreviewed)} trip(s) not reviewed."))
    num_error = _invoice_numbers_error(f)
    if num_error:
        return redirect(url_for('invoice_center', invoice_type=invoice_type or 'party', inv_error=num_error))
    extra_loading = _parse_money(f.get('loading_charges'), 'Loading charges')[0]
    extra_other = _parse_money(f.get('other_charges'), 'Other charges')[0]
    tds_rate = _parse_money(f.get('tds_rate'), 'TDS %')[0]
    gst_rate_input = f.get('gst_rate')
    if gst_rate_input:
        gst_rate_input = str(_parse_money(gst_rate_input, 'GST %')[0])

    conn = get_db()
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    # Fields that were already on every invoice (the 8 core charges, Others items, Fuel/Driver
    # Advance deduction toggles) are checked by default — excluded_charge_keys only ever holds what
    # was deliberately unchecked. Fields that have never been on any invoice before (Detention,
    # Police, SIM Tracking, Union, and all 7 deduction fields) are unchecked by default —
    # included_charge_keys only ever holds what was deliberately opted in. Both empty means a plain
    # form submit still behaves exactly as every invoice generated before this feature existed.
    excluded_charge_keys = set(f.getlist('excluded_charge_keys'))
    included_charge_keys = set(f.getlist('included_charge_keys'))
    extra_items = []
    if trip_ids:
        item_rows = conn.execute(f"""SELECT id, trip_id, description, amount, item_type
                                     FROM invoice_items WHERE trip_id IN ({placeholders})""", trip_ids).fetchall()
        extra_items = _filter_trip_items(item_rows, excluded_charge_keys)

    # Ad-hoc charges/deductions typed directly into Invoice Center at generation time — kept as
    # named entries too (once the batch exists, saved as real invoice_batch_items so they're still
    # visible/editable from Generated Invoices afterwards, same as items added there).
    extra_descs = f.getlist('extra_item_desc')
    extra_amounts = f.getlist('extra_item_amount')
    extra_types = f.getlist('extra_item_type')
    new_items = []
    for i, desc in enumerate(extra_descs):
        desc = (desc or '').strip()
        if not desc:
            continue
        amt = (_parse_money(extra_amounts[i], 'Amount')[0] or 0) if i < len(extra_amounts) and extra_amounts[i] else 0
        item_type = extra_types[i] if i < len(extra_types) and extra_types[i] in ('charge', 'deduction') else 'charge'
        new_items.append((desc, amt, item_type))
        extra_items.append({'desc': desc, 'amount': amt, 'item_type': item_type})

    entity = None
    if invoice_type == 'vehicle_owner' and vendor_id:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    elif party_id:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()

    s = _get_invoice_settings(conn, session.get('company_id', 1))
    toll_map = _toll_by_trip(conn, [t['id'] for t in trips])

    if invoice_type == 'tax':
        total_gst_rate = float(gst_rate_input) if gst_rate_input else (float(s['cgst_rate'] or 0) + float(s['sgst_rate'] or 0))
        cgst_rate = sgst_rate = round(total_gst_rate / 2, 4)
    else:
        cgst_rate = sgst_rate = 0

    next_num = int(s['next_invoice_number'] or 1)
    invoice_number = f"{s['invoice_prefix'] or 'ATS/INV'}/{datetime.datetime.now().year}/{next_num:04d}"

    if mode == 'preview':
        # Preview never touches the DB or the invoice-number counter — it's just a look at the PDF.
        buf = _build_invoice_pdf(trips, invoice_type, entity, s, invoice_number, invoice_date, due_date, payment_status, remarks,
                                  cgst_rate=cgst_rate, sgst_rate=sgst_rate, extra_loading=extra_loading, extra_other=extra_other,
                                  tds_rate=tds_rate, extra_items=extra_items, toll_map=toll_map,
                                  excluded_charge_keys=excluded_charge_keys, included_charge_keys=included_charge_keys)
        conn.close()
        return send_file(buf, download_name=f'preview-{invoice_number.replace("/","-")}.pdf', mimetype='application/pdf')

    audit_now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    status = 'draft' if mode == 'draft' else 'generated'
    cur = conn.execute("""INSERT INTO invoice_batches (invoice_number, invoice_type, party_id, vendor_id, invoice_date, due_date,
                          payment_terms, place_of_supply, remarks, gst_rate, tds_rate, loading_charges, other_charges, status, payment_status, created_at,
                          created_by)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (invoice_number, invoice_type, party_id, vendor_id, invoice_date, due_date, payment_terms,
                  place_of_supply, remarks, cgst_rate+sgst_rate, tds_rate, extra_loading, extra_other,
                  status, payment_status, audit_now, session.get('user_id')))
    batch_id = cur.lastrowid
    for tid in trip_ids:
        conn.execute("INSERT INTO invoice_batch_trips (invoice_batch_id, trip_id, created_by, created_at) VALUES (?,?,?,?)",
                     (batch_id, tid, session.get('user_id'), audit_now))
    for desc, amt, item_type in new_items:
        conn.execute("""INSERT INTO invoice_batch_items (invoice_batch_id, description, amount, item_type, created_by, created_at)
                        VALUES (?,?,?,?,?,?)""",
                     (batch_id, desc, amt, item_type, session.get('user_id'), audit_now))
    # Persist exactly what was unchecked/opted-in in the popup against this batch, so
    # View/Regenerate PDF reproduces the same invoice later instead of reverting to defaults.
    _save_invoice_charge_exclusions(conn, batch_id, excluded_charge_keys, included_charge_keys, session.get('user_id'), audit_now)
    conn.execute("""INSERT OR REPLACE INTO settings (key, value, company_id, created_by, created_at, updated_by, updated_at)
                    VALUES ('next_invoice_number', ?, ?, ?, ?, ?, ?)""",
                 (str(next_num + 1), session.get('company_id', 1), session.get('user_id'), audit_now,
                  session.get('user_id'), audit_now))
    conn.commit()
    conn.close()

    if mode == 'draft':
        return redirect(url_for('invoice_batches_list'))

    buf = _build_invoice_pdf(trips, invoice_type, entity, s, invoice_number, invoice_date, due_date, payment_status, remarks,
                              cgst_rate=cgst_rate, sgst_rate=sgst_rate, extra_loading=extra_loading, extra_other=extra_other,
                              tds_rate=tds_rate, extra_items=extra_items, toll_map=toll_map,
                              excluded_charge_keys=excluded_charge_keys, included_charge_keys=included_charge_keys)
    return send_file(buf, as_attachment=True, download_name=f'{invoice_number.replace("/","-")}.pdf', mimetype='application/pdf')

@app.route('/invoices/generated')
def invoice_batches_list():
    conn = get_db()
    search_f = request.args.get('search', '')
    type_f = request.args.get('invoice_type', '')
    query = """SELECT ib.id, ib.invoice_number, ib.invoice_type, ib.invoice_date, ib.due_date,
               ib.payment_status, ib.status, ib.created_at, p.name as party_name, v.name as vendor_name,
               (SELECT COUNT(*) FROM invoice_batch_trips ibt WHERE ibt.invoice_batch_id=ib.id) as trip_count
               FROM invoice_batches ib
               LEFT JOIN parties p ON ib.party_id=p.id
               LEFT JOIN vendors v ON ib.vendor_id=v.id
               WHERE 1=1"""
    params = []
    if type_f:
        query += " AND ib.invoice_type=?"; params.append(type_f)
    if search_f:
        query += " AND (p.name LIKE ? OR v.name LIKE ? OR ib.invoice_number LIKE ?)"
        params += [f"%{search_f}%", f"%{search_f}%", f"%{search_f}%"]
    query += " ORDER BY ib.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('invoice_batches_list.html', rows=rows, f_search=search_f, f_type=type_f, active='invoices')

@app.route('/invoices/generated/<int:batch_id>/edit', methods=['GET', 'POST'])
def invoice_batch_edit(batch_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        num_error = _invoice_numbers_error(f)
        if num_error:
            conn.close()
            return redirect(url_for('invoice_batch_edit', batch_id=batch_id, inv_error=num_error))
        conn.execute("""UPDATE invoice_batches SET invoice_date=?, due_date=?, payment_terms=?, place_of_supply=?,
                        remarks=?, payment_status=?, gst_rate=?, tds_rate=?, loading_charges=?, other_charges=?,
                        updated_by=?, updated_at=?
                        WHERE id=?""",
                     (f.get('invoice_date'), f.get('due_date'), f.get('payment_terms'), f.get('place_of_supply'),
                      f.get('remarks'), f.get('payment_status'), _parse_money(f.get('gst_rate'), 'GST %')[0], _parse_money(f.get('tds_rate'), 'TDS %')[0],
                      _parse_money(f.get('loading_charges'), 'Loading charges')[0], _parse_money(f.get('other_charges'), 'Other charges')[0],
                      session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), batch_id))
        _save_invoice_batch_items(conn, batch_id, f)
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_batches_list'))
    batch = conn.execute("""SELECT ib.*, p.name as party_name, v.name as vendor_name FROM invoice_batches ib
                            LEFT JOIN parties p ON ib.party_id=p.id LEFT JOIN vendors v ON ib.vendor_id=v.id
                            WHERE ib.id=?""", (batch_id,)).fetchone()
    batch_item_rows = conn.execute("""SELECT ibi.*, ve.name as item_vendor_name FROM invoice_batch_items ibi
                                      LEFT JOIN vendors ve ON ibi.vendor_id=ve.id
                                      WHERE ibi.invoice_batch_id=? ORDER BY ibi.id""", (batch_id,)).fetchall()
    # Plain dicts, not sqlite3.Row — the template seeds the client-side editable items table from
    # this via |tojson, and Jinja's tojson can't serialize a Row directly.
    batch_items = [{'description': r['description'], 'item_type': r['item_type'], 'amount': r['amount'],
                     'vendor_name': r['item_vendor_name'] or ''} for r in batch_item_rows]
    vendors = conn.execute("SELECT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
    conn.close()
    return render_template('invoice_batch_edit.html', b=batch, batch_items=batch_items, vendors=vendors, active='invoices')

@app.route('/invoices/generated/<int:batch_id>/items/add', methods=['POST'])
def invoice_batch_item_add(batch_id):
    conn = get_db()
    f = request.form
    item_amount, item_err = _parse_money(f.get('amount'), 'Amount')
    if item_err:
        conn.close()
        return redirect(url_for('invoice_batch_edit', batch_id=batch_id, inv_error=item_err))
    vendor_id = get_or_create_vendor(conn, f.get('vendor_name')) if f.get('vendor_name') else None
    conn.execute("""INSERT INTO invoice_batch_items (invoice_batch_id, description, amount, item_type, vendor_id, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (batch_id, f.get('description'), item_amount, f.get('item_type') or 'charge',
                  vendor_id, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_batch_edit', batch_id=batch_id))

@app.route('/invoices/generated/items/<int:item_id>/delete', methods=['POST'])
def invoice_batch_item_delete(item_id):
    conn = get_db()
    row = conn.execute("SELECT invoice_batch_id FROM invoice_batch_items WHERE id=?", (item_id,)).fetchone()
    conn.execute("DELETE FROM invoice_batch_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_batch_edit', batch_id=row['invoice_batch_id'])) if row else redirect(url_for('invoice_batches_list'))

@app.route('/invoices/generated/<int:batch_id>/delete', methods=['POST'])
def invoice_batch_delete(batch_id):
    conn = get_db()
    conn.execute("DELETE FROM invoice_batch_trips WHERE invoice_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM invoice_batch_items WHERE invoice_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM invoice_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_batches_list'))

@app.route('/invoices/generated/<int:batch_id>/pdf')
def invoice_batch_pdf(batch_id):
    from flask import send_file
    conn = get_db()
    batch = conn.execute("SELECT * FROM invoice_batches WHERE id=?", (batch_id,)).fetchone()
    trip_id_rows = conn.execute("SELECT trip_id FROM invoice_batch_trips WHERE invoice_batch_id=?", (batch_id,)).fetchall()
    trip_ids = [str(r['trip_id']) for r in trip_id_rows]
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if batch['invoice_type'] == 'vehicle_owner' and batch['vendor_id']:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (batch['vendor_id'],)).fetchone()
    elif batch['party_id']:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (batch['party_id'],)).fetchone()
    s = _get_invoice_settings(conn, session.get('company_id', 1))

    # Batch-level custom items (added from the Edit screen) plus each trip's own "Others" items —
    # kept as named entries, same as at generate time, so a regenerated PDF still prints each one
    # by its own description (e.g. "Detention") instead of folding them into one anonymous total.
    # Reload the same per-trip charge exclusions saved when this invoice was first generated, so
    # a regenerated PDF is byte-for-byte the same choice, not "everything included" again.
    excluded_charge_keys, included_charge_keys = _load_invoice_charge_exclusions(conn, batch_id)
    extra_items = []
    for it in conn.execute("SELECT description, amount, item_type FROM invoice_batch_items WHERE invoice_batch_id=?", (batch_id,)).fetchall():
        extra_items.append({'desc': it['description'] or 'Other', 'amount': it['amount'] or 0, 'item_type': it['item_type']})
    if trip_ids:
        item_rows = conn.execute(f"SELECT id, trip_id, description, amount, item_type FROM invoice_items WHERE trip_id IN ({placeholders})", trip_ids).fetchall()
        extra_items += _filter_trip_items(item_rows, excluded_charge_keys)
    toll_map = _toll_by_trip(conn, [t['id'] for t in trips])
    conn.close()

    cgst_rate = sgst_rate = round((batch['gst_rate'] or 0) / 2, 4) if batch['invoice_type'] == 'tax' else 0
    buf = _build_invoice_pdf(trips, batch['invoice_type'], entity, s, batch['invoice_number'], batch['invoice_date'],
                              batch['due_date'], batch['payment_status'], batch['remarks'],
                              cgst_rate=cgst_rate, sgst_rate=sgst_rate,
                              extra_loading=batch['loading_charges'] or 0, extra_other=batch['other_charges'] or 0,
                              tds_rate=batch['tds_rate'] or 0, extra_items=extra_items, toll_map=toll_map,
                              excluded_charge_keys=excluded_charge_keys, included_charge_keys=included_charge_keys)
    return send_file(buf, as_attachment=True, download_name=f'{batch["invoice_number"].replace("/","-")}.pdf', mimetype='application/pdf')

@app.route('/invoices/generated/<int:batch_id>/pdf/preview')
def invoice_batch_pdf_preview(batch_id):
    """Same PDF as invoice_batch_pdf above, byte-for-byte — just returned inline (no
    Content-Disposition: attachment) so it can sit in an <iframe> on the Edit Invoice page
    instead of triggering a download. Kept as its own route rather than adding a query-param
    branch to invoice_batch_pdf so that route's existing behavior is never touched."""
    from flask import send_file
    conn = get_db()
    batch = conn.execute("SELECT * FROM invoice_batches WHERE id=?", (batch_id,)).fetchone()
    trip_id_rows = conn.execute("SELECT trip_id FROM invoice_batch_trips WHERE invoice_batch_id=?", (batch_id,)).fetchall()
    trip_ids = [str(r['trip_id']) for r in trip_id_rows]
    placeholders = ','.join('?' * len(trip_ids))
    trips = conn.execute(f"""SELECT t.*, v.vehicle_no FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.id IN ({placeholders})""", trip_ids).fetchall() if trip_ids else []

    entity = None
    if batch['invoice_type'] == 'vehicle_owner' and batch['vendor_id']:
        entity = conn.execute("SELECT * FROM vendors WHERE id=?", (batch['vendor_id'],)).fetchone()
    elif batch['party_id']:
        entity = conn.execute("SELECT * FROM parties WHERE id=?", (batch['party_id'],)).fetchone()
    s = _get_invoice_settings(conn, session.get('company_id', 1))

    # Reload the same per-trip charge exclusions saved when this invoice was first generated, so
    # a regenerated PDF is byte-for-byte the same choice, not "everything included" again.
    excluded_charge_keys, included_charge_keys = _load_invoice_charge_exclusions(conn, batch_id)
    extra_items = []
    for it in conn.execute("SELECT description, amount, item_type FROM invoice_batch_items WHERE invoice_batch_id=?", (batch_id,)).fetchall():
        extra_items.append({'desc': it['description'] or 'Other', 'amount': it['amount'] or 0, 'item_type': it['item_type']})
    if trip_ids:
        item_rows = conn.execute(f"SELECT id, trip_id, description, amount, item_type FROM invoice_items WHERE trip_id IN ({placeholders})", trip_ids).fetchall()
        extra_items += _filter_trip_items(item_rows, excluded_charge_keys)
    toll_map = _toll_by_trip(conn, [t['id'] for t in trips])
    conn.close()

    cgst_rate = sgst_rate = round((batch['gst_rate'] or 0) / 2, 4) if batch['invoice_type'] == 'tax' else 0
    buf = _build_invoice_pdf(trips, batch['invoice_type'], entity, s, batch['invoice_number'], batch['invoice_date'],
                              batch['due_date'], batch['payment_status'], batch['remarks'],
                              cgst_rate=cgst_rate, sgst_rate=sgst_rate,
                              extra_loading=batch['loading_charges'] or 0, extra_other=batch['other_charges'] or 0,
                              tds_rate=batch['tds_rate'] or 0, extra_items=extra_items, toll_map=toll_map,
                              excluded_charge_keys=excluded_charge_keys, included_charge_keys=included_charge_keys)
    return send_file(buf, mimetype='application/pdf')

@app.route('/invoices')
def invoices_search():
    lr_f = request.args.get('lr_number', '')
    conn = get_db()
    results = []
    if lr_f:
        results = conn.execute("""SELECT t.id, t.date, t.lr_number, v.vehicle_no, p.name as party_name,
                                  t.from_loc, t.to_loc, t.billed_amount
                                  FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                  LEFT JOIN parties p ON t.party_id=p.id
                                  WHERE t.lr_number LIKE ? ORDER BY t.date DESC""", (f"%{lr_f}%",)).fetchall()
    conn.close()
    return render_template('invoices_search.html', results=results, f_lr=lr_f, active='invoices')

@app.route('/invoices/<int:trip_id>')
def invoice_preview(trip_id):
    conn = get_db()
    t = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name
                        FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                        LEFT JOIN parties p ON t.party_id=p.id WHERE t.id=?""", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    s = _get_invoice_settings(conn, session.get('company_id', 1))
    conn.close()
    charges = [
        ('Driver Payment', t['driver_payment']), ('Detention Charges', t['detention_charges']),
        ('GPS Cost', t['gps_cost']), ('Loading Charge', t['loading_charge']),
        ('Unloading Charge', t['unloading_charge']), ('Police Charges', t['police_charges']),
        ('SIM Tracking', t['sim_tracking']), ('Union Charges', t['union_charges']),
        ('Weight Charges', t['weight_charges']), ('Other Charges', t['other_charges']),
    ]
    charges = [(l, v) for l, v in charges if v]
    charges += [(item['description'], item['amount']) for item in extra_items if item['item_type']=='charge']
    deductions = [
        ('Brokerage', t['brokerage']), ('Builty Commission', t['builty_commission']),
        ('Late Fees', t['late_fees']), ('Material Damage', t['material_damage']),
        ('Shortage Amount', t['shortage_amount']), ('TDS', t['tds']), ('Other Deductions', t['other_deductions']),
    ]
    deductions = [(l, v) for l, v in deductions if v]
    deductions += [(item['description'], item['amount']) for item in extra_items if item['item_type']=='deduction']
    freight = _trip_freight(t['quantity'], t['rate'], t['rate_type'], t['fixed_rate_amount'], t['guarantee_qty'])
    extra_charges_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='charge')
    extra_deductions_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='deduction')
    invoice_total = (t['billed_amount'] or 0) + extra_charges_total - extra_deductions_total
    amount_paid = (t['payment_received'] or 0) + (t['party_advance'] or 0)
    balance_due = invoice_total - amount_paid
    return render_template('invoice_preview.html', t=t, inv=inv, charges=charges, deductions=deductions, freight=freight,
                            invoice_total=invoice_total, amount_paid=amount_paid, balance_due=balance_due, extra_items=extra_items,
                            s=s, active='invoices')

@app.route('/invoices/<int:trip_id>/edit', methods=['GET', 'POST'])
def invoice_edit(trip_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        existing = conn.execute("SELECT id FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
        _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if existing:
            conn.execute("UPDATE invoices SET invoice_number=?, due_date=?, notes=?, updated_by=?, updated_at=? WHERE trip_id=?",
                         (f.get('invoice_number'), f.get('due_date'), f.get('notes'), session.get('user_id'), _now, trip_id))
        else:
            conn.execute("""INSERT INTO invoices (trip_id, invoice_number, due_date, notes, created_by, created_at)
                            VALUES (?,?,?,?,?,?)""",
                         (trip_id, f.get('invoice_number'), f.get('due_date'), f.get('notes'), session.get('user_id'), _now))
        conn.commit()
        conn.close()
        return redirect(url_for('invoice_preview', trip_id=trip_id))

    t = conn.execute("SELECT lr_number FROM trips WHERE id=?", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    conn.close()
    default_invoice_no = f"INV-{t['lr_number']}" if not (inv and inv['invoice_number']) else None
    return render_template('invoice_edit.html', t=t, inv=inv, default_invoice_no=default_invoice_no, trip_id=trip_id, extra_items=extra_items, active='invoices')

@app.route('/invoices/<int:trip_id>/items/add', methods=['POST'])
def invoice_item_add(trip_id):
    conn = get_db()
    f = request.form
    item_amount, item_err = _parse_money(f.get('amount'), 'Amount')
    if item_err:
        conn.close()
        return redirect(url_for('invoice_edit', trip_id=trip_id, inv_error=item_err))
    conn.execute("""INSERT INTO invoice_items (trip_id, description, amount, item_type, created_by, created_at)
                    VALUES (?,?,?,?,?,?)""",
                 (trip_id, f.get('description'), item_amount, f.get('item_type'),
                  session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_edit', trip_id=trip_id))

@app.route('/invoices/items/<int:item_id>/delete', methods=['POST'])
def invoice_item_delete(item_id):
    conn = get_db()
    row = conn.execute("SELECT trip_id FROM invoice_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return redirect(url_for('invoice_center'))
    trip_id = row[0]
    conn.execute("DELETE FROM invoice_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('invoice_edit', trip_id=trip_id))

@app.route('/invoices/<int:trip_id>/pdf')
def invoice_pdf(trip_id):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from flask import send_file
    from xml.sax.saxutils import escape as esc
    import io

    conn = get_db()
    t = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name
                        FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                        LEFT JOIN parties p ON t.party_id=p.id WHERE t.id=?""", (trip_id,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE trip_id=?", (trip_id,)).fetchone()
    extra_items = conn.execute("SELECT * FROM invoice_items WHERE trip_id=?", (trip_id,)).fetchall()
    s = _get_invoice_settings(conn, session.get('company_id', 1))
    conn.close()
    company_name = s['company_name'] or get_company_name(session.get('company_id', 1))
    company_contact_line = ' &nbsp;&nbsp;|&nbsp;&nbsp; '.join(x for x in [
        (f"GSTIN No.: {esc(s['gstin'])}" if s['gstin'] else ''),
        (f"Mob. {esc(s['phone'])}" if s['phone'] else '')] if x)

    invoice_number = (inv['invoice_number'] if inv and inv['invoice_number'] else f"INV-{t['lr_number']}")
    due_date = (inv['due_date'] if inv and inv['due_date'] else '')
    inv_notes = (inv['notes'] if inv and inv['notes'] else '')

    freight = _trip_freight(t['quantity'], t['rate'], t['rate_type'], t['fixed_rate_amount'], t['guarantee_qty'])
    charges_total = sum(t[c] or 0 for c in ['driver_payment','detention_charges','gps_cost','loading_charge',
                        'unloading_charge','police_charges','sim_tracking','union_charges','weight_charges','other_charges'])
    deductions_total = sum(t[c] or 0 for c in ['brokerage','builty_commission','late_fees','material_damage',
                            'shortage_amount','tds','other_deductions'])
    extra_charges_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='charge')
    extra_deductions_total = sum(item['amount'] or 0 for item in extra_items if item['item_type']=='deduction')
    charges_total += extra_charges_total
    deductions_total += extra_deductions_total
    amount_paid = (t['payment_received'] or 0) + (t['party_advance'] or 0)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Title'], fontSize=20, textColor=colors.HexColor('#1B2A4A'), alignment=1)
    sub_style = ParagraphStyle('S', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'), alignment=1)
    label_style = ParagraphStyle('L', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5A6B8C'))
    story = []

    story.append(Paragraph(esc(company_name), title_style))
    story.append(Paragraph(esc(s['address'] or ''), sub_style))
    story.append(Paragraph(company_contact_line, sub_style))
    story.append(Spacer(1, 6))
    line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
    line_table.setStyle(TableStyle([('LINEBELOW', (0,0), (-1,-1), 1.5, colors.HexColor('#1B2A4A'))]))
    story.append(line_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>INVOICE</b>", styles['Heading2']))
    story.append(Spacer(1, 8))

    info_data = [
        ['Invoice No.', invoice_number, 'LR Number', t['lr_number'] or ''],
        ['Date', t['date'] or '', 'Due Date', due_date or '—'],
        ['Bill To', t['party_name'] or '', 'Vehicle No', t['vehicle_no'] or ''],
        ['From', t['from_loc'] or '', 'To', t['to_loc'] or ''],
        ['Material', t['material'] or '', 'Quantity (MT)', str(t['quantity'] or '')],
    ]
    info_table = Table(info_data, colWidths=[1*inch, 2.2*inch, 1*inch, 1.8*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 16))

    charge_rows = [['Description', 'Amount (Rs.)']]
    charge_rows.append(['Freight' + (' (Fixed)' if t['rate_type']=='FIXED' else f" ({t['quantity']} MT x Rs.{t['rate']})"), f"{freight:,.0f}"])
    if charges_total:
        charge_rows.append(['Total Charges (+)', f"{charges_total:,.0f}"])
    if deductions_total:
        charge_rows.append(['Total Deductions (-)', f"-{deductions_total:,.0f}"])
    invoice_total = (t['billed_amount'] or 0) + extra_charges_total - extra_deductions_total
    balance_due = invoice_total - amount_paid
    charge_rows.append(['TOTAL BILLED', f"{invoice_total:,.0f}"])
    if amount_paid:
        charge_rows.append(['Amount Already Paid', f"-{amount_paid:,.0f}"])
        charge_rows.append(['BALANCE DUE', f"{balance_due:,.0f}"])

    charge_table = Table(charge_rows, colWidths=[4*inch, 2*inch])
    charge_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B2A4A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor('#1B2A4A')),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#DDE3EC')),
    ]))
    story.append(charge_table)
    story.append(Spacer(1, 20))
    if inv_notes:
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"<b>Notes:</b> {inv_notes}", label_style))
    story.append(Spacer(1, 20))
    story.append(Paragraph("This is a system-generated invoice.", label_style))

    doc.build(story)
    buf.seek(0)
    safe_lr = "".join(c for c in (t['lr_number'] or 'invoice') if c.isalnum() or c in " _-")[:40]
    return send_file(buf, as_attachment=True, download_name=f'invoice_{safe_lr}.pdf', mimetype='application/pdf')

@app.route('/ledger/<entity_type>/<int:entity_id>/contact', methods=['POST'])
def update_contact(entity_type, entity_id):
    conn = get_db()
    f = request.form
    table = 'parties' if entity_type == 'party' else 'vendors'
    status = f.get('status') if f.get('status') in ('Active', 'Inactive') else 'Active'
    conn.execute(f"UPDATE {table} SET address=?, contact=?, email=?, credit_limit=?, gstin=?, category=?, status=?, updated_by=?, updated_at=? WHERE id=?",
                 (f.get('address'), f.get('contact'), f.get('email'), f.get('credit_limit') or None,
                  f.get('gstin') or None, f.get('category') or None, status,
                  session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), entity_id))
    conn.commit()
    conn.close()
    if entity_type == 'party':
        return redirect(url_for('party_ledger', party_id=entity_id))
    return redirect(url_for('vendor_ledger', vendor_id=entity_id))

def _update_ledger_opening_balance_from_form(conn, entity_type, entity_id, f):
    """Sets/replaces the carried-over opening balance for one party/vendor — same logic used by
    both update_opening_balance() (website) and app_update_ledger_opening_balance() (mobile).
    Returns (ok, error_message_or_None) — does not commit or close conn."""
    if entity_type not in ('party', 'vendor'):
        return False, 'Unknown account type.'
    table = 'parties' if entity_type == 'party' else 'vendors'
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone():
        return False, 'Account not found.'
    ob, err = _parse_money(f.get('opening_balance'), 'Opening Balance', required=True, allow_negative=True)
    if err:
        return False, err
    ob_date = (f.get('opening_balance_date') or '').strip() or None
    if ob_date:
        try:
            datetime.date.fromisoformat(ob_date)
        except ValueError:
            return False, 'As Of Date is not a valid date.'
    conn.execute(f"UPDATE {table} SET opening_balance=?, opening_balance_date=?, updated_by=?, updated_at=? WHERE id=?",
                 (ob, ob_date, session.get('user_id'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), entity_id))
    return True, None

@app.route('/ledger/<entity_type>/<int:entity_id>/opening-balance', methods=['POST'])
def update_opening_balance(entity_type, entity_id):
    conn = get_db()
    ok, error = _update_ledger_opening_balance_from_form(conn, entity_type, entity_id, request.form)
    if ok:
        conn.commit()
    conn.close()
    if not ok and entity_type not in ('party', 'vendor'):
        return redirect(url_for('accounts'))
    kw = {} if ok else {'pay_error': error}
    if entity_type == 'party':
        return redirect(url_for('party_ledger', party_id=entity_id, **kw))
    return redirect(url_for('vendor_ledger', vendor_id=entity_id, **kw))

def get_company_name(company_id=1):
    """company_id defaults to 1 — see the note on _get_invoice_settings above; same
    zero-behavior-change-today, ready-for-real-multi-company-later reasoning applies here.
    Cached on flask.g for the lifetime of the current request only (reset automatically by Flask
    on every new request, never persisted or shared across requests/users) — this one function is
    called many times per single page render (inject_company_name()'s context processor,
    _app_user_display(), and assorted routes that still ask for it directly), always with the
    exact same company_id within one request, always the same real DB value; caching it here
    turns every one of those into a single real query instead of one query per call, with the
    query itself, its result, and every caller's behavior completely unchanged."""
    cache_key = f'_company_name_cache_{company_id}'
    cached = getattr(g, cache_key, None)
    if cached is not None:
        return cached
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='company_name' AND company_id=?", (company_id,)).fetchone()
    conn.close()
    result = row['value'] if row else 'ANIL TRANSPORT SERVICE'
    setattr(g, cache_key, result)
    return result

@app.context_processor
def inject_company_name():
    try:
        return {'company_name': get_company_name(session.get('company_id', 1))}
    except Exception:
        return {'company_name': 'ANIL TRANSPORT SERVICE'}

@app.context_processor
def inject_app_name():
    """`app_name` — the product's own fixed name (see APP_NAME above), never per-company settings
    data. Used by the splash, login/signup/OTP screens, and the browser-tab title on all of
    them — every one of those reads {{ app_name }} rather than hardcoding the string, so renaming
    the product is exactly one edit (APP_NAME above) and nothing else. Kept as its own context
    processor rather than folded into inject_company_name() so the two stay conceptually and
    literally separate. See app_manifest() below for the one place APP_NAME couldn't reach as a
    plain Jinja variable — the PWA install manifest, which used to be a static JSON file with its
    own separately-hardcoded copy of the name."""
    return {'app_name': APP_NAME}

@app.route('/favicon.ico')
def favicon():
    """Browsers ask for /favicon.ico on their own (pages without a <link rel=icon>, bookmarks, tab restore); answer with the same wheel icon."""
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon', max_age=86400)


@app.route('/app-manifest.json')
def app_manifest():
    """The PWA install manifest — name/short_name come from APP_NAME (the same single source of
    truth every other RATH-branded screen reads via {{ app_name }}), not a second hardcoded copy.
    Used to live as a plain static file (static/app-manifest.json) with its own "RAATH" baked in,
    which is exactly the kind of second copy that goes stale the moment the product gets renamed
    again — same reasoning as _auth_hero.html's own comment about page-local copies. Icons/colors
    stay as real constants here (they're the brand's visual identity, not "the name", and aren't
    read from anywhere else) — see static/app-icons/*.png for those. mimetype set explicitly since
    this is a route, not a static file, so Flask wouldn't otherwise know to serve it as JSON."""
    return jsonify({
        'name': APP_NAME,
        'short_name': APP_NAME,
        'description': 'Fleet operations app',
        'start_url': '/app/dashboard',
        'scope': '/app/',
        'display': 'standalone',
        'background_color': '#c05621',
        'theme_color': '#c05621',
        'orientation': 'portrait-primary',
        'icons': [
            {'src': '/static/app-icons/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/app-icons/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/app-icons/icon-512-maskable.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    })

@app.context_processor
def inject_current_year():
    """Sidebar footer copyright year — computed, not hardcoded, so it never goes stale."""
    return {'current_year': datetime.datetime.now().year}

@app.context_processor
def inject_challan_alerts():
    """Powers the topbar notification bell + Vehicles nav badge on every page — real vehicles
    with a pending challan count, plus how many are expiring soon on Insurance/Fitness/PUC/
    Permit (same 30-day window as the Vehicles page's own KPI cards), not a fabricated example.
    Cheap query (only own-fleet vehicles), so it's fine to run on every request rather than
    caching. fleet_warning_marker is the single source of truth both this bell/badge AND
    vehicles_list.html's warning strip compare against when deciding whether a dismissal (see
    dismissChallanAlert) still applies — so they never disagree about what counts as "the same
    warning" vs. "something new." login_token — the current login's timestamp — keys that
    dismissal to this specific login, so it shows again on the next one rather than forever."""
    if 'user_id' not in session:
        return {'challan_alert_vehicles': [], 'challan_alert_count': 0, 'login_token': '', 'fleet_warning_marker': ''}
    if getattr(g, 'access_level', None) == 'Limited Access' and not _module_level('Vehicles'):
        # vehicle challan / expiry alerts are Vehicles data — a user without that screen doesn't get them in the bell
        return {'challan_alert_vehicles': [], 'challan_alert_count': 0, 'login_token': session.get('login_at', ''), 'fleet_warning_marker': ''}
    try:
        conn = get_db()
        rows = conn.execute("""SELECT id, vehicle_no, challan_count, challan_amount FROM vehicles
                               WHERE type IS NOT NULL AND type = 'own' AND COALESCE(challan_count,0) > 0
                               ORDER BY challan_amount DESC""").fetchall()
        challan_vehicles_count = len(rows)
        own_fleet = conn.execute("""SELECT insurance_expiry, fitness_expiry, puc_valid_upto, permit_valid_upto
                                    FROM vehicles WHERE type IS NOT NULL AND type = 'own'""").fetchall()
        ins_expiring = sum(1 for r in own_fleet if _expiry_bucket(r['insurance_expiry']) == 'expiring')
        fit_expiring = sum(1 for r in own_fleet if _expiry_bucket(r['fitness_expiry']) == 'expiring')
        puc_expiring = sum(1 for r in own_fleet if _expiry_bucket(r['puc_valid_upto']) == 'expiring')
        permit_expiring = sum(1 for r in own_fleet if _expiry_bucket(r['permit_valid_upto']) == 'expiring')
        conn.close()
        marker = f"{challan_vehicles_count}-{ins_expiring}-{permit_expiring}-{puc_expiring}-{fit_expiring}"
        return {'challan_alert_vehicles': rows, 'challan_alert_count': challan_vehicles_count,
                'login_token': session.get('login_at', ''), 'fleet_warning_marker': marker}
    except Exception:
        return {'challan_alert_vehicles': [], 'challan_alert_count': 0,
                'login_token': session.get('login_at', ''), 'fleet_warning_marker': ''}

IDLE_TIMEOUT_SECONDS = 3 * 60 * 60  # auto-logout after 3 hours with zero requests, any request resets it


# ---------------------------------------------------------------------------------------------------------------
# Limited Access: which screens a user may open, and whether they may change anything there.
# users.module_access holds e.g. "Trips:e,Ledger:v"  (e = Edit, v = View only). Old rows ("Trips,Ledger") count as Edit.
# ---------------------------------------------------------------------------------------------------------------
_MODULE_ALIASES = {'Payments': 'Ledger'}
_MODULE_ENDPOINTS = {
    'Dashboard': ['home', 'dashboard', 'app_dashboard'],
    'Trips': ['trips_list', 'trip_view', 'add_trip', 'edit_trip', 'delete_trip', 'end_trip', 'export_trips', 'export_trips_pdf',
              'app_trips', 'app_trip_detail', 'app_edit_trip', 'app_end_trip', 'app_add_trip'],
    'Vehicles': ['vehicles_list', 'vehicle_detail', 'vehicle_challan_detail', 'vehicle_rc_lookup', 'vehicle_rc_download', 'vehicle_sync_now',
                 'vehicle_compliance_renew', 'vehicle_compliance_sync', 'vehicles_compliance_sync_all', 'add_vehicle', 'edit_vehicle',
                 'delete_vehicle', 'toggle_vehicle_status', 'app_vehicles', 'app_vehicle_detail', 'app_add_vehicle', 'app_compliance',
                 'app_add_compliance'],
    'Maintenance': ['maintenance_list', 'maintenance_view', 'add_maintenance', 'edit_maintenance', 'delete_maintenance', 'export_maintenance',
                    'export_maintenance_pdf', 'check_battery', 'mark_battery_dead', 'delete_battery', 'install_battery', 'add_battery',
                    'delete_compliance_expense', 'add_compliance_expense', 'delete_insurance', 'edit_insurance', 'add_insurance',
                    'add_service', 'edit_service', 'approve_toll', 'delete_toll', 'reject_toll', 'add_toll', 'toll_excel_import',
                    'toll_excel_preview', 'sync_fastag_toll', 'toll_excel_template', 'add_tyre', 'edit_tyre', 'delete_tyre_stock',
                    'install_tyre_stock', 'scrap_tyre_stock', 'add_tyre_stock', 'delete_urea', 'edit_urea', 'add_urea', 'app_maintenance',
                    'app_edit_service', 'app_add_service', 'app_battery', 'app_install_battery', 'app_add_battery', 'app_tyres',
                    'app_add_tyre', 'app_install_tyre_stock', 'app_add_tyre_stock', 'app_urea', 'app_add_urea', 'app_toll', 'app_add_toll'],
    'Ledger': ['accounts', 'toggle_account_status', 'add_account', 'export_accounts', 'party_ledger', 'vendor_ledger', 'export_party_ledger',
               'export_party_ledger_pdf', 'export_vendor_ledger', 'export_vendor_ledger_pdf', 'update_contact', 'update_opening_balance',
               'add_party_payment', 'add_vendor_payment', 'payment_view', 'app_ledger', 'app_update_ledger_opening_balance',
               'app_add_account', 'app_party_ledger', 'app_vendor_ledger', 'app_add_party_payment', 'app_add_vendor_payment'],
    'Invoices': ['invoice_center', 'invoice_center_generate', 'invoice_center_review', 'invoices_search', 'invoice_preview', 'invoice_edit',
                 'invoice_item_add', 'invoice_pdf', 'invoice_batches_list', 'invoice_batch_delete', 'invoice_batch_edit',
                 'invoice_batch_item_add', 'invoice_batch_pdf', 'invoice_batch_pdf_preview', 'invoice_batch_item_delete', 'invoice_item_delete'],
    'Expenses': ['overheads_list', 'add_overhead', 'delete_overhead', 'edit_overhead', 'export_overheads', 'app_expenses', 'app_edit_overhead',
                 'app_add_overhead'],
    'Salaries': ['salaries_list', 'download_payslip', 'delete_salary', 'advances_list', 'add_advance', 'employee_ledger',
                 'update_employee_opening_balance', 'add_employee_advance', 'deactivate_employee', 'edit_employee', 'add_employee',
                 'bulk_mark_attendance', 'mark_attendance', 'app_employee_ledger', 'app_add_employee_payment',
                 'app_update_employee_opening_balance', 'app_salary', 'app_add_employee', 'app_salary_attendance'],
    'Reports': ['business_performance', 'export_business_performance', 'driver_performance', 'performance', 'export_performance',
                'route_analytics', 'export_route_analytics', 'fleet_utilization', 'export_fleet_utilization', 'empty_runs', 'idle_tracker',
                'route_finder_search', 'route_finder_suggest', 'route_finder_extras', 'app_analysis', 'app_analytics',
                'app_business_performance', 'app_fleet_utilization', 'app_performance'],
}
_ENDPOINT_MODULE = {ep: m for m, eps in _MODULE_ENDPOINTS.items() for ep in eps}
# open to every signed-in user (own profile, password, sign-in/out, static files)
_COMMON_ENDPOINTS = {'settings_page', 'website_change_password', 'website_update_my_profile', 'app_profile', 'app_change_password',
                     'app_update_profile', 'logout', 'static', 'app_manifest', 'favicon'}
# the whole-database Excel export is for Full Access users only (Limited users never get it)
_FULL_ACCESS_ONLY_ENDPOINTS = {'export_excel'}
# where each screen opens: (website, app)
_MODULE_LANDING = {'Dashboard': ('/dashboard', '/app/dashboard'), 'Trips': ('/trips', '/app/trips'), 'Vehicles': ('/vehicles', '/app/vehicles'),
                   'Maintenance': ('/maintenance', '/app/maintenance'), 'Ledger': ('/accounts', '/app/ledger'),
                   'Invoices': ('/invoice-center', None), 'Expenses': ('/overheads', '/app/expenses'), 'Salaries': ('/salaries', '/app/salary'),
                   'Reports': ('/route-analytics', '/app/analysis')}

def _parse_modules(text):
    """'Trips:e,Ledger:v' (or legacy 'Trips,Ledger') -> {'Trips': 'e', 'Ledger': 'v'}; unknown names are dropped."""
    out = {}
    for part in (text or '').split(','):
        part = part.strip()
        if not part:
            continue
        name, _, lvl = part.partition(':')
        name = _MODULE_ALIASES.get(name.strip(), name.strip())
        if name in MODULE_LIST:
            lvl = 'v' if lvl.strip().lower() in ('v', 'view') else 'e'
            if out.get(name) != 'e':        # Edit wins if a name appears twice (e.g. Payments + Ledger)
                out[name] = lvl
    return out

def _format_modules(mods):
    return ','.join(f'{m}:{mods[m]}' for m in MODULE_LIST if m in mods)

# ---- "Hide amounts on Trips" (per user) -----------------------------------------------------------------------
# A Limited Access user whose Trips access is View-only can be set to see stars instead of what the trip earns and what has been paid on it
# (rate, trip bill, charges, deductions, TDS, payments received, balance, party advance, and the hired-vehicle owner's amounts). Fuel, driver
# advance, toll and the other trip expenses, and everything about the trip itself (route, party, material, tonnage, dates, vehicle, driver) stay
# visible. With Edit access nothing is ever hidden. The values are removed on the server (here), not just hidden by the page.
TRIP_HIDDEN_COLUMNS = (
    'rate', 'fixed_rate_amount', 'billed_amount',
    'detention_charges', 'gps_cost', 'loading_charge', 'unloading_charge', 'police_charges', 'sim_tracking', 'union_charges',
    'weight_charges', 'other_charges',
    'brokerage', 'builty_commission', 'late_fees', 'material_damage', 'shortage_amount', 'tds', 'other_deductions',
    'party_advance', 'payment_received',
    'owner_rate', 'owner_fixed_amount', 'owner_amount', 'paid_to_owner', 'pending_to_owner',
)
_TRIP_FORM_ENDPOINTS = {'add_trip', 'edit_trip', 'app_add_trip', 'app_edit_trip'}
HIDDEN_AMOUNT_MARK = '\u2605\u2605\u2605\u2605'   # four stars: always the same length, so it says nothing about the size of the amount


def _hide_trip_amounts():
    """True when the signed-in user must not see trip amounts (see above)."""
    return bool(getattr(g, 'hide_trip_amounts', False))


def _mask_trip_dict(row):
    """A copy of one trip (dict or sqlite row) with every hidden column set to 0; returned unchanged when amounts are not hidden."""
    d = dict(row)
    if _hide_trip_amounts():
        for k in TRIP_HIDDEN_COLUMNS:
            if k in d:
                d[k] = 0
    return d


@app.template_global('mny')
def _mny(value, spec='{:,.0f}', prefix='\u20b9'):
    """Money for templates: the amount with the rupee sign, or four stars when this user's trip amounts are hidden."""
    if _hide_trip_amounts():
        return HIDDEN_AMOUNT_MARK
    try:
        return prefix + spec.format(value or 0)
    except (TypeError, ValueError):
        return prefix + '0'


@app.template_global('employee_choices')
def _employee_choices():
    """[{id, name, type, mobile}] of every employee, for the "also show the salary of ..." list and the live "own salary matched to ..." hint in the user editors."""
    conn = get_db()
    try:
        return [{'id': r['id'], 'name': r['name'], 'type': r['type'] or '', 'mobile': _digits10(r['mobile'])} for r in conn.execute("SELECT id, name, type, mobile FROM employees ORDER BY name").fetchall()]
    finally:
        conn.close()


@app.context_processor
def _inject_hide_amounts():
    return {'hide_amounts': _hide_trip_amounts()}


def _modules_from_form(form, access_level):
    """(module_access text or None, error or None) from the add/edit user form — values arrive as 'Trips:e' / 'Ledger:v'."""
    if access_level != 'Limited Access':
        return None, None
    mods = _parse_modules(','.join(form.getlist('modules')))
    if not mods:
        return None, 'Choose at least one screen for Limited Access.'
    return _format_modules(mods), None

def _hide_trip_amounts_from_form(form, modules_text):
    """1 only when the form asked for it AND the user's Trips access is View-only (with Edit nothing is ever hidden)."""
    return 1 if form.get('hide_trip_amounts') == '1' and _parse_modules(modules_text).get('Trips') == 'v' else 0


# ---- Salary visibility for a Limited Access user whose Salaries access is View-only ---------------------------------
# By default such a user sees NO employee's salary. The Administrator can tick "Own salary" (needs the login linked to an employee record) and/or
# "Drivers' salaries" (every employee whose type is Driver). With Edit access, or Full Access, nothing is restricted — as before.
def _employee_for_login(conn, phone, username=None):
    """The employee whose mobile number equals this login's mobile number (the login's phone, or its username when that is a mobile number), or
    None. This match is what "their own salary" means: no name or typed link is involved, so nobody can be matched to a salary that is not
    theirs. Two employees can never share a mobile number (checked when employees are saved); if bad data ever makes it ambiguous, nobody matches."""
    mob = _digits10(phone) or _digits10(username)
    if not mob:
        return None
    hits = [r for r in conn.execute("SELECT id, name, mobile FROM employees").fetchall() if _digits10(r['mobile']) == mob]
    return hits[0] if len(hits) == 1 else None


def _salary_scope_ids(conn):
    """None = unrestricted; otherwise the set of employees.id this user may see salary data for.
    Always includes the employee whose mobile number equals this login's mobile number (their OWN salary); plus every employee the Administrator ticked
    ('*' = everyone). The identity comes from the signed-in session (server side); the link and the ticks come from the database on every
    request, so a change by the Administrator (or a corrected mobile number) takes effect on the very next click."""
    sc = getattr(g, 'salary_scope', None)
    if sc is None:
        return None
    ids = set()
    if sc.get('employee_id'):
        ids.add(int(sc['employee_id']))
    raw = (sc.get('ids') or '').strip()
    if raw == '*':
        ids |= {r['id'] for r in conn.execute("SELECT id FROM employees").fetchall()}
    elif raw:
        wanted = {int(x) for x in raw.split(',') if x.strip().isdigit()}
        if wanted:
            ids |= {r['id'] for r in conn.execute("SELECT id FROM employees").fetchall() if r['id'] in wanted}
    return ids


def _salary_visible_employees(conn):
    """Every employees row this user may see, name order."""
    rows = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
    ids = _salary_scope_ids(conn)
    return rows if ids is None else [r for r in rows if r['id'] in ids]


def _salary_name_allowed(conn, name):
    """May this user see the ledger of the employee called `name`?"""
    ids = _salary_scope_ids(conn)
    if ids is None:
        return True
    row = conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE", ((name or '').strip(),)).fetchone()
    return bool(row) and row['id'] in ids


def _salary_ids_sql(ids):
    """'(1,2,3)' for a SQL IN clause ('(NULL)' when empty); ids are our own integers, never user text."""
    return '(' + (','.join(str(int(i)) for i in sorted(ids)) if ids else 'NULL') + ')'


def _salary_denied():
    return _limited_denied('Salaries', message="You can only see your own salary and, where your Administrator has allowed it, the drivers' salaries.")


def _digits10(v):
    d = ''.join(ch for ch in str(v or '') if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ''


def _salary_scope_from_form(conn, form, modules_text, user_id=None):
    """(salary_view_ids, error) from the add/edit user form: '*' (Everyone), '3,5' (ticked employees) or '' — counts only while Salaries is
    View-only. Their OWN salary needs no setting: it is found by matching the login's mobile number to an employee's mobile number."""
    if _parse_modules(modules_text).get('Salaries') != 'v':
        return '', None
    if form.get('salary_everyone') == '1':
        return '*', None
    valid = {r['id'] for r in conn.execute("SELECT id FROM employees").fetchall()}
    picked = sorted({int(x) for x in form.getlist('salary_ids') if str(x).isdigit() and int(x) in valid})
    return ','.join(str(i) for i in picked), None


def _module_level(module):
    """'e' (may change things), 'v' (view only) or '' (no access) for the signed-in user on `module`."""
    lvl = getattr(g, 'access_level', None)
    if lvl == 'Limited Access':
        return (getattr(g, 'modules', None) or {}).get(module, '')
    if lvl == 'Read Only':
        return 'v'
    return 'e'

def _first_allowed_url(app_side):
    for m in MODULE_LIST:
        if _module_level(m):
            site, mobile = _MODULE_LANDING[m]
            url = mobile if app_side else site
            if url:
                return url
    return '/app/profile' if app_side else '/settings'

def _limited_denied(module, view_only=False, message=None):
    msg = message or (f'Your access to {module} is view-only. Ask your Administrator if you need to make changes.' if view_only
           else (f'You don\'t have access to {module}. Ask your Administrator to add it.' if module else 'This screen isn\'t available for your access level.'))
    app_side = request.path.startswith('/app/')
    home = _first_allowed_url(app_side)
    if (app_side and 'text/html' not in (request.headers.get('Accept') or '')) or 'application/json' in (request.headers.get('Accept') or '') or request.method != 'GET':
        return jsonify({'ok': False, 'error': msg}), 403
    import html as _html
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>No access</title>'
            f'<body style="font-family:system-ui,sans-serif;background:#f5f6f9;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center">'
            f'<div style="background:#fff;border-radius:18px;padding:28px 24px;max-width:24rem;margin:16px;text-align:center;box-shadow:0 2px 12px rgba(20,33,61,.08)">'
            f'<div style="font-size:2.2rem">&#128274;</div><h2 style="margin:8px 0 6px;color:#14213d">{_html.escape(module or "This screen")}</h2>'
            f'<p style="color:#5a6b8c;line-height:1.5;margin:0 0 18px">{_html.escape(msg)}</p>'
            f'<a href="{home}" style="display:inline-block;background:#c05621;color:#fff;text-decoration:none;font-weight:700;padding:11px 20px;border-radius:12px">Go to my screens</a>'
            f'</div></body>', 403)

def _limited_block():
    """Enforcement for Limited Access users: None if the request may proceed, else the response to send instead."""
    ep = request.endpoint
    if ep is None or ep in _COMMON_ENDPOINTS:
        return None
    if ep in _FULL_ACCESS_ONLY_ENDPOINTS:
        return _limited_denied(None)
    module = _ENDPOINT_MODULE.get(ep)
    level = (g.modules or {}).get(module) if module else None
    if not level:
        if ep in _MODULE_ENDPOINTS['Dashboard'] and request.method == 'GET':
            dest = _first_allowed_url(request.path.startswith('/app/'))
            if dest != request.path:
                return redirect(dest)
        return _limited_denied(module)
    if level == 'v':
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _read_only_may_post():
            return _limited_denied(module, view_only=True)
        g.page_view_only = True      # shows the same "View-only access" layer a Read Only user gets, on this screen only
    return None

_APP_NAV_ITEMS = [   # module, label, url, icon, active-key, colours for the More menu
    ('Dashboard', 'Home', '/app/dashboard', 'home', 'dashboard', None, None),
    ('Trips', 'Trips', '/app/trips', 'trips', 'trips', None, None),
    ('Ledger', 'Ledger', '/app/ledger', 'ledger', 'ledger', None, None),
    ('Maintenance', 'Maintenance', '/app/maintenance', 'maintenance', 'maintenance', '#efeaff', 'var(--a-purple)'),
    ('Vehicles', 'Vehicles', '/app/vehicles', 'vehicles', 'vehicles', '#e8f5ee', 'var(--a-green)'),
    ('Expenses', 'Expenses', '/app/expenses', 'expenses', 'expenses', '#fdecea', 'var(--a-red)'),
    ('Salaries', 'Salary', '/app/salary', 'salary', 'salary', '#e6f6f4', 'var(--a-teal)'),
    ('Reports', 'Business Overview', '/app/analysis', 'analysis', 'analysis', '#fdf3e0', 'var(--a-amber)'),
]
_APP_FAB_ITEMS = [   # module needing Edit, label, url, icon, colours, opens the maintenance sub-menu?
    ('Trips', 'Add Trip', '/app/trips/add', 'add_trip', '#fdecea', 'var(--a-red)', False),
    ('Maintenance', 'Add Maintenance', '#', 'maintenance', '#eaf1fb', 'var(--a-blue)', True),
    ('Expenses', 'Add Expense', '/app/expenses?add=1', 'expenses', '#efeaff', 'var(--a-purple)', False),
    ('Vehicles', 'Add Vehicle', '/app/vehicles?add=1', 'vehicles', '#e8f5ee', 'var(--a-green)', False),
]
_FAB_POSITIONS = {1: [2], 2: [2, 3], 3: [1, 2, 3], 4: [1, 2, 3, 4]}

def _limited_nav():
    """What the mobile app's bottom bar, quick-add (+) fan and More menu show for a Limited Access user: only the screens
    they were given, packed into the same layout (first three in the bar, the rest under More, + only if they can add
    something). Full / Read Only users keep the standard fixed layout."""
    allowed = [dict(module=m, label=lb, url=u, icon=ic, active=ak, bg=bg, fg=fg)
               for (m, lb, u, ic, ak, bg, fg) in _APP_NAV_ITEMS if _module_level(m)]
    bar, more = allowed[:3], allowed[3:]
    fab = [dict(label=lb, url=u, icon=ic, bg=bg, fg=fg, maint=mf) for (m, lb, u, ic, bg, fg, mf) in _APP_FAB_ITEMS if _module_level(m) == 'e']
    for item, pos in zip(fab, _FAB_POSITIONS.get(len(fab), [])):
        item['pos'] = pos
    for i, item in enumerate(more, 1):
        item['pos'] = i
    return {'left': bar[:2], 'right': bar[2:], 'more': more, 'fab': fab, 'maint_fan': any(f['maint'] for f in fab),
            'view_only_all': bool(allowed) and not fab}

def _safe_next(raw, default):
    """Only ever send the user to a page on this site — never to an address supplied in a form."""
    raw = (raw or '').strip()
    if raw.startswith('/') and not raw.startswith('//') and '\\' not in raw and '\n' not in raw and '\r' not in raw:
        return raw
    return default

def _signup_open():
    return os.environ.get('ALLOW_SELF_SIGNUP') == '1'

@app.context_processor
def inject_security_flags():
    return {'signup_open': _signup_open()}

# Uploaded business documents (insurance papers, toll receipts) sit under /static/, which has no login check of its own —
# without this, anyone holding a file's address could open it. Logos stay public (the login page may show one).
_PRIVATE_UPLOAD_PREFIXES = ('/static/uploads/insurance/', '/static/uploads/toll_receipts/')

@app.before_request
def reject_cross_site_posts():
    """A form on another website must not be able to act as a logged-in user. Browsers state where a POST came from
    (Origin, or Referer as a fallback); if it names a different site than the one being used, refuse. Requests with
    neither header (scripts, the test client) are unaffected. Set DISABLE_ORIGIN_CHECK=1 only to rule this check out
    while debugging a proxy/host problem."""
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and os.environ.get('DISABLE_ORIGIN_CHECK') != '1':
        from urllib.parse import urlparse
        src = request.headers.get('Origin') or request.headers.get('Referer') or ''
        host = urlparse(src).netloc if src else ''
        if host and host != request.host:
            app.logger.warning('Blocked cross-site %s to %s from %s', request.method, request.path, src)
            return jsonify({'ok': False, 'error': 'This request came from another website and was blocked.'}), 403

@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if request.is_secure:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return resp

@app.before_request
def require_login():
    if request.endpoint == 'static' and request.path.startswith(_PRIVATE_UPLOAD_PREFIXES) and not session.get('user_id'):
        return ('Please sign in to open this document.', 403)
    exempt = ['login', 'app_login', 'app_signup', 'static', 'send_login_otp', 'verify_login_otp', 'internal_sync_tick', 'internal_backup',
              'app_login_otp', 'app_verify_login_otp', 'app_manifest', 'favicon']
    if request.endpoint in exempt:
        return
    # Every redirect-to-login below picks app_login instead of the website's own login for any
    # request under /app/ — otherwise an installed, standalone-launched app would drop back into
    # the full desktop marketing login page the instant a session isn't (or stops being) valid,
    # which is exactly the "looks like the website" break this branch exists to avoid.
    in_app_section = request.path.startswith('/app/')
    if not session.get('user_id'):
        return redirect(url_for('app_login') if in_app_section else url_for('login'))
    # Idle timeout — genuinely idle time, not a fixed session length: every authenticated request
    # (including "Remember me" sessions) refreshes this timestamp, so someone actively using the
    # app never gets logged out mid-work; only real inactivity for 3+ hours does.
    now = datetime.datetime.now()
    last_activity = session.get('last_activity')
    if last_activity:
        try:
            idle_seconds = (now - datetime.datetime.fromisoformat(last_activity)).total_seconds()
        except (ValueError, TypeError):
            idle_seconds = 0
        if idle_seconds > IDLE_TIMEOUT_SECONDS:
            session.clear()
            return redirect(url_for('app_login') if in_app_section else url_for('login'))
    session['last_activity'] = now.isoformat()

    # ---- user access level, read from the database on EVERY request so that an admin changing someone
    # to Read Only (or deactivating them) takes effect immediately, not at their next login.
    _conn = get_db()
    try:
        _u = _conn.execute("SELECT access_level, status, role, module_access, hide_trip_amounts, salary_view_ids, phone, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
    except sqlite3.OperationalError:
        # a database that does not have the per-user switch columns yet (its start-up step could not run): behave as if none is set
        _u = _conn.execute("SELECT access_level, status, role, module_access, 0 AS hide_trip_amounts, NULL AS salary_view_ids, phone, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
    _conn.close()
    if _u is None or (_u['status'] or 'Active') == 'Inactive':
        session.clear()
        return redirect(url_for('app_login') if in_app_section else url_for('login'))
    g.access_level = _u['access_level'] or 'Full Access'
    g.user_role = (_u['role'] or '').strip()
    # "Administrator" = the ROLE, not the Full Access level: a Manager can have Full Access to every operation
    # (trips, ledger, invoices...) and still must not see or change company settings, other users or the user log.
    g.is_administrator = g.user_role.lower() == 'administrator'
    if not g.is_administrator and (request.endpoint in _ADMIN_ONLY_ENDPOINTS or (request.endpoint == 'settings_page' and request.method == 'POST')):
        return _admin_only_denied()
    if g.access_level == 'Read Only' and request.method not in ('GET', 'HEAD', 'OPTIONS') and not _read_only_may_post():
        return _read_only_denied()
    g.modules = {}
    g.hide_trip_amounts = False
    g.salary_scope = None            # None = may see every employee's salary; otherwise {'own', 'drivers', 'employee_id'} (see _salary_scope_ids)
    if g.access_level == 'Limited Access':
        g.modules = _parse_modules(_u['module_access'])
        g.hide_trip_amounts = bool(_u['hide_trip_amounts']) and g.modules.get('Trips') == 'v'
        if g.modules.get('Salaries') == 'v':
            _c2 = get_db()
            try:
                own = _employee_for_login(_c2, _u['phone'], _u['username'])
            finally:
                _c2.close()
            g.salary_scope = {'ids': (_u['salary_view_ids'] or '').strip(), 'employee_id': own['id'] if own else None}
        if g.hide_trip_amounts and request.endpoint in _TRIP_FORM_ENDPOINTS:
            # the Add / Edit trip forms are full of rates and charges, and a View-only user cannot save them anyway
            return redirect('/app/trips' if in_app_section else url_for('trips_list'))
        blocked = _limited_block()
        if blocked is not None:
            return blocked


# ---- Read Only access: can see everything, can change nothing --------------------------------------
# These POSTs only READ (or change the user's own password), so a Read Only user may still use them.
_READ_ONLY_POST_ALLOWED = {
    'app_change_password',        # their own password — not business data
    'website_change_password',    # same, from the website's My Profile page
    'invoice_center_review',      # renders the review screen, writes nothing
    'toll_excel_preview',         # previews an import file, writes nothing
    'route_finder_extras',        # live map lookups
}

# Only an Administrator may touch these: the user list, user create/edit/delete/export, and company settings.
_ADMIN_ONLY_ENDPOINTS = {'add_user', 'edit_user', 'update_user_phone', 'delete_user', 'export_users', 'upload_company_logo',
                         'make_administrator', 'app_settings_save', 'app_settings_company', 'app_settings_banking', 'app_settings_invoice',
                         'app_settings_tax', 'app_settings_users'}

def _admin_only_denied():
    message = 'Only an Administrator can do this. You can view and update your own profile from Profile & Settings.'
    app.logger.info('Non-administrator user %s blocked from %s %s', session.get('user_id'), request.method, request.path)
    wants_html = 'text/html' in (request.headers.get('Accept') or '')
    if (request.path.startswith('/app/') and not wants_html) or 'application/json' in (request.headers.get('Accept') or ''):
        return jsonify({'ok': False, 'error': message}), 403
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Administrators only</title><body style="font-family:system-ui,sans-serif;max-width:34rem;margin:12vh auto;padding:0 1rem;line-height:1.5">'
            f'<h2>Administrators only</h2><p>{message}</p><p><a href="/settings">Go to my profile</a></p></body>', 403)

def _read_only_may_post():
    if request.endpoint in _READ_ONLY_POST_ALLOWED:
        return True
    # Invoice Center: only the 'preview' mode is a pure look at the PDF; 'generate'/'draft' save a batch
    # and consume an invoice number.
    if request.endpoint == 'invoice_center_generate' and (request.form.get('mode') or '') == 'preview':
        return True
    return False

def _read_only_denied():
    message = 'Your account has view-only access. You can see everything, but you cannot add, change or delete anything.'
    app.logger.info('Read Only user %s blocked from %s %s', session.get('user_id'), request.method, request.path)
    wants_html = 'text/html' in (request.headers.get('Accept') or '')
    if (request.path.startswith('/app/') and not wants_html) or 'application/json' in (request.headers.get('Accept') or ''):
        return jsonify({'ok': False, 'error': message}), 403
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>View-only access</title><body style="font-family:system-ui,sans-serif;max-width:34rem;margin:12vh auto;padding:0 1rem;line-height:1.5">'
            f'<h2>View-only access</h2><p>{message}</p><p>If you need to make changes, ask an administrator to give you Full Access.</p>'
            f'<p><a href="javascript:history.back()">&larr; Go back</a></p></body>', 403)

@app.context_processor
def inject_access_level():
    """`read_only` is True for a Read Only user — templates can use it to hide write controls."""
    level = getattr(g, 'access_level', None)
    role = getattr(g, 'user_role', '') or ''
    limited = level == 'Limited Access'
    return {'access_level_now': level, 'read_only': level == 'Read Only',
            'is_administrator': bool(getattr(g, 'is_administrator', False)), 'user_role_label': role or 'Staff',
            # can('Ledger') -> may this user open that screen at all; can('Ledger', edit=True) -> may they change things there
            'can': (lambda module, edit=False: bool(_module_level(module)) if not edit else _module_level(module) == 'e'),
            'limited_access': limited,
            'limited_nav': _limited_nav() if (limited and request.path.startswith('/app/')) else None}

_READ_ONLY_PAGE_SNIPPET = r'''
<style id="ro-style">
[data-ro-hide]{display:none !important}
#ro-toast{position:fixed;left:50%;top:18px;transform:translateX(-50%);z-index:2147483001;background:#b3261e;color:#fff;font:600 13px/1.4 system-ui,sans-serif;padding:10px 16px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:90vw;display:none}
</style>
<div id="ro-toast" role="alert"></div>
<script>
(function(){
  var MSG='View-only access: you can see everything, but you cannot add, change or delete anything.';
  var ALLOW=/\/profile\/change-password|\/settings\/my-password|\/invoice-center\/review|\/invoice-center\/generate|\/toll\/excel\/preview|\/route-finder\//;
  function toast(){var t=document.getElementById('ro-toast');if(!t)return;t.textContent=MSG;t.style.display='block';clearTimeout(window.__roT);window.__roT=setTimeout(function(){t.style.display='none'},3500);}
  var f=window.fetch;
  if(f){window.fetch=function(u,o){var m=((o&&o.method)||'GET').toUpperCase();var url=(typeof u==='string'?u:(u&&u.url)||'');
    if(m!=='GET'&&m!=='HEAD'&&!ALLOW.test(url)){toast();return Promise.resolve(new Response(JSON.stringify({ok:false,error:MSG}),{status:403,headers:{'Content-Type':'application/json'}}));}
    return f.apply(this,arguments);};}
  document.addEventListener('submit',function(e){var fm=e.target;if(fm&&(fm.method||'get').toLowerCase()==='post'&&!ALLOW.test(fm.action||'')){e.preventDefault();e.stopPropagation();toast();}},true);
  var WORDS=/^\s*(\+|\u2795|add\b|edit\b|delete\b|remove\b|update\b|save\b|end trip\b|record\b|import\b|approve\b|reject\b|deactivate\b|activate\b|mark\b|renew\b|give\b|install\b|sync\b|upload\b)/i;
  var TITLES=/delete|remove|edit/i;
  function sweep(root){(root||document).querySelectorAll('button,a,input[type=submit],input[type=button],[role=button]').forEach(function(el){
    if(el.hasAttribute('data-ro-hide')||el.closest('#ro-toast'))return;
    var txt=(el.tagName==='INPUT'?el.value:el.textContent)||'';txt=txt.trim().replace(/^[^A-Za-z0-9+]+/,'');   /* ignore a leading icon such as the pencil in 'Edit' */
    var ttl=(el.getAttribute('title')||'')+' '+(el.getAttribute('aria-label')||'');
    if(el.closest('form[method=get],form[method=GET]')&&el.type==='submit')return;
    if((txt.length<40&&WORDS.test(txt))||(!txt&&TITLES.test(ttl))||(TITLES.test(ttl)&&txt.length<3)){el.setAttribute('data-ro-hide','1');}
  });}
  function run(){sweep();try{new MutationObserver(function(){sweep();}).observe(document.body,{childList:true,subtree:true});}catch(e){}}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();
})();
</script>
'''

@app.after_request
def _read_only_page_enhancements(response):
    """For a Read Only user's HTML pages: hide the obvious add / edit /
    delete controls and politely block any write that is attempted anyway. This is a convenience layer —
    the real enforcement is the server-side check in require_login()."""
    try:
        if ((getattr(g, 'access_level', None) == 'Read Only' or getattr(g, 'page_view_only', False)) and response.mimetype == 'text/html'
                and response.status_code == 200 and not response.direct_passthrough and not request.path.startswith('/static/')):
            body = response.get_data(as_text=True)
            if '</body>' in body:
                response.set_data(body.replace('</body>', _READ_ONLY_PAGE_SNIPPET + '</body>', 1))
    except Exception:
        app.logger.exception('could not add the read-only page layer')
    return response

@app.after_request
def _no_bfcache_for_dynamic_pages(response):
    """Every page here is server-rendered fresh from the live database on each request — with no
    Cache-Control header at all (the previous state), the browser's back/forward cache is free to
    restore a page from memory instead of asking the server again. That's exactly what made
    editing or deleting a trip and then hitting Back look like the Dashboard "didn't update" —
    the numbers were correct on the server the whole time, the browser just showed an old
    snapshot instead of re-fetching. no-store forces a real request on every back/forward
    navigation too, so any page always reflects whatever the database actually holds right now.
    Left untouched for /static/ (real static assets — fine to let the browser cache those for
    performance, they don't change based on what any user just did) and for anything already
    carrying its own Cache-Control (a PDF/Excel download already sets its own headers via
    send_file — this must never override those)."""
    if not request.path.startswith('/static/') and 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    from werkzeug.security import check_password_hash
    error = None
    if request.method == 'POST':
        f = request.form
        username = (f.get('username') or '').strip()
        if not _username_login_format_ok(username):
            return render_template('login.html', error='Username must be a valid Indian mobile number or email address.')
        conn = get_db()
        blocked = _login_throttle_message(conn, username)
        if blocked:
            conn.close()
            return render_template('login.html', error=blocked), 429
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], f.get('password') or ''):
            if (user['status'] or 'Active') == 'Inactive':
                conn.close()
                return render_template('login.html', error='This account has been deactivated. Contact an administrator.')
            _auth_clear(conn, 'login_fail_user', username.lower())
            session.permanent = bool(f.get('remember_me'))
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user['is_admin']
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            session['login_at'] = now  # lets dismissed-warning state reset on every fresh login, see inject_challan_alerts
            session['show_zoom_tip'] = True  # one-shot — shown at most once, then popped by base.html on the next render
            conn.execute("UPDATE users SET last_login=?, updated_by=?, updated_at=? WHERE id=?",
                         (now, user['id'], now, user['id']))
            conn.execute("INSERT INTO access_logs (user_id, event, date) VALUES (?,?,?)", (user['id'], 'Login', now))
            conn.commit()
            conn.close()
            return redirect(url_for('dashboard'))
        _login_failed(conn, username)
        conn.close()
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    # ctx=app is the reliable signal — set explicitly by templates/app/profile.html's own logout
    # link — that this sign-out happened from inside the installed app, so it should land back on
    # the app's own login screen, not the website's. Referer is kept only as a fallback for any
    # other logout link that doesn't pass ctx=app yet: an installed, standalone PWA frequently
    # sends NO Referer header at all (privacy-motivated browser/OS behavior), which silently made
    # this always fall through to the website's login page — the real cause behind "logout lands
    # somewhere with the old design" reports, not a caching or deployment issue.
    came_from_app = request.args.get('ctx') == 'app' or (bool(request.referrer) and '/app/' in request.referrer)
    session.clear()
    if came_from_app:
        # from_logout=1 tells _splash.html to skip the cold-open splash on this one landing —
        # sessionStorage's own "already shown this session" gate can't be relied on here (some
        # mobile WebViews don't reliably keep sessionStorage across a standalone-PWA navigation
        # like this), and replaying the full splash animation right after a deliberate logout
        # reads as a jarring re-launch instead of a quick, quiet sign-out.
        return redirect(url_for('app_login', from_logout=1))
    return redirect(url_for('login'))

def _sms_configured(s):
    """SMS login works through MSG91 only (an Indian provider, a few paise per SMS)."""
    return bool((s.get('msg91_authkey') or '').strip() and (s.get('msg91_template_id') or '').strip())

MSG91_OTP_URL = 'https://control.msg91.com/api/v5/otp'

def _send_otp_msg91(s, phone, otp):
    """Delivers OUR code through MSG91's Send OTP API. We create, store (hashed) and verify the code ourselves — MSG91 only carries
    the SMS, so attempt limits, expiry and single use stay exactly as they are. The text of the SMS is the DLT-approved template
    in the MSG91 account (it must contain the ##OTP## variable). MSG91 can answer HTTP 200 with {"type":"error"}, so the body is
    checked, not just the status. Raises on any failure."""
    import requests
    mobile = ('91' + phone) if USERNAME_MOBILE_RE.match(phone or '') else re.sub(r'\D', '', phone or '')
    resp = requests.post(MSG91_OTP_URL,
                         params={'template_id': s['msg91_template_id'].strip(), 'mobile': mobile, 'otp': otp, 'otp_length': len(otp), 'otp_expiry': 5},
                         headers={'authkey': s['msg91_authkey'].strip(), 'Content-Type': 'application/json', 'Accept': 'application/json'},
                         json={}, timeout=15)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or str(data.get('type', '')).lower() != 'success':
        raise RuntimeError(f"MSG91 did not send the code (HTTP {resp.status_code}): {str(data.get('message') or resp.text)[:160]}")

def _send_otp_sms(s, phone, otp):
    _send_otp_msg91(s, phone, otp)

@app.route('/login/otp/send', methods=['POST'])
def send_login_otp():
    phone = (request.form.get('phone') or '').strip()
    if not phone:
        return render_template('login.html', otp_mode=True, otp_error='Enter a mobile number.')
    if not USERNAME_MOBILE_RE.match(phone):
        return render_template('login.html', otp_mode=True, otp_error='Enter a valid 10-digit Indian mobile number.')
    err = _start_otp_login(phone)
    if err:
        return render_template('login.html', otp_mode=True, otp_error=err), (429 if err.startswith('Too many') else 200)
    return render_template('login.html', otp_mode=True, otp_sent=True, otp_phone=phone)

def _start_otp_login(phone):
    """Shared by the website and the app: rate-limits, creates a code and sends it. Returns an error message or None.
    The reply is the same whether or not the number belongs to an account, so the form cannot be used to find out
    who has one."""
    conn = get_db()
    s = _get_all_settings(conn, session.get('company_id', 1))
    if not _sms_configured(s):
        conn.close()
        return "SMS login isn't configured yet. Ask an admin to set up the SMS service in Settings."
    wait = max(_auth_wait_seconds(conn, 'otp_send_phone', phone), _auth_wait_seconds(conn, 'otp_send_ip', _client_ip()))
    if wait:
        conn.close()
        return f'Too many codes requested. Please try again in {_wait_text(wait)}.'
    _auth_record(conn, 'otp_send_phone', phone)
    _auth_record(conn, 'otp_send_ip', _client_ip())
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    session.pop('otp_row', None)
    for key in ('otp_user_id', 'otp_hash', 'otp_expires'):   # leftovers from the old cookie-based flow
        session.pop(key, None)
    if user and (user['status'] or 'Active') != 'Inactive':
        code, otp_id = _otp_issue(conn, user, phone)
        session['otp_row'] = otp_id
        session['otp_phone'] = phone
        try:
            _send_otp_sms(s, phone, code)
        except Exception:
            app.logger.exception('Could not send login OTP SMS')
            conn.execute("UPDATE login_otps SET used=1 WHERE id=?", (otp_id,))
            conn.commit()
            conn.close()
            return 'Could not send the code right now. Please try again in a few minutes.'
    else:
        session['otp_phone'] = phone     # nothing is sent, but the screen behaves the same
    conn.close()
    return None

def _finish_otp_login(phone, code):
    """(user_row, None) once logged in, else (None, message)."""
    conn = get_db()
    user_id, err = _otp_check(conn, session.get('otp_row'), (phone or ''), code) if session.get('otp_phone') == phone else (None, 'Session expired. Please request a new OTP.')
    if err:
        conn.close()
        return None, err
    session.pop('otp_row', None); session.pop('otp_phone', None)
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return None, 'Session expired. Please request a new OTP.'
    if (user['status'] or 'Active') == 'Inactive':
        conn.close()
        return None, 'This account has been deactivated. Contact an administrator.'
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['is_admin'] = user['is_admin']
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    session['login_at'] = now  # lets dismissed-warning state reset on every fresh login, see inject_challan_alerts
    session['show_zoom_tip'] = True  # one-shot — shown at most once, then popped by base.html on the next render
    conn.execute("UPDATE users SET last_login=?, updated_by=?, updated_at=? WHERE id=?", (now, user['id'], now, user['id']))
    conn.execute("INSERT INTO access_logs (user_id, event, date) VALUES (?,?,?)", (user['id'], 'Login (OTP)', now))
    conn.commit()
    conn.close()
    return user, None

@app.route('/login/otp/verify', methods=['POST'])
def verify_login_otp():
    code = (request.form.get('otp') or '').strip()
    phone = request.form.get('phone') or ''
    user, err = _finish_otp_login(phone, code)
    if err:
        if err.startswith('Incorrect'):
            return render_template('login.html', otp_mode=True, otp_sent=True, otp_phone=phone, otp_error=err)
        if err.startswith('This account'):
            return render_template('login.html', error=err)
        return render_template('login.html', otp_mode=True, otp_error=err)
    return redirect(url_for('dashboard'))

ALL_SETTING_KEYS = [
    'company_name', 'address', 'state', 'phone', 'email', 'gstin', 'pan', 'business_type', 'company_since', 'currency',
    'bank_name', 'account_holder', 'account_number', 'ifsc_code', 'branch', 'account_type',
    'default_invoice_type', 'default_payment_terms', 'default_place_of_supply', 'default_due_days',
    'show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words', 'logo_path',
    'invoice_prefix', 'next_invoice_number',
    'cgst_rate', 'sgst_rate', 'igst_rate', 'reverse_charge_applicable', 'rcm_on_transport',
    'tds_applicable', 'tds_rate_default', 'eway_bill_mandatory', 'round_off_limit', 'rcm_clause',
    'msg91_authkey', 'msg91_template_id',
    'rc_lookup_api_key', 'rc_sync_interval_days', 'challan_sync_interval_days'
]

# The screens an Administrator can give to a "Limited Access" user (Settings and Users are never listed — they stay with the
# Administrator). Payments live inside Ledger. Each can be granted as View or Edit.
MODULE_LIST = ['Dashboard', 'Trips', 'Vehicles', 'Maintenance', 'Ledger', 'Invoices', 'Expenses', 'Salaries', 'Reports']
ROLE_SUGGESTIONS = ['Administrator', 'Manager', 'Operations Head', 'Accountant', 'Supervisor', 'Driver']

def _update_own_profile(conn, user_id, f):
    """A user editing THEIR OWN name / email / mobile — nothing else (role, access level, status and password are an
    Administrator's / the password flow's business). Returns (ok, error_or_None). Does not commit."""
    full_name = (f.get('full_name') or '').strip()
    email = (f.get('email') or '').strip() or None
    phone = (f.get('phone') or '').strip() or None
    if not full_name:
        return False, 'Full Name is required.'
    if phone and not USERNAME_MOBILE_RE.match(phone):
        return False, 'Mobile Number must be a valid 10-digit Indian mobile number.'
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return False, 'Email is not valid.'
    dup = _duplicate_user_contact(conn, phone, email, exclude_user_id=user_id)
    if dup:
        return False, dup
    conn.execute("UPDATE users SET full_name=?, email=?, phone=?, updated_by=?, updated_at=? WHERE id=?",
                 (full_name, email, phone, user_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
    return True, None

def _change_own_password(conn, user_id, f):
    """Self-service password change (current password verified first). Returns (ok, error_or_None). Does not commit."""
    from werkzeug.security import check_password_hash, generate_password_hash
    current_pw, new_pw, confirm_pw = f.get('current_password') or '', f.get('new_password') or '', f.get('confirm_password') or ''
    if not current_pw or not new_pw:
        return False, 'All fields are required.'
    if new_pw != confirm_pw:
        return False, 'New password and confirmation do not match.'
    if len(new_pw) < 8:
        return False, 'New password must be at least 8 characters.'
    user = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not check_password_hash(user['password_hash'], current_pw):
        return False, 'Current password is incorrect.'
    conn.execute("UPDATE users SET password_hash=?, updated_by=?, updated_at=? WHERE id=?",
                 (generate_password_hash(new_pw), user_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
    return True, None

def _render_my_profile(conn, message=None, error=None):
    u = conn.execute("SELECT username, full_name, phone, email, role, access_level, status, created_at, last_login FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return render_template('profile_self.html', u=u, message=message, error=error, active='settings')

@app.route('/settings/my-profile', methods=['POST'])
def website_update_my_profile():
    conn = get_db()
    ok, err = _update_own_profile(conn, session['user_id'], request.form)
    if ok:
        conn.commit()
    return _render_my_profile(conn, message='Profile updated.' if ok else None, error=err)

@app.route('/settings/my-password', methods=['POST'])
def website_change_password():
    conn = get_db()
    ok, err = _change_own_password(conn, session['user_id'], request.form)
    if ok:
        conn.commit()
    return _render_my_profile(conn, message='Password changed.' if ok else None, error=err)

def _administrator_count(conn, exclude_user_id=None):
    q = "SELECT COUNT(*) FROM users WHERE LOWER(TRIM(COALESCE(role,'')))='administrator' AND COALESCE(status,'Active')!='Inactive'"
    p = []
    if exclude_user_id is not None:
        q += " AND id != ?"; p.append(exclude_user_id)
    return conn.execute(q, p).fetchone()[0]

_ONE_ADMIN_MSG = ('Only one Administrator can be active at a time. To hand the role to someone else, open that user and choose '
                  '"Make Administrator" — you will become a Manager.')

def _is_admin_role(role):
    return (role or '').strip().lower() == 'administrator'

def _other_active_administrator(conn, exclude_user_id=None):
    """The active Administrator other than `exclude_user_id`, or None. There may be at most ONE active Administrator."""
    q = "SELECT id, full_name, username FROM users WHERE LOWER(TRIM(COALESCE(role,'')))='administrator' AND COALESCE(status,'Active')!='Inactive'"
    p = []
    if exclude_user_id is not None:
        q += " AND id != ?"; p.append(exclude_user_id)
    return conn.execute(q + " LIMIT 1", p).fetchone()

def _role_suggestions(conn):
    """Role picker suggestions — 'Administrator' is only offered while nobody holds it (it is a single seat)."""
    if _other_active_administrator(conn):
        return [r for r in ROLE_SUGGESTIONS if not _is_admin_role(r)]
    return list(ROLE_SUGGESTIONS)

def _users_with_stats(conn):
    rows = conn.execute("""SELECT * FROM users ORDER BY
                           CASE access_level WHEN 'Full Access' THEN 0 WHEN 'Read Only' THEN 1 ELSE 2 END,
                           username""").fetchall()
    users = [dict(r) for r in rows]
    for u in users:                    # which employee this login's mobile number matches (their own salary), if any
        emp = _employee_for_login(conn, u.get('phone'), u.get('username'))
        u['salary_employee'] = emp['name'] if emp else None
    total = len(users)
    admin_n = sum(1 for u in users if u['access_level'] == 'Full Access')
    readonly_n = sum(1 for u in users if u['access_level'] == 'Read Only')
    limited_n = sum(1 for u in users if u['access_level'] == 'Limited Access')
    inactive_n = sum(1 for u in users if u['status'] == 'Inactive')
    return users, total, admin_n, readonly_n, limited_n, inactive_n

_SETTINGS_FIELD_GROUPS = {
    'company': ['company_name', 'address', 'state', 'phone', 'email', 'gstin', 'pan', 'business_type', 'company_since', 'currency'],
    'banking': ['bank_name', 'account_holder', 'account_number', 'ifsc_code', 'branch', 'account_type'],
    'invoice_prefs': ['default_invoice_type', 'default_payment_terms', 'default_place_of_supply', 'default_due_days',
                      'show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words'],
    'invoice_numbering': ['invoice_prefix', 'next_invoice_number'],
    'tax': ['cgst_rate', 'sgst_rate', 'igst_rate', 'reverse_charge_applicable', 'rcm_on_transport',
            'tds_applicable', 'tds_rate_default', 'eway_bill_mandatory', 'round_off_limit'],
    'rcm': ['rcm_clause'],
    'sms': ['msg91_authkey', 'msg91_template_id'],
    'rc_lookup': ['rc_lookup_api_key'],
    'sync_schedule': ['rc_sync_interval_days', 'challan_sync_interval_days'],
}
_SECRET_SETTING_KEYS = {'msg91_authkey', 'rc_lookup_api_key'}
_SETTINGS_CHECKBOX_KEYS = {'show_company_logo', 'show_bank_details', 'show_signatory', 'print_amount_words',
                           'reverse_charge_applicable', 'rcm_on_transport', 'tds_applicable', 'eway_bill_mandatory'}

def _save_settings_group(conn, form_type, form):
    """Saves one Settings form (company / banking / invoice / tax / ...) — the single writer used by BOTH the website's
    Settings page and the mobile app's Administrator screens, so the two can never drift apart. Returns True if
    form_type is a known group. Commits."""
    if form_type not in _SETTINGS_FIELD_GROUPS:
        return False
    # INSERT OR REPLACE, not a plain UPDATE — a bare UPDATE silently does nothing when no
    # row exists yet for that key (no error, no rows affected), which is exactly how a
    # setting could look "saved" (200 OK, page reloads fine) while never actually taking
    # effect. This is the same pattern already used correctly for logo_path in upload_company_logo.
    _settings_now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for key in _SETTINGS_FIELD_GROUPS[form_type]:
        if key in _SETTINGS_CHECKBOX_KEYS:
            val = '1' if form.get(key) == 'on' else '0'
        else:
            val = form.get(key, '')
            if key in _SECRET_SETTING_KEYS and not (val or '').strip():
                # secrets are never shown again in a form, so a blank box means "keep the one already saved"
                row = conn.execute("SELECT value FROM settings WHERE key=? AND company_id=?", (key, session.get('company_id', 1))).fetchone()
                val = row['value'] if row else ''
        conn.execute("""INSERT OR REPLACE INTO settings (key, value, company_id, created_by, created_at, updated_by, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (key, val, session.get('company_id', 1), session.get('user_id'), _settings_now,
                      session.get('user_id'), _settings_now))
    conn.commit()
    return True

def _settings_form_error(form_type, form):
    """Readable validation message for the app's Settings forms (None = fine). The website form has always accepted
    anything; the app form checks the obvious things so a typo can't put "abc%" into a GST rate."""
    def num(key, label, lo=0, hi=None, integer=False):
        raw = (form.get(key) or '').strip()
        if raw == '':
            return None
        try:
            v = float(raw)
        except ValueError:
            return f'{label} must be a number.'
        if v != v or v in (float('inf'), float('-inf')):
            return f'{label} must be a number.'
        if integer and v != int(v):
            return f'{label} must be a whole number.'
        if v < lo:
            return f'{label} cannot be less than {lo:g}.'
        if hi is not None and v > hi:
            return f'{label} cannot be more than {hi:g}.'
        return None
    checks = []
    if form_type == 'company':
        if not (form.get('company_name') or '').strip():
            return 'Company Name is required.'
        em = (form.get('email') or '').strip()
        if em and ('@' not in em or ' ' in em):
            return 'Enter a valid email address.'
    elif form_type == 'tax':
        for key, label in (('cgst_rate', 'CGST Rate'), ('sgst_rate', 'SGST Rate'), ('igst_rate', 'IGST Rate'),
                           ('tds_rate_default', 'TDS Rate'), ('round_off_limit', 'Round Off Limit')):
            if not (form.get(key) or '').strip():
                return f'{label} is required (enter 0 if it does not apply).'
        checks = [('cgst_rate', 'CGST Rate', 0, 100), ('sgst_rate', 'SGST Rate', 0, 100), ('igst_rate', 'IGST Rate', 0, 100),
                  ('tds_rate_default', 'TDS Rate', 0, 100), ('round_off_limit', 'Round Off Limit', 0, 1000)]
    elif form_type == 'invoice_prefs':
        checks = [('default_due_days', 'Default Due Days', 0, 3650, True)]
    elif form_type == 'invoice_numbering':
        if not (form.get('invoice_prefix') or '').strip():
            return 'Invoice Prefix is required.'
        if not (form.get('next_invoice_number') or '').strip():
            return 'Next Invoice Number is required.'
        checks = [('next_invoice_number', 'Next Invoice Number', 1, 99999999, True)]
    elif form_type == 'sms':
        if not (form.get('msg91_authkey') or '').strip():
            return 'MSG91 Auth Key is required.'
        if not (form.get('msg91_template_id') or '').strip():
            return 'MSG91 Template ID is required.'
    elif form_type == 'sync_schedule':
        checks = [('rc_sync_interval_days', 'RC / Compliance Sync days', 1, 365, True),
                  ('challan_sync_interval_days', 'eChallan Sync days', 1, 365, True)]
    for c in checks:
        err = num(*c[:4], integer=(len(c) > 4 and c[4]))
        if err:
            return err
    return None

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    conn = get_db()
    if not g.is_administrator:
        # Everyone who is not an Administrator sees ONLY their own profile here — no company details, bank/API keys,
        # user list or user log (a POST from a non-administrator is refused in require_login()).
        return _render_my_profile(conn)
    if request.method == 'POST':
        _save_settings_group(conn, request.form.get('form_type'), request.form)

    users, total_users, admin_users, readonly_users, limited_users, inactive_users = _users_with_stats(conn)
    role_counts = {}
    for u in users:
        r = u['role'] or 'Unassigned'
        role_counts[r] = role_counts.get(r, 0) + 1
    access_logs = conn.execute("""SELECT al.date, al.event, u.username, u.full_name FROM access_logs al
                                  LEFT JOIN users u ON al.user_id=u.id ORDER BY al.id DESC LIMIT 50""").fetchall()
    s = _get_all_settings(conn, session.get('company_id', 1))
    recent_sync_log = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 8").fetchall()
    role_sugg = _role_suggestions(conn)
    conn.close()

    active_settings_tab = request.args.get('tab', 'company')
    invoice_example = f"{s['invoice_prefix']}/2026/{int(s['next_invoice_number'] or 1):04d}" if s['invoice_prefix'] else ''
    return render_template('settings.html', company_name=s.get('company_name') or get_company_name(session.get('company_id', 1)),
                            users=users, total_users=total_users, admin_users=admin_users, readonly_users=readonly_users,
                            limited_users=limited_users, inactive_users=inactive_users, role_counts=role_counts,
                            access_logs=access_logs, module_list=MODULE_LIST, role_suggestions=role_sugg,
                            s=s, invoice_example=invoice_example, recent_sync_log=recent_sync_log,
                            active='settings', active_settings_tab=active_settings_tab)

def _get_all_settings(conn, company_id=1):
    """company_id defaults to 1 — see the note on _get_invoice_settings above."""
    s = {}
    for key in ALL_SETTING_KEYS:
        row = conn.execute("SELECT value FROM settings WHERE key=? AND company_id=?", (key, company_id)).fetchone()
        s[key] = row['value'] if row else ''
    return s

def _duplicate_user_contact(conn, phone, email, exclude_user_id=None):
    """Returns a human-readable reason string if `phone` or `email` is already used by a
    DIFFERENT user account, else None — checked before every user create/update (add_user,
    edit_user, update_user_phone, app_signup) so two accounts can never silently end up sharing
    the same mobile number or email. Each is how a real person is identified/contacted now (see
    app_signup()'s own comment on why username IS the phone/email for self-signups), so a
    collision here is a real account mixup waiting to happen, not just a cosmetic duplicate.
    Blank phone/email never conflicts with anything — most admin-created accounts legitimately
    have one or both unset. exclude_user_id lets an edit ignore the row's own current value."""
    phone = (phone or '').strip() or None
    email = (email or '').strip() or None
    if phone:
        q = "SELECT username FROM users WHERE phone=?"
        params = [phone]
        if exclude_user_id is not None:
            q += " AND id != ?"; params.append(exclude_user_id)
        row = conn.execute(q, params).fetchone()
        if row:
            return f'Mobile number "{phone}" is already used by another account ("{row["username"]}").'
    if email:
        q = "SELECT username FROM users WHERE email=? COLLATE NOCASE"
        params = [email]
        if exclude_user_id is not None:
            q += " AND id != ?"; params.append(exclude_user_id)
        row = conn.execute(q, params).fetchone()
        if row:
            return f'Email "{email}" is already used by another account ("{row["username"]}").'
    return None

def _wants_json():
    """The mobile app's Administrator screens call the same user/settings routes with fetch() and this header, and get JSON
    back instead of the website's redirect / re-rendered page."""
    return request.headers.get('X-App-Request') == '1'

def _users_done():
    return jsonify({'ok': True}) if _wants_json() else redirect(url_for('settings_page', tab='users'))

def _render_settings_users_error(conn, error, reopen_add_user=False, reopen_edit_user_id=None):
    """Shared re-render for every Settings > Users form's validation-failure path (username
    taken, duplicate phone/email, ...) — one place building the same users/stats/access_logs
    context add_user()'s own username-taken branch always needed, so edit_user() and future
    checks don't each re-duplicate that block. Closes `conn`. reopen_add_user/reopen_edit_user_id
    tell settings.html which modal (if any) to pop back open with `error` shown inside it, instead
    of the admin's in-progress edit silently vanishing behind a page reload."""
    if _wants_json():
        conn.close()
        return jsonify({'ok': False, 'error': error}), 400
    users, total_users, admin_users, readonly_users, limited_users, inactive_users = _users_with_stats(conn)
    role_counts = {}
    for u in users:
        r = u['role'] or 'Unassigned'
        role_counts[r] = role_counts.get(r, 0) + 1
    access_logs = conn.execute("""SELECT al.date, al.event, u.username, u.full_name FROM access_logs al
                                  LEFT JOIN users u ON al.user_id=u.id ORDER BY al.id DESC LIMIT 50""").fetchall()
    s = _get_all_settings(conn, session.get('company_id', 1))
    invoice_example = f"{s['invoice_prefix']}/2026/{int(s['next_invoice_number'] or 1):04d}" if s['invoice_prefix'] else ''
    role_sugg = _role_suggestions(conn)
    conn.close()
    return render_template('settings.html', company_name=s.get('company_name') or get_company_name(session.get('company_id', 1)),
                            users=users, total_users=total_users, admin_users=admin_users, readonly_users=readonly_users,
                            limited_users=limited_users, inactive_users=inactive_users, role_counts=role_counts,
                            access_logs=access_logs, module_list=MODULE_LIST, role_suggestions=role_sugg,
                            s=s, invoice_example=invoice_example, active='settings', active_settings_tab='users',
                            user_error=error, reopen_add_user=reopen_add_user, reopen_edit_user_id=reopen_edit_user_id)

@app.route('/settings/users/add', methods=['POST'])
def add_user():
    from werkzeug.security import generate_password_hash
    import datetime, sqlite3, secrets
    f = request.form
    conn = get_db()
    access_level = f.get('access_level') or 'Read Only'
    modules, modules_err = _modules_from_form(request.form, access_level)
    if modules_err:
        return _render_settings_users_error(conn, modules_err, reopen_add_user=True)
    sal_ids, sal_err = _salary_scope_from_form(conn, request.form, modules)
    if sal_err:
        return _render_settings_users_error(conn, sal_err, reopen_add_user=True)
    # A password is optional — leave it blank and the user signs in with mobile OTP only;
    # set one here and they can also sign in the normal username+password way.
    pw = f.get('password') or ''
    pw_hash = generate_password_hash(pw) if pw else generate_password_hash(secrets.token_hex(16))
    # Username IS the mobile number now, same rule Sign Up enforces (app_signup()'s own comment) —
    # this form no longer asks for Username and Mobile Number as two separately-typed fields that
    # could disagree; the one required field here becomes both.
    username = (f.get('username') or '').strip()
    full_name = (f.get('full_name') or '').strip()
    email = (f.get('email') or '').strip() or None
    # Full Name is mandatory — it's what the greeting/avatar on every /app/ page actually shows
    # (app_dashboard()'s own comment on this: falling back to username, now a phone number, reads
    # as "Hi 9668348621" instead of a real name), so an account created without one would show a
    # phone number as its "name" everywhere until someone went back and filled this in by hand.
    if not full_name:
        return _render_settings_users_error(conn, 'Full Name is required.', reopen_add_user=True)
    if pw and len(pw) < 8:
        return _render_settings_users_error(conn, 'Password must be at least 8 characters (or leave it blank for OTP-only login).', reopen_add_user=True)
    if _is_admin_role(f.get('role')) and (f.get('status') or 'Active') != 'Inactive' and _other_active_administrator(conn):
        return _render_settings_users_error(conn, _ONE_ADMIN_MSG, reopen_add_user=True)
    if not USERNAME_MOBILE_RE.match(username):
        return _render_settings_users_error(conn, 'Mobile Number must be a valid 10-digit Indian mobile number.', reopen_add_user=True)
    phone = username
    dup = _duplicate_user_contact(conn, phone, email)
    if dup:
        return _render_settings_users_error(conn, dup, reopen_add_user=True)
    try:
        conn.execute("""INSERT INTO users (username, password_hash, role, is_admin, phone, full_name, email,
                        access_level, module_access, status, created_at, created_by, hide_trip_amounts,
                        salary_view_ids)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (username, pw_hash, f.get('role'),
                      1 if access_level == 'Full Access' else 0, phone,
                      full_name, email, access_level, modules,
                      f.get('status') or 'Active', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                      session.get('user_id'), _hide_trip_amounts_from_form(f, modules), sal_ids or None))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        return _render_settings_users_error(conn, f"Mobile Number \"{username}\" is already taken.", reopen_add_user=True)
    return _users_done()

@app.route('/settings/users/<int:user_id>/edit', methods=['POST'])
def edit_user(user_id):
    from werkzeug.security import generate_password_hash
    f = request.form
    conn = get_db()
    access_level = f.get('access_level') or 'Read Only'
    modules, modules_err = _modules_from_form(request.form, access_level)
    if modules_err:
        return _render_settings_users_error(conn, modules_err, reopen_edit_user_id=user_id)
    sal_ids, sal_err = _salary_scope_from_form(conn, request.form, modules, user_id=user_id)
    if sal_err:
        return _render_settings_users_error(conn, sal_err, reopen_edit_user_id=user_id)
    full_name = (f.get('full_name') or '').strip()
    phone = (f.get('phone') or '').strip() or None
    email = (f.get('email') or '').strip() or None
    # Same reasoning as add_user()'s own check — Full Name is what every /app/ page's greeting and
    # avatar actually show; leaving it blank on an edit would fall back to showing this account's
    # phone-number username instead of a real name.
    if not full_name:
        return _render_settings_users_error(conn, 'Full Name is required.', reopen_edit_user_id=user_id)
    if (f.get('password') or '') and len(f.get('password')) < 8:
        return _render_settings_users_error(conn, 'Password must be at least 8 characters.', reopen_edit_user_id=user_id)
    # Mobile Number is mandatory and must be a real 10-digit Indian mobile — same rule add_user()
    # and Sign Up enforce; the form's own `required` attribute alone isn't enough, since a direct
    # POST skips it.
    if not phone or not USERNAME_MOBILE_RE.match(phone):
        return _render_settings_users_error(conn, 'Mobile Number must be a valid 10-digit Indian mobile number.', reopen_edit_user_id=user_id)
    dup = _duplicate_user_contact(conn, phone, email, exclude_user_id=user_id)
    if dup:
        return _render_settings_users_error(conn, dup, reopen_edit_user_id=user_id)
    old = conn.execute("SELECT role, status FROM users WHERE id=?", (user_id,)).fetchone()
    new_is_admin = (f.get('role') or '').strip().lower() == 'administrator' and (f.get('status') or 'Active') != 'Inactive'
    was_active_admin = bool(old) and _is_admin_role(old['role']) and (old['status'] or 'Active') != 'Inactive'
    if new_is_admin and not was_active_admin and _other_active_administrator(conn, exclude_user_id=user_id):
        return _render_settings_users_error(conn, _ONE_ADMIN_MSG, reopen_edit_user_id=user_id)
    if old and (old['role'] or '').strip().lower() == 'administrator' and not new_is_admin and _administrator_count(conn, exclude_user_id=user_id) == 0:
        return _render_settings_users_error(conn, "This is the last active Administrator — keep their role as Administrator and Active, or make someone else an Administrator first.", reopen_edit_user_id=user_id)
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""UPDATE users SET full_name=?, role=?, phone=?, email=?, access_level=?, module_access=?,
                    status=?, is_admin=?, updated_by=?, updated_at=?, hide_trip_amounts=?,
                    salary_view_ids=? WHERE id=?""",
                 (full_name, f.get('role'), phone,
                  email, access_level, modules, f.get('status') or 'Active',
                  1 if access_level == 'Full Access' else 0, session.get('user_id'), _now,
                  _hide_trip_amounts_from_form(f, modules), sal_ids or None, user_id))
    # Leave the password untouched unless a new one was actually typed in.
    new_pw = f.get('password') or ''
    if new_pw:
        conn.execute("UPDATE users SET password_hash=?, updated_by=?, updated_at=? WHERE id=?",
                     (generate_password_hash(new_pw), session.get('user_id'), _now, user_id))
    conn.commit()
    conn.close()
    return _users_done()

@app.route('/settings/users/<int:user_id>/make-administrator', methods=['POST'])
def make_administrator(user_id):
    """Hands the single Administrator seat to another ACTIVE user in one step: that user becomes the Administrator and the
    current Administrator becomes a Manager (Full Access) — so there is never a moment with two Administrators or none.
    Administrator-only (see _ADMIN_ONLY_ENDPOINTS)."""
    conn = get_db()
    me = session.get('user_id')
    target = conn.execute("SELECT id, full_name, username, status FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        return _render_settings_users_error(conn, 'That user no longer exists.')
    if user_id == me:
        return _render_settings_users_error(conn, 'You are already the Administrator.')
    if (target['status'] or 'Active') == 'Inactive':
        return _render_settings_users_error(conn, 'Make this user Active first — an inactive user cannot be the Administrator.')
    _now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # demote first, then promote, in one transaction (the database allows only one active Administrator)
    conn.execute("UPDATE users SET role='Manager', access_level='Full Access', module_access=NULL, hide_trip_amounts=0, salary_view_ids=NULL, is_admin=1, updated_by=?, updated_at=? WHERE id=?", (me, _now, me))
    conn.execute("UPDATE users SET role='Administrator', access_level='Full Access', module_access=NULL, hide_trip_amounts=0, salary_view_ids=NULL, is_admin=1, status='Active', updated_by=?, updated_at=? WHERE id=?", (me, _now, user_id))
    conn.execute("INSERT INTO access_logs (user_id, event, date) VALUES (?,?,?)", (me, f"Administrator role handed to {target['full_name'] or target['username']}", _now))
    conn.commit()
    conn.close()
    return _users_done()

@app.route('/settings/users/<int:user_id>/phone', methods=['POST'])
def update_user_phone(user_id):
    conn = get_db()
    phone = (request.form.get('phone') or '').strip() or None
    if phone and not USERNAME_MOBILE_RE.match(phone):
        conn.close()
        return redirect(url_for('settings_page', tab='users'))
    if _duplicate_user_contact(conn, phone, None, exclude_user_id=user_id):
        conn.close()
        return redirect(url_for('settings_page', tab='users'))
    conn.execute("UPDATE users SET phone=?, updated_by=?, updated_at=? WHERE id=?",
                 (phone, session.get('user_id'),
                  datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('settings_page', tab='users'))

@app.route('/settings/users/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    conn = get_db()
    target = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if user_id == session.get('user_id') or (target and (target['role'] or '').strip().lower() == 'administrator' and _administrator_count(conn, exclude_user_id=user_id) == 0):
        # never delete yourself or the last Administrator — nobody would be able to manage users or settings any more
        return _render_settings_users_error(conn, "You can't delete your own account or the last Administrator.")
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.execute("DELETE FROM access_logs WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return _users_done()

@app.route('/settings/users/export')
def export_users():
    import openpyxl, io
    from flask import send_file
    conn = get_db()
    users, *_ = _users_with_stats(conn)
    conn.close()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Users'
    ws.append(['Full Name', 'Username', 'Role', 'Access Level', 'Mobile', 'Email', 'Status', 'Last Login'])
    for u in users:
        ws.append([u['full_name'] or '', u['username'], u['role'] or '', u['access_level'] or '',
                   u['phone'] or '', u['email'] or '', u['status'] or '', u['last_login'] or ''])
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='users.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def _route_analytics_data(conn, date_from, date_to, from_f, to_f, type_f, vehicle_no=''):
    """Shared per-route revenue/cost/profit rollup — both the Best Routes and Route Rates tabs
    draw from the `trips` list this returns, so app_route_analytics() (mobile) reads the exact
    same numbers as route_analytics() (website) for the fields both surfaces show. Extracted
    unchanged from route_analytics() below; that route now just calls this and continues with
    its own Route Rates computation using the returned trips list. vehicle_no is optional
    (default '' = all vehicles, every existing caller unaffected) — added for the native
    Dashboard's Top Routes card, which needs the same real per-route numbers filtered to one
    vehicle when the Dashboard's own vehicle filter is set; the website's Route Analytics page
    itself has no vehicle filter today, so this stays unused there."""
    # All origins/destinations ever seen (unfiltered), for the From/To dropdowns — independent of
    # the current filters, and independent of each other, so you can filter by From alone, To
    # alone, or both together for one exact route.
    all_routes_rows = conn.execute("SELECT DISTINCT from_loc, to_loc FROM trips").fetchall()
    all_from_list = sorted(set(_clean_loc(r['from_loc']) for r in all_routes_rows if r['from_loc']))
    all_to_list = sorted(set(_clean_loc(r['to_loc']) for r in all_routes_rows if r['to_loc']))

    trip_query = """SELECT t.id, t.from_loc, t.to_loc, t.type, t.rate, t.rate_type, t.billed_amount, t.fuel_amount, t.driver_adv_amount,
                    t.toll, t.parking, t.agent_commission, t.builty_expense, t.conductor_expense, t.fine, t.labour_charges,
                    t.puncture, t.urea, t.loading_expense, t.unloading_expense, t.wear_tear, t.weighbridge_charges,
                    t.other_expense, t.permit_charges, t.fixed_rate_amount, t.owner_rate, t.owner_rate_type, t.owner_fixed_amount, t.quantity
                    FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
                    WHERE t.date>=? AND t.date<=? AND (t.lr_number IS NULL OR t.lr_number NOT LIKE 'Empty%')"""
    params = [date_from, date_to]
    if type_f:
        trip_query += " AND t.type=?"
        params.append(type_f)
    if vehicle_no:
        trip_query += " AND v.vehicle_no=?"
        params.append(vehicle_no)
    trips = conn.execute(trip_query, params).fetchall()
    route_toll_map = _toll_by_trip(conn, [t['id'] for t in trips])

    groups = {}
    for t in trips:
        cf, ct = _clean_loc(t['from_loc']), _clean_loc(t['to_loc'])
        if (from_f and cf != from_f) or (to_f and ct != to_f):
            continue
        d = groups.setdefault((cf, ct), {'trips': 0, 'revenue': 0, 'cost': 0})
        d['trips'] += 1
        d['revenue'] += t['billed_amount'] or 0
        # Fuel/driver-advance on a Hired trip is the owner's own money, already covered by the
        # contracted freight added below — including both here double-counts it (same rule as the
        # Dashboard/Ledger/Invoice Center fixes: it's not a real company cost unless a different,
        # explicitly-tagged vendor paid it, which this per-route rollup has no vendor context for).
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        cost = (fuel_and_adv + _trip_toll(t, route_toll_map) + (t['parking'] or 0) +
                (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) + (t['fine'] or 0) +
                (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) + (t['loading_expense'] or 0) +
                (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) + (t['weighbridge_charges'] or 0) +
                (t['other_expense'] or 0) + (t['permit_charges'] or 0))
        if t['type'] == VEHICLE_TYPE_HIRED:
            cost += (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        d['cost'] += cost

    route_rows = []
    for (cf, ct), d in groups.items():
        profit = d['revenue'] - d['cost']
        margin = round(profit / d['revenue'] * 100, 1) if d['revenue'] else 0
        route_rows.append({'route': f"{cf} → {ct}", 'clean_from': cf, 'clean_to': ct, 'trips': d['trips'],
                            'revenue': d['revenue'], 'cost': d['cost'], 'profit': profit, 'margin': margin})

    total_routes = len(route_rows)
    total_trips_sel = sum(r['trips'] for r in route_rows)
    total_revenue_sel = sum(r['revenue'] for r in route_rows)
    total_cost_sel = sum(r['cost'] for r in route_rows)
    avg_margin = round((total_revenue_sel - total_cost_sel) / total_revenue_sel * 100, 1) if total_revenue_sel else 0

    rate_values = [t['rate'] for t in trips if t['rate_type'] == 'PER_MT' and (t['rate'] or 0) > 0
                   and (not from_f or _clean_loc(t['from_loc']) == from_f) and (not to_f or _clean_loc(t['to_loc']) == to_f)]
    avg_rate = round(sum(rate_values) / len(rate_values), 0) if rate_values else 0

    top5_by_margin = sorted(route_rows, key=lambda r: r['margin'], reverse=True)[:5]
    top5_by_revenue = sorted(route_rows, key=lambda r: r['revenue'], reverse=True)[:5]
    top_rev_max = max([r['revenue'] for r in top5_by_revenue], default=1) or 1
    for r in top5_by_revenue:
        r['pct'] = round(r['revenue'] / top_rev_max * 100, 1)

    return {
        'trips': trips, 'route_rows': route_rows, 'all_from_list': all_from_list, 'all_to_list': all_to_list,
        'total_routes': total_routes, 'total_trips_sel': total_trips_sel, 'total_revenue_sel': total_revenue_sel,
        'total_cost_sel': total_cost_sel, 'avg_margin': avg_margin, 'avg_rate': avg_rate,
        'top5_by_margin': top5_by_margin, 'top5_by_revenue': top5_by_revenue,
    }

def _route_rates_data(trips, from_f='', to_f=''):
    """Shared per-(from,to,vehicle-type) PER_MT rate-range rollup — the Route Rates tab's own
    real numbers, extracted unchanged from route_analytics() below so app_analytics()'s own
    Route Rates tab (mobile) reads the exact same Highest/Average/Lowest rate, Driver Advance,
    and Fuel Taken figures the website shows, never a second hand-written copy. Takes the
    already date-range/vehicle-type-filtered `trips` list _route_analytics_data() returns (not a
    fresh query) — so the two tabs can never disagree on which trips are 'in scope' before this
    function's own from_f/to_f narrowing. Does not touch conn; rr_groups is returned too since
    the website's own Rate Trend chart / Own-vs-Hired donut (mobile doesn't show these — real
    future scope, not this pass) need the raw per-route rate lists, not just the summarized rows."""
    rr_groups = {}
    rr_driver_adv_groups = {}
    rr_fuel_groups = {}
    for t in trips:
        if t['rate_type'] != 'PER_MT' or not t['rate'] or t['rate'] <= 0:
            continue
        cf, ct = _clean_loc(t['from_loc']), _clean_loc(t['to_loc'])
        if (from_f and cf != from_f) or (to_f and ct != to_f):
            continue
        rr_groups.setdefault((cf, ct, t['type']), []).append(t['rate'])
        if t['driver_adv_amount']:
            rr_driver_adv_groups.setdefault((cf, ct, t['type']), []).append(t['driver_adv_amount'])
        if t['fuel_amount']:
            rr_fuel_groups.setdefault((cf, ct, t['type']), []).append(t['fuel_amount'])

    rr_rows = []
    for (cf, ct, ttype), rates in rr_groups.items():
        da = rr_driver_adv_groups.get((cf, ct, ttype), [])
        fu = rr_fuel_groups.get((cf, ct, ttype), [])
        rr_rows.append({'clean_from': cf, 'clean_to': ct, 'type': ttype, 'highest': max(rates),
                         'average': sum(rates) / len(rates), 'lowest': min(rates), 'trips': len(rates),
                         'da_lowest': min(da) if da else 0, 'da_average': (sum(da) / len(da)) if da else 0, 'da_highest': max(da) if da else 0,
                         'fuel_lowest': min(fu) if fu else 0, 'fuel_average': (sum(fu) / len(fu)) if fu else 0, 'fuel_highest': max(fu) if fu else 0})
    rr_rows.sort(key=lambda r: (r['clean_from'], r['clean_to']))

    # Overall Driver Advance / Fuel Taken KPI cards — every trip in the current filters (not just
    # the PER_MT-rate subset that feeds the rate table), since these are real costs paid regardless
    # of how the party was charged. Zero/blank amounts are excluded — "lowest" should mean the
    # smallest amount actually given, not an untouched trip.
    overall_driver_adv = [t['driver_adv_amount'] for t in trips if t['driver_adv_amount']
                           and (not from_f or _clean_loc(t['from_loc']) == from_f) and (not to_f or _clean_loc(t['to_loc']) == to_f)]
    overall_fuel = [t['fuel_amount'] for t in trips if t['fuel_amount']
                     and (not from_f or _clean_loc(t['from_loc']) == from_f) and (not to_f or _clean_loc(t['to_loc']) == to_f)]
    da_lowest = min(overall_driver_adv) if overall_driver_adv else 0
    da_average = round(sum(overall_driver_adv) / len(overall_driver_adv)) if overall_driver_adv else 0
    da_highest = max(overall_driver_adv) if overall_driver_adv else 0
    fuel_lowest = min(overall_fuel) if overall_fuel else 0
    fuel_average = round(sum(overall_fuel) / len(overall_fuel)) if overall_fuel else 0
    fuel_highest = max(overall_fuel) if overall_fuel else 0

    all_rates_flat = [r for rates in rr_groups.values() for r in rates]
    highest_entry = None
    lowest_entry = None
    for (cf, ct, ttype), rates in rr_groups.items():
        h, l = max(rates), min(rates)
        if highest_entry is None or h > highest_entry[2]:
            highest_entry = (cf, ct, h)
        if lowest_entry is None or l < lowest_entry[2]:
            lowest_entry = (cf, ct, l)
    rr_avg_rate_overall = round(sum(all_rates_flat) / len(all_rates_flat), 0) if all_rates_flat else 0
    rr_total_routes = len(rr_groups)

    return {
        'rr_groups': rr_groups, 'rr_rows': rr_rows, 'rr_total_count': len(rr_rows),
        'highest_entry': highest_entry, 'lowest_entry': lowest_entry,
        'rr_avg_rate_overall': rr_avg_rate_overall, 'rr_total_routes': rr_total_routes,
        'da_lowest': da_lowest, 'da_average': da_average, 'da_highest': da_highest,
        'fuel_lowest': fuel_lowest, 'fuel_average': fuel_average, 'fuel_highest': fuel_highest,
    }


# ============================================================================
# Route Finder — live route lookup on OpenStreetMap (providers/osm_routes.py).
#
# Type a From and a To, press Search, and get the routes a maps app would show: distance, drive
# time, which highways (NH/SH) each uses, and — for the chosen route — the toll plazas and major
# towns along it, drawn on a map. Everything is looked up on demand; NOTHING is stored in the
# database (no new tables), only a short in-memory cache below so repeating a search, or flipping
# between the alternatives, doesn't ask the free public OSM servers again. The cache is per web
# worker, which is fine — a miss just means one more lookup.
# ============================================================================
_RF_CACHE = {}
_RF_CACHE_LOCK = threading.Lock()
_RF_CACHE_MAX = 300
# At most this many lookups talk to OpenStreetMap at once, across all users of this worker.
_RF_SLOTS = threading.BoundedSemaphore(3)
_RF_BUSY_MSG = 'Several route searches are running right now — wait a few seconds and press Search again.'

def _rf_cache_get(key):
    with _RF_CACHE_LOCK:
        hit = _RF_CACHE.get(key)
        if hit and hit[0] > _time.time():
            return hit[1]
        _RF_CACHE.pop(key, None)
    return None

def _rf_cache_put(key, value, ttl):
    with _RF_CACHE_LOCK:
        if len(_RF_CACHE) >= _RF_CACHE_MAX:
            now = _time.time()
            for k in [k for k, (exp, _v) in _RF_CACHE.items() if exp <= now]:
                del _RF_CACHE[k]
            if len(_RF_CACHE) >= _RF_CACHE_MAX:
                for k in sorted(_RF_CACHE, key=lambda k: _RF_CACHE[k][0])[:_RF_CACHE_MAX // 4]:
                    del _RF_CACHE[k]
        _RF_CACHE[key] = (_time.time() + ttl, value)

def _rf_text(v):
    return re.sub(r'\s+', ' ', (v or '').strip())[:120]

def _rf_coord(lat, lng):
    """(lat, lng) floats if both parse and are on Earth, else None."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None

def _route_finder_texts(conn):
    """Route Analytics clean key ("RSP") -> the best searchable text for it, so a row's map link
    can prefill the Route Finder: the most common raw spelling in trips, cleaned of the doubled-up
    "RSP RSP null" shape (see osm_routes.clean_search_text). Used ONLY to prefill links from
    Route Analytics — the From/To boxes themselves search all of India as you type (see
    route_finder_suggest), not a fixed list of your own trip places."""
    rows = conn.execute("""SELECT from_loc AS loc, COUNT(*) AS n FROM trips
                           WHERE from_loc IS NOT NULL AND TRIM(from_loc) != '' GROUP BY from_loc
                           UNION ALL
                           SELECT to_loc, COUNT(*) FROM trips
                           WHERE to_loc IS NOT NULL AND TRIM(to_loc) != '' GROUP BY to_loc""").fetchall()
    by_key = {}
    for r in rows:
        key = _clean_loc(r['loc'])
        text = osm_routes.clean_search_text(r['loc'])
        if key and text:
            counts = by_key.setdefault(key, {})
            counts[text] = counts.get(text, 0) + r['n']
    return {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in by_key.items()}

def _route_finder_ctx(conn):
    return {'rf_texts': _route_finder_texts(conn),
            'rf_from': _rf_text(request.args.get('rf_from')), 'rf_to': _rf_text(request.args.get('rf_to'))}

def _route_history(conn, from_text, to_text):
    """What your own trips on the same route recorded as toll — the one real, per-route toll
    figure in the system (OpenStreetMap has no toll amounts at all). Matches on the same
    cleaned From/To the Route Analytics page groups by; None when no trip has run this route."""
    cf, ct = _clean_loc(from_text), _clean_loc(to_text)
    if not cf or not ct:
        return None
    rows = conn.execute("""SELECT id, from_loc, to_loc, toll FROM trips
                           WHERE from_loc IS NOT NULL AND to_loc IS NOT NULL
                           AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')""").fetchall()
    matched = [t for t in rows if _clean_loc(t['from_loc']) == cf and _clean_loc(t['to_loc']) == ct]
    if not matched:
        return None
    toll_map = _toll_by_trip(conn, [t['id'] for t in matched])
    tolls = [x for x in (_trip_toll(t, toll_map) for t in matched) if x > 0]
    return {'trips': len(matched), 'trips_with_toll': len(tolls),
            'avg_toll': round(sum(tolls) / len(tolls)) if tolls else 0,
            'min_toll': round(min(tolls)) if tolls else 0, 'max_toll': round(max(tolls)) if tolls else 0,
            'from_key': cf, 'to_key': ct}

def _rf_geocode(text, bias_center=None):
    bias_key = (round(bias_center[0]), round(bias_center[1])) if bias_center else None
    key = ('geo', text.lower(), bias_key)
    hit = _rf_cache_get(key)
    if hit is not None:
        return hit
    cands = osm_routes.geocode_candidates(text, bias_center=bias_center)
    if cands:
        _rf_cache_put(key, cands, 24 * 3600)
    return cands

# Separate from _RF_SLOTS: suggestions fire while typing and are quick, so they must never be
# starved by (or starve) the slower route lookups.
_RF_SUGGEST_SLOTS = threading.BoundedSemaphore(4)

@app.route('/route-finder/suggest')
def route_finder_suggest():
    """Type-ahead for the From/To boxes: places anywhere in India matching what's been typed so
    far (3+ letters), from Photon over OpenStreetMap data. Server-side rather than called from the
    browser so it's login-gated, cached and identifies this app to Photon. `lat`/`lon` (optional)
    bias results towards the other end of the trip."""
    q = _rf_text(request.args.get('q'))
    if len(q) < osm_routes.SUGGEST_MIN_CHARS:
        return jsonify({'ok': True, 'results': []})
    bias = _rf_coord(request.args.get('lat'), request.args.get('lon'))
    key = ('sug', q.lower(), (round(bias[0]), round(bias[1])) if bias else None)
    hit = _rf_cache_get(key)
    if hit is not None:
        return jsonify({'ok': True, 'results': hit})
    if not _RF_SUGGEST_SLOTS.acquire(blocking=False):
        return jsonify({'ok': True, 'results': [], 'busy': True})
    try:
        results = osm_routes.suggest_places(q, bias_center=bias)
    except osm_routes.RouteLookupError as e:
        return jsonify({'ok': False, 'error': str(e)}), 502
    except Exception:
        app.logger.exception('route_finder_suggest failed')
        return jsonify({'ok': False, 'error': 'Place search hit a problem. You can still type the full place name and press Search.'}), 500
    finally:
        _RF_SUGGEST_SLOTS.release()
    _rf_cache_put(key, results, 3600)
    return jsonify({'ok': True, 'results': results})

@app.route('/route-finder/search')
def route_finder_search():
    """JSON for one From/To search. `from`/`to` are place names; `from_lat`+`from_lng` (and
    `to_*`) pin an end to a specific match the user picked from "Not the right place?", skipping
    the name lookup for that end."""
    ft, tt = _rf_text(request.args.get('from')), _rf_text(request.args.get('to'))
    if not ft or not tt:
        return jsonify({'ok': False, 'error': 'Enter both a From and a To place.'}), 400
    pinned_from = _rf_coord(request.args.get('from_lat'), request.args.get('from_lng'))
    pinned_to = _rf_coord(request.args.get('to_lat'), request.args.get('to_lng'))
    if not _RF_SLOTS.acquire(blocking=False):
        return jsonify({'ok': False, 'error': _RF_BUSY_MSG}), 429
    try:
        ends = {}
        # From first, then To biased towards it — both ends of a trip are usually in one region,
        # which settles ambiguous local names ("RSP", "IDC") without asking.
        for side, text, pinned, other in (('from', ft, pinned_from, None), ('to', tt, pinned_to, None)):
            if pinned:
                name = _rf_text(request.args.get(f'{side}_name')) or text
                ends[side] = {'text': text, 'candidates': [],
                              'chosen': {'lat': pinned[0], 'lng': pinned[1], 'display_name': name}}
                continue
            bias = None
            if side == 'to' and 'from' in ends:
                c = ends['from']['chosen']
                bias = (c['lat'], c['lng'])
            cands = _rf_geocode(text, bias_center=bias)
            if not cands:
                return jsonify({'ok': False, 'error': f'Couldn\'t find "{text}" on the map. Try adding the town or state, e.g. "{text}, Odisha".'}), 404
            ends[side] = {'text': text, 'candidates': cands, 'chosen': cands[0]}
        a, b = ends['from']['chosen'], ends['to']['chosen']
        straight = osm_routes.haversine_km(a['lat'], a['lng'], b['lat'], b['lng'])
        if straight < 0.2:
            return jsonify({'ok': False, 'error': 'From and To are the same place.'}), 400
        rkey = ('route', round(a['lat'], 4), round(a['lng'], 4), round(b['lat'], 4), round(b['lng'], 4))
        routes = _rf_cache_get(rkey)
        if routes is None:
            routes = osm_routes.search_routes(a, b)
            _rf_cache_put(rkey, routes, 3600)
    except osm_routes.RouteLookupError as e:
        return jsonify({'ok': False, 'error': str(e)}), 502
    except Exception:
        app.logger.exception('route_finder_search failed')
        return jsonify({'ok': False, 'error': 'Something went wrong looking up that route. Please try again.'}), 500
    finally:
        _RF_SLOTS.release()
    conn = get_db()
    history = _route_history(conn, ft, tt)
    conn.close()
    return jsonify({'ok': True, 'from': ends['from'], 'to': ends['to'], 'straight_km': round(straight, 1),
                    'routes': routes, 'history': history})

@app.route('/route-finder/extras', methods=['POST'])
def route_finder_extras():
    """Toll plazas + major towns along ONE route, sent the route's own simplified path back (the
    one /route-finder/search returned) so this works whichever web worker answers."""
    data = request.get_json(silent=True) or {}
    geom, dist, hws = data.get('geometry'), data.get('distance_km'), data.get('highways') or []
    try:
        dist = float(dist)
        ok_geom = isinstance(geom, list) and 2 <= len(geom) <= 4000 and all(
            isinstance(p, (list, tuple)) and len(p) == 2 and _rf_coord(p[0], p[1]) for p in geom)
        ok_hw = isinstance(hws, list) and len(hws) <= 500 and all(
            isinstance(h, dict) and isinstance(h.get('from_km'), (int, float)) and isinstance(h.get('km'), (int, float))
            and (h.get('ref') is None or isinstance(h.get('ref'), str)) for h in hws)
        if not (ok_geom and ok_hw and 0 < dist < 6000):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'That route could not be read.'}), 400
    poly = [[float(p[0]), float(p[1])] for p in geom]
    hws = [{'ref': h.get('ref'), 'km': float(h['km']), 'from_km': float(h['from_km'])} for h in hws]
    # part: 'places' = towns from the built-in list (instant, no network); 'tolls' / 'fuel' = the two small
    # Overpass lookups, asked for one after another by the browser so a failure of one hides only itself;
    # no part = the original all-in-one answer.
    part = data.get('part')
    if part not in (None, 'places', 'tolls', 'fuel'):
        return jsonify({'ok': False, 'error': 'That route could not be read.'}), 400
    key = ('extras', part or 'all', hashlib.sha1(json.dumps(poly).encode()).hexdigest(), round(dist, 1))
    extras = _rf_cache_get(key)
    if extras is None:
        if part == 'places':
            extras = osm_routes.route_places(poly, dist)         # local: needs no slot and cannot be rate-limited
        else:
            if not _RF_SLOTS.acquire(blocking=False):
                return jsonify({'ok': False, 'error': _RF_BUSY_MSG}), 429
            try:
                if part == 'tolls':
                    extras = osm_routes.route_tolls(poly, dist, hws)
                elif part == 'fuel':
                    extras = osm_routes.route_fuel(poly, dist, hws)
                else:
                    extras = osm_routes.route_extras(poly, dist, hws)
            except Exception:
                app.logger.exception('route_finder_extras failed')
                return jsonify({'ok': False, 'error': 'Something went wrong looking up toll plazas. Please try again.'}), 500
            finally:
                _RF_SLOTS.release()
        if extras['extras_ok']:
            _rf_cache_put(key, extras, 3600)   # failures aren't cached, so Retry really retries
        else:
            app.logger.warning('route_finder_extras(%s): lookup failed: %s', part or 'all', extras['note'])
    return jsonify(dict(extras, ok=True, part=part))

@app.route('/route-analytics')
def route_analytics():
    conn = get_db()
    tab = request.args.get('tab', 'best-routes')
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    from_f = request.args.get('from', '')
    to_f = request.args.get('to', '')
    type_f = request.args.get('vehicle_type', '')

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    _ra = _route_analytics_data(conn, date_from, date_to, from_f, to_f, type_f)
    trips = _ra['trips']; route_rows = _ra['route_rows']
    all_from_list = _ra['all_from_list']; all_to_list = _ra['all_to_list']
    total_routes = _ra['total_routes']; total_trips_sel = _ra['total_trips_sel']
    total_revenue_sel = _ra['total_revenue_sel']
    avg_margin = _ra['avg_margin']; avg_rate = _ra['avg_rate']
    top5_by_margin = _ra['top5_by_margin']; top5_by_revenue = _ra['top5_by_revenue']

    # Route Summary table — same rows, paginated, sorted by revenue.
    route_rows.sort(key=lambda r: r['revenue'], reverse=True)
    page, per_page, total_pages = _paginate(request.args.get('page'), request.args.get('per_page'), total_routes,
                                             per_page_options=(10, 25, 50, 100), default_per_page=10)
    summary_rows = route_rows[(page - 1) * per_page: page * per_page]
    page_tokens = _page_tokens(page, total_pages)

    # ---------- Route Rates tab: rate stats grouped by (from, to, vehicle type) ----------
    _rr = _route_rates_data(trips, from_f, to_f)
    rr_groups = _rr['rr_groups']
    rr_rows = _rr['rr_rows']
    da_lowest, da_average, da_highest = _rr['da_lowest'], _rr['da_average'], _rr['da_highest']
    fuel_lowest, fuel_average, fuel_highest = _rr['fuel_lowest'], _rr['fuel_average'], _rr['fuel_highest']
    highest_entry, lowest_entry = _rr['highest_entry'], _rr['lowest_entry']
    rr_avg_rate_overall, rr_total_routes = _rr['rr_avg_rate_overall'], _rr['rr_total_routes']

    # Route Rates table — its own page number (rr_page) but shares the same per_page value/param
    # as the Best Routes summary table above, same two-pagers-one-per_page pattern Performance
    # already uses for its Driver/Vehicle tabs (d_page/v_page sharing one per_page).
    rr_total_count = len(rr_rows)
    rr_page, rr_per_page, rr_total_pages = _paginate(request.args.get('rr_page'), request.args.get('per_page'), rr_total_count,
                                                       per_page_options=(10, 25, 50, 100), default_per_page=10)
    rr_rows_page = rr_rows[(rr_page - 1) * rr_per_page: rr_page * rr_per_page]
    rr_page_tokens = _page_tokens(rr_page, rr_total_pages)

    # Own/Hired rate donut. Vehicle type is a strict 2-way own/hired split now, so unlike the old
    # 3-way Line/Local/Market version this never needs to fold one bucket's remainder into another.
    all_rates_flat = [r for rates in rr_groups.values() for r in rates]
    own_rates = [r for (cf, ct, tt), rates in rr_groups.items() if tt == VEHICLE_TYPE_OWN for r in rates]
    hired_rates = [r for (cf, ct, tt), rates in rr_groups.items() if tt == VEHICLE_TYPE_HIRED for r in rates]
    other_rates = [r for (cf, ct, tt), rates in rr_groups.items() if tt not in (VEHICLE_TYPE_OWN, VEHICLE_TYPE_HIRED) for r in rates]
    own_avg = round(sum(own_rates) / len(own_rates), 0) if own_rates else 0
    hired_avg = round(sum(hired_rates) / len(hired_rates), 0) if hired_rates else 0
    own_pct = round(len(own_rates) / len(all_rates_flat) * 100, 0) if all_rates_flat else 0
    hired_pct = round(len(hired_rates) / len(all_rates_flat) * 100, 0) if all_rates_flat else 0
    # Any stray/unexpected type (pre-migration legacy data, say) gets folded into Hired's slice
    # visually (better than silently vanishing) but never into its average — other_rates existing
    # at all would be a real data gap worth investigating, not expected in normal operation.
    hired_pct += round(len(other_rates) / len(all_rates_flat) * 100, 0) if all_rates_flat else 0

    rt_query = """SELECT substr(date,1,7) as month, AVG(rate) as avg_rate FROM trips
                  WHERE rate_type='PER_MT' AND rate > 0 AND date>=? AND date<=?
                  AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')"""
    rt_params = [date_from, date_to]
    if type_f:
        rt_query += " AND type=?"
        rt_params.append(type_f)
    rt_query += " GROUP BY month ORDER BY month"
    monthly_trips = conn.execute(rt_query, rt_params).fetchall()
    trend_points = [{'month': m['month'], 'rate': round(m['avg_rate'] or 0)} for m in monthly_trips]
    svg_points = ''
    svg_labels = []
    if trend_points:
        rates = [p['rate'] for p in trend_points]
        rmin, rmax = min(rates), max(rates)
        rrange = (rmax - rmin) or 1
        n = len(trend_points)
        chart_w, chart_h = 400, 140
        coords = []
        for i, p in enumerate(trend_points):
            x = (i / (n - 1) * chart_w) if n > 1 else chart_w / 2
            y = chart_h - ((p['rate'] - rmin) / rrange * (chart_h - 20)) - 10
            coords.append((round(x, 1), round(y, 1)))
        svg_points = ' '.join(f"{x},{y}" for x, y in coords)
        svg_labels = [p['month'][5:] + '/' + p['month'][2:4] for p in trend_points]

    rf_ctx = _route_finder_ctx(conn)
    conn.close()

    from urllib.parse import urlencode
    base_params = request.args.to_dict()
    base_params.pop('page', None)
    base_params.pop('per_page', None)
    base_qs = urlencode(base_params)

    rr_base_params = request.args.to_dict()
    rr_base_params.pop('rr_page', None)
    rr_base_params.pop('per_page', None)
    rr_base_qs = urlencode(rr_base_params)

    return render_template('route_analytics.html',
        tab=tab, f_date_from=date_from, f_date_to=date_to, f_from=from_f, f_to=to_f, f_vehicle_type=type_f,
        all_from_list=all_from_list, all_to_list=all_to_list,
        total_routes=total_routes, total_trips_sel=total_trips_sel, total_revenue_sel=total_revenue_sel,
        avg_rate=avg_rate, avg_margin=avg_margin,
        top5_by_margin=top5_by_margin, top5_by_revenue=top5_by_revenue,
        summary_rows=summary_rows, page=page, per_page=per_page, total_pages=total_pages,
        page_tokens=page_tokens, base_qs=base_qs,
        rr_rows=rr_rows_page, rr_total_count=rr_total_count, rr_page=rr_page, rr_per_page=rr_per_page,
        rr_total_pages=rr_total_pages, rr_page_tokens=rr_page_tokens, rr_base_qs=rr_base_qs,
        highest_entry=highest_entry, lowest_entry=lowest_entry, rr_avg_rate_overall=rr_avg_rate_overall,
        rr_total_routes=rr_total_routes, own_avg=own_avg, hired_avg=hired_avg,
        own_pct=own_pct, hired_pct=hired_pct,
        da_lowest=da_lowest, da_average=da_average, da_highest=da_highest,
        fuel_lowest=fuel_lowest, fuel_average=fuel_average, fuel_highest=fuel_highest,
        trend_points=trend_points, svg_points=svg_points, svg_labels=svg_labels,
        active='route-analytics', **rf_ctx)

@app.route('/route-analytics/export')
def export_route_analytics():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    from_f = request.args.get('from', '')
    to_f = request.args.get('to', '')
    type_f = request.args.get('vehicle_type', '')
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    trip_query = """SELECT id, from_loc, to_loc, type, rate, rate_type, billed_amount, fuel_amount, driver_adv_amount,
                    toll, parking, agent_commission, builty_expense, conductor_expense, fine, labour_charges,
                    puncture, urea, loading_expense, unloading_expense, wear_tear, weighbridge_charges,
                    other_expense, permit_charges, fixed_rate_amount, owner_rate, owner_rate_type, owner_fixed_amount, quantity
                    FROM trips WHERE date>=? AND date<=? AND (lr_number IS NULL OR lr_number NOT LIKE 'Empty%')"""
    params = [date_from, date_to]
    if type_f:
        trip_query += " AND type=?"
        params.append(type_f)
    trips = conn.execute(trip_query, params).fetchall()
    route_toll_map = _toll_by_trip(conn, [t['id'] for t in trips])
    conn.close()

    groups = {}
    rr_groups = {}
    rr_driver_adv_groups = {}
    rr_fuel_groups = {}
    for t in trips:
        cf, ct = _clean_loc(t['from_loc']), _clean_loc(t['to_loc'])
        if (from_f and cf != from_f) or (to_f and ct != to_f):
            continue
        d = groups.setdefault((cf, ct), {'trips': 0, 'revenue': 0, 'cost': 0})
        d['trips'] += 1
        d['revenue'] += t['billed_amount'] or 0
        # Fuel/driver-advance on a Hired trip is the owner's own money, already covered by the
        # contracted freight added below — including both here double-counts it (same rule as the
        # Dashboard/Ledger/Invoice Center fixes: it's not a real company cost unless a different,
        # explicitly-tagged vendor paid it, which this per-route rollup has no vendor context for).
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        cost = (fuel_and_adv + _trip_toll(t, route_toll_map) + (t['parking'] or 0) +
                (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) + (t['fine'] or 0) +
                (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) + (t['loading_expense'] or 0) +
                (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) + (t['weighbridge_charges'] or 0) +
                (t['other_expense'] or 0) + (t['permit_charges'] or 0))
        if t['type'] == VEHICLE_TYPE_HIRED:
            cost += (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        d['cost'] += cost
        if t['rate_type'] == 'PER_MT' and (t['rate'] or 0) > 0:
            rr_groups.setdefault((cf, ct, t['type']), []).append(t['rate'])
            if t['driver_adv_amount']:
                rr_driver_adv_groups.setdefault((cf, ct, t['type']), []).append(t['driver_adv_amount'])
            if t['fuel_amount']:
                rr_fuel_groups.setdefault((cf, ct, t['type']), []).append(t['fuel_amount'])

    route_rows = []
    for (cf, ct), d in groups.items():
        profit = d['revenue'] - d['cost']
        margin = round(profit / d['revenue'] * 100, 1) if d['revenue'] else 0
        route_rows.append((f"{cf} → {ct}", d['trips'], d['revenue'], d['cost'], profit, margin))
    route_rows.sort(key=lambda r: r[2], reverse=True)

    rate_rows = []
    for (cf, ct, ttype), rates in rr_groups.items():
        da = rr_driver_adv_groups.get((cf, ct, ttype), [])
        fu = rr_fuel_groups.get((cf, ct, ttype), [])
        rate_rows.append((f"{cf} → {ct}", ttype, max(rates), sum(rates) / len(rates), min(rates), len(rates),
                           min(da) if da else 0, (sum(da) / len(da)) if da else 0, max(da) if da else 0,
                           min(fu) if fu else 0, (sum(fu) / len(fu)) if fu else 0, max(fu) if fu else 0))
    rate_rows.sort(key=lambda r: r[0])

    navy = "1B2A4A"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=navy)

    wb = Workbook()
    ws = wb.active
    ws.title = "Route Summary"
    headers = ["Route", "Trips", "Total Revenue", "Total Cost", "Profit", "Margin %"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, row in enumerate(route_rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)

    ws2 = wb.create_sheet("Route Rates")
    headers2 = ["Route", "Type", "Highest Rate", "Average Rate", "Lowest Rate", "Trips",
                "Driver Adv. Lowest", "Driver Adv. Average", "Driver Adv. Highest",
                "Fuel Lowest", "Fuel Average", "Fuel Highest"]
    for i, h in enumerate(headers2, 1):
        c = ws2.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, row in enumerate(rate_rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws2.cell(row=r_idx, column=c_idx, value=val)

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='route_analytics_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/idle-tracker')
def idle_tracker():
    args = request.args.to_dict()
    return redirect(url_for('fleet_utilization', **args))

@app.route('/empty-runs')
def empty_runs():
    args = request.args.to_dict()
    return redirect(url_for('fleet_utilization', **args))

def _fleet_utilization_data(conn, date_from, date_to, vehicle_f=''):
    """Real idle/active/empty-run rollup for Fleet Utilization — shared by fleet_utilization()
    (website) and app_fleet_utilization() (mobile). date_from/date_to/vehicle are the website's
    own real filters. Total KM / Total Fuel / Avg Fuel Efficiency are new here (the website page
    itself has never surfaced them) but are real sums of trips.actual_km/fuel_amount for the same
    own-fleet/period scope as everything else on this page — not fabricated, just not previously
    named as page metrics; same "additive real metric" call already made for Ledger/Expenses/
    Salary search. Returns a dict; does not close conn."""
    # From Date must never be after To Date — swap rather than let the math go negative.
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    d2 = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()

    # ---------- Idle tracker: active days = union of each trip's start..end_date span ----------
    # This whole page is scoped to the own fleet only (a Hired vehicle isn't this company's asset
    # to track utilization for) — there used to be a Line/Local sub-type filter here too, but with
    # vehicle type collapsed to a strict own/hired split, every row on this page is now the same
    # type by construction, so a same-value filter no longer means anything and was removed.
    query = "SELECT id, vehicle_no, type, registration_date FROM vehicles WHERE type = 'own'"
    params = []
    if vehicle_f:
        query += " AND vehicle_no = ?"
        params.append(vehicle_f)
    own_vehicles = conn.execute(query, params).fetchall()

    rows = []
    for v in own_vehicles:
        active_days, idle_days, total_days_v = _vehicle_active_idle_days(
            conn, v['id'], v['registration_date'], date_from, date_to)
        last_trip = conn.execute("SELECT MAX(date) FROM trips WHERE vehicle_id=?", (v['id'],)).fetchone()[0]
        status = 'Idle' if idle_days >= active_days else 'Active'
        empty_run_count = conn.execute("""SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND lr_number LIKE 'Empty%'
                                          AND date>=? AND date<=?""", (v['id'], date_from, date_to)).fetchone()[0]
        vehicle_trip_count = conn.execute("SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND date>=? AND date<=?",
                                           (v['id'], date_from, date_to)).fetchone()[0]
        empty_pct = round(empty_run_count / vehicle_trip_count * 100, 1) if vehicle_trip_count else 0
        idle_pct = round(idle_days / total_days_v * 100, 1) if total_days_v else 0
        rows.append({'vehicle_no': v['vehicle_no'], 'type': v['type'], 'group': v['type'],
                      'active_days': active_days, 'idle_days': idle_days, 'total_days': total_days_v,
                      'empty_run_count': empty_run_count, 'empty_pct': empty_pct, 'idle_pct': idle_pct,
                      'last_trip': last_trip, 'status': status})
    rows.sort(key=lambda r: r['idle_days'], reverse=True)

    total_vehicles = len(rows)
    active_vehicles = sum(1 for r in rows if r['status'] == 'Active')
    idle_vehicles = sum(1 for r in rows if r['status'] == 'Idle')
    active_pct = round((active_vehicles / total_vehicles * 100), 1) if total_vehicles > 0 else 0
    idle_vehicles_pct = round((idle_vehicles / total_vehicles * 100), 1) if total_vehicles > 0 else 0
    total_active_days = sum(r['active_days'] for r in rows)
    total_idle_days = sum(r['idle_days'] for r in rows)
    total_all_days = total_active_days + total_idle_days
    active_days_pct = round((total_active_days / total_all_days * 100), 1) if total_all_days > 0 else 0
    idle_days_pct = round((total_idle_days / total_all_days * 100), 1) if total_all_days > 0 else 0
    # Per-vehicle averages — the raw fleet-wide sums above can run into the thousands (17 vehicles
    # each idle for part of a year adds up fast) and read as if that were a single days count,
    # which isn't a meaningful number on its own. The percentages stay valid as a proportion; the
    # donut itself shows the average per vehicle instead of the summed total.
    avg_idle_days = round(total_idle_days / total_vehicles, 1) if total_vehicles > 0 else 0
    avg_active_days = round(total_active_days / total_vehicles, 1) if total_vehicles > 0 else 0
    most_idle = sorted(rows, key=lambda r: r['idle_days'], reverse=True)[:5]

    # ---------- Empty runs (same formulas as the old Empty Runs page — LR number starting with "Empty") ----------
    eq = """SELECT t.id, t.date, v.vehicle_no, v.type as vehicle_type, t.from_loc, t.to_loc, t.fuel_amount, t.toll
            FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id
            WHERE t.lr_number LIKE 'Empty%'"""
    eparams = []
    if date_from:
        eq += " AND t.date >= ?"; eparams.append(date_from)
    if date_to:
        eq += " AND t.date <= ?"; eparams.append(date_to)
    if vehicle_f:
        eq += " AND v.vehicle_no = ?"; eparams.append(vehicle_f)
    eq += " ORDER BY t.date DESC"
    empty_rows = conn.execute(eq, eparams).fetchall()
    empty_toll_map = _toll_by_trip(conn, [r['id'] for r in empty_rows])
    total_fuel = sum(r['fuel_amount'] or 0 for r in empty_rows)
    total_toll = sum(_trip_toll(r, empty_toll_map) for r in empty_rows)
    total_empty_cost = total_fuel + total_toll
    empty_run_trips = len(empty_rows)

    # ---------- Empty Runs by Vehicle & by Route (own fleet only) — ranked most-frequent-first,
    # replacing the old flat per-trip list per instruction ("bring those data points with total
    # cost" instead). Own fleet only, not hired — a hired vehicle's empty-run fuel/toll isn't
    # really "your" cost the same way an own vehicle's is. Route grouping reuses _clean_loc(), the
    # same normalization Route Analytics already uses, so "Mumbai"/"MUMBAI"/"Mumbai " don't count
    # as 3 different routes here either. ----------
    empty_by_vehicle_map = {}
    empty_by_route_map = {}
    for r in empty_rows:
        if r['vehicle_type'] != 'own' or not r['vehicle_no']:
            continue
        cost = (r['fuel_amount'] or 0) + _trip_toll(r, empty_toll_map)
        vm = empty_by_vehicle_map.setdefault(r['vehicle_no'], {'vehicle_no': r['vehicle_no'], 'count': 0, 'cost': 0})
        vm['count'] += 1
        vm['cost'] += cost
        cf, ct = _clean_loc(r['from_loc']), _clean_loc(r['to_loc'])
        rk = (cf, ct)
        rm = empty_by_route_map.setdefault(rk, {'from_loc': cf or '—', 'to_loc': ct or '—', 'count': 0, 'cost': 0})
        rm['count'] += 1
        rm['cost'] += cost
    empty_by_vehicle = sorted(empty_by_vehicle_map.values(), key=lambda x: (-x['count'], -x['cost']))
    empty_by_route = sorted(empty_by_route_map.values(), key=lambda x: (-x['count'], -x['cost']))

    total_trips_period = conn.execute("SELECT COUNT(*) FROM trips WHERE date>=? AND date<=?", (date_from, date_to)).fetchone()[0]
    empty_run_pct_overall = round(empty_run_trips / total_trips_period * 100, 1) if total_trips_period else 0

    # ---------- 12-week idle trend (own vehicles, same corrected active/idle formula, bucketed weekly) ----------
    trend = []
    for i in range(11, -1, -1):
        w_end = d2 - datetime.timedelta(days=7 * i)
        w_start = w_end - datetime.timedelta(days=6)
        week_idle = 0
        for v in own_vehicles:
            _, w_idle, _ = _vehicle_active_idle_days(conn, v['id'], v['registration_date'],
                                                       w_start.isoformat(), w_end.isoformat())
            week_idle += w_idle
        trend.append({'label': w_start.strftime('%-d %b'), 'idle_days': week_idle})
    trend_max = max([t['idle_days'] for t in trend], default=1) or 1
    chart_w, chart_h, pad_l, pad_r, pad_t, pad_b = 700, 220, 30, 20, 20, 34
    plot_w, plot_h, n = chart_w - pad_l - pad_r, chart_h - pad_t - pad_b, len(trend)
    chart_bottom = pad_t + plot_h
    for i, t in enumerate(trend):
        t['x'] = round(pad_l + (i * plot_w / (n - 1) if n > 1 else 0), 1)
        t['y'] = round(pad_t + plot_h - (t['idle_days'] / trend_max * plot_h if trend_max else 0), 1)
    y_ticks = []
    for k in range(5):
        tick_val = round(trend_max * k / 4)
        y_ticks.append({'value': tick_val, 'y': round(pad_t + plot_h - (tick_val / trend_max * plot_h if trend_max else 0), 1)})

    all_vehicles_list = conn.execute("SELECT vehicle_no FROM vehicles WHERE type = 'own' AND COALESCE(status,'Active')='Active' ORDER BY vehicle_no").fetchall()

    # Real, own-fleet KM/Fuel rollup for the period — actual_km and fuel_liters (the real litres
    # column, distinct from fuel_amount which is cost) already used elsewhere in this app (e.g.
    # the Maintenance Overview's own cost-per-km), just not previously surfaced as a Fleet
    # Utilization page metric.
    km_fuel_row = conn.execute("""SELECT COALESCE(SUM(t.actual_km),0) as km,
                                  COALESCE(SUM(CAST(t.fuel_liters AS REAL)),0) as fuel_ltr
                                  FROM trips t JOIN vehicles v ON t.vehicle_id=v.id
                                  WHERE v.type='own' AND t.date>=? AND t.date<=?""", (date_from, date_to)).fetchone()
    fleet_total_km = km_fuel_row['km'] or 0
    fleet_total_fuel_ltr = km_fuel_row['fuel_ltr'] or 0
    avg_fuel_efficiency = round(fleet_total_km / fleet_total_fuel_ltr, 2) if fleet_total_fuel_ltr else 0

    # Real vehicle-status 3-way split (On Trip / In Workshop / Idle) — same real, mutually-
    # exclusive categories app_dashboard()'s own Business Overview panel already uses.
    on_trip_count = conn.execute("""SELECT COUNT(DISTINCT vehicle_id) FROM trips
                                    WHERE end_date IS NULL AND vehicle_id IN
                                    (SELECT id FROM vehicles WHERE type='own')""").fetchone()[0]
    in_workshop = conn.execute("SELECT COUNT(*) FROM vehicles WHERE type='own' AND status='In Maintenance'").fetchone()[0]
    vs_idle = max(total_vehicles - on_trip_count - in_workshop, 0)

    # Utilization by Vehicle Type — real body_type breakdown (own fleet), same _short_body_type()
    # grouping the Tyre Layout diagram already uses, not a fabricated fixed category list.
    body_types = conn.execute("SELECT body_type FROM vehicles WHERE type='own'").fetchall()
    type_counts = {}
    for b in body_types:
        key = _short_body_type(b['body_type']) or 'Others'
        type_counts[key] = type_counts.get(key, 0) + 1
    type_breakdown = sorted(type_counts.items(), key=lambda kv: -kv[1])

    conn.close()
    return {'rows': rows, 'most_idle': most_idle, 'empty_rows': empty_rows, 'trend': trend, 'trend_max': trend_max,
        'chart_bottom': chart_bottom, 'y_ticks': y_ticks,
        'total_vehicles': total_vehicles, 'active_vehicles': active_vehicles, 'idle_vehicles': idle_vehicles,
        'active_pct': active_pct, 'idle_vehicles_pct': idle_vehicles_pct,
        'total_active_days': total_active_days, 'total_idle_days': total_idle_days,
        'active_days_pct': active_days_pct, 'idle_days_pct': idle_days_pct,
        'avg_idle_days': avg_idle_days, 'avg_active_days': avg_active_days,
        'empty_run_trips': empty_run_trips, 'empty_run_pct_overall': empty_run_pct_overall,
        'total_fuel': total_fuel, 'total_toll': total_toll, 'total_empty_cost': total_empty_cost,
        'empty_by_vehicle': empty_by_vehicle, 'empty_by_route': empty_by_route,
        'f_date_from': date_from, 'f_date_to': date_to, 'f_vehicle': vehicle_f, 'vehicles': all_vehicles_list,
        'fleet_total_km': fleet_total_km, 'on_trip_count': on_trip_count, 'in_workshop': in_workshop, 'vs_idle': vs_idle,
        'type_breakdown': type_breakdown, 'fleet_total_fuel_ltr': fleet_total_fuel_ltr, 'avg_fuel_efficiency': avg_fuel_efficiency}

@app.route('/fleet-utilization')
def fleet_utilization():
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    vehicle_f = request.args.get('vehicle', '')
    d = _fleet_utilization_data(conn, date_from, date_to, vehicle_f)
    return render_template('fleet_utilization.html',
        rows=d['rows'], most_idle=d['most_idle'], empty_rows=d['empty_rows'], trend=d['trend'], trend_max=d['trend_max'],
        chart_bottom=d['chart_bottom'], y_ticks=d['y_ticks'],
        total_vehicles=d['total_vehicles'], active_vehicles=d['active_vehicles'], idle_vehicles=d['idle_vehicles'],
        active_pct=d['active_pct'], idle_vehicles_pct=d['idle_vehicles_pct'],
        total_active_days=d['total_active_days'], total_idle_days=d['total_idle_days'],
        active_days_pct=d['active_days_pct'], idle_days_pct=d['idle_days_pct'],
        avg_idle_days=d['avg_idle_days'], avg_active_days=d['avg_active_days'],
        empty_run_trips=d['empty_run_trips'], empty_run_pct_overall=d['empty_run_pct_overall'],
        total_fuel=d['total_fuel'], total_toll=d['total_toll'], total_empty_cost=d['total_empty_cost'],
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_vehicle=d['f_vehicle'], vehicles=d['vehicles'],
        active='fleet-utilization')

@app.route('/fleet-utilization/export')
def export_fleet_utilization():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    date_from = request.args.get('date_from', '2026-04-01')
    date_to = request.args.get('date_to', '2027-03-31')
    vehicle_f = request.args.get('vehicle', '')
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    query = "SELECT id, vehicle_no, type, registration_date FROM vehicles WHERE type = 'own'"
    params = []
    if vehicle_f:
        query += " AND vehicle_no = ?"; params.append(vehicle_f)
    own_vehicles = conn.execute(query, params).fetchall()

    rows = []
    for v in own_vehicles:
        active_days, idle_days, total_days_v = _vehicle_active_idle_days(
            conn, v['id'], v['registration_date'], date_from, date_to)
        last_trip = conn.execute("SELECT MAX(date) FROM trips WHERE vehicle_id=?", (v['id'],)).fetchone()[0]
        status = 'Idle' if idle_days >= active_days else 'Active'
        empty_run_count = conn.execute("""SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND lr_number LIKE 'Empty%'
                                          AND date>=? AND date<=?""", (v['id'], date_from, date_to)).fetchone()[0]
        rows.append((v['vehicle_no'], v['type'], active_days, idle_days, total_days_v, empty_run_count, last_trip or '', status))
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Fleet Utilization"
    headers = ["Vehicle No", "Type", "Active Days", "Idle Days", "Total Days", "Empty Run Trips", "Last Trip Date", "Status"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1B2A4A")
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='fleet_utilization.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/trips/edit/<int:trip_id>', methods=['GET', 'POST'])
def edit_trip(trip_id):
    conn = get_db()
    if request.method == 'POST':
        f = request.form

        # Same duplicate-LR guard as add_trip (see the comment there) — excludes this trip's own
        # row so re-saving a trip without changing its LR Number doesn't trip over itself.
        lr_number = (f.get('lr_number') or '').strip()
        dup = None
        if lr_number:
            dup = conn.execute("""SELECT t.id, t.date, v.vehicle_no FROM trips t
                                  LEFT JOIN vehicles v ON t.vehicle_id=v.id
                                  WHERE t.lr_number = ? COLLATE NOCASE AND TRIM(t.lr_number) != '' AND t.id != ?""",
                               (lr_number, trip_id)).fetchone()
        form_error = None if dup else _trip_form_error(f, check_advance=False)
        if True:
            if dup or form_error:
                custom_item_rows = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, ve.name as vendor_name
                                                   FROM invoice_items ii LEFT JOIN vendors ve ON ii.vendor_id=ve.id
                                                   WHERE ii.trip_id=?""", (trip_id,)).fetchall()
                custom_items = [{'description': r['description'], 'item_type': r['item_type'], 'rate': r['amount'], 'quantity': 1,
                                  'vendor_name': r['vendor_name'] or ''}
                                 for r in custom_item_rows]
                conn.close()
                vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
                conn2 = get_db()
                employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
                vehicle_type_map = _vehicle_type_map(conn2)
                conn2.close()
                error = form_error or (f"LR Number \"{lr_number}\" is already used on trip #{dup['id']} "
                                        f"({dup['date']}, {dup['vehicle_no'] or 'no vehicle'}).")
                return render_template('trip_form.html', mode='edit', t=f, custom_items=custom_items,
                                        vehicles=vehicles, parties=parties, vendors=vendors, combined_names=combined_names,
                                        employees=employees, vehicle_type_map=vehicle_type_map,
                                        return_to=f.get('return_to', ''), active='trips', error=error)

        # The whole recompute-and-UPDATE (billed amount, payment/owner floors, vendors, custom items) lives in
        # _update_trip_from_form(), the single copy the mobile app also uses — this route used to carry its own
        # ~100-line duplicate of it, which is exactly how the two once drifted apart.
        ok, err = _update_trip_from_form(conn, trip_id, f)
        if not ok:
            conn.close()
            return redirect(url_for('trips_list'))
        conn.commit()
        conn.close()
        return_to = f.get('return_to')
        return redirect(return_to) if return_to else redirect(url_for('trips_list'))

    trip = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name,
                           fv.name as fuel_vendor_name, dv.name as driveradv_vendor_name, mv.name as misc_vendor_name
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           LEFT JOIN vendors mv ON t.misc_vendor_id=mv.id
                           WHERE t.id=?""", (trip_id,)).fetchone()
    if not trip:
        conn.close()
        return redirect(url_for('trips_list'))
    custom_item_rows = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, ve.name as vendor_name
                                       FROM invoice_items ii LEFT JOIN vendors ve ON ii.vendor_id=ve.id
                                       WHERE ii.trip_id=?""", (trip_id,)).fetchall()
    custom_items = [{'description': r['description'], 'item_type': r['item_type'], 'rate': r['amount'], 'quantity': 1,
                      'vendor_name': r['vendor_name'] or ''}
                     for r in custom_item_rows]
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn2 = get_db()
    employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
    vehicle_type_map = _vehicle_type_map(conn2)
    conn2.close()
    return render_template('trip_form.html', mode='edit', t=dict(trip), custom_items=custom_items,
                            vehicles=vehicles, parties=parties, vendors=vendors, combined_names=combined_names,
                            employees=employees, vehicle_type_map=vehicle_type_map,
                            return_to=request.args.get('return_to', ''), active='trips')

def _trip_detail_data(conn, trip_id):
    """Shared by trip_view() (website, Ledger popup) and app_trip_detail() (mobile) — every real
    field either page needs about one trip, computed once. Returns None if the trip doesn't
    exist."""
    trip = conn.execute("""SELECT t.*, v.id as veh_id, v.vehicle_no, v.status as veh_status,
                           v.body_type as veh_body_type, v.capacity_mt as veh_capacity_mt,
                           v.rc_synced_data as veh_rc_data, p.name as party_name,
                           fv.name as fuel_vendor_name, dv.name as driveradv_vendor_name, mv.name as misc_vendor_name
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           LEFT JOIN vendors mv ON t.misc_vendor_id=mv.id
                           WHERE t.id=?""", (trip_id,)).fetchone()
    if not trip:
        return None
    custom_item_rows = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, ve.name as vendor_name
                                       FROM invoice_items ii LEFT JOIN vendors ve ON ii.vendor_id=ve.id
                                       WHERE ii.trip_id=?""", (trip_id,)).fetchall()
    t = _mask_trip_dict(trip)             # a "hide amounts" user gets 0 in every revenue / payment column (see TRIP_HIDDEN_COLUMNS)
    if _hide_trip_amounts():
        custom_item_rows = [dict(r, amount=0) for r in custom_item_rows]
    is_hired = t.get('type') == VEHICLE_TYPE_HIRED
    owner_amount = 0
    if is_hired:
        if (t.get('owner_rate_type') or 'PER_MT') == 'FIXED':
            owner_amount = t.get('owner_fixed_amount') or 0
        else:
            owner_amount = (t.get('owner_rate') or 0) * (t.get('quantity') or 0)
    veh_rc = None
    if t.get('veh_rc_data'):
        try:
            veh_rc = json.loads(t['veh_rc_data'])
        except ValueError:
            veh_rc = None
    additions_total = sum((t.get(k) or 0) for k in
        ('detention_charges', 'gps_cost', 'loading_charge', 'unloading_charge', 'police_charges',
         'sim_tracking', 'union_charges', 'weight_charges', 'other_charges'))
    deductions_total = sum((t.get(k) or 0) for k in
        ('brokerage', 'builty_commission', 'late_fees', 'material_damage', 'shortage_amount', 'tds', 'other_deductions'))
    for it in custom_item_rows:
        deductions_total += (it['amount'] or 0) if it['item_type'] == 'deduction' else 0
        additions_total += (it['amount'] or 0) if it['item_type'] == 'charge' else 0
    # Same formula the party ledger's own pending-trips list and the Trips list totals already
    # use (e.g. _app_entity_ledger()'s pending_trips_raw query, trips_list()'s pending_total) —
    # this one was missing party_advance entirely, so a trip with an advance on it showed its
    # full billed amount as still due even before any payment was ever recorded against it.
    balance_due = (t.get('billed_amount') or 0) - (t.get('payment_received') or 0) - (t.get('party_advance') or 0)
    return {'t': t, 'custom_items': custom_item_rows, 'is_hired': is_hired,
            'owner_amount': owner_amount, 'veh_rc': veh_rc, 'additions_total': additions_total,
            'deductions_total': deductions_total, 'balance_due': balance_due}

@app.route('/trips/<int:trip_id>/view')
def trip_view(trip_id):
    """Read-only trip detail — opened from a Ledger row's Trip Bill entry in a centered popup
    (see openRecordModal in base.html), instead of routing to the editable Trip form. Same
    fields as trip_form.html/edit_trip, just displayed rather than edited — purely look-don't-
    touch, no edit/download action on this view. See _trip_detail_data() above for what's
    computed."""
    conn = get_db()
    data = _trip_detail_data(conn, trip_id)
    conn.close()
    if data is None:
        return redirect(url_for('trips_list'))
    return render_template('trip_view.html', active='accounts', **data)

@app.route('/business-performance')
def business_performance():
    conn = get_db()
    # Default to the last fully completed calendar month (standard CEO-dashboard convention).
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    default_from, default_to = _month_bounds(last_month_end.year, last_month_end.month)
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or default_to
    date_from, date_to = _normalized_date_range(date_from, date_to)

    curr = _period_financials(conn, date_from, date_to)
    prev_from, prev_to = _shift_period_back(date_from, date_to)
    prev = _period_financials(conn, prev_from, prev_to)
    q_from, q_to = _quarter_bounds(datetime.datetime.strptime(date_to, '%Y-%m-%d').date())
    pq_from, pq_to = _shift_period_back(q_from, q_to)
    curr_q = _period_financials(conn, q_from, q_to)
    prev_q = _period_financials(conn, pq_from, pq_to)
    y_from, y_to = _shift_period_year(date_from, date_to)
    curr_y = _period_financials(conn, y_from, y_to)

    days_in_period = (datetime.datetime.strptime(date_to, '%Y-%m-%d').date() -
                       datetime.datetime.strptime(date_from, '%Y-%m-%d').date()).days + 1

    revenue_growth = _pct_growth(curr['revenue'], prev['revenue'])
    profit_growth = _pct_growth(curr['net_profit'], prev['net_profit'])
    expense_growth = _pct_growth(curr['total_expenses'], prev['total_expenses'])
    profit_growth_qoq = _pct_growth(curr_q['net_profit'], prev_q['net_profit'])
    profit_growth_yoy = _pct_growth(curr['net_profit'], curr_y['net_profit'])
    revenue_growth_qoq = _pct_growth(curr_q['revenue'], prev_q['revenue'])
    revenue_growth_yoy = _pct_growth(curr['revenue'], curr_y['revenue'])

    # Outstanding receivables / payables — all-time snapshot balances, same rows _accounts_rows()
    # builds for the Ledger page (see its docstring — computed from each party/vendor's real
    # transactional entries, not a separate hand-written aggregate that can drift out of sync).
    _acct_rows_bp = _accounts_rows(conn)
    party_balance = {r['id']: r['balance'] for r in _acct_rows_bp if r['role'] == 'Party'}
    party_name = {r['id']: r['name'] for r in _acct_rows_bp if r['role'] == 'Party'}
    total_receivables = sum(r['balance'] for r in _acct_rows_bp if r['balance'] > 0)
    total_payables = sum(-r['balance'] for r in _acct_rows_bp if r['balance'] < 0)

    # Cash movement for the selected period.
    cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                   (date_from, date_to)).fetchone()[0]
    cash_collected += sum(t['party_advance'] or 0 for t in curr['trips'])
    cash_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='paid' AND date>=? AND date<=?",
                              (date_from, date_to)).fetchone()[0]
    cash_paid += curr['maint'] + curr['overheads'] + curr['salaries']

    prev_cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                        (prev_from, prev_to)).fetchone()[0]
    prev_cash_collected += sum(t['party_advance'] or 0 for t in prev['trips'])
    collection_growth = _pct_growth(cash_collected, prev_cash_collected)
    collection_efficiency = round(cash_collected / curr['revenue'] * 100, 2) if curr['revenue'] else 0

    avg_revenue_per_trip = round(curr['revenue'] / curr['trip_count'], 2) if curr['trip_count'] else 0
    avg_profit_per_trip = round(curr['net_profit'] / curr['trip_count'], 2) if curr['trip_count'] else 0
    operating_ratio = round(curr['total_expenses'] / curr['revenue'] * 100, 2) if curr['revenue'] else 0
    ebitda = curr['net_profit']  # no interest/tax/depreciation tracked separately, so this is profit-based estimate

    # Financial Overview / Monthly Profit Trend — trailing 6 months ending at date_to's month.
    end_d = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    trailing_months = []
    yy_, mm_ = end_d.year, end_d.month
    for i in range(5, -1, -1):
        mm = mm_ - i
        yy = yy_
        while mm <= 0:
            mm += 12
            yy -= 1
        trailing_months.append((yy, mm))
    monthly_rows = []
    for (yy, mm) in trailing_months:
        mf, mt = _month_bounds(yy, mm)
        mfin = _period_financials(conn, mf, mt)
        monthly_rows.append({'label': calendar.month_abbr[mm], 'revenue': mfin['revenue'],
                              'expenses': mfin['total_expenses'], 'net_profit': mfin['net_profit']})

    # Top customers by revenue (period).
    party_period = {}
    curr_toll_map = _toll_by_trip(conn, [t['id'] for t in curr['trips']])
    for t in curr['trips']:
        if not t['party_id']:
            continue
        d = party_period.setdefault(t['party_id'], {'revenue': 0, 'direct_cost': 0, 'trips': 0})
        d['revenue'] += t['billed_amount'] or 0
        d['trips'] += 1
        # Fuel/driver-advance on a Hired trip is the owner's own money, already covered by the
        # contracted freight added below — see the same fix in route_analytics().
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        direct = fuel_and_adv + _trip_toll(t, curr_toll_map) + (t['parking'] or 0)
        if t['type'] == VEHICLE_TYPE_HIRED:
            direct += (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        d['direct_cost'] += direct
    top_customers = []
    for pid, d in sorted(party_period.items(), key=lambda kv: kv[1]['revenue'], reverse=True)[:6]:
        top_customers.append({
            'name': party_name.get(pid, '—'), 'revenue': d['revenue'],
            'pct': round(d['revenue'] / curr['revenue'] * 100, 2) if curr['revenue'] else 0,
            'profit': d['revenue'] - d['direct_cost'],
            'outstanding': max(party_balance.get(pid, 0), 0),
        })
    customers_other_revenue = curr['revenue'] - sum(c['revenue'] for c in top_customers)
    customer_growth = _pct_growth(len(party_period), len(prev['party_ids']))

    # Expense breakdown (period).
    exp_cats = [
        ('Fuel', curr['fuel'], '#2a78d6'), ('Driver Advance', curr['driver_adv'], '#eb6834'),
        ('Maintenance', curr['maint'], '#1baf7a'), ('Salary', curr['salaries'], '#eda100'),
        ('Toll / Parking', curr['toll'] + curr['parking'], '#e87ba4'),
        ('Other Expenses', curr['misc'] + curr['overheads'] + curr['owner_cost'], '#008300'),
    ]
    exp_total = sum(v for _, v, _ in exp_cats) or 1
    expense_breakdown = [{'label': l, 'amount': v, 'color': c, 'pct': round(v / exp_total * 100, 2)} for l, v, c in exp_cats]

    # Collections analytics — age each party's *net* balance (party_balance, already netted against
    # opening balance and unallocated payments) off their oldest still-pending trip, so overdue_total
    # stays a subset of total_receivables instead of double-counting against raw per-trip pending.
    all_trips = conn.execute("SELECT id, date, lr_number, party_id, billed_amount, payment_received, party_advance FROM trips").fetchall()
    party_pending_trips = {}
    for t in all_trips:
        if not t['party_id'] or not t['date']:
            continue
        pending = (t['billed_amount'] or 0) - (t['payment_received'] or 0) - (t['party_advance'] or 0)
        if pending <= 0.01:
            continue
        party_pending_trips.setdefault(t['party_id'], []).append(t)

    # "Overdue" is a real-world, right-now fact about the receivable — how many days old it
    # actually is TODAY — never relative to whichever date_to happens to be selected on the page.
    # Ageing this off end_d (the filter's own end date) instead of today's real date meant looking
    # at an old month showed genuinely-overdue money as "Overdue: ₹0", purely because the filter's
    # end date was earlier than the trip itself — nothing about which trips are pending changes
    # with the filter (party_pending_trips has no date filter at all), only whether they LOOK
    # overdue did, which made the number silently wrong for any period other than the current one.
    today_d = datetime.date.today()
    overdue_total = 0
    oldest_outstanding = None
    for pid, bal in party_balance.items():
        if bal <= 0.01:
            continue
        trips_for_party = party_pending_trips.get(pid)
        if not trips_for_party:
            continue
        oldest_trip = min(trips_for_party, key=lambda t: t['date'])
        t_date = datetime.datetime.strptime(oldest_trip['date'], '%Y-%m-%d').date()
        age = (today_d - t_date).days
        if age > 30:
            overdue_total += bal
        if oldest_outstanding is None or t_date < oldest_outstanding['date']:
            oldest_outstanding = {'date': t_date, 'lr': oldest_trip['lr_number'] or f"Trip #{oldest_trip['id']}", 'age': age}

    alloc_rows = conn.execute("""SELECT pay.date as pay_date, t.date as trip_date FROM payment_allocations pa
                                 JOIN payments pay ON pa.payment_id = pay.id
                                 JOIN trips t ON pa.trip_id = t.id
                                 WHERE pay.date>=? AND pay.date<=?""", (date_from, date_to)).fetchall()
    if not alloc_rows:
        alloc_rows = conn.execute("""SELECT pay.date as pay_date, t.date as trip_date FROM payment_allocations pa
                                     JOIN payments pay ON pa.payment_id = pay.id
                                     JOIN trips t ON pa.trip_id = t.id""").fetchall()
    collection_days_list = []
    for r in alloc_rows:
        if not r['pay_date'] or not r['trip_date']:
            continue
        pd_ = datetime.datetime.strptime(r['pay_date'], '%Y-%m-%d').date()
        td_ = datetime.datetime.strptime(r['trip_date'], '%Y-%m-%d').date()
        collection_days_list.append((pd_ - td_).days)
    avg_collection_days = round(sum(collection_days_list) / len(collection_days_list), 1) if collection_days_list else None

    # Highest / lowest revenue & profit month across all months with data.
    all_months_rows = conn.execute("SELECT DISTINCT substr(date,1,7) as ym FROM trips ORDER BY ym").fetchall()
    month_fin = []
    for r in all_months_rows:
        yy, mm = int(r['ym'][:4]), int(r['ym'][5:7])
        mf, mt = _month_bounds(yy, mm)
        f = _period_financials(conn, mf, mt)
        month_fin.append({'label': f"{calendar.month_abbr[mm]} {yy}", 'revenue': f['revenue'], 'net_profit': f['net_profit']})
    highest_revenue_month = max(month_fin, key=lambda r: r['revenue']) if month_fin else None
    highest_profit_month = max(month_fin, key=lambda r: r['net_profit']) if month_fin else None
    lowest_profit_month = min(month_fin, key=lambda r: r['net_profit']) if month_fin else None

    # Highest collection / expense day within the selected period.
    day_collect = {}
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM payments WHERE payment_type='received' AND date>=? AND date<=? GROUP BY date",
                           (date_from, date_to)).fetchall():
        day_collect[r['date']] = day_collect.get(r['date'], 0) + (r['amt'] or 0)
    for t in curr['trips']:
        if t['party_advance']:
            day_collect[t['date']] = day_collect.get(t['date'], 0) + t['party_advance']
    highest_collection_day = max(day_collect.items(), key=lambda kv: kv[1]) if day_collect else None

    # Toll now goes through the same _trip_toll()/_toll_by_trip() every other screen uses (real
    # linked Toll Management amount if one exists, else the trip's own manual estimate, never
    # both) — the old comment's "maintenance-by-date sum already carries Toll Management's real
    # cost" was only true for a LINKED toll; an unlinked trips.toll estimate never creates a
    # maintenance row at all, so it was silently invisible to this KPI. The maintenance-by-date
    # query below now excludes the Toll category specifically, so a LINKED toll's mirrored
    # maintenance row is never ALSO summed on top of being counted via _trip_toll() (same
    # maint/toll split _period_financials already uses).
    day_expense_toll_map = _toll_by_trip(conn, [t['id'] for t in curr['trips']])
    day_expense = {}
    for t in curr['trips']:
        # Fuel/driver-advance on a Hired trip is the owner's own money, already covered by the
        # contracted freight added below — see the same fix in route_analytics().
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        direct = (fuel_and_adv + _trip_toll(t, day_expense_toll_map) + (t['parking'] or 0) +
                  (t['agent_commission'] or 0) + (t['builty_expense'] or 0) + (t['conductor_expense'] or 0) + (t['fine'] or 0) +
                  (t['labour_charges'] or 0) + (t['puncture'] or 0) + (t['urea'] or 0) + (t['loading_expense'] or 0) +
                  (t['unloading_expense'] or 0) + (t['wear_tear'] or 0) + (t['weighbridge_charges'] or 0) +
                  (t['other_expense'] or 0) + (t['permit_charges'] or 0))
        if t['type'] == VEHICLE_TYPE_HIRED:
            direct += (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        day_expense[t['date']] = day_expense.get(t['date'], 0) + direct
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM maintenance WHERE date>=? AND date<=? AND COALESCE(category,'')!='Toll' GROUP BY date", (date_from, date_to)).fetchall():
        day_expense[r['date']] = day_expense.get(r['date'], 0) + (r['amt'] or 0)
    for r in conn.execute("SELECT date, SUM(amount) as amt FROM overheads WHERE date>=? AND date<=? GROUP BY date", (date_from, date_to)).fetchall():
        day_expense[r['date']] = day_expense.get(r['date'], 0) + (r['amt'] or 0)
    highest_expense_day = max(day_expense.items(), key=lambda kv: kv[1]) if day_expense else None

    avg_daily_revenue = round(curr['revenue'] / days_in_period, 2) if days_in_period else 0
    avg_daily_profit = round(curr['net_profit'] / days_in_period, 2) if days_in_period else 0

    # Scoped to vehicles/employees that actually existed by the end of the selected period —
    # previously a plain unfiltered COUNT(*), so adding a vehicle or hiring someone TODAY silently
    # diluted this KPI for every past period you looked back at, even one from years earlier.
    # registration_date/joining_date (the real-world date each one came on) wins when set; falls
    # back to created_at (when the record entered this app) for older rows that predate either
    # field being collected, and finally to a date far enough in the past that a genuinely
    # undated legacy row is still counted rather than silently dropped.
    own_vehicle_count = conn.execute("""SELECT COUNT(*) FROM vehicles WHERE type = 'own'
                                        AND COALESCE(registration_date, substr(created_at,1,10), '0001-01-01') <= ?""",
                                     (date_to,)).fetchone()[0]
    own_revenue = sum(t['billed_amount'] or 0 for t in curr['trips'] if t['type'] == VEHICLE_TYPE_OWN)
    avg_revenue_per_vehicle = round(own_revenue / own_vehicle_count, 2) if own_vehicle_count else 0
    employee_count = conn.execute("""SELECT COUNT(*) FROM employees
                                     WHERE COALESCE(joining_date, substr(created_at,1,10), '0001-01-01') <= ?""",
                                  (date_to,)).fetchone()[0]
    avg_revenue_per_employee = round(curr['revenue'] / employee_count, 2) if employee_count else 0

    invoice_count_curr = conn.execute("SELECT COUNT(*) FROM invoice_batches WHERE invoice_date>=? AND invoice_date<=?",
                                       (date_from, date_to)).fetchone()[0]
    invoice_count_prev = conn.execute("SELECT COUNT(*) FROM invoice_batches WHERE invoice_date>=? AND invoice_date<=?",
                                       (prev_from, prev_to)).fetchone()[0]
    invoice_growth = _pct_growth(invoice_count_curr, invoice_count_prev)
    avg_invoice_value = round(curr['revenue'] / invoice_count_curr, 2) if invoice_count_curr else avg_revenue_per_trip
    trip_growth = _pct_growth(curr['trip_count'], prev['trip_count'])

    # Rule-based ("AI") insights, generated from the numbers above — not a live model call.
    insights = []
    if revenue_growth is not None:
        if revenue_growth >= 0:
            insights.append({'kind': 'good', 'text': f"Revenue increased by {revenue_growth}% compared to last month."})
        else:
            insights.append({'kind': 'bad', 'text': f"Revenue dropped by {abs(revenue_growth)}% compared to last month."})
    if profit_growth is not None:
        if profit_growth >= 0:
            insights.append({'kind': 'good', 'text': f"Profit increased by {profit_growth}% due to better revenue and controlled expenses."})
        else:
            insights.append({'kind': 'bad', 'text': f"Profit fell by {abs(profit_growth)}% — expenses grew faster than revenue."})
    prev_cats = {
        'Fuel': prev['fuel'], 'Driver Advance': prev['driver_adv'], 'Maintenance': prev['maint'],
        'Salary': prev['salaries'], 'Toll / Parking': prev['toll'] + prev['parking'],
        'Other Expenses': prev['misc'] + prev['overheads'] + prev['owner_cost'],
    }
    cat_growth = []
    for cat in expense_breakdown:
        g = _pct_growth(cat['amount'], prev_cats.get(cat['label'], 0))
        if g is not None and g > 0:
            cat_growth.append((cat['label'], g))
    if cat_growth:
        top_cat, top_g = max(cat_growth, key=lambda x: x[1])
        insights.append({'kind': 'warn', 'text': f"{top_cat} expenses increased by {top_g}%. Review costs in this category."})
    if total_receivables > 0:
        insights.append({'kind': 'info', 'text': f"Outstanding receivables stand at ₹{total_receivables:,.0f}. Focus on collections."})
    if avg_collection_days is not None and avg_collection_days > 30:
        insights.append({'kind': 'bad', 'text': f"Average collection period is {avg_collection_days} days. Consider tighter follow-up on pending invoices."})

    # Business Health Score — five sub-scores, each 0-100, averaged.
    def clamp(v, lo=0, hi=100):
        return max(lo, min(hi, v))
    profitability_score = clamp(round(curr['margin'] / 30 * 100)) if curr['revenue'] else 0
    financial_score = clamp(round(200 - 2 * operating_ratio)) if curr['revenue'] else 0
    collection_score = clamp(round(100 - (overdue_total / total_receivables * 100))) if total_receivables > 0 else 100
    cashflow_score = clamp(round(50 + (cash_collected - cash_paid) / curr['revenue'] * 100)) if curr['revenue'] else 50
    growth_score = clamp(round(50 + (revenue_growth or 0) * 2))
    sub_scores = [
        {'label': 'Financial Health', 'score': financial_score},
        {'label': 'Profitability', 'score': profitability_score},
        {'label': 'Collection Health', 'score': collection_score},
        {'label': 'Cash Flow', 'score': cashflow_score},
        {'label': 'Growth', 'score': growth_score},
    ]
    overall_score = round(sum(s['score'] for s in sub_scores) / len(sub_scores))
    if overall_score >= 80:
        health_label, health_kind = 'Healthy', 'good'
    elif overall_score >= 60:
        health_label, health_kind = 'Stable', 'info'
    elif overall_score >= 40:
        health_label, health_kind = 'Needs Attention', 'warn'
    else:
        health_label, health_kind = 'Critical', 'bad'
    weakest = min(sub_scores, key=lambda s: s['score'])
    suggestions = []
    if weakest['score'] < 70:
        tips = {
            'Financial Health': 'Bring operating expenses down relative to revenue to strengthen financial health.',
            'Profitability': 'Review pricing on low-margin routes and trim direct trip costs to lift profitability.',
            'Collection Health': 'Chase overdue receivables past 30 days — this is dragging down collection health.',
            'Cash Flow': 'Cash paid is outpacing cash collected this period — prioritise follow-up on pending payments.',
            'Growth': 'Revenue growth has slowed — look for new routes or repeat business with top customers.',
        }
        suggestions.append(f"{weakest['label']} is your weakest score ({weakest['score']}). {tips.get(weakest['label'], '')}")
    if overall_score >= 80:
        suggestions.append('Great job! Your business is performing well. Keep focusing on collections and cost optimization.')

    fy_map = {'2026-04-01|2027-03-31': ('2026-04-01', '2027-03-31'), '2025-04-01|2026-03-31': ('2025-04-01', '2026-03-31')}
    f_fy = ''
    for key, (fy_from, fy_to) in fy_map.items():
        if date_from == fy_from and date_to == fy_to:
            f_fy = key
            break

    conn.close()
    return render_template('business_performance.html',
        f_date_from=date_from, f_date_to=date_to, f_fy=f_fy,
        total_revenue=curr['revenue'], net_profit=curr['net_profit'], profit_margin=curr['margin'],
        total_receivables=total_receivables, total_payables=total_payables,
        cash_collected=cash_collected, cash_paid=cash_paid, collection_efficiency=collection_efficiency,
        revenue_growth=revenue_growth, expense_growth=expense_growth, profit_growth=profit_growth,
        avg_revenue_per_trip=avg_revenue_per_trip, avg_profit_per_trip=avg_profit_per_trip,
        operating_ratio=operating_ratio, ebitda=ebitda, trip_count=curr['trip_count'],
        monthly_rows=monthly_rows,
        profit_growth_mom=profit_growth, profit_growth_qoq=profit_growth_qoq, profit_growth_yoy=profit_growth_yoy,
        revenue_growth_mom=revenue_growth, revenue_growth_qoq=revenue_growth_qoq, revenue_growth_yoy=revenue_growth_yoy,
        curr_revenue=curr['revenue'], prev_revenue=prev['revenue'], revenue_diff=curr['revenue'] - prev['revenue'],
        curr_q_revenue=curr_q['revenue'], prev_q_revenue=prev_q['revenue'], curr_y_revenue=curr_y['revenue'],
        top_customers=top_customers, customers_other_revenue=customers_other_revenue,
        expense_breakdown=expense_breakdown, exp_total=exp_total,
        overdue_total=overdue_total, oldest_outstanding=oldest_outstanding, avg_collection_days=avg_collection_days,
        customer_growth=customer_growth, trip_growth=trip_growth, invoice_growth=invoice_growth, collection_growth=collection_growth,
        highest_revenue_month=highest_revenue_month, highest_profit_month=highest_profit_month, lowest_profit_month=lowest_profit_month,
        highest_collection_day=highest_collection_day, highest_expense_day=highest_expense_day,
        avg_daily_revenue=avg_daily_revenue, avg_daily_profit=avg_daily_profit, avg_invoice_value=avg_invoice_value,
        avg_revenue_per_vehicle=avg_revenue_per_vehicle, avg_revenue_per_employee=avg_revenue_per_employee,
        insights=insights, sub_scores=sub_scores, overall_score=overall_score, health_label=health_label, health_kind=health_kind,
        suggestions=suggestions,
        active='business-performance')

@app.route('/business-performance/export')
def export_business_performance():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io
    conn = get_db()
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    default_from, default_to = _month_bounds(last_month_end.year, last_month_end.month)
    date_from = request.args.get('date_from') or default_from
    date_to = request.args.get('date_to') or default_to
    date_from, date_to = _normalized_date_range(date_from, date_to)

    curr = _period_financials(conn, date_from, date_to)

    # Same rows _accounts_rows() builds for the Ledger page — see its docstring.
    _acct_rows_bpx = _accounts_rows(conn)
    party_balance = {r['id']: r['balance'] for r in _acct_rows_bpx if r['role'] == 'Party'}
    party_name = {r['id']: r['name'] for r in _acct_rows_bpx if r['role'] == 'Party'}
    total_receivables = sum(r['balance'] for r in _acct_rows_bpx if r['balance'] > 0)
    total_payables = sum(-r['balance'] for r in _acct_rows_bpx if r['balance'] < 0)

    cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                   (date_from, date_to)).fetchone()[0]
    cash_collected += sum(t['party_advance'] or 0 for t in curr['trips'])
    cash_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='paid' AND date>=? AND date<=?",
                              (date_from, date_to)).fetchone()[0]
    cash_paid += curr['maint'] + curr['overheads'] + curr['salaries']
    collection_efficiency = round(cash_collected / curr['revenue'] * 100, 2) if curr['revenue'] else 0
    operating_ratio = round(curr['total_expenses'] / curr['revenue'] * 100, 2) if curr['revenue'] else 0

    party_period = {}
    export_toll_map = _toll_by_trip(conn, [t['id'] for t in curr['trips']])
    for t in curr['trips']:
        if not t['party_id']:
            continue
        d = party_period.setdefault(t['party_id'], {'revenue': 0, 'direct_cost': 0})
        d['revenue'] += t['billed_amount'] or 0
        # Fuel/driver-advance on a Hired trip is the owner's own money, already covered by the
        # contracted freight added below — see the same fix in route_analytics().
        fuel_and_adv = 0 if t['type'] == VEHICLE_TYPE_HIRED else (t['fuel_amount'] or 0) + (t['driver_adv_amount'] or 0)
        direct = fuel_and_adv + _trip_toll(t, export_toll_map) + (t['parking'] or 0)
        if t['type'] == VEHICLE_TYPE_HIRED:
            direct += (t['owner_fixed_amount'] or 0) if (t['owner_rate_type'] or 'PER_MT') == 'FIXED' else (t['owner_rate'] or 0) * (t['quantity'] or 0)
        d['direct_cost'] += direct
    top_customers = []
    for pid, d in sorted(party_period.items(), key=lambda kv: kv[1]['revenue'], reverse=True):
        top_customers.append((party_name.get(pid, '—'), d['revenue'], d['revenue'] - d['direct_cost'],
                               max(party_balance.get(pid, 0), 0)))

    expense_breakdown = [
        ('Fuel', curr['fuel']), ('Driver Advance', curr['driver_adv']), ('Maintenance', curr['maint']),
        ('Salary', curr['salaries']), ('Toll / Parking', curr['toll'] + curr['parking']),
        ('Other Expenses', curr['misc'] + curr['overheads'] + curr['owner_cost']),
    ]
    conn.close()

    navy = "1B2A4A"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=navy)

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Business Performance", f"{date_from} to {date_to}"])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([])
    summary_pairs = [
        ("Total Revenue", curr['revenue']), ("Net Profit", curr['net_profit']), ("Profit Margin %", curr['margin']),
        ("Outstanding Receivables", total_receivables), ("Outstanding Payables", total_payables),
        ("Cash Collected", cash_collected), ("Cash Paid", cash_paid),
        ("Collection Efficiency %", collection_efficiency), ("Operating Ratio %", operating_ratio),
        ("Total Trips", curr['trip_count']),
    ]
    for label, val in summary_pairs:
        ws.append([label, val])
    for row in ws.iter_rows(min_row=3, max_row=2 + len(summary_pairs), min_col=1, max_col=1):
        for c in row:
            c.font = Font(bold=True)

    ws2 = wb.create_sheet("Top Customers")
    for i, h in enumerate(["Customer", "Revenue", "Profit", "Outstanding"], 1):
        c = ws2.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, row in enumerate(top_customers, 2):
        for c_idx, val in enumerate(row, 1):
            ws2.cell(row=r_idx, column=c_idx, value=val)

    ws3 = wb.create_sheet("Expense Breakdown")
    for i, h in enumerate(["Category", "Amount"], 1):
        c = ws3.cell(row=1, column=i, value=h); c.font = header_font; c.fill = header_fill
    for r_idx, row in enumerate(expense_breakdown, 2):
        for c_idx, val in enumerate(row, 1):
            ws3.cell(row=r_idx, column=c_idx, value=val)

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='business_performance_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/export')
def export_excel():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from flask import send_file
    import io

    conn = get_db()
    wb = Workbook()

    navy = "1B2A4A"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=navy)

    # Trips sheet
    ws = wb.active
    ws.title = "Trips"
    headers = ["Date","LR Number","Vehicle","Party","From","To","Quantity","Rate","Billed Amount",
               "Fuel Amount","Fuel Vendor","Driver Adv Amount","Driver Adv Vendor","Party Advance","Payment Received"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows = conn.execute("""SELECT t.date, t.lr_number, v.vehicle_no, p.name, t.from_loc, t.to_loc,
                           t.quantity, t.rate, t.billed_amount, t.fuel_amount, fv.name, t.driver_adv_amount,
                           dv.name, t.party_advance, t.payment_received
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           ORDER BY t.date""").fetchall()
    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)

    # Maintenance sheet
    ws2 = wb.create_sheet("Maintenance")
    headers2 = ["Date","Vehicle","Category","Amount","Paid Amount","Vendor","Notes"]
    for i, h in enumerate(headers2, 1):
        c = ws2.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows2 = conn.execute("""SELECT m.date, v.vehicle_no, m.category, m.amount, m.paid_amount, ve.name, m.notes
                            FROM maintenance m
                            LEFT JOIN vehicles v ON m.vehicle_id=v.id
                            LEFT JOIN vendors ve ON m.vendor_id=ve.id
                            ORDER BY m.date""").fetchall()
    for r_idx, row in enumerate(rows2, 2):
        for c_idx, val in enumerate(row, 1):
            ws2.cell(row=r_idx, column=c_idx, value=val)

    # Payments sheet
    ws3 = wb.create_sheet("Payments")
    headers3 = ["Date","Type","Amount","Party","Vendor","Mode"]
    for i, h in enumerate(headers3, 1):
        c = ws3.cell(row=1, column=i, value=h)
        c.font = header_font; c.fill = header_fill
    rows3 = conn.execute("""SELECT pay.date, pay.payment_type, pay.amount, p.name, v.name, pay.mode
                            FROM payments pay
                            LEFT JOIN parties p ON pay.party_id=p.id
                            LEFT JOIN vendors v ON pay.vendor_id=v.id
                            ORDER BY pay.date""").fetchall()
    for r_idx, row in enumerate(rows3, 2):
        for c_idx, val in enumerate(row, 1):
            ws3.cell(row=r_idx, column=c_idx, value=val)

    conn.close()

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='fleet_export.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/internal/backup', methods=['POST'])
def internal_backup():
    """Off-site backup pull. Returns a consistent, gzip-compressed copy of the whole database (SQLite's online backup, safe while
    the app is running) to a caller that sends the secret in the X-Backup-Token header. The secret is the BACKUP_TOKEN
    environment variable; if it is not set the endpoint refuses everyone. Wrong tries are limited per address. Used by
    pull_backup.py, run daily from a computer that keeps the copies (Render's disk can only be read from inside this service)."""
    import gzip, hmac, tempfile
    from flask import after_this_request, send_file
    token = os.environ.get('BACKUP_TOKEN', '')
    conn = get_db()
    ip = _client_ip()
    if _auth_wait_seconds(conn, 'backup_fail_ip', ip):
        conn.close()
        return jsonify({'ok': False, 'error': 'Too many attempts.'}), 429
    if not token or not hmac.compare_digest(token, request.headers.get('X-Backup-Token', '')):
        _auth_record(conn, 'backup_fail_ip', ip)
        conn.commit()
        conn.close()
        app.logger.warning('Refused a database backup request from %s', ip)
        return jsonify({'ok': False, 'error': 'Not allowed.'}), 403
    conn.close()
    tmpdir = tempfile.mkdtemp(prefix='fleetbk_')
    raw = os.path.join(tmpdir, 'copy.db')
    gz = os.path.join(tmpdir, 'copy.db.gz')
    src = sqlite3.connect(DB, timeout=60)
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            return jsonify({'ok': False, 'error': 'The copy failed its integrity check; nothing was sent.'}), 500
    finally:
        src.close()
        dst.close()
    with open(raw, 'rb') as fi, gzip.open(gz, 'wb', compresslevel=6) as fo:
        fo.write(fi.read())
    os.remove(raw)

    @after_this_request
    def _cleanup(resp):
        try:
            os.remove(gz)
            os.rmdir(tmpdir)
        except OSError:
            pass
        return resp
    app.logger.info('Database backup sent to %s (%d KB compressed)', ip, os.path.getsize(gz) // 1024)
    return send_file(gz, mimetype='application/gzip', as_attachment=True, download_name='fleet_backup.db.gz')

@app.route('/internal/sync-tick', methods=['POST'])
def internal_sync_tick():
    """External trigger for the background sync jobs (scheduler.py), for production.

    The in-process APScheduler below (see the `if __name__=='__main__':` block) only ever starts
    when this file is run directly — gunicorn imports `app` as a module and never executes that
    block, so under gunicorn the RC/eChallan/compliance sync would otherwise silently never run
    at all. Point an external cron (or DigitalOcean's Scheduled Jobs) at this endpoint instead:

      POST /internal/sync-tick?job=sync_tick            — call roughly hourly. Internally
        self-throttled by scheduler.py's run_sync_tick against the Settings-configured
        RC/challan sync intervals; safe to call more often than that, it just no-ops until a
        job is actually due. This is the default if `job` is omitted.

      POST /internal/sync-tick?job=weekly_compliance     — call roughly weekly. Runs the
        fitness/PUC/permit compliance sync unconditionally on every call (no internal
        self-throttling), so the caller's own schedule IS the cadence here.

    Protected by a shared secret (INTERNAL_SYNC_TOKEN) sent as the X-Sync-Token header — this
    can trigger real, paid external API calls (RC/challan lookups), so it must never be
    triggerable by an anonymous request. Exempted from the login-required gate (require_login)
    since a cron obviously isn't a logged-in browser session. Fails closed: if
    INTERNAL_SYNC_TOKEN isn't configured at all, every request is rejected rather than the
    endpoint being silently wide open.
    """
    token = os.environ.get('INTERNAL_SYNC_TOKEN')
    if not token or request.headers.get('X-Sync-Token') != token:
        return {'error': 'unauthorized'}, 401
    job = request.args.get('job', 'sync_tick')
    if job == 'weekly_compliance':
        from scheduler import run_weekly_sync
        summary = run_weekly_sync(get_db)
        return {'ok': True, 'job': job, 'summary': summary}
    else:
        from scheduler import run_sync_tick
        run_sync_tick(get_db, enable_rc_sync=(os.environ.get('ENABLE_RC_SYNC') == '1'),
                      enable_challan_sync=(os.environ.get('ENABLE_CHALLAN_SYNC') == '1'))
        return {'ok': True, 'job': job}

# ============================================================================
# /app/ — separate installable mobile app section. Its own templates
# (templates/app/), its own stylesheet (static/app.css), its own manifest +
# service worker (static/app-manifest.json, static/app-sw.js). Reuses existing
# read-only helper functions (get_company_name, _period_financials,
# _pct_growth, _shift_period_back, _accounts_rows, _expiry_bucket) exactly as
# they already are — nothing here modifies or calls into any existing route,
# so the website (every route above this point) is completely unaffected by
# this section existing.
#
# app_login below is a deliberate near-duplicate of login() (same password-hash check, same
# session keys, same access_logs insert) rather than a refactor of login() itself — the two
# render completely different templates (this one has no desktop marketing hero panel, matches
# static/app.css) and redirect to app_dashboard instead of dashboard, so this app section still
# feels like an installed app end-to-end instead of dropping back into the website's own login
# screen. require_login() (below the website's own routes) sends unauthenticated /app/* requests
# here instead of to login — see the `in_app_section` check there.
# ============================================================================

@app.route('/app/login', methods=['GET', 'POST'])
def app_login():
    from werkzeug.security import check_password_hash
    error = None
    if request.method == 'POST':
        f = request.form
        username = (f.get('username') or '').strip()
        if not _username_login_format_ok(username):
            return render_template('app/login.html', error='Username must be a valid Indian mobile number or email address.',
                                    year=datetime.date.today().year)
        conn = get_db()
        blocked = _login_throttle_message(conn, username)
        if blocked:
            conn.close()
            return render_template('app/login.html', error=blocked, year=datetime.date.today().year), 429
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], f.get('password') or ''):
            if (user['status'] or 'Active') == 'Inactive':
                conn.close()
                return render_template('app/login.html', error='This account has been deactivated. Contact an administrator.',
                                        year=datetime.date.today().year)
            _auth_clear(conn, 'login_fail_user', username.lower())
            session.permanent = bool(f.get('remember_me'))
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user['is_admin']
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            session['login_at'] = now
            session['show_zoom_tip'] = True
            conn.execute("UPDATE users SET last_login=?, updated_by=?, updated_at=? WHERE id=?",
                         (now, user['id'], now, user['id']))
            conn.execute("INSERT INTO access_logs (user_id, event, date) VALUES (?,?,?)", (user['id'], 'Login', now))
            conn.commit()
            conn.close()
            return redirect(url_for('app_dashboard'))
        _login_failed(conn, username)
        conn.close()
        error = 'Invalid username or password.'
    return render_template('app/login.html', error=error, year=datetime.date.today().year)

@app.route('/app/signup', methods=['GET', 'POST'])
def app_signup():
    """Self-service account creation for the native app's login screen. New accounts are created
    least-privilege by design (role='Staff', access_level='Read Only', is_admin=0) — same safe
    default an admin gets by leaving "Access Level" unset on Settings > Users' add-user form
    (see add_user() above). There's no approval-queue status anywhere else in this app (the login
    route only ever blocks 'Inactive'), so a self-signup account can sign in immediately, same as
    one an admin creates; an admin can promote access_level/role or deactivate it afterward from
    Settings > Users like any other account.

    Username IS the mobile number or email (see USERNAME_MOBILE_RE/USERNAME_EMAIL_RE's own
    comment) — there's no separate "Mobile Number" form field anymore, so whichever one it
    matches is what gets copied into users.phone or users.email below, purely so OTP login
    (which looks a user up by the phone column) and anything else keyed on those two columns
    keep working without asking for the same information twice."""
    from werkzeug.security import generate_password_hash
    import sqlite3
    if not _signup_open():
        # Self-registration gave any stranger a working login that could read every screen. Accounts are now created only
        # by an Administrator (Profile & Settings > Users). Set ALLOW_SELF_SIGNUP=1 to switch it back on deliberately.
        return render_template('app/login.html', error='Sign-up is closed. Ask an Administrator to create your account.',
                                year=datetime.date.today().year), 403
    error = None
    form_values = {}
    if request.method == 'POST':
        f = request.form
        full_name = (f.get('full_name') or '').strip()
        username = (f.get('username') or '').strip()
        password = f.get('password') or ''
        confirm = f.get('confirm_password') or ''
        form_values = {'full_name': full_name, 'username': username}
        is_mobile = bool(USERNAME_MOBILE_RE.match(username))
        is_email = bool(USERNAME_EMAIL_RE.match(username))
        if not full_name or not username or not password:
            error = 'Please fill in your name, username and password.'
        elif not (is_mobile or is_email):
            error = 'Username must be a 10-digit mobile number or a valid email address.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            conn = get_db()
            existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            phone = username if is_mobile else None
            email = username if is_email else None
            # Catches the gap the username-uniqueness check above can't: an admin-created account
            # (Settings > Users) that has this same phone/email in its own separate phone/email
            # column, under a completely different username — e.g. an admin added "Rahul" with
            # phone 9876543210 before this self-signup ever happened.
            dup = None if existing else _duplicate_user_contact(conn, phone, email)
            if existing:
                conn.close()
                error = f'Username "{username}" is already taken.'
            elif dup:
                conn.close()
                error = dup
            else:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                try:
                    cur = conn.execute("""INSERT INTO users (username, password_hash, role, is_admin, phone, email,
                                    full_name, access_level, status, created_at)
                                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                 (username, generate_password_hash(password), 'Staff', 0, phone, email,
                                  full_name, 'Read Only', 'Active', now))
                    user_id = cur.lastrowid
                    session.permanent = False
                    session['user_id'] = user_id
                    session['username'] = username
                    session['is_admin'] = 0
                    session['login_at'] = now
                    session['show_zoom_tip'] = True
                    conn.execute("INSERT INTO access_logs (user_id, event, date) VALUES (?,?,?)", (user_id, 'Signup', now))
                    conn.commit()
                    conn.close()
                    return redirect(url_for('app_dashboard'))
                except sqlite3.IntegrityError:
                    conn.close()
                    error = f'Username "{username}" is already taken.'
    return render_template('app/signup.html', error=error, form_values=form_values,
                            year=datetime.date.today().year)

def _current_fy_bounds(today):
    """(date_from, date_to, label) for the financial year (April-start) containing `today`,
    computed from `today` rather than a hardcoded literal — so a "current FY" default keeps
    tracking the real current FY across year boundaries instead of going stale the way the old
    literal '2026-04-01'/'2027-03-31' defaults on Fleet Utilization/Performance/Route Analytics
    would have once FY 2027-28 arrived."""
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    return (f"{fy_start_year}-04-01", f"{fy_start_year + 1}-03-31",
            f"FY {fy_start_year}-{(fy_start_year + 1) % 100:02d}")

def _resolve_analysis_period(period, c_from, c_to, today):
    """Period vocabulary for the 4 Business Overview analysis pages (Route Analytics/Fleet
    Utilization/Performance/Business Performance) — Today/This Week/This Month/Last Month/
    current FY/previous FY/Custom Range. Unlike the Dashboard's own _resolve_dashboard_period
    (which defaults to This Month), this defaults to the CURRENT FINANCIAL YEAR when no period
    is selected — these are longer-horizon analysis pages, not the daily-ops Dashboard, so a
    full-year default is more useful than a single month, per instruction."""
    cur_from, cur_to, cur_label = _current_fy_bounds(today)
    if period == 'fyprev':
        prev_start = int(cur_from[:4]) - 1
        return (f"{prev_start}-04-01", f"{prev_start + 1}-03-31",
                f"FY {prev_start}-{(prev_start + 1) % 100:02d}")
    if period == 'today':
        d = today.isoformat()
        return d, d, 'Today'
    if period == 'week':
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        return monday.isoformat(), sunday.isoformat(), 'This Week'
    if period == 'month':
        mf, mt = _month_bounds(today.year, today.month)
        return mf, mt, 'This Month'
    if period == 'lastmonth':
        last_month_end = today.replace(day=1) - datetime.timedelta(days=1)
        mf, mt = _month_bounds(last_month_end.year, last_month_end.month)
        return mf, mt, 'Last Month'
    if period == 'custom' and c_from and c_to:
        c_from, c_to = _normalized_date_range(c_from, c_to)
        try:
            lbl = (datetime.datetime.strptime(c_from, '%Y-%m-%d').strftime('%d %b') + ' – ' +
                   datetime.datetime.strptime(c_to, '%Y-%m-%d').strftime('%d %b %Y'))
        except ValueError:
            lbl = f"{c_from} to {c_to}"
        return c_from, c_to, lbl
    # '' (no selection yet), 'fy', or anything unrecognized -> current FY, the default.
    return cur_from, cur_to, cur_label

def _analysis_period_presets(today):
    """(key, label) pairs for the Select Period sheet shared by the 4 Business Overview analysis
    pages — same 6 presets everywhere, FY labels computed from `today` so they're never stale."""
    cur_from, cur_to, cur_label = _current_fy_bounds(today)
    prev_start = int(cur_from[:4]) - 1
    prev_label = f"FY {prev_start}-{(prev_start + 1) % 100:02d}"
    return [('fy', cur_label), ('fyprev', prev_label), ('today', 'Today'), ('week', 'This Week'),
            ('month', 'This Month'), ('lastmonth', 'Last Month')]

DASHBOARD_PERIODS = ('today', 'week', 'month', 'lastmonth', 'fy2627', 'fy2526', 'custom')

def _resolve_dashboard_period(period, c_from, c_to, today):
    """Turns the Dashboard's period pill selection into a concrete (date_from, date_to, label) —
    the ONE global filter that drives Business Overview / Revenue vs Expenses / Top Routes / Top
    Expenses on the native Dashboard, matching the mockup's own 'Global Period Filter Logic'
    table. 'custom' falls back to This Month if the two date fields aren't both present, same
    graceful-default behavior as every other filter sheet in this app."""
    if period == 'today':
        d = today.isoformat()
        return d, d, 'Today'
    if period == 'week':
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        return monday.isoformat(), sunday.isoformat(), 'This Week'
    if period == 'lastmonth':
        last_month_end = today.replace(day=1) - datetime.timedelta(days=1)
        mf, mt = _month_bounds(last_month_end.year, last_month_end.month)
        return mf, mt, 'Last Month'
    if period == 'fy2627':
        return '2026-04-01', '2027-03-31', 'FY 2026-27'
    if period == 'fy2526':
        return '2025-04-01', '2026-03-31', 'FY 2025-26'
    if period == 'custom' and c_from and c_to:
        c_from, c_to = _normalized_date_range(c_from, c_to)
        return c_from, c_to, f"{c_from} to {c_to}"
    mf, mt = _month_bounds(today.year, today.month)
    return mf, mt, 'This Month'

@app.route('/app/dashboard')
def app_dashboard():
    conn = get_db()
    today = datetime.date.today()
    today_str = today.isoformat()

    # ---------- Top bar ----------
    # Full Name, not username, drives both the greeting and the avatar initials — a self-signed-up
    # account's username IS its mobile number now (see app_signup()'s own comment), so falling
    # back to username here the way this used to unconditionally do reads as "Hi 9668348621" and
    # an avatar showing "9" instead of a real name, even though the account's real name is right
    # there on file. Same "full_name or username" fallback _app_user_display() (used by every
    # other /app/ page's own top bar) already applies — this route just never went through that
    # shared helper for these two values specifically. Falls back to the raw username only for the
    # rare account with no full_name on file at all (e.g. the plain-name legacy accounts that
    # predate this rule), same as before.
    display_name = session.get('username') or 'User'
    if session.get('user_id'):
        u_row = conn.execute("SELECT full_name FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u_row and u_row['full_name']:
            display_name = u_row['full_name']
    user_initials = ''.join([p[0] for p in display_name.replace('_', ' ').replace('.', ' ').split()[:2]]).upper() or 'U'
    greeting_name = display_name.replace('_', ' ').replace('.', ' ').split()[0].title()
    # company_name deliberately NOT computed here — inject_company_name() (context_processor,
    # near the top of this file) already runs this exact same get_company_name(session.get(
    # 'company_id', 1)) call automatically for every render_template(), so a second explicit call
    # here was a redundant query whose result just got overridden by the identical context-
    # processor value anyway. Every render_template() call below that used to pass
    # company_name=company_name now just omits it and gets the same value for free.

    # ---------- Today's Fleet — real, right-now operational numbers. Always current/live,
    # deliberately unaffected by the period filter below (same split the mockup's own "Global
    # Period Filter Logic" table specifies: Today's Fleet / Action Required / Fleet Status /
    # Recent Activity are all "Always Current"). ----------
    total_vehicles = conn.execute("SELECT COUNT(*) FROM vehicles WHERE vehicle_no IS NOT NULL").fetchone()[0]
    on_trip_count = conn.execute("""SELECT COUNT(DISTINCT vehicle_id) FROM trips
                                    WHERE end_date IS NULL AND date<=? AND vehicle_id IS NOT NULL""", (today_str,)).fetchone()[0]
    today_trips_completed = conn.execute("SELECT COUNT(*) FROM trips WHERE end_date=?", (today_str,)).fetchone()[0]
    today_revenue = conn.execute("SELECT COALESCE(SUM(billed_amount),0) FROM trips WHERE date=?", (today_str,)).fetchone()[0]

    # ---------- Action Required — 2 real, current tiles, each reusing the app's OWN existing
    # single source of truth instead of a new one-off query, so this can never show a different
    # number than what's already shown elsewhere in the app for the same thing. "Invoices
    # Pending" and "Services Due" were both in the original reference design but are deliberately
    # left out: Invoices Pending per explicit instruction, and Services Due because
    # maintenance.next_service_date — the only real column that could back it — is never actually
    # written by any current form (every one of the 452 real maintenance rows has it empty), so
    # the tile could only ever show 0. Both can be added back later without touching anything
    # else here. ----------
    # Same exact query as inject_challan_alerts() (app.py) — the context processor that powers
    # the topbar bell + Vehicles nav badge on every page. Counts own-fleet VEHICLES with a
    # pending challan, not raw challan rows, so this tile always agrees with that badge.
    challans_pending = conn.execute(
        "SELECT COUNT(*) FROM vehicles WHERE type IS NOT NULL AND type='own' AND COALESCE(challan_count,0) > 0"
    ).fetchone()[0]
    # Same real compliance_service.get_compliance_alerts() the website's own dashboard() already
    # calls for its own Alerts feed — per-document-type warning windows (Insurance 30d/Fitness
    # 15d/PUC 7d/Permit 30d), own-fleet vehicles, and prefers a synced vehicle_compliance record's
    # date over the raw vehicles.* column where one exists. Counts documents, not vehicles, so one
    # vehicle with 2 expiring documents counts as 2 — matches the tile's own name.
    compliance_alerts = cs.get_compliance_alerts(conn)
    # Split by the SAME two real statuses the website's own Vehicles page comp_colors scheme uses
    # (Expired = red, Expiring Soon = accent orange, see vehicles_list.html) — each alert here
    # already carries that exact status string, so this is just a partition, not a new judgment
    # call about what counts as which. Shown as two separately-colored chips instead of one flat
    # amber "Documents Expiring" total, so a real Expired document (already overdue) reads with
    # the same urgency here as it does on the Vehicles page, not lumped in with a merely
    # upcoming one.
    documents_expired = sum(1 for a in compliance_alerts if a['status'] == 'Expired')
    documents_expiring_soon = sum(1 for a in compliance_alerts if a['status'] == 'Expiring Soon')
    documents_expiring = documents_expired + documents_expiring_soon
    alert_count = (1 if challans_pending else 0) + (1 if documents_expiring else 0)
    alert_bits = []
    if challans_pending:
        alert_bits.append(f"{challans_pending} challan{'s' if challans_pending != 1 else ''} pending")
    if documents_expired:
        alert_bits.append(f"{documents_expired} document{'s' if documents_expired != 1 else ''} expired")
    if documents_expiring_soon:
        alert_bits.append(f"{documents_expiring_soon} document{'s' if documents_expiring_soon != 1 else ''} expiring soon")
    alert_summary = ', '.join(alert_bits)

    # ---------- Global Period Filter — the ONE filter that drives Business Overview, Revenue vs
    # Expenses, Top Routes and Top Expenses below (mockup's own logic table). A separate Vehicle
    # filter icon narrows all 4 of those same sections further, independent of which period is
    # selected. ----------
    period = request.args.get('period', 'month')
    if period not in DASHBOARD_PERIODS:
        period = 'month'
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    f_vehicle = request.args.get('vehicle', '')
    bo_from, bo_to, period_label = _resolve_dashboard_period(period, c_from, c_to, today)

    dfin = _dashboard_financials_data(conn, bo_from, bo_to, f_vehicle)
    prev_from, prev_to = _shift_period_back(bo_from, bo_to)
    prev_dfin = _dashboard_financials_data(conn, prev_from, prev_to, f_vehicle)
    bo_trip_growth = _pct_growth(dfin['trip_count'], prev_dfin['trip_count'])
    bo_revenue_growth = _pct_growth(dfin['total_revenue'], prev_dfin['total_revenue'])
    bo_expense_growth = _pct_growth(dfin['total_expenses'], prev_dfin['total_expenses'])
    # Receivable stays a running, un-dated balance — same as the website's own dashboard(), which
    # also computes total_receivables from _accounts_rows(conn) with no date filter at all.
    acct_rows = _accounts_rows(conn)
    # Split by balance sign only — exactly like the website Dashboard and the Ledger page: an
    # overpaid vendor (balance > 0) is a real receivable and an overpaid party (balance < 0) is a
    # real payable, so neither is dropped just because of its role.
    total_receivables = sum(r['balance'] for r in acct_rows if r['balance'] > 0)
    receivable_party_count = sum(1 for r in acct_rows if r['balance'] > 0.01)
    total_payables = sum(-r['balance'] for r in acct_rows if r['balance'] < 0)
    payable_count = sum(1 for r in acct_rows if r['balance'] < -0.01)

    dash_vehicles = conn.execute("SELECT DISTINCT vehicle_no FROM vehicles WHERE vehicle_no IS NOT NULL ORDER BY vehicle_no").fetchall()

    # ---------- Revenue vs Expenses — trailing 6 calendar months ending at the selected period's
    # own end month (so picking "Last Month" or a past Custom Range slides the trend window back
    # with it, not just "always the last 6 months from today"), same vehicle filter, same real
    # _dashboard_financials_data() totals used above. ----------
    end_month_anchor = datetime.date.fromisoformat(bo_to).replace(day=1)
    trend_months = []
    cur = end_month_anchor
    for _ in range(6):
        trend_months.append(cur)
        cur = (cur - datetime.timedelta(days=1)).replace(day=1)
    trend_months.reverse()
    revexp_trend = []
    for m in trend_months:
        mf, mt = _month_bounds(m.year, m.month)
        mfin = _dashboard_financials_data(conn, mf, mt, f_vehicle)
        revexp_trend.append({'label': m.strftime('%b'), 'revenue': mfin['total_revenue'], 'expenses': mfin['total_expenses']})
    revexp_max = max([max(m['revenue'], m['expenses']) for m in revexp_trend], default=1) or 1
    for m in revexp_trend:
        m['rev_pct'] = round(m['revenue'] / revexp_max * 100, 1) if revexp_max else 0
        m['exp_pct'] = round(m['expenses'] / revexp_max * 100, 1) if revexp_max else 0

    # ---------- Top Routes — same real per-route rollup Route Analytics uses, extended with an
    # optional vehicle filter (see _route_analytics_data()) for this page's own Vehicle icon. ----------
    route_data = _route_analytics_data(conn, bo_from, bo_to, '', '', '', vehicle_no=f_vehicle)
    top_routes = route_data['top5_by_revenue'][:3]
    more_routes_count = max(route_data['total_routes'] - len(top_routes), 0)

    # ---------- Top Expenses — same real category breakdown as the website's own dashboard()
    # Expense Breakdown chart (_dashboard_financials_data() above), just re-sorted by size and
    # trimmed to the top 5 for this card. ----------
    top_expenses = [e for e in dfin['exp_breakdown_ranked'] if e['value'] > 0][:5]

    # ---------- Fleet Status — three real, mutually-exclusive categories that always sum to
    # total_vehicles. Always current/live, same as Today's Fleet above. A fourth "Waiting" bucket
    # isn't something this data model can honestly distinguish today — no field records
    # "scheduled but not yet started" — so it's left out rather than inventing a number for it. ----------
    in_workshop = conn.execute("SELECT COUNT(*) FROM vehicles WHERE status='In Maintenance'").fetchone()[0]
    vs_idle = max(total_vehicles - on_trip_count - in_workshop, 0)
    fleet_status_total = total_vehicles or 1
    fs_on_trip_pct = round(on_trip_count / fleet_status_total * 100, 1)
    fs_workshop_pct = round(in_workshop / fleet_status_total * 100, 1)
    fs_idle_pct = round(vs_idle / fleet_status_total * 100, 1)

    # ---------- Recent activity — same real event sources the website's Dashboard draws from,
    # queried independently here rather than calling into dashboard() itself. Always current. ----------
    activities = []
    for r in conn.execute("""SELECT t.lr_number, v.vehicle_no, t.billed_amount, t.updated_at FROM trips t
                             LEFT JOIN vehicles v ON t.vehicle_id=v.id
                             WHERE t.end_date IS NOT NULL AND t.updated_at IS NOT NULL ORDER BY t.updated_at DESC LIMIT 5""").fetchall():
        activities.append({'icon': 'trip', 'title': 'Trip Completed', 'sub': f"{r['lr_number'] or '—'} • {r['vehicle_no'] or '—'}",
                            'amount': r['billed_amount'] or 0, 'ts': r['updated_at']})
    for r in conn.execute("""SELECT ib.invoice_number, ib.created_at, COALESCE(p.name, v.name) as who
                             FROM invoice_batches ib LEFT JOIN parties p ON ib.party_id=p.id LEFT JOIN vendors v ON ib.vendor_id=v.id
                             WHERE ib.created_at IS NOT NULL ORDER BY ib.created_at DESC LIMIT 5""").fetchall():
        activities.append({'icon': 'invoice', 'title': 'Invoice Created', 'sub': f"{r['invoice_number']} • {r['who'] or '—'}",
                            'amount': None, 'ts': r['created_at']})
    for r in conn.execute("""SELECT m.category, m.amount, v.vehicle_no, m.created_at FROM maintenance m
                             LEFT JOIN vehicles v ON m.vehicle_id=v.id
                             WHERE m.created_at IS NOT NULL ORDER BY m.created_at DESC LIMIT 5""").fetchall():
        activities.append({'icon': 'expense', 'title': 'Expense Added', 'sub': f"{r['category'] or 'Maintenance'} • {r['vehicle_no'] or '—'}",
                            'amount': r['amount'] or 0, 'ts': r['created_at']})

    def _norm_ts(raw):
        if not raw:
            return None
        try:
            return datetime.datetime.strptime(raw[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    for a in activities:
        a['_dt'] = _norm_ts(a['ts'])
    activities = [a for a in activities if a['_dt']]
    activities.sort(key=lambda a: a['_dt'], reverse=True)
    activities = activities[:5]
    for a in activities:
        a['time_label'] = 'Today, ' + a['_dt'].strftime('%I:%M %p') if a['_dt'].date() == today else (
            'Yesterday, ' + a['_dt'].strftime('%I:%M %p') if a['_dt'].date() == today - datetime.timedelta(days=1)
            else a['_dt'].strftime('%d %b, %I:%M %p'))

    conn.close()
    return render_template('app/dashboard.html', active='dashboard',
        user_initials=user_initials, greeting_name=greeting_name,
        today_display=today.strftime('%d %b %Y'), notif_count=alert_count,
        total_vehicles=total_vehicles, on_trip_count=on_trip_count,
        today_trips_completed=today_trips_completed, today_revenue=today_revenue,
        alert_count=alert_count, alert_summary=alert_summary,
        challans_pending=challans_pending, documents_expiring=documents_expiring,
        documents_expired=documents_expired, documents_expiring_soon=documents_expiring_soon,
        bo_trips=dfin['trip_count'], bo_trip_growth=bo_trip_growth,
        bo_revenue=dfin['total_revenue'], bo_revenue_growth=bo_revenue_growth,
        bo_expenses=dfin['total_expenses'], bo_expense_growth=bo_expense_growth,
        total_receivables=total_receivables, receivable_party_count=receivable_party_count,
        total_payables=total_payables, payable_count=payable_count,
        revexp_trend=revexp_trend, revexp_max=revexp_max,
        top_routes=top_routes, more_routes_count=more_routes_count,
        top_expenses=top_expenses,
        vs_on_trip=on_trip_count, vs_workshop=in_workshop, vs_idle=vs_idle,
        fs_on_trip_pct=fs_on_trip_pct, fs_workshop_pct=fs_workshop_pct, fs_idle_pct=fs_idle_pct,
        activities=activities,
        period=period, period_label=period_label, f_date_from=c_from, f_date_to=c_to,
        f_vehicle=f_vehicle, dash_vehicles=dash_vehicles)

@app.route('/app/trips/add', methods=['GET', 'POST'])
def app_add_trip():
    """Mobile Add Trip — a 4-step wizard (Trip Details / Cost & Charges / Other Items / Review)
    over the exact same fields, validation, and insert logic as the website's own Add Trip page
    (templates/trip_form.html, add_trip() above): both call _duplicate_lr_trip() and
    _insert_trip_from_form(), so the two can never quietly disagree on what a trip is or how its
    billed_amount is computed. Deliberately does NOT reproduce the demo mockup's "Trip ID (Auto)"
    display, per-leg Transit Point/date-time/contact fields, or a server-side "Save Draft" — none
    of those correspond to a real column this app actually has; the wizard only ever asks for
    fields that exist on trips (see _insert_trip_from_form) so nothing entered here is silently
    discarded. "Save Draft" in the UI is a real feature, just client-side only (localStorage on
    the phone), not a server-side draft trips row — there's no draft/status concept on trips
    for a server-side draft to belong to.
    """
    conn = get_db()
    if request.method == 'POST':
        f = request.form
        dup = _duplicate_lr_trip(conn, f.get('lr_number'))
        form_error = None if dup else _trip_form_error(f)
        if dup or form_error:
            conn.close()
            vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
            conn2 = get_db()
            employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
            vehicle_type_map = _vehicle_type_map(conn2)
            location_options = _get_route_locations(conn2)
            conn2.close()
            error = form_error or (f"LR Number \"{(f.get('lr_number') or '').strip()}\" is already used on trip #{dup['id']} "
                                    f"({dup['date']}, {dup['vehicle_no'] or 'no vehicle'}).")
            return render_template('app/trip_add.html', t=f, vehicles=[v['vehicle_no'] for v in vehicles],
                                    combined_names=combined_names, employees=[e['name'] for e in employees],
                                    vehicle_type_map=vehicle_type_map, location_options=location_options,
                                    error=error,
                                    mode='add', custom_items=[])
        _insert_trip_from_form(conn, f)
        conn.commit()
        conn.close()
        return redirect(url_for('app_dashboard', trip_added='1'))
    conn.close()
    vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
    conn2 = get_db()
    employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
    vehicle_type_map = _vehicle_type_map(conn2)
    location_options = _get_route_locations(conn2)
    conn2.close()
    return render_template('app/trip_add.html', t={}, vehicles=[v['vehicle_no'] for v in vehicles],
                            combined_names=combined_names, employees=[e['name'] for e in employees],
                            vehicle_type_map=vehicle_type_map, location_options=location_options,
                            error=None,
                            mode='add', custom_items=[])

@app.route('/app/trips/<int:trip_id>/edit', methods=['GET', 'POST'])
def app_edit_trip(trip_id):
    """Mobile Update Trip — the exact same 4-step wizard as Add Trip (app_add_trip() above),
    pre-filled with this trip's real values, over the exact same real update logic the website's
    own Edit Trip page uses (templates/trip_form.html, edit_trip() above): both call
    _duplicate_lr_trip(..., exclude_trip_id=trip_id) and _update_trip_from_form(), so the two can
    never quietly disagree. Vehicle is locked (read-only in the wizard) — a trip's vehicle_id is
    never changeable after creation, same rule edit_trip() enforces server-side too. Existing
    custom "Other Items" are pre-loaded into Step 3 (see tw_existing_items in trip_add.html) —
    without that, saving would silently wipe them, since _save_trip_custom_items() always
    replaces a trip's items from whatever the submitted form actually contains."""
    conn = get_db()

    def _load_form_context():
        vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
        conn2 = get_db()
        employees = conn2.execute("SELECT name FROM employees ORDER BY name").fetchall()
        vehicle_type_map = _vehicle_type_map(conn2)
        location_options = _get_route_locations(conn2)
        conn2.close()
        return ([v['vehicle_no'] for v in vehicles], combined_names, [e['name'] for e in employees],
                vehicle_type_map, location_options)

    if request.method == 'POST':
        f = request.form
        dup = _duplicate_lr_trip(conn, f.get('lr_number'), exclude_trip_id=trip_id)
        form_error = None if dup else _trip_form_error(f, check_advance=False)
        if dup or form_error:
            custom_item_rows = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, ve.name as vendor_name
                                               FROM invoice_items ii LEFT JOIN vendors ve ON ii.vendor_id=ve.id
                                               WHERE ii.trip_id=?""", (trip_id,)).fetchall()
            custom_items = [{'description': r['description'], 'item_type': r['item_type'], 'rate': r['amount'], 'quantity': 1,
                              'vendor_name': r['vendor_name'] or ''} for r in custom_item_rows]
            conn.close()
            vehicles, combined_names, employees, vehicle_type_map, location_options = _load_form_context()
            error = form_error or (f"LR Number \"{(f.get('lr_number') or '').strip()}\" is already used on trip #{dup['id']} "
                                    f"({dup['date']}, {dup['vehicle_no'] or 'no vehicle'}).")
            return render_template('app/trip_add.html', t=f, vehicles=vehicles, combined_names=combined_names,
                                    employees=employees, vehicle_type_map=vehicle_type_map,
                                    location_options=location_options, error=error,
                                    mode='edit', trip_id=trip_id, custom_items=custom_items)
        ok, error = _update_trip_from_form(conn, trip_id, f)
        if not ok:
            conn.close()
            return redirect(url_for('app_trips'))
        conn.commit()
        conn.close()
        return redirect(url_for('app_trip_detail', trip_id=trip_id, updated='1'))

    trip = conn.execute("""SELECT t.*, v.vehicle_no, p.name as party_name,
                           fv.name as fuel_vendor_name, dv.name as driveradv_vendor_name, mv.name as misc_vendor_name
                           FROM trips t
                           LEFT JOIN vehicles v ON t.vehicle_id=v.id
                           LEFT JOIN parties p ON t.party_id=p.id
                           LEFT JOIN vendors fv ON t.fuel_vendor_id=fv.id
                           LEFT JOIN vendors dv ON t.driver_adv_vendor_id=dv.id
                           LEFT JOIN vendors mv ON t.misc_vendor_id=mv.id
                           WHERE t.id=?""", (trip_id,)).fetchone()
    if not trip:
        conn.close()
        return redirect(url_for('app_trips'))
    custom_item_rows = conn.execute("""SELECT ii.description, ii.amount, ii.item_type, ve.name as vendor_name
                                       FROM invoice_items ii LEFT JOIN vendors ve ON ii.vendor_id=ve.id
                                       WHERE ii.trip_id=?""", (trip_id,)).fetchall()
    custom_items = [{'description': r['description'], 'item_type': r['item_type'], 'rate': r['amount'], 'quantity': 1,
                      'vendor_name': r['vendor_name'] or ''} for r in custom_item_rows]
    conn.close()
    vehicles, combined_names, employees, vehicle_type_map, location_options = _load_form_context()
    return render_template('app/trip_add.html', t=dict(trip), vehicles=vehicles, combined_names=combined_names,
                            employees=employees, vehicle_type_map=vehicle_type_map,
                            location_options=location_options, error=None,
                            mode='edit', trip_id=trip_id, custom_items=custom_items)

@app.route('/app/maintenance')
def app_maintenance():
    """Mobile Maintenance — Overview and Service tabs are native; Tyres/Battery/Urea/Toll/
    Compliance still bridge to the real website (see the tab strip in app/maintenance.html).
    Overview: real numbers straight from _maintenance_overview_data() (the exact same
    computation the website's own Maintenance Overview tab uses). Trimmed to the data points
    that matter most on a small screen (no Avg Cost/KM or Fleet Availability tiles; trend
    simplified to a 6-month bar strip instead of a plotted SVG line) — nothing here is
    fabricated, it's a subset of the same real dict the website renders in full.
    Service: real numbers/rows straight from _maintenance_service_rows() (shared with the
    website's own Service tab). Charts (Service by Type / Monthly Trend / Top Costly Vehicles)
    are left off this pass — the reference design for this tab didn't include them either.
    "Load More" (growing show count) instead of the website's numbered pager, same convention
    every other list in this app already uses.
    """
    conn = get_db()
    tab = request.args.get('tab', 'overview')
    if tab not in ('overview', 'service'):
        tab = 'overview'
    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'

    if tab == 'service':
        d = _maintenance_service_rows(conn)
        total_count = d['total_count']
        try:
            show = int(request.args.get('show', 15))
        except (TypeError, ValueError):
            show = 15
        show = max(15, show)
        has_more = total_count > show
        page_rows = d['rows'][:show]
        vehicles, parties, vendors, combined_names = _get_autocomplete_lists()
        workshops = conn.execute("SELECT DISTINCT name FROM vendors WHERE COALESCE(status,'Active')='Active' ORDER BY name").fetchall()
        service_types = sorted(set((r['service_type'] or r['category'] or '') for r in d['all_rows']
                                    if _maintenance_classify(r['category'], r['service_type']) == 'Service' and (r['service_type'] or r['category'])))
        conn.close()
        return render_template('app/maintenance.html', user_initials=user_initials,
            active='maintenance', notif_count=0, tab='service',
            rows=page_rows, total_count=total_count, show=show, has_more=has_more,
            total_cost=d['total_cost'], open_jobs=d['open_jobs'], completed_jobs=d['completed_jobs'],
            breakdown_count=d['breakdown_count'], due_soon=d['due_soon'], avg_cost=d['avg_cost'],
            vehicles=[v['vehicle_no'] for v in vehicles], workshops=[w['name'] for w in workshops],
            service_types=service_types, combined_names=combined_names, checklist_items=MAINTENANCE_CHECKLIST,
            f_vehicle=d['f_vehicle'], f_workshop=d['f_workshop'], f_service_type=d['f_service_type'],
            f_status=d['f_status'], f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_search=d['f_search'])

    # Overview month: last month by default, any of the previous 12 via ?month=YYYY-MM (the
    # selector in the period bar) — the website tab offers a date range, so the app mustn't be
    # stuck on one month.
    last_month_end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    month_options = []
    cur_m = datetime.date.today().replace(day=1)
    for _ in range(13):
        month_options.append({'value': cur_m.strftime('%Y-%m'), 'label': cur_m.strftime('%b %Y')})
        cur_m = (cur_m - datetime.timedelta(days=1)).replace(day=1)
    chosen = request.args.get('month', '')
    if chosen not in [m['value'] for m in month_options]:
        chosen = last_month_end.strftime('%Y-%m')
    year_m, mon_m = int(chosen[:4]), int(chosen[5:7])
    date_from, date_to = _month_bounds(year_m, mon_m)
    d = _maintenance_overview_data(conn, date_from, date_to)
    conn.close()

    return render_template('app/maintenance.html', user_initials=user_initials,
        notif_count=d['pending_count'] + len(d['actions']), active='maintenance', tab='overview',
        period_label=datetime.datetime.strptime(date_from, '%Y-%m-%d').strftime('%b %Y'),
        month_options=month_options, chosen_month=chosen,
        total_fleet=d['total_fleet'], vehicles_serviced=d['vehicles_serviced'], pending_count=d['pending_count'],
        total_cost=d['total_cost'], total_unpaid=d['total_unpaid'],
        health_bands=d['health_bands'],
        actions=d['actions'], trend=d['trend'], trend_max=d['trend_max'])

@app.route('/app/maintenance/service/add', methods=['POST'])
def app_add_service():
    """In-app "Add Service" modal (maintenance.html's Service tab) posts here via fetch instead
    of the website's own modal — same _insert_service_from_form() the website itself uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_service_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_maintenance', tab='service')})

@app.route('/app/maintenance/service/<int:m_id>/edit', methods=['POST'])
def app_edit_service(m_id):
    """Tap a Service Records row -> read-only detail popup -> "Update" -> this real edit form,
    posted here via fetch instead of the website's own edit form — same
    _update_service_from_form() the website's own edit_service() uses, so the two can never
    quietly accept a different set of fields on edit than either already accepts on create."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    _update_service_from_form(conn, m_id, request.form)
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_maintenance', tab='service')})

@app.route('/app/tyres')
def app_tyres():
    """Mobile Tyre Management — its own page (not a Maintenance sub-tab) reached from the bottom
    nav's More sheet, matching the reference design. Real numbers straight from
    _tyre_management_data() (shared with the website's own Maintenance > Tyres tab) — KPIs (Total
    Tyres/In Stock/Puncture Repair/Total Spent), the real Tyre Stock list, and the real Tyre
    Entries list. Vehicle Tyre Layout diagram and the cost trend/breakdown charts are left off
    this pass — the reference design for this page didn't include them either, same call already
    made for Maintenance > Service. "Load More" (growing show count) on Tyre Entries instead of
    the website's numbered pager, same convention every other list in this app already uses; Tyre
    Stock is shown in full (unpaginated) since it's normally a short list."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    d = _tyre_management_data(conn)
    total_count = d['total_count']
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = d['rows'][:show]
    conn.close()
    return render_template('app/tyres.html', company_name=company_name, user_initials=user_initials,
        active='maintenance', notif_count=0,
        rows=page_rows, total_count=total_count, show=show, has_more=has_more,
        total_cost=d['total_cost'], unpaid_total=d['unpaid_total'], vehicles_covered=d['vehicles_covered'],
        puncture_count=d['puncture_count'], avg_cost=d['avg_cost'],
        stock_rows=d['stock_rows'], stock_in_count=d['stock_in_count'], stock_new_count=d['stock_new_count'],
        stock_resole_count=d['stock_resole_count'], stock_value=d['stock_value'],
        vehicles=[v['vehicle_no'] for v in d['vehicles']], tyre_vendors=[v['name'] for v in d['tyre_vendors']],
        combined_names=d['combined_names'], tyre_brands=d['tyre_brands'],
        tyre_actions=TYRE_ACTIONS, tyre_positions=TYRE_POSITIONS,
        f_vehicle=d['f_vehicle'], f_action=d['f_action'], f_position=d['f_position'], f_vendor=d['f_vendor'],
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_search=d['f_search'])

@app.route('/app/tyres/add', methods=['POST'])
def app_add_tyre():
    """"New Tyre" mode of the in-app Add Tyre modal (tyres.html) — posts here via fetch instead of
    the website's own #addTyreModal, same _insert_tyre_from_form() the website itself uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_tyre_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_tyres')})

@app.route('/app/tyres/stock/add', methods=['POST'])
def app_add_tyre_stock():
    """"Add to Stock" mode of the in-app Add Tyre modal — same _insert_tyre_stock_from_form() the
    website's own #addTyreModal (Add to Stock mode) uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_tyre_stock_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_tyres')})

@app.route('/app/tyres/stock/<int:stock_id>/install', methods=['POST'])
def app_install_tyre_stock(stock_id):
    """"Install from Stock" mode of the in-app Add Tyre modal — same _install_tyre_stock_from_form()
    the website's own #addTyreModal (Install from Stock mode) uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _install_tyre_stock_from_form(conn, stock_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_tyres')})

@app.route('/app/battery')
def app_battery():
    """Mobile Battery Management — its own top-level page (not a Maintenance sub-tab), reached
    from the bottom nav's More sheet and from the Maintenance tab strip, same pattern as Tyres.
    Real numbers straight from _battery_management_data() (shared with the website's own
    Maintenance > Battery tab) — KPIs (Total Batteries/Good Condition/Needs Attention/Dead/Avg.
    Health), and the real filtered battery list. Warranty/Low-Health panels and the cost trend
    chart are left off this pass, same call already made for Tyres/Service. "Load More" (growing
    show count) instead of the website's numbered pager, same convention every list in this app
    already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    d = _battery_management_data(conn)
    total_count = d['total_count']
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = d['rows'][:show]
    conn.close()
    return render_template('app/battery.html', company_name=company_name, user_initials=user_initials,
        active='maintenance', notif_count=0,
        rows=page_rows, total_count=total_count, show=show, has_more=has_more,
        total_batteries=d['total_batteries'], in_use=d['in_use'], in_stock=d['in_stock'],
        weak_soon=d['weak_soon'], dead_count=d['dead_count'], avg_health_pct=d['avg_health_pct'],
        vehicles=[v['vehicle_no'] for v in d['vehicles']], battery_vendors=[v['name'] for v in d['battery_vendors']],
        combined_names=d['combined_names'], battery_brands=d['battery_brands'], battery_types=BATTERY_TYPES,
        stock_batteries=d['stock_batteries'],
        f_vehicle=d['f_vehicle'], f_brand=d['f_brand'], f_status=d['f_status'], f_battery_type=d['f_battery_type'],
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_search=d['f_search'])

@app.route('/app/battery/add', methods=['POST'])
def app_add_battery():
    """"Install to Vehicle" / "Add to Stock" modes of the in-app Add Battery modal (battery.html)
    — posts here via fetch instead of the website's own #addBatteryModal, same
    _insert_battery_from_form() the website itself uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_battery_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_battery')})

@app.route('/app/battery/<int:battery_id>/install', methods=['POST'])
def app_install_battery(battery_id):
    """"Install from Stock" mode of the in-app Add Battery modal — same
    _install_battery_from_form() the website's own #addBatteryModal (Install from Stock mode) and
    per-row Install button both use."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _install_battery_from_form(conn, battery_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_battery')})

@app.route('/app/urea')
def app_urea():
    """Mobile Urea (AdBlue/DEF) Management — its own top-level page (not a Maintenance sub-tab),
    reached from the bottom nav's FAB/More sheet and the Maintenance tab strip, same pattern as
    Tyres/Battery — was a website-bridge (↗) link before this pass. Real numbers straight from
    _urea_management_data() (shared with the website's own Maintenance > Urea tab) — KPIs
    (Current Stock/Total Value/Today's Usage/This Month Usage/Avg Consumption/Low Stock Alerts),
    the real filtered transaction list, the 30-day stock trend, and Top Vehicles by usage (last
    30 days). "Load More" (growing show count) instead of the website's numbered pager, same
    convention every list in this app already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    location_f = request.args.get('location', '')
    supplier_f = request.args.get('supplier', '')
    type_f = request.args.get('type', '')
    search_f = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    d = _urea_management_data(conn, location_f, supplier_f, type_f, search_f, date_from, date_to)
    all_rows = d['all_rows']
    total_count = len(all_rows)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = all_rows[:show]
    conn.close()
    return render_template('app/urea.html', company_name=company_name, user_initials=user_initials,
        active='maintenance', notif_count=0,
        rows=page_rows, total_count=total_count, show=show, has_more=has_more,
        current_stock=d['current_stock'], total_value=d['total_value'], avg_unit_cost=d['avg_unit_cost'],
        today_usage=d['today_usage'], month_usage=d['month_usage'], avg_consumption=d['avg_consumption'],
        low_stock_alerts=d['low_stock_alerts'], locations=d['locations'],
        trend_rows=d['trend_rows'], trend_max=d['trend_max'], top_vehicles=d['top_vehicles'],
        vehicles=[v['vehicle_no'] for v in d['vehicles']], suppliers=[s['name'] for s in d['suppliers']],
        combined_names=d['combined_names'],
        f_location=location_f, f_supplier=supplier_f, f_type=type_f, f_search=search_f,
        f_date_from=date_from, f_date_to=date_to)

@app.route('/app/urea/add', methods=['POST'])
def app_add_urea():
    """All 3 Add Urea modes (Stock In/Stock Out/Direct) — posts here via fetch instead of the
    website's own modal, same _insert_urea_from_form() the website's own add_urea() uses, so the
    two can never disagree on the stock math or accept a different set of fields."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, err = _insert_urea_from_form(conn, request.form)
    if not ok:
        conn.close()
        kind, detail = err
        msg = f'Insufficient stock — only {detail} L available.' if kind == 'insufficient_stock' else detail
        return jsonify({'ok': False, 'error': msg}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_urea')})

def _resolve_toll_period(period, c_from, c_to, today):
    """Turns the mobile Toll page's period preset into a concrete (date_from, date_to, label) —
    same (key -> date range) shape as _resolve_analysis_period/_resolve_dashboard_period, just a
    shorter, Toll-specific vocabulary (This Month/Last Month/Last 3 Months/All Time/Custom) since
    a toll ledger is a shorter-horizon thing to filter than the Business Overview pages' own
    FY-scale periods."""
    if period == 'last_month':
        lm_end = today.replace(day=1) - datetime.timedelta(days=1)
        mf, mt = _month_bounds(lm_end.year, lm_end.month)
        return mf, mt, 'Last Month'
    if period == 'last_3_months':
        sm, sy = today.month - 2, today.year
        while sm <= 0:
            sm += 12; sy -= 1
        date_from, _unused = _month_bounds(sy, sm)
        _unused2, date_to = _month_bounds(today.year, today.month)
        return date_from, date_to, 'Last 3 Months'
    if period == 'all_time':
        return '', '', 'All Time'
    if period == 'custom' and c_from and c_to:
        c_from, c_to = _normalized_date_range(c_from, c_to)
        try:
            lbl = (datetime.datetime.strptime(c_from, '%Y-%m-%d').strftime('%d %b') + ' – ' +
                   datetime.datetime.strptime(c_to, '%Y-%m-%d').strftime('%d %b %Y'))
        except ValueError:
            lbl = f"{c_from} to {c_to}"
        return c_from, c_to, lbl
    # '' (no selection yet), 'this_month', or anything unrecognized -> This Month, the default.
    mf, mt = _month_bounds(today.year, today.month)
    return mf, mt, 'This Month'

def _toll_period_presets():
    """(key, label) pairs for the mobile Toll page's own Select Period sheet."""
    return [('this_month', 'This Month'), ('last_month', 'Last Month'),
            ('last_3_months', 'Last 3 Months'), ('all_time', 'All Time')]

@app.route('/app/toll')
def app_toll():
    """Mobile Toll — its own top-level page (not a Maintenance sub-tab), reached from the bottom
    nav's FAB/More sheet and the Maintenance tab strip, same pattern as Urea/Tyres/Battery — was a
    website-bridge (↗) link before this pass. Real numbers straight from _toll_tab_base_context()
    (shared with the website's own Maintenance > Toll tab): the real filtered toll-entry list,
    This Month's KPIs with a real vs-last-month comparison, and the 6-month trend. "This
    Month/Last Month/Last 3 Months/All Time" is a period preset instead of the website's raw
    from/to date fields — computed here into the same date_from/date_to _toll_tab_base_context()
    already accepts (now optional overrides — see its own docstring), so this is a genuinely
    filtered own-fleet ledger, not a fabricated stat. "Load More" (growing show count) instead of
    the website's numbered pager, same convention every list in this app already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    period = request.args.get('period', 'this_month')
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    today = datetime.date.today()
    date_from, date_to, period_label = _resolve_toll_period(period, c_from, c_to, today)
    if period == 'custom' and not (c_from and c_to):
        period = 'this_month'  # same graceful-default-to-This-Month behavior as the Dashboard's own period filter

    d = _toll_tab_base_context(conn, date_from=date_from, date_to=date_to)
    all_rows = d.pop('all_rows')
    total_count = len(all_rows)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = all_rows[:show]
    conn.close()
    # d still carries _toll_tab_base_context()'s own page/per_page pager fields (rows/total_count/
    # page/per_page/total_pages/page_tokens/base_qs) — the website's numbered-pager convention.
    # This route uses the "Load More" growing-window convention instead (rows/total_count/show/
    # has_more, set explicitly below), so those page-pager keys are dropped rather than passed
    # through, to avoid a duplicate-keyword clash with the ones set here.
    for k in ('rows', 'total_count', 'page', 'per_page', 'total_pages', 'page_tokens', 'base_qs'):
        d.pop(k, None)
    return render_template('app/toll.html', company_name=company_name, user_initials=user_initials,
        active='maintenance', notif_count=0, period=period, period_label=period_label,
        period_presets=_toll_period_presets(), rows=page_rows, total_count=total_count,
        show=show, has_more=has_more, **d)

@app.route('/app/toll/add', methods=['POST'])
def app_add_toll():
    """Manual Toll Entry — posts here via fetch instead of the website's own modal, same
    _insert_toll_from_form() the website's own add_toll() uses, so the two can never disagree on
    what makes a valid entry. Excel/FASTag bulk import stays website-only for now, same call
    already made for Urea's stock-in/out modes vs. the website's own bulk tools."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, err = _insert_toll_from_form(conn, request.form, request.files.get('receipt'), session.get('company_id', 1))
    conn.close()
    if not ok:
        return jsonify({'ok': False, 'error': err}), 400
    return jsonify({'ok': True, 'redirect': url_for('app_toll')})

@app.route('/app/compliance')
def app_compliance():
    """Mobile Compliance Expenses — its own top-level page (not a Maintenance tab-strip box, same
    "lives in More instead" reasoning as Toll — see toll.html's own comment), reached from the
    bottom nav's FAB/More sheet and every native Maintenance page's own More sheet. Real numbers
    straight from _compliance_expense_tab_context() (shared verbatim with the website's own
    Maintenance > Compliance Expenses tab) — was a website-bridge (↗) link before this pass, now a
    real native page, same migration Toll already went through. "Add Expense" is the same real
    _insert_compliance_expense_from_form() the website's own Add Compliance Expense modal posts
    to, so the two can never accept a different set of fields. "Load More" (growing show count)
    instead of the website's numbered pager, same convention every list in this app already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    d = _compliance_expense_tab_context(conn)
    all_rows = d.pop('all_rows')
    total_count = len(all_rows)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = all_rows[:show]
    conn.close()
    # d still carries _compliance_expense_tab_context()'s own page/per_page pager fields (rows/
    # total_count/page/per_page/total_pages/page_tokens/base_qs) — the website's numbered-pager
    # convention. This route uses the "Load More" growing-window convention instead (rows/
    # total_count/show/has_more, set explicitly below), so those page-pager keys are dropped
    # rather than passed through, to avoid a duplicate-keyword clash with the ones set here — same
    # pattern app_toll() already uses.
    for k in ('rows', 'total_count', 'page', 'per_page', 'total_pages', 'page_tokens', 'base_qs'):
        d.pop(k, None)
    return render_template('app/compliance.html', company_name=company_name, user_initials=user_initials,
        active='maintenance', notif_count=0, rows=page_rows, total_count=total_count,
        show=show, has_more=has_more, compliance_expense_colors=COMPLIANCE_EXPENSE_COLORS, **d)

@app.route('/app/compliance/add', methods=['POST'])
def app_add_compliance():
    """Add Compliance Expense — posts here via fetch instead of the website's own modal, same
    _insert_compliance_expense_from_form() the website's own add_compliance_expense() uses, so the
    two can never disagree on what makes a valid entry."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, err = _insert_compliance_expense_from_form(conn, request.form)
    conn.close()
    if not ok:
        return jsonify({'ok': False, 'error': err}), 400
    return jsonify({'ok': True, 'redirect': url_for('app_compliance')})

@app.route('/app/expenses')
def app_expenses():
    """Mobile Expenses — real numbers straight from _overhead_list_data() (shared with the
    website's own /overheads). KPIs (Total Expenses/This Month/Pending Bills/Recurring (Monthly)/
    Avg Monthly Expense/Categories) and the real filtered Expenses list — the Recurring/Vendor
    Summary/Category Summary tabs and the donut/trend charts are left off this pass, same call
    already made for Tyres/Battery/Service. "Load More" (growing show count) instead of the
    website's numbered pager, same convention every list in this app already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    d = _overhead_list_data(conn)
    total_count_filtered = len(d['rows'])
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count_filtered > show
    page_rows = d['rows'][:show]
    today = datetime.date.today()
    conn.close()
    return render_template('app/expenses.html', company_name=company_name, user_initials=user_initials,
        active='overheads', notif_count=0,
        rows=page_rows, total_count_filtered=total_count_filtered, show=show, has_more=has_more,
        total_amount=d['total_amount'], total_count=d['total_count'], this_month_amount=d['this_month_amount'],
        pending_amount=d['pending_amount'], pending_count=d['pending_count'],
        recurring_monthly_amount=d['recurring_monthly_amount'], recurring_count=d['recurring_count'],
        avg_monthly_expense=d['avg_monthly_expense'], active_categories=d['active_categories'],
        categories=[c['category'] for c in d['categories']], vendors=[v['vendor'] for v in d['vendors']],
        f_category=d['f_category'], f_vendor=d['f_vendor'], f_status=d['f_status'],
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_search=d['f_search'],
        today_str=today.isoformat(), month_labels_full=today.strftime('%b %Y'))

@app.route('/app/expenses/add', methods=['POST'])
def app_add_overhead():
    """In-app "Add Expense" modal (expenses.html) posts here via fetch instead of the website's
    own #addModal, same _insert_overhead_from_form() the website itself uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_overhead_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_expenses')})

@app.route('/app/expenses/<int:o_id>/edit', methods=['POST'])
def app_edit_overhead(o_id):
    """In-app "Edit Expense" modal (expenses.html, opened from the read-only view popup's "Update"
    button) posts here via fetch — same _update_overhead_from_form() the website's own edit form
    uses, so the two can never accept a different set of fields on edit."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, error = _update_overhead_from_form(conn, o_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': error}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_expenses')})

@app.route('/app/salary')
def app_salary():
    """Mobile Salary — real numbers straight from _employees_salary_data() (shared with the
    website's own /salaries?tab=salary). KPIs (Total Payroll/Employees Paid/Avg Salary/
    Outstanding Advance) and the real filtered employee list — tapping a row opens that
    employee's own native Ledger (see app_employee_ledger()), same as the website's own "click
    any row" hint. Only 2 tabs shown (Salary/Attendance) — Overview is dropped per instruction,
    and Attendance is a real page on the website but shows a "Coming Soon" placeholder here
    instead of being rebuilt natively this pass. "Load More" (growing show count) instead of the
    website's numbered pager, same convention every list in this app already uses."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    d = _employees_salary_data(conn)
    total_count = d['total_count']
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_rows = d['rows'][:show]
    conn.close()
    return render_template('app/salary.html', company_name=company_name, user_initials=user_initials,
        active='salaries', notif_count=0, tab='salary',
        rows=page_rows, total_count=total_count, show=show, has_more=has_more,
        total_payroll=d['total_payroll'], employees_paid=d['employees_paid'],
        total_employees_all=d['total_employees_all'], avg_salary=d['avg_salary'],
        total_outstanding_advances=d['total_outstanding_advances'], roles=d['roles'],
        f_date_from=d['f_date_from'], f_date_to=d['f_date_to'], f_role=d['f_role'], f_search=d['f_search'])

@app.route('/app/salary/attendance')
def app_salary_attendance():
    """"Coming Soon" placeholder — Attendance is a real, working tab on the website
    (_employees_attendance_tab()) but isn't rebuilt natively this pass, per instruction."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    conn.close()
    return render_template('app/salary.html', company_name=company_name, user_initials=user_initials,
        active='salaries', notif_count=0, tab='attendance')

@app.route('/app/employee/<employee>')
def app_employee_ledger(employee):
    """Mobile Employee Ledger — real entries straight from _employee_ledger_entries() (shared
    verbatim with the website's own /employee/<name>). Search is new (the website itself has no
    search box here) — added as a plain substring match over entry type/notes, same "layer a
    search convenience on top of otherwise-real filters" call already made for Ledger/Expenses.
    "Load More" (growing show count) instead of showing every entry at once."""
    conn = get_db()
    if not _salary_name_allowed(conn, employee):
        conn.close()
        return _salary_denied()
    company_name, user_initials = _app_user_display(conn)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    date_from, date_to = _normalized_date_range(date_from, date_to)
    search_f = request.args.get('search', '')
    led = _employee_ledger_entries(conn, employee, date_from, date_to)
    entries = led['entries']
    if search_f:
        s = search_f.lower()
        entries = [e for e in entries if s in (e['entry_type'] or '').lower() or s in (e['notes'] or '').lower()]
    total_count = len(entries)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show
    page_entries = entries[:show]
    conn.close()
    return render_template('app/employee_ledger.html', company_name=company_name, user_initials=user_initials,
        active='salaries', notif_count=0, employee=employee,
        entries=page_entries, total_count=total_count, show=show, has_more=has_more,
        advance_balance=led['advance_balance'], brought_forward=led['brought_forward'], total_salary_paid=led['total_salary_paid'],
        opening_balance=led['opening_balance'], opening_balance_date=led['opening_balance_date'],
        f_date_from=date_from, f_date_to=date_to, f_search=search_f)

@app.route('/app/employee/<employee>/add', methods=['POST'])
def app_add_employee_payment(employee):
    """"Payment" mode of the in-app Add Payment modal (employee_ledger.html) — posts here via
    fetch instead of the website's own #addModal, same _insert_employee_payment_from_form() the
    website itself uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_employee_payment_from_form(conn, employee, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_employee_ledger', employee=employee)})

@app.route('/app/employee/<employee>/opening-balance', methods=['POST'])
def app_update_employee_opening_balance(employee):
    """"Opening Balance" mode of the in-app Add Payment modal — same
    _update_employee_opening_balance_from_form() the website's own #addModal (Opening Balance
    tab) uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _update_employee_opening_balance_from_form(conn, employee, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_employee_ledger', employee=employee)})

@app.route('/app/salary/add', methods=['POST'])
def app_add_employee():
    """Native "Add Employee" on the Salary page — same _insert_employee_from_form() the
    website's own /employees Add Employee modal (add_employee(), templates/partials/
    _employees_modals.html) uses, so a new employee can never quietly differ between the two
    surfaces. JSON endpoint (fetch from salary.html), matching every other native Add route."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_employee_from_form(conn, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_salary')})

@app.route('/app/trips')
def app_trips():
    """Mobile Trips list. Real fields only, matching the website's own /trips exactly — no
    fabricated "Trip ID" code (trips have no such column, just an internal numeric id; LR Number
    is the real human-facing identifier used everywhere else in this app, so that's what's shown
    here too) and no "Cancelled" tab/status (trips only have a real 2-state model — end_date IS
    NULL means ongoing, NOT NULL means completed; there is no cancellation concept anywhere in
    the schema, so a 3rd tab for it would be inventing data, not reflecting it).
    """
    conn = get_db()
    status_f = request.args.get('status', '')  # '', 'active', 'completed' — same convention trips_list() already uses
    search_f = (request.args.get('search') or '').strip()
    # Real filters beyond status/search — same fields trips_list() itself supports, added behind
    # the filter icon (see .a-filter-btn) rather than as extra inline dropdowns.
    type_f = request.args.get('type', '')
    lr_received_f = request.args.get('lr_received', '')
    date_from_f = request.args.get('date_from', '')
    date_to_f = request.args.get('date_to', '')
    date_from_f, date_to_f = _normalized_date_range(date_from_f, date_to_f)
    # Date sort — same 'sort'/'dir' whitelist pattern trips_list() (the website's own /trips) uses
    # (see its own sort_columns dict), just narrowed to the one column this page actually exposes
    # a control for (date). 'desc' (Newest First) matches this page's existing default order, so
    # a link/bookmark from before this existed behaves exactly as it always did.
    sort_dir_f = request.args.get('sort_dir', 'desc')
    if sort_dir_f not in ('asc', 'desc'):
        sort_dir_f = 'desc'

    where = " WHERE 1=1"
    params = []
    if status_f == 'active':
        where += " AND t.end_date IS NULL"
    elif status_f == 'completed':
        where += " AND t.end_date IS NOT NULL"
    if type_f:
        where += " AND t.type = ?"
        params.append(type_f)
    if lr_received_f == 'received':
        where += " AND t.lr_received = 'Yes'"
    elif lr_received_f == 'not_received':
        where += " AND (t.lr_received IS NULL OR t.lr_received != 'Yes')"
    if date_from_f:
        where += " AND t.date >= ?"
        params.append(date_from_f)
    if date_to_f:
        where += " AND t.date <= ?"
        params.append(date_to_f)
    if search_f:
        where += """ AND (t.lr_number LIKE ? OR v.vehicle_no LIKE ? OR p.name LIKE ? OR t.driver_name LIKE ?)"""
        like = f"%{search_f}%"
        params.extend([like, like, like, like])

    total_count = conn.execute(
        f"SELECT COUNT(*) FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id{where}",
        params).fetchone()[0]

    # "Load More" (growing window) rather than the website's numbered Prev/1/2/3/Next — see the
    # same reasoning on app_vehicles() above; this is the mobile-list convention.
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    has_more = total_count > show

    order_dir = 'ASC' if sort_dir_f == 'asc' else 'DESC'
    query = f"""SELECT t.id, t.date, t.lr_number, t.end_date, t.billed_amount, t.driver_name,
                      t.from_loc, t.to_loc, v.vehicle_no, p.name as party_name
               FROM trips t LEFT JOIN vehicles v ON t.vehicle_id=v.id LEFT JOIN parties p ON t.party_id=p.id
               {where} ORDER BY t.date {order_dir}, t.id {order_dir} LIMIT ?"""
    rows = conn.execute(query, params + [show]).fetchall()
    trips = [{'id': r['id'], 'date': r['date'], 'lr_number': r['lr_number'] or f"Trip #{r['id']}",
              'ongoing': r['end_date'] is None, 'billed_amount': 0 if _hide_trip_amounts() else (r['billed_amount'] or 0),
              'vehicle_no': r['vehicle_no'] or '—', 'route': ' → '.join(x for x in [r['from_loc'], r['to_loc']] if x) or '—'}
             for r in rows]

    month_from, month_to = _month_bounds(datetime.date.today().year, datetime.date.today().month)
    month_trips = conn.execute("SELECT billed_amount, end_date FROM trips WHERE date>=? AND date<=?", (month_from, month_to)).fetchall()
    total_trips_month = len(month_trips)
    completed_month = sum(1 for t in month_trips if t['end_date'] is not None)
    total_freight_month = 0 if _hide_trip_amounts() else sum(t['billed_amount'] or 0 for t in month_trips)
    ongoing_count = conn.execute("SELECT COUNT(*) FROM trips WHERE end_date IS NULL").fetchone()[0]

    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'
    conn.close()

    def _fmt_freight(n):
        if n >= 100000:
            return f"₹{n/100000:.2f}L"
        return f"₹{n:,.0f}"

    return render_template('app/trips.html', user_initials=user_initials,
        active='trips', notif_count=ongoing_count,
        trips=trips, f_status=status_f, f_search=search_f, f_type=type_f, f_lr_received=lr_received_f,
        f_date_from=date_from_f, f_date_to=date_to_f, f_sort_dir=sort_dir_f, show=show, has_more=has_more, total_count=total_count,
        total_trips_month=total_trips_month, ongoing_count=ongoing_count,
        completed_month=completed_month, total_freight_month=(HIDDEN_AMOUNT_MARK if _hide_trip_amounts() else _fmt_freight(total_freight_month)))

@app.route('/app/trips/<int:trip_id>')
def app_trip_detail(trip_id):
    """Mobile Trip detail — same real data as trip_view() (see _trip_detail_data() above: trip
    fields, vehicle/party/vendor joins, custom invoice items, owner amount, additions/deductions
    totals, balance due). Real tabs grouping the same fields the website's own trip_form.html
    edit wizard uses (Trip Info / Costs & Payments / Deductions / Completion) — nothing
    fabricated, just a mobile-friendly regrouping of real columns. Only one real action button:
    "End Trip" (native modal, posts to the same real /trips/<id>/end endpoint the website's own
    per-row modal uses) — shown only while the trip is still open (end_date IS NULL), same
    condition the website enforces; "Update Trip" bridges (↗) to the real full edit wizard
    (edit_trip()/trip_form.html) rather than being rebuilt here — that form has ~30 fields across
    cargo/cost/deduction/owner sections, out of scope for a native rebuild in this pass."""
    conn = get_db()
    data = _trip_detail_data(conn, trip_id)
    if data is None:
        conn.close()
        return redirect(url_for('app_trips'))
    company_name, user_initials = _app_user_display(conn)
    conn.close()

    tab = request.args.get('tab', 'info')
    if tab not in ('info', 'costs', 'deductions', 'completion'):
        tab = 'info'
    return render_template('app/trip_detail.html', company_name=company_name, user_initials=user_initials,
        active='trips', notif_count=0, tab=tab, **data)

@app.route('/app/trips/<int:trip_id>/end', methods=['POST'])
def app_end_trip(trip_id):
    """In-app "End Trip" modal (trip_detail.html) posts here via fetch instead of the website's
    inline per-row modal — same _end_trip_from_form() the website itself uses, just returning
    JSON so the modal can close in place and reload the real (now-completed) trip."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, error = _end_trip_from_form(conn, trip_id, request.form)
    conn.close()
    if not ok:
        return jsonify({'ok': False, 'error': error}), 400
    return jsonify({'ok': True, 'redirect': url_for('app_trip_detail', trip_id=trip_id, tab='completion')})

@app.route('/app/vehicles')
def app_vehicles():
    """Mobile Vehicles — same real fields/thresholds as the website's own vehicles_list():
    _expiry_bucket() for Insurance/Fitness/PUC/Permit (expired / expiring within 30 days / ok),
    _vehicle_age_years() for Age, vehicles.challan_count/challan_amount for the challan alert.
    Trimmed for a small screen per this app's design direction: no Permit Expiring tile (Insurance
    is the one that matters most day-to-day), no Model/Type filter dropdowns (there's no "model"
    column on vehicles at all — that part of the reference mockup doesn't correspond to a real
    field) — just a status tab strip (All/Active/In Maintenance, the same three real status
    values vehicles_list() already counts) and a vehicle-number search, both real. Road Tax is
    intentionally left off each card — it only ever has data after an RC sync has run for that
    vehicle, and showing "No Data" on every card by default would be more noise than signal on a
    small screen; still on the website's own detail page.
    Sorted by Age (oldest first — vehicles.registration_date via _vehicle_age_years(), same
    unknown-ages-sort-last rule vehicles_list()'s own sort=age already uses) by default. Uses
    "Load More" (a growing `show` count, re-rendering the top N of the same sorted/filtered list
    on each tap) instead of the website's numbered Prev/1/2/3/Next pagination — that's a desktop
    convention; every mobile list worth copying (Instagram, X, etc.) either infinite-scrolls or
    uses Load More, never numbered pages.
    """
    conn = get_db()
    status_f = request.args.get('status', '')
    search_f = (request.args.get('search') or '').strip()
    # Type and Age — real filters on the website's own vehicles_list() (type_f/age_f), added
    # behind the filter icon (see .a-filter-btn) rather than as extra inline dropdowns.
    type_f = request.args.get('type', '')
    age_f = request.args.get('age', '')

    query = """SELECT id, vehicle_no, type, registration_date, status, body_type,
                      insurance_expiry, fitness_expiry, puc_valid_upto, permit_valid_upto,
                      challan_count, challan_amount
               FROM vehicles WHERE type IS NOT NULL"""
    params = []
    if status_f:
        query += " AND COALESCE(status,'Active') = ?"
        params.append(status_f)
    if type_f:
        query += " AND type = ?"
        params.append(type_f)
    if search_f:
        query += " AND vehicle_no LIKE ?"
        params.append(f"%{search_f}%")
    raw_rows = conn.execute(query, params).fetchall()

    def _exp_field(date_str):
        bucket = _expiry_bucket(date_str)
        days_left = None
        if date_str:
            try:
                days_left = (datetime.datetime.strptime(date_str, '%Y-%m-%d').date() - datetime.date.today()).days
            except ValueError:
                pass
        return {'date': date_str, 'bucket': bucket or 'unknown', 'days_left': days_left}

    # A Cancelled policy needs its own card treatment — the date-only bucket above has no concept
    # of "Cancelled" at all (a cancelled policy can still carry a real, unexpired date on paper),
    # so this reads it straight from refresh_compliance()'s own status_override check (see that
    # function's own comment for the bug this closes: Insurance status showing Valid/Expiring on
    # every card even after being marked Cancelled on the Insurance tab). Maps onto the same
    # red/"needs attention" styling .expired already has — a cancelled policy needs a new one just
    # as urgently as an expired one does.
    def _insurance_field(comp, raw_date):
        if comp and comp['insurance']['status'] == 'Cancelled':
            return {'date': 'Cancelled', 'bucket': 'expired', 'days_left': None}
        return _exp_field(raw_date)

    # Fitness/PUC/Permit have a real synced source of truth — vehicle_compliance, populated by
    # eChallan syncs — that this card's raw vehicles.fitness_expiry/puc_valid_upto/permit_valid_upto
    # columns often never catch up to (those only ever get set if someone manually types a date into
    # the old Add/Edit Vehicle fields). cs.refresh_compliance() is the exact same "synced date,
    # falling back to the manual column" merge the vehicle detail page already uses (see
    # _vehicle_detail_data()'s own comp_row) — reusing it here means this card can never show a
    # blank "—" for a compliance type that's actually on file, just because it was synced instead of
    # typed in by hand. Own-fleet only (refresh_compliance()'s own scope — hired vehicles have no
    # compliance sync at all, so they keep reading the plain columns below, same as before).
    comp_by_vehicle_id = {c['vehicle_id']: c for c in cs.refresh_compliance(conn)}

    all_vehicles = []
    for r in raw_rows:
        comp = comp_by_vehicle_id.get(r['id'])
        all_vehicles.append({
            'id': r['id'], 'vehicle_no': r['vehicle_no'],
            'body_type_short': _short_body_type(r['body_type']) or (r['type'] or '').title(),
            'status': r['status'] or 'Active', 'age_years': _vehicle_age_years(r['registration_date']),
            'insurance': _insurance_field(comp, r['insurance_expiry']),
            'fitness': _exp_field(comp['fitness']['expiry'] if comp else r['fitness_expiry']),
            'puc': _exp_field(comp['puc']['expiry'] if comp else r['puc_valid_upto']),
            'permit': _exp_field(comp['permit']['expiry'] if comp else r['permit_valid_upto']),
            'challan_count': r['challan_count'] or 0, 'challan_amount': r['challan_amount'] or 0,
        })
    # Age-bucket filter — same post-query logic as vehicles_list() (age is computed, never a real
    # SQL column, so it can't be filtered in the WHERE above).
    if age_f == 'under3':
        all_vehicles = [v for v in all_vehicles if v['age_years'] is not None and v['age_years'] < 3]
    elif age_f == '3to6':
        all_vehicles = [v for v in all_vehicles if v['age_years'] is not None and 3 <= v['age_years'] < 6]
    elif age_f == 'over6':
        all_vehicles = [v for v in all_vehicles if v['age_years'] is not None and v['age_years'] >= 6]
    # Oldest first; unknown age (no registration_date on file) always sorts last regardless —
    # "unknown" isn't meaningfully old or young, same rule vehicles_list() uses for sort=age.
    known = [v for v in all_vehicles if v['age_years'] is not None]
    unknown = [v for v in all_vehicles if v['age_years'] is None]
    known.sort(key=lambda v: v['age_years'], reverse=True)
    all_vehicles = known + unknown

    # "Load More" (growing window), not desktop-style numbered pages — the standard mobile-list
    # pattern (Instagram, X, etc. all do this, or true infinite scroll; numbered Prev/1/2/3/Next
    # is a desktop convention that doesn't translate well to a small screen or a thumb). Each tap
    # just re-requests this same URL with a bigger `show` count and a full-page reload re-renders
    # the top N of the same sorted/filtered list — no JS, no offset math, no separate "page" state
    # to keep in sync with the filters above.
    total_count = len(all_vehicles)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    vehicles = all_vehicles[:show]
    has_more = total_count > show

    # Same real counts as vehicles_list() itself (unfiltered by the tab/search above, so the
    # stat row always reflects the whole fleet, not just what's currently shown in the list).
    stat_rows = conn.execute("SELECT status, insurance_expiry, challan_count, challan_amount FROM vehicles WHERE type IS NOT NULL").fetchall()
    total_vehicles = len(stat_rows)
    active_count = sum(1 for r in stat_rows if (r['status'] or 'Active') == 'Active')
    maint_count = sum(1 for r in stat_rows if r['status'] == 'In Maintenance')
    ins_expiring = sum(1 for r in stat_rows if _expiry_bucket(r['insurance_expiry']) in ('expiring', 'expired'))
    challan_rows = [r for r in stat_rows if (r['challan_count'] or 0) > 0]
    challan_vehicle_count = len(challan_rows)
    challan_total_amount = sum(r['challan_amount'] or 0 for r in challan_rows)

    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'
    conn.close()

    return render_template('app/vehicles.html', user_initials=user_initials,
        active='vehicles', notif_count=ins_expiring + challan_vehicle_count,
        vehicles=vehicles, f_status=status_f, f_search=search_f, f_type=type_f, f_age=age_f,
        show=show, has_more=has_more, total_count=total_count,
        total_vehicles=total_vehicles, active_count=active_count,
        active_pct=round(active_count / total_vehicles * 100, 1) if total_vehicles else 0,
        maint_count=maint_count, ins_expiring=ins_expiring,
        challan_vehicle_count=challan_vehicle_count, challan_total_amount=challan_total_amount)

@app.route('/app/vehicles/<int:v_id>')
def app_vehicle_detail(v_id):
    """Mobile Vehicle detail — same real data as the website's own vehicle_detail() (see
    _vehicle_detail_data() above): header info + the same 5 compliance summary cards, plus two
    native tabs (Challans, History) since both are pure read-only displays. The 4 compliance
    cards (Insurance/Permit/Fitness/PUC) each bridge (↗) to the real website's own edit-capable
    tab instead — each one is a genuinely large multi-section add/edit form (policy type,
    premium, agent details, document uploads...) that doesn't belong rebuilt natively on a phone
    screen for this pass. No fabricated "Overview"/"Documents"/"Reminders" tabs — the real page
    only ever had Insurance/Permit/Fitness/PUC/eChallan/History (see vehicle_detail.html's own
    tab_titles)."""
    conn = get_db()
    data = _vehicle_detail_data(conn, v_id)
    if data is None:
        conn.close()
        return redirect(url_for('app_vehicles'))
    # _vehicle_detail_data() returns its own 'company_name' (used for the Owner field) — drop it
    # in favor of the canonical get_company_name()-backed one from _app_user_display(), same as
    # every other /app/ page's topbar; in practice they're the same value today.
    data.pop('company_name', None)
    company_name, user_initials = _app_user_display(conn)
    conn.close()

    tab = request.args.get('tab', 'echallan')
    if tab not in ('echallan', 'history'):
        tab = 'echallan'
    return render_template('app/vehicle_detail.html', company_name=company_name, user_initials=user_initials,
        active='vehicles', notif_count=0, tab=tab, **data)

@app.route('/app/vehicles/add', methods=['POST'])
def app_add_vehicle():
    """In-app "Add Vehicle" modal (vehicles.html) posts here via fetch instead of navigating to
    the real /vehicles/add page — same _insert_vehicle_from_form() the website itself uses (same
    fields, same insert-or-update-by-vehicle_no behavior), just returning JSON instead of a
    redirect so the modal can close in place and reload the real list without a full navigation.
    No file-upload handling here (or in the website's own add_vehicle()) — that's a real gap
    matching the real backend, not something invented for the modal."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, error = _insert_vehicle_from_form(conn, request.form)
    conn.close()
    if not ok:
        return jsonify({'ok': False, 'error': error}), 400
    return jsonify({'ok': True, 'redirect': url_for('app_vehicles')})

@app.route('/app/analysis')
def app_analysis():
    """Mobile Analysis hub — now just an alias straight to Business Performance
    (app_business_performance()), the first of the 4 real tabs in the "Business Overview" strip
    (_overview_tabstrip.html): Business Performance, Fleet Utilization, Performance, Route
    Analytics — order per instruction (was originally Route Analytics first; this alias has to
    match whatever tab is actually first, or opening "Business Overview" would land on the wrong
    tab). All 4 are native (see app_business_performance()/app_fleet_utilization()/
    app_performance()/app_analytics()), each showing the same real top tab strip so switching
    between them never leaves this section."""
    return redirect(url_for('app_business_performance'))

@app.route('/app/analytics')
def app_analytics():
    """Mobile Route Analytics. The website's own /route-analytics (templates/route_analytics.html)
    has exactly 2 real tabs — Best Routes and Route Rates — not 4; there is no separate "Route
    Performance" or "Cost Analysis" tab or route anywhere in this app (checked: no matching
    @app.route exists), so this deliberately does NOT invent two extra tabs to match a 4-tab
    reference design. Both tabs are built natively here, on the exact same
    _route_analytics_data()/_route_rates_data() the website's own route_analytics() uses, so the
    two can never disagree on a route's margin or rate. One shared filter (Date From/To, From,
    To, Vehicle Type — same 5 real fields the website's own filter_bar has) drives both tabs,
    behind a single filter icon (matching every other native list page's search/filter pattern)
    instead of the website's own always-visible inline bar. Route Rates' Rate Trend chart and
    Own-vs-Hired donut are left for the website's bridge (real future scope for a native
    redesign) — everything the mockup itself asked for (KPI cards, Driver Advance/Fuel Taken
    ranges, the Route Rates table) is fully native."""
    conn = get_db()
    tab = request.args.get('tab', 'best')
    if tab not in ('best', 'rates', 'finder'):
        tab = 'best'
    today = datetime.date.today()
    period = request.args.get('period', '')
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    date_from, date_to, period_label = _resolve_analysis_period(period, c_from, c_to, today)
    from_f = request.args.get('from', '')
    to_f = request.args.get('to', '')
    type_f = request.args.get('vehicle_type', '')

    d = _route_analytics_data(conn, date_from, date_to, from_f, to_f, type_f)
    route_rows = d['route_rows']
    most_trips_route = max(route_rows, key=lambda r: r['trips']) if route_rows else None
    avg_revenue_per_trip = round(d['total_revenue_sel'] / d['total_trips_sel']) if d['total_trips_sel'] else 0

    rr = None
    rr_show = 0
    if tab == 'rates':
        rr = _route_rates_data(d['trips'], from_f, to_f)
        rr_show = min(int(request.args.get('show', 10) or 10), rr['rr_total_count']) or rr['rr_total_count']

    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'
    rf_ctx = _route_finder_ctx(conn)
    conn.close()

    from urllib.parse import urlencode
    base_qs = urlencode({'period': period, 'date_from': date_from, 'date_to': date_to,
                          'from': from_f, 'to': to_f, 'vehicle_type': type_f})

    ctx = dict(user_initials=user_initials,
        active='analytics', notif_count=0, tab=tab, active_ov='route', base_qs=base_qs,
        period=period, period_label=period_label, period_presets=_analysis_period_presets(today),
        f_date_from=date_from, f_date_to=date_to, f_from=from_f, f_to=to_f, f_vehicle_type=type_f,
        all_from_list=d['all_from_list'], all_to_list=d['all_to_list'],
        total_routes=d['total_routes'], total_trips_sel=d['total_trips_sel'], total_revenue_sel=d['total_revenue_sel'],
        avg_margin=d['avg_margin'], top5_by_margin=d['top5_by_margin'], top5_by_revenue=d['top5_by_revenue'],
        most_trips_route=most_trips_route, avg_revenue_per_trip=avg_revenue_per_trip, **rf_ctx)
    if rr is not None:
        rows = rr['rr_rows']
        ctx.update(rr_rows=rows[:rr_show], rr_total_count=rr['rr_total_count'], rr_show=rr_show,
            rr_has_more=rr_show < rr['rr_total_count'],
            highest_entry=rr['highest_entry'], lowest_entry=rr['lowest_entry'],
            rr_avg_rate_overall=rr['rr_avg_rate_overall'], rr_total_routes=rr['rr_total_routes'],
            da_lowest=rr['da_lowest'], da_average=rr['da_average'], da_highest=rr['da_highest'],
            fuel_lowest=rr['fuel_lowest'], fuel_average=rr['fuel_average'], fuel_highest=rr['fuel_highest'])
    return render_template('app/analytics.html', **ctx)

@app.route('/app/fleet-utilization')
def app_fleet_utilization():
    """Mobile Fleet Utilization — real numbers straight from _fleet_utilization_data() (shared
    with the website's own /fleet-utilization). 3 real tabs (Overview/Idle Analysis/Empty Run
    Analysis, same as the website's own). Date range now driven by the same compact period
    control (defaulting to current FY) shared with the other 3 Business Overview analysis pages,
    replacing the old fixed '2026-04-01'/'2027-03-31' literal. Vehicle filter trimmed off this
    pass (Route Analytics' own Vehicle Type toggle stays, this page's per-vehicle filter is real
    future scope)."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    today = datetime.date.today()
    period = request.args.get('period', '')
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    date_from, date_to, period_label = _resolve_analysis_period(period, c_from, c_to, today)
    d = _fleet_utilization_data(conn, date_from, date_to)
    conn.close()
    tab = request.args.get('tab', 'overview')
    if tab not in ('overview', 'idle', 'empty'):
        tab = 'overview'
    return render_template('app/fleet_utilization.html', company_name=company_name, user_initials=user_initials,
        active='analytics', notif_count=0, tab=tab, d=d, active_ov='fleet',
        period=period, period_label=period_label, period_presets=_analysis_period_presets(today),
        f_date_from=date_from, f_date_to=date_to)

@app.route('/app/performance')
def app_performance():
    """Mobile Performance — real numbers straight from _performance_data() (shared with the
    website's own /performance). 2 real tabs (Driver Performance/Vehicle Performance, same as the
    website's own). Top 5 tables show real columns only (Trips/Total Billed/Profit) — the
    reference design's "Avg Rating"/"On Time %"/"Safety Score" have no backing column anywhere in
    this data model (checked: no rating/attendance-timeliness/safety field exists on trips,
    drivers, or vehicles), so they're deliberately not invented here. Date range driven by the
    same compact period control (defaulting to current FY) shared with the other 3 Business
    Overview analysis pages, replacing the old fixed '2026-04-01'/'2027-03-31' literal."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    today = datetime.date.today()
    period = request.args.get('period', '')
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    date_from, date_to, period_label = _resolve_analysis_period(period, c_from, c_to, today)
    d = _performance_data(conn, date_from, date_to)
    conn.close()
    tab = request.args.get('tab', 'driver')
    if tab not in ('driver', 'vehicle'):
        tab = 'driver'
    return render_template('app/performance.html', company_name=company_name, user_initials=user_initials,
        active='analytics', notif_count=0, tab=tab, d=d, active_ov='performance',
        period=period, period_label=period_label, period_presets=_analysis_period_presets(today),
        f_date_from=date_from, f_date_to=date_to)

@app.route('/app/business-performance')
def app_business_performance():
    """Mobile Business Performance — a leaner native rollup built from the exact same
    _period_financials()/_accounts_rows()/_pct_growth() primitives the website's own (much
    larger) business_performance() route uses for its own headline numbers, so the two can never
    disagree on Revenue/Expenses/Net Profit/Receivables — deliberately not extracted from that
    350+ line function itself, which also computes a deeper AI-insights/health-score/suggestions
    engine that's real future scope for mobile, not rebuilt this pass (same "trim to what's
    really needed" call already made for Tyres/Battery/Service's own charts). Cash in Hand here
    is cash_collected minus cash_paid for the period — a real derived figure from the same real
    payments the website's own page already sums, just not previously named as one metric there.
    Date range driven by the same compact period control (defaulting to current FY) shared with
    the other 3 Business Overview analysis pages — replacing the old fixed "always Last Month"
    default, which didn't match what any of the other 3 pages defaulted to."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    today = datetime.date.today()
    period = request.args.get('period', '')
    c_from = request.args.get('date_from', '')
    c_to = request.args.get('date_to', '')
    date_from, date_to, period_label = _resolve_analysis_period(period, c_from, c_to, today)
    curr = _period_financials(conn, date_from, date_to)
    prev_from, prev_to = _shift_period_back(date_from, date_to)
    prev = _period_financials(conn, prev_from, prev_to)
    revenue_growth = _pct_growth(curr['revenue'], prev['revenue'])
    expense_growth = _pct_growth(curr['total_expenses'], prev['total_expenses'])
    profit_growth = _pct_growth(curr['net_profit'], prev['net_profit'])

    acct_rows = _accounts_rows(conn)
    total_receivables = sum(r['balance'] for r in acct_rows if r['balance'] > 0)

    cash_collected = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='received' AND date>=? AND date<=?",
                                   (date_from, date_to)).fetchone()[0]
    cash_collected += sum(t['party_advance'] or 0 for t in curr['trips'])
    cash_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_type='paid' AND date>=? AND date<=?",
                              (date_from, date_to)).fetchone()[0]
    cash_paid += curr['maint'] + curr['overheads'] + curr['salaries']
    cash_in_hand = cash_collected - cash_paid

    # Revenue vs Expenses — trailing 6 months, same trailing-month convention as the website's
    # own Monthly Profit Trend.
    end_d = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
    monthly_rows = []
    yy_, mm_ = end_d.year, end_d.month
    for i in range(5, -1, -1):
        mm = mm_ - i
        yy = yy_
        while mm <= 0:
            mm += 12
            yy -= 1
        mf, mt = _month_bounds(yy, mm)
        mfin = _period_financials(conn, mf, mt)
        monthly_rows.append({'label': calendar.month_abbr[mm], 'revenue': mfin['revenue'], 'expenses': mfin['total_expenses']})
    trend_max = max([max(m['revenue'], m['expenses']) for m in monthly_rows], default=1) or 1
    conn.close()

    return render_template('app/business_performance.html', company_name=company_name, user_initials=user_initials,
        active='analytics', notif_count=0,
        total_revenue=curr['revenue'], total_expenses=curr['total_expenses'], net_profit=curr['net_profit'],
        margin=curr['margin'], trip_count=curr['trip_count'], total_receivables=total_receivables,
        cash_in_hand=cash_in_hand, revenue_growth=revenue_growth, expense_growth=expense_growth,
        profit_growth=profit_growth, monthly_rows=monthly_rows, trend_max=trend_max,
        period=period, period_label=period_label, period_presets=_analysis_period_presets(today),
        f_date_from=date_from, f_date_to=date_to, active_ov='business')

@app.route('/app/ledger')
def app_ledger():
    """Mobile Ledger — same real source of truth as the website's own /accounts (which itself
    powers the Dashboard's and Business Performance's receivable/payable figures too):
    _accounts_rows(), used completely unchanged, not re-derived. Every stat card and every row
    here is the exact same computation the website's accounts() route does — same balance sign
    convention (positive = receivable, negative = payable), same Active/Inactive status, same
    GSTIN field.
    Search here is deliberately narrower than the website's own (name + contact + email): name
    only, matching real party/vendor names, not phone/email — a mobile-specific simplification,
    not a website behavior being reproduced.
    Type (All/Parties/Vendors) is still the tab strip. Group and Status are real filters too
    (accounts()'s own group_f/status_f) — added behind the filter icon (.a-filter-btn) rather
    than extra inline dropdowns, same pattern as Vehicles/Trips/Maintenance-Service. No numbered
    pagination, "Load More" instead (see app_vehicles()/app_trips() for the same pattern); no
    "Sort by" control since _accounts_rows() already returns everything sorted by name and the
    website itself has no other real sort option to mirror. No download/export button for now
    (removed per instruction) — the real /accounts/export still exists and could be wired back
    in later if wanted.
    """
    conn = get_db()
    search_f = (request.args.get('search') or '').strip()
    role_f = request.args.get('role', '')  # '', 'Party', 'Vendor' — same convention accounts() uses
    group_f = request.args.get('group', '')
    status_f = request.args.get('status', '')

    all_rows = _accounts_rows(conn)
    total_parties = sum(1 for r in all_rows if r['role'] == 'Party')
    active_parties = sum(1 for r in all_rows if r['role'] == 'Party' and r['status'] == 'Active')
    total_vendors = sum(1 for r in all_rows if r['role'] == 'Vendor')
    active_vendors = sum(1 for r in all_rows if r['role'] == 'Vendor' and r['status'] == 'Active')
    total_receivable = sum(r['balance'] for r in all_rows if r['balance'] > 0)
    receivable_from = sum(1 for r in all_rows if r['balance'] > 0)
    total_payable = sum(-r['balance'] for r in all_rows if r['balance'] < 0)
    payable_to = sum(1 for r in all_rows if r['balance'] < 0)
    net_balance = sum(r['balance'] for r in all_rows)
    groups = sorted(set(r['group'] for r in all_rows if r['group']))

    # Name-only search here, not the website's name+contact+email — narrower on purpose per this
    # app's design direction, still matching real party/vendor names, just not phone/email too.
    filtered_rows = all_rows
    if search_f:
        s = search_f.lower()
        filtered_rows = [r for r in filtered_rows if s in r['name'].lower()]
    if role_f:
        filtered_rows = [r for r in filtered_rows if r['role'] == role_f]
    if group_f:
        filtered_rows = [r for r in filtered_rows if r['group'] == group_f]
    if status_f:
        filtered_rows = [r for r in filtered_rows if r['status'].lower() == status_f.lower()]
    total_count = len(filtered_rows)
    try:
        show = int(request.args.get('show', 15))
    except (TypeError, ValueError):
        show = 15
    show = max(15, show)
    rows = filtered_rows[:show]
    has_more = total_count > show
    # Avatar initials from the first two *letters* actually found in the name — plain
    # word-splitting picks up stray leading punctuation (e.g. "ACT (ALLIANCE..." -> "A("),
    # this skips straight past anything that isn't a letter.
    for r in rows:
        letters = [c for c in r['name'].upper() if c.isalpha()]
        r['initials'] = ''.join(letters[:2]) or '?'

    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'
    conn.close()

    return render_template('app/ledger.html', user_initials=user_initials,
        active='ledger', notif_count=0, f_search=search_f, f_role=role_f, f_group=group_f, f_status=status_f,
        groups=groups, show=show, has_more=has_more,
        total_count=total_count, rows=rows,
        total_parties=total_parties, active_parties=active_parties,
        total_vendors=total_vendors, active_vendors=active_vendors,
        total_receivable=total_receivable, receivable_from=receivable_from,
        total_payable=total_payable, payable_to=payable_to, net_balance=net_balance)

@app.route('/app/ledger/add', methods=['POST'])
def app_add_account():
    """Mobile Add Party/Vendor — shares _create_account_from_form() with the website's own
    /accounts/add (add_account()) so this is the exact same field set and insert logic, not a
    second hand-written copy; only the redirect target differs (back to the mobile Ledger list,
    preserving whatever role/search/group/status filter was active)."""
    conn = get_db()
    _create_account_from_form(conn, request.form)
    conn.close()
    return redirect(url_for('app_ledger', role=request.form.get('return_role', ''),
                             search=request.form.get('return_search', '')))

def _app_user_display(conn):
    """company_name + avatar initials for the logged-in user — the same few lines every /app/
    route has been repeating individually; factored out here rather than copy-pasted an 8th time."""
    company_name = get_company_name(session.get('company_id', 1))
    display_name = session.get('username', '')
    if session.get('user_id'):
        u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if u:
            display_name = u['full_name'] or u['username']
    name_parts = [p for p in (display_name or '').split() if p]
    user_initials = ((name_parts[0][0] if name_parts else '') +
                      (name_parts[1][0] if len(name_parts) > 1 else (name_parts[0][1] if name_parts and len(name_parts[0]) > 1 else ''))).upper() or 'U'
    return company_name, user_initials

def _app_entity_ledger(entity_type, entity_id):
    """Shared by app_party_ledger()/app_vendor_ledger() below — same real entries as the
    website's own party_ledger()/vendor_ledger(): _get_party_ledger_entries()/
    _get_vendor_ledger_entries(), _filter_ledger_entries(), _tab_filter_entries(), all called
    completely unchanged. No download/export button (per instruction — the real export route
    still exists at /ledger/party/<id>/export and /ledger/vendor/<id>/export if wanted later).
    Filters trimmed to just the tab strip (All/Receivables/Payables, the same real
    _tab_filter_entries() the website itself uses) — the website's From/To Date, Vehicle Type
    and Transaction Type dropdowns are real too, but a 4-field filter form is exactly the kind of
    thing this app's other list pages already trim in favor of a simpler control (see
    app_vehicles()/app_trips()/app_ledger()); "Load More" instead of numbered pages, same pattern
    as everywhere else. The Summary card (Opening Balance/Total Debit/Total Credit/Closing
    Balance) doesn't actually exist on the website's own ledger.html — it's computed here from
    real fields anyway (opening_balance, and real debit/credit across every entry), since the
    data's genuinely there even though the website's own page doesn't happen to surface it this
    way.
    """
    conn = get_db()
    if entity_type == 'party':
        row = conn.execute("SELECT * FROM parties WHERE id=?", (entity_id,)).fetchone()
        if not row:
            conn.close()
            return None
        all_entries = _get_party_ledger_entries(entity_id)
        notes = row['notes']
        payment_url = f'/payment/party/{entity_id}'
        role_label = 'Party Ledger'
        # Real pending-trip rows, same query party_ledger() itself uses — needed for the native
        # Add Payment modal's trip-allocation table (app_add_party_payment()).
        pending_trips = _party_pending_trips(conn, entity_id)
        pending_maint_total = 0
    else:
        row = conn.execute("SELECT * FROM vendors WHERE id=?", (entity_id,)).fetchone()
        if not row:
            conn.close()
            return None
        if row['linked_party_id']:
            conn.close()
            return {'redirect_party_id': row['linked_party_id']}
        all_entries = _get_vendor_ledger_entries(entity_id)
        notes = None  # vendors have no notes column at all — real schema difference, not a bug
        payment_url = f'/payment/vendor/{entity_id}'
        role_label = 'Vendor Ledger'
        pending_trips = _vendor_pending_trips(conn, entity_id)
        pending_maint_total = (sum(m['pending'] for m in _vendor_pending_maintenance(conn, entity_id))
                               + sum(o['pending'] for o in _vendor_pending_overheads(conn, entity_id)))
    total_pending_trips = sum(t['pending'] for t in pending_trips)
    conn.close()

    tab_f = request.args.get('tab', '')
    entries = _filter_ledger_entries(all_entries, request.args.get('date_from', ''), request.args.get('date_to', ''),
                                      request.args.get('txn_type', ''), request.args.get('type', ''))
    entries = _tab_filter_entries(entries, tab_f)
    total_count = len(entries)
    try:
        show = int(request.args.get('show', 10))
    except (TypeError, ValueError):
        show = 10
    show = max(10, show)
    page_entries = entries[:show]
    has_more = total_count > show

    final_balance = all_entries[0]['balance'] if all_entries else 0
    total_receivable = max(final_balance, 0)
    total_payable = max(-final_balance, 0)

    opening_balance = row['opening_balance'] or 0
    total_debit = sum(e['debit'] for e in all_entries)
    total_credit = sum(e['credit'] for e in all_entries)

    # Pre-built here rather than a conditional url_for(party-endpoint-or-vendor-endpoint) in the
    # template — much less fragile than juggling two mutually-exclusive optional id kwargs there.
    endpoint = 'app_party_ledger' if entity_type == 'party' else 'app_vendor_ledger'
    id_kwarg = {'party_id': entity_id} if entity_type == 'party' else {'vendor_id': entity_id}
    detail_url_all = url_for(endpoint, **id_kwarg)
    detail_url_receivables = url_for(endpoint, tab='receivables', **id_kwarg)
    detail_url_payables = url_for(endpoint, tab='payables', **id_kwarg)
    load_more_kwargs = dict(id_kwarg, show=show + 10)
    if tab_f:
        load_more_kwargs['tab'] = tab_f
    detail_url_load_more = url_for(endpoint, **load_more_kwargs)

    return {
        'name': row['name'], 'role_label': role_label, 'category': row['category'], 'status': row['status'] or 'Active',
        'contact': row['contact'], 'email': row['email'], 'address': row['address'], 'gstin': row['gstin'],
        'since_date': row['since_date'], 'credit_limit': row['credit_limit'], 'notes': notes,
        'payment_url': payment_url, 'entity_type': entity_type, 'entity_id': entity_id,
        'final_balance': final_balance, 'total_receivable': total_receivable, 'total_payable': total_payable,
        'opening_balance': opening_balance, 'total_debit': total_debit, 'total_credit': total_credit,
        'entries': page_entries, 'total_count': total_count, 'show': show, 'has_more': has_more, 'f_tab': tab_f or 'all',
        'detail_url_all': detail_url_all, 'detail_url_receivables': detail_url_receivables,
        'detail_url_payables': detail_url_payables, 'detail_url_load_more': detail_url_load_more,
        'pending_trips': pending_trips, 'total_pending_trips': total_pending_trips, 'pending_maint_total': pending_maint_total,
        'opening_balance_date': row['opening_balance_date'],
    }

@app.route('/app/ledger/party/<int:party_id>')
def app_party_ledger(party_id):
    data = _app_entity_ledger('party', party_id)
    if data is None:
        return redirect(url_for('app_ledger'))
    _conn = get_db()
    company_name, user_initials = _app_user_display(_conn)
    _conn.close()
    return render_template('app/ledger_detail.html', company_name=company_name, user_initials=user_initials, active='ledger', notif_count=0, **data)

@app.route('/app/ledger/vendor/<int:vendor_id>')
def app_vendor_ledger(vendor_id):
    data = _app_entity_ledger('vendor', vendor_id)
    if data is None:
        return redirect(url_for('app_ledger'))
    if 'redirect_party_id' in data:
        return redirect(url_for('app_party_ledger', party_id=data['redirect_party_id']))
    _conn = get_db()
    company_name, user_initials = _app_user_display(_conn)
    _conn.close()
    return render_template('app/ledger_detail.html', company_name=company_name, user_initials=user_initials, active='ledger', notif_count=0, **data)

@app.route('/app/ledger/party/<int:party_id>/payment', methods=['POST'])
def app_add_party_payment(party_id):
    """"Payment" mode of the in-app Add Payment modal (ledger_detail.html, party) — posts here
    via fetch instead of the website's own #paymentModal, same _insert_party_payment_from_form()
    the website itself uses (same trip-allocation logic, same fields)."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_party_payment_from_form(conn, party_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_party_ledger', party_id=party_id)})

@app.route('/app/ledger/vendor/<int:vendor_id>/payment', methods=['POST'])
def app_add_vendor_payment(vendor_id):
    """"Payment" mode of the in-app Add Payment modal (ledger_detail.html, vendor) — same
    _insert_vendor_payment_from_form() the website's own #paymentModal uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _insert_vendor_payment_from_form(conn, vendor_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_vendor_ledger', vendor_id=vendor_id)})

@app.route('/app/ledger/<entity_type>/<int:entity_id>/opening-balance', methods=['POST'])
def app_update_ledger_opening_balance(entity_type, entity_id):
    """"Opening Balance" mode of the in-app Add Payment modal — same
    _update_ledger_opening_balance_from_form() the website's own #paymentModal (Opening Balance
    tab) uses."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, result = _update_ledger_opening_balance_from_form(conn, entity_type, entity_id, request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': result}), 400
    conn.commit()
    conn.close()
    endpoint = 'app_party_ledger' if entity_type == 'party' else 'app_vendor_ledger'
    id_kwarg = {'party_id': entity_id} if entity_type == 'party' else {'vendor_id': entity_id}
    return jsonify({'ok': True, 'redirect': url_for(endpoint, **id_kwarg)})

def _name_initials(name):
    """Avatar initials from a display name — first letter of the first two words ("Abinash Lenka"
    -> "AL"), or the first two letters of a single word ("Priyanka" -> "PR"). Same rule every
    /app/ route's own inline copy of this uses (see _app_user_display())."""
    parts = [p for p in (name or '').replace('_', ' ').replace('.', ' ').split() if p]
    if not parts:
        return 'U'
    if len(parts) > 1:
        return (parts[0][0] + parts[1][0]).upper()
    return parts[0][:2].upper()

@app.context_processor
def _web_header_context():
    """Website counterpart of _app_header_context() below (which is /app/-only): gives base.html's
    top-right profile chip and sidebar "Signed in as" the user's real Full Name + initials instead
    of session['username'] — which is now a mobile number/email, so showing it there put a phone
    number in the avatar and name. Falls back to the username only for an account with no Full
    Name saved at all (the old-style Priyanka/Prakash logins)."""
    if request.path.startswith('/app/') or request.path.startswith('/static/') or not session.get('user_id'):
        return {}
    conn = get_db()
    u = conn.execute("SELECT full_name, username FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    if not u:
        return {}
    name = (u['full_name'] or '').strip() or u['username']
    return {'web_user_name': name, 'web_user_initials': _name_initials(name)}

@app.context_processor
def _app_header_context():
    """Injects the logged-in user's name/role/email into every /app/ page automatically, so the
    avatar dropdown in base_app.html doesn't need every single app_* route to pass it through by
    hand (the same reasoning as _app_user_display(), one step further). Guarded to /app/ paths
    only so it never touches the website's own templates/context. role_label mirrors the same
    is_admin-wins-over-role logic login()/app_login() already use to set session['is_admin']."""
    if not request.path.startswith('/app/') or not session.get('user_id'):
        return {}
    conn = get_db()
    u = conn.execute("SELECT full_name, username, role, email FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    if not u:
        return {}
    return {
        'app_user_name': u['full_name'] or u['username'],
        'app_user_role': (u['role'] or '').strip() or 'Staff',
        'app_user_email': u['email'] or '',
        'app_is_admin': (u['role'] or '').strip().lower() == 'administrator',
    }

@app.route('/app/profile')
def app_profile():
    """Native Profile page — avatar, name/role/email (same fields as the dropdown above) plus a
    Personal Information block of real users-table columns. Deliberately does NOT include
    Change Password, Notification Preferences, Language or Theme toggles, or an app version
    footer: none of those have a real backend anywhere in this codebase (no self-service
    password-change route, no per-user preferences table, no tracked version number) — same
    "don't fabricate it" call already made for Create Account / Forgot Password on login.html."""
    if not session.get('user_id'):
        return redirect(url_for('app_login'))
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    u = conn.execute("""SELECT username, full_name, phone, email, role, access_level, status, created_at
                         FROM users WHERE id=?""", (session['user_id'],)).fetchone()
    conn.close()
    if not u:
        return redirect(url_for('app_login'))
    member_since = ''
    if u['created_at']:
        try:
            member_since = datetime.datetime.strptime(u['created_at'][:19], '%Y-%m-%d %H:%M:%S').strftime('%d %b %Y')
        except ValueError:
            member_since = u['created_at']
    # Administrators get the full "Profile & Settings" hub (company, banking, invoice, tax, users — same options as the
    # website); every other role keeps the simple profile page below, unchanged.
    return render_template('app/profile_admin.html' if g.is_administrator else 'app/profile.html',
        company_name=company_name, user_initials=user_initials,
        active='profile', notif_count=0,
        full_name=u['full_name'] or u['username'].upper(), username=u['username'], phone=u['phone'] or '',
        email=u['email'] or '', role_label=(u['role'] or '').strip() or 'Staff',
        access_level=u['access_level'] or '', status=u['status'] or 'Active', member_since=member_since,
        is_admin=bool(getattr(g, 'is_administrator', False)))

def _app_settings_page(template_name, section, title, subtitle, **extra):
    """Shared shell for the Administrator-only mobile Settings screens (gate: _ADMIN_ONLY_ENDPOINTS in require_login)."""
    conn = get_db()
    company_name, user_initials = _app_user_display(conn)
    s = _get_all_settings(conn, session.get('company_id', 1))
    payload = extra.pop('loader', lambda c: {})(conn)
    conn.close()
    invoice_example = f"{s['invoice_prefix']}/2026/{int(s['next_invoice_number'] or 1):04d}" if s['invoice_prefix'] else ''
    return render_template(template_name, company_name=s.get('company_name') or company_name, user_initials=user_initials,
                           active='profile', notif_count=0, s=s, section=section, page_title=title, page_sub=subtitle,
                           invoice_example=invoice_example, my_user_id=session['user_id'], **payload, **extra)

@app.route('/app/settings/company')
def app_settings_company():
    return _app_settings_page('app/settings_company.html', 'company', 'Company Profile', 'Manage your business information')

@app.route('/app/settings/banking')
def app_settings_banking():
    return _app_settings_page('app/settings_banking.html', 'banking', 'Banking Details', 'Bank account shown on Tax Invoices')

@app.route('/app/settings/invoice')
def app_settings_invoice():
    return _app_settings_page('app/settings_invoice.html', 'invoice', 'Invoice Settings', 'Configure invoice preferences')

@app.route('/app/settings/tax')
def app_settings_tax():
    return _app_settings_page('app/settings_tax.html', 'tax', 'Tax & Compliance', 'GST, TDS, reverse charge and tax settings')

@app.route('/app/settings/users')
def app_settings_users():
    def load(conn):
        users, total, admin_n, readonly_n, limited_n, inactive_n = _users_with_stats(conn)
        role_counts = {}
        for u in users:
            r = u['role'] or 'Unassigned'
            role_counts[r] = role_counts.get(r, 0) + 1
        logs = conn.execute("""SELECT al.date, al.event, u.username, u.full_name FROM access_logs al
                               LEFT JOIN users u ON al.user_id=u.id ORDER BY al.id DESC LIMIT 50""").fetchall()
        sync = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 8").fetchall()
        # whitelisted fields only — the raw rows carry password hashes and must never be serialised into the page
        users_public = [{k: u.get(k) for k in ('id', 'username', 'full_name', 'phone', 'email', 'role', 'access_level',
                                               'module_access', 'status', 'last_login', 'hide_trip_amounts',
                                               'salary_view_ids', 'salary_employee')} for u in users]
        return dict(users=users_public, total_users=total, admin_users=admin_n, readonly_users=readonly_n, limited_users=limited_n,
                    inactive_users=inactive_n, role_counts=role_counts, access_logs=logs, recent_sync_log=sync,
                    module_list=MODULE_LIST, role_suggestions=_role_suggestions(conn))
    return _app_settings_page('app/settings_users.html', 'users', 'Users & Roles', 'Manage team access and permissions', loader=load)

@app.route('/app/settings/save', methods=['POST'])
def app_settings_save():
    """JSON twin of the website Settings page's POST — same field groups, same writer (_save_settings_group), plus
    readable validation. Administrator-only."""
    form_type = request.form.get('form_type')
    if form_type not in _SETTINGS_FIELD_GROUPS:
        return jsonify({'ok': False, 'error': 'Unknown settings form.'}), 400
    form = request.form.to_dict()
    conn = get_db()
    # The app never echoes the MSG91 key / RC API key back into a form field, so a blank box means "keep the
    # saved one" (the website form pre-fills them, which is why it doesn't need this).
    company_id = session.get('company_id', 1)
    for secret in {'sms': ['msg91_authkey'], 'rc_lookup': ['rc_lookup_api_key']}.get(form_type, []):
        if not (form.get(secret) or '').strip():
            row = conn.execute("SELECT value FROM settings WHERE key=? AND company_id=?", (secret, company_id)).fetchone()
            form[secret] = row['value'] if row else ''
    err = _settings_form_error(form_type, form)
    if err:
        conn.close()
        return jsonify({'ok': False, 'error': err}), 400
    _save_settings_group(conn, form_type, form)
    conn.close()
    return jsonify({'ok': True})

@app.route('/app/profile/update', methods=['POST'])
def app_update_profile():
    """Real self-service "Edit Profile" — a logged-in user updating their OWN full_name/email/
    phone, nothing else. Deliberately narrower than edit_user() (the admin-only User Management
    route, /settings/users/<id>/edit): that one can also change someone's role/access_level/
    status/password, which a user editing their own profile has no business touching (role and
    access level are permissions an admin assigns, not something to self-service; password has
    its own dedicated, current-password-verified flow below). username is real but intentionally
    not editable here — it's the login identity, changing it is a bigger, riskier action than a
    profile edit, and nothing elsewhere in the app has a "rename my username" flow either."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, err = _update_own_profile(conn, session['user_id'], request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': err}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'redirect': url_for('app_profile')})

@app.route('/app/profile/change-password', methods=['POST'])
def app_change_password():
    """Real self-service "Change Password" — verifies the real current password (same
    check_password_hash pattern login()/app_login() already use) before allowing a new one, same
    baseline safeguard any password-change flow needs. No length/complexity rule enforced beyond
    "non-empty" — nothing else in this codebase enforces one either (add_user()'s own password
    field is optional with zero validation), so this doesn't invent a stricter bar than the rest
    of the app already holds itself to."""
    from flask import jsonify
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Session expired — please log in again.'}), 401
    conn = get_db()
    ok, err = _change_own_password(conn, session['user_id'], request.form)
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': err}), 400
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/app/login/otp', methods=['GET', 'POST'])
def app_login_otp():
    """Mobile Login-with-Mobile-OTP. Same rules as the website's (see _start_otp_login / _finish_otp_login): code kept
    server side, 5 wrong tries per code, limits on how many codes can be requested, same reply for unknown numbers."""
    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        if not phone:
            return render_template('app/login_otp.html', error='Enter a mobile number.')
        if not USERNAME_MOBILE_RE.match(phone):
            return render_template('app/login_otp.html', error='Enter a valid 10-digit Indian mobile number.')
        err = _start_otp_login(phone)
        if err:
            return render_template('app/login_otp.html', error=err), (429 if err.startswith('Too many') else 200)
        return render_template('app/verify_otp.html', phone=phone, error=None)
    return render_template('app/login_otp.html', error=None)

@app.route('/app/login/otp/verify', methods=['POST'])
def app_verify_login_otp():
    code = (request.form.get('otp') or '').strip()
    phone = request.form.get('phone') or ''
    user, err = _finish_otp_login(phone, code)
    if err:
        if err.startswith('Incorrect'):
            return render_template('app/verify_otp.html', phone=phone, error=err)
        return render_template('app/login_otp.html', error=err)
    return redirect(url_for('app_dashboard'))

if __name__ == '__main__':
    # Defaults to the same debug=True this always ran with locally — nothing changes for local
    # `python app.py` unless FLASK_DEBUG is actually set. A real deployment MUST set
    # FLASK_DEBUG=0: debug mode shows a full interactive stack trace (and lets a visitor execute
    # code) on any unhandled error, which is a serious leak in production. Set explicitly on
    # app.debug before the scheduler-gating check below reads it, since that check runs before
    # app.run() would otherwise set it as a side effect.
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.debug = debug_mode

    # Weekly compliance scheduler — opt-in via ENABLE_COMPLIANCE_SCHEDULER=1, not on by default.
    # A background scheduler that starts silently every time `python app.py` is run (which
    # happens constantly during local development, including on every debug-reloader restart)
    # is more surprising than helpful; a real deployment should set this env var deliberately.
    # When it IS enabled: with debug=True, Werkzeug's reloader runs this file in two processes
    # (a watcher + the actual server); only the actual server process has WERKZEUG_RUN_MAIN set,
    # so gating on it stops the job from being registered twice. Under gunicorn (no reloader, no
    # debug), it starts once per worker — fine for a single-worker deployment; a multi-worker
    # production deployment should instead trigger the sync from an external cron hitting a
    # protected endpoint, not this in-process scheduler.
    if os.environ.get('ENABLE_COMPLIANCE_SCHEDULER') == '1' and (os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug):
        import logging
        logging.basicConfig(level=logging.INFO)
        from scheduler import start_scheduler
        # RC sync + Challan sync (eChallan) are independent opt-ins — ENABLE_RC_SYNC=1 /
        # ENABLE_CHALLAN_SYNC=1 — since unlike the compliance job they spend real paid API
        # credits. Their actual cadence (in days) is a Settings value, not fixed here — see
        # scheduler.py's run_sync_tick. Off by default; not enabled here, per instruction not
        # to trigger any live eChallan calls yet.
        start_scheduler(get_db, enable_rc_sync=(os.environ.get('ENABLE_RC_SYNC') == '1'),
                         enable_challan_sync=(os.environ.get('ENABLE_CHALLAN_SYNC') == '1'))
    # Defaults to 127.0.0.1 (localhost-only), same as this has always run — set FLASK_HOST=0.0.0.0
    # to also accept connections from other devices on the same LAN/Wi-Fi (e.g. testing the /app/
    # mobile section on an actual phone during local development). Never do this on a real
    # deployment; that's what a real WSGI server + reverse proxy is for.
    # threaded=True — the dev server otherwise handles exactly one request at a time; with a phone
    # and a couple of browser tabs all hitting it, one slow/open request blocks every other tab
    # until it finishes, which reads as "the app froze". Purely a concurrency fix, no behavior
    # change for a single request. Still not a substitute for a real WSGI server in production.
    # use_reloader=False by default — debug=True's own auto-reloader restarts the ENTIRE server
    # process on every .py file save, and while that's restarting (a real second or two), any
    # request from a phone actively testing the app — including a plain button tap's navigation —
    # just hangs or gets refused, which reads exactly like "the button didn't respond, I have to
    # tap it again". Real, reported symptom during a live editing session, not a bug in any page's
    # own code. debug=True's other benefit (a full interactive traceback on an unhandled error)
    # is unaffected — only the auto-restart-on-save behavior is off; code changes now take effect
    # on the next manual restart instead of automatically. Set FLASK_USE_RELOADER=1 to opt back
    # into the old auto-reload-on-save behavior if ever wanted for a different workflow.
    app.run(debug=debug_mode, port=5050, host=os.environ.get('FLASK_HOST', '127.0.0.1'), threaded=True,
            use_reloader=os.environ.get('FLASK_USE_RELOADER') == '1')
