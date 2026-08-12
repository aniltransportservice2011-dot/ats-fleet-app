"""Two-company isolation test — the actual test that matters for multi-tenancy Step A.

NOT run as part of the normal app or test suite. This is scratch verification code, written
now so it's ready the moment a real Postgres connection exists (e.g. a free Neon dev database),
rather than something to write from scratch later. It does not touch fleet.db, app.py, or
anything else the live app depends on — it only talks to whatever Postgres connection string
you give it, and only ever against a throwaway/dev database (it drops and recreates every
table it touches — never point this at anything you want to keep).

Usage:
    pip install psycopg2-binary        # not in requirements.txt on purpose — dev-only tool
    export DATABASE_URL="postgresql://user:pass@host/dbname?sslmode=require"
    python3 test_multitenancy_isolation.py

What this actually proves, not just "does it run without an error":
  1. The 5 composite UNIQUE constraints work — two companies can each register a vehicle
     "OD14H4222" and a user "admin" without colliding (the exact bug that would happen on the
     OLD schema.sql's plain UNIQUE constraints).
  2. Row-Level Security genuinely isolates — querying as company 1 returns only company 1's
     rows, even with a deliberately-unscoped "SELECT * FROM vehicles" that has no WHERE clause
     at all. This is the actual safety net multi-tenancy Step A depends on.
  3. The RLS-vs-table-owner gotcha documented in schema_postgres.sql's header — reports whether
     the connecting role is the table owner (if so, and if the FORCE ROW LEVEL SECURITY clauses
     in the schema weren't there, every policy would be silently ignored for this role).
"""
import os
import sys

DATABASE_URL = os.environ.get('DATABASE_URL')
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema_postgres.sql')


def _fail(msg):
    print(f"\n[FAIL] {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"[OK]   {msg}")


def main():
    if not DATABASE_URL:
        _fail("Set DATABASE_URL to a real Postgres connection string first (e.g. from Neon). "
              "This script refuses to guess a default — a throwaway test database should be "
              "chosen deliberately, not accidentally pointed at something real.")

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        _fail("psycopg2 not installed. Run: pip install psycopg2-binary")

    print(f"Connecting to {DATABASE_URL.split('@')[-1]}...")  # never print the credential part
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # --- Clean slate: drop everything this schema would create, so this script is re-runnable ---
    print("\n--- Resetting to a clean slate (this connection's own schema only) ---")
    cur.execute("""
        DO $$ DECLARE r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
    """)
    _ok("Dropped any existing tables in the public schema")

    print("\n--- Loading schema_postgres.sql ---")
    with open(SCHEMA_FILE) as f:
        schema_sql = f.read()
    # Strip the commented-out ROLE section at the end — psycopg2 can run the rest as one script
    cur.execute(schema_sql)
    _ok(f"Loaded {SCHEMA_FILE}")

    # --- Report whether we're the table owner (the RLS gotcha the schema file's header warns about) ---
    print("\n--- Checking the RLS-vs-owner gotcha ---")
    cur.execute("SELECT current_user;")
    current_user = cur.fetchone()['current_user']
    cur.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'vehicles';")
    owner = cur.fetchone()['tableowner']
    print(f"  Connected as: {current_user}")
    print(f"  Table owner:  {owner}")
    if current_user == owner:
        print("  This connection IS the table owner — without FORCE ROW LEVEL SECURITY (which")
        print("  schema_postgres.sql includes), every RLS policy below would be silently a")
        print("  no-op for this exact connection. Testing that FORCE actually saves us.")
    else:
        _ok("Connection is NOT the table owner — the more typical multi-tenant setup")

    # --- Seed two companies with deliberately colliding data ---
    print("\n--- Seeding two companies with colliding natural keys ---")
    cur.execute("INSERT INTO companies (name) VALUES ('Test Company A') RETURNING id;")
    company_a = cur.fetchone()['id']
    cur.execute("INSERT INTO companies (name) VALUES ('Test Company B') RETURNING id;")
    company_b = cur.fetchone()['id']
    print(f"  Company A id={company_a}, Company B id={company_b}")

    try:
        cur.execute("INSERT INTO vehicles (vehicle_no, company_id) VALUES ('OD14H4222', %s);", (company_a,))
        cur.execute("INSERT INTO vehicles (vehicle_no, company_id) VALUES ('OD14H4222', %s);", (company_b,))
        _ok("Both companies registered vehicle 'OD14H4222' without colliding (composite UNIQUE works)")
    except Exception as e:
        _fail(f"Vehicle number collision test failed: {e}")

    try:
        cur.execute("INSERT INTO users (username, password_hash, company_id) VALUES ('admin', 'x', %s);", (company_a,))
        cur.execute("INSERT INTO users (username, password_hash, company_id) VALUES ('admin', 'x', %s);", (company_b,))
        _ok("Both companies registered username 'admin' without colliding (composite UNIQUE works)")
    except Exception as e:
        _fail(f"Username collision test failed: {e}")

    # Give company A a second vehicle, so a leak would be obvious (more A rows than B rows)
    cur.execute("INSERT INTO vehicles (vehicle_no, company_id) VALUES ('OD14X9999', %s);", (company_a,))

    # --- The test that actually matters: does RLS block cross-company reads? ---
    print("\n--- Testing Row-Level Security isolation ---")
    cur.execute("SET app.current_company_id = %s;", (str(company_a),))
    cur.execute("SELECT vehicle_no FROM vehicles;")  # deliberately unscoped — no WHERE at all
    rows_as_a = [r['vehicle_no'] for r in cur.fetchall()]
    print(f"  Querying as Company A (unscoped SELECT *): {rows_as_a}")
    if set(rows_as_a) != {'OD14H4222', 'OD14X9999'}:
        _fail(f"Company A should see exactly its own 2 vehicles, got {rows_as_a}")
    _ok("Company A sees only its own vehicles, even with a deliberately unscoped query")

    cur.execute("SET app.current_company_id = %s;", (str(company_b),))
    cur.execute("SELECT vehicle_no FROM vehicles;")
    rows_as_b = [r['vehicle_no'] for r in cur.fetchall()]
    print(f"  Querying as Company B (unscoped SELECT *): {rows_as_b}")
    if rows_as_b != ['OD14H4222']:
        _fail(f"Company B should see exactly its own 1 vehicle, got {rows_as_b}")
    _ok("Company B sees only its own vehicle, even with a deliberately unscoped query")

    # Same test on users, to confirm this isn't a fluke of one table's setup
    cur.execute("SET app.current_company_id = %s;", (str(company_a),))
    cur.execute("SELECT username FROM users;")
    users_as_a = cur.fetchall()
    if len(users_as_a) != 1:
        _fail(f"Company A should see exactly 1 user, got {len(users_as_a)}")
    _ok("Company A sees only its own user row")

    print("\n" + "=" * 70)
    print("ALL ISOLATION TESTS PASSED — schema_postgres.sql's RLS setup works as designed.")
    print("=" * 70)
    print("\nThis test database still has the test data in it — safe to leave (it's throwaway),")
    print("or re-run this script any time to reset to a clean slate.")

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
