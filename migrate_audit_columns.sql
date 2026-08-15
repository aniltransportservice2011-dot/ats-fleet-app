-- Migration: add created_by / created_at / updated_by / updated_at audit columns to every
-- table except access_logs and sync_log.
--
-- Why: tracked down a stray vendor named "None" on the live deployment and had no way to
-- confirm who created it -- no table anywhere in this app records who created or last touched
-- a row. access_logs only logs logins, nothing else. This closes that gap for good.
--
-- Exemptions:
--   - access_logs already carries user_id + date, which *is* its created_by/created_at under
--     different names -- adding the generic columns would just duplicate that fact.
--   - sync_log is purely system-generated (written only by scheduler.py, no human actor ever)
--     -- these columns would be permanent NULL noise there.
--
-- Column types:
--   - created_by / updated_by: INTEGER REFERENCES users(id) -- same as company_id's REFERENCES
--     clause in migrate_company_id.sql, this is NOT enforced at runtime (get_db() never sets
--     PRAGMA foreign_keys=ON for the app itself) -- documentary only.
--   - created_at / updated_at: TEXT, matching every existing timestamp column in this app
--     ('%Y-%m-%d %H:%M:%S', written by app.py's inline datetime.datetime.now().strftime(...)
--     call-site style -- there is no shared timestamp helper in app.py to route through).
--
-- Backfill: deliberately none. Every pre-existing row gets NULL in all 4 new columns -- there
-- is no way to know retroactively who created/updated a historical row, and backfilling
-- created_at with today's migration timestamp would be actively misleading (it would make
-- years-old trips/payments/etc. look like they were created today). NULL honestly means
-- "unknown." App code (once swept) only ever populates these going forward. Writes triggered
-- by the background RC/Challan sync (scheduler.py, via the login-exempt internal_sync_tick
-- route) have no session/human actor either -- those also get NULL created_by/updated_by,
-- same reasoning.
--
-- Skip list, and why each already has some (but not all) of the 4 columns:
--   companies, invoice_batches, urea_transactions, toll_entries, salaries, advances,
--   attendance, users already have created_at -- only created_by/updated_by/updated_at added.
--   vehicle_compliance already has BOTH created_at and updated_at -- only created_by/updated_by
--   added.
--
-- Idempotency: SQLite errors on ADD COLUMN if the column already exists -- this file is
-- intended to run exactly once per database (local fleet.db, then separately the live Render
-- /data/fleet.db), tested against a disposable copy first per this project's testing
-- discipline, and becomes a historical record afterward, same as migrate_company_id.sql.

PRAGMA foreign_keys = ON;

-- created_by (all 30 non-exempt tables)
ALTER TABLE companies            ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE vehicles             ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE vehicle_compliance   ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE vehicle_challans     ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE insurance_policies   ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE batteries            ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE battery_checks       ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE tyre_stock           ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE parties              ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE vendors              ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE payments             ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE payment_allocations  ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE trips                ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE invoices             ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_items        ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batches      ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batch_trips  ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batch_items  ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE maintenance          ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE maintenance_items    ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE urea_transactions    ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE toll_entries         ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE employees            ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE salaries             ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE salary_items         ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE advances             ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE attendance           ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE overheads            ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE users                ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE settings             ADD COLUMN created_by INTEGER REFERENCES users(id);

-- created_at (21 tables that don't already have it)
ALTER TABLE vehicles             ADD COLUMN created_at TEXT;
ALTER TABLE vehicle_challans     ADD COLUMN created_at TEXT;
ALTER TABLE insurance_policies   ADD COLUMN created_at TEXT;
ALTER TABLE batteries            ADD COLUMN created_at TEXT;
ALTER TABLE battery_checks       ADD COLUMN created_at TEXT;
ALTER TABLE tyre_stock           ADD COLUMN created_at TEXT;
ALTER TABLE parties              ADD COLUMN created_at TEXT;
ALTER TABLE vendors              ADD COLUMN created_at TEXT;
ALTER TABLE payments             ADD COLUMN created_at TEXT;
ALTER TABLE payment_allocations  ADD COLUMN created_at TEXT;
ALTER TABLE trips                ADD COLUMN created_at TEXT;
ALTER TABLE invoices             ADD COLUMN created_at TEXT;
ALTER TABLE invoice_items        ADD COLUMN created_at TEXT;
ALTER TABLE invoice_batch_trips  ADD COLUMN created_at TEXT;
ALTER TABLE invoice_batch_items  ADD COLUMN created_at TEXT;
ALTER TABLE maintenance          ADD COLUMN created_at TEXT;
ALTER TABLE maintenance_items    ADD COLUMN created_at TEXT;
ALTER TABLE employees            ADD COLUMN created_at TEXT;
ALTER TABLE salary_items         ADD COLUMN created_at TEXT;
ALTER TABLE overheads            ADD COLUMN created_at TEXT;
ALTER TABLE settings             ADD COLUMN created_at TEXT;

-- updated_by (all 30 non-exempt tables)
ALTER TABLE companies            ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE vehicles             ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE vehicle_compliance   ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE vehicle_challans     ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE insurance_policies   ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE batteries            ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE battery_checks       ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE tyre_stock           ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE parties              ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE vendors              ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE payments             ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE payment_allocations  ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE trips                ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE invoices             ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_items        ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batches      ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batch_trips  ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE invoice_batch_items  ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE maintenance          ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE maintenance_items    ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE urea_transactions    ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE toll_entries         ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE employees            ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE salaries             ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE salary_items         ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE advances             ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE attendance           ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE overheads            ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE users                ADD COLUMN updated_by INTEGER REFERENCES users(id);
ALTER TABLE settings             ADD COLUMN updated_by INTEGER REFERENCES users(id);

-- updated_at (29 tables that don't already have it)
ALTER TABLE companies            ADD COLUMN updated_at TEXT;
ALTER TABLE vehicles             ADD COLUMN updated_at TEXT;
ALTER TABLE vehicle_challans     ADD COLUMN updated_at TEXT;
ALTER TABLE insurance_policies   ADD COLUMN updated_at TEXT;
ALTER TABLE batteries            ADD COLUMN updated_at TEXT;
ALTER TABLE battery_checks       ADD COLUMN updated_at TEXT;
ALTER TABLE tyre_stock           ADD COLUMN updated_at TEXT;
ALTER TABLE parties              ADD COLUMN updated_at TEXT;
ALTER TABLE vendors              ADD COLUMN updated_at TEXT;
ALTER TABLE payments             ADD COLUMN updated_at TEXT;
ALTER TABLE payment_allocations  ADD COLUMN updated_at TEXT;
ALTER TABLE trips                ADD COLUMN updated_at TEXT;
ALTER TABLE invoices             ADD COLUMN updated_at TEXT;
ALTER TABLE invoice_items        ADD COLUMN updated_at TEXT;
ALTER TABLE invoice_batches      ADD COLUMN updated_at TEXT;
ALTER TABLE invoice_batch_trips  ADD COLUMN updated_at TEXT;
ALTER TABLE invoice_batch_items  ADD COLUMN updated_at TEXT;
ALTER TABLE maintenance          ADD COLUMN updated_at TEXT;
ALTER TABLE maintenance_items    ADD COLUMN updated_at TEXT;
ALTER TABLE urea_transactions    ADD COLUMN updated_at TEXT;
ALTER TABLE toll_entries         ADD COLUMN updated_at TEXT;
ALTER TABLE employees            ADD COLUMN updated_at TEXT;
ALTER TABLE salaries             ADD COLUMN updated_at TEXT;
ALTER TABLE salary_items         ADD COLUMN updated_at TEXT;
ALTER TABLE advances             ADD COLUMN updated_at TEXT;
ALTER TABLE attendance           ADD COLUMN updated_at TEXT;
ALTER TABLE overheads            ADD COLUMN updated_at TEXT;
ALTER TABLE users                ADD COLUMN updated_at TEXT;
ALTER TABLE settings             ADD COLUMN updated_at TEXT;
