"""Front matter, Part A (the big picture) and Part B (infrastructure and deployment)."""
import datetime
from lib import *
import diagrams as D
import facts as F


def cover():
    return [CoverPage('Architecture Blueprint', 'How the ATS Fleet App is built, from the cloud server at the bottom to the button on a driver\'s phone at the top.',
                      [f'Version of the code described: branch "maintenance", {F.APP_LINES:,} lines in app.py, {F.N_ROUTES} URLs, {F.N_TABLES} database tables',
                       'Written for two readers at once: someone who has never seen code, and the engineer who has to change it.',
                       f'Generated {datetime.date.today().strftime("%d %B %Y")} directly from the project files, so the numbers are real.']),
            NextPageTemplate('body'), PageBreak()]


def how_to_read():
    st = []
    st += [H1('How to read this document'), P('This is a complete map of the system. It is long on purpose: every layer is explained twice — first in plain words with a picture, then in exact technical detail with file names, function names and line numbers.', 'lead')]
    st += [tbl([['If you are...', 'Start with', 'You can skip'],
                ['The owner / manager who wants to understand what you have', 'Part A (the big picture), the diagrams in Parts B\u2013E, and the findings in Chapter 52 (Part H)', 'The tables of functions, the data dictionary and the execution cards'],
                ['A new developer', 'Part A, then Part C (back-end), Part D (database), Part G (front-end); Part E for how each feature works', 'Nothing \u2014 but read Chapter 30 (money) before changing money code'],
                ['Someone deploying or fixing a problem at 2 a.m.', 'Part B and Chapter 53 (runbook, Part I)', 'Everything else until you need it'],
                ['An auditor or security reviewer', 'Chapter 21 (security), Chapter 50 (testing) and Chapter 52 (known gaps)', ''],
                ['Someone who wants to know exactly what one URL or one table does', 'Part F: Chapter 41 (a card for every URL), Chapter 42 (the life of every table), Chapter 43 (screen to data)', '']], [30, 40, 30], bold_first=True)]
    st += [Spacer(1, 8)]
    st += [callout('plain', 'Conventions used here', 'Names in <font face="Courier">this typeface</font> are real file, function, table or setting names you can search for. "Line 10071" means line 10071 of app.py at the time of writing. Diagrams are numbered Figure 1, 2, 3… and are drawn from the real design, not from a template.'),
           callout('tip', 'One idea carries the whole design', 'Every screen — website or phone — is produced by the same Python program reading the same database file. There is only one copy of every business rule (how a bill is calculated, how a payment is applied), so two screens can never disagree. Most of the testing in this project exists to prove exactly that.')]
    st += [PageBreak()]
    return st


def toc_page(toc):
    return [H1('Contents'), toc, PageBreak()]


def part_a():
    st = [NextPageTemplate('divider'), PageBreak(), PartDivider('A', 'The big picture', 'What the system is, who uses it, the words you need, and the technology in one table.',
                                                            ['What the app does and for whom', 'Plain-language glossary', 'Everything on one page (system map)', 'The layer cake: base to top', 'Technology stack with versions', 'Map of the project folders']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('1. What this system is'))
    st.append(P('The ATS Fleet App is the day-to-day operating system of a road-transport business, Anil Transport Service. It replaces an Excel workbook (Fleet_Master_Tracker.xlsx) with a web application that records every truck trip, every rupee that is owed or paid, every repair and document, and turns those records into balances, invoices and performance reports.', 'lead'))
    st += stat_row([(f'{F.APP_LINES:,}', 'lines in app.py'), (str(F.N_ROUTES), 'URLs (pages and actions)'), (str(F.N_TABLES), 'database tables'), (str(F.T_ALL), 'page templates'), ('17', 'automated test stages')])
    st.append(H2('1.1 Who uses it, and how'))
    st += bullets(['<b>The Administrator</b> (one person) controls company settings, bank and invoice details, users and what each user may see.',
                   '<b>Managers and staff</b> book trips, record payments, enter maintenance and expenses, and generate invoices, with as much access as the Administrator grants (Full, Read Only, or Limited to chosen screens).',
                   '<b>People on the road or at the yard</b> use the same system as an installable phone app (a "PWA") that looks like a native app but is really the same web server.',
                   'Today about five people use it; the design has room for a few dozen at once.'])
    st.append(H2('1.2 The journey of one trip through the system'))
    st.append(P('The best way to understand the architecture is to follow one piece of business through it. Every step below is a real function in the code; later parts of this document explain each in detail.'))
    st.append(tbl([['Step', 'What happens in business terms', 'What happens in the system'],
                   ['1', 'A truck is booked to carry goods for a customer (a "party").', 'The Add Trip form posts to <font face="Courier">add_trip</font> (website) or <font face="Courier">app_add_trip</font> (phone). Both call the same <font face="Courier">_insert_trip_from_form</font>, so a trip is calculated identically from either screen.'],
                   ['2', 'The freight is worked out: quantity × rate (or a fixed amount), plus extras such as detention, minus deductions such as shortage or TDS.', '<font face="Courier">_trip_freight</font> and the billed-amount formula; every number first passes <font face="Courier">_parse_money</font> (no letters, no NaN, no negatives).'],
                   ['3', 'Fuel and a driver advance are given; for a hired truck the owner\'s terms are recorded.', 'Stored on the same <font face="Courier">trips</font> row (80 columns); fuel pumps and owners become <font face="Courier">vendors</font> automatically.'],
                   ['4', 'The customer pays some money, maybe before the trip is finished.', 'Typed on the trip, or recorded from the ledger with the payment popup: <font face="Courier">_insert_party_payment_from_form</font> splits it over the oldest pending trips and writes <font face="Courier">payment_allocations</font>.'],
                   ['5', 'The trip is finished; shortage or extra expenses are settled.', '<font face="Courier">end_trip</font> / <font face="Courier">_end_trip_from_form</font> records the end date and corrections.'],
                   ['6', 'An invoice is made for a batch of trips.', 'Invoice Center → review gate → <font face="Courier">invoice_center_generate</font> → ReportLab PDF; rows go to <font face="Courier">invoice_batches</font> and friends.'],
                   ['7', 'The owner wants to know who owes what, and whether the month made money.', 'The ledger engine computes balances from the same allocations; Dashboard and Business Performance read the same numbers.'],
                   ['8', 'Someone asks "was that trip profitable on that route?"', 'Route Analytics and Performance group the same trips by route, driver and vehicle.']], [7, 36, 57], bold_first=True))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'Own versus hired trucks', 'A trip on one of the company\'s own trucks costs fuel, advances, tolls and so on. A trip on a hired truck costs what the vehicle owner is paid; fuel and advances on a hired trip sit inside that owner payment and are not counted twice (only what the <i>company</i> itself paid is deducted from the owner\'s dues). This one rule shows up in the vendor ledger, the payment popup, owner invoices, the Dashboard and Business Performance, and tests keep them identical.'))
    st.append(PageBreak())

    st.append(H1('2. Plain-language glossary'))
    st.append(P('If a word in this document is unfamiliar, it is probably here.', 'lead'))
    g = [['Server', 'A computer that is always on and answers requests from other computers. Here: a Render cloud machine.'],
         ['Browser', 'The program (Chrome, Safari) that shows web pages.'],
         ['Request / response', 'The browser asks for a page or sends a form (request); the server answers with a page, a file or data (response).'],
         ['HTTP / HTTPS', 'The language of the web. HTTPS is the same, scrambled so nobody on the way can read it (the padlock).'],
         ['Python', 'The programming language the whole back-end is written in.'],
         ['Flask', 'A Python toolkit for building websites: it matches a web address to a Python function (a "route") and sends back the result.'],
         ['Route', 'One web address handled by one function, e.g. <font face="Courier">/trips/add</font> → <font face="Courier">add_trip()</font>. This app has ' + str(F.N_ROUTES) + '.'],
         ['Template (Jinja2)', 'An HTML page with blanks. The server fills the blanks with real data before sending it. ' + str(F.T_ALL) + ' templates make all the screens.'],
         ['Front-end / back-end', 'Front-end: what runs in the browser (HTML, CSS, JavaScript). Back-end: what runs on the server (Python and the database).'],
         ['CSS / JavaScript', 'CSS = colours, sizes, layout. JavaScript = small programs that make a page react without reloading.'],
         ['Database', 'Organised storage for records. Here: one SQLite file, <font face="Courier">fleet.db</font>, holding ' + str(F.N_TABLES) + ' tables.'],
         ['SQLite', 'A database that lives in a single file instead of a separate server. Simple, fast and perfect for a small team.'],
         ['Table / row / column', 'A table is like one Excel sheet; a row is one record (one trip); a column is one field (the date).'],
         ['SQL', 'The language used to ask the database questions and make changes.'],
         ['Index', 'A ready-made lookup list (like a book index) that makes searching a big table fast.'],
         ['Foreign key', 'A column that points at a row in another table, e.g. a trip\'s <font face="Courier">party_id</font> points at a party.'],
         ['Transaction / commit', 'A group of changes that must all happen or none; "commit" makes them permanent.'],
         ['WAL mode', 'A SQLite setting that lets people read while someone else is saving, so screens do not freeze.'],
         ['Session / cookie', 'After login the server hands the browser a small signed note (a cookie) that says who you are; the browser sends it back with every click.'],
         ['Hash', 'A one-way scramble. Passwords are stored as hashes, so even the database owner cannot read them.'],
         ['OTP', 'One-time password: a 6-digit code sent by SMS, valid for 5 minutes, usable once.'],
         ['MSG91 / DLT', 'MSG91 is the Indian company that delivers the SMS. DLT is India\'s mandatory registration of SMS templates and senders.'],
         ['gunicorn', 'The production web-server program that runs the Flask app with several workers at once.'],
         ['Worker / thread', 'A worker is a copy of the app running as its own process; a thread is one of its lanes. Here 2 workers × 4 threads = 8 requests at the same time.'],
         ['Render', 'The cloud company that hosts the app (a "web service" with a "persistent disk").'],
         ['Persistent disk', 'Storage that survives restarts and redeployments. Without it, a hosted file system is wiped every time.'],
         ['Snapshot / backup', 'A saved copy of data at one moment, used to go back after a mistake or disaster.'],
         ['Migration', 'A scripted change to the database layout (add a column, add an index) done safely and repeatably.'],
         ['PWA', 'Progressive Web App: a website that can be installed on a phone home screen and opens full-screen like an app.'],
         ['Service worker', 'A tiny background script of a PWA. Here it only keeps two icons ready; it deliberately never stores data.'],
         ['API', 'A way for programs to talk to each other. The app calls APIs of MSG91, eChallan and OpenStreetMap.'],
         ['Ledger', 'The running account of one party or vendor: debits, credits and a balance after each line.'],
         ['Party / vendor', 'Party = a customer you carry goods for (they owe you). Vendor = someone you pay: truck owners, fuel pumps, workshops.'],
         ['LR', 'Lorry Receipt: the consignment note number that identifies a trip for the customer.'],
         ['Allocation', 'The record that says "this payment of Rs 50,000 settled these trips".'],
         ['Multi-tenancy', 'One system serving several separate companies. Prepared for (every table has company_id), not switched on yet.'],
         ['Test harness', 'A program that drives the app automatically with thousands of invented scenarios to catch wrong numbers.']]
    st.append(tbl([['Word', 'Meaning']] + g, [20, 80], bold_first=True))
    st.append(PageBreak())

    st.append(H1('3. The whole system on one page'))
    st.append(P('Read Figure 1 from left to right: people use the website or the phone app; their requests travel over the internet to one web service on Render; the service holds both the program and the database file; and three outside services help with SMS, maps and vehicle data. The bottom row shows how work gets in (the developer\'s laptop and GitHub) and how safety copies get out (your Mac).'))
    st.append(D.d_context())
    st.append(callout('plain', 'The most important fact about this picture', 'There is <b>one</b> server program and <b>one</b> database file. There is no separate "mobile back-end", no cache server, no message queue. That keeps it cheap and simple, and it is why backups and the persistent disk matter so much: the single file <i>is</i> the business.'))
    st.append(PageBreak())

    st.append(H1('4. The layer cake: base to top'))
    st.append(P('Every web application is a stack of layers, each one standing on the one below. Figure 2 names them from the infrastructure at the base (1) to the people at the top (9). When something goes wrong, the first question is always "which layer?" — a slow page is usually layer 3 or 4 (database), a wrong colour is layer 8, a refused login is layer 7.'))
    st.append(D.d_layers())
    v = F.VER
    st.append(H2('4.1 Technology stack, with exact versions'))
    st.append(tbl([['Layer', 'Technology', 'Version', 'Plain explanation', 'Where'],
                   ['Language', 'Python', v['python'], 'The language of the back-end', 'all <font face="Courier">.py</font> files'],
                   ['Web framework', 'Flask', v['flask'], 'Matches URLs to functions, handles forms, sessions, cookies', '<font face="Courier">app.py</font>'],
                   ['Web toolkit', 'Werkzeug', v['werkzeug'], 'Low-level web parts under Flask: password hashing, proxy fix, routing', 'imported by Flask'],
                   ['Templates', 'Jinja2 / MarkupSafe', f"{v['jinja2']} / {v['markupsafe']}", 'Fills HTML pages with data; escapes text so it cannot inject code', f'<font face="Courier">templates/</font> ({F.T_ALL} files)'],
                   ['Cookie signing', 'itsdangerous', v['itsdangerous'], 'Signs the session cookie so it cannot be forged', 'inside Flask'],
                   ['Production server', 'gunicorn (gthread)', v['gunicorn'], 'Runs the app with 2 workers × 4 threads on Render', '<font face="Courier">Procfile</font>'],
                   ['Database', 'SQLite', v['sqlite'], 'A whole database in one file, WAL mode', '<font face="Courier">fleet.db</font>'],
                   ['PDF', 'ReportLab', v['reportlab'], 'Draws invoices, ledgers, payslips, reports as PDFs', 'inside <font face="Courier">app.py</font>'],
                   ['Excel', 'openpyxl', v['openpyxl'], 'Reads the toll Excel import, writes every Excel export', 'inside <font face="Courier">app.py</font>'],
                   ['HTTP client', 'requests', v['requests'], 'Calls MSG91, eChallan, OSRM, Nominatim, Photon, Overpass', 'app.py, <font face="Courier">providers/</font>'],
                   ['Scheduler', 'APScheduler', v['apscheduler'], 'Hourly tick that runs background syncs (locally; on Render an outside cron)', '<font face="Courier">scheduler.py</font>'],
                   ['Map library', 'Leaflet (bundled)', '1.x', 'Draws the Route Finder map; shipped inside the project, no CDN', '<font face="Courier">static/vendor/leaflet</font>'],
                   ['Font', 'Poppins (Google Fonts)', '—', 'The one external resource a page loads: only the phone app\'s sign-in, SMS-code and sign-up screens use it. Every other page uses system fonts (Segoe UI, San Francisco).', 'templates/app/login*.html, signup, verify_otp'],
                   ['Charts', 'hand-drawn SVG / CSS', '—', 'No chart library: bars, lines, donuts are SVG and CSS built by the templates', 'dashboard, performance, route analytics'],
                   ['Hosting', 'Render', '—', 'Runs the web service, gives HTTPS, a persistent disk and daily snapshots', 'cloud'],
                   ['Source control', 'git + GitHub', '—', 'History of every change; Render deploys from it', 'repository'],
                   ['SMS', 'MSG91 Send-OTP API', 'v5', 'Delivers login codes by SMS in India', '<font face="Courier">_send_otp_msg91</font>'],
                   ['Vehicle data', 'eChallan API', '—', 'RC details and traffic challans (paid per call, switched off by default)', '<font face="Courier">providers/echallan_client.py</font>'],
                   ['Maps and routes', 'OSRM, Nominatim, Photon, Overpass', '—', 'Free OpenStreetMap-based services behind the Route Finder', '<font face="Courier">providers/osm_routes.py</font>'],
                   ['Test tools', 'Flask test client, beautifulsoup4, lxml (dev only)', '—', 'Drive the app and read the rendered pages in tests', '<font face="Courier">test_*.py</font>']],
                  [12, 17, 9, 38, 24], bold_first=True))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'What is deliberately NOT in the stack', 'No JavaScript framework (React, Vue), no build step, no Node.js, no ORM, no Redis, no Celery, no separate API server. Fewer moving parts means fewer things to break, patch or pay for. The price is that app.py is large (Chapter 55.1 discusses splitting it).'))
    st.append(PageBreak())

    st.append(H1('5. Map of the project folders'))
    st.append(P('Everything lives in one folder. This table lists every top-level item and what it is for; Chapter 54 (Part I) has the file-by-file reference.'))
    rows = [['Item', 'Kind', 'What it is'],
            ['app.py', 'code', f'The application: {F.APP_LINES:,} lines, {F.N_FUNCS} top-level functions ({F.N_ROUTES} URLs + {F.N_HELPERS} helpers/hooks). Back-end, business rules, PDF/Excel makers, mobile (/app/) routes.'],
            ['db.py', 'code', 'Thin database wrapper — the one place a future switch to PostgreSQL would be wired in.'],
            ['scheduler.py, compliance_service.py', 'code', 'Background syncs of vehicle documents, RC and challans.'],
            ['providers/', 'code', 'Clients for outside services: eChallan (RC/challan), OpenStreetMap services (routes), and mock providers for fitness/PUC/permit/insurance.'],
            ['config/apis.py', 'code', 'Defaults for compliance providers (keys come from environment variables).'],
            ['templates/', 'front-end', f'{F.T_ALL} HTML templates: {F.T_ROOT} website pages, {F.T_PART} partials (tabs/modals), {F.T_APP} phone-app files.'],
            ['static/', 'front-end', 'CSS, JavaScript, icons, images, the Leaflet library and uploaded files (logo, documents).'],
            ['schema.sql, schema_postgres.sql', 'database', 'The authoritative SQLite layout and the prepared PostgreSQL twin (with real multi-company isolation).'],
            ['migrate_*.sql / .py', 'database', f'{len(F.MIGRATIONS)} historical, documented database changes. The important ones are also applied automatically at start-up.'],
            ['fleet.db', 'data', 'THE data (never committed to git; backed up separately).'],
            ['Procfile', 'deploy', 'The one line Render runs to start the app.'],
            ['requirements.txt', 'deploy', 'Python packages to install.'],
            [f'test_*.py ({len(F.TEST_FILES)} files)', 'quality', 'Automated tests; test_balance_audit.py (3,000+ lines) is the big harness.'],
            ['load_test.py', 'quality', 'Seeds 12,000 trips and times every screen.'],
            ['db_health_check.py, backup_db.py, pull_backup.py, restore_check.py', 'operations', 'Read-only data audit, local backup, off-site pull from Render, and proof-of-restore.'],
            ['CLAUDE.md', 'rules', 'The project rules: keep multi-company readiness, test on disposable copies, deploy database and code separately.'],
            ['*.md reports', 'docs', 'DEPLOY_TO_CLOUD, BACKUP_AND_RESTORE, production_readiness_report, code_health_report, balance_audit_findings, load_test_report.'],
            ['docs/blueprint/', 'docs', 'The program that generates this PDF from the real files.'],
            ['archive/, audit_reports/', 'misc', 'Retired screens and earlier test reports.']]
    st.append(tbl(rows, [27, 11, 62], bold_first=True, mono_cols=()))
    st.append(PageBreak())
    return st
