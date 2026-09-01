-- Adds trips.guarantee_qty — the trip's Minimum Guarantee Weight (MGW). When a PER_MT trip's
-- actual quantity comes in under this, the party is billed as if guarantee_qty was moved instead
-- (see _trip_freight() in app.py, the single formula every freight computation in the app now
-- calls). DEFAULT 0 means "no guarantee" for every trip that already exists — max(quantity, 0)
-- is always just quantity, so this is a strict no-op for all historical data; billed_amount for
-- existing trips is completely unaffected by running this migration.
ALTER TABLE trips ADD COLUMN guarantee_qty REAL DEFAULT 0;
