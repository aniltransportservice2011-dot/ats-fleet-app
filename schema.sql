CREATE TABLE vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_no TEXT UNIQUE NOT NULL,
    type TEXT
);

CREATE TABLE parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    contact TEXT,
    notes TEXT
);

CREATE TABLE vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    category TEXT,
    contact TEXT
);

CREATE TABLE trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    lr_number TEXT,
    vehicle_id INTEGER REFERENCES vehicles(id),
    party_id INTEGER REFERENCES parties(id),
    from_loc TEXT,
    to_loc TEXT,
    quantity REAL,
    rate REAL,
    rate_type TEXT,
    billed_amount REAL,
    fuel_amount REAL,
    fuel_vendor_id INTEGER REFERENCES vendors(id),
    driver_adv_amount REAL,
    driver_adv_vendor_id INTEGER REFERENCES vendors(id),
    party_advance REAL DEFAULT 0,
    payment_received REAL DEFAULT 0
);

CREATE TABLE maintenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    vehicle_id INTEGER REFERENCES vehicles(id),
    category TEXT,
    amount REAL,
    paid_amount REAL DEFAULT 0,
    vendor_id INTEGER REFERENCES vendors(id),
    notes TEXT
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    payment_type TEXT CHECK(payment_type IN ('received','paid')),
    amount REAL,
    party_id INTEGER REFERENCES parties(id),
    vendor_id INTEGER REFERENCES vendors(id),
    mode TEXT
);
