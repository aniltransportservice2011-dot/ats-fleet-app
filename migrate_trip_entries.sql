-- Dated fuel / driver-advance entries on a trip, and a marker for payments entered on a trip's own screen.
-- Purely additive: a new empty table and a new nullable column. The app also adds both itself on start-up (_ensure_billing_columns), so running this by
-- hand is optional; it is here so the change is on record next to the other migrations. Safe to run once.
ALTER TABLE payments ADD COLUMN entered_from TEXT;

CREATE TABLE IF NOT EXISTS trip_cost_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL REFERENCES trips(id),
    kind        TEXT NOT NULL CHECK(kind IN ('fuel','driver_adv')),
    date        TEXT,
    vendor_id   INTEGER REFERENCES vendors(id),
    amount      REAL NOT NULL,
    liters      REAL,
    ref         TEXT,
    company_id  INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT,
    updated_by  INTEGER REFERENCES users(id),
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_trip_cost_entries_trip ON trip_cost_entries(trip_id);
CREATE INDEX IF NOT EXISTS idx_trip_cost_entries_vendor ON trip_cost_entries(vendor_id);
