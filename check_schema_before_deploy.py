"""Read-only pre-deployment check: does THIS database have everything the current code needs?

    python3 check_schema_before_deploy.py [path/to/fleet.db]        (default: ./fleet.db)

Compares the database with schema.sql (the authoritative schema the code is written against) and lists, for every missing table / column /
index, which migrate_*.sql file adds it. It never writes to the database (it reads a snapshot taken with SQLite's online backup, so it is safe
while the app is running).

Exit code 0 = nothing missing, 1 = something is missing (apply the listed migrations, in the order shown, BEFORE deploying the code).

What the app creates for itself on start-up (the `users` per-user switches, payment_allocations.overhead_id, the login/OTP tables, the
performance indexes) is applied to the SNAPSHOT first, so it is not reported as missing — only what needs a manual migration is.
"""
import ast
import glob
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))


_STARTUP_STEPS = ('_ensure_overhead_allocation_column', '_ensure_performance_indexes', '_ensure_auth_tables', '_ensure_user_extra_columns', '_ensure_billing_columns')


def _apply_startup_steps(snapshot_path):
    """Runs the app's own self-applying start-up steps (read from app.py's source, without importing the app) against the snapshot copy."""
    tree = ast.parse(open(os.path.join(REPO, 'app.py'), encoding='utf-8').read())
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _STARTUP_STEPS:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(tg, ast.Name) and tg.id.startswith('_') and tg.id.isupper() for tg in node.targets) \
                and isinstance(node.value, (ast.Tuple, ast.List, ast.Constant, ast.Dict, ast.Set)):
            keep.append(node)                       # constants such as _USER_EXTRA_COLUMNS
    import datetime, time, shutil
    ns = {'os': os, 'sqlite3': sqlite3, 'sys': sys, 'DB': snapshot_path, 're': re, 'datetime': datetime, 'time': time, 'shutil': shutil}
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'app.py', 'exec'), ns)
    results = {}
    for name in _STARTUP_STEPS:
        results[name] = ns[name](snapshot_path)
    return results


def _migrations():
    out = []
    for f in sorted(glob.glob(os.path.join(REPO, 'migrate_*.sql'))):
        out.append((os.path.basename(f), open(f, encoding='utf-8').read()))
    return out


def _fixers(migs, table, column=None, index=None):
    hits = []
    for name, text in migs:
        body = '\n'.join(ln for ln in text.splitlines() if not ln.strip().startswith('--'))
        if index and re.search(rf'CREATE\s+(UNIQUE\s+)?INDEX\s+(IF NOT EXISTS\s+)?{re.escape(index)}\b', body, re.I):
            hits.append(name)
        elif column and re.search(rf'ALTER\s+TABLE\s+{re.escape(table)}\s+ADD\s+COLUMN\s+{re.escape(column)}\b', body, re.I):
            hits.append(name)
        elif column and re.search(rf'CREATE\s+TABLE\s+{re.escape(table)}_new\b[^;]*\b{re.escape(column)}\b', body, re.I | re.S):
            hits.append(name)            # a table rebuild that carries the column
        elif not column and not index and re.search(rf'CREATE\s+TABLE\s+(IF NOT EXISTS\s+)?{re.escape(table)}\b', body, re.I):
            hits.append(name)
    return hits


def main(path):
    if not os.path.exists(path):
        print(f'No database file at {path}')
        return 2
    snap = os.path.join(tempfile.mkdtemp(prefix='schemacheck_'), 'snap.db')
    src = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    dst = sqlite3.connect(snap)
    src.backup(dst)
    src.close()
    dst.close()
    startup = _apply_startup_steps(snap)
    live = sqlite3.connect(snap)
    ref = sqlite3.connect(':memory:')
    ref.executescript(open(os.path.join(REPO, 'schema.sql'), encoding='utf-8').read())

    def tables(c):
        return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    def cols(c, t):
        return [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]

    def indexes(c):
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}

    migs = _migrations()
    live_tables = set(tables(live))
    problems = []
    for t in tables(ref):
        if t not in live_tables:
            problems.append(('table', f'table  {t}', _fixers(migs, t)))
            continue
        have = set(cols(live, t))
        for c in cols(ref, t):
            if c not in have:
                problems.append(('column', f'column {t}.{c}', _fixers(migs, t, column=c)))
    for ix in sorted(indexes(ref) - indexes(live)):
        tbl = ref.execute("SELECT tbl_name FROM sqlite_master WHERE name=?", (ix,)).fetchone()[0]
        if tbl in live_tables:
            problems.append(('index', f'index  {ix}  (on {tbl})', _fixers(migs, tbl, index=ix)))
    extra = [(t, c) for t in tables(ref) if t in live_tables for c in cols(live, t) if c not in cols(ref, t)]

    print(f'Database: {path}')
    failed = {k: v for k, v in startup.items() if isinstance(v, str) and v.startswith('failed')}
    if failed:
        print('Warning - a start-up step could not run on the snapshot:', failed)
    if not problems:
        print('OK - every table, column and index the code expects is present.')
    else:
        print(f'MISSING ({len(problems)}): apply these BEFORE deploying the code\n')
        for _, what, fix in problems:
            print(f'  {what:62s} -> {", ".join(fix) if fix else "no migration file adds this (check schema.sql / ask)"}')
        order = []
        for _, _, fix in problems:
            for f in fix:
                if f not in order:
                    order.append(f)
        if order:
            print('\nMigration files needed, in this order (each is run ONCE; skip any not listed):')
            for f in sorted(order):
                print(f'  sqlite3 fleet.db < {f}')
    if extra:
        print('\nNote - present in the database but not in schema.sql (harmless, but schema.sql may be stale):',
              ', '.join(f'{t}.{c}' for t, c in extra[:12]))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'fleet.db')))
