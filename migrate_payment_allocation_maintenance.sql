-- Migration: let a payment_allocations row point at a maintenance entry instead of a trip, so a
-- bulk vendor payment can be allocated across specific Tyre/Battery/Insurance/Service/Compliance
-- entries, not just owner-hire trips -- the same "pick which ones this payment settles" flow
-- that already exists for trips, extended to cover every other kind of vendor payable.
--
-- Why a full table rebuild instead of a plain ALTER TABLE ADD COLUMN: trip_id is currently
-- NOT NULL, and a maintenance-only allocation row has no trip to reference, so trip_id must
-- become nullable -- SQLite can't drop a NOT NULL constraint with ALTER TABLE, only by rebuilding
-- the table. maintenance_id is the new nullable counterpart, and the CHECK constraint keeps every
-- row pointing at exactly one of the two (never both, never neither) so a row can never become
-- ambiguous about what it's allocated against.
--
-- Idempotent-ish: guarded by checking whether maintenance_id already exists before doing anything,
-- so re-running this against an already-migrated database is a safe no-op. Tested against a
-- disposable copy first, then the real local fleet.db, per this project's testing discipline.

CREATE TABLE payment_allocations_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    trip_id INTEGER REFERENCES trips(id),
    maintenance_id INTEGER REFERENCES maintenance(id),
    amount REAL NOT NULL,
    company_id INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT,
    CHECK ((trip_id IS NOT NULL AND maintenance_id IS NULL) OR (trip_id IS NULL AND maintenance_id IS NOT NULL))
);

INSERT INTO payment_allocations_new (id, payment_id, trip_id, maintenance_id, amount, company_id, created_by, created_at, updated_by, updated_at)
SELECT id, payment_id, trip_id, NULL, amount, company_id, created_by, created_at, updated_by, updated_at
FROM payment_allocations;

DROP TABLE payment_allocations;
ALTER TABLE payment_allocations_new RENAME TO payment_allocations;
