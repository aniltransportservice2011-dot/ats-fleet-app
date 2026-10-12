"""Part D: the database."""
from lib import *
import diagrams as D
import facts as F
import schema_notes as SN
from content_c import fn


def part_d():
    st = [NextPageTemplate('divider'), PageBreak(), PartDivider('D', 'The database', 'What is stored, how the tables relate, and the rules that keep the numbers honest.',
                                                            ['SQLite and the single file', 'How the tables relate (three diagrams)', 'Data dictionary: every table, every column', 'Conventions: signs, statuses, indexes, audit columns', 'Multi-company readiness and the PostgreSQL plan', 'Migrations']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('22. The database at a glance'))
    st.append(P(f'All business data lives in <font face="Courier">fleet.db</font>, a single SQLite file of about {round(__import__("os").path.getsize(F.p("fleet.db"))/1024)} KB today, holding {F.N_TABLES} tables and {F.N_INDEXES} indexes. SQLite is not a separate server: the Python process reads and writes the file directly, which removes a whole class of setup, network and password problems for a business of this size.', 'lead'))
    st += stat_row([(str(F.N_TABLES), 'tables'), (str(F.N_INDEXES), 'indexes'), (f'{F.table_rows("trips"):,}', 'trips'), (f'{F.table_rows("maintenance"):,}', 'maintenance bills'), (f'{F.table_rows("payments"):,}', 'payments')])
    st.append(H2('22.1 How the file is configured'))
    st.append(tbl([['Setting', 'Value', 'Why'],
                   ['Journal mode', 'WAL (write-ahead log)', 'Readers never wait for the writer; a 0.5-second dashboard no longer blocks saves. Note: the live database is the .db file PLUS its -wal file, so backups use SQLite\'s backup command, never a plain copy.'],
                   ['Lock wait', '30 seconds', 'Two people saving together queue briefly instead of seeing "database is locked".'],
                   ['synchronous', 'NORMAL', 'Safe with WAL; far fewer disk syncs per save.'],
                   ['Foreign keys', 'declared but not enforced at run time (SQLite default)', 'Orphans are watched by db_health_check.py instead; deletes remove dependent allocation rows explicitly.'],
                   ['Row access', 'sqlite3.Row', 'Columns read by name (row["amount"]).'],
                   ['Dates', 'text YYYY-MM-DD (times as text)', 'Sorts correctly as text; the health check flags any other format.'],
                   ['Money', 'REAL (floating point), rounded to paise on payments', 'Validated and rounded when written; tests compare screens to independent arithmetic to catch drift.'],
                   ['Audit columns', 'company_id, created_by, created_at, updated_by, updated_at', 'On nearly every table: who wrote it and when.']], [18, 28, 54], bold_first=True))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'Why SQLite is a good fit now and what changes later', 'One writer at a time is no problem for five to thirty people entering trips and payments. The honest limit is sustained simultaneous editing by dozens of users or very heavy reporting; the answer is PostgreSQL, for which schema_postgres.sql already exists (Chapter 25).'))
    st.append(PageBreak())

    st.append(H1('23. How the tables relate'))
    st.append(P('Three pictures cover the whole model. Arrows read "has many". Numbers in the boxes are the row counts in the database used to prepare this document.', 'lead'))
    st.append(D.d_er_core())
    st.append(D.d_er_fleet())
    st.append(PageBreak())
    st.append(D.d_er_people())
    st.append(H2('23.1 Every foreign-key link (generated from the schema)'))
    rows = [['From table', 'Column', 'Points at']]
    for t in F.TABLES:
        for col, tgt in F.fk_list(t):
            if tgt in ('users',):
                continue
            rows.append([f'<font face="Courier">{t}</font>', f'<font face="Courier">{col}</font>', f'<font face="Courier">{tgt}</font>'])
    st.append(tbl(rows, [34, 33, 33]))
    st.append(Spacer(1, 4))
    st.append(P('Not listed: the audit columns created_by and updated_by, which point at users on almost every table (a user who is deleted leaves these as empty values; the health check reports them as information, not errors), and links by <i>name</i> rather than id: salaries and advances refer to employees by employee name (and employee_id on newer rows), and trips keep owner_name text next to owner_vendor_id.'))
    st.append(PageBreak())

    # data dictionary
    st.append(H1('24. Data dictionary'))
    st.append(P('Every table with its purpose, its row count and every column with its type. This section is generated from the live schema when the document is built, so it cannot be out of date; the notes beside non-obvious columns were written by hand.', 'lead'))
    for t in F.TABLES:
        info = F.table_info(t)
        n = F.table_rows(t)
        st.append(H3(f'<font face="Courier">{t}</font> — {n:,} rows, {len(info)} columns'))
        st.append(P(SN.TABLE.get(t, ''), 'small'))
        rows = [['Column', 'Type', 'Key', 'Notes']]
        for c in info:
            key = 'PK' if c['pk'] else ''
            rows.append([f'<font face="Courier">{c["name"]}</font>', c['type'] or '', key + (' NOT NULL' if c['notnull'] and not c['pk'] else ''), SN.COL.get((t, c['name']), '')])
        if len(rows) > 28:      # wide tables: print three columns' worth per row to keep the document readable
            names = [r[0] + (' <font color="#5a6b8c">' + r[1].lower() + '</font>') for r in rows[1:]]
            third = (len(names) + 2) // 3
            grid = [[names[i] if i < len(names) else '', names[i + third] if i + third < len(names) else '', names[i + 2 * third] if i + 2 * third < len(names) else ''] for i in range(third)]
            st.append(tbl([['Columns (name, type)', '', '']] + grid, [34, 33, 33], header=True, zebra=True))
            notes = [(c, SN.COL[(t, c)]) for (tt, c) in SN.COL if tt == t]
            if notes:
                st.append(tbl([['Column', 'Notes']] + [[f'<font face="Courier">{a}</font>', b] for a, b in notes], [24, 76]))
        else:
            st.append(tbl(rows, [27, 14, 12, 47]))
        st.append(Spacer(1, 4))
    st.append(PageBreak())

    st.append(H1('25. Conventions, rules and multi-company readiness'))
    st.append(H2('25.1 Rules enforced by the database itself'))
    st.append(tbl([['Rule', 'How'],
                   ['A payment allocation points at exactly one target', 'CHECK on payment_allocations: (trip_id is not null) + (maintenance_id is not null) + (overhead_id is not null) = 1'],
                   ['Allowed values', 'CHECK constraints: payments.payment_type (received, paid), advances.type (given, repaid), employees.type (Staff, Driver), attendance.status (Present, Absent, Leave, Half Day), invoice_batches.invoice_type (party, vehicle_owner, tax, bill), item types (charge, deduction, allowance), urea txn_type and source, toll source and status, compliance_type (fitness, puc, permit)'],
                   ['Unique mobile and email per user', 'unique index on users(phone) where not null; unique index on lower(email) where not null; usernames are unique'],
                   ['One active Administrator', 'unique index uq_one_active_administrator on users(company_id) where role is Administrator and status is not Inactive'],
                   ['Performance', f'{F.N_INDEXES} indexes, including partial indexes on mostly-empty allocation columns (SQLite ignores an ordinary index on a column that is almost all NULL)']], [30, 70], bold_first=True))
    st.append(H2('25.2 Sign and meaning conventions'))
    st += bullets(['<b>Ledger balance</b> = debits − credits; positive means the party owes the company, negative means the company owes (payable).',
                   '<b>Opening balances</b> are signed numbers with a date on parties, vendors and employees.',
                   '<b>Status words</b> in use: vehicles Active/Inactive; users Active/Inactive; maintenance Open/Completed; expenses Paid/Pending; invoices payment status PENDING/PAID; toll synced/approved/pending/rejected; batteries Good/Weak/Replace Soon/Dead/In Stock; insurance Active/Expiring Soon/Expired/Cancelled.',
                   '<b>Own versus hired</b> appears as type on both vehicles and trips and changes how costs and profit are read everywhere.'])
    st.append(D.d_tenancy())
    st.append(H2('25.3 The PostgreSQL twin'))
    st.append(P('<font face="Courier">schema_postgres.sql</font> is a complete, parse-verified PostgreSQL version of the schema (it has been checked with a SQL parser, not yet run against a live Postgres): every table carries company_id as a real foreign key with an index; natural-key uniques are composite (company_id, name); and row-level security policies make a connection that sets app.current_company_id physically unable to read another company\'s rows. Five bare UNIQUE constraints that would break the day a second company exists (vehicle number, party name, vendor name, employee name, username) were already corrected there. <font face="Courier">db.py</font> holds the one-line placeholder translation for the day it is switched on.'))
    st.append(PageBreak())

    st.append(H1('26. Migrations: how the database layout has changed'))
    st.append(P('Every change to the layout is a documented script, kept as history. The latest ones are also applied automatically at start-up (Chapter 8.2), so a deployment can never find the code ahead of the database.', 'lead'))
    mig = {'migrate.py': 'Original import from the Excel workbook into fleet.db.', 'migrate_extra.py': 'Extra one-time import steps.', 'migrate_v2.py': 'Second-generation import / reshape.',
           'migrate_audit_columns.sql': 'Adds created_by, created_at, updated_by, updated_at to the tables.', 'migrate_company_id.sql': 'Adds company_id (default 1) to every table: the multi-company preparation.',
           'migrate_compliance_expenses.sql': 'Creates compliance_expenses.', 'migrate_drop_guarantee_applied.sql': 'Removes the old guarantee_applied flag.', 'migrate_guarantee_qty.sql': 'Adds trips.guarantee_qty (minimum guaranteed weight).',
           'migrate_invoice_batch_items_vendor.sql': 'Lets invoice lines name a vendor.', 'migrate_invoice_charge_exclusions.sql': 'Creates invoice_batch_charge_exclusions.', 'migrate_payment_allocation_maintenance.sql': 'Lets a payment allocation target a maintenance bill.',
           'migrate_payment_allocation_overhead.sql': 'Lets a payment allocation target an expense bill (also self-applied at start-up).', 'migrate_unique_phone_email.sql': 'Unique mobile and email per user.',
           'migrate_username_nocase.sql': 'Case-insensitive usernames.', 'migrate_vehicle_type_own_hired.sql': 'Normalises vehicle type to own / hired.'}
    st.append(tbl([['File', 'What it changed']] + [[f'<font face="Courier">{m}</font>', mig.get(m, '')] for m in F.MIGRATIONS], [38, 62]))
    st.append(Spacer(1, 6))
    st.append(P('Changes made after these files are all self-applying and listed in Chapter 8.2: the payment_allocations.overhead_id column, the ~36 performance indexes, WAL mode, login_otps and auth_attempts, the secret key row, and the single-Administrator index.'))
    st.append(PageBreak())
    return st
