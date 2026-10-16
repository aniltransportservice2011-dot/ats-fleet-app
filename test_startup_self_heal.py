"""Regression test: an OLDER database (one that never received migrate_guarantee_qty.sql / migrate_invoice_batch_items_vendor.sql /
migrate_invoice_charge_exclusions.sql) must not make invoices crash. On start-up the app adds those purely additive pieces itself
(_ensure_billing_columns). Runs against disposable copies of the dated database backups; the real fleet.db is never modified.

Usage:  python3 test_startup_self_heal.py
"""
import glob
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import app as appmod                                   # noqa: E402  (start-up steps run on the project's own db, as in every other suite)
from test_support import ensure_test_user              # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
        print(f'[OK]   {msg}')
    else:
        fail += 1
        print(f'[FAIL] {msg}')


def fingerprint(path, skip=()):
    """Row count + a hash of every row, per table (columns that the step adds are left out so before/after are comparable)."""
    c = sqlite3.connect(path)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1").fetchall():
        cols = sorted(r[1] for r in c.execute(f'PRAGMA table_info("{t}")') if f'{t}.{r[1]}' not in skip)      # by name: a table rebuild may reorder columns
        rows = c.execute(f'SELECT {", ".join(chr(34) + x + chr(34) for x in cols)} FROM "{t}" ORDER BY "id"').fetchall() if cols and 'id' in cols else (c.execute(f'SELECT {", ".join(chr(34) + x + chr(34) for x in cols)} FROM "{t}"').fetchall() if cols else [])
        out[t] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
    c.close()
    return out


tmp = tempfile.mkdtemp(prefix='selfheal_')
sources = sorted(glob.glob(os.path.join(REPO, 'fleet.db.b*')))
check(len(sources) > 0, f'{len(sources)} older database backups found to test against')
skip = {'trips.guarantee_qty', 'invoice_batch_items.vendor_id', 'payment_allocations.overhead_id', 'payment_allocations.maintenance_id', 'payments.entered_from'}   # the columns the two start-up steps add
tested_invoices = 0
for src in sources:
    name = os.path.basename(src)
    dst = os.path.join(tmp, name)
    shutil.copy(src, dst)
    c = sqlite3.connect(dst)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'trips' not in tables or 'invoice_batches' not in tables:
        c.close()
        r = appmod._ensure_billing_columns(dst)
        check(not str(r).startswith('failed'), f'{name}: pre-invoice database is left alone without an error ({r})')
        continue
    trip_cols = [r[1] for r in c.execute('PRAGMA table_info(trips)')]
    c.close()
    before = fingerprint(dst, skip)
    r1 = appmod._ensure_billing_columns(dst)
    check(not str(r1).startswith('failed'), f'{name}: first run ok ({r1})')
    appmod._ensure_overhead_allocation_column(dst)          # the app's other start-up step that production runs on the same database
    c = sqlite3.connect(dst)
    check('guarantee_qty' in [r[1] for r in c.execute('PRAGMA table_info(trips)')], f'{name}: trips.guarantee_qty present')
    check('vendor_id' in [r[1] for r in c.execute('PRAGMA table_info(invoice_batch_items)')], f'{name}: invoice_batch_items.vendor_id present')
    check(c.execute("SELECT 1 FROM sqlite_master WHERE name='invoice_batch_charge_exclusions'").fetchone() is not None, f'{name}: charge-exclusions table present')
    check(c.execute("SELECT 1 FROM sqlite_master WHERE name='trip_cost_entries'").fetchone() is not None and 'entered_from' in [r[1] for r in c.execute('PRAGMA table_info(payments)')], f'{name}: trip_cost_entries table and payments.entered_from present')
    if 'guarantee_qty' not in trip_cols:
        check(c.execute('SELECT COUNT(*) FROM trips WHERE COALESCE(guarantee_qty,0)<>0').fetchone()[0] == 0, f'{name}: every existing trip has guarantee 0 (billing unchanged)')
    c.close()
    after = fingerprint(dst, skip)
    changed = [t for t in before if after.get(t) != before[t]]
    check(not changed, f'{name}: no existing row in any table changed' + (f' (changed: {changed})' if changed else ''))
    check(appmod._ensure_billing_columns(dst) == 'already up to date', f'{name}: second run does nothing')

    # invoices now work on this database: preview + download for every batch it has, and the invoice list pages
    ensure_test_user(dst)

    def gdb(p=dst):
        x = sqlite3.connect(p)
        x.row_factory = sqlite3.Row
        return x
    appmod.get_db = gdb
    appmod.app.config['TESTING'] = True
    cl = appmod.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'] = 900001
    c = sqlite3.connect(dst)
    batch_ids = [r[0] for r in c.execute('SELECT id FROM invoice_batches ORDER BY id LIMIT 3')]
    c.close()
    for b in batch_ids:
        for suffix in ('/pdf/preview', '/pdf', '/edit'):
            try:
                code = cl.get(f'/invoices/generated/{b}{suffix}').status_code
            except Exception as e:                        # an unhandled error is exactly the bug
                code = f'EXC {e!r}'[:70]
            check(code == 200, f'{name}: invoice {b}{suffix} opens ({code})')
            tested_invoices += 1
    c = sqlite3.connect(dst)
    has_maint_alloc = 'maintenance_id' in [r[1] for r in c.execute('PRAGMA table_info(payment_allocations)')]
    c.close()
    # databases older than migrate_payment_allocation_maintenance.sql (a table rebuild, NOT self-applying) cannot open the money pages until that
    # migration is run by hand — check_schema_before_deploy.py lists it; here it is reported, not hidden
    pages = ('/invoice-center', '/trips', '/dashboard') if has_maint_alloc else ('/invoice-center', '/trips')
    if not has_maint_alloc:
        print(f'[NOTE] {name}: needs migrate_payment_allocation_maintenance.sql by hand (dashboard/ledger pages skipped)')
    for path in pages:
        try:
            code = cl.get(path).status_code
        except Exception as e:
            code = f'EXC {e!r}'[:70]
        check(code == 200, f'{name}: {path} opens ({code})')

print(f'\nInvoice pages exercised on old databases: {tested_invoices}')
print(f'SUMMARY: {ok} passed, {fail} failed')
shutil.rmtree(tmp, ignore_errors=True)
print('Disposable copies removed. Real fleet.db was never modified by this test.')
sys.exit(1 if fail else 0)
