-- Fleet App — Authoritative Database Schema
--
-- Generated directly from `sqlite3 fleet.db ".schema"` against the live database and verified
-- to recreate it exactly (see the disposable-DB check this file was built with). This supersedes
-- the old schema.sql/schema_v2.sql, which together only documented 7 of the app's 31 tables and
-- had drifted out of date — most of the schema below was added over time through ad-hoc changes
-- with no script anywhere that reproduced it. This file is that script, kept current from here on.
--
-- Column order and formatting have been cleaned up for readability (the live database has extra
-- columns appended over time via ALTER TABLE, which read as one long trailing line); every
-- column name, type, default, and constraint below is unchanged from what's actually running.
--
-- Not included: `sqlite_sequence` — SQLite creates this automatically for any table using
-- AUTOINCREMENT; it must never be created by hand.
--
-- `company_id` on every table below (defaulting to 1) is cheap prep for a future multi-tenancy
-- retrofit — see /Users/beenash/.claude/plans/whimsical-conjuring-parrot.md and
-- migrate_company_id.sql, which is what actually added it to the live database. Today there is
-- only ever one company (row 1 in `companies`), so nothing observably changed when it was added.
-- Not yet done, deliberately deferred to that future retrofit: making the 5 currently-global
-- UNIQUE constraints (vehicles.vehicle_no, parties.name, vendors.name, employees.name,
-- users.username) composite with company_id, and restructuring settings' primary key from
-- `key` alone to `(company_id, key)`.

PRAGMA foreign_keys = ON;

-- ============================================================================
-- Companies (multi-tenancy prep — see the note on company_id above)
-- ============================================================================

CREATE TABLE companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TEXT,
    created_by INTEGER REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT
);

-- ============================================================================
-- Vehicles & Fleet Compliance
-- ============================================================================

CREATE TABLE vehicles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_no          TEXT UNIQUE NOT NULL,
    type                TEXT,
    registration_date   TEXT,
    capacity_mt         REAL,
    insurance_expiry    TEXT,
    fitness_expiry      TEXT,
    notes               TEXT,
    puc_valid_upto      TEXT,
    permit_valid_upto   TEXT,
    status              TEXT DEFAULT 'Active',
    body_type           TEXT,
    chassis_number      TEXT,
    engine_number       TEXT,
    rc_synced_data      TEXT,
    rc_last_synced      TEXT,
    challan_count       INTEGER DEFAULT 0,
    challan_amount      REAL DEFAULT 0,
    challan_last_synced TEXT,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT,
    updated_by          INTEGER REFERENCES users(id),
    updated_at          TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE vehicle_compliance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id        INTEGER NOT NULL REFERENCES vehicles(id),
    compliance_type   TEXT NOT NULL CHECK(compliance_type IN ('fitness','puc','permit')),
    document_number   TEXT,
    issuing_authority TEXT,
    permit_subtype    TEXT,
    valid_from        TEXT,
    valid_upto        TEXT,
    status_override   TEXT,
    source            TEXT DEFAULT 'manual',
    provider_name     TEXT,
    last_sync_time    TEXT,
    sync_status       TEXT DEFAULT 'Not Synced',
    remarks           TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    created_by        INTEGER REFERENCES users(id),
    updated_by        INTEGER REFERENCES users(id),
    company_id            INTEGER NOT NULL DEFAULT 1,
    UNIQUE(vehicle_id, compliance_type)
);
CREATE INDEX idx_vehicle_compliance_vehicle ON vehicle_compliance(vehicle_id);

CREATE TABLE vehicle_challans (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id            INTEGER NOT NULL,
    api_id                TEXT,
    challan_no            TEXT,
    challan_date_time     TEXT,
    challan_place         TEXT,
    challan_status        TEXT,
    fine_imposed          REAL,
    amount_of_fine_imposed REAL,
    department            TEXT,
    driver_name           TEXT,
    name_of_violator      TEXT,
    owner_name            TEXT,
    dl_no                 TEXT,
    document_impounded    TEXT,
    remark                TEXT,
    rto_distric_name      TEXT,
    state_code            TEXT,
    court_name            TEXT,
    court_address         TEXT,
    date_of_proceeding    TEXT,
    sent_to_court_on      TEXT,
    sent_to_reg_court     TEXT,
    sent_to_virtual_court TEXT,
    offence_details       TEXT,
    source_created_at     TEXT,
    source_updated_at     TEXT,
    last_synced           TEXT,
    created_by            INTEGER REFERENCES users(id),
    created_at            TEXT,
    updated_by            INTEGER REFERENCES users(id),
    updated_at            TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE insurance_policies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    insurance_type   TEXT,
    insurer_id       INTEGER REFERENCES vendors(id),
    policy_number    TEXT,
    start_date       TEXT,
    expiry_date      TEXT,
    premium_amount   REAL,
    idv              REAL,
    ncb_pct          REAL,
    gst_included     TEXT,
    agent_name       TEXT,
    agent_contact    TEXT,
    agent_email      TEXT,
    reminder_days    INTEGER,
    notes            TEXT,
    status_override  TEXT,
    policy_doc_path  TEXT,
    invoice_doc_path TEXT,
    rc_doc_path      TEXT,
    maintenance_id   INTEGER REFERENCES maintenance(id),
    created_by       INTEGER REFERENCES users(id),
    created_at       TEXT,
    updated_by       INTEGER REFERENCES users(id),
    updated_at       TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE batteries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_no         TEXT UNIQUE,
    brand              TEXT,
    model              TEXT,
    capacity_ah        REAL,
    battery_type       TEXT,
    voltage_rating     TEXT,
    serial_no          TEXT,
    vehicle_id         INTEGER REFERENCES vehicles(id),
    installed_location TEXT,
    install_date       TEXT,
    purchase_date      TEXT,
    purchase_price     REAL,
    vendor_id          INTEGER REFERENCES vendors(id),
    invoice_no         TEXT,
    warranty_months    INTEGER,
    maintenance_id     INTEGER REFERENCES maintenance(id),
    health_pct         REAL,
    voltage            REAL,
    temp_c             REAL,
    last_checked_date  TEXT,
    status_override    TEXT,
    notes              TEXT,
    created_by         INTEGER REFERENCES users(id),
    created_at         TEXT,
    updated_by         INTEGER REFERENCES users(id),
    updated_at         TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE battery_checks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id INTEGER REFERENCES batteries(id),
    date       TEXT,
    event      TEXT,
    health_pct REAL,
    voltage    REAL,
    temp_c     REAL,
    remarks    TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE tyre_stock (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_id       INTEGER REFERENCES maintenance(id),
    tyre_id              TEXT,
    brand                TEXT,
    tyre_type            TEXT,
    purchase_date        TEXT,
    purchase_cost        REAL,
    vendor_id            INTEGER REFERENCES vendors(id),
    invoice_no           TEXT,
    status               TEXT DEFAULT 'In Stock',
    notes                TEXT,
    installed_vehicle_id INTEGER REFERENCES vehicles(id),
    installed_position   TEXT,
    installed_date       TEXT,
    created_by           INTEGER REFERENCES users(id),
    created_at           TEXT,
    updated_by           INTEGER REFERENCES users(id),
    updated_at           TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

-- ============================================================================
-- Parties, Vendors & Ledger
-- ============================================================================

CREATE TABLE parties (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT UNIQUE NOT NULL,
    contact               TEXT,
    notes                 TEXT,
    address               TEXT,
    email                 TEXT,
    credit_limit          REAL,
    since_date            TEXT,
    opening_balance       REAL DEFAULT 0,
    opening_balance_date  TEXT,
    gstin                 TEXT,
    category              TEXT,
    status                TEXT DEFAULT 'Active',
    created_by            INTEGER REFERENCES users(id),
    created_at            TEXT,
    updated_by            INTEGER REFERENCES users(id),
    updated_at            TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE vendors (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT UNIQUE NOT NULL,
    category              TEXT,
    contact               TEXT,
    address               TEXT,
    email                 TEXT,
    credit_limit          REAL,
    since_date            TEXT,
    linked_party_id       INTEGER,
    opening_balance       REAL DEFAULT 0,
    opening_balance_date  TEXT,
    gstin                 TEXT,
    status                TEXT DEFAULT 'Active',
    created_by            INTEGER REFERENCES users(id),
    created_at            TEXT,
    updated_by            INTEGER REFERENCES users(id),
    updated_at            TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    payment_type    TEXT CHECK(payment_type IN ('received','paid')),
    amount          REAL,
    party_id        INTEGER REFERENCES parties(id),
    vendor_id       INTEGER REFERENCES vendors(id),
    mode            TEXT,
    reference_id    TEXT,
    remarks         TEXT,
    allocated_amount REAL DEFAULT 0,
    created_by       INTEGER REFERENCES users(id),
    created_at       TEXT,
    updated_by       INTEGER REFERENCES users(id),
    updated_at       TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE payment_allocations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    trip_id    INTEGER NOT NULL REFERENCES trips(id),
    amount     REAL NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

-- ============================================================================
-- Trips & Billing
-- ============================================================================

CREATE TABLE trips (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    date                  TEXT NOT NULL,
    lr_number             TEXT,
    vehicle_id            INTEGER REFERENCES vehicles(id),
    type                  TEXT,
    party_id              INTEGER REFERENCES parties(id),
    from_loc              TEXT,
    to_loc                TEXT,
    quantity              REAL,
    rate                  REAL,
    driver_name           TEXT,
    material              TEXT,
    rate_type             TEXT,
    billed_amount         REAL,
    driver_payment        REAL DEFAULT 0,
    detention_charges     REAL DEFAULT 0,
    gps_cost              REAL DEFAULT 0,
    loading_charge        REAL DEFAULT 0,
    unloading_charge      REAL DEFAULT 0,
    police_charges        REAL DEFAULT 0,
    sim_tracking          REAL DEFAULT 0,
    union_charges         REAL DEFAULT 0,
    weight_charges        REAL DEFAULT 0,
    other_charges         REAL DEFAULT 0,
    brokerage             REAL DEFAULT 0,
    builty_commission     REAL DEFAULT 0,
    late_fees             REAL DEFAULT 0,
    material_damage       REAL DEFAULT 0,
    shortage_amount       REAL DEFAULT 0,
    shortage_qty          REAL DEFAULT 0,
    tds                   REAL DEFAULT 0,
    other_deductions      REAL DEFAULT 0,
    fuel_amount           REAL DEFAULT 0,
    fuel_vendor_id        INTEGER REFERENCES vendors(id),
    driver_adv_amount     REAL DEFAULT 0,
    driver_adv_vendor_id  INTEGER REFERENCES vendors(id),
    party_advance         REAL DEFAULT 0,
    payment_received      REAL DEFAULT 0,
    owner_name            TEXT,
    fixed_rate_amount     REAL DEFAULT 0,
    owner_rate            REAL DEFAULT 0,
    owner_amount          REAL DEFAULT 0,
    paid_to_owner         REAL DEFAULT 0,
    pending_to_owner      REAL DEFAULT 0,
    agent_commission      REAL DEFAULT 0,
    builty_expense        REAL DEFAULT 0,
    conductor_expense     REAL DEFAULT 0,
    fine                  REAL DEFAULT 0,
    labour_charges        REAL DEFAULT 0,
    parking               REAL DEFAULT 0,
    puncture              REAL DEFAULT 0,
    toll                  REAL DEFAULT 0,
    urea                  REAL DEFAULT 0,
    loading_expense       REAL DEFAULT 0,
    unloading_expense     REAL DEFAULT 0,
    wear_tear             REAL DEFAULT 0,
    weighbridge_charges   REAL DEFAULT 0,
    other_expense         REAL DEFAULT 0,
    misc_vendor_id        INTEGER REFERENCES vendors(id),
    lr_received           TEXT,
    owner_vendor_id       INTEGER,
    permit_charges        REAL DEFAULT 0,
    end_date              TEXT,
    shortage_date         TEXT,
    end_time              TEXT,
    actual_km             REAL,
    shortage_unit         TEXT,
    shortage_remarks      TEXT,
    remarks               TEXT,
    is_empty              INTEGER DEFAULT 0,
    owner_rate_type       TEXT DEFAULT 'PER_MT',
    owner_fixed_amount    REAL DEFAULT 0,
    fuel_liters           TEXT,
    fuel_price            REAL DEFAULT 0,
    created_by            INTEGER REFERENCES users(id),
    created_at            TEXT,
    updated_by            INTEGER REFERENCES users(id),
    updated_at            TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id        INTEGER UNIQUE,
    invoice_number TEXT,
    due_date       TEXT,
    notes          TEXT,
    created_by     INTEGER REFERENCES users(id),
    created_at     TEXT,
    updated_by     INTEGER REFERENCES users(id),
    updated_at     TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE invoice_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER,
    description TEXT,
    amount      REAL,
    item_type   TEXT CHECK(item_type IN ('charge','deduction')),
    vendor_id   INTEGER REFERENCES vendors(id),
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT,
    updated_by  INTEGER REFERENCES users(id),
    updated_at  TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE invoice_batches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number   TEXT,
    invoice_type     TEXT CHECK(invoice_type IN ('party','vehicle_owner','tax','bill')),
    party_id         INTEGER,
    vendor_id        INTEGER,
    invoice_date     TEXT,
    due_date         TEXT,
    payment_terms    TEXT,
    place_of_supply  TEXT,
    remarks          TEXT,
    gst_rate         REAL DEFAULT 18,
    tds_rate         REAL DEFAULT 1,
    loading_charges  REAL DEFAULT 0,
    other_charges    REAL DEFAULT 0,
    status           TEXT DEFAULT 'draft',
    created_at       TEXT,
    payment_status   TEXT DEFAULT "PENDING",
    created_by       INTEGER REFERENCES users(id),
    updated_by       INTEGER REFERENCES users(id),
    updated_at       TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE invoice_batch_trips (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_batch_id  INTEGER,
    trip_id           INTEGER,
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT,
    updated_by        INTEGER REFERENCES users(id),
    updated_at        TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE invoice_batch_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_batch_id  INTEGER,
    description       TEXT,
    amount            REAL,
    item_type         TEXT CHECK(item_type IN ('charge','deduction')),
    vendor_id         INTEGER REFERENCES vendors(id),
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT,
    updated_by        INTEGER REFERENCES users(id),
    updated_at        TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

-- ============================================================================
-- Maintenance
-- ============================================================================

CREATE TABLE maintenance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT NOT NULL,
    vehicle_id        INTEGER REFERENCES vehicles(id),
    category          TEXT,
    amount            REAL,
    paid_amount       REAL DEFAULT 0,
    vendor_id         INTEGER REFERENCES vendors(id),
    notes             TEXT,
    km_reading        REAL,
    service_type      TEXT,
    next_due_km       REAL,
    next_service_date TEXT,
    invoice_no        TEXT,
    invoice_date      TEXT,
    status            TEXT DEFAULT 'Completed',
    checklist_done    TEXT,
    tyre_action       TEXT,
    tyre_id           TEXT,
    tyre_brand        TEXT,
    tyre_position     TEXT,
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT,
    updated_by        INTEGER REFERENCES users(id),
    updated_at        TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE maintenance_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_id INTEGER REFERENCES maintenance(id),
    item_name      TEXT,
    category       TEXT,
    qty            REAL,
    unit           TEXT,
    rate           REAL,
    amount         REAL,
    created_by     INTEGER REFERENCES users(id),
    created_at     TEXT,
    updated_by     INTEGER REFERENCES users(id),
    updated_at     TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE urea_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    txn_type        TEXT NOT NULL CHECK(txn_type IN ('stock_in','stock_out')),
    source          TEXT NOT NULL DEFAULT 'stock' CHECK(source IN ('stock','direct')),
    batch_no        TEXT,
    supplier_id     INTEGER REFERENCES vendors(id),
    invoice_no      TEXT,
    vehicle_id      INTEGER REFERENCES vehicles(id),
    quantity_l      REAL NOT NULL,
    unit_price      REAL DEFAULT 0,
    total_value     REAL DEFAULT 0,
    balance_after_l REAL,
    location        TEXT,
    odometer_km     REAL,
    notes           TEXT,
    maintenance_id  INTEGER REFERENCES maintenance(id),
    created_at      TEXT,
    created_by      INTEGER REFERENCES users(id),
    updated_by      INTEGER REFERENCES users(id),
    updated_at      TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_urea_txn_date ON urea_transactions(date);

CREATE TABLE toll_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    time          TEXT,
    vehicle_id    INTEGER REFERENCES vehicles(id),
    trip_id       INTEGER REFERENCES trips(id),
    toll_plaza    TEXT NOT NULL,
    highway       TEXT,
    state         TEXT,
    amount        REAL NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('fastag','manual')),
    payment_mode  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('synced','approved','pending','rejected')),
    reference_no  TEXT,
    receipt_path  TEXT,
    notes         TEXT,
    maintenance_id INTEGER REFERENCES maintenance(id),
    created_at    TEXT,
    created_by    INTEGER REFERENCES users(id),
    updated_by    INTEGER REFERENCES users(id),
    updated_at    TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_toll_date ON toll_entries(date);
CREATE INDEX idx_toll_vehicle ON toll_entries(vehicle_id);

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

-- ============================================================================
-- People — Employees, Salaries, Attendance, Advances
-- ============================================================================

CREATE TABLE employees (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT UNIQUE NOT NULL,
    type                 TEXT CHECK(type IN ('Staff','Driver')),
    opening_balance      REAL DEFAULT 0,
    opening_balance_date TEXT,
    employee_code        TEXT,
    role                 TEXT,
    mobile               TEXT,
    email                TEXT,
    address              TEXT,
    joining_date         TEXT,
    date_of_birth        TEXT,
    bank_account         TEXT,
    ifsc_code            TEXT,
    upi_id               TEXT,
    emergency_contact    TEXT,
    aadhaar              TEXT,
    pan                  TEXT,
    driving_license      TEXT,
    status               TEXT DEFAULT 'Active',
    basic_salary         REAL DEFAULT 0,
    created_by           INTEGER REFERENCES users(id),
    created_at           TEXT,
    updated_by           INTEGER REFERENCES users(id),
    updated_at           TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE salaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    employee         TEXT NOT NULL,
    month            TEXT NOT NULL,
    amount           REAL,
    date             TEXT,
    created_at       TEXT,
    employee_id      INTEGER REFERENCES employees(id),
    month_key        TEXT,
    basic_salary     REAL,
    gross_salary     REAL,
    total_deductions REAL,
    advance_recovery REAL DEFAULT 0,
    net_salary       REAL,
    payment_status   TEXT DEFAULT 'pending',
    payment_date     TEXT,
    payment_mode     TEXT,
    transaction_id   TEXT,
    paid_by          TEXT,
    remarks          TEXT,
    created_by       INTEGER REFERENCES users(id),
    updated_by       INTEGER REFERENCES users(id),
    updated_at       TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_salaries_month_key ON salaries(month_key);

CREATE TABLE salary_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    salary_id   INTEGER NOT NULL REFERENCES salaries(id),
    item_type   TEXT NOT NULL CHECK(item_type IN ('allowance','deduction')),
    description TEXT NOT NULL,
    amount      REAL NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT,
    updated_by  INTEGER REFERENCES users(id),
    updated_at  TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE advances (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    employee   TEXT NOT NULL,
    date       TEXT,
    amount     REAL,
    type       TEXT CHECK(type IN ('given','repaid')),
    notes      TEXT,
    created_at TEXT,
    created_by INTEGER REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    date        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('Present','Absent','Leave','Half Day')),
    in_time     TEXT,
    out_time    TEXT,
    remarks     TEXT,
    marked_by   TEXT,
    created_at  TEXT,
    created_by  INTEGER REFERENCES users(id),
    updated_by  INTEGER REFERENCES users(id),
    updated_at  TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1,
    UNIQUE(employee_id, date)
);
CREATE INDEX idx_attendance_date ON attendance(date);
CREATE INDEX idx_attendance_employee ON attendance(employee_id);

-- ============================================================================
-- Overheads
-- ============================================================================

CREATE TABLE overheads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,
    category            TEXT,
    amount              REAL,
    notes               TEXT,
    payment_mode        TEXT,
    receipt_number      TEXT,
    vendor              TEXT,
    description         TEXT,
    status              TEXT DEFAULT 'Paid',
    is_recurring        INTEGER DEFAULT 0,
    recurring_frequency TEXT,
    due_date            TEXT,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT,
    updated_by          INTEGER REFERENCES users(id),
    updated_at          TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

-- ============================================================================
-- Auth, Settings & Operational Logs
-- ============================================================================

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'staff',
    is_admin      INTEGER DEFAULT 0,
    created_at    TEXT,
    phone         TEXT,
    full_name     TEXT,
    email         TEXT,
    access_level  TEXT DEFAULT 'Read Only',
    module_access TEXT,
    status        TEXT DEFAULT 'Active',
    last_login    TEXT,
    created_by    INTEGER REFERENCES users(id),
    updated_by    INTEGER REFERENCES users(id),
    updated_at    TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE access_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    event   TEXT,
    date    TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

-- Singleton key-value settings — one global row per key today (company profile, banking,
-- invoice numbering/prefs, tax rates, Twilio SMS-OTP credentials, RC-lookup API key, sync
-- intervals — 42 distinct keys as of this writing). Flagged in the multi-tenancy plan for a
-- structural redesign to a (company_id, key) composite key once this app supports more than
-- one company.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE sync_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    synced_count  INTEGER DEFAULT 0,
    failed_count  INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    note          TEXT,
    company_id            INTEGER NOT NULL DEFAULT 1
);
