"""Part H: quality (tests, performance) and the honest list of findings."""
import ast
from lib import *
import diagrams as D
import facts as F
from flowlib import C


def doc1(path):
    t = ast.parse(open(path, encoding='utf-8').read())
    d = (ast.get_docstring(t) or '').split('\n')[0]
    return d


def part_h():
    st = [NextPageTemplate('divider'), PageBreak(),
          PartDivider('H', 'Quality: tests, speed and honest findings', 'How correctness is proven, how fast it runs under load, and the gaps found while documenting the code.',
                      ['Test architecture and results', 'Performance engineering and the load test', 'Findings recorded while preparing this document', 'Business decisions still open']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('50. How the app is tested'))
    st.append(P('The biggest risk in a money application is a screen showing a number that is quietly wrong. The test strategy attacks that directly: drive the real screens with thousands of invented situations and compare every number shown with an independent calculation.', 'lead'))
    st.append(D.d_tests())
    st.append(H2('50.1 The audit harness (test_balance_audit.py, 3,000+ lines)'))
    st += bullets(['<b>Isolation.</b> Each batch of scenarios builds a fresh SQLite database from schema.sql. The real fleet.db is never opened. All outside network calls are blocked, so no paid API can be called and no SMS can be sent.',
                   '<b>Realism.</b> Scenarios are executed through the real routes with a forged session for a real test user (the app re-reads the user on every request, so the test user exists in the scratch database). Website forms and phone routes are both used.',
                   '<b>Independent model.</b> After every step the harness reads the numbers off the rendered pages (using an HTML parser) and compares them with its own first-principles arithmetic and with each other (for example Trips "Balance due" versus Ledger balance versus Dashboard receivable).',
                   '<b>Findings, not failures.</b> Every disagreement is recorded with the exact scenario that caused it and grouped by cause so a few hundred identical symptoms become one finding. Remaining findings are classified DESIGN (a business definition difference) or BUG; the run for this document shows only DESIGN items.'])
    stages = [('party', '18,604', '918,301', 'every sequence of trip, advance, payment (ticked and unticked), edit, end and delete for a party; Trips vs Ledger vs Dashboard; remaining differences are the opening-balance and unallocated-payment DESIGN items'),
              ('vendor', '3,228', '92,992', 'owner-hire trips, fuel and advance paid by the company, maintenance bills, expense bills and vendor payments; vendor ledger vs popup'),
              ('edge', '83', '145', 'awkward inputs and orderings'), ('maint', '200', '2,040', 'maintenance bills and allocations'), ('expense', '40', '396', 'expenses, pending bills, vendor linking'),
              ('employee', '60', '3,026', 'salary, advances, repayments, date filters'), ('empedge', '198', '350', 'employee edge cases'), ('acct', '9', '504', 'Ledger list, bulk balances'),
              ('invoice', '24', '162', 'invoice totals, charges, deductions, large invoices'), ('invinput', '36', '50', 'invoice number and amount validation'), ('urea', '18', '388', 'stock in/out/direct and stock floor'),
              ('xbill', '240', '5,172', 'expense bills naming vendors and payment settlement'), ('ownerinv', '32', '128', 'owner (freight bill) invoice rules'), ('access', '9', '94', 'Read Only: every write refused, nothing changes'),
              ('invreview', '24', '36', 'invoice review gate'),
              ('roles', '10', '218', 'Administrator-only screens, own profile, last-Administrator rules, security controls, the one-Administrator rule, all of Limited Access (screen map, view/edit, navigation, dashboard cards), MSG91'),
              ('fuzz', '101', '1,212', 'garbage into every POST route: no 500s, no data change')]
    st.append(tbl([['Stage', 'Scenarios', 'Checks', 'What it proves']] + [[C(a), b, c, d] for a, b, c, d in stages], [10, 11, 11, 68]))
    st.append(P('Latest full run (all 17 stages): no BUG findings; the only differences are the DESIGN items listed in Chapter 52.', 'small'))
    st.append(H2('50.2 The other test files'))
    st.append(tbl([['File', 'Purpose (first line of its own description)']] + [[C(f.split('/')[-1]), doc1(f)] for f in F.TEST_FILES if not f.endswith('test_balance_audit.py')] + [[C('load_test.py'), doc1(F.p('load_test.py'))], [C('db_health_check.py'), doc1(F.p('db_health_check.py'))]], [32, 68]))
    st.append(Spacer(1, 4))
    st.append(P('The Postgres isolation test (test_multitenancy_isolation.py) needs a real PostgreSQL connection string and is skipped until Phase 1 starts. The accounts-equivalence test proves the fast bulk balances equal the original per-account ones (on a copy of the real data and on the 12,000-trip database). Run any test with <font face="Courier">python3 test_x.py</font>; the harness stage with <font face="Courier">python3 test_balance_audit.py --stage roles</font>.'))
    st.append(PageBreak())

    st.append(H1('51. Performance'))
    st.append(P('A load test seeds 12,000 trips, 4,215 payments (17,086 allocations), 4,500 maintenance bills, 2,280 salaries, 3,500 expenses, 24,000 attendance rows and 6,000 toll entries (a 10.9 MB database) and times every screen. Rating bands: under 100 ms excellent, under 300 ms good, under 1 s OK, 1–3 s slow, over 3 s poor.', 'lead'))
    st.append(tbl([['Group', 'Screens', 'Excellent', 'Good', 'OK', 'Slow', 'Poor'],
                   ['Website screens', '87', '76', '8', '2', '1', '0'], ['Phone screens', '36', '31', '2', '3', '0', '0'], ['Excel / PDF downloads', '21', '10', '4', '5', '1', '1'], ['Buttons that save data', '13', '12', '1', '0', '0', '0']], [34, 14, 13, 13, 13, 13, 13], bold_first=True))
    st.append(Spacer(1, 4))
    st.append(P('Slowest items: Business Performance over a long date range about 1.0 s (189 queries); Dashboard with a financial-year filter about 0.5 s (plain 0.27 s); the full Trips PDF about 5.8 s and the whole-database Excel about 1.3 s. Everything else is under half a second.'))
    st.append(H2('51.1 Many people at once'))
    st.append(P('Real gunicorn with the Render settings (2 workers × 4 threads). Each virtual user signs in and clicks around (about 1–2 clicks a second, one in ten is a save):'))
    st.append(tbl([['Users at once', 'Requests/s', 'Typical (p50) ms', 'p95 ms', 'Slowest ms', 'Errors'],
                   ['1', '1.5', '76', '313', '562', '0'], ['5', '7.0', '60', '320', '1,732', '0'], ['10', '10.1', '73', '1,726', '5,733', '0'], ['25', '7.5', '1,783', '6,062', '11,471', '0'], ['50', '5.1', '5,637', '15,091', '18,806', '0']], [18, 16, 20, 16, 16, 14], bold_first=True))
    st.append(P('Reading it: up to 10 simultaneous users the app stays snappy and error-free; at 25 or more heavy simultaneous users pages slow to several seconds because only 8 requests can run at once and SQLite allows one writer. For about five staff this is far inside the safe zone. The next steps if growth demands it are more workers, trimming the heaviest reports and PostgreSQL.', 'small'))
    st.append(H2('51.2 What made it fast'))
    st += bullets(['<b>Indexes.</b> About 36 were added (including partial indexes for mostly-empty allocation columns). The foreign-key columns of every table with 200+ rows are indexed (checked by db_health_check.py).',
                   '<b>Bulk balances.</b> The Ledger list went from about 9,000 queries to a handful (3.5 s → 0.1 s; Dashboard 3.9 s → 0.27 s).',
                   '<b>WAL mode and a 30-second lock wait.</b> Saves no longer fail with "database is locked" when 25 or more users work at once.',
                   '<b>Single connection per request,</b> closed at teardown even after a crash.',
                   '<b>No caching of data.</b> Speed comes from fewer queries, never from remembering old answers.'])
    st.append(PageBreak())

    st.append(H1('52. Findings recorded while preparing this document'))
    st.append(P('Writing down every path exposed several small gaps. None changes today\'s numbers, but each is worth knowing and fixing. They are listed so nothing is hidden; the first group are data-integrity gaps, the second are design and operations notes.', 'lead'))
    st.append(H2('52.1 Data-integrity gaps (left behind rows or silent no-ops)'))
    st.append(tbl([['#', 'Where', 'What happens', 'Suggested fix'],
                   ['1', 'delete_vehicle', 'A bare DELETE with no check; trips, bills, documents and tolls that point at the vehicle are left pointing at a missing id (foreign keys are not enforced).', 'Refuse when the vehicle is used, like delete_trip does; offer Deactivate instead.'],
                   ['2', 'delete_trip', 'Removes only the trips row. The trip\'s invoice_items ("Others" lines) and any invoice_batch_trips link remain as orphans.', 'Delete or refuse on those links too.'],
                   ['3', 'invoice_batch_delete', 'Leaves the batch\'s invoice_batch_charge_exclusions rows behind.', 'Delete them in the same transaction.'],
                   ['4', 'edit_employee', 'Salaries and advances are matched by employee NAME; renaming an employee leaves the old name on earlier rows, which then no longer match.', 'Match by employee_id, or rewrite the name on related rows when renaming.'],
                   ['5', 'add_service, add_tyre_stock, add_battery, install_tyre_stock, add_compliance_expense', 'The website routes ignore the validation message returned by their helper and just redirect, so a request missing a required field (possible only if the browser form checks are bypassed) silently saves nothing.', 'Pass the error back like add_overhead does.'],
                   ['6', 'invoice numbers', 'invoice_batches.invoice_number has no UNIQUE constraint and the next number is read, then written at the end of the request. Two generations at the same moment could use the same number.', 'Add a unique index on (company, invoice_number) and take the number inside the same write transaction.'],
                   ['7', 'payments', 'The duplicate guard covers only 10 seconds, and payments have no edit or reverse screen, only the allocation of what was recorded.', 'A "reverse payment" action that restores allocations.'],
                   ['8', 'upload files', 'Toll and insurance uploads accept any file type and size up to the 16 MB request cap, and live in the app folder, not on the persistent disk.', 'Allow PDF/JPG/PNG/WEBP, limit size, resize photos, store on the disk.']], [4, 22, 44, 30]))
    st.append(H2('52.2 Design and operations notes'))
    st += bullets(['<b>app.py is one 14,800-line file.</b> It works and is tested, but splitting it into modules (trips, ledger, maintenance, invoices, security, phone) would make change safer.',
                   '<b>Money is stored as floating-point numbers</b> rounded to paise on writes. Tests compare totals with independent arithmetic, but a fixed-point (integer paise) design would remove any rounding doubt.',
                   '<b>Foreign keys are declared but not enforced</b> by SQLite at run time; consistency is kept by code and watched by db_health_check.py (currently 4 old orphan rows from mock compliance and insurance data, one trip over-received by Rs 434, one duplicate LR).',
                   '<b>FASTag sync is a mock.</b> The toll Excel import (BOSS statements) is the real route for toll data.',
                   '<b>The toll-import stash is per worker and in memory.</b> A restart between "preview" and "import" means repeating the preview.',
                   '<b>Sync endpoint token compare</b> (sync-tick) is a plain inequality; the backup endpoint uses a constant-time compare.',
                   '<b>Security still open:</b> full CSRF tokens (today SameSite + Origin check), a Content-Security-Policy, encryption of stored keys, pinned dependency versions, error alerting, closing the old Twilio account, removing old database backups from the GitHub history.',
                   '<b>Uploads and the persistent disk</b> must be confirmed for production (Chapters 6 and 9).'])
    st.append(H2('52.3 Business decisions still open (screens may differ by definition)'))
    st += bullets(['<b>Ledger versus Trips:</b> a party\'s opening balance and any payment not allocated to a trip count in the Ledger but not in the Trips balance.',
                   '<b>Invoice versus ledger:</b> an invoice\'s grand total (optional lines, discounts, detention, RCM) can differ from the ledger\'s billed amount.',
                   '<b>Linked party + vendor ledger</b> has no "pay the owner" popup; vendor popup gaps for opening balances and maintenance.',
                   '<b>Maintenance Overview</b> counts own vehicles only (hired-vehicle bills are excluded).',
                   '<b>Employees:</b> a repayment can exceed what was given (no cap).'])
    st.append(PageBreak())
    return st
