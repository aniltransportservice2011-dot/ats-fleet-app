"""Part E, last chapters: administration, the Route Finder, phone twins and background/operations flows."""
from lib import *
import facts as F
import analysis as A
from flowlib import flow, C, fnl


def twin_rows():
    """phone route -> shared helpers -> website routes that call the same helpers (derived from the code)."""
    app_routes = [r for r in F.ROUTES if r[2].startswith('app_')]
    web_routes = [r for r in F.ROUTES if not r[2].startswith('app_')]
    shared_names = [n for n in A.FUNC_NAMES if n.startswith('_') and (n.startswith('_insert') or n.startswith('_update') or n.endswith('_data') or n.startswith('_create') or n.startswith('_end_') or n.startswith('_install') or n.startswith('_save'))]
    web_by_helper = {}
    for w in web_routes:
        ch = set(A.ROUTE_CLOSURE[w[2]]['chain'])
        for h in ch & set(shared_names):
            web_by_helper.setdefault(h, set()).add(w[2])
    rows = []
    for a in app_routes:
        ch = [h for h in A.ROUTE_CLOSURE[a[2]]['chain'] if h in web_by_helper]
        seen, helpers = set(), []
        for h in ch:
            if h not in seen:
                seen.add(h); helpers.append(h)
        if not helpers:
            continue
        webs = sorted({w for h in helpers for w in web_by_helper[h]})
        rows.append([C(a[0]), C(a[2]), ', '.join(C(h) for h in helpers[:4]), ', '.join(C(w) for w in webs[:4])])
    return rows


def part_e3():
    st = []
    st.append(H1('37. Administration: settings, users and your own profile'))
    st += flow('37.1 Save company settings (company, banking, invoice, tax, SMS, keys, sync)',
               'The Administrator changes how the company appears on invoices, the GST rates, the bank account, the SMS and vehicle-data keys and the sync schedule.',
               'Website: Profile and Settings (/settings) tabs Company Profile, Invoice Settings, Tax and Compliance, Users and Roles (the SMS, RC key and sync cards are inside it). Phone: Profile and Settings hub → five screens.',
               'Website: POST /settings?tab=… with form_type = company | banking | invoice_prefs | invoice_numbering | tax | rcm | sms | rc_lookup | sync_schedule and that group\'s fields. Phone: POST /app/settings/save with the same form_type values (JSON answer).',
               f'{fnl("settings_page")} and {fnl("app_settings_save")} → {fnl("_settings_form_error")} (phone only) → {fnl("_save_settings_group")}.', 'app_settings_company, app_settings_banking, app_settings_invoice, app_settings_tax, app_settings_users, app_settings_save',
               ['Only the Administrator may reach the routes (a Manager gets "Administrators only" / HTTP 403; a non-administrator who opens /settings is shown "My Profile" instead).',
                'Phone only: validation first — company name required, valid email, GST/TDS between 0 and 100, round-off and due-day limits, next invoice number at least 1, sync days at least 1, and MSG91 key and template both present. Blank secret boxes are replaced with the saved value before validating.',
                '_save_settings_group loops over the group\'s keys. Checkbox keys (show logo, show bank details, show signatory, print amount in words, reverse charge, RCM on transport, TDS applicable, e-way bill mandatory) are stored as 1 or 0 depending on whether the box was ticked. Every key is written with INSERT OR REPLACE into settings with the company id, so a missing row can never make a save silently do nothing.',
                'Secret keys (msg91_authkey, rc_lookup_api_key) are never shown again; a blank box keeps the stored one.'],
               [('settings', 'INSERT OR REPLACE (one row per key)', 'company: company_name, address, state, phone, email, gstin, pan, business_type, company_since, currency · banking: bank_name, account_holder, account_number, ifsc_code, branch, account_type · invoice_prefs: default_invoice_type, default_payment_terms, default_place_of_supply, default_due_days, show_company_logo, show_bank_details, show_signatory, print_amount_words · invoice_numbering: invoice_prefix, next_invoice_number · tax: cgst_rate, sgst_rate, igst_rate, reverse_charge_applicable, rcm_on_transport, tds_applicable, tds_rate_default, eway_bill_mandatory, round_off_limit · rcm: rcm_clause · sms: msg91_authkey, msg91_template_id · rc_lookup: rc_lookup_api_key · sync_schedule: rc_sync_interval_days, challan_sync_interval_days; each with company_id, created_by/at, updated_by/at')],
               ['settings, users, access_logs, sync_log (for the Users and Roles screen)'],
               ['The next invoice, the company name in the sidebar and PDFs, the GST split on tax invoices and the sync cadence use the new values immediately; the phone reloads the screen with a "Saved" toast.'],
               ['Invoice numbering: invoice_center_generate reads next_invoice_number, uses it and writes number + 1; editing it here changes where numbering continues.'])
    st += flow('37.2 Upload the company logo',
               'The picture printed top-left on every invoice PDF and used as the browser tab icon.', 'Company Profile → Upload/Change Logo.',
               'POST /settings/logo/upload with a file named logo (PNG, JPG, GIF or WEBP).', fnl('upload_company_logo'), None,
               ['An allowed extension is required (otherwise a clear message in the app, nothing saved).', 'The file is saved as logo_<millisecond timestamp>.<ext> under static/uploads/logo/<company_id>/; the relative path is written to settings.logo_path.'],
               [('settings', 'INSERT OR REPLACE', 'logo_path = uploads/logo/<company_id>/logo_<time>.<ext>')], [], ['The new logo shows on the Company Profile, in the browser tab and on the next invoice PDF.'],
               ['Old logo files are not deleted (three logos were already on disk when this document was prepared). Logos are public files; insurance and toll documents are protected.'])
    st += flow('37.3 Create, edit, hand over and remove users',
               'The Administrator decides who can sign in and what they can do.', 'Settings → Users and Roles (website table and modals; phone: Users and Roles list and bottom sheet).',
               'POST /settings/users/add (full_name, username = mobile, email, password, role, access_level, modules, status); /settings/users/<id>/edit (the same plus phone); /settings/users/<id>/make-administrator; /settings/users/<id>/delete. The phone sheet sends the same forms with the header X-App-Request: 1 and gets JSON.',
               f'{fnl("add_user")}; {fnl("edit_user")}; {fnl("make_administrator")}; {fnl("delete_user")}; {fnl("_modules_from_form")}; {fnl("_other_active_administrator")}.', 'the app sheet calls the same routes',
               ['Add: full name required; mobile must be a valid 10-digit number (it becomes both username and phone); duplicate mobile or email is refused with the account named; password optional (at least 8 if given — a blank one stores a random hash so only SMS login works); a role typed as Administrator is refused while another Administrator is active (unless the new user is Inactive); access level Limited needs at least one screen ("Ledger:e,Reports:v" format, unknown names dropped).',
                'INSERT into users with is_admin = 1 only for Full Access, status (default Active), created_by.',
                'Edit: same validations (excluding the user\'s own row); the last-Administrator rules stop demoting or deactivating the only one; the password changes only if a new one is typed; UPDATE.',
                'Make Administrator: the target must exist, be Active and not be the caller; in one transaction the caller becomes Manager / Full Access (module_access cleared) and the target becomes Administrator / Full Access / Active; an access_logs line records the hand-over.',
                'Delete: refused for yourself or the last Administrator; otherwise the user and their access_logs rows are deleted.'],
               [('users', 'INSERT / UPDATE / DELETE', 'username, password_hash, role, is_admin, phone, full_name, email, access_level, module_access, status, created_at, created_by (insert); full_name, role, phone, email, access_level, module_access, status, is_admin, password_hash (edit)'),
                ('access_logs', 'INSERT / DELETE', 'hand-over event; a deleted user\'s log rows are removed')],
               ['users, settings, access_logs'],
               ['The users table updates; the affected user\'s very next request re-reads their row, so a change of level, screens or status applies at once (an Inactive user is signed out on the next click).'],
               ['Every refusal is a readable message (a banner in the website modal, a red line in the phone sheet).'])
    st += flow('37.4 Your own profile and password',
               'Anyone can change their own name, mobile, email and password.', 'Website: Profile and Settings (shows "My Profile" for non-administrators); phone: Profile → Edit Profile / Change Password.',
               'POST /settings/my-profile (full_name, phone, email); POST /settings/my-password (current_password, new_password, confirm_password); phone: POST /app/profile/update and /app/profile/change-password (JSON).',
               f'{fnl("website_update_my_profile")} → {fnl("_update_own_profile")}; {fnl("website_change_password")} → {fnl("_change_own_password")}.', 'app_update_profile, app_change_password',
               ['Profile: name required; mobile must be 10 digits; email must look valid; another user\'s mobile or email is refused. Only name, email and phone are updated — a posted role or access level is ignored.', 'Password: all fields required, new equals confirmation, at least 8 characters, and the current password must verify before the hash is replaced.'],
               [('users', 'UPDATE', 'full_name, email, phone | password_hash (with updated_by, updated_at)')], ['users'],
               ['The header shows the new name/initials; Read Only users may still change their own password (the only write they are allowed).'], None)
    st.append(PageBreak())

    st.append(H1('38. The Route Finder'))
    st += flow('38.1 Type-ahead, search and stops',
               'Finding a driving route between two places in India and the toll plazas, towns and fuel pumps along it.',
               'Route Analytics → Route Finder tab; phone: Business Overview → Route analytics → Finder.',
               'GET /route-finder/suggest?q=…&lat=…&lon=… (after 3 letters); GET /route-finder/search?from=…&to=…&from_lat/from_lng/to_lat/to_lng (when a suggestion was picked); POST /route-finder/extras with JSON {geometry, distance_km, highways}.',
               f'{fnl("route_finder_suggest")}, {fnl("route_finder_search")}, {fnl("route_finder_extras")} and providers/osm_routes.py.', None,
               ['Suggest: fewer than 3 letters returns an empty list; results are cached for an hour per worker; a separate semaphore (_RF_SUGGEST_SLOTS) allows a limited number of simultaneous suggestion calls and answers "busy" otherwise; Photon is asked for places in India.',
                'Search: at most 3 searches run at once (_RF_SLOTS), otherwise HTTP 429 "Several route searches are running"; each end is either the picked coordinate pair or a Nominatim lookup (geocodes are cached 24 hours; the To end is biased towards the From end); OSRM returns up to three routes (cached 1 hour by rounded coordinates); the result also includes what the company\'s OWN past trips on that route earned (_route_history reads trips and toll_entries).',
                'Extras: the browser sends the chosen route\'s simplified path back (so any gunicorn worker can answer); the server checks it (2–4000 valid points, distance below 6000 km), caches by a hash of the path, and asks Overpass for toll booths and gantries, cities and towns, petrol pumps, airports, junctions and notable landmarks inside ~50 km boxes along the route; features are matched to the path by distance (tolls within 250 m, fuel 200 m, towns 4 km, landmarks 3 km, airports 8 km); places are ranked and the best spread along the route are chosen per screen size.',
                'Failures: an Overpass failure is reported with its HTTP status and a "Try again" button; failures are never cached.'],
               [], ['trips and toll_entries only (for the company\'s own route history); everything else comes from the outside services'],
               ['A map (Leaflet) with the route(s), cards with distance, time and highways, a "Route Stops" timeline with elapsed time, toll and petrol counts per stretch, and the honest notes about data limits.'],
               ['Nothing from the outside services is stored in the database.'])
    st.append(PageBreak())

    st.append(H1('39. The phone app: how it relates to the website'))
    st.append(P(f'The phone app has its own routes ({sum(1 for r in F.ROUTES if r[2].startswith("app_"))} of them under /app/) and its own templates ({F.T_APP} files), but wherever a business action exists on the website, the phone route calls the same helper, so the saved rows are identical. The table is generated from the code: for every phone route that shares a helper with the website, it lists the shared helper and the website routes that use it.', 'lead'))
    rows = [['Phone URL', 'Phone function', 'Shared helper(s)', 'Website route(s) using the same helper']] + twin_rows()
    st.append(tbl(rows, [19, 20, 31, 30], mono_cols=()))
    st.append(Spacer(1, 6))
    st += bullets(['<b>Differences are only on the surface:</b> mobile templates (cards, bottom sheets, fans) instead of tables and modals, JSON answers to fetch() calls instead of redirects, and a few phone-only pages (the Administrator settings hub).',
                   '<b>Access rules are the same hook.</b> require_login runs for /app/ exactly as for the website; a blocked phone request gets JSON or the friendly "No access" page.',
                   '<b>Navigation shell.</b> The phone layout swaps page content with fetch() instead of reloading (Figure 15), but every page is still a normal server-rendered response.'])
    st.append(PageBreak())

    st.append(H1('40. Background jobs and operational endpoints'))
    st += flow('40.1 Scheduled vehicle-data refresh',
               'Keeping RC data, compliance dates and challans fresh without anyone pressing a button.', 'Render cron (or the local APScheduler) every hour.',
               'POST /internal/sync-tick?job=sync_tick (default) or job=weekly_compliance, with header X-Sync-Token.', f'{fnl("internal_sync_tick")} → scheduler.run_sync_tick / run_weekly_sync.', None,
               ['The token is compared with INTERNAL_SYNC_TOKEN; if it is unset or wrong the answer is HTTP 401 and nothing runs.',
                'run_sync_tick reads sync_log and the Settings intervals; if a job is due and enabled (ENABLE_RC_SYNC / ENABLE_CHALLAN_SYNC) it runs: one eChallan call per own vehicle, then the same writes as "Sync now".',
                'weekly_compliance runs the provider sync for the whole own fleet unconditionally.',
                'Every real run writes one sync_log row at start and updates it at the end with counts.'],
               [('sync_log', 'INSERT / UPDATE', 'job_name, started_at, finished_at, synced_count, failed_count, skipped_count, note'), ('vehicles', 'UPDATE', 'rc_synced_data, rc_last_synced, challan_count, challan_amount, challan_last_synced and back-filled columns'),
                ('vehicle_challans', 'DELETE then INSERT', 'fresh challan rows'), ('vehicle_compliance', 'INSERT / UPDATE', 'fitness, PUC, permit records')],
               ['settings (intervals, rc_lookup_api_key), vehicles, sync_log'],
               ['Vehicles badges and the bell reflect the new data; Users and Roles → Integrations lists the latest runs.'],
               ['Off by default; a failed call never overwrites good data; each call is a paid credit, so only deliberate opt-in turns it on. The token comparison is a plain inequality, not constant-time (low risk, noted).'])
    st += flow('40.2 Database backup download',
               'Giving your Mac a verified copy of the live database.', 'pull_backup.py running daily on the Mac.',
               'POST /internal/backup with header X-Backup-Token.', fnl('internal_backup'), None,
               ['If more than 5 wrong tokens came from this address in 15 minutes the answer is HTTP 429. If BACKUP_TOKEN is unset or the header is wrong the answer is 403 and the attempt is recorded.',
                'SQLite\'s online backup copies the database to a temp file, PRAGMA integrity_check runs, the file is gzipped and streamed as fleet_backup.db.gz; temp files are removed after the response.'],
               [('auth_attempts', 'INSERT (wrong tries)', 'kind backup_fail_ip, key = address, time')], ['the whole database file, read through the backup API'],
               ['The Mac script saves render_YYYYmmdd_HHMMSS.db after its own checks (integrity, key tables not empty, no sudden 20% drop in rows).'], None)
    st += flow('40.3 What happens at start-up',
               'Every gunicorn worker, while importing app.py.', 'Render starting or restarting the service.', 'none (not a request).', 'Chapter 8.2 lists the seven steps.', None,
               ['Flask is created; cookie, proxy and upload settings are applied; the database path is resolved; then, in order: overhead allocation column, performance indexes, WAL mode, auth tables, secret key, single-Administrator index, Twilio cleanup.', 'Hooks and routes are registered; the worker begins to serve.'],
               [('payment_allocations / settings / users', 'ALTER or INSERT only if missing', 'overhead_id column (after a backup copy), the private secret key row, indexes, the two auth tables')],
               [], ['The server log prints one line per step ([schema] / [security]).'], ['Safe to repeat and safe with two workers starting at once.'])
    st.append(PageBreak())
    return st
