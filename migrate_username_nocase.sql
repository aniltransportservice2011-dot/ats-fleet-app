-- Migration: make users.username case-insensitive (already applied to the live database — this
-- is a historical record, not something to re-run).
--
-- Makes users.username uniqueness (and every plain WHERE username=? lookup — login, signup,
-- add_user's duplicate check) case-insensitive: "Abinash", "abinash", "ABINASH" are now the same
-- username for both the uniqueness check and for matching at login, on the website and in the
-- app alike, since both read/write this same column/constraint. SQLite can't ALTER a column's
-- COLLATE in place, so this rebuilds the table (standard SQLite pattern) preserving every column,
-- every row, and the AUTOINCREMENT counter. Verified safe against the real data first — no two
-- existing usernames collided under case-insensitive comparison (SELECT ... GROUP BY
-- LOWER(username) HAVING COUNT(*) > 1 returned zero rows) before this was applied.
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

CREATE TABLE users_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'staff',
    is_admin INTEGER DEFAULT 0,
    created_at TEXT,
    phone TEXT,
    full_name TEXT,
    email TEXT,
    access_level TEXT DEFAULT 'Read Only',
    module_access TEXT,
    status TEXT DEFAULT 'Active',
    last_login TEXT,
    company_id INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT
);

INSERT INTO users_new (id, username, password_hash, role, is_admin, created_at, phone, full_name,
                        email, access_level, module_access, status, last_login, company_id,
                        created_by, updated_by, updated_at)
SELECT id, username, password_hash, role, is_admin, created_at, phone, full_name,
       email, access_level, module_access, status, last_login, company_id,
       created_by, updated_by, updated_at
FROM users;

DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

COMMIT;
PRAGMA foreign_keys=ON;
