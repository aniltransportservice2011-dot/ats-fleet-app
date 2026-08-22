-- Migration: add invoice_batch_charge_exclusions, so the per-trip "which charges/deductions to
-- include" choice made in Invoice Center at generate time survives re-viewing an invoice later.
--
-- Why this is needed at all: Generate Invoice creates a saved invoice_batches row, but
-- View/Regenerate PDF (from Generated Invoices) rebuilds the PDF from scratch off the trips'
-- live field values every time -- it has no memory of which of the auto-charge fields (Loading,
-- Unloading, Permit, Toll, Weighment, Driver Bata, GPS, Other Charges) or which Fuel/Driver
-- Advance deduction were unchecked when the invoice was first generated. Without a persisted
-- record, everything a user deliberately excluded would silently reappear the next time the same
-- invoice is opened or re-downloaded. One row here = one field, on one trip, excluded from one
-- specific generated invoice.
--
-- charge_key is a small fixed vocabulary matching _build_invoice_pdf's own field keys exactly
-- (loading/unloading/permit/toll/weighment/driver_bata/gps/other for charges,
-- fuel_deduction/driver_adv_deduction for the owner-invoice-only deduction toggles) plus
-- 'item:<invoice_items.id>' for an excluded "Others" line item, so one table covers both kinds
-- of exclusion without a second table.
--
-- No exclusion rows for a given (invoice_batch_id, trip_id) means "nothing was excluded for this
-- trip" -- the default, matching every invoice generated before this feature existed, so old
-- invoices keep rendering exactly as they always have with zero backfill needed.
--
-- Idempotent: SQLite errors on CREATE TABLE if it already exists -- this file is intended to run
-- exactly once per database (local fleet.db, then separately the live Render /data/fleet.db),
-- tested against a disposable copy first per this project's testing discipline.

CREATE TABLE invoice_batch_charge_exclusions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_batch_id  INTEGER NOT NULL REFERENCES invoice_batches(id),
    trip_id           INTEGER NOT NULL REFERENCES trips(id),
    charge_key        TEXT NOT NULL,
    company_id        INTEGER NOT NULL DEFAULT 1,
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT,
    UNIQUE(invoice_batch_id, trip_id, charge_key)
);

CREATE INDEX idx_invoice_batch_charge_exclusions_batch ON invoice_batch_charge_exclusions(invoice_batch_id);
