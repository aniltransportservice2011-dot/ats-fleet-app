"""Shared helper for the test scripts: the app now checks, on every request, that the logged-in user
exists, is Active, and what access level they have — so a forged test session must point at a REAL user
row in the (disposable) database."""
import sqlite3


def ensure_test_user(db_path, user_id=900001):
    """Make sure the disposable test database has an Active, Full Access user; return its id."""
    c = sqlite3.connect(db_path)
    try:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            c.execute("INSERT INTO users (id, username, password_hash, role, is_admin, full_name, access_level, status, phone, created_at) "
                      "VALUES (?, 'test_runner', 'x', 'Administrator', 1, 'Test Runner', 'Full Access', 'Active', '9000000000', '2026-01-01')", (user_id,))
            c.commit()
        return user_id
    finally:
        c.close()
