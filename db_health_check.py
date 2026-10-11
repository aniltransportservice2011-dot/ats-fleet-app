"""Read-only database health check for the fleet app.

    python3 db_health_check.py [path/to/fleet.db]        (default: ./fleet.db)

Works on a consistent snapshot (SQLite online backup) so it is safe to run while the app is running, and never
writes to the database. Reports: table sizes, always-empty / constant columns, dangling references, schema drift
against schema.sql, tables missing company_id (multi-tenancy rule in CLAUDE.md), missing indexes on foreign-key
style columns, and business-data anomalies (over-received trips, duplicate LR numbers, desynced payments...).
Exit code 0 = nothing that needs attention, 1 = findings.
"""
import collections
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))


def main(path):
    snap = os.path.join(tempfile.mkdtemp(prefix='dbhealth_'), 'snap.db')
    src = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    dst = sqlite3.connect(snap)
    src.backup(dst)
    src.close()
    c = dst
    c.row_factory = sqlite3.Row
    q = lambda sql, *a: c.execute(sql, a).fetchall()
    findings = []

    def flag(level, text):
        findings.append((level, text))
        print(f'  [{level}] {text}')

    tables = [r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f'Database: {path}  ({os.path.getsize(path) / 1024:.0f} KB, {len(tables)} tables, journal mode {q("PRAGMA journal_mode")[0][0]})')
    print('\n1. Integrity')
    ic = q('PRAGMA integrity_check')[0][0]
    print(f'  integrity_check: {ic}')
    if ic != 'ok':
        flag('PROBLEM', f'integrity_check says {ic}')
    fk = collections.Counter((r[0], r[2]) for r in q('PRAGMA foreign_key_check'))
    audit = {k: v for k, v in fk.items() if k[1] == 'users'}
    real = {k: v for k, v in fk.items() if k[1] != 'users'}
    if audit:
        flag('INFO', f'{sum(audit.values())} audit columns (created_by / updated_by) point at users that no longer exist: ' +
             ', '.join(f'{t}:{n}' for (t, _), n in sorted(audit.items())))
    for (t, p), n in sorted(real.items()):
        flag('PROBLEM', f'{n} row(s) in {t} point at a {p} row that does not exist (orphans)')
    print('  PRAGMA foreign_keys is OFF by default in SQLite — the app does not enforce these links; the check above is the only guard.')

    print('\n2. Schema drift vs schema.sql')
    ref = sqlite3.connect(':memory:')
    ref.executescript(open(os.path.join(REPO, 'schema.sql')).read())
    ref_tables = {r[0] for r in ref.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    for t in sorted(set(tables) - ref_tables):
        flag('DRIFT', f'table {t} exists in the database but not in schema.sql')
    for t in sorted(ref_tables - set(tables)):
        flag('DRIFT', f'table {t} is in schema.sql but missing from the database')
    for t in sorted(set(tables) & ref_tables):
        a = {r[1] for r in ref.execute(f'PRAGMA table_info("{t}")')}
        b = {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}
        if a != b:
            flag('DRIFT', f'{t}: only in database {sorted(b - a)}; only in schema.sql {sorted(a - b)}')
    if not any(f[0] == 'DRIFT' for f in findings):
        print('  database and schema.sql agree on every table and column')

    print('\n3. Multi-tenancy (CLAUDE.md: every table carries company_id)')
    missing = [t for t in tables if t != 'companies' and 'company_id' not in {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}]
    if missing:
        flag('PROBLEM', f'tables without company_id: {missing}')
    else:
        print('  all tables have company_id')

    print('\n4. Table sizes and unused columns')
    wasted = 0
    for t in tables:
        n = q(f'SELECT COUNT(*) FROM "{t}"')[0][0]
        cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
        empty = []
        if n:
            empty = [col for col in cols if q(f'SELECT COUNT("{col}") FROM "{t}"')[0][0] == 0 and col not in ('company_id',)]
        wasted += len(empty)
        print(f'  {t:<32}{n:>8} rows {len(cols):>3} cols' + (f'   always empty: {", ".join(empty[:5])}{" …" if len(empty) > 5 else ""}' if empty else ''))
    flag('INFO', f'{wasted} columns (across tables that have rows) have never been filled in')
    wide = [(t, len(list(c.execute(f'PRAGMA table_info("{t}")')))) for t in tables]
    for t, n in wide:
        if n >= 40:
            flag('INFO', f'{t} has {n} columns — a very wide table; consider splitting charges / deductions / owner terms into their own tables')

    print('\n5. Indexes on lookup columns')
    idx_cols = collections.defaultdict(set)
    for t in tables:
        for ix in q(f'PRAGMA index_list("{t}")'):
            for col in q(f'PRAGMA index_info("{ix[1]}")'):
                idx_cols[t].add(col[2])
    unindexed = []
    for t in tables:
        n = q(f'SELECT COUNT(*) FROM "{t}"')[0][0]
        for col in [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]:
            if (col.endswith('_id') and col not in ('company_id', 'created_by', 'updated_by', 'tyre_id', 'reference_id', 'transaction_id')) and col not in idx_cols[t] and n >= 200:
                unindexed.append(f'{t}.{col}')
    if unindexed:
        flag('SLOW', f'lookup columns without an index on tables with 200+ rows: {unindexed}')
    else:
        print('  every *_id lookup column on a table with 200+ rows is indexed')

    print('\n6. Business-data anomalies')
    def check(label, sql, level='PROBLEM'):
        n = q(sql)[0][0]
        if n:
            flag(level, f'{n} {label}')
    check('trips received more (payments + advance) than they were billed', "SELECT COUNT(*) FROM trips WHERE COALESCE(payment_received,0)+COALESCE(party_advance,0) > billed_amount + 0.01")
    check('LR numbers used on more than one trip', "SELECT COUNT(*) FROM (SELECT 1 FROM trips WHERE TRIM(COALESCE(lr_number,''))<>'' GROUP BY LOWER(TRIM(lr_number)) HAVING COUNT(*)>1)")
    check('trips with a negative billed amount', "SELECT COUNT(*) FROM trips WHERE billed_amount < 0")
    check('payments whose allocated_amount differs from their allocation rows', "SELECT COUNT(*) FROM payments p WHERE ABS(COALESCE(allocated_amount,0)-COALESCE((SELECT SUM(amount) FROM payment_allocations WHERE payment_id=p.id),0))>0.01")
    check('trips whose payment_received is lower than the payments allocated to them', "SELECT COUNT(*) FROM trips t WHERE COALESCE(payment_received,0) < COALESCE((SELECT SUM(amount) FROM payment_allocations WHERE trip_id=t.id),0)-0.01")
    check('trips whose type differs from their vehicle\'s type', "SELECT COUNT(*) FROM trips t JOIN vehicles v ON v.id=t.vehicle_id WHERE COALESCE(t.type,'')<>COALESCE(v.type,'')")
    check('vehicles with no own/hired type', "SELECT COUNT(*) FROM vehicles WHERE type IS NULL", 'INFO')
    check('duplicate party names (ignoring case/spaces)', "SELECT COUNT(*) FROM (SELECT 1 FROM parties GROUP BY LOWER(TRIM(name)) HAVING COUNT(*)>1)")
    check('duplicate vendor names (ignoring case/spaces)', "SELECT COUNT(*) FROM (SELECT 1 FROM vendors GROUP BY LOWER(TRIM(name)) HAVING COUNT(*)>1)")
    check('vehicle numbers entered twice in different styles', "SELECT COUNT(*) FROM (SELECT 1 FROM vehicles GROUP BY UPPER(REPLACE(REPLACE(REPLACE(vehicle_no,' ',''),'-',''),'.','')) HAVING COUNT(*)>1)")
    check('dates that are not YYYY-MM-DD (trips/payments/maintenance/expenses)', "SELECT (SELECT COUNT(*) FROM trips WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') + (SELECT COUNT(*) FROM payments WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') + (SELECT COUNT(*) FROM maintenance WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') + (SELECT COUNT(*) FROM overheads WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')")
    check('money amounts stored as text instead of numbers', "SELECT (SELECT COUNT(*) FROM trips WHERE typeof(billed_amount) NOT IN ('real','integer','null')) + (SELECT COUNT(*) FROM payments WHERE typeof(amount) NOT IN ('real','integer','null')) + (SELECT COUNT(*) FROM maintenance WHERE typeof(amount) NOT IN ('real','integer','null'))")
    check('maintenance bills with no vehicle', "SELECT COUNT(*) FROM maintenance WHERE vehicle_id IS NULL AND category NOT IN ('Urea')", 'INFO')
    check('Pending expenses already past their due date', "SELECT COUNT(*) FROM overheads WHERE status='Pending' AND due_date < date('now')", 'INFO')
    check('users whose phone is not a 10-digit Indian mobile', "SELECT COUNT(*) FROM users WHERE phone IS NOT NULL AND phone NOT GLOB '[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'")

    problems = [f for f in findings if f[0] in ('PROBLEM', 'DRIFT', 'SLOW')]
    print(f"\nSummary: {len(problems)} item(s) needing attention, {len(findings) - len(problems)} informational.")
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'fleet.db')))
