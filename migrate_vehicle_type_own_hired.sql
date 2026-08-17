-- Migration: collapse vehicles.type / trips.type from the old 3-way free-text field
-- (Line / Local / Market) to a strict 2-way own/hired split.
--
-- Why: "Line" and "Local" always meant the same thing to every piece of business logic in this
-- app -- a company-owned vehicle -- and only "Market" ever meant a hired-in vehicle. That's now
-- made explicit: the stored value is a stable lowercase CODE ('own' / 'hired'), never the display
-- word, matching the VEHICLE_TYPE_OWN / VEHICLE_TYPE_HIRED constants in app.py (see the comment
-- there) and this project's existing convention for rate_type (PER_MT/FIXED) and item_type
-- (charge/deduction) -- the code is what's stored and compared, the label is what's shown.
--
-- Scope: only vehicles.type and trips.type hold these values anywhere in this schema (confirmed
-- by inspecting every 'type' column across schema.sql -- no other table stores Line/Local/Market).
--
-- Data migration only -- no ALTER TABLE, no column type change, just UPDATE-ing existing string
-- values in place. Idempotent: re-running this is a safe no-op, since after the first run there
-- are no more 'Line'/'Local'/'Market' rows left to match.
--
-- Tested against a disposable copy first, per this project's testing discipline (see CLAUDE.md).

UPDATE vehicles SET type = 'own'   WHERE type IN ('Line', 'Local');
UPDATE vehicles SET type = 'hired' WHERE type = 'Market';

UPDATE trips    SET type = 'own'   WHERE type IN ('Line', 'Local');
UPDATE trips    SET type = 'hired' WHERE type = 'Market';
