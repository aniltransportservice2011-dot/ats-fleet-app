"""Part E, second half: maintenance, vehicles, people, invoices, reports, administration, phone twins, operations."""
from lib import *
import facts as F
import analysis as A
from flowlib import flow, C, fnl


def R(name):
    c = A.ROUTE_CLOSURE[name]
    return ', '.join(C(t) for t in sorted(c['reads']))


def ins(tpl, n=60):
    return ', '.join(C(x) for x in F.ui_summary(tpl)['inputs'][:n])


def part_e2():
    st = []
    # ------------------------------------------------------------------ 32 maintenance
    st.append(H1('32. Maintenance: service, tyres, batteries, urea, tolls, compliance fees and insurance'))
    st.append(P('Every purchase in this family follows one pattern: it creates (or updates) a row in <font face="Courier">maintenance</font> — the bill — and, for kinds that have extra detail, a second row in a specialised table that points back with <font face="Courier">maintenance_id</font>. The bill is what vendor payments settle; the specialised row is what the Tyres, Battery, Urea, Toll or Insurance tab shows.', 'lead'))
    st.append(tbl([['Kind', 'Bill row (maintenance.category)', 'Detail row', 'Link'],
                   ['Service', 'category = service type, service_type set', 'maintenance_items (checklist and item lines)', 'maintenance_items.maintenance_id'],
                   ['Tyre bought and fitted', 'Tyres, tyre_action, tyre_id, tyre_brand, tyre_position', 'none (the bill row is the record)', '—'],
                   ['Tyre bought for stock', 'Tyres, tyre_action = Stock Purchase, no vehicle', 'tyre_stock (In Stock)', 'tyre_stock.maintenance_id'],
                   ['Battery', 'Battery', 'batteries + battery_checks', 'batteries.maintenance_id'],
                   ['Urea purchase / direct fill', 'Urea', 'urea_transactions (stock_in or direct)', 'urea_transactions.maintenance_id'],
                   ['Toll', 'Toll (paid = amount)', 'toll_entries', 'toll_entries.maintenance_id'],
                   ['Compliance fee', 'the compliance type (fitness, permit, PUC, other)', 'compliance_expenses', 'compliance_expenses.maintenance_id'],
                   ['Insurance policy', 'Insurance (amount = premium)', 'insurance_policies', 'insurance_policies.maintenance_id']], [20, 31, 28, 21], bold_first=True))
    st.append(Spacer(1, 6))
    st += flow('32.1 Add or edit a service bill',
               'A truck goes to a workshop; the bill and what was done are recorded.',
               'Maintenance → Service tab → Add Service (modal with a checklist and repeating item rows). Phone: + → Add Maintenance → Service.',
               'POST /maintenance/service/add or /maintenance/service/edit/<id>: ' + ', '.join(C(x) for x in ('vehicle_no', 'service_type', 'date', 'vendor_name', 'amount', 'paid_amount', 'km_reading', 'next_due_km', 'next_service_date', 'invoice_no', 'invoice_date', 'status', 'notes', 'checklist')) + ' and repeating ' + ', '.join(C(x) for x in ('item_name', 'item_category', 'item_qty', 'item_unit', 'item_rate')) + '.',
               f'{fnl("add_service")} → {fnl("_insert_service_from_form")} → {fnl("_save_service_items")}; {fnl("edit_service")} → {fnl("_update_service_from_form")}.', 'app_add_service, app_edit_service',
               ['Vehicle, service type, date and total amount are required (the add route redirects to the Service tab either way).',
                'The vehicle number is resolved (a new number creates a vehicle row without a type); the workshop is resolved with get_or_create_vendor and is optional.',
                'INSERT into maintenance with category and service_type both set to the chosen service type, amount, paid_amount, km, next due km/date, invoice number/date, status (default Completed) and the ticked checklist as comma-separated text.',
                '_save_service_items deletes the bill\'s old maintenance_items and inserts one row per named item (qty × rate becomes amount).',
                'Edit rewrites the same columns, then _enforce_maintenance_paid_floor lifts paid_amount if ledger payments already applied more.'],
               [('maintenance', 'INSERT / UPDATE', 'date, vehicle_id, category, service_type, amount, paid_amount, vendor_id, notes, km_reading, next_due_km, next_service_date, invoice_no, invoice_date, status, checklist_done'),
                ('maintenance_items', 'DELETE then INSERT', 'maintenance_id, item_name, category, qty, unit, rate, amount'), ('vendors / vehicles', 'INSERT if new', 'name / number')],
               ['vehicles, vendors, payment_allocations (paid floor)'],
               ['The Service tab lists the bill with workshop, amount, paid, balance and status; the Overview cards and charts include it; the workshop\'s vendor ledger shows a payable for the unpaid part.'], None)
    st += flow('32.2 Tyres: buy and fit, buy for stock, fit from stock, scrap, delete',
               'Tyres can be bought and fitted at once, or bought ahead into stock and fitted later.',
               'Maintenance → Tyres tab (vehicle wheel layout diagram, stock table, entries table).',
               'POST /maintenance/tyre/add (with optional stock_id), /maintenance/tyre/edit/<id>, /maintenance/tyre/stock/add, /maintenance/tyre/stock/<id>/install, /scrap, /delete.',
               f'{fnl("add_tyre")} → {fnl("_insert_tyre_from_form")}; {fnl("_insert_tyre_stock_from_form")}; {fnl("_install_tyre_stock_from_form")}; {fnl("edit_tyre")}; {fnl("scrap_tyre_stock")}; {fnl("delete_tyre_stock")}.', 'app_add_tyre, app_add_tyre_stock, app_install_tyre_stock',
               ['Buy and fit: vehicle, action, date, vendor (required) and amount are validated; one maintenance row (category Tyres) holds the cost, supplier, tyre id/brand, position and action.',
                'Buy for stock: one maintenance row with no vehicle and tyre_action = Stock Purchase, then one tyre_stock row (status In Stock) linked by maintenance_id.',
                'Fit from stock: the SAME maintenance row is updated (vehicle, position, action = New Tyre Fitted, km) and tyre_stock becomes Installed with vehicle, position and date — the supplier is not billed twice. add_tyre with a stock_id does the same.',
                'Edit: updates the bill and also mirrors vehicle/position into tyre_stock so the wheel layout (reads maintenance.tyre_position) and the stock table (reads tyre_stock.installed_position) cannot disagree.',
                'Scrap: sets a stock tyre to Scrapped. Delete: allowed only for In Stock tyres; removes the stock row then the bill (and its allocations).'],
               [('maintenance', 'INSERT / UPDATE / DELETE', 'date, vehicle_id, category = Tyres, amount, paid_amount, vendor_id, notes, km_reading, invoice_no/date, tyre_action, tyre_id, tyre_brand, tyre_position, status'),
                ('tyre_stock', 'INSERT / UPDATE / DELETE', 'maintenance_id, tyre_id, brand, tyre_type, purchase_date, purchase_cost, vendor_id, invoice_no, status (In Stock, Installed, Scrapped), installed_vehicle_id, installed_position, installed_date, notes')],
               ['tyre_stock, vehicles, vendors, payment_allocations'],
               ['The Tyres tab shows KPI cards, the stock table with Fit / Scrap / Delete actions, the entries table and a wheel layout per vehicle (positions depend on the vehicle\'s wheel count).'], None)
    st += flow('32.3 Batteries',
               'A battery is bought, fitted to a truck (or put in stock), checked from time to time and eventually marked dead.',
               'Maintenance → Battery tab.', 'POST /maintenance/battery/add (mode install or stock), /<id>/install, /<id>/check, /<id>/dead, /<id>/delete.',
               f'{fnl("add_battery")} → {fnl("_insert_battery_from_form")}; {fnl("install_battery")}; {fnl("check_battery")}; {fnl("mark_battery_dead")}; {fnl("delete_battery")}.', 'app_add_battery, app_install_battery',
               ['Add: brand, capacity (Ah), type, purchase date, warranty months, price and vendor are required (plus vehicle and installation date in install mode). A bill goes into maintenance (category Battery); the battery gets the next number from _next_battery_no; a batteries row stores the details.',
                'Two history rows are written to battery_checks: "Purchased" and, when fitted, "Installed in <vehicle>" with the health, voltage and temperature typed.',
                'Install later: updates batteries (vehicle, location, install date, last checked), the linked maintenance row\'s vehicle, and adds a history row.',
                'Health check: updates the battery\'s health, voltage, temperature and last-checked date and adds a "Health Check" history row. Mark dead: status_override = Dead plus a "Marked Dead" row.',
                'Delete: only for unfitted batteries; deletes checks, the battery and its bill.',
                'Status shown to the user is derived: Dead/override wins, In Stock if no vehicle, else Good ≥70% health, Weak ≥40%, Replace Soon below.'],
               [('maintenance', 'INSERT / UPDATE / DELETE', 'category = Battery, amount = price, paid_amount, vendor_id, vehicle_id, invoice_no'), ('batteries', 'INSERT / UPDATE / DELETE', 'battery_no, brand, model, capacity_ah, battery_type, voltage_rating, serial_no, vehicle_id, installed_location, install_date, purchase_date, purchase_price, vendor_id, invoice_no, warranty_months, maintenance_id, health_pct, voltage, temp_c, last_checked_date, status_override, notes'),
                ('battery_checks', 'INSERT / DELETE', 'battery_id, date, event, health_pct, voltage, temp_c, remarks')],
               ['batteries, battery_checks, vehicles, vendors'],
               ['The Battery tab: cards (fitted, in stock, weak soon, dead, average health), a table with warranty end date and status, and a history drawer per battery.'], None)
    st += flow('32.4 Urea (AdBlue)',
               'Urea is bought into a tank (stock in), drawn from stock into a truck (stock out) or bought and poured directly (direct).',
               'Maintenance → Urea tab → Add Urea (one modal, three modes).', 'POST /maintenance/urea/add with mode = stock_in, stock_out or direct; fields ' + ', '.join(C(x) for x in ('date', 'quantity_l', 'unit_price', 'paid_amount', 'supplier_name', 'invoice_no', 'batch_no', 'vehicle_no', 'location', 'odometer_km', 'notes')) + '. Edit/Delete: /maintenance/urea/<id>/edit, /delete.',
               f'{fnl("add_urea")} → {fnl("_insert_urea_from_form")}; {fnl("edit_urea")}; {fnl("delete_urea")}.', 'app_add_urea',
               ['Quantity must be positive. "Price" is the real total paid (it is not multiplied by quantity).',
                'stock_in: a maintenance row (category Urea, no vehicle) holds the cost and a urea_transactions row (txn_type stock_in, source stock) records litres, batch, supplier, invoice and location.',
                'stock_out (from stock): no cost row (already paid). First the current stock is computed from the whole ledger (purchases minus stock-sourced uses) rounded to 0.01 L; asking for more than is on hand is refused with "insufficient stock" and the available litres. Otherwise one urea_transactions row (stock_out, source stock) with the vehicle and odometer.',
                'direct: a maintenance row for the vehicle plus a urea_transactions row (stock_out, source direct) with the supplier and cost.',
                'Edit updates the transaction and keeps the linked bill\'s date, amount and notes equal; delete removes the transaction and its bill.'],
               [('urea_transactions', 'INSERT / UPDATE / DELETE', 'date, txn_type, source, batch_no, supplier_id, vehicle_id, invoice_no, quantity_l, unit_price, total_value, location, odometer_km, notes, maintenance_id'),
                ('maintenance', 'INSERT / UPDATE / DELETE', 'category = Urea, date, vehicle_id, amount, paid_amount, vendor_id, notes, invoice_no')],
               ['urea_transactions (stock check), vehicles, vendors'],
               ['The Urea tab shows current stock, today/month use, average consumption, low-stock alerts, a running-balance ledger and a trend; a refused stock-out shows "insufficient stock".'],
               ['Stock can never go negative.'])
    st += flow('32.5 Tolls: manual entry, Excel import, approve / reject, FASTag sync',
               'Toll spend is added by hand, imported from a BlackBuck BOSS statement (or the template), and approved.',
               'Maintenance → Toll tab; phone: + → Add Maintenance → Toll.',
               'POST /maintenance/toll/add (with a receipt file), /maintenance/toll/excel/preview (file), /maintenance/toll/excel/import (token + options), /<id>/approve, /<id>/reject, /<id>/delete, /maintenance/toll/sync-fastag.',
               f'{fnl("add_toll")} → {fnl("_insert_toll_from_form")}; {fnl("toll_excel_preview")} → {fnl("_parse_toll_excel")}; {fnl("toll_excel_import")}; {fnl("approve_toll")}; {fnl("reject_toll")}; {fnl("sync_fastag_toll")}.', 'app_add_toll',
               ['Manual: the vehicle must be an OWN vehicle. An optional trip LR ("LR | vehicle | date") is looked up to set trip_id. A receipt file is saved by _save_toll_receipt under uploads/toll_receipts/<company_id>/. One maintenance row (category Toll, paid = amount) and one toll_entries row (source manual → status pending, or fastag → synced).',
                'Excel preview: the file is parsed (auto-detecting a BOSS statement, where only debit FasTag Recharge lines count, or the plain template). The parsed rows are stashed in memory under a random token (kept 30 minutes) and a summary is shown: new rows, duplicates (by transaction id), changed duplicates, invalid rows, total amount. Nothing is written yet.',
                'Excel import: for each valid row a maintenance row and a toll_entries row are inserted (status synced for fastag, otherwise approved if "auto approve" was ticked, else pending); a duplicate is never inserted again — a changed duplicate may overwrite the existing record only if "override changed" was ticked; "auto-link trips" matches trip_no to a trip\'s LR.',
                'Approve and Reject only change status from pending. FASTag sync is a MOCK generator (no live feed): it adds a small deterministic batch at most once per hour.'],
               [('toll_entries', 'INSERT / UPDATE / DELETE', 'date, time, vehicle_id, trip_id, toll_plaza, highway, state, amount, source, payment_mode, status, reference_no, receipt_path, notes, maintenance_id'),
                ('maintenance', 'INSERT / UPDATE / DELETE', 'category = Toll, date, vehicle_id, amount, paid_amount = amount, notes')],
               ['vehicles (own only), trips (LR lookup), toll_entries (duplicates)'],
               ['The Toll tab lists entries with status chips, receipt links, totals per vehicle and an import wizard summary. Linked tolls reduce a trip\'s own toll estimate in reports (a real linked toll replaces the typed estimate, never added to it).'],
               ['The in-memory import stash is per worker and expires; if a different worker answers the second step the user simply repeats the preview.'])
    st += flow('32.6 Compliance fees, insurance policies, generic bills',
               'Fitness / permit / PUC fees, insurance premiums and any other maintenance bill.',
               'Maintenance → Compliance tab; Vehicles → Insurance tab; Maintenance → Add (generic).',
               'POST /maintenance/compliance/add; /maintenance/insurance/add, /<id>/edit, /<id>/delete (with up to three files: policy_doc, invoice_doc, rc_doc); /maintenance/add; /maintenance/edit/<id>; /maintenance/delete/<id>.',
               f'{fnl("add_compliance_expense")} → {fnl("_insert_compliance_expense_from_form")}; {fnl("add_insurance")}; {fnl("edit_insurance")}; {fnl("delete_insurance")}; {fnl("add_maintenance")}; {fnl("edit_maintenance")}; {fnl("delete_maintenance")}.', 'app_add_compliance',
               ['Compliance fee: the vehicle must be an own vehicle. A maintenance row (category = the compliance type) and a compliance_expenses row are written; paid amount is separate from amount, so a blank paid amount leaves it payable to the vendor.',
                'Insurance add: insurer becomes a vendor; a maintenance row (category Insurance, amount = premium, paid separate) is the bill; three uploaded documents are saved under uploads/insurance/<company_id>/; one insurance_policies row stores type, policy number, dates, premium, IDV, NCB, GST flag, agent details, reminder days, notes, optional Cancelled override and document paths; the vehicle\'s insurance_expiry is updated.',
                'Insurance edit: rewrites the policy (keeping old document paths when no new file is sent), updates the linked bill and re-applies the paid floor; delete removes the policy and its bill.',
                'Generic add/edit: a plain maintenance row with a free category; delete removes the row, its payment allocations and its checklist items.'],
               [('compliance_expenses', 'INSERT / DELETE', 'date, vehicle_id, compliance_type, description, vendor_id, amount, payment_mode, maintenance_id, notes'),
                ('insurance_policies', 'INSERT / UPDATE / DELETE', 'vehicle_id, insurance_type, insurer_id, policy_number, start_date, expiry_date, premium_amount, idv, ncb_pct, gst_included, agent_name, agent_contact, agent_email, reminder_days, notes, status_override, policy_doc_path, invoice_doc_path, rc_doc_path, maintenance_id'),
                ('maintenance', 'INSERT / UPDATE / DELETE', 'the bill for each of the above'), ('vehicles', 'UPDATE', 'insurance_expiry')],
               ['vehicles, vendors, insurance_policies'],
               ['Insurance policies show Active / Expiring Soon (within 30 days) / Expired from their dates (Cancelled by assertion); the Vehicles page badges and the bell use the same dates; premiums appear in the insurer\'s ledger.'], None)
    st += flow('32.7 The Maintenance hub (how everything above is shown)',
               'One page with tabs.', 'Sidebar → Maintenance (/maintenance?tab=overview|service|tyres|battery|urea|toll|compliance-expenses).',
               'GET /maintenance with tab and each tab\'s own filters (vehicle, vendor, dates, search, status, action, position …).', f'{fnl("maintenance_list")} → the tab function for the chosen tab (for example {fnl("_maintenance_service_tab")}, {fnl("_maintenance_tyres_tab")}, {fnl("_maintenance_battery_tab")}, {fnl("_maintenance_urea_tab")}, {fnl("_maintenance_toll_tab")}, {fnl("_maintenance_compliance_tab")}, {fnl("_maintenance_overview_tab")}).', 'app_maintenance and one phone page per tab',
               ['Each tab calls its own _X_data function (shared with the phone) which runs filtered SELECTs over maintenance joined to vehicles and vendors plus its specialised table, then computes cards and chart series in Python.',
                'The Overview covers OWN vehicles only (hired-vehicle bills are excluded by design) and uses _maintenance_classify so an entry lands in the same bucket everywhere.'],
               [], ['all of: ' + R('maintenance_list')],
               ['KPI cards, charts drawn as SVG/CSS, tables with Add / Edit / Delete buttons and modals; the filter panel keeps its values in the address.'], None)
    st.append(PageBreak())

    # ------------------------------------------------------------------ 33 vehicles
    st.append(H1('33. Vehicles and their documents'))
    st += flow('33.1 Add, edit, activate and delete a vehicle',
               'Registering a truck (own or hired).', 'Vehicles → Add Vehicle (modal) or full Edit page; phone Add Vehicle.',
               'POST /vehicles/add (also used to update by number), /vehicles/edit/<id>, /vehicles/toggle_status/<id>, /vehicles/delete/<id>. Fields: ' + ins('add_vehicle.html') + '.',
               f'{fnl("add_vehicle")} → {fnl("_insert_vehicle_from_form")} → {fnl("_mirror_vehicle_compliance_from_form")}; {fnl("edit_vehicle")}; {fnl("toggle_vehicle_status")}; {fnl("delete_vehicle")}.', 'app_add_vehicle',
               ['Add requires a vehicle number. If that number already exists the existing row is UPDATED (so re-adding is an update), otherwise a row is inserted.',
                'insurance_expiry is deliberately never written here: the Insurance tab (insurance_policies) is its only source.',
                'For OWN vehicles the three dates (fitness, PUC, permit) are mirrored into vehicle_compliance with source manual and sync_status "Not Synced", so the compliance badges and the detail page agree with the form.',
                'Delete runs a bare DELETE on the vehicle row.'],
               [('vehicles', 'INSERT / UPDATE / DELETE', 'vehicle_no, type, registration_date, capacity_mt, fitness_expiry, puc_valid_upto, permit_valid_upto, status, body_type, chassis_number, engine_number, notes'),
                ('vehicle_compliance', 'INSERT / UPDATE', 'vehicle_id, compliance_type (fitness, puc, permit), valid_upto, source = manual, sync_status')],
               ['vehicles, vehicle_compliance'],
               ['The vehicle appears in lists, autocomplete (active ones), the trip form\'s vehicle-type map and Fleet Utilization (own only).'],
               ['Observed gap: delete does not check for trips, bills or documents that still point at the vehicle, and foreign keys are not enforced, so such records would be left pointing at a missing vehicle.'])
    st += flow('33.2 Record a renewal; view RC details; refresh from the outside',
               'Keeping the documents and challans current.', 'Vehicle detail / Vehicles list buttons.',
               'POST /vehicles/<id>/compliance/<fitness|puc|permit>/renew; GET /vehicles/<id>/rc-lookup; POST /vehicles/<id>/sync-now; POST /vehicles/<id>/compliance/sync; POST /vehicles/compliance/sync-all.',
               f'{fnl("vehicle_compliance_renew")}; {fnl("vehicle_rc_lookup")}; {fnl("vehicle_sync_now")}; {fnl("vehicle_compliance_sync")}; {fnl("vehicles_compliance_sync_all")}.', None,
               ['Renewal: inserts or updates the vehicle_compliance row for that type with document number, authority, (permit subtype), valid-from/upto, source = manual; a manual entry does not overwrite whatever the last sync found.',
                'RC view: reads vehicles.rc_synced_data (JSON cached from the last sync) and shows it; no live call is made.',
                'Sync now (own vehicles only; skipped for hired): if an RC key is saved, calls eChallan for RC data → UPDATE vehicles.rc_synced_data / rc_last_synced and back-fill gaps (registration date, chassis, engine, expiry dates, body, capacity); then calls the challan lookup → DELETE the vehicle\'s vehicle_challans and INSERT the fresh set (33 columns), then UPDATE challan_count, challan_amount, challan_last_synced.',
                'Compliance sync (single or all): runs the provider classes (mock until a key exists) and updates vehicle_compliance statuses.'],
               [('vehicle_compliance', 'INSERT / UPDATE', 'document_number, issuing_authority, permit_subtype, valid_from, valid_upto, source, provider_name, last_sync_time, sync_status, remarks'),
                ('vehicles', 'UPDATE', 'rc_synced_data, rc_last_synced, challan_count, challan_amount, challan_last_synced and back-filled spec/date columns'), ('vehicle_challans', 'DELETE then INSERT', 'the full challan record per challan')],
               ['settings (rc_lookup_api_key), vehicles, vehicle_compliance'],
               ['Vehicles page badges (Insurance, Fitness, PUC, Permit) turn red / amber / green; the bell and Vehicles menu badge count vehicles with pending challans; the eChallan tab lists each challan.'],
               ['Every sync can spend paid credits, so none runs unless a person presses a button or the opt-in background job is enabled.'])
    st += flow('33.3 The Vehicles list and a vehicle\'s detail page',
               'Fleet overview and one truck in depth.', 'Sidebar → Vehicles (/vehicles) and a vehicle (/vehicles/<id>).',
               'GET /vehicles?tab=all|insurance|permit …; GET /vehicles/<id>?tab=….', f'{fnl("vehicles_list")}; {fnl("vehicle_detail")} → {fnl("_vehicle_detail_data")}.', 'app_vehicles, app_vehicle_detail',
               ['The list reads vehicles with their trips counts and the date fields; Python assigns each date a bucket (expired / expiring within 30 days / ok) with _expiry_bucket and computes KPI cards.', 'The detail reads the vehicle, its insurance policies, maintenance history, challans and settings.'],
               [], ['vehicles list: ' + R('vehicles_list'), 'vehicle detail: ' + R('vehicle_detail')],
               ['The list: cards Total Vehicles, Active, In Maintenance, Insurance / PUC / Permit Expiring; tabs Overview, All vehicles, Insurance, eChallan; a table with document badges and quick actions. The detail page: vehicle facts (type, model, chassis, engine, registration date, age) and tabs Insurance, Permit, Fitness, PUC, eChallan and History.'], None)
    st.append(PageBreak())

    # ------------------------------------------------------------------ 34 people
    st.append(H1('34. People: employees, salaries, advances and attendance'))
    st += flow('34.1 Add or edit an employee',
               'A driver or an office person joins.', 'Salaries → Add Employee modal; employee card → Edit.',
               'POST /employees/add and /employees/<id>/edit: ' + ', '.join(C(x) for x in ('name', 'type', 'employee_code', 'role', 'mobile', 'email', 'address', 'joining_date', 'date_of_birth', 'bank_account', 'ifsc_code', 'upi_id', 'emergency_contact', 'aadhaar', 'pan', 'driving_license', 'basic_salary')) + '.',
               f'{fnl("add_employee")} → {fnl("_insert_employee_from_form")}; {fnl("edit_employee")}; {fnl("deactivate_employee")}.', 'app_add_employee',
               ['Name and type (Driver or Staff) are required; basic salary is parsed. If an employee with that name already exists (case-insensitive) nothing new is created.',
                'INSERT into employees with status Active; when no employee code was typed one is made (DR-001 for drivers, ST-001 for staff, from the new id).',
                'Edit rewrites the personal and pay columns (it does not touch opening balances). Deactivate flips Active ↔ Inactive.'],
               [('employees', 'INSERT / UPDATE', 'name, type, employee_code, role, mobile, email, address, joining_date, date_of_birth, bank_account, ifsc_code, upi_id, emergency_contact, aadhaar, pan, driving_license, status, basic_salary')],
               ['employees (duplicate name check)'],
               ['The employee card and table appear on the Overview tab with attendance for today, advance outstanding and salary summary.'],
               ['Observed gap: salaries and advances are matched to an employee by NAME. Renaming an employee through Edit does not rewrite the old name stored in earlier salary and advance rows, so those rows no longer match the renamed person.'])
    st += flow('34.2 Pay a salary, give an advance, record a repayment',
               'Money moves between the company and an employee.', 'Employee ledger (/employee/<name>) → entry form, or a drawer on the Salaries page.',
               'POST /employee/<name> with entry_kind = salary, given or repaid, date, amount, payment_mode, notes. Also POST /employees/<id>/advance/add and POST /advances/add.',
               f'{fnl("employee_ledger")} → {fnl("_insert_employee_payment_from_form")}; {fnl("add_employee_advance")}; {fnl("add_advance")}.', 'app_employee_ledger, app_add_employee_payment',
               ['The entry kind, a valid date and a positive amount are required; the employee must exist.',
                'salary: one row into salaries — month label (e.g. Mar 2026) and month_key (2026-03) from the date, gross = net = amount, no deductions, payment_status paid, payment date and mode, employee_id, basic salary from the employee record (or the amount).',
                'given / repaid: one row into advances (employee name, date, amount, type, notes).'],
               [('salaries', 'INSERT', 'employee, month, amount, date, employee_id, month_key, basic_salary, gross_salary, total_deductions = 0, advance_recovery = 0, net_salary, payment_status = paid, payment_date, payment_mode, remarks'),
                ('advances', 'INSERT', 'employee, date, amount, type (given or repaid), notes')],
               ['employees'],
               ['The employee ledger lists Opening Balance, Salary Paid, Advance Given and Advance Repaid with a running ADVANCE balance computed over the whole history (a date filter hides rows but never changes the balance). Salary payments do not change the advance balance.'], None)
    st += flow('34.3 Attendance and payslips',
               'Marking who came to work and printing a payslip.', 'Salaries → Attendance tab (calendar and bulk toolbar); salary row → Payslip.',
               'POST /attendance/mark (employee_id, date, status, in_time, out_time, remarks); POST /attendance/bulk-mark (employee_ids, date, status); GET /salaries/<id>/payslip; POST /salaries/delete/<id>.',
               f'{fnl("mark_attendance")}; {fnl("bulk_mark_attendance")}; {fnl("download_payslip")}; {fnl("delete_salary")}.', 'app_salary_attendance',
               ['Attendance is one row per employee per date: an INSERT ... ON CONFLICT(employee_id, date) DO UPDATE, so marking the same day again changes the status instead of adding a second row. Allowed statuses: Present, Absent, Leave, Half Day.',
                'Payslip: reads the salary, the employee, its allowance/deduction items and the company settings and draws a PDF with ReportLab.',
                'Delete salary removes its salary_items and the salary row.'],
               [('attendance', 'INSERT or UPDATE', 'employee_id, date, status, in_time, out_time, remarks, marked_by'), ('salaries / salary_items', 'DELETE (on delete)', 'the rows')],
               ['employees, attendance, salaries, salary_items, settings'],
               ['The attendance calendar for the month, with per-employee present/absent counts; the payslip PDF downloads.'], None)
    st += flow('34.4 The Employees hub',
               'Overview, Salary and Attendance in one page.', 'Sidebar → Salaries (/salaries?tab=overview|salary|attendance).', 'GET with tab, dates, month, search.',
               f'{fnl("salaries_list")} → {fnl("_employees_overview_tab")} / {fnl("_employees_salary_tab")} / {fnl("_employees_attendance_tab")}.', 'app_salary',
               ['Overview: employees, today\'s attendance, per-person outstanding advance (' + fnl('_employee_outstanding_advance') + ') and month calendar; Salary: paid-this-period totals and payments table; Attendance: month grid.'],
               [], [R('salaries_list')], ['KPI cards (employees, drivers, staff, present today, outstanding advances), employee cards, tables and drawers.'], None)
    st.append(PageBreak())

    # ------------------------------------------------------------------ 35 invoices
    st.append(H1('35. Invoices'))
    st += flow('35.1 Choose trips in the Invoice Center',
               'Billing day: the clerk chooses a party (or an owner), a date range and the trips to put on one invoice.',
               'Sidebar → Invoices (/invoice-center).', 'GET /invoice-center with invoice_type, party_id, vendor_id, vehicle, lr_number, date_from, date_to, invoice_status.',
               fnl('invoice_center'), None,
               ['Reads active parties and vendors (plus parties not linked to a vendor, offered as possible owners for freight bills; picking one creates its vendor row on the fly — the only write on this page).',
                'Reads matching trips with their already-invoiced status (from invoice_batch_trips), their extra lines (invoice_items) and tolls, and the invoice settings.'],
               [('vendors', 'INSERT (rare)', 'name, when a party-only owner is picked')],
               [R('invoice_center')],
               ['A seven-step page: 1 Select invoice type, 2 Select party or owner, 3 Filter trips, 4 Select trips to include (tick boxes and an LR link per row), 5 Invoice summary, 6 Invoice preview, 7 Actions (Preview, Save as Draft, Generate); charge and deduction toggles per trip.'], None)
    st += flow('35.2 Review, then generate, draft or preview',
               'Producing the invoice.', 'Invoice Center buttons.',
               'POST /invoice-center/review (to show the review screen) then POST /invoice-center/generate with mode = generate | draft | preview, trip_ids, reviewed_trip_ids, invoice_type, party_id / vendor_id, invoice_date, due_date, payment_terms, place_of_supply, remarks, payment_status, loading_charges, other_charges, tds_rate, gst_rate, excluded_charge_keys, included_charge_keys, extra_item_desc / extra_item_amount / extra_item_type.',
               f'{fnl("invoice_center_review")}; {fnl("invoice_center_generate")} → {fnl("_build_invoice_pdf")}.', None,
               ['For generate and draft the server checks that the LR link of EVERY selected trip was opened (reviewed_trip_ids); otherwise it redirects back with a message and a balloon at the LR column. Preview skips this check.',
                '_invoice_numbers_error validates the numeric fields; money fields go through _parse_money.',
                'Trips are loaded; their invoice_items are filtered by the unchecked charge keys; extra lines typed into the form are added.',
                'The entity (party or owner vendor) is loaded; for a tax invoice the GST rate comes from the form or the CGST+SGST settings and is split in two.',
                'The invoice number is prefix + current year + next number (e.g. ATS/INV/2026/0004).',
                'Preview: builds the PDF and returns it without writing anything or consuming a number.',
                'Draft / Generate: INSERT invoice_batches (status draft or generated), one invoice_batch_trips row per trip, one invoice_batch_items row per typed extra line, the charge choices into invoice_batch_charge_exclusions, then writes settings.next_invoice_number = next + 1. Commit.',
                'Draft redirects to Generated Invoices; Generate returns the PDF as a download.'],
               [('invoice_batches', 'INSERT', 'invoice_number, invoice_type, party_id, vendor_id, invoice_date, due_date, payment_terms, place_of_supply, remarks, gst_rate, tds_rate, loading_charges, other_charges, status, payment_status, created_by, created_at'),
                ('invoice_batch_trips', 'INSERT (one per trip)', 'invoice_batch_id, trip_id'), ('invoice_batch_items', 'INSERT', 'invoice_batch_id, description, amount, item_type'),
                ('invoice_batch_charge_exclusions', 'INSERT', 'invoice_batch_id, trip_id, charge_key (a leading + means opted in)'), ('settings', 'INSERT OR REPLACE', 'next_invoice_number')],
               ['trips, vehicles, parties or vendors, invoice_items, toll_entries, settings'],
               ['A PDF with the company letterhead and logo, party details, one row per trip, charges, GST/TDS/round-off, amount in words, bank details and the RCM clause (tax invoices).'],
               ['Saving requires the review gate; the number counter moves only on a saved invoice, so previews never burn numbers.'])
    st += flow('35.3 Generated invoices: list, edit, add/remove lines, PDF, delete; per-trip invoices',
               'After saving, an invoice can be reopened.', 'Invoices → Generated Invoices (/invoices/generated).',
               'GET /invoices/generated; GET/POST /invoices/generated/<id>/edit; POST .../items/add; POST /invoices/generated/items/<id>/delete; GET .../pdf and .../pdf/preview; POST .../delete. Older per-trip invoices: /invoices, /invoices/<trip>, /invoices/<trip>/edit, .../items/add, /invoices/items/<id>/delete, /invoices/<trip>/pdf.',
               f'{fnl("invoice_batches_list")}; {fnl("invoice_batch_edit")}; {fnl("invoice_batch_item_add")}; {fnl("invoice_batch_item_delete")}; {fnl("invoice_batch_pdf")}; {fnl("invoice_batch_delete")}.', None,
               ['The PDF is regenerated from the stored batch every time: trips from invoice_batch_trips, lines from invoice_batch_items and invoice_items, the saved charge choices from invoice_batch_charge_exclusions and the current company settings.',
                'Delete removes the batch\'s trip links, lines and the batch itself.'],
               [('invoice_batches', 'UPDATE / DELETE', 'dates, terms, remarks, status, payment_status …'), ('invoice_batch_items', 'INSERT / DELETE', 'description, amount, item_type, vendor_id'), ('invoice_batch_trips', 'DELETE (on delete)', 'the links')],
               [R('invoice_batch_pdf')],
               ['The list shows number, type, party/vendor, date, trip count, status and payment status with PDF, Edit and Delete.'],
               ['Observed gap: deleting an invoice batch does not delete its invoice_batch_charge_exclusions rows, which are left behind unused.', 'An invoice\'s total can differ from the ledger\'s billed amount when optional lines are included (a documented business decision).'])
    st.append(PageBreak())

    # ------------------------------------------------------------------ 36 reports
    st.append(H1('36. Reports and exports: how the numbers are retrieved'))
    st.append(P('All of these only read. They share the profit rule in Chapter 18. For each, the real tables read (found by analysing the code) are listed.', 'lead'))
    rep = [
        ('36.1 Dashboard', 'dashboard', 'Sidebar → Dashboard.', 'GET /dashboard with financial year, From, To, vehicle.', 'app_dashboard',
         ['_dashboard_financials_data loads the period\'s trips (optionally one vehicle) and totals revenue, own-vehicle revenue, hired-vehicle profit, fuel and maintenance cost, profit and the expense breakdown with the same own/hired rule as everywhere.',
          'Receivables and payables come from _accounts_rows (the bulk ledger engine). A freight trend (this period versus last), recent activities (latest trips, payments, salaries, invoices, expenses), trip-status split and top vehicles are built in Python.'],
         ['Seven KPI cards, a freight revenue line chart, expense breakdown bars, recent activities, a trip-status donut, payables and receivables, top vehicles table.']),
        ('36.2 Business Performance', 'business_performance', 'Sidebar → Business Performance.', 'GET with date_from, date_to (default: last full month).', 'app_business_performance',
         ['_period_financials is evaluated for the chosen period, the previous period, the quarter, the previous quarter and the previous year; growth percentages come from _pct_growth.'],
         ['Revenue, costs, profit, receivable/payable and growth versus earlier periods, with an Excel export.']),
        ('36.3 Performance (drivers and vehicles)', 'performance', 'Sidebar → Performance (driver_performance redirects here).', 'GET with period and dates.', 'app_performance',
         ['_performance_data groups trips by driver and by vehicle: trips, km, revenue, fuel and the full cost basis; profit = billed − cost (own) or billed − owner cost (hired); toll comes from linked toll entries else the trip estimate; trend lines per month.'],
         ['Ranked tables of drivers and vehicles with revenue, cost, profit and trend charts.']),
        ('36.4 Route Analytics', 'route_analytics', 'Sidebar → Route Analytics.', 'GET with tab (best routes, route summary, route rates, finder), dates, from/to, vehicle type.', 'app_analytics',
         ['_route_analytics_data groups trips by cleaned from/to place names (_clean_loc) and computes revenue, cost, margin and average rate; _route_rates_data finds lowest, average and highest rate per route and vehicle type.'],
         ['Top routes by margin and revenue, a route table, rate cards and the Route Finder tab.']),
        ('36.5 Fleet Utilization', 'fleet_utilization', 'Sidebar → Fleet Utilization (idle-tracker and empty-runs redirect here).', 'GET with dates, vehicle, period.', 'app_fleet_utilization',
         ['_fleet_utilization_data counts, per OWN vehicle, the days covered by the union of each trip\'s start..end span (active) and the rest of the window (idle, starting no earlier than the registration date), empty runs, kilometres, fuel and efficiency.'],
         ['Utilization percentage per vehicle, idle trend chart, empty-run list and totals.']),
    ]
    for title, nm, start, req, twin, steps, shows in rep:
        st += flow(title, '', start, req, f'{fnl(nm)}', twin, steps, [], [R(nm)], shows, None)
    st += flow('36.6 Exports',
               'Taking data out as Excel or PDF.', 'Export buttons on list pages.',
               'GET /trips/export, /trips/export/pdf, /maintenance/export(/pdf), /overheads/export, /accounts/export, /ledger/party|vendor/<id>/export(/pdf), /performance/export, /route-analytics/export, /fleet-utilization/export, /business-performance/export, /settings/users/export, /export.',
               f'{fnl("export_trips")}, {fnl("export_trips_pdf")}, {fnl("export_maintenance")}, {fnl("export_overheads")}, {fnl("export_accounts")}, {fnl("export_party_ledger")}, {fnl("export_excel")} and the others.', None,
               ['Each export runs the same data function and filters as its screen and writes the result to an in-memory Excel workbook (openpyxl) or PDF (ReportLab), then returns it as a download.'],
               [], ['the same tables as the matching screen; the whole-database export reads ' + R('export_excel')],
               ['A file download (trips_export.pdf, ledger PDF, users.xlsx, and so on).'],
               ['The whole-database Excel is refused for Limited Access users. Large PDFs of all trips take several seconds (about 5.8 s for 12,000 trips in the load test).'])
    st.append(PageBreak())
    return st
