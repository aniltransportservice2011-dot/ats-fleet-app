"""Thin database-connection wrapper — the ONLY place that needs to change if this app's database
is ever swapped (e.g. SQLite -> PostgreSQL, for the future multi-tenancy retrofit — see
/Users/beenash/.claude/plans/whimsical-conjuring-parrot.md). Every one of the ~475 call sites
elsewhere in this app just does conn.execute(sql, params); none of them need to know or care
which real database is underneath — only get_db() (app.py) and this file do.

Today (backend='sqlite', the only backend actually wired up and tested): pure passthrough,
zero behavior change from a bare sqlite3.Connection. Every method just forwards straight to the
real connection.

What this DOES solve for a future swap: placeholder syntax. SQLite uses '?', PostgreSQL (via
psycopg2) uses '%s' — since every call site already just writes SQL with '?' and calls
.execute(sql, params), a future Postgres backend can translate '?' -> '%s' in one place instead
of at each of the ~475 call sites. _translate_placeholders() below does that translation and is
unit-tested in isolation (run this file directly: `python3 db.py`) — confirmed safe against
every SQL string actually used in this app today (grepped app.py: no query has a literal '?'
character inside a string literal, which is the one thing that would make a blind substitution
wrong).

What this will NOT automatically solve, and needs fixing by hand once a Postgres backend is
actually wired in and testable against a real server (a short, findable list — not a sweep of
the whole codebase):
  - cursor.lastrowid (34 call sites in app.py as of writing) — psycopg2 cursors don't support
    this; Postgres needs "INSERT ... RETURNING id" instead.
  - 2 uses of SQLite's INSERT OR REPLACE — Postgres needs INSERT ... ON CONFLICT DO UPDATE.
  - ~36 uses of SQLite date functions (strftime, etc.) — Postgres syntax differs.

The Postgres path (wiring _translate_placeholders into Connection.execute) is deliberately NOT
activated below — there is no Postgres instance available to test it against end-to-end in this
environment. Activating it is one line (see the TODO in Connection.execute), but shouldn't
happen until it can actually be run against a real Postgres connection, not just unit-tested as
a string transformation.
"""
import re

_PLACEHOLDER_RE = re.compile(r'\?')


def _translate_placeholders(sql):
    """'?' -> '%s', in query order — the one mechanical difference between SQLite and
    psycopg2's placeholder styles. Pure string transformation, no DB connection needed to
    verify it (see the tests at the bottom of this file)."""
    return _PLACEHOLDER_RE.sub('%s', sql)


class Connection:
    """Wraps a real DB-API connection (sqlite3.Connection today). `backend` records which real
    driver is underneath so a future Postgres-backed instance of this class can behave
    differently in execute() — see the TODO below. Every other method is a direct passthrough."""

    def __init__(self, real_conn, backend='sqlite'):
        self._conn = real_conn
        self.backend = backend

    def execute(self, sql, params=()):
        # TODO once a Postgres backend is actually wired up (see get_db() in app.py) and can be
        # tested against a real server, not just unit-tested as a string transformation:
        #     if self.backend == 'postgres':
        #         sql = _translate_placeholders(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        return self._conn.executemany(sql, seq_of_params)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __getattr__(self, name):
        # Anything not explicitly wrapped above — passthrough to the real connection. Only ever
        # exercised on the sqlite backend today.
        return getattr(self._conn, name)


if __name__ == '__main__':
    # Unit tests for _translate_placeholders — pure string logic, no database needed. Run with
    # `python3 db.py`.
    cases = [
        ("SELECT * FROM vehicles WHERE id=?", "SELECT * FROM vehicles WHERE id=%s"),
        ("SELECT * FROM trips WHERE date>=? AND date<=?", "SELECT * FROM trips WHERE date>=%s AND date<=%s"),
        ("SELECT * FROM vehicles", "SELECT * FROM vehicles"),  # no placeholders at all
        ("UPDATE tyre_stock SET status='Scrapped' WHERE id=? AND status='In Stock'",
         "UPDATE tyre_stock SET status='Scrapped' WHERE id=%s AND status='In Stock'"),
        ("SELECT * FROM t WHERE id IN (?,?,?)", "SELECT * FROM t WHERE id IN (%s,%s,%s)"),
    ]
    failures = 0
    for sql_in, expected in cases:
        actual = _translate_placeholders(sql_in)
        ok = actual == expected
        if not ok:
            failures += 1
        print(f"  [{'OK' if ok else 'FAIL'}] {sql_in!r} -> {actual!r}")
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    if failures:
        raise SystemExit(1)

    # Passthrough sanity check against a real sqlite3 connection — confirms the wrapper doesn't
    # change behavior on the backend that's actually in use today.
    import sqlite3
    raw = sqlite3.connect(':memory:')
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn = Connection(raw, backend='sqlite')
    conn.execute("INSERT INTO t (name) VALUES (?)", ('hello',))
    conn.commit()
    row = conn.execute("SELECT * FROM t WHERE id=?", (1,)).fetchone()
    assert row['name'] == 'hello', "passthrough execute/fetchone broken"
    cur = conn.execute("INSERT INTO t (name) VALUES (?)", ('world',))
    assert cur.lastrowid == 2, "passthrough lastrowid broken"
    conn.close()
    print("Passthrough sanity check against a real sqlite3 connection: OK")
