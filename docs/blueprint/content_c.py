"""Part C: the back-end, chapter by chapter."""
from lib import *
import diagrams as D
import facts as F

LINE = {f.name: f.lineno for f in F.FUNCS}

def fn(name):
    return f'<font face="Courier">{name}</font> ({LINE[name]})'

def fns(*names):
    return ', '.join(fn(n) for n in names)


def part_c():
    st = [NextPageTemplate('divider'), PageBreak(), PartDivider('C', 'The back-end', 'The Python program: how a request travels, who may do what, and how every business rule is calculated.',
                                                            ['Anatomy of app.py', 'Life of a request', 'Database access', 'Sign-in, sessions and access control', 'Every business engine, step by step', 'Analytics, mobile twins, background jobs, security']),
          NextPageTemplate('body'), PageBreak()]

    # ------------------------------------------------------------------ 11
    st.append(H1('11. Anatomy of app.py'))
    st.append(P(f'All back-end code is one Python file of {F.APP_LINES:,} lines holding {F.N_FUNCS} top-level functions: {F.N_ROUTES} of them are <b>routes</b> (a URL that the outside world can ask for) and {F.N_HELPERS} are <b>helpers</b> (calculations, validators, hooks) that routes call. Keeping everything in one file was a choice for speed of change; Chapter 55.1 (Part I) lists splitting it as the main maintainability task.', 'lead'))
    st.append(H2('11.1 What lives where (line ranges)'))
    rows = [['Lines', 'Section', 'What is in it'],
            ['1–100', 'Imports, settings, constants', 'Flask app creation, cookie and upload settings, ProxyFix on Render, the database path, regular expressions for mobile numbers and emails, app name RATH.'],
            ['103–300', 'Database connection and self-applying schema', 'get_db(), then the first three start-up steps (overhead allocation column, performance indexes, WAL).'],
            ['300–500', 'Sign-in security core', 'Auth tables, secret key, single-Administrator index, Twilio cleanup, throttle counters, SMS-code issue and check.'],
            ['497–590', 'Input guards and template helpers', 'Friendly 400 handler, NaN/infinity guard, per-request connection cleanup, asset versioning, site logo.'],
            ['589–960', 'Shared helpers', 'Period and growth maths, pagination, place-name cleaning, vehicle body/wheel helpers, get_or_create for parties, vendors and vehicles.'],
            ['968–1280', 'Ledger balance engine and Ledger list', 'Per-account entries, the fast bulk balance computation, accounts list, create/toggle account.'],
            ['1281–1670', 'Website Dashboard', 'Revenue, expense, profit core shared with the phone Dashboard; charts and tables.'],
            ['1673–2215', 'Vehicles', 'RC lookup/PDF, vehicle detail and list, compliance renew and sync.'],
            ['2215–2880', 'Employees and Expenses lists', 'Employee overview, salary and attendance tabs; expense list data and export.'],
            ['2879–3370', 'Trips', 'List, duplicate-LR check, freight formula, validation, insert, update, add; autocomplete data.'],
            ['3371–5930', 'Maintenance family', 'Hub and Overview, Service, Tyres, Battery, Urea, Toll (incl. Excel import and FASTag sync), Compliance fees, Insurance.'],
            ['5929–6540', 'Ledger pages and exports', 'Party/vendor ledger entries and pages, Excel and PDF export, money parser.'],
            ['6502–6950', 'Payment engine and expense writes', 'Party and vendor payment allocation, duplicate guard, expense insert/update.'],
            ['6948–7710', 'Trip delete/end, employees, attendance, payslip, vehicle add/edit, exports, advances', 'Operational write routes and exports.'],
            ['7707–8250', 'Employee ledger, performance, employee add', 'True running balance for advances; driver and vehicle performance.'],
            ['8248–9700', 'Invoices', 'Invoice Center, review, PDF builder, generate, batches, per-trip invoices.'],
            ['9701–9830', 'Opening balances, company name, PWA manifest', 'Small shared pieces.'],
            ['9825–10250', 'Alerts, Limited Access, security hooks', 'Challan bell, screen map and enforcement, cross-site check, headers, require_login, access context, read-only layer, no-cache.'],
            ['10249–10500', 'Login, logout, SMS codes, own profile', 'Both login flows and the "My profile" page.'],
            ['10496–10910', 'Users and Settings', 'One-Administrator helpers, settings save and validation, add/edit/delete/hand-over user, user export.'],
            ['10911–11520', 'Route Analytics and Route Finder', 'Route rollups, the Route Finder endpoints and cache.'],
            ['11514–11908', 'Fleet utilization, edit trip, trip detail', ''],
            ['11908–12430', 'Business Performance and full Excel export', 'Monthly/quarterly/yearly comparison.'],
            ['12427–12533', 'Internal endpoints', 'Backup download and sync tick (token protected).'],
            ['12533–14834', 'The phone app (/app/)', 'About 100 routes: login, dashboard, trips, maintenance, expenses, salary, vehicles, analytics, ledger, profile and the Administrator settings screens.']]
    st.append(tbl(rows, [11, 28, 61], bold_first=True))
    st.append(H2('11.2 Naming habits that make the file navigable'))
    st += bullets(['<font face="Courier">_insert_X_from_form(conn, f)</font> / <font face="Courier">_update_X_from_form</font>: the <i>one</i> place a record of kind X is written. Both the website route and the phone route call it, so they can never disagree (for trips, payments, expenses, tyres, batteries, urea, tolls, vehicles, employees…).',
                   '<font face="Courier">_X_data(conn, ...)</font>: the one place the numbers of a screen are computed (<font face="Courier">_performance_data</font>, <font face="Courier">_fleet_utilization_data</font>, <font face="Courier">_dashboard_financials_data</font>, <font face="Courier">_maintenance_overview_data</font>); the website page and its phone twin both render from it.',
                   '<font face="Courier">_X_tab(conn)</font>: the tab inside a hub page (Maintenance and Employees are hubs with several tabs, switched by <font face="Courier">?tab=</font>).',
                   '<font face="Courier">app_*</font>: the phone-app twin of a website route; same helper, different template and, for actions, JSON answers.',
                   'A leading underscore means "helper, not a URL". Hooks (<font face="Courier">before_request</font>, <font face="Courier">context_processor</font>, <font face="Courier">after_request</font>) are named for what they do.'])
    st.append(callout('tip', 'Why the twins matter', 'Early on the website and phone code were separate copies and drifted (a payment recorded on the phone updated a different set of columns than on the website). Extracting the shared helpers was the cure, and the test harness posts to <i>both</i> sets of routes and compares every screen so a new drift is caught the day it appears.'))
    st.append(PageBreak())

    # ------------------------------------------------------------------ 12
    st.append(H1('12. The life of a request'))
    st.append(P('Every click becomes an HTTP request and passes the same checkpoints in the same order. This is the most useful picture in the whole document for debugging: if a page does not behave, find the first checkpoint that disagrees.', 'lead'))
    st.append(D.d_request())
    st.append(H2('12.1 The hooks, in running order'))
    st.append(tbl([['Order', 'Hook', 'Kind', 'What it does'],
                   ['1', fn('_reject_non_finite_numbers'), 'before_request', 'For POSTs, looks only at fields whose NAME sounds numeric (amount, rate, qty, toll, …) and refuses "nan", "inf", "1e999" or anything above 1e12, so one typo cannot poison a total. A vendor called "Inf" is never affected because only numeric-looking names are checked.'],
                   ['2', fn('reject_cross_site_posts'), 'before_request', 'For POST/PUT/PATCH/DELETE: if the Origin (or Referer) names a different site than the one being used, answer 403. Browsers always send Origin on form posts; scripts and tests send none and are unaffected. Escape hatch: DISABLE_ORIGIN_CHECK=1.'],
                   ['3', fn('require_login'), 'before_request', 'The gatekeeper. (a) Blocks anonymous access to private upload folders. (b) Lets the exempt pages through (login, OTP, signup, static, manifest, internal tokens). (c) Requires a session. (d) 3-hour idle timeout. (e) Re-reads the user\'s access_level, status, role and screens from the database. (f) Inactive or deleted → signed out. (g) Computes is_administrator. (h) Administrator-only endpoints → 403 for others. (i) Read Only may not change anything except a short allow-list. (j) Limited Access: screen map check.'],
                   ['4', 'the route function', 'route', 'Validates, applies business rules, runs SQL, commits, answers.'],
                   ['5', fn('_bad_form_input') + ' and the global handlers', 'error handlers', 'ValueError, OverflowError and IntegrityError become a friendly 400 ("could not be saved — nothing was changed"), JSON for the phone app.'],
                   ['6', 'context processors', 'while rendering', f'{fn("inject_access_level")} (can(), read_only, is_administrator, limited_nav), {fn("inject_security_flags")}, {fn("inject_company_name")}, {fn("inject_app_name")}, {fn("inject_asset_version")} (asset_v), {fn("inject_site_logo")}, {fn("inject_challan_alerts")} (the bell), {fn("inject_vehicle_type_labels")}, {fn("inject_current_year")}, and the header contexts for website and app.'],
                   ['7', fn('add_security_headers'), 'after_request', 'X-Content-Type-Options: nosniff, X-Frame-Options: SAMEORIGIN, Referrer-Policy: strict-origin-when-cross-origin, and Strict-Transport-Security on HTTPS.'],
                   ['8', fn('_read_only_page_enhancements'), 'after_request', 'For Read Only users (and any screen a Limited user holds as View only) injects a small script: a "View-only access" badge, hides obvious Add/Edit/Delete buttons, and blocks any write that is attempted anyway.'],
                   ['9', fn('_no_bfcache_for_dynamic_pages'), 'after_request', 'Cache-Control: no-store so the browser\'s Back button re-fetches live numbers instead of showing an old snapshot.'],
                   ['10', fn('_close_leaked_db_conns'), 'teardown', 'Closes every database connection the request opened, even if the route crashed, so a 500 can never leave the file locked for the next person.']], [6, 24, 14, 56]))
    st.append(PageBreak())

    # ------------------------------------------------------------------ 13
    st.append(H1('13. The database access layer'))
    st.append(P(f'Every query goes through {fn("get_db")}. It is deliberately tiny and is the only place connection behaviour is decided.'))
    st.append(code('''conn = sqlite3.connect(DB, timeout=30)        # wait up to 30 s for a lock instead of failing at 5 s
conn.row_factory = sqlite3.Row                 # read columns by name:  row['billed_amount']
conn.execute("PRAGMA synchronous=NORMAL")      # safe with WAL; far fewer disk syncs per save
wrapper = db.Connection(conn, backend='sqlite')# pure pass-through today; the only place a Postgres swap would happen
g['_open_db_conns'].append(wrapper)            # remembered so teardown can close it even after a crash'''))
    st += bullets(['<b>Placeholders, never string-building.</b> Queries are written with <font face="Courier">?</font> and a parameter list, e.g. <font face="Courier">conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,))</font>. That makes SQL injection impossible for values. The few f-strings in the code only splice in fixed table names chosen from a two-value test (<font face="Courier">parties</font> or <font face="Courier">vendors</font>) or a list of "?" marks for an IN (...) list.',
                   '<b>Commit discipline.</b> A route opens a connection, does its reads and writes, then commits once and closes. If anything raises before the commit, nothing is saved (SQLite rolls back when the connection closes).',
                   '<b>WAL mode</b> (set at start-up) lets readers and the single writer work at once; the 30-second wait turns "two people saved at the same second" into a short queue instead of an error.',
                   f'<b>db.py</b> (113 lines) exists so that, when PostgreSQL arrives, the <font face="Courier">?</font>→<font face="Courier">%s</font> translation is added in one function. Known manual work for that day: the 34 places using <font face="Courier">cursor.lastrowid</font> (Postgres needs RETURNING id), 2 uses of INSERT OR REPLACE, and about 36 SQLite date functions.',
                   '<b>Connections inside loops.</b> Screens that need many accounts pass one open connection down (<font face="Courier">conn=conn</font>) instead of opening one per account; the old Ledger list opened about 9,000 queries for 250 accounts, the current <font face="Courier">_bulk_balances</font> uses a handful.'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 14
    st.append(H1('14. Sign-in and sessions'))
    st.append(P('There are two ways in: a password, or an SMS code. Figure 4 shows both. Both end the same way — the server writes the user\'s id into a signed cookie and logs the event — and both are guarded by counters stored in the database so the limits hold across both gunicorn workers.', 'lead'))
    st.append(D.d_logins())
    st.append(H2('14.1 The numbers that define the behaviour'))
    st.append(tbl([['Rule', 'Value', 'Where'],
                   ['Wrong passwords before lock-out', '5 per account and 30 per visitor address within 15 minutes; answer HTTP 429 with the minutes left', '_AUTH_LIMITS, ' + fn('_login_throttle_message')],
                   ['SMS codes that may be requested', '3 per mobile number per 15 minutes, 10 per address per hour', fn('_start_otp_login')],
                   ['SMS code life and tries', '6 digits from secrets; valid 5 minutes; 5 wrong tries kill it; one use only', fn('_otp_issue') + ', ' + fn('_otp_check')],
                   ['How a code is protected', 'Only an HMAC-SHA256 of id+code (keyed with the secret key) is stored in login_otps; nothing readable is kept in the cookie', fn('_otp_hash')],
                   ['Unknown number', 'Same "code sent" screen, nothing sent, so the form cannot reveal who has an account', fn('_start_otp_login')],
                   ['Password rules', 'minimum 8 characters when changing or setting; stored with werkzeug\'s salted hash', fn('_change_own_password')],
                   ['Session cookie', 'signed with the secret key, HttpOnly, SameSite=Lax, Secure on Render; "remember me" keeps it 30 days, otherwise it dies with the browser', 'app config near line 20'],
                   ['Idle timeout', '3 hours without a request signs you out (any request resets the clock)', fn('require_login')],
                   ['Secret key', 'SECRET_KEY if set, else one random key generated once and kept in settings (_auto_secret_key)', fn('_ensure_secret_key')],
                   ['Self sign-up', 'Closed (403) unless ALLOW_SELF_SIGNUP=1; new accounts would be Staff / Read Only', fn('app_signup')]], [27, 50, 23]))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'Why the SMS code is kept on the server', 'An earlier design stored a fingerprint of the code inside the browser cookie. A cookie is signed but readable, so anyone could recover the six digits in a fraction of a second and sign in as the Administrator without receiving the SMS. The code (as a keyed hash) now lives only in the database and the cookie holds just an opaque row number. A regression test repeats that attack and expects it to fail.'))
    st.append(PageBreak())

    # ------------------------------------------------------------------ 15
    st.append(H1('15. Who may do what: roles and access'))
    st.append(P('Two separate ideas are combined. A user has a <b>role</b> (a label such as Administrator, Manager, Accountant) and an <b>access level</b> (Full Access, Read Only or Limited Access). Only the role value "Administrator" opens company-level powers; the access level decides how much of the day-to-day screens a person may use.', 'lead'))
    st.append(D.d_access())
    st.append(H2('15.1 The four kinds of user'))
    st.append(tbl([['Kind', 'Can see', 'Can change', 'Cannot'],
                   ['Administrator (exactly one active)', 'everything, plus Settings, users, bank/invoice/tax, integrations, access logs', 'everything, including users and company settings', 'be removed, demoted or deactivated without handing the seat to another active user'],
                   ['Full Access (Manager etc.)', 'all operating screens', 'all operating records', 'see Settings, other users or the user log; add users'],
                   ['Read Only', 'all operating screens', 'nothing (own password only; may preview/review invoices and use route lookups)', 'save, edit, delete anything; the server refuses even if a button is forced'],
                   ['Limited Access', 'only the screens ticked for them', 'only on screens ticked as Edit; View screens are read-only', 'open any unticked screen, export the whole database, reach Settings or users']], [22, 28, 28, 22], bold_first=True))
    st.append(H2('15.2 Limited Access in detail'))
    st.append(P(f'The Administrator ticks which of nine <b>screens</b> a user gets, each as View or Edit. The choice is stored in <font face="Courier">users.module_access</font> as text such as <font face="Courier">Dashboard:v,Ledger:e,Invoices:e</font> (e = edit, v = view; old values without a letter count as edit, and the former "Payments" screen is part of Ledger). Every one of the {F.N_ROUTES} routes is assigned to exactly one screen, or marked common (own profile, logout, static files), Administrator-only, or Full-Access-only (the whole-database export). A test fails if a new route is added without being classified, so a forgotten route can never silently become open or silently become unreachable.'))
    mrows = [['Screen', 'URLs it protects', 'Opens on', 'Examples of what it covers']]
    ex = {'Dashboard': 'website and phone dashboards', 'Trips': 'trip list, add/edit/end/delete, trip exports', 'Vehicles': 'vehicle list/detail, RC lookup, compliance, challans',
          'Maintenance': 'service, tyres, battery, urea, tolls (and Excel import), compliance fees, insurance', 'Ledger': 'party and vendor ledgers, payments, opening balances, exports',
          'Invoices': 'Invoice Center, review, generated invoices, PDFs', 'Expenses': 'expense list, add/edit/delete, export', 'Salaries': 'employees, salary, advances, attendance, payslips, employee ledger',
          'Reports': 'Business Performance, Performance, Route Analytics and Finder, Fleet Utilization'}
    land = {'Dashboard': '/dashboard', 'Trips': '/trips', 'Vehicles': '/vehicles', 'Maintenance': '/maintenance', 'Ledger': '/accounts', 'Invoices': '/invoice-center', 'Expenses': '/overheads', 'Salaries': '/salaries', 'Reports': '/route-analytics'}
    for m in F.MODULE_LIST:
        mrows.append([f'<b>{m}</b>', str(len(F.MODULE_ENDPOINTS[m])), f'<font face="Courier">{land[m]}</font>', ex[m]])
    st.append(tbl(mrows, [14, 12, 20, 54]))
    st.append(Spacer(1, 6))
    st += bullets([f'<b>Enforcement</b> is {fn("_limited_block")}, called from {fn("require_login")} on every request: it looks the route up in the screen map (<font face="Courier">_ENDPOINT_MODULE</font>) and answers with a friendly "no access" page (JSON for the app), a redirect to the first allowed screen when the Dashboard is not granted, or lets the request through (marking a View screen so the read-only layer is added).',
                   f'<b>Navigation</b> is built from the same table: the website sidebar and Dashboard cards use <font face="Courier">can(\'Ledger\')</font>; the phone bottom bar, + quick-add fan and More menu are generated by {fn("_limited_nav")} (first three allowed screens in the bar, the rest under More, + only if they can add something). Users who are not Limited keep the original fixed layout.',
                   f'<b>The editor</b> the Administrator uses is one shared script, <font face="Courier">static/limited_access_picker.js</font>, with presets (Accountant, Operations, Fleet and service), a switch and a View/Edit toggle per screen, and a live phone preview; it writes hidden fields that {fn("_modules_from_form")} validates (at least one screen required).'])
    st.append(H2('15.3 The one-Administrator rule'))
    st += bullets([f'{fn("_other_active_administrator")} and the checks in {fn("add_user")} / {fn("edit_user")} refuse to create or activate a second Administrator.',
                   'A database unique index (<font face="Courier">uq_one_active_administrator</font>) enforces the same thing even against a bug or two simultaneous requests.',
                   f'{fn("make_administrator")} hands over the seat: it demotes the caller to Manager (Full Access) first and promotes the target second inside one transaction, so there is never a moment with two or with none.',
                   'The only active Administrator cannot deactivate, demote or delete themselves (so nobody is ever locked out of Settings).'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 16
    st.append(H1('16. Validation and error handling'))
    st.append(P('A bad number must never be stored, and a bad request must never show a crash page. Four layers cooperate.', 'lead'))
    st.append(tbl([['Layer', 'What it catches', 'Result'],
                   ['Browser (HTML attributes, small scripts)', 'Required fields, number pads, 10-digit mobile patterns, date pickers', 'Convenience only; never trusted'],
                   [fn('_reject_non_finite_numbers'), 'nan / inf / 1e999 / over 1e12 in amount-like fields of any form', '400 with a friendly message'],
                   [fn('_parse_money'), 'text, commas and a leading rupee sign tolerated; blanks; negatives (unless allowed); zero (unless allowed); absurdly large; rounds to paise', '(value, error) so each route shows a precise message'],
                   [fn('_trip_form_error'), 'every one of 40 money fields; computed bill cannot be negative; on a NEW trip the party advance cannot exceed the bill (edits may legitimately end up with more received than billed)', 'form re-shown with the message'],
                   [fn('_validated_payment_basics') + ' + ' + fn('_payment_is_duplicate'), 'amount, date, mode; the exact same payment twice within 10 seconds', '"not adding it twice"'],
                   [fn('_settings_form_error'), 'company name required, GST/TDS 0–100, round-off, due days, next invoice number ≥ 1, sync days, MSG91 key and template present', 'readable JSON error in the app'],
                   [fn('_bad_form_input') + ' (also for IntegrityError)', 'anything above that still raises ValueError, OverflowError or a constraint violation', 'friendly 400; nothing saved; original error logged'],
                   ['Database constraints', 'CHECK that a payment allocation points at exactly one of trip / maintenance / expense; unique usernames, phone and email rules; the single-Administrator index', 'IntegrityError → friendly 400']], [28, 46, 26]))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'The fuzz stage', 'The test harness has a stage that feeds garbage (huge numbers, NaN, SQL fragments, empty values, wrong types) to every POST route in the app and expects no 500 error and no data change. It is the safety net behind the validation layers above.'))
    st.append(PageBreak())
    return st
