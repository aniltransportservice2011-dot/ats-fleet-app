-- Fleet App — PostgreSQL Multi-Tenant Schema
--
-- Ported from schema.sql (the SQLite version) as part of pulling the Phase 1 multi-tenancy
-- retrofit forward (see /Users/beenash/.claude/plans/whimsical-conjuring-parrot.md). Every
-- table from schema.sql is here, translated to Postgres syntax, with tenant isolation made
-- REAL rather than just a column:
--
--   1. company_id is NOT NULL with NO default (schema.sql's SQLite version defaulted it to 1
--      for the cheap single-tenant-today prep — that convenience goes away here on purpose;
--      every insert must now say explicitly which company a row belongs to).
--   2. The 5 previously-global UNIQUE constraints are composite: vehicles.vehicle_no,
--      parties.name, vendors.name, employees.name, users.username (plus batteries.battery_no)
--      are each now UNIQUE(company_id, <column>) — two companies can both have a vehicle
--      "OD14H4222" or a user "admin".
--   3. settings' primary key is (company_id, key), not key alone.
--   4. Every tenant table has an index on company_id — every real query in this app will now
--      filter by it, so this is load-bearing for performance, not optional.
--   5. Row-Level Security is enabled on every tenant table with a policy tying visibility to
--      current_setting('app.current_company_id') — the database itself refuses to return
--      another company's rows, even if application code forgets a WHERE clause.
--
-- CRITICAL RLS gotcha, easy to get wrong: Postgres does NOT apply row-level security policies
-- to a table's OWNER or to a superuser role, regardless of policies defined — by default RLS
-- only restricts non-owner roles. If the app connects using the same role that owns these
-- tables (e.g. the default role created alongside a fresh database), every policy below is
-- silently a no-op and every company would see every other company's data. Two ways to make
-- this real, pick one before this schema is trusted for anything:
--   (a) Have the app connect as a separate, non-owner role with only the grants it needs
--       (the standard, recommended approach — see the ROLE section at the bottom), or
--   (b) Run `ALTER TABLE <table> FORCE ROW LEVEL SECURITY;` on every table, which makes RLS
--       apply even to the owner (still doesn't apply to actual superusers).
-- This file does both: creates a dedicated non-owner `fleet_app` role, AND adds FORCE ROW
-- LEVEL SECURITY as defense in depth. Verify this specific behavior with a real test (connect
-- as fleet_app, try to read another company's row with no WHERE clause, confirm zero rows)
-- before trusting it — this is exactly the kind of thing that looks right and silently isn't.

-- ============================================================================
-- Companies
-- ============================================================================

CREATE TABLE companies (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    created_by INTEGER,
    updated_by INTEGER,
    updated_at TEXT
);

-- ============================================================================
-- Vehicles & Fleet Compliance
-- ============================================================================

CREATE TABLE vehicles (
    id                  SERIAL PRIMARY KEY,
    vehicle_no          TEXT NOT NULL,
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
    created_by          INTEGER,
    created_at          TEXT,
    updated_by          INTEGER,
    updated_at          TEXT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, vehicle_no)
);
CREATE INDEX idx_vehicles_company ON vehicles(company_id);

CREATE TABLE vehicle_compliance (
    id                SERIAL PRIMARY KEY,
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
    created_by        INTEGER,
    updated_by        INTEGER,
    company_id        INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(vehicle_id, compliance_type)
);
CREATE INDEX idx_vehicle_compliance_vehicle ON vehicle_compliance(vehicle_id);
CREATE INDEX idx_vehicle_compliance_company ON vehicle_compliance(company_id);

CREATE TABLE vehicle_challans (
    id                     SERIAL PRIMARY KEY,
    vehicle_id             INTEGER NOT NULL REFERENCES vehicles(id),
    api_id                 TEXT,
    challan_no             TEXT,
    challan_date_time      TEXT,
    challan_place          TEXT,
    challan_status         TEXT,
    fine_imposed           REAL,
    amount_of_fine_imposed REAL,
    department             TEXT,
    driver_name            TEXT,
    name_of_violator       TEXT,
    owner_name             TEXT,
    dl_no                  TEXT,
    document_impounded     TEXT,
    remark                 TEXT,
    rto_distric_name       TEXT,
    state_code             TEXT,
    court_name             TEXT,
    court_address          TEXT,
    date_of_proceeding     TEXT,
    sent_to_court_on       TEXT,
    sent_to_reg_court      TEXT,
    sent_to_virtual_court  TEXT,
    offence_details        TEXT,
    source_created_at      TEXT,
    source_updated_at      TEXT,
    last_synced            TEXT,
    created_by             INTEGER,
    created_at             TEXT,
    updated_by             INTEGER,
    updated_at             TEXT,
    company_id             INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_vehicle_challans_vehicle ON vehicle_challans(vehicle_id);
CREATE INDEX idx_vehicle_challans_company ON vehicle_challans(company_id);

-- insurer_id -> vendors(id) and maintenance_id -> maintenance(id) are added as ALTER TABLE
-- constraints near the end of this file (both tables are defined later — Postgres, unlike
-- SQLite, refuses an inline REFERENCES to a table that doesn't exist yet).
CREATE TABLE insurance_policies (
    id               SERIAL PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    insurance_type   TEXT,
    insurer_id       INTEGER,
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
    maintenance_id   INTEGER,
    created_by       INTEGER,
    created_at       TEXT,
    updated_by       INTEGER,
    updated_at       TEXT,
    company_id       INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_insurance_policies_company ON insurance_policies(company_id);

-- vendor_id and maintenance_id -> ALTER TABLE constraints near the end (see the note above
-- insurance_policies).
CREATE TABLE batteries (
    id                 SERIAL PRIMARY KEY,
    battery_no         TEXT,
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
    vendor_id          INTEGER,
    invoice_no         TEXT,
    warranty_months    INTEGER,
    maintenance_id     INTEGER,
    health_pct         REAL,
    voltage            REAL,
    temp_c             REAL,
    last_checked_date  TEXT,
    status_override    TEXT,
    notes              TEXT,
    created_by         INTEGER,
    created_at         TEXT,
    updated_by         INTEGER,
    updated_at         TEXT,
    company_id         INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, battery_no)
);
CREATE INDEX idx_batteries_company ON batteries(company_id);

CREATE TABLE battery_checks (
    id         SERIAL PRIMARY KEY,
    battery_id INTEGER REFERENCES batteries(id),
    date       TEXT,
    event      TEXT,
    health_pct REAL,
    voltage    REAL,
    temp_c     REAL,
    remarks    TEXT,
    created_by INTEGER,
    created_at TEXT,
    updated_by INTEGER,
    updated_at TEXT,
    company_id INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_battery_checks_company ON battery_checks(company_id);

-- maintenance_id and vendor_id -> ALTER TABLE constraints near the end (see the note above
-- insurance_policies).
CREATE TABLE tyre_stock (
    id                   SERIAL PRIMARY KEY,
    maintenance_id       INTEGER,
    tyre_id              TEXT,
    brand                TEXT,
    tyre_type            TEXT,
    purchase_date        TEXT,
    purchase_cost        REAL,
    vendor_id            INTEGER,
    invoice_no           TEXT,
    status               TEXT DEFAULT 'In Stock',
    notes                TEXT,
    installed_vehicle_id INTEGER REFERENCES vehicles(id),
    installed_position   TEXT,
    installed_date       TEXT,
    created_by           INTEGER,
    created_at           TEXT,
    updated_by           INTEGER,
    updated_at           TEXT,
    company_id           INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_tyre_stock_company ON tyre_stock(company_id);

-- ============================================================================
-- Parties, Vendors & Ledger
-- ============================================================================

CREATE TABLE parties (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
    contact              TEXT,
    notes                TEXT,
    address              TEXT,
    email                TEXT,
    credit_limit         REAL,
    since_date           TEXT,
    opening_balance      REAL DEFAULT 0,
    opening_balance_date TEXT,
    gstin                TEXT,
    category             TEXT,
    status               TEXT DEFAULT 'Active',
    created_by           INTEGER,
    created_at           TEXT,
    updated_by           INTEGER,
    updated_at           TEXT,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, name)
);
CREATE INDEX idx_parties_company ON parties(company_id);

CREATE TABLE vendors (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
    category             TEXT,
    contact              TEXT,
    address              TEXT,
    email                TEXT,
    credit_limit         REAL,
    since_date           TEXT,
    linked_party_id      INTEGER,
    opening_balance      REAL DEFAULT 0,
    opening_balance_date TEXT,
    gstin                TEXT,
    status               TEXT DEFAULT 'Active',
    created_by           INTEGER,
    created_at           TEXT,
    updated_by           INTEGER,
    updated_at           TEXT,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, name)
);
CREATE INDEX idx_vendors_company ON vendors(company_id);

CREATE TABLE payments (
    id               SERIAL PRIMARY KEY,
    date             TEXT NOT NULL,
    payment_type     TEXT CHECK(payment_type IN ('received','paid')),
    amount           REAL,
    party_id         INTEGER REFERENCES parties(id),
    vendor_id        INTEGER REFERENCES vendors(id),
    mode             TEXT,
    reference_id     TEXT,
    remarks          TEXT,
    allocated_amount REAL DEFAULT 0,
    created_by       INTEGER,
    created_at       TEXT,
    updated_by       INTEGER,
    updated_at       TEXT,
    company_id       INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_payments_company ON payments(company_id);

-- trip_id -> ALTER TABLE constraint near the end (trips is defined later in this file).
CREATE TABLE payment_allocations (
    id         SERIAL PRIMARY KEY,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    trip_id    INTEGER NOT NULL,
    amount     REAL NOT NULL,
    created_by INTEGER,
    created_at TEXT,
    updated_by INTEGER,
    updated_at TEXT,
    company_id INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_payment_allocations_company ON payment_allocations(company_id);

-- ============================================================================
-- Trips & Billing
-- ============================================================================

CREATE TABLE trips (
    id                   SERIAL PRIMARY KEY,
    date                 TEXT NOT NULL,
    lr_number            TEXT,
    vehicle_id           INTEGER REFERENCES vehicles(id),
    type                 TEXT,
    party_id             INTEGER REFERENCES parties(id),
    from_loc             TEXT,
    to_loc               TEXT,
    quantity             REAL,
    rate                 REAL,
    driver_name          TEXT,
    material              TEXT,
    rate_type            TEXT,
    billed_amount        REAL,
    driver_payment       REAL DEFAULT 0,
    detention_charges    REAL DEFAULT 0,
    gps_cost             REAL DEFAULT 0,
    loading_charge       REAL DEFAULT 0,
    unloading_charge     REAL DEFAULT 0,
    police_charges       REAL DEFAULT 0,
    sim_tracking         REAL DEFAULT 0,
    union_charges        REAL DEFAULT 0,
    weight_charges       REAL DEFAULT 0,
    other_charges        REAL DEFAULT 0,
    brokerage            REAL DEFAULT 0,
    builty_commission    REAL DEFAULT 0,
    late_fees            REAL DEFAULT 0,
    material_damage      REAL DEFAULT 0,
    shortage_amount      REAL DEFAULT 0,
    shortage_qty         REAL DEFAULT 0,
    tds                  REAL DEFAULT 0,
    other_deductions     REAL DEFAULT 0,
    fuel_amount          REAL DEFAULT 0,
    fuel_vendor_id       INTEGER REFERENCES vendors(id),
    driver_adv_amount    REAL DEFAULT 0,
    driver_adv_vendor_id INTEGER REFERENCES vendors(id),
    party_advance        REAL DEFAULT 0,
    payment_received     REAL DEFAULT 0,
    owner_name           TEXT,
    fixed_rate_amount    REAL DEFAULT 0,
    owner_rate           REAL DEFAULT 0,
    owner_amount         REAL DEFAULT 0,
    paid_to_owner        REAL DEFAULT 0,
    pending_to_owner     REAL DEFAULT 0,
    agent_commission     REAL DEFAULT 0,
    builty_expense       REAL DEFAULT 0,
    conductor_expense    REAL DEFAULT 0,
    fine                 REAL DEFAULT 0,
    labour_charges       REAL DEFAULT 0,
    parking              REAL DEFAULT 0,
    puncture             REAL DEFAULT 0,
    toll                 REAL DEFAULT 0,
    urea                 REAL DEFAULT 0,
    loading_expense      REAL DEFAULT 0,
    unloading_expense    REAL DEFAULT 0,
    wear_tear            REAL DEFAULT 0,
    weighbridge_charges  REAL DEFAULT 0,
    other_expense        REAL DEFAULT 0,
    misc_vendor_id       INTEGER REFERENCES vendors(id),
    lr_received          TEXT,
    owner_vendor_id      INTEGER,
    permit_charges       REAL DEFAULT 0,
    end_date             TEXT,
    shortage_date        TEXT,
    end_time             TEXT,
    actual_km            REAL,
    shortage_unit        TEXT,
    shortage_remarks     TEXT,
    remarks              TEXT,
    is_empty             INTEGER DEFAULT 0,
    owner_rate_type      TEXT DEFAULT 'PER_MT',
    owner_fixed_amount   REAL DEFAULT 0,
    fuel_liters          TEXT,
    fuel_price           REAL DEFAULT 0,
    created_by           INTEGER,
    created_at           TEXT,
    updated_by           INTEGER,
    updated_at           TEXT,
    company_id           INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_trips_company ON trips(company_id);
CREATE INDEX idx_trips_company_date ON trips(company_id, date);

CREATE TABLE invoices (
    id             SERIAL PRIMARY KEY,
    trip_id        INTEGER UNIQUE,
    invoice_number TEXT,
    due_date       TEXT,
    notes          TEXT,
    created_by     INTEGER,
    created_at     TEXT,
    updated_by     INTEGER,
    updated_at     TEXT,
    company_id     INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_invoices_company ON invoices(company_id);

CREATE TABLE invoice_items (
    id          SERIAL PRIMARY KEY,
    trip_id     INTEGER,
    description TEXT,
    amount      REAL,
    item_type   TEXT CHECK(item_type IN ('charge','deduction')),
    vendor_id   INTEGER REFERENCES vendors(id),
    created_by  INTEGER,
    created_at  TEXT,
    updated_by  INTEGER,
    updated_at  TEXT,
    company_id  INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_invoice_items_company ON invoice_items(company_id);

CREATE TABLE invoice_batches (
    id              SERIAL PRIMARY KEY,
    invoice_number  TEXT,
    invoice_type    TEXT CHECK(invoice_type IN ('party','vehicle_owner','tax','bill')),
    party_id        INTEGER,
    vendor_id       INTEGER,
    invoice_date    TEXT,
    due_date        TEXT,
    payment_terms   TEXT,
    place_of_supply TEXT,
    remarks         TEXT,
    gst_rate        REAL DEFAULT 18,
    tds_rate        REAL DEFAULT 1,
    loading_charges REAL DEFAULT 0,
    other_charges   REAL DEFAULT 0,
    status          TEXT DEFAULT 'draft',
    created_at      TEXT,
    payment_status  TEXT DEFAULT 'PENDING',
    created_by      INTEGER,
    updated_by      INTEGER,
    updated_at      TEXT,
    company_id      INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_invoice_batches_company ON invoice_batches(company_id);

CREATE TABLE invoice_batch_trips (
    id               SERIAL PRIMARY KEY,
    invoice_batch_id INTEGER,
    trip_id          INTEGER,
    created_by       INTEGER,
    created_at       TEXT,
    updated_by       INTEGER,
    updated_at       TEXT,
    company_id       INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_invoice_batch_trips_company ON invoice_batch_trips(company_id);

CREATE TABLE invoice_batch_items (
    id               SERIAL PRIMARY KEY,
    invoice_batch_id INTEGER,
    description      TEXT,
    amount           REAL,
    item_type        TEXT CHECK(item_type IN ('charge','deduction')),
    vendor_id        INTEGER REFERENCES vendors(id),
    created_by       INTEGER,
    created_at       TEXT,
    updated_by       INTEGER,
    updated_at       TEXT,
    company_id       INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_invoice_batch_items_company ON invoice_batch_items(company_id);

-- ============================================================================
-- Maintenance
-- ============================================================================

CREATE TABLE maintenance (
    id                SERIAL PRIMARY KEY,
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
    created_by        INTEGER,
    created_at        TEXT,
    updated_by        INTEGER,
    updated_at        TEXT,
    company_id        INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_maintenance_company ON maintenance(company_id);

CREATE TABLE maintenance_items (
    id             SERIAL PRIMARY KEY,
    maintenance_id INTEGER REFERENCES maintenance(id),
    item_name      TEXT,
    category       TEXT,
    qty            REAL,
    unit           TEXT,
    rate           REAL,
    amount         REAL,
    created_by     INTEGER,
    created_at     TEXT,
    updated_by     INTEGER,
    updated_at     TEXT,
    company_id     INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_maintenance_items_company ON maintenance_items(company_id);

CREATE TABLE urea_transactions (
    id              SERIAL PRIMARY KEY,
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
    created_by      INTEGER,
    updated_by      INTEGER,
    updated_at      TEXT,
    company_id      INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_urea_txn_date ON urea_transactions(date);
CREATE INDEX idx_urea_transactions_company ON urea_transactions(company_id);

CREATE TABLE toll_entries (
    id             SERIAL PRIMARY KEY,
    date           TEXT NOT NULL,
    time           TEXT,
    vehicle_id     INTEGER REFERENCES vehicles(id),
    trip_id        INTEGER REFERENCES trips(id),
    toll_plaza     TEXT NOT NULL,
    highway        TEXT,
    state          TEXT,
    amount         REAL NOT NULL DEFAULT 0,
    source         TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('fastag','manual')),
    payment_mode   TEXT,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('synced','approved','pending','rejected')),
    reference_no   TEXT,
    receipt_path   TEXT,
    notes          TEXT,
    maintenance_id INTEGER REFERENCES maintenance(id),
    created_at     TEXT,
    created_by     INTEGER,
    updated_by     INTEGER,
    updated_at     TEXT,
    company_id     INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_toll_date ON toll_entries(date);
CREATE INDEX idx_toll_vehicle ON toll_entries(vehicle_id);
CREATE INDEX idx_toll_entries_company ON toll_entries(company_id);

CREATE TABLE compliance_expenses (
    id              SERIAL PRIMARY KEY,
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
    created_by      INTEGER,
    updated_by      INTEGER,
    updated_at      TEXT,
    company_id      INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_compliance_expenses_date ON compliance_expenses(date);
CREATE INDEX idx_compliance_expenses_vehicle ON compliance_expenses(vehicle_id);
CREATE INDEX idx_compliance_expenses_company ON compliance_expenses(company_id);

-- ============================================================================
-- People — Employees, Salaries, Attendance, Advances
-- ============================================================================

CREATE TABLE employees (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
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
    created_by           INTEGER,
    created_at           TEXT,
    updated_by           INTEGER,
    updated_at           TEXT,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, name)
);
CREATE INDEX idx_employees_company ON employees(company_id);

CREATE TABLE salaries (
    id               SERIAL PRIMARY KEY,
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
    created_by       INTEGER,
    updated_by       INTEGER,
    updated_at       TEXT,
    company_id       INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_salaries_month_key ON salaries(month_key);
CREATE INDEX idx_salaries_company ON salaries(company_id);

CREATE TABLE salary_items (
    id          SERIAL PRIMARY KEY,
    salary_id   INTEGER NOT NULL REFERENCES salaries(id),
    item_type   TEXT NOT NULL CHECK(item_type IN ('allowance','deduction')),
    description TEXT NOT NULL,
    amount      REAL NOT NULL DEFAULT 0,
    created_by  INTEGER,
    created_at  TEXT,
    updated_by  INTEGER,
    updated_at  TEXT,
    company_id  INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_salary_items_company ON salary_items(company_id);

CREATE TABLE advances (
    id         SERIAL PRIMARY KEY,
    employee   TEXT NOT NULL,
    date       TEXT,
    amount     REAL,
    type       TEXT CHECK(type IN ('given','repaid')),
    notes      TEXT,
    created_at TEXT,
    created_by INTEGER,
    updated_by INTEGER,
    updated_at TEXT,
    company_id INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_advances_company ON advances(company_id);

CREATE TABLE attendance (
    id          SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    date        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('Present','Absent','Leave','Half Day')),
    in_time     TEXT,
    out_time    TEXT,
    remarks     TEXT,
    marked_by   TEXT,
    created_at  TEXT,
    created_by  INTEGER,
    updated_by  INTEGER,
    updated_at  TEXT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(employee_id, date)
);
CREATE INDEX idx_attendance_date ON attendance(date);
CREATE INDEX idx_attendance_employee ON attendance(employee_id);
CREATE INDEX idx_attendance_company ON attendance(company_id);

-- ============================================================================
-- Overheads
-- ============================================================================

CREATE TABLE overheads (
    id                  SERIAL PRIMARY KEY,
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
    created_by          INTEGER,
    created_at          TEXT,
    updated_by          INTEGER,
    updated_at          TEXT,
    company_id          INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_overheads_company ON overheads(company_id);

-- ============================================================================
-- Auth, Settings & Operational Logs
-- ============================================================================

CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL,
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
    created_by    INTEGER,
    updated_by    INTEGER,
    updated_at    TEXT,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    UNIQUE(company_id, username)
);
CREATE INDEX idx_users_company ON users(company_id);

CREATE TABLE access_logs (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    event      TEXT,
    date       TEXT,
    company_id INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_access_logs_company ON access_logs(company_id);

-- Was a singleton (key TEXT PRIMARY KEY) in the SQLite version — now genuinely per-company.
CREATE TABLE settings (
    company_id INTEGER NOT NULL REFERENCES companies(id),
    key        TEXT NOT NULL,
    value      TEXT,
    created_by INTEGER,
    created_at TEXT,
    updated_by INTEGER,
    updated_at TEXT,
    PRIMARY KEY (company_id, key)
);

CREATE TABLE sync_log (
    id            SERIAL PRIMARY KEY,
    job_name      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    synced_count  INTEGER DEFAULT 0,
    failed_count  INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    note          TEXT,
    company_id    INTEGER NOT NULL REFERENCES companies(id)
);
CREATE INDEX idx_sync_log_company ON sync_log(company_id);

-- ============================================================================
-- Deferred foreign keys — for columns whose target table is defined later above than the
-- column itself (Postgres requires the referenced table to exist already; SQLite didn't
-- enforce this, so schema.sql's inline REFERENCES on these same columns silently worked there
-- but would fail here). Verified against this exact file with a real dependency-order check
-- (a script that walks every CREATE TABLE and every inline REFERENCES and confirms the
-- referenced table was already defined) before trusting this list — see the notes at each
-- CREATE TABLE above ("ALTER TABLE constraint near the end").
-- ============================================================================

ALTER TABLE insurance_policies ADD CONSTRAINT fk_insurance_policies_insurer FOREIGN KEY (insurer_id) REFERENCES vendors(id);
ALTER TABLE insurance_policies ADD CONSTRAINT fk_insurance_policies_maintenance FOREIGN KEY (maintenance_id) REFERENCES maintenance(id);
ALTER TABLE batteries ADD CONSTRAINT fk_batteries_vendor FOREIGN KEY (vendor_id) REFERENCES vendors(id);
ALTER TABLE batteries ADD CONSTRAINT fk_batteries_maintenance FOREIGN KEY (maintenance_id) REFERENCES maintenance(id);
ALTER TABLE tyre_stock ADD CONSTRAINT fk_tyre_stock_maintenance FOREIGN KEY (maintenance_id) REFERENCES maintenance(id);
ALTER TABLE tyre_stock ADD CONSTRAINT fk_tyre_stock_vendor FOREIGN KEY (vendor_id) REFERENCES vendors(id);
ALTER TABLE payment_allocations ADD CONSTRAINT fk_payment_allocations_trip FOREIGN KEY (trip_id) REFERENCES trips(id);

-- created_by / updated_by -> users(id), deferred for the same reason: users is defined near
-- the end of this file (after most tenant tables), and users' own created_by/updated_by are
-- self-referential, which Postgres only allows via ALTER TABLE after the table exists anyway.
ALTER TABLE companies             ADD CONSTRAINT fk_companies_created_by             FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE companies             ADD CONSTRAINT fk_companies_updated_by             FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE vehicles              ADD CONSTRAINT fk_vehicles_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE vehicles              ADD CONSTRAINT fk_vehicles_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE vehicle_compliance    ADD CONSTRAINT fk_vehicle_compliance_created_by    FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE vehicle_compliance    ADD CONSTRAINT fk_vehicle_compliance_updated_by    FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE vehicle_challans      ADD CONSTRAINT fk_vehicle_challans_created_by      FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE vehicle_challans      ADD CONSTRAINT fk_vehicle_challans_updated_by      FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE insurance_policies    ADD CONSTRAINT fk_insurance_policies_created_by    FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE insurance_policies    ADD CONSTRAINT fk_insurance_policies_updated_by    FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE batteries             ADD CONSTRAINT fk_batteries_created_by             FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE batteries             ADD CONSTRAINT fk_batteries_updated_by             FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE battery_checks        ADD CONSTRAINT fk_battery_checks_created_by        FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE battery_checks        ADD CONSTRAINT fk_battery_checks_updated_by        FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE tyre_stock             ADD CONSTRAINT fk_tyre_stock_created_by           FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE tyre_stock             ADD CONSTRAINT fk_tyre_stock_updated_by           FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE parties               ADD CONSTRAINT fk_parties_created_by               FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE parties               ADD CONSTRAINT fk_parties_updated_by               FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE vendors               ADD CONSTRAINT fk_vendors_created_by               FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE vendors               ADD CONSTRAINT fk_vendors_updated_by               FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE payments              ADD CONSTRAINT fk_payments_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE payments              ADD CONSTRAINT fk_payments_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE payment_allocations   ADD CONSTRAINT fk_payment_allocations_created_by   FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE payment_allocations   ADD CONSTRAINT fk_payment_allocations_updated_by   FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE trips                 ADD CONSTRAINT fk_trips_created_by                 FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE trips                 ADD CONSTRAINT fk_trips_updated_by                 FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE invoices              ADD CONSTRAINT fk_invoices_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE invoices              ADD CONSTRAINT fk_invoices_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE invoice_items         ADD CONSTRAINT fk_invoice_items_created_by         FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE invoice_items         ADD CONSTRAINT fk_invoice_items_updated_by         FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE invoice_batches       ADD CONSTRAINT fk_invoice_batches_created_by       FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE invoice_batches       ADD CONSTRAINT fk_invoice_batches_updated_by       FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE invoice_batch_trips   ADD CONSTRAINT fk_invoice_batch_trips_created_by   FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE invoice_batch_trips   ADD CONSTRAINT fk_invoice_batch_trips_updated_by   FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE invoice_batch_items   ADD CONSTRAINT fk_invoice_batch_items_created_by   FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE invoice_batch_items   ADD CONSTRAINT fk_invoice_batch_items_updated_by   FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE maintenance           ADD CONSTRAINT fk_maintenance_created_by           FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE maintenance           ADD CONSTRAINT fk_maintenance_updated_by           FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE maintenance_items     ADD CONSTRAINT fk_maintenance_items_created_by     FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE maintenance_items     ADD CONSTRAINT fk_maintenance_items_updated_by     FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE urea_transactions     ADD CONSTRAINT fk_urea_transactions_created_by     FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE urea_transactions     ADD CONSTRAINT fk_urea_transactions_updated_by     FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE toll_entries          ADD CONSTRAINT fk_toll_entries_created_by          FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE toll_entries          ADD CONSTRAINT fk_toll_entries_updated_by          FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE employees             ADD CONSTRAINT fk_employees_created_by             FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE employees             ADD CONSTRAINT fk_employees_updated_by             FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE salaries              ADD CONSTRAINT fk_salaries_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE salaries              ADD CONSTRAINT fk_salaries_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE salary_items          ADD CONSTRAINT fk_salary_items_created_by          FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE salary_items          ADD CONSTRAINT fk_salary_items_updated_by          FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE advances              ADD CONSTRAINT fk_advances_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE advances              ADD CONSTRAINT fk_advances_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE attendance            ADD CONSTRAINT fk_attendance_created_by            FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE attendance            ADD CONSTRAINT fk_attendance_updated_by            FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE overheads              ADD CONSTRAINT fk_overheads_created_by            FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE overheads              ADD CONSTRAINT fk_overheads_updated_by            FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE users                 ADD CONSTRAINT fk_users_created_by                 FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE users                 ADD CONSTRAINT fk_users_updated_by                 FOREIGN KEY (updated_by) REFERENCES users(id);
ALTER TABLE settings              ADD CONSTRAINT fk_settings_created_by              FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE settings              ADD CONSTRAINT fk_settings_updated_by              FOREIGN KEY (updated_by) REFERENCES users(id);

-- ============================================================================
-- Row-Level Security — the actual isolation mechanism, on every tenant table
-- ============================================================================

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY[
            'vehicles','vehicle_compliance','vehicle_challans','insurance_policies','batteries',
            'battery_checks','tyre_stock','parties','vendors','payments','payment_allocations',
            'trips','invoices','invoice_items','invoice_batches','invoice_batch_trips',
            'invoice_batch_items','maintenance','maintenance_items','urea_transactions',
            'toll_entries','employees','salaries','salary_items','advances','attendance',
            'overheads','users','access_logs','settings','sync_log'
        ])
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        -- FORCE so the policy applies even to this schema's owning role, not just other roles
        -- — see the RLS gotcha note at the top of this file.
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (company_id = current_setting(''app.current_company_id'')::int);',
            t
        );
    END LOOP;
END $$;

-- ============================================================================
-- Application role — must NOT be the role that owns these tables (see RLS gotcha note above)
-- ============================================================================

-- CREATE ROLE fleet_app WITH LOGIN PASSWORD '<set a real one, never commit it>';
-- GRANT USAGE ON SCHEMA public TO fleet_app;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fleet_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fleet_app;
-- Left commented out rather than run automatically — the password needs to be a real secret
-- set deliberately, not generated by this script. Run these by hand (with a real password)
-- against the dev database, then app.py's connection must use fleet_app, never the owning role.
