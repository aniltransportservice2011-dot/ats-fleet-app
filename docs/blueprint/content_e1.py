"""Part E, first half: how every action is executed (sign-in, trips, money, expenses)."""
from lib import *
import facts as F
import analysis as A
from flowlib import flow, C, fnl


def ins(tpl, n=60):
    return ', '.join(C(x) for x in F.ui_summary(tpl)['inputs'][:n])


def part_e1():
    st = [NextPageTemplate('divider'), PageBreak(),
          PartDivider('E', 'Execution walkthroughs', 'Every action in the app, followed from the click to the database and back to the screen: what is sent, what is checked, which tables get which columns, and what is shown next.',
                      ['How to read a walkthrough', 'Sign-in and sign-out', 'Trips', 'Payments, ledgers, parties and vendors', 'Expenses', 'Maintenance, vehicles, people, invoices, reports, administration', 'The phone-app twins and background jobs']),
          NextPageTemplate('body'), PageBreak()]

    st.append(H1('27. How to read a walkthrough'))
    st.append(P('Each walkthrough follows one action from the user\'s finger to the database and back. They all share the same skeleton, so once you understand one you can read the others quickly.', 'lead'))
    st.append(tbl([['Part', 'What it tells you'],
                   ['Where it starts', 'The screen, button or form the user uses.'],
                   ['What is sent', 'The URL, the HTTP method and the form fields or query parameters that travel to the server (names as they appear in the code).'],
                   ['Code entry', 'The route function that receives it (with its line number in app.py) and the helpers it calls.'],
                   ['Phone twin', 'The equivalent route used by the phone app, when there is one.'],
                   ['Step by step', 'Exactly what the code does, in order: validate, look up, calculate, write, answer.'],
                   ['What is saved', 'Every table written, whether it is an INSERT (new row), UPDATE (changed row) or DELETE, and which columns receive values.'],
                   ['What is read', 'The tables consulted to make the decision or to build the answer.'],
                   ['What the user sees next', 'The redirect or page returned, and how the saved data shows up on screen later.'],
                   ['Protections and edge cases', 'What is refused, what is repeated safely, and the rules that keep totals honest.']], [24, 76], bold_first=True))
    st.append(H2('27.1 Things every write has in common'))
    st += bullets(['<b>Post, then redirect.</b> Every form is sent as an HTTP POST. After the work is done the server answers with a redirect to a list or ledger page (so pressing Refresh never re-sends the form). Errors travel back as a short message in the address (for example <font face="Courier">?pay_error=...</font>, <font face="Courier">?exp_error=...</font>, <font face="Courier">?end_error=...</font>) and the target page shows it as a banner.',
                   '<b>Who and when.</b> Almost every write records <font face="Courier">created_by</font> / <font face="Courier">updated_by</font> (the signed-in user\'s id from the session) and <font face="Courier">created_at</font> / <font face="Courier">updated_at</font> (server time as YYYY-MM-DD HH:MM:SS). The tables below list only the business columns; read these four as always present on tables that have them.',
                   '<b>Names become records.</b> Typing a new party, vendor or vehicle name on a trip or bill does not stop the user: <font face="Courier">get_or_create_party / get_or_create_vendor / get_or_create_vehicle</font> look the name up and create a row if it is new, and the new id goes onto the transaction.',
                   '<b>One commit.</b> A route opens one connection, performs all its reads and writes, then commits once. If anything fails before the commit nothing is saved.',
                   '<b>Numbers are text until checked.</b> Browsers send everything as text. Amounts pass through <font face="Courier">_parse_money</font> (or float with a guard) and are stored as numbers.',
                   '<b>The same function serves both faces.</b> Wherever a "phone twin" is named, the twin calls the same <font face="Courier">_insert_X_from_form</font> helper, so the saved columns are identical.'])
    st.append(callout('note', 'Where to find the complete lists', 'This Part walks through the important actions in prose. Part F then gives an automatically generated "execution card" for every one of the ' + str(F.N_ROUTES) + ' URLs (inputs, helper chain, tables read and written with columns, page shown), and a "life of each table" listing which URLs create, change, delete and read it.'))
    st.append(PageBreak())

    # ------------------------------------------------------------------ 28 sign-in
    st.append(H1('28. Sign-in and sign-out'))
    st += flow('28.1 Password sign-in',
               'A user opens the website (or the phone app) and signs in with a mobile number or email and a password.',
               'Website /login form (templates/login.html) or phone /app/login (templates/app/login.html).',
               'POST /login or /app/login: ' + C('username') + ', ' + C('password') + ', ' + C('remember_me') + '.',
               f'{fnl("login")} (website) and {fnl("app_login")} (phone).', 'app_login',
               ['The username must look like a 10-digit Indian mobile or an email (' + fnl('_username_login_format_ok') + '), otherwise the page says so before touching the database.',
                'Throttle check: ' + fnl('_login_throttle_message') + ' counts failures for this username and for this visitor address in the last 15 minutes (table ' + C('auth_attempts') + '). Five (account) or thirty (address) means "Too many failed sign-in attempts" with HTTP 429 and the password is not even checked.',
                'The user row is fetched by username and the password is checked against the stored hash with werkzeug.',
                'An Inactive account is refused with a clear message.',
                'On success the failure counter for that username is cleared, the session is filled (user_id, username, is_admin, login_at, a one-time "zoom tip" flag) and made permanent (30 days) only if "remember me" was ticked.',
                'last_login is updated and a row "Login" is added to access_logs; the browser is redirected to the Dashboard (or, for a Limited user without Dashboard, the first allowed screen).',
                'On failure the attempt is recorded for both counters and the page shows "Invalid username or password."'],
               [('users', 'UPDATE', 'last_login, updated_by, updated_at'), ('access_logs', 'INSERT', 'user_id, event = "Login", date'),
                ('auth_attempts', 'INSERT / DELETE', 'failure rows (kind login_fail_user and login_fail_ip, key, time); the user\'s failure rows are deleted after a successful login')],
               ['users (password_hash, status), auth_attempts (recent failures)'],
               ['The signed cookie now identifies the user; every following request re-reads their level, role, status and screens.', 'Access Logs (Administrator only) show the sign-in.'],
               ['Failed attempts against unknown usernames are counted too, so the limit cannot be used to discover which usernames exist.', 'Messages are identical for a wrong username and a wrong password.'])
    st += flow('28.2 Sign-in by SMS code',
               'A user who has an account types only their mobile number and receives a six-digit code by SMS through MSG91.',
               'Website: "Login with OTP" on /login. Phone: /app/login/otp then the verify screen.',
               'POST /login/otp/send (website) or /app/login/otp (phone): ' + C('phone') + '. Then POST /login/otp/verify or /app/login/otp/verify: ' + C('phone') + ', ' + C('otp') + '.',
               f'{fnl("send_login_otp")}, {fnl("_start_otp_login")}, {fnl("verify_login_otp")}, {fnl("_finish_otp_login")}.', 'app_login_otp, app_verify_login_otp',
               ['The number must be a 10-digit Indian mobile. If MSG91 is not configured the page says SMS login is not set up.',
                'Limits are checked (3 codes per number per 15 minutes, 10 per address per hour) and the request is counted.',
                'If a matching active user exists, ' + fnl('_otp_issue') + ' stores a new row in login_otps (older unused codes for that user are marked used), generates six digits with secrets, stores only a keyed hash of id+code, and ' + fnl('_send_otp_msg91') + ' asks MSG91 to send the SMS. A failed send marks the code used and shows "Could not send the code right now".',
                'If no such user exists nothing is sent, but the same "code sent" screen is shown.',
                'On verify, ' + fnl('_otp_check') + ' finds the code row from the session, checks it is unused, unexpired and for this number, counts the try, compares the keyed hash in constant time, and marks the code used on success.',
                'The user is then signed in exactly like a password login (session filled, last_login updated, access_logs "Login (OTP)").'],
               [('login_otps', 'INSERT / UPDATE', 'user_id, phone, otp_hash, expires_at, attempts, used, created_at'), ('auth_attempts', 'INSERT', 'kind otp_send_phone / otp_send_ip, key, time'),
                ('users', 'UPDATE', 'last_login'), ('access_logs', 'INSERT', 'event = "Login (OTP)"')],
               ['settings (msg91_authkey, msg91_template_id), users (phone, status), login_otps'],
               ['After a correct code: the Dashboard. After a wrong code: "Incorrect OTP"; after five wrong codes: "Too many incorrect codes. Please request a new OTP."'],
               ['The browser cookie holds only the code row number, never anything that reveals the code.', 'The SMS text is MSG91\'s DLT-approved template; the app only supplies the digits.'])
    st += flow('28.3 Sign-out and "sign-up"',
               'Leaving the app, and the (closed) public registration page.',
               'Sidebar "Logout" (website) or Profile "Log Out" (phone); /app/signup link (hidden).',
               'GET /logout (with ?ctx=app from the phone); GET or POST /app/signup.', f'{fnl("logout")}, {fnl("app_signup")}.', None,
               ['Logout clears the whole session and redirects to the website login or, when it came from the phone app, to the app login (without replaying the splash animation).',
                'Sign-up checks ' + C('_signup_open()') + ': unless the environment variable ALLOW_SELF_SIGNUP is 1 it renders the app login with "Sign-up is closed. Ask an Administrator to create your account." and HTTP 403; no row is written.'],
               [], [], ['The login page.'], ['If sign-up is ever reopened, new accounts are Staff / Read Only / Active, which can still read every screen — the reason it is closed.'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 29 trips
    st.append(H1('29. Trips'))
    st.append(P('Trips are the heart of the system. Five actions touch them: add, edit, end, delete (guarded) and view; plus the list that everyone looks at.', 'lead'))
    st += flow('29.1 Add a trip',
               'Dispatch enters a new journey: the party, the vehicle, the route, the rate, fuel and advances.',
               'Website: Trips → Add Trip (trip_form.html, a multi-section form with live Trip Summary, estimated profit and payment tracking). Phone: Add Trip (+ menu) → app/trip_add.html wizard.',
               'POST /trips/add (phone: /app/trips/add). Fields: ' + ins('trip_form.html') + ', plus repeating rows ' + ', '.join(C(x) for x in ('other_desc', 'other_type', 'other_rate', 'other_qty', 'other_vendor')) + ' for extra lines.',
               f'{fnl("add_trip")} → {fnl("_duplicate_lr_trip")} → {fnl("_trip_form_error")} → {fnl("_insert_trip_from_form")}.', 'app_add_trip',
               ['The LR number is checked for duplicates (case-insensitive, ignoring blanks). A clash re-shows the form with "LR Number ... is already used on trip #N (date, vehicle)".',
                '_trip_form_error validates every one of the 40 money fields with _parse_money, refuses a negative computed bill and (for a NEW trip) a party advance larger than the bill.',
                '_insert_trip_from_form resolves names: vehicle (get_or_create_vehicle, which also creates a vehicle row if the number is new), party, fuel vendor, driver-advance vendor, owner vendor (only if an owner name is typed), misc vendor. The trip type (own or hired) follows the vehicle.',
                'The freight is computed by _trip_freight: FIXED → fixed_rate_amount; otherwise (guarantee_qty if non-zero, else quantity) × rate.',
                'billed_amount = freight + (detention + GPS + loading + unloading + police + SIM tracking + union + weight + other charges) − (brokerage + builty commission + late fees + material damage + shortage amount + TDS + other deductions).',
                'One INSERT into trips writes every business column. Then _save_trip_custom_items replaces the trip\'s "Others" lines in invoice_items (rate × quantity becomes the stored amount; an optional vendor name is resolved to vendor_id).',
                'The route commits and redirects to the Trips list.'],
               [('trips', 'INSERT', 'date, lr_number, vehicle_id, type, party_id, from_loc, to_loc, quantity, guarantee_qty, rate, driver_name, material, rate_type, billed_amount, the 9 charge columns, the 7 deduction columns (incl. shortage_qty), fuel_amount, fuel_vendor_id, fuel_liters, fuel_price, driver_adv_amount, driver_adv_vendor_id, party_advance, payment_received, owner_name, fixed_rate_amount, owner_rate, owner_rate_type, owner_fixed_amount, paid_to_owner, owner_vendor_id, the own-vehicle cost columns (agent_commission, builty_expense, fine, labour_charges, parking, puncture, toll, urea, loading_expense, unloading_expense, weighbridge_charges, other_expense), misc_vendor_id, lr_received, is_empty, created_by, created_at'),
                ('invoice_items', 'DELETE then INSERT', 'for this trip: description, amount (rate × qty), item_type (charge or deduction), vendor_id, created_by, created_at'),
                ('parties / vendors / vehicles', 'INSERT (only if the typed name is new)', 'name (+ type for a vehicle)')],
               ['trips (duplicate LR check), parties, vendors, vehicles (name lookups), employees (driver name suggestions on the form)'],
               ['The new trip appears at the top of Trips (newest date first) with its LR, vehicle, party, route, Billed and Pending (= billed − payment received − party advance).',
                'It immediately feeds the party\'s ledger (a Trip Bill line), the Dashboard totals, Route Analytics, Performance and Fleet Utilization.',
                'Columns owner_amount, pending_to_owner, driver_payment, conductor_expense and wear_tear exist in the table but are not written by the current form: the owner\'s amount is computed when needed from owner_rate/owner_fixed_amount and quantity.'],
               ['A trip on a hired vehicle needs an owner name to appear in the owner\'s (vendor) ledger.', 'The vehicle cannot be changed later (locked at creation).'])
    st += flow('29.2 Edit a trip',
               'Someone corrects a rate, adds an advance or fixes a route after the trip was entered.',
               'Trips list → row → Edit (opens trip_form.html pre-filled). Phone: trip detail → Edit.',
               'POST /trips/edit/<id> (phone /app/trips/<id>/edit) with the same fields as Add (the vehicle field is ignored).',
               f'{fnl("edit_trip")} → {fnl("_update_trip_from_form")}.', 'app_edit_trip',
               ['Duplicate-LR check, excluding this trip itself. The "advance larger than bill" check is skipped on edits (a rate correction can legitimately leave more received than billed).',
                '_update_trip_from_form reads the trip\'s existing vehicle_id (never changed), recomputes the type, resolves the same names, recomputes freight and billed_amount exactly like Add.',
                'Two floors protect money already allocated: payment_received can never be saved below the sum of party payments already allocated to this trip, and paid_to_owner never below owner payments allocated (SELECT SUM(amount) from payment_allocations joined to payments).',
                'One UPDATE rewrites all business columns; the custom invoice_items are replaced again; commit; redirect to the list (or to return_to).'],
               [('trips', 'UPDATE', 'all columns listed under Add, except vehicle_id and the created_* fields; updated_by, updated_at'), ('invoice_items', 'DELETE then INSERT', 'same as Add'),
                ('parties / vendors', 'INSERT (only if a typed name is new)', 'name')],
               ['trips (existing vehicle_id), payment_allocations + payments (floors), vendors, parties'],
               ['The list, the party ledger and every total show the new numbers on the next page load (nothing is cached).'],
               ['A plain re-save can never reduce what a real payment already settled, so a ledger "Trip Bill" line cannot look like it double-subtracts a payment.'])
    st += flow('29.3 End a trip',
               'When the truck is delivered the trip is closed with the actual end date, kilometres and any shortage.',
               'Trips list → End Trip button (modal "End Trip"), or the phone trip detail End Trip sheet.',
               'POST /trips/<id>/end: ' + ', '.join(C(x) for x in ('end_date', 'end_time', 'actual_km', 'lr_received', 'freight_amount', 'shortage_qty', 'shortage_unit', 'shortage_amount', 'shortage_remarks', 'remarks')) + '.',
               f'{fnl("end_trip")} → {fnl("_end_trip_from_form")}.', 'app_end_trip',
               ['The trip must exist and must not already have an end_date (once ended, no further edits).',
                'End date is required; freight amount, shortage quantity, shortage amount and kilometres are parsed with _parse_money.',
                'The Freight Amount box is pre-filled with the trip\'s GROSS freight, so the new bill is simply freight_amount − shortage_amount. If that is negative the end is refused.',
                'One UPDATE closes the trip.'],
               [('trips', 'UPDATE', 'end_date, end_time, actual_km, lr_received, shortage_qty, shortage_unit, shortage_amount, shortage_date (= end date when there is a shortage), shortage_remarks, remarks, billed_amount (= freight − shortage), updated_by, updated_at')],
               ['trips'],
               ['The row moves from "Active" to completed; the Pending balance reflects the new billed amount; Fleet Utilization counts the trip as active from its start date through its end date.'],
               ['The shortage is taken off exactly once (earlier versions deducted it twice).', 'Errors come back as ?end_error=... shown above the list.'])
    st += flow('29.4 Delete a trip',
               'Removing a trip entered by mistake.',
               'Trips list → Delete (confirmation modal).',
               'POST /trips/delete/<id>.',
               f'{fnl("delete_trip")}.', None,
               ['Counts payment_allocations rows for the trip: if any exist the delete is refused (redirect with error=has_payment, the amount and the LR) because removing the trip would silently lose track of that money.',
                'Counts toll_entries linked to the trip: if any exist the delete is refused (error=has_toll).',
                'Otherwise one DELETE FROM trips; commit.'],
               [('trips', 'DELETE', 'the row')],
               ['payment_allocations, toll_entries'],
               ['Back on the list, a red message explains a refusal ("This trip has Rs X already received/paid against it ... reverse the payment first").'],
               ['Observed gap: only the trips row is removed. The trip\'s invoice_items (its "Others" lines) and any invoice_batch_trips link are left behind and become orphans.'])
    st += flow('29.5 The Trips list (how trips are retrieved and shown)',
               'The screen everyone opens first.',
               'Sidebar → Trips (/trips).',
               'GET /trips with optional filters ' + ', '.join(C(x) for x in ('vehicle', 'party', 'date_from', 'date_to', 'status', 'lr_received', 'lr_number', 'from_loc', 'to_loc', 'type', 'sort', 'dir', 'page', 'per_page')) + '.',
               f'{fnl("trips_list")} → templates/trips_list.html.', 'app_trips (templates/app/trips.html)',
               ['Builds one SELECT over trips LEFT JOIN vehicles LEFT JOIN parties and appends an AND clause for each filter that was given (LIKE for vehicle, party, LR, from, to; comparisons for dates; IS NULL / IS NOT NULL on end_date for active/completed; the LR-received tests).',
                'Runs five aggregate queries over the same WHERE clause: total billed, count, pending (SUM of billed − payment_received − party_advance), active count, LR-received count.',
                'Pagination: default 50 per page (10/25/50/100 allowed). Sorting uses a whitelist (date or party) so the query string can never inject SQL; the page is cut with LIMIT/OFFSET.',
                'Dropdown lists of active vehicles and parties are read for the filter boxes; the template is rendered.'],
               [], ['trips, vehicles, parties'],
               ['Five cards: Total Trips, Total Billed, Pending Balance, Active Trips, LR Pending.', 'A table with columns ' + ', '.join(C(c) for c in F.ui_summary('trips_list.html')['columns']) + ' and per-row End Trip / Delete / Edit actions.', 'Sort arrows on Date and Party keep every filter in the address; Excel/PDF export buttons use the same filters.'],
               ['All search text is passed as a bound parameter; only whitelisted column names are placed in the SQL text.'])
    st += flow('29.6 View one trip',
               'A read-only look at everything about a trip, opened from a ledger line.',
               'Ledger row "Trip Bill" → centred popup (website) or trip detail page (phone).',
               'GET /trips/<id>/view (phone /app/trips/<id>).', f'{fnl("trip_view")} → {fnl("_trip_detail_data")}.', 'app_trip_detail',
               ['One SELECT joins trips with vehicles (number, status, body, capacity, cached RC data), parties and the three vendor names; the trip\'s invoice_items are read.',
                'Computed in Python: the owner\'s amount for hired trips (fixed amount or rate × quantity), additions total (charges + "charge" lines), deductions total (deductions + "deduction" lines) and balance_due = billed − payment received − party advance.'],
               [], ['trips, vehicles, parties, vendors, invoice_items'],
               ['Sections for route and parties, rate and bill, charges, deductions, fuel and advance, payment tracking, owner terms for hired trips and the vehicle\'s RC summary.'], None)
    st.append(PageBreak())

    # ------------------------------------------------------------------ 30 money
    st.append(H1('30. Money: payments, ledgers, parties and vendors'))
    st.append(P('This chapter holds the most important logic in the app. If you read only one chapter of Part E, read this one together with Figure 6.', 'lead'))
    st += flow('30.1 Record money received from a party',
               'A customer pays Rs 50,000. The clerk records it so that the right trips are marked as paid and the ledger balance falls.',
               'Party ledger → "Add Payment" popup (it lists the party\'s pending trips with tick boxes). Phone: party ledger → payment sheet.',
               'POST /payment/party/<party_id>: ' + ', '.join(C(x) for x in ('date', 'amount', 'mode', 'reference_id', 'remarks')) + ', ' + C('trip_ids') + ' (one per ticked trip).',
               f'{fnl("add_party_payment")} → {fnl("_insert_party_payment_from_form")}.', 'app_add_party_payment',
               ['The party must exist. ' + fnl('_validated_payment_basics') + ' requires a valid ISO date, an amount greater than zero and a mode.',
                fnl('_payment_is_duplicate') + ': the same party, date, amount, mode and reference within the last 10 seconds is treated as a double-click and refused.',
                'INSERT one row into payments (payment_type = "received", party_id, amount, mode, reference_id, remarks).',
                fnl('_party_pending_trips') + ' computes each of the party\'s trips still owing: pending = billed_amount − payment_received − party_advance, only where pending > 0.004, oldest first.',
                'The order of settlement is: the ticked trips that really belong to this party (in ticked order), then every other pending trip oldest first.',
                'For each trip in that order: alloc = min(pending, money left). UPDATE trips SET payment_received = payment_received + alloc; INSERT a payment_allocations row (payment_id, trip_id, alloc). Stop when the money is used up.',
                'UPDATE payments SET allocated_amount = total allocated. Anything left (a real surplus, e.g. an advance or an opening-balance settlement) stays unallocated and appears as its own ledger line.',
                'Commit; redirect to the party ledger.'],
               [('payments', 'INSERT, then UPDATE', 'date, payment_type = received, amount, party_id, mode, reference_id, remarks; then allocated_amount'),
                ('payment_allocations', 'INSERT (one per trip)', 'payment_id, trip_id, amount'), ('trips', 'UPDATE (one per settled trip)', 'payment_received += allocation, updated_by, updated_at')],
               ['parties, trips (pending), payments (duplicate check)'],
               ['Party ledger: new lines "Payment received — Applied to LR ..." (credit) and a lower balance; the Trips list shows a smaller Pending for each settled trip; Dashboard receivables fall.',
                'Errors come back as ?pay_error=... in a red banner above the ledger.'],
               ['Because the same allocation rows feed the trip list and the ledger, the two cannot drift apart.', 'A trip that was already paid in full is never over-allocated.'])
    st += flow('30.2 Record money paid to a vendor',
               'The company pays a truck owner, fuel pump, workshop or any vendor, and the payment settles what is owed to them.',
               'Vendor ledger (or a linked party\'s merged ledger) → "Add Payment".',
               'POST /payment/vendor/<vendor_id>: date, amount, mode, reference_id, remarks, trip_ids.',
               f'{fnl("add_vendor_payment")} → {fnl("_insert_vendor_payment_from_form")}.', 'app_add_vendor_payment',
               ['Same validation and duplicate guard as 30.1; INSERT payments with payment_type = "paid" and vendor_id.',
                'Step A — owner-hire trips: ' + fnl('_vendor_pending_trips') + ' lists trips where this vendor is the owner and something is still owed: owed = owner amount − paid_to_owner − fuel/advance the COMPANY paid on his behalf (see _company_paid_on_hired_trip). Ticked trips first, then oldest. UPDATE trips SET paid_to_owner += allocation; INSERT payment_allocations(trip).',
                'Step B — unpaid bills: ' + fnl('_vendor_pending_maintenance') + ' lists maintenance, tyre, battery, service and insurance bills of this vendor where amount − paid_amount > 0. UPDATE maintenance SET paid_amount += allocation; INSERT payment_allocations(maintenance_id).',
                'Step C — pending expense bills: ' + fnl('_vendor_pending_overheads') + ' lists Pending expenses whose vendor text equals this vendor\'s name, minus what is already allocated. INSERT payment_allocations(overhead_id); ' + fnl('_settle_overhead_status') + ' flips the expense to Paid once fully covered (and clears its due date).',
                'UPDATE payments SET allocated_amount; commit; redirect to the vendor ledger. A surplus stays unallocated.'],
               [('payments', 'INSERT, then UPDATE', 'payment_type = paid, vendor_id, amount, mode, reference_id, remarks; allocated_amount'),
                ('payment_allocations', 'INSERT (one per target)', 'payment_id and exactly one of trip_id, maintenance_id, overhead_id; amount'),
                ('trips', 'UPDATE', 'paid_to_owner += allocation'), ('maintenance', 'UPDATE', 'paid_amount += allocation'), ('overheads', 'UPDATE', 'status → Paid, due_date → NULL when fully allocated')],
               ['vendors, trips, maintenance, overheads, payment_allocations'],
               ['Vendor ledger shows the payment line(s) and a smaller payable; the Maintenance tab shows the bill as paid; Expenses → Pending Bills shrink; the vendor payment popup shows the new pending amounts.'],
               ['The database CHECK guarantees an allocation points at exactly one of the three targets.'])
    st += flow('30.3 View a payment',
               'Opening the detail of one recorded payment.', 'Ledger line "Payment In/Out" → side panel.', 'GET /payments/<id>/view.', fnl('payment_view'), None,
               ['Reads the payment, the party or vendor name, and its allocations from three joins (to trips, to maintenance, to overheads).', 'The unallocated leftover is amount − allocated_amount (never negative).'],
               [], ['payments, parties or vendors, payment_allocations, trips, maintenance, overheads'],
               ['A summary of date, amount, mode, reference, remarks, which LRs / bills / expenses it settled and any leftover, with a link back to the ledger.'], None)
    st += flow('30.4 The Ledger list (all parties and vendors)',
               'Who owes what, at a glance.', 'Sidebar → Ledger (/accounts).',
               'GET /accounts with ' + ', '.join(C(x) for x in ('search', 'role', 'group', 'status', 'page', 'per_page')) + '.', f'{fnl("accounts")} → {fnl("_accounts_rows")} → {fnl("_bulk_balances")}.', 'app_ledger',
               ['Reads all parties and all vendors (vendors that are linked to a party are excluded because they are merged into that party).',
                '_bulk_balances computes every balance in a handful of queries: allocation totals per trip / bill / expense, per-payment totals, then trips, payments, maintenance, expenses, invoice items and opening balances grouped in Python — the same arithmetic as the detailed ledger, proven identical by a test.',
                'Rows are sorted by name, filtered by search / role / group / status, then totals are computed: receivable (sum of positive balances) and how many parties owe, payable (sum of negative balances) and how many vendors, net.',
                'Pagination 10 per page by default.'],
               [], ['parties, vendors, trips, payments, payment_allocations, maintenance, overheads, invoice_batches, invoice_batch_items, invoice_items'],
               ['Cards for parties, vendors, receivable, payable and net; a table of accounts with a coloured balance (green receivable, red payable) and links to each ledger; tabs All / Parties / Vendors; search and group filters; add-account and Excel export.'],
               ['Linked party/vendor pairs appear once, under the party, with the merged balance.'])
    st += flow('30.5 One party\'s ledger',
               'The account statement of a customer: opening balance, every trip bill, every payment, running balance.',
               'Ledger list → a party (/ledger/party/<id>).', 'GET with ' + ', '.join(C(x) for x in ('date_from', 'date_to', 'tab', 'txn_type', 'type', 'page', 'per_page')) + '.',
               f'{fnl("party_ledger")} → {fnl("_get_party_ledger_entries")} → {fnl("_ledger_page_context")}.', 'app_party_ledger',
               ['Reads the party row and its pending trips (for the payment popup).',
                '_get_party_ledger_entries builds the lines: Opening Balance; one "Trip Bill" per trip (debit = billed, credit = the part of payment_received that did NOT come from a ledger payment, plus party_advance); one "Payment In" line per allocation (and one for any unallocated leftover); and, when a vendor is linked, all of that vendor\'s lines merged in.',
                'Lines are sorted by date, a running balance (debit − credit) is added, and the list is reversed so the newest is on top.',
                '_ledger_page_context applies date range, transaction type, vehicle type and tab (receivables / payables) filters in Python and paginates.'],
               [], ['parties, trips, payments, payment_allocations, invoices (for real invoice numbers), maintenance, overheads, invoice items, vendors (linked)'],
               ['Header with contact, GSTIN, credit limit, opening balance and Edit/Opening-balance actions; a table of Date, Detail, Ref, Debit, Credit, Balance; clicking a Trip Bill opens the trip popup, a Payment line opens the payment panel; Add Payment and Excel/PDF export buttons.'],
               ['Dates are normalised (a From after a To is swapped).'])
    st += flow('30.6 Add a party or vendor; edit contact; opening balance; activate/deactivate',
               'Creating the people the business deals with and correcting their details.',
               'Ledger → Add Account; ledger header → Edit contact / Opening balance; list → toggle.',
               'POST /accounts/add: ' + ', '.join(C(x) for x in ('role', 'name', 'contact', 'email', 'address', 'category', 'gstin', 'credit_limit', 'opening_balance')) + '. POST /ledger/<party|vendor>/<id>/contact. POST /ledger/<type>/<id>/opening-balance. POST /accounts/<role>/<id>/toggle-status.',
               f'{fnl("add_account")} → {fnl("_create_account_from_form")}; {fnl("update_contact")}; {fnl("update_opening_balance")} → {fnl("_update_ledger_opening_balance_from_form")}; {fnl("toggle_account_status")}.', 'app_add_account, app_update_ledger_opening_balance',
               ['Create: a blank name does nothing; credit limit and opening balance go through _parse_money (opening balance may be negative); a nonsense amount creates nothing. role = Vendor inserts into vendors, anything else into parties. A duplicate name hits the UNIQUE constraint and is ignored.',
                'Contact edit: UPDATE address, contact, email, credit_limit, gstin, category and status (only Active or Inactive accepted) on the chosen table.',
                'Opening balance: requires a number (negative allowed) and an optional valid "as of" date; UPDATE opening_balance, opening_balance_date.',
                'Toggle: flips Active ↔ Inactive.'],
               [('parties', 'INSERT / UPDATE', 'name, contact, email, address, credit_limit, opening_balance, category, gstin; later status, opening_balance_date'), ('vendors', 'INSERT / UPDATE', 'same columns for a vendor')],
               ['parties / vendors'],
               ['The new account appears in the Ledger list; the opening balance becomes the first line of its ledger and moves the balance immediately; inactive accounts disappear from entry dropdowns (autocomplete lists only Active records).'],
               ['The opening balance is a signed number: positive means they owed the company before the app was used.'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 31 expenses
    st.append(H1('31. Expenses'))
    st += flow('31.1 Add, edit and delete an expense',
               'Recording money spent on things that are not a trip or a maintenance bill (office, rent, fuel for non-trip use, etc.), or a bill still to be paid to a vendor.',
               'Expenses → Add Expense modal; row → Edit / Delete. Phone: + menu → Add Expense.',
               'POST /overheads/add or /overheads/edit/<id>: ' + ins('overheads_list.html', 18) + '. POST /overheads/delete/<id>.',
               f'{fnl("add_overhead")} → {fnl("_insert_overhead_from_form")}; {fnl("edit_overhead")} → {fnl("_update_overhead_from_form")}; {fnl("delete_overhead")} → {fnl("_delete_overhead_row")}.', 'app_add_overhead, app_edit_overhead',
               ['Add: date, category and a positive amount are required. Status defaults to Paid. If the status is Pending and a vendor name is typed, the vendor is created if new so the bill has a ledger to land in. INSERT into overheads (due date only kept when Pending; recurring frequency only when the recurring box is ticked).',
                'Edit: the same field set is rewritten. If ledger payments were already applied to the bill, the vendor cannot be changed and the amount cannot be lowered below what was paid. After the UPDATE, _settle_overhead_status marks it Paid when fully covered.',
                'Delete: every payment allocation to this expense is removed and the payment\'s allocated_amount is reduced by that amount (the payment stays recorded and becomes partly unallocated again); then the expense row is deleted.'],
               [('overheads', 'INSERT / UPDATE / DELETE', 'date, category, amount, notes, payment_mode, receipt_number, vendor, description, status, is_recurring, recurring_frequency, due_date'),
                ('vendors', 'INSERT (new vendor, Pending only)', 'name'), ('payment_allocations', 'DELETE (on delete)', 'rows for this expense'), ('payments', 'UPDATE (on delete)', 'allocated_amount reduced')],
               ['overheads, payment_allocations'],
               ['The list shows the new row; KPI cards (total, paid, pending, recurring), category and vendor summaries, donut and trend update; a Pending bill with a vendor appears in that vendor\'s ledger and in the payment popup.', 'A refused edit shows ?exp_error=... above the list.'], None)
    st += flow('31.2 The Expenses list',
               'Where all expense bills are reviewed.', 'Sidebar → Expenses (/overheads).', 'GET with category, vendor, status, date range, tab and page filters.',
               f'{fnl("overheads_list")} → {fnl("_overhead_list_data")}.', 'app_expenses',
               ['One SELECT over overheads with the chosen filters; an additional per-row sum of allocations from payment_allocations (so a part-paid bill shows what is left).', 'Python builds KPI totals, per-category and per-vendor summaries, the monthly trend and the donut gradient (' + fnl('_conic_gradient') + ').'],
               [], ['overheads, payment_allocations, vendors'],
               ['Cards, summary tabs, a table of bills with status chips and actions, and charts drawn as CSS/SVG.'], None)
    st.append(PageBreak())
    return st
