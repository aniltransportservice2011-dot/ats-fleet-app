-- Migration: add compliance_expenses table for the new Maintenance > Compliance Expenses tab.
--
-- Why a dedicated table instead of just filtering the existing maintenance table by category:
-- maintenance.category already holds loose historical free-text values (some old rows literally
-- say 'Insurance' or 'Fitness', entered through the old generic Add Maintenance form, others say
-- near-miss variants like 'Pollution Certificate' or 'Loan'). Reusing that column directly for
-- this new structured feature would either silently surface some old unrelated rows (an exact
-- string collision) while missing others (a near-miss spelling) -- a confusing, half-backfilled
-- view. This table instead mirrors the existing Toll Management pattern (toll_entries): every
-- row logged through the new Add Expense flow ALSO gets a mirrored row inserted into the shared
-- maintenance table (category = compliance_type, linked back via maintenance_id) purely so it
-- still flows into the Maintenance Overview's own-fleet cost rollup, exactly like Toll already
-- does -- but the new tab's own list/filters/charts query this table, not maintenance directly,
-- so it never mixes with old freeform data.
--
-- compliance_type is a small fixed set (Insurance / PUC / Permit / Fitness / EMI & Loans /
-- Other), matching the same "own fleet only" scope Toll/Urea/compliance_service.py already use --
-- a hired vehicle's insurance or loan EMI isn't this company's expense to track.
--
-- Idempotent: SQLite errors on CREATE TABLE if it already exists -- this file is intended to run
-- exactly once per database (local fleet.db, then separately the live Render /data/fleet.db),
-- tested against a disposable copy first per this project's testing discipline.

CREATE TABLE compliance_expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    vehicle_id      INTEGER REFERENCES vehicles(id),
    compliance_type TEXT NOT NULL,
    description     TEXT,
    vendor_id       INTEGER REFERENCES vendors(id),
    amount          REAL NOT NULL DEFAULT 0,
    payment_mode    TEXT,
    maintenance_id  INTEGER REFERENCES maintenance(id),
    notes           TEXT,
    created_at      TEXT,
    created_by      INTEGER REFERENCES users(id),
    updated_by      INTEGER REFERENCES users(id),
    updated_at      TEXT,
    company_id      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_compliance_expenses_date ON compliance_expenses(date);
CREATE INDEX idx_compliance_expenses_vehicle ON compliance_expenses(vehicle_id);
