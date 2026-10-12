"""Part I: operations runbook, file-by-file reference and the guide to extending the system."""
import glob, os
from lib import *
import facts as F
from flowlib import C


FILES = [
    ('app.py', 'The whole back-end: routes, business rules, security hooks, PDF/Excel builders, phone-app routes, self-applying schema steps.'),
    ('db.py', 'Connection wrapper (pass-through today; the one place a PostgreSQL switch would be wired).'),
    ('scheduler.py', 'APScheduler jobs and the sync-tick logic: weekly compliance, RC sync, challan sync, back-fill rules, sync_log.'),
    ('compliance_service.py', 'Fitness/PUC/permit status calculation and the sync of a vehicle or the whole own fleet through the providers.'),
    ('providers/base_provider.py', 'The shared provider interface and the "mock or live" rule.'),
    ('providers/echallan_client.py', 'HTTP client for the eChallan RC and challan APIs (X-API-Key header, one call serves several providers).'),
    ('providers/vahan_provider.py', 'Fitness slice of the RC answer (mock until a key exists).'), ('providers/puc_provider.py', 'PUC slice of the RC answer.'),
    ('providers/permit_provider.py', 'Permit slice of the RC answer.'), ('providers/insurance_provider.py', 'Placeholder for a future insurance cross-check API.'),
    ('providers/osm_routes.py', 'Route Finder engine: Photon suggestions, Nominatim geocode, OSRM routes, Overpass features, ranking, rate limiting and budgets.'),
    ('config/apis.py', 'Compliance provider defaults and environment-variable names.'),
    ('schema.sql', 'Authoritative SQLite schema (also used to build the test databases).'), ('schema_postgres.sql', 'PostgreSQL twin with real company_id enforcement and row-level security (parse-verified, not yet run).'),
    ('migrate.py, migrate_extra.py, migrate_v2.py', 'One-time imports from Fleet_Master_Tracker.xlsx (historical).'),
    ('migrate_*.sql (12 files)', 'Documented schema changes; see Chapter 26.'),
    ('backfill_rc_fields.py', 'One-off script that back-filled vehicle spec/date fields from cached RC data.'),
    ('Procfile', 'The gunicorn command Render runs.'), ('requirements.txt', 'Python packages: flask, openpyxl, reportlab, gunicorn, apscheduler, requests.'),
    ('backup_db.py', 'Local verified backup of fleet.db.'), ('pull_backup.py', 'Daily download of a verified copy of the live database from Render.'),
    ('restore_check.py', 'Proves a backup restores and the app runs on it.'), ('db_health_check.py', 'Read-only database audit (integrity, drift, anomalies).'),
    ('load_test.py', 'Seeds 12,000 trips and times every screen and concurrent users.'),
    ('test_balance_audit.py', 'The 17-stage audit harness.'), ('test_*.py (15 more)', 'Earlier regression suites; see Chapter 50.2.'), ('test_support.py', 'Helper that creates an active Administrator test user in disposable databases.'),
    ('CLAUDE.md', 'Project rules (multi-company readiness, test on copies, deploy database and code separately).'),
    ('README.md, DEPLOY_TO_CLOUD.md, BACKUP_AND_RESTORE.md', 'Original local guide, an older DigitalOcean deployment guide (superseded by Render), and the current backup/restore guide.'),
    ('production_readiness_report.md, code_health_report.md, balance_audit_findings.md, load_test_report.md', 'Review reports that feed Part H.'),
    ('docs/blueprint/', 'This document\'s generator: lib.py (layout), diagrams.py, facts.py and analysis.py (read the real code), content_*.py (text), build.py.'),
    ('archive/unused_ui/', 'Retired templates kept for reference (old Analysis page, old salary list, mock-ups).'),
    ('audit_reports/', 'Machine-written reports of earlier audit runs.'),
    ('static/, templates/', 'Front-end files; see Chapters 45 and 46.'),
]


def part_i():
    st = [NextPageTemplate('divider'), PageBreak(),
          PartDivider('I', 'Running, changing and extending', 'The practical handbook: day-to-day commands, a file-by-file reference and a checklist for adding something new without breaking the rules.',
                      ['Runbook: the commands and tasks', 'File-by-file reference', 'How to add a screen, a table or a rule safely', 'Roadmap']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('53. Runbook'))
    st.append(H2('53.1 Run it on your computer'))
    st.append(code('''cd /Users/beenash/Downloads/fleet_local_app
python3 app.py                                  # website + phone app at http://127.0.0.1:5050  (debug on)
FLASK_HOST=0.0.0.0 FLASK_DEBUG=0 python3 app.py # also reachable from phones on the same Wi-Fi (use debug OFF)
python3 test_balance_audit.py --stage roles     # run one test stage (omit --stage for everything)
python3 db_health_check.py                      # read-only audit of fleet.db'''))
    st.append(H2('53.2 Everyday tasks'))
    st.append(tbl([['Task', 'How'],
                   ['Create a user', 'Sign in as the Administrator → Profile and Settings → Users and Roles → Add User (mobile number, name, role, access level; for Limited Access tick screens and choose View or Edit). Password is optional (SMS login only if blank).'],
                   ['Give a user fewer screens', 'Edit the user → Access level Limited Access → presets or switches. Takes effect on their next click.'],
                   ['Hand the Administrator seat to someone', 'Edit that (active) user → Make Administrator. You become a Manager with Full Access.'],
                   ['Turn on SMS login', 'Create a DLT-approved MSG91 OTP template containing ##OTP##; copy the Auth Key and Template ID; switch off the key\'s IP restriction or add Render\'s address; paste both in Users and Roles → Integrations (or the website SMS card).'],
                   ['Change invoice numbering or GST', 'Settings → Invoice Settings / Tax and Compliance.'],
                   ['Take a backup now', 'python3 backup_db.py (local) or BACKUP_URL=… BACKUP_TOKEN=… python3 pull_backup.py (live).'],
                   ['Check a backup restores', 'python3 restore_check.py path/to/backup.db'],
                   ['Deploy', 'Run the tests, commit and push the branch Render follows; Render builds and restarts; the app applies its own safe schema steps. Check the server log for the [schema] / [security] lines.'],
                   ['Rotate a key', 'MSG91 or eChallan: create a new key in the provider\'s console, paste it into Settings (blank box keeps the old one), then disable the old key. SECRET_KEY: change it in Render (everyone is signed out once).'],
                   ['Reopen public sign-up', 'Set ALLOW_SELF_SIGNUP=1 (not recommended).']], [24, 76], bold_first=True))
    st.append(H2('53.3 When something is wrong'))
    st.append(tbl([['Symptom', 'First place to look'],
                   ['A page is blank or error 500', 'The server log; then the layer that owns it (Figure 2). Bad form input is answered with a friendly 400, so a 500 is a real bug worth a test.'],
                   ['"Database is locked"', 'Should not appear (30 s wait, WAL). If it does: a long report plus many saves; check the load test numbers and worker count.'],
                   ['A user says "no access"', 'Their access level and screens in Users and Roles; Read Only and View screens refuse writes by design.'],
                   ['Balances disagree between screens', 'Run python3 db_health_check.py; then the audit stages party / vendor; compare against Chapter 52.3 (known definition differences).'],
                   ['SMS code not arriving', 'MSG91 template approved? Auth Key IP restriction? Balance? The app says "Could not send the code right now" and logs the provider\'s reason.'],
                   ['Everything vanished after a deploy', 'The persistent disk is missing: restore from Render\'s snapshot or the latest pull_backup copy (BACKUP_AND_RESTORE.md).'],
                   ['A vehicle shows no RC data', 'No RC key saved, or the vehicle is hired (not synced), or the paid sync has never run: use "Sync Latest Data" on the vehicle.']], [30, 70], bold_first=True))
    st.append(PageBreak())

    st.append(H1('54. File-by-file reference'))
    rows = [['File / folder', 'Lines', 'Purpose']]
    for name, purpose in FILES:
        first = name.split(',')[0].split(' ')[0]
        path = F.p(first)
        n = ''
        if os.path.isfile(path) and path.endswith(('.py', '.sql', '.md')):
            n = f'{F.lines(path):,}'
        rows.append([C(name), n, purpose])
    st.append(tbl(rows, [30, 8, 62]))
    st.append(Spacer(1, 5))
    st.append(P(f'In numbers: app.py {F.APP_LINES:,} lines; {F.N_FUNCS} functions; {F.N_ROUTES} URLs; {F.T_ALL} templates ({sum(m["lines"] for m in F.TEMPLATES):,} lines); {sum(F.CSS_LINES.values()):,} lines of CSS and {sum(F.JS_LINES.values()):,} lines of JavaScript of our own; {F.N_TABLES} tables and {F.N_INDEXES} indexes; {len(F.TEST_FILES)} test files.'))
    st.append(PageBreak())

    st.append(H1('55. Adding something new without breaking the rules'))
    st.append(P('A short checklist, distilled from the project rules (CLAUDE.md) and from every defect found during this project.', 'lead'))
    st.append(tbl([['If you add…', 'Do this'],
                   ['A table', 'company_id INTEGER NOT NULL DEFAULT 1 (and the Postgres twin with a foreign key and an index; natural-key uniques as (company_id, name)); add created_by/at, updated_by/at; add it to schema.sql and schema_postgres.sql; write a guarded self-applying step if the live database must change.'],
                   ['A form that saves', 'One _insert_X_from_form / _update_X_from_form helper used by both website and phone routes; validate every number with _parse_money; return (ok, message) and SHOW the message; resolve names with get_or_create_*; redirect after POST.'],
                   ['A screen', 'Put its numbers in an _X_data function shared by website and phone; add its routes to the right screen in _MODULE_ENDPOINTS (a test fails otherwise); wrap its menu entries in can(\'Screen\'); read company settings through _get_all_settings(company_id).'],
                   ['Money logic', 'Change the shared helper, never a screen; add a scenario to the audit harness (party / vendor / the relevant stage) and run the full run; compare every screen that shows the number.'],
                   ['A setting or secret', 'Add the key to ALL_SETTING_KEYS and to a group in _SETTINGS_FIELD_GROUPS; if secret, add to _SECRET_SETTING_KEYS (never printed back; blank keeps the saved value).'],
                   ['An upload', 'Save under uploads/<type>/<company_id>/; restrict type and size; serve private documents only to signed-in users.'],
                   ['A call to a paid or outside service', 'Opt-in switch, hard timeout, failure message that never leaks details, tests with the network blocked.'],
                   ['A new company-specific text', 'Never hard-code the company name, address or GSTIN in a page or PDF; use get_company_name(company_id) and settings.']], [22, 78], bold_first=True))
    st.append(H2('55.1 Roadmap'))
    st += bullets(['<b>Before real users:</b> confirm the persistent disk and daily backups on Render, set SECRET_KEY, rotate old keys, make sure exactly one Administrator exists there, test on real phones.',
                   '<b>Soon:</b> fix the integrity gaps in Chapter 52.1, put uploads on the disk with type/size limits and image shrinking, add a disk-usage indicator, pin package versions, add uptime and error alerts, full CSRF tokens and a Content-Security-Policy.',
                   '<b>Quality of the code base:</b> split app.py into modules, one set of design tokens for fonts and spacing, integer-paise money.',
                   '<b>Scale:</b> multi-company (read company_id from the session everywhere, remove the seven hard-coded company PDFs), PostgreSQL with row-level security, more workers.'])
    st.append(Spacer(1, 10))
    st.append(callout('plain', 'End of the blueprint', 'This document was generated from the project files by docs/blueprint/build.py: re-run it after any change and the counts, the tables, the data dictionary, the execution cards and the table lifecycle regenerate from the real code.'))
    return st
