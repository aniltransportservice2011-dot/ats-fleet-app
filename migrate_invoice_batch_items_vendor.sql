-- Migration: add vendor_id to invoice_batch_items.
--
-- Why: the Edit Invoice page's "Additional Line Items" add-item form needed a Vendor field
-- (so a batch-invoice charge/deduction can be tagged to a specific vendor, same as trips'
-- "Others" items already support via invoice_items.vendor_id), but invoice_batch_items had no
-- column to hold it.
--
-- Column type: vendor_id INTEGER REFERENCES vendors(id) -- nullable, no default, matching every
-- other optional FK column added this project (company_id's REFERENCES clause is documentary
-- only, same as here -- get_db() never sets PRAGMA foreign_keys=ON for the app itself).
--
-- Backfill: none. Every pre-existing invoice_batch_items row gets NULL vendor_id -- there's no
-- way to know retroactively which vendor (if any) an old line item was meant for, and NULL
-- honestly means "no vendor set," which is also a fully valid value going forward for items
-- that are genuinely not vendor-specific (e.g. a generic "Route deviation charge").
--
-- Idempotency: SQLite errors on ADD COLUMN if the column already exists -- this file is
-- intended to run exactly once per database (local fleet.db, then separately the live Render
-- /data/fleet.db), tested against a disposable copy first per this project's testing
-- discipline, and becomes a historical record afterward, same as migrate_audit_columns.sql.

PRAGMA foreign_keys = ON;

ALTER TABLE invoice_batch_items ADD COLUMN vendor_id INTEGER REFERENCES vendors(id);
