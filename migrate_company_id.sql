-- Migration: add company_id to every table, defaulted to one company (id=1).
--
-- Cheap prep for the future multi-tenancy retrofit (see the approved plan at
-- /Users/beenash/.claude/plans/whimsical-conjuring-parrot.md), run now while there's only one
-- company's data to tag rather than doing this backfill later against a live, multi-month,
-- revenue-generating dataset.
--
-- Nothing observable changes today:
--   - `companies` gets exactly one row, seeded from the current settings.company_name.
--   - Every other table gets `company_id INTEGER NOT NULL DEFAULT 1` (logically a reference to
--     companies.id — SQLite's ALTER TABLE ADD COLUMN refuses to combine an inline REFERENCES
--     clause with a non-null DEFAULT, confirmed by testing this against a disposable copy first,
--     so the relationship is enforced by convention/comment here rather than a formal foreign
--     key; this app's own get_db() never turns on PRAGMA foreign_keys anyway, so nothing was
--     actually being enforced by the existing REFERENCES clauses elsewhere in the schema
--     either). SQLite's ADD COLUMN with a DEFAULT backfills every existing row to 1 as part of
--     the same statement — no separate UPDATE needed. NOT NULL DEFAULT 1 also means any row the
--     (still-unmodified) app.py inserts going forward keeps working without any code change,
--     since SQLite fills the omitted column with its default automatically.
--   - The 5 UNIQUE constraints that will need to become composite (vehicles.vehicle_no,
--     parties.name, vendors.name, employees.name, users.username) and the settings table's
--     primary key (key TEXT PRIMARY KEY -> (company_id, key)) are deliberately NOT touched here
--     — that requires a full table rebuild in SQLite and only actually matters once a second
--     company exists. Deferred to the real Phase 1 retrofit.

PRAGMA foreign_keys = ON;

CREATE TABLE companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TEXT
);

INSERT INTO companies (id, name, created_at)
VALUES (1, (SELECT value FROM settings WHERE key='company_name'), datetime('now'));

ALTER TABLE vehicles            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE vehicle_compliance  ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE vehicle_challans    ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE insurance_policies  ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE batteries           ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE battery_checks      ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE tyre_stock          ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE parties             ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE vendors             ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE payments            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE payment_allocations ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE trips               ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE invoices            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE invoice_items       ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE invoice_batches     ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE invoice_batch_trips ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE invoice_batch_items ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE maintenance         ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE maintenance_items   ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE urea_transactions   ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE toll_entries        ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE employees           ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE salaries            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE salary_items        ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE advances            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE attendance          ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE overheads           ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users               ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE settings            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE access_logs         ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE sync_log            ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1;
