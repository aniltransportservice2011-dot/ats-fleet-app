-- Migration: let a payment_allocations row point at an Expense (overheads) bill, so a payment made
-- to a vendor from the Ledger can settle that vendor's Pending expense bills (e.g. an electricity
-- bill entered as Pending with vendor "Utility Co") the same way it already settles owner-hire
-- trips (trip_id) and maintenance bills (maintenance_id).
--
-- Why a table rebuild: the existing CHECK says "exactly one of trip_id / maintenance_id" and SQLite
-- cannot alter a CHECK constraint in place. The rebuilt CHECK says "exactly one of trip_id /
-- maintenance_id / overhead_id", so a row can never be ambiguous about what it was applied to.
--
-- Run ONCE (it is not idempotent): confirm first with
--   sqlite3 fleet.db "SELECT COUNT(*) FROM pragma_table_info('payment_allocations') WHERE name='overhead_id'"   -- must print 0
-- Per this project's rules: test on a disposable copy, back up fleet.db first, and deploy this
-- database change BEFORE the application code that uses overhead_id.

PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE payment_allocations_new (
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
);

INSERT INTO payment_allocations_new (id, payment_id, trip_id, maintenance_id, overhead_id, amount, created_by, created_at, updated_by, updated_at, company_id)
SELECT id, payment_id, trip_id, maintenance_id, NULL, amount, created_by, created_at, updated_by, updated_at, company_id
FROM payment_allocations;

DROP TABLE payment_allocations;
ALTER TABLE payment_allocations_new RENAME TO payment_allocations;

COMMIT;
PRAGMA foreign_keys=ON;
