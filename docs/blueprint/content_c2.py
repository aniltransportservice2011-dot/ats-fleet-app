"""Part C continued: business engines, analytics, mobile twins, background jobs, security."""
from lib import *
import diagrams as D
import facts as F
from content_c import fn, fns


def part_c2():
    st = []
    # ------------------------------------------------------------------ 17
    st.append(H1('17. The business engines, one by one'))
    st.append(P('This chapter explains how each business rule is actually calculated and where it lives. Read it with Figure 6 (money flow) in mind: trips create what is owed, payments settle it, and the ledger engine sums the difference.', 'lead'))

    st.append(H2('17.1 Trips'))
    st.append(P('A trip is one journey carrying one customer\'s goods. It is the largest table (80 columns) because it holds the whole commercial story of the journey.'))
    st.append(tbl([['Column group', 'Columns (examples)', 'Meaning'],
                   ['Identity', 'id, date, lr_number, vehicle_id, type, party_id, from_loc, to_loc, material, driver_name', 'which truck, for which party, what route; type is own or hired and follows the vehicle'],
                   ['Rate', 'quantity, guarantee_qty, rate, rate_type, fixed_rate_amount', 'PER_MT (tonnes × rate) or FIXED amount; a guarantee quantity bills the minimum weight when one applies'],
                   ['Bill', 'billed_amount', 'freight + extra charges − deductions, calculated once on save'],
                   ['Extra charges (added)', 'detention_charges, gps_cost, loading_charge, unloading_charge, police_charges, sim_tracking, union_charges, weight_charges, other_charges', 'extras the party is billed'],
                   ['Deductions (subtracted)', 'brokerage, builty_commission, late_fees, material_damage, shortage_amount, shortage_qty, tds, other_deductions', 'what reduces the bill'],
                   ['Fuel and driver advance', 'fuel_amount, fuel_liters, fuel_price, fuel_vendor_id, driver_adv_amount, driver_adv_vendor_id', 'company costs; vendors are created on the fly'],
                   ['Party money', 'party_advance, payment_received', 'money already received against this bill'],
                   ['Hired-vehicle owner terms', 'owner_name, owner_vendor_id, owner_rate, owner_rate_type, owner_fixed_amount, owner_amount, paid_to_owner, pending_to_owner', 'what the vehicle owner is paid'],
                   ['Own-vehicle costs', 'toll, urea, parking, puncture, fine, labour_charges, agent_commission, builty_expense, loading/unloading_expense, weighbridge_charges, permit_charges, other_expense, misc_vendor_id', 'direct costs of running the trip'],
                   ['Status and timing', 'lr_received, end_date, end_time, actual_km, shortage_date/unit/remarks, is_empty, remarks', 'whether it is finished, kilometres, empty runs'],
                   ['Audit', 'company_id, created_by, created_at, updated_by, updated_at', 'who and when; company for future multi-tenancy']], [18, 47, 35], bold_first=True))
    st.append(H3('How a trip is saved'))
    st += bullets([f'{fn("add_trip")} (website) and {fn("app_add_trip")} (phone) validate with {fn("_trip_form_error")}, refuse a duplicate LR number ({fn("_duplicate_lr_trip")}), then call {fn("_insert_trip_from_form")}.',
                   f'That function resolves names to ids ({fn("get_or_create_vehicle")}, {fn("get_or_create_party")}, {fn("get_or_create_vendor")}) so typing a new fuel pump or owner just creates the vendor; the trip type follows the vehicle ({fn("_resolve_trip_type")}).',
                   f'The freight comes from {fn("_trip_freight")}: FIXED → the fixed amount; PER_MT → (guarantee quantity if filled, else actual quantity) × rate. The same function is used by insert, update, invoice preview, invoice PDF and batch lines, so a trip is billed identically everywhere. The guarantee never applies to what the owner is paid.',
                   f'{fn("_update_trip_from_form")} recomputes the same way on edit; {fn("end_trip")} / {fn("_end_trip_from_form")} close a running trip (the End Trip box pre-fills the gross freight so shortage is deducted once, not twice); {fn("delete_trip")} removes the trip and its allocations.',
                   '<b>Own versus hired:</b> type "own" costs fuel, advance and the direct-cost columns; type "hired" costs the owner payment. Profit = billed − cost under that rule, everywhere.'])
    st.append(PageBreak())

    st.append(H2('17.2 Parties, vendors and the linked-account merge'))
    st.append(P(f'A <b>party</b> is a customer; a <b>vendor</b> is anyone the company pays. The same organisation can be both (a transporter who hires you trucks and also ships with you); then the vendor row carries <font face="Courier">linked_party_id</font> and the two are shown as <b>one</b> merged ledger: what they owe you minus what you owe them. {fn("get_or_create_party")} and {fn("get_or_create_vendor")} create records from typed names so data entry never stops to ask. Opening balances (money owed before the app existed) are a signed number with a date on each party, vendor and employee.'))

    st.append(H2('17.3 The ledger engine'))
    st.append(P(f'A ledger is a list of dated lines with a debit, a credit and a running balance. {fn("_get_party_ledger_entries")} builds a party\'s lines and {fn("_get_vendor_ledger_entries")} a vendor\'s; the ledger page, exports and popups all show them.'))
    st.append(tbl([['Line kind', 'Debit (they owe us more)', 'Credit (they owe us less)', 'Source'],
                   ['Opening Balance', 'if positive', 'if negative', 'parties.opening_balance'],
                   ['Trip Bill', 'billed_amount', 'the part of payment_received NOT coming from a ledger payment + party_advance', 'trips'],
                   ['Payment In', '—', 'one line per allocation ("Applied to LR …"); any unallocated leftover as its own line', 'payments + payment_allocations'],
                   ['Linked vendor lines', 'vendor-side bills and owner trips (merged in)', 'vendor-side payments', '_get_vendor_ledger_entries']], [18, 28, 36, 18], bold_first=True))
    st += bullets(['Balance = sum of debits − sum of credits; <b>positive = receivable</b> (they owe us), <b>negative = payable</b> (we owe them). The vendor ledger follows the same sign rule.',
                   'A trip\'s own "payment received" is floored at zero after subtracting what ledger allocations already account for, so a payment is never subtracted twice.',
                   f'<b>Speed:</b> {fn("_accounts_rows_by_entries")} (the original) builds every account\'s full ledger — about 9,000 queries for 250 accounts. {fn("_bulk_balances")} restates the same arithmetic over whole tables in a few queries and feeds {fn("_accounts_rows")}. A test proves both give identical balances on the real data copy and on the 12,000-trip load database (2 ms vs 10 ms, and 72 ms vs 3,652 ms).'])

    st.append(H2('17.4 Payments and allocation'))
    st.append(P('Recording a payment is more than saving a row: the money is also <b>applied</b> to the things it settles, so the Trips screen, the Maintenance screen, the Expenses screen and the Ledger can never drift apart.'))
    st.append(tbl([['', 'Money received from a party', 'Money paid to a vendor'],
                   ['Function', fn('_insert_party_payment_from_form'), fn('_insert_vendor_payment_from_form')],
                   ['Order of application', '1) the trips the user ticked, in ticked order (each capped at what is still pending); 2) the party\'s other pending trips, oldest first', '1) ticked owner-hire trips (net of fuel/advance the company paid); 2) other pending owner-hire trips; 3) unpaid maintenance, tyre, battery, service and insurance bills; 4) Pending expense bills naming this vendor, oldest first'],
                   ['Surplus', 'A true surplus (more than everything owes, e.g. an advance or opening-balance settlement) stays unallocated and shows as its own ledger line', 'Same'],
                   ['Guards', fn('_validated_payment_basics') + '; ' + fn('_payment_is_duplicate') + ' (same payment within 10 seconds is refused)', 'Same, plus the vendor must exist'],
                   ['What gets written', 'payments row + one payment_allocations row per trip; trips.payment_received increased', 'payments row + allocation rows pointing at a trip OR a maintenance bill OR an expense; paid_amount / status updated'],
                   ['Consistency helpers', fn('_party_pending_trips'), f'{fns("_vendor_pending_trips", "_vendor_pending_maintenance", "_vendor_pending_overheads", "_settle_overhead_status", "_enforce_maintenance_paid_floor")}']], [16, 42, 42], bold_first=True))
    st.append(Spacer(1, 4))
    st += bullets(['<b>Owner dues rule (hired trips):</b> only fuel or advance the <i>company</i> paid (vendor blank or a third party, not the owner) is deducted from the owner\'s dues — one function, ' + fn('_company_paid_on_hired_trip') + ', used by the vendor ledger, the payment popup and the owner invoice.',
                   '<b>Deleting</b> a maintenance bill or expense first removes its allocations ('+ fns('_delete_maintenance_row', '_delete_overhead_row') + '); a bill\'s paid amount can never fall below what ledger payments already allocated.'])
    st.append(PageBreak())

    st.append(H2('17.5 Invoices'))
    st.append(D.d_invoice())
    st.append(tbl([['Topic', 'How it works'],
                   ['Types', 'Party invoice, Hired-vehicle ("Freight Bill") invoice for owners, Tax invoice (with GST) and Bill invoice. The default type, payment terms, place of supply, due days and four display switches (logo, bank details, signatory, amount in words) are Settings.'],
                   ['Invoice Center (' + str(F.ROUTES[[r[2] for r in F.ROUTES].index('invoice_center')][3]) + ')', 'Filters by type, party/vendor, vehicle, LR, dates and invoiced status. Each trip row shows whether it is already on an invoice and which optional charges can be included.'],
                   ['Review gate', 'Before Generate or Draft is possible, every selected trip\'s LR link must have been opened and reviewed; the screen shows a single balloon at the LR column explaining what is missing and the server re-checks <font face="Courier">reviewed_trip_ids</font>. Preview is always allowed.'],
                   ['Charges and deductions', 'Fields that never fed an invoice before (detention, police, SIM tracking, union, brokerage, builty commission, late fees, material damage, shortage, trip TDS, other deductions) are opt-in per trip; opted-out lines are recorded in invoice_batch_charge_exclusions so regenerating gives the same answer.'],
                   ['Numbering', 'invoice_prefix + year + zero-padded next_invoice_number from Settings (e.g. ATS/INV/2026/0004); numbers and dates are validated by ' + fn('_invoice_numbers_error') + '.'],
                   ['PDF', fn('_build_invoice_pdf') + ' (ReportLab) draws letterhead from company settings, party details, trip table, charges, GST (CGST/SGST/IGST), TDS, round-off, amount in words (' + fn('number_to_words_inr') + '), bank details and RCM clause. With more than 14 extra lines it switches to a stacked full-width layout (200-trip invoices once crashed the compact one).'],
                   ['After saving', 'Generated invoices can be edited (' + fn('invoice_batch_edit') + '), have lines added or removed, be deleted, previewed inline or downloaded.']], [22, 78], bold_first=True))
    st.append(Spacer(1, 4))
    st.append(callout('warn', 'Known design difference', 'An invoice grand total can differ from the Ledger\'s billed amount when optional lines (Others, discounts, detention, RCM) are included, because the ledger bills the trip while the invoice may add or remove lines. This is listed as a business decision, not a bug (Chapter 52, Part H).'))

    st.append(H2('17.6 The maintenance family'))
    st.append(P(f'Maintenance is one hub ({fn("maintenance_list")}) with tabs chosen by <font face="Courier">?tab=</font>: Overview, Service, Tyres, Battery, Urea, Toll and Compliance. Every kind of purchase ends as a row in <font face="Courier">maintenance</font> (the bill: amount, paid, vendor, vehicle, km) so the money side is in one place and vendor payments can settle it.'))
    st.append(tbl([['Tab', 'Data it keeps', 'Rules worth knowing'],
                   ['Overview', fn('_maintenance_overview_data'), 'Cards and charts for own vehicles only (hired-vehicle bills are excluded by design); category buckets come from ' + fn('_maintenance_classify') + ' so every tab agrees.'],
                   ['Service', 'maintenance + maintenance_items (checklist)', 'Created by ' + fn('_insert_service_from_form') + '; next-due km/date tracked.'],
                   ['Tyres', 'maintenance (tyre rows) + tyre_stock', 'Buy into stock, fit on a wheel position, scrap; wheel layout by vehicle wheeler count.'],
                   ['Battery', 'batteries + battery_checks', 'Health bands from the latest logged health%: Good ≥70, Weak ≥40, below that Replace Soon, Dead; Mark as Dead is an asserted fact that wins. Warranty end computed from install date + months.'],
                   ['Urea (AdBlue)', 'urea_transactions', 'A stock ledger: purchases add to stock (and create the bill), usage subtracts; using stock already paid for is not a second cost. Running balance is computed live, not stored.'],
                   ['Toll', 'toll_entries (+ receipt files)', 'Manual entry, Excel import (auto-detects a BlackBuck BOSS statement: only the debit "FasTag Recharge" lines count as toll; the temporary credit and its recovery debit and top-ups are ignored; or a plain template), approve/reject. ' + fn('sync_fastag_toll') + ' is currently a <b>mock</b> generator (no live FASTag feed exists yet; it seeds at most once per hour).'],
                   ['Compliance', 'compliance_expenses + vehicle_compliance + insurance_policies', 'Fees for fitness/permit/PUC become expense bills; insurance policies (with documents) are Active / Expiring (within 30 days) / Expired from dates, Cancelled by assertion.']], [14, 30, 56], bold_first=True))
    st.append(PageBreak())

    st.append(H2('17.7 Expenses (overheads)'))
    st.append(P(f'{fn("_overhead_list_data")} powers the list for both website and phone: category, vendor, status and date filters plus search. An expense can be Paid or Pending; a Pending bill that <i>names a vendor</i> appears in that vendor\'s ledger and is settled by vendor payments, oldest first ({fn("_vendor_pending_overheads")}), flipping to Paid when allocations cover it ({fn("_settle_overhead_status")}). Recurring expenses carry a frequency and due date; the app shows overdue items.'))
    st.append(H2('17.8 Employees, salary, advances and attendance'))
    st += bullets([f'Employees (drivers and office staff) are listed in a hub with Overview, Salary and Attendance tabs ({fn("_employees_overview_tab")}, {fn("_employees_salary_tab")}, {fn("_employees_attendance_tab")}).',
                   f'<b>Advances and salary are separate ledgers.</b> {fn("_employee_ledger_entries")} builds Opening balance, Salary paid, Advance given and Advance repaid lines for one employee (matched by name) with a true running <i>advance</i> balance over the whole history; a date filter only limits which rows are shown, never the balance. Salary payments do not change the advance balance.',
                   f'Attendance is one row per employee per day ({fn("mark_attendance")}, {fn("bulk_mark_attendance")}); {fn("download_payslip")} makes a PDF from a salary row (gross, deductions, advance recovery, net).'])
    st.append(H2('17.9 Vehicles, documents and compliance'))
    st += bullets([f'{fn("vehicles_list")} shows each vehicle\'s Insurance / Fitness / PUC / Permit badges using {fn("_expiry_bucket")} (expired, expiring within 30 days, ok) and the pending challan count.',
                   'Values come from three places, in this order of trust: what an office user typed ('+ fn('vehicle_compliance_renew') +'), what the eChallan RC lookup returned (cached in vehicles.rc_synced_data), and mock provider data (labelled as mock) when no key exists.',
                   f'{fn("vehicle_rc_lookup")} shows the cached RC details; {fn("vehicle_sync_now")} fetches fresh RC and challan data for one vehicle (a paid call, only on demand); {fn("_build_rc_pdf")} prints the RC.'])
    st.append(H2('17.10 Company settings, banking, tax'))
    st.append(P(f'All company-level settings live in the <font face="Courier">settings</font> key/value table (one row per key per company): company details, one bank account, invoice preferences and numbering, GST rates, TDS, e-way bill and round-off switches, the RCM clause, the MSG91 and eChallan keys and the sync intervals. {fn("_save_settings_group")} is the single writer used by <b>both</b> the website Settings page and the phone Administrator screens; {fn("_get_all_settings")} and {fn("get_company_name")} read them, always with a company id. Secret values are never printed into a page and a blank box means "keep the saved one".'))
    st.append(H2('17.11 Exports'))
    st.append(P('Excel (openpyxl) exports exist for trips, maintenance, expenses, ledger list, party ledger, vendor ledger, users, performance, route analytics, fleet utilization and business performance, plus a full-database Excel (<font face="Courier">/export</font>, Full Access only). PDF (ReportLab) exports exist for trips, maintenance, party and vendor ledgers, invoices, payslips and RC details. Every export reads the same data functions as the screen, so a file always matches what was on screen.'))
    st.append(PageBreak())

    # ------------------------------------------------------------------ 18
    st.append(H1('18. Reports and analytics'))
    st.append(P('Every analytical screen is a different grouping of the same trips, bills and expenses. The one shared rule is how a trip\'s profit is calculated; it is defined once and reused so Dashboard, Business Performance, Performance, Route Analytics and the phone screens agree.', 'lead'))
    st.append(tbl([['Screen', 'Function', 'What it answers'],
                   ['Dashboard (website / phone)', f'{fn("dashboard")} / {fn("app_dashboard")} using {fn("_dashboard_financials_data")}', 'Revenue, profit, own-vehicle revenue, hired-vehicle profit, fuel, maintenance, receivables and payables, expense mix, trend and top vehicles for a chosen financial year / period and vehicle.'],
                   ['Business Performance', fn('business_performance') + ' with ' + fn('_period_financials'), 'This period versus the previous period, quarter and year (growth percentages) — the owner\'s view.'],
                   ['Performance', fn('performance') + ' with ' + fn('_performance_data'), 'Drivers and vehicles ranked by profit on the full real cost basis (own: fuel, advances, tolls and direct costs; hired: owner cost).'],
                   ['Route Analytics', fn('route_analytics') + ' with ' + fn('_route_analytics_data') + ', ' + fn('_route_rates_data'), 'Best routes by margin or revenue; lowest / average / highest rate per route and vehicle type; plus the Route Finder tab.'],
                   ['Fleet Utilization', fn('fleet_utilization') + ' with ' + fn('_fleet_utilization_data'), 'Active versus idle days per own vehicle (a trip is "active" from start to end date), empty runs, kilometres, fuel and efficiency. Hired vehicles are not this company\'s asset and are excluded.'],
                   ['Trips, Ledger, Expenses lists', 'the list routes', 'Operational lists with the same filters on website and phone.']], [20, 34, 46], bold_first=True))
    st.append(H2('18.1 The profit rule, stated once'))
    st += bullets(['<b>Own vehicle:</b> profit = billed − (fuel + driver advance + direct trip costs + toll). Toll uses the linked Toll-Management amount if one exists, otherwise the trip\'s own estimate — never both.',
                   '<b>Hired vehicle:</b> profit = billed − owner cost. Fuel and advance on a hired trip sit inside the owner freight and are not subtracted again.',
                   '<b>Periods:</b> the financial year runs April to March; "this month / last month / FY" presets are resolved on the server ('+ fn('_resolve_dashboard_period') + ', ' + fn('_current_fy_bounds') + '); a From date after the To date is swapped rather than producing negative days.'])
    st.append(H2('18.2 The Route Finder'))
    st.append(P('The Route Finder (Figure 13) is a live look-up, not stored data. It exists on the website (Route Analytics → Route Finder) and in the phone app. Its endpoints are ' + fns('route_finder_suggest', 'route_finder_search', 'route_finder_extras') + '; the work is in <font face="Courier">providers/osm_routes.py</font> (726 lines).'))
    st.append(D.d_routefinder())
    st.append(PageBreak())

    # ------------------------------------------------------------------ 19
    st.append(H1('19. The phone app (/app/) on the server side'))
    st.append(P(f'The phone app is not a separate program. It is a second set of routes under <font face="Courier">/app/</font> (about 100 of the {F.N_ROUTES}) that use the same helpers as the website but answer with mobile templates, and for actions with small JSON replies the page\'s JavaScript understands.', 'lead'))
    st += bullets(['<b>Same rules, different face.</b> A trip entered on the phone calls the same <font face="Courier">_insert_trip_from_form</font>; a payment calls the same allocation function; the same access checks run in the same hook.',
                   '<b>Own login screens.</b> /app/login, /app/login/otp and /app/signup render their own templates; an expired session on a /app/ URL redirects to the app login, never to the desktop page.',
                   f'<b>JSON for actions.</b> Calls made with fetch() (add vehicle, edit profile, change password, Administrator settings saves, user add/edit/delete via the <font face="Courier">X-App-Request</font> header) get {{ok, error}} answers; the friendly-error handlers return JSON for /app/ requests that do not ask for HTML.',
                   f'<b>The Administrator hub.</b> {fn("app_profile")} shows the full "Profile and Settings" hub only to the Administrator (Company Profile, Banking, Invoice Settings, Tax and Compliance, Users and Roles, with Integrations inside Users); everyone else keeps the simple profile page. The five settings screens and {fn("app_settings_save")} are Administrator-only endpoints.',
                   f'<b>Phone navigation.</b> The bottom bar, + fan and More menu are normally fixed; for Limited users they are built from their screens by {fn("_limited_nav")}.'])
    st.append(callout('note', 'Mobile screens that exist', 'Dashboard · Trips (list, detail, add, edit, end) · Maintenance hub (service, tyres, battery, urea, toll, compliance) · Expenses · Salary and attendance, employee ledger · Vehicles (list, detail, add) · Business Overview hub (analytics and Route Finder, fleet utilization, performance, business performance) · Ledger (list, party, vendor, payments) · Profile. There is no phone invoice screen: invoices are made on the website.'))

    # ------------------------------------------------------------------ 20
    st.append(H1('20. Background jobs'))
    st.append(P(f'Only one thing in the app calls an outside service automatically: the vehicle-document refresh in <font face="Courier">scheduler.py</font> ({341} lines) and <font face="Courier">compliance_service.py</font> ({279} lines). Everything the Vehicles screens show is read from the database; these jobs keep the database current.', 'lead'))
    st.append(D.d_sync())
    st += bullets(['<b>Locally</b> the scheduler starts from <font face="Courier">if __name__ == "__main__"</font> when ENABLE_COMPLIANCE_SCHEDULER=1. Under gunicorn that block never runs (and would start once per worker), so on Render the tick is triggered by an outside cron calling <font face="Courier">POST /internal/sync-tick</font> with the X-Sync-Token header (INTERNAL_SYNC_TOKEN); the endpoint refuses everyone if the token is not configured.',
                   'The intervals are Settings values (RC every 15 days, challan every 3 by default), so a change applies on the next tick with no restart.',
                   'Providers (<font face="Courier">providers/*_provider.py</font>) share one interface: fetch(vehicle_no) returns status, expiry, document number and "source: mock or live". The VAHAN, PUC and permit providers read slices of the single eChallan RC answer, so one paid call serves all three.',
                   'Each real run writes a sync_log row (job, start, finish, synced, failed, skipped) which the Administrator can read under Users and Roles → Integrations.'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 21
    st.append(H1('21. Security architecture'))
    st.append(P('Security is layered so that a single mistake is caught by the next layer (Figure 17). The table lists each realistic threat, the control, where it lives and the test that proves it.', 'lead'))
    st.append(D.d_security())
    st.append(tbl([['Threat', 'Control', 'Where', 'Proof'],
                   ['Anyone can create an account', 'Self sign-up closed (403), link hidden', fn('app_signup'), 'security test: SIGNUP_CLOSED'],
                   ['Guessing passwords', '5 failures per account / 30 per address → 15-minute lock; 8-character minimum', fn('_login_throttle_message'), 'LOGIN_LOCKOUT, APP_LOGIN_LOCKOUT'],
                   ['Faking or recovering the SMS code', 'Server-side keyed hash, 5 tries, limits, unknown numbers look identical', fn('_otp_check'), 'OTP_NOT_IN_COOKIE, OTP_LOCKED_AFTER_5_WRONG'],
                   ['Forging a login cookie', 'Private random secret key; the old fixed key is rejected', fn('_ensure_secret_key'), 'OLD_SECRET_KEY_REJECTED'],
                   ['A malicious page posting forms as a logged-in user', 'SameSite=Lax cookie + Origin/Referer check', fn('reject_cross_site_posts'), 'CROSS_SITE_POST_BLOCKED'],
                   ['Reading private documents by link', 'Insurance and toll-receipt folders need a login', fn('require_login'), 'DOCS_NEED_LOGIN'],
                   ['A user exceeding their rights', 'Roles, Read Only, Limited screens, one Administrator — all on the server', fn('require_login'), 'roles stage (219 checks)'],
                   ['Bad or hostile input', 'Number guard, _parse_money, friendly 400, parameterised SQL, upload cap 16 MB, local-only return links', fn('_reject_non_finite_numbers'), 'fuzz stage (1,200 checks)'],
                   ['Clickjacking / MIME tricks', 'X-Frame-Options, nosniff, Referrer-Policy, HSTS', fn('add_security_headers'), 'SECURITY_HEADERS'],
                   ['Stolen database copy', 'Passwords are salted hashes; secrets are not printed in pages; backups are verified', fn('_save_settings_group'), 'KEYS_NEVER_SHOWN_IN_PAGES'],
                   ['Backup endpoint abuse', 'Token required (refuses everyone when unset), attempt limit, constant-time compare', fn('internal_backup'), 'backup endpoint tests']], [24, 34, 21, 21]))
    st.append(Spacer(1, 5))
    st.append(callout('warn', 'Still open (see the readiness report)', 'Full CSRF tokens (today: SameSite + Origin check), a Content-Security-Policy, encrypting the stored SMS/RC keys at rest, confirming the persistent disk, off-site upload backups, error alerts, pinning package versions, closing the old Twilio account, and removing old database backups from the GitHub history.'))
    st.append(PageBreak())
    return st
