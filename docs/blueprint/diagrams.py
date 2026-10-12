"""All diagrams of the blueprint, drawn with the Diagram toolkit in lib.py (vector graphics, no image files)."""
from lib import *
import facts as F

W = CONTENT_W


def d_context():
    g = Diagram(W, 372)
    # people
    g.lane(0, 120, 112, 244, 'PEOPLE AND DEVICES', GREY_L)
    g.box(8, 292, 96, 50, 'Owner / Administrator', 'laptop browser', BLUE_L, BLUE)
    g.box(8, 226, 96, 50, 'Managers and staff', 'website in a browser', BLUE_L, BLUE)
    g.box(8, 160, 96, 50, 'Staff on phones', 'installed mobile app (PWA)', ACCENT_L, ACCENT)
    g.text(8, 132, 'about 5 people today', 6.8, STEEL)
    # internet
    g.d.add(Rect(120, 130, 22, 232, rx=4, ry=4, fillColor=INK, strokeColor=None))
    for i, ch in enumerate('HTTPS  INTERNET'):
        g.text(131, 345 - i * 14.2, ch, 8, WHITE, True, 'middle')
    for y in (317, 251, 185):
        g.arrow([(104, y), (120, y)], BLUE if y != 185 else ACCENT, both=True)
    # render
    g.lane(150, 120, 222, 244, 'RENDER CLOUD  ·  one web service', ACCENT_L, ACCENT)
    g.box(160, 298, 202, 38, 'Render edge and proxy', 'HTTPS certificate · forwards the real visitor address', WHITE, ACCENT, size=7.8)
    g.box(160, 244, 202, 40, 'gunicorn  ·  2 workers x 4 threads', '8 requests at the same time', WHITE, ACCENT, size=7.8)
    g.box(160, 190, 202, 40, 'Flask application  (app.py)', '199 URLs · business rules · PDF and Excel makers', INK, INK, WHITE, ACCENT_L, size=8.6)
    g.cyl(160, 128, 104, 52, 'SQLite  fleet.db', 'persistent disk', BLUE_L, BLUE)
    g.box(272, 134, 90, 40, 'Uploads', 'insurance, toll receipts, logo', WHITE, ACCENT, size=7.2)
    for a, b in (((261, 298), (261, 284)), ((261, 244), (261, 230)), ((212, 190), (212, 180))):
        g.arrow([a, b], ACCENT)
    g.arrow([(317, 190), (317, 174)], ACCENT)
    g.text(165, 108, 'Render takes a snapshot of the disk every 24 hours (kept 7+ days)', 6.8, STEEL)
    # externals
    g.lane(384, 120, 109, 244, 'OUTSIDE SERVICES', GREY_L)
    g.box(390, 292, 97, 52, 'MSG91', 'sends the login code by SMS', PURPLE_L, PURPLE, size=7.6)
    g.box(390, 226, 97, 58, 'OpenStreetMap services', 'routes, place search, toll plazas (Route Finder)', TEAL_L, TEAL, size=7.2)
    g.box(390, 160, 97, 58, 'eChallan API', 'RC and challan data for vehicles (paid per call)', AMBER_L, AMBER, size=7.2)
    g.box(390, 128, 97, 24, 'Google Fonts', 'phone sign-in font only', WHITE, STEEL, size=7)
    g.arrow([(362, 210), (390, 318)], PURPLE, dashed=True)
    g.arrow([(362, 205), (390, 255)], TEAL, dashed=True)
    g.arrow([(362, 200), (390, 190)], AMBER, dashed=True)
    # backup
    g.box(150, 20, 222, 62, 'Your Mac  ·  pull_backup.py (daily)', 'asks the app for a verified copy of the database and keeps the newest 30 under ~/Downloads/fleet_db_backups/render', GREEN_L, GREEN, size=8)
    g.arrow([(262, 120), (262, 82)], GREEN, label='token-protected download  /internal/backup', loff=(66, 0), both=True)
    g.box(384, 20, 109, 62, 'GitHub', 'source code (private repository); every deploy starts from it', WHITE, STEEL, size=7.6)
    g.box(0, 20, 138, 62, 'Developer laptop', 'edits code, runs 17 test stages, commits and pushes', WHITE, STEEL, size=7.8)
    g.arrow([(138, 51), (150, 51)], STEEL)
    g.arrow([(372, 51), (384, 51)], STEEL, dashed=True)
    return figure(g, 'Figure 1 — The whole system on one page: who uses it, where it runs, and what it talks to.')


def d_layers():
    g = Diagram(W, 408)
    layers = [
        ('9', 'PEOPLE AND DEVICES', 'Chrome / Safari on laptops and phones · the installed phone app (a PWA)', BLUE_L, BLUE),
        ('8', 'FRONT-END  —  what the user sees', 'HTML pages made by Jinja2 templates (107 files) · CSS (style.css for the website, app.css for the phone app) · plain JavaScript · Leaflet map · charts drawn as SVG/CSS', ACCENT_L, ACCENT),
        ('7', 'WEB LAYER  —  Flask 3.1', '199 URL routes · request hooks in a fixed order: cross-site check → login → access rules → route → security headers', PURPLE_L, PURPLE),
        ('6', 'BUSINESS RULES  —  Python functions in app.py', 'trip freight maths · ledger and balance engine · payment allocation · invoice builder · analytics · validation · roles and Limited Access', PURPLE_L, PURPLE),
        ('5', 'DOCUMENT AND FILE ENGINES', 'ReportLab 5.0 makes PDFs (invoices, ledgers, payslips, reports) · openpyxl 3.1 makes Excel files · uploaded documents on disk', TEAL_L, TEAL),
        ('4', 'DATA ACCESS', 'get_db() opens a sqlite3 connection (30 s lock wait, row access by column name) wrapped by db.py so a future database swap changes one place', BLUE_L, BLUE),
        ('3', 'DATABASE  —  SQLite 3.50', 'one file, fleet.db · 36 tables · 62 indexes · WAL journal mode · company_id on every table (multi-company ready)', BLUE_L, BLUE),
        ('2', 'RUNTIME', 'Python 3.14 · gunicorn with the gthread worker (2 workers x 4 threads) on Render · Flask dev server on your Mac', GREY_L, STEEL),
        ('1', 'INFRASTRUCTURE', 'Render web service + persistent disk + HTTPS · GitHub · MSG91 (SMS) · OpenStreetMap services · eChallan API · Google Fonts', GREY_L, STEEL),
    ]
    y = 408 - 40
    for num, title, text, fill, stroke in layers:
        g.d.add(Rect(0, y, 36, 36, rx=5, ry=5, fillColor=stroke, strokeColor=None))
        g.text(18, y + 12.5, num, 15, WHITE, True, 'middle')
        g.d.add(Rect(42, y, W - 42, 36, rx=5, ry=5, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
        g.text(50, y + 23.5, title, 8.4, INK, True)
        g.para(50, y + 12.5, W - 62, text, 6.9, STEEL)
        y -= 44
    return figure(g, 'Figure 2 — The layer cake, base (1) to top (9). A request travels from layer 9 down to 3 and the answer climbs back up.')


def d_request():
    g = Diagram(W, 560)
    x0, bw = 0, 270
    steps = [
        ('1  Browser or phone sends a request', 'for example  POST /payment/party/12  (record a payment)', BLUE_L, BLUE),
        ('2  Render proxy  +  gunicorn', 'HTTPS ends here · ProxyFix puts the visitor\'s real address on the request · one of 8 threads takes it', GREY_L, STEEL),
        ('3  before_request: input and origin guards', 'NaN, infinity or absurd numbers in any amount field are refused; a POST whose Origin names another website is refused (403)', PURPLE_L, PURPLE),
        ('4  before_request: require_login', 'private-upload gate → exempt pages (login, OTP, static) → must have a session → 3-hour idle timeout → re-read the user from the database (status, role, access level, screens) on EVERY request', PURPLE_L, PURPLE),
        ('5  Access rules', 'Administrator-only pages → Read Only may only look → Limited Access: is this screen allowed, and is it View or Edit?', PURPLE_L, PURPLE),
        ('6  The route function runs', 'reads the form → checks every number (_parse_money, no NaN/negative) → business rules → SQL with ? placeholders → commit', ACCENT_L, ACCENT),
        ('7  Template renders (or JSON / PDF / Excel)', 'Jinja2 fills base.html or base_app.html; context processors add the menu, company name, can() helper, security flags', TEAL_L, TEAL),
        ('8  after_request hooks', 'security headers → "view-only" layer for read-only screens → no-cache so Back always shows live numbers', PURPLE_L, PURPLE),
        ('9  Response goes back', 'HTML page · redirect · JSON for the phone app · file download', BLUE_L, BLUE),
    ]
    y = 560 - 50
    prev = None
    for t, s, f, st in steps:
        h = 46 if len(s) < 110 else 56
        g.box(x0, y - (h - 46), bw, h, t, s, f, st, size=7.8, align='left') if False else g.box(x0, y - (h - 46), bw, h, t, s, f, st, size=7.8)
        if prev is not None:
            g.arrow([(x0 + bw / 2, prev), (x0 + bw / 2, y - (h - 46) + h)], STEEL)
        prev = y - (h - 46)
        y -= h + 10 - (0)
    # side notes
    nx = 292
    notes = [
        (466, 'A thread is free again the moment the response leaves — nothing is kept in memory between requests except a 1-hour route cache.'),
        (392, 'Wrong passwords, OTP guesses and backup-token guesses are counted in the auth_attempts table, so the limits hold across both workers.'),
        (318, 'The user row is re-read each time, so deactivating someone or changing their access level takes effect on their very next click.'),
        (244, 'If any step refuses, the later steps never run: a blocked user never reaches a route, and a failed validation never reaches the database.'),
        (170, 'Every page is rendered fresh from the live database. The browser is told not to keep a copy (Cache-Control: no-store).'),
        (96, 'Errors: bad numbers become a friendly 400 message, never a crash page; unexpected errors are logged on the server.'),
    ]
    for ny, nt in notes:
        g.d.add(Rect(nx - 8, ny - 46, W - nx + 8, 56, rx=5, ry=5, fillColor=PAPER, strokeColor=LINE))
        g.para(nx, ny - 2, W - nx - 6, nt, 7.2, STEEL)
    return figure(g, 'Figure 3 — Life of one request, in the exact order the code runs it.')


def d_logins():
    g = Diagram(W, 506)
    half = (W - 16) / 2
    # password
    g.lane(0, 0, half, 506, 'A.  PASSWORD LOGIN  (website /login and app /app/login)', BLUE_L, BLUE)
    st = [('Username (mobile or email) + password', None), ('Format check: 10-digit Indian mobile or email', None),
          ('Throttle check: 5 failures per account / 30 per address in 15 minutes?', 'if yes → "Too many attempts" (HTTP 429)'),
          ('Find the user and verify the password hash (werkzeug)', None), ('Inactive account? → refused', None),
          ('Success: clear failure counter, store user id in the signed session cookie', 'remember-me = 30-day cookie, otherwise browser session'),
          ('Write last_login and an access_logs row', None), ('Redirect to Dashboard (or first allowed screen)', None)]
    y = 506 - 68
    prev = None
    for t, s in st:
        h = 40 if s else 34
        g.box(12, y - (h - 34), half - 24, h, t, s, WHITE, BLUE, size=7.4)
        if prev: g.arrow([(half / 2, prev), (half / 2, y - (h - 34) + h)], BLUE)
        prev = y - (h - 34); y -= h + 12
    g.box(12, 8, half - 24, 30, 'Failure: record attempt (user + address), say "Invalid username or password"', None, RED_L, RED, size=7)
    # otp
    x = half + 16
    g.lane(x, 0, half, 506, 'B.  OTP LOGIN BY SMS  (MSG91)', PURPLE_L, PURPLE)
    st = [('Enter 10-digit mobile number', None), ('Limits: 3 codes per number and 10 per address per hour/15 min', None),
          ('Account exists and is active?', 'if not → same "code sent" screen, nothing is sent (no way to probe who has an account)'),
          ('Create a 6-digit code with secrets; store only a keyed hash (HMAC) in login_otps: 5-minute expiry, 0 tries used', None),
          ('MSG91 Send-OTP API delivers the code (template with ##OTP##)', 'a failed send voids the code'),
          ('User types the code', None),
          ('Check: not used, not expired, max 5 tries counted first, compare with constant-time HMAC', None),
          ('Right: mark used, log in. Wrong: "Incorrect OTP"; 5 wrong = code dead', None)]
    y = 506 - 68
    prev = None
    for t, s in st:
        h = 46 if s and len(s) > 50 else (40 if s else 34)
        if len(t) > 80: h = max(h, 44)
        g.box(x + 12, y - (h - 34), half - 24, h, t, s, WHITE, PURPLE, size=7.2)
        if prev: g.arrow([(x + half / 2, prev), (x + half / 2, y - (h - 34) + h)], PURPLE)
        prev = y - (h - 34); y -= h + 10
    return figure(g, 'Figure 4 — The two ways to sign in. In both, the secret decisions happen on the server; the browser only holds a signed cookie.')


def d_access():
    g = Diagram(W, 400)
    g.box(170, 360, 150, 30, 'Request arrives (signed in)', None, BLUE_L, BLUE)
    g.box(170, 312, 150, 34, 'User still Active? Row exists?', 'no → signed out at once', WHITE, STEEL, size=7.8)
    g.box(170, 262, 150, 34, 'Administrator-only page?', 'users, company, logo, backups of settings', WHITE, PURPLE, size=7.8)
    g.box(0, 262, 130, 34, 'Role is not Administrator → 403', None, RED_L, RED, size=7.4)
    g.box(170, 208, 150, 34, 'Which access level?', None, ACCENT_L, ACCENT)
    g.box(0, 140, 150, 56, 'FULL ACCESS', 'everything that is not Administrator-only; export of the whole database allowed', GREEN_L, GREEN, size=8)
    g.box(170, 140, 150, 56, 'READ ONLY', 'every page may be opened; any change is refused (403) except own password, invoice preview/review, route lookups', BLUE_L, BLUE, size=8)
    g.box(343, 140, 150, 56, 'LIMITED ACCESS', 'only the screens the Administrator ticked, each as View or Edit', PURPLE_L, PURPLE, size=8)
    for a, b in (((245, 360), (245, 346)), ((245, 312), (245, 296)), ((245, 262), (245, 242))):
        g.arrow([a, b], STEEL)
    g.arrow([(170, 279), (130, 279)], RED)
    g.arrow([(245, 208), (75, 196)], GREEN); g.arrow([(245, 208), (245, 196)], BLUE); g.arrow([(245, 208), (418, 196)], PURPLE)
    # limited detail
    g.lane(343, 10, 150, 118, 'LIMITED: the decision', WHITE, PURPLE)
    g.para(350, 100, 138, '1. Look the page up in the screen map (every one of 199 routes belongs to one of 9 screens).', 6.9, INK)
    g.para(350, 70, 138, '2. Screen not ticked → friendly "no access" page (JSON for the app).', 6.9, INK)
    g.para(350, 48, 138, '3. Ticked as View → reads only. Ticked as Edit → allowed.', 6.9, INK)
    g.para(350, 26, 138, '4. No Dashboard ticked → open the first allowed screen.', 6.9, INK)
    g.lane(0, 10, 333, 118, 'THE ONE-ADMINISTRATOR RULE', WHITE, ACCENT)
    g.para(8, 100, 318, 'Only ONE active Administrator can exist. Enforced twice: by the add/edit user code and by a database unique index.', 7, INK)
    g.para(8, 78, 318, 'To hand over the role, the Administrator presses "Make Administrator" on another active user: the new person becomes Administrator and the old one becomes Manager (Full Access) in a single step — there is never a moment with two, or with none.', 7, INK)
    g.para(8, 40, 318, 'The only Administrator cannot deactivate, demote or delete themselves; Managers can have Full Access but never see Settings, users or the user log.', 7, INK)
    return figure(g, 'Figure 5 — Who may do what. These checks run on the server for every request, so hiding a button is never the only protection.')


def d_money():
    g = Diagram(W, 470)
    half = W / 2
    g.lane(0, 150, half - 8, 320, 'MONEY COMING IN  —  from parties (customers)', GREEN_L, GREEN)
    g.lane(half + 8, 150, half - 8, 320, 'MONEY GOING OUT  —  to vendors (owners, fuel pumps, workshops)', RED_L, RED)
    # party side
    g.box(10, 392, 218, 44, '1  Trip is booked', 'billed = freight + extra charges − deductions   (freight = quantity x rate, or a fixed amount, or guarantee weight)', WHITE, GREEN, size=7.4)
    g.box(10, 334, 218, 42, '2  Party advance / money typed on the trip', 'stored on the trip row: party_advance, payment_received', WHITE, GREEN, size=7.4)
    g.box(10, 276, 218, 46, '3  Party pays later: "Record payment"', 'ticked trips first, then the OLDEST pending trips; a true surplus stays unallocated', WHITE, GREEN, size=7.4)
    g.box(10, 218, 218, 44, '4  payment_allocations rows', 'one row per trip the payment settled; trip.payment_received is kept in step', WHITE, GREEN, size=7.4)
    g.box(10, 160, 218, 44, '5  Party balance', 'opening + billed − (received + advance) − payments ;  positive = receivable', GREEN_L, GREEN, size=7.4)
    for yy in (392, 334, 276, 218):
        g.arrow([(119, yy), (119, yy - 14)], GREEN)
    # vendor side
    x = half + 18
    g.box(x, 392, 218, 44, '1  Costs arise', 'owner-hire trips (owner cost) · maintenance, tyre, battery and insurance bills · expense bills naming the vendor · fuel/advance the company paid', WHITE, RED, size=7.2)
    g.box(x, 334, 218, 42, '2  Only the company\'s own payments are deducted', 'on a hired trip, fuel or advance paid by the company (not by the owner) reduces what is owed to the owner', WHITE, RED, size=7.2)
    g.box(x, 276, 218, 46, '3  We pay a vendor: "Record payment"', 'order: ticked trips → other owner trips → unpaid maintenance bills → pending expense bills (oldest first)', WHITE, RED, size=7.2)
    g.box(x, 218, 218, 44, '4  payment_allocations rows', 'point at a trip, a maintenance bill OR an expense (exactly one of the three, enforced by a CHECK)', WHITE, RED, size=7.2)
    g.box(x, 160, 218, 44, '5  Vendor balance', 'opening + bills − payments ;  negative = payable. A vendor linked to a party is merged into the party ledger', RED_L, RED, size=7.2)
    for yy in (392, 334, 276, 218):
        g.arrow([(x + 109, yy), (x + 109, yy - 14)], RED)
    # engine
    g.box(60, 84, W - 120, 46, 'LEDGER ENGINE   —   _get_party_ledger_entries / _get_vendor_ledger_entries / _bulk_balances', 'one formula; the fast bulk version computes all 250 accounts in a few queries and is proven identical to the slow per-account version by a test', INK, INK, WHITE, ACCENT_L, size=8)
    g.arrow([(119, 160), (119, 130)], GREEN); g.arrow([(x + 109, 160), (x + 109, 130)], RED)
    g.box(0, 6, 158, 52, 'Ledger screens', 'party and vendor ledgers, running balance, Excel and PDF export', BLUE_L, BLUE, size=7.4)
    g.box(168, 6, 158, 52, 'Dashboard and Business Performance', 'receivables / payables, profit = billed − costs', BLUE_L, BLUE, size=7.4)
    g.box(336, 6, 157, 52, 'Trips screen "Balance due"', 'always agrees with the ledger because both read the same allocations', BLUE_L, BLUE, size=7.4)
    for cx in (79, 247, 414):
        g.arrow([(W / 2 + (cx - W / 2) * 0.35, 84), (cx, 58)], STEEL)
    return figure(g, 'Figure 6 — How money moves through the app. Every screen that shows a balance reads the same allocations, so they cannot disagree.')


def d_invoice():
    g = Diagram(W, 300)
    xs = [0, 100, 200, 300, 400]
    w = 93
    steps = [('1  Invoice Center', 'choose type: Party, Hired-vehicle (freight bill), Tax or Bill; filter by party, date, LR', BLUE_L, BLUE),
             ('2  Pick trips', 'each trip shows what is already invoiced; opt-in charges can be included', BLUE_L, BLUE),
             ('3  Review gate', 'every selected LR link must be opened and checked; Generate/Draft stay locked until then (Preview is always allowed)', AMBER_L, AMBER),
             ('4  Generate / Draft / Preview', 'numbers and dates validated; invoice number from prefix + next number in Settings', ACCENT_L, ACCENT),
             ('5  Saved + PDF', 'batch, trips, extra lines and exclusions stored; ReportLab builds the PDF', GREEN_L, GREEN)]
    for x, (t, s, f, st) in zip(xs, steps):
        g.box(x, 196, w, 96, t, s, f, st, size=7.6)
    for i in range(4):
        g.arrow([(xs[i] + w, 244), (xs[i + 1], 244)], STEEL)
    g.lane(0, 6, W, 170, 'WHAT GETS STORED  (tables)', PAPER)
    g.box(10, 106, 112, 50, 'invoice_batches', 'number, type, party or vendor, dates, GST/TDS rates, status, payment status', WHITE, STEEL, size=7.2)
    g.box(132, 106, 112, 50, 'invoice_batch_trips', 'which trips are on which invoice', WHITE, STEEL, size=7.2)
    g.box(254, 106, 112, 50, 'invoice_batch_items', 'extra charge and deduction lines (with optional vendor)', WHITE, STEEL, size=7.2)
    g.box(376, 106, 108, 50, 'invoice_batch_charge_exclusions', 'trip charges left out of this invoice', WHITE, STEEL, size=7.2)
    g.box(10, 24, 230, 66, 'Layout rule', 'up to 14 extra lines the PDF uses the compact two-column layout; more than that it switches to a stacked full-width layout so 200-trip invoices never crash the PDF builder', GREEN_L, GREEN, size=7.2)
    g.box(254, 24, 230, 66, 'Old per-trip invoices', 'invoices (per trip) and invoice_items remain for single-trip bills: preview, edit lines, PDF', GREEN_L, GREEN, size=7.2)
    return figure(g, 'Figure 7 — From a pile of trips to a numbered PDF invoice.')


def _er(title_note, boxes, links, h):
    g = Diagram(W, h)
    pos = {}
    for key, (x, y, w, hh, t, s, f, st) in boxes.items():
        g.box(x, y, w, hh, t, s, f, st, size=7.6)
        pos[key] = (x, y, w, hh)
    def edge(a, b, label, sa='r', sb='l', off=0):
        ax, ay, aw, ah = pos[a]; bx, by, bw, bh = pos[b]
        pa = {'r': (ax + aw, ay + ah / 2), 'l': (ax, ay + ah / 2), 't': (ax + aw / 2, ay + ah), 'b': (ax + aw / 2, ay)}[sa]
        pb = {'r': (bx + bw, by + bh / 2), 'l': (bx, by + bh / 2), 't': (bx + bw / 2, by + bh), 'b': (bx + bw / 2, by)}[sb]
        g.arrow([pa, pb], STEEL, label=label, width=0.9)
    for a, b, lab, sa, sb in links:
        edge(a, b, lab, sa, sb)
    return g


def d_er_core():
    g = Diagram(W, 372)
    B = {
        'parties': (0, 300, 120, 56, 'parties', '70 rows \u00B7 customers', GREEN_L, GREEN),
        'trips': (186, 262, 120, 94, 'trips', '472 rows \u00B7 80 columns: route, rates, charges, deductions, fuel, advances, owner terms, timing', ACCENT_L, ACCENT),
        'vehicles': (372, 300, 120, 56, 'vehicles', '60 rows \u00B7 own or hired', BLUE_L, BLUE),
        'payments': (0, 176, 120, 56, 'payments', '224 rows \u00B7 received or paid', WHITE, STEEL),
        'alloc': (186, 160, 120, 66, 'payment_allocations', '22 rows \u00B7 each payment split into trip OR maintenance OR expense', WHITE, STEEL),
        'vendors': (372, 176, 120, 56, 'vendors', '38 rows \u00B7 owners, fuel pumps, workshops', RED_L, RED),
        'over': (0, 36, 120, 56, 'overheads', '5 rows \u00B7 expense bills', AMBER_L, AMBER),
        'maint': (186, 36, 120, 56, 'maintenance', '452 rows \u00B7 service, tyre, battery, insurance bills', BLUE_L, BLUE),
    }
    for x, y, w, h, t, sb, f, st in B.values():
        g.box(x, y, w, h, t, sb, f, st, size=7.6)
    A = lambda pts, lab=None, **k: g.arrow(pts, STEEL, label=lab, width=0.9, **k)
    A([(120, 328), (186, 322)], '1 : many')
    A([(372, 328), (306, 322)], '1 : many')
    A([(60, 300), (60, 232)], '1 : many')
    A([(120, 204), (186, 196)], 'split into')
    A([(246, 226), (246, 262)], 'settles')
    A([(390, 232), (390, 280), (306, 280)], 'owner / fuel / misc', lpos=0.6, loff=(0, 5))
    A([(432, 176), (432, 130), (60, 130), (60, 176)], 'vendors are paid too', lpos=0.5, loff=(0, 4))
    A([(246, 160), (246, 92)], 'or a bill', loff=(24, 0))
    A([(120, 64), (186, 64)], None)
    g.text(128, 70, 'or an', 6.8, STEEL)
    g.text(0, 8, 'Not drawn here: invoices (see Figure 7) and toll_entries (Figure 9) \u2014 both also point at trips.', 7, STEEL)
    return figure(g, 'Figure 8 \u2014 Data model, part 1: trading and money. The trips table is the heart; every payment is split into allocations that point at what it settled.')


def d_er_fleet():
    boxes = {
        'veh': (186, 270, 120, 56, 'vehicles', 'the asset every record hangs on', BLUE_L, BLUE),
        'comp': (0, 320, 120, 50, 'vehicle_compliance', 'fitness, PUC, permit dates (54)', WHITE, STEEL),
        'chal': (0, 250, 120, 50, 'vehicle_challans', 'traffic challans from eChallan (9)', WHITE, STEEL),
        'ins': (372, 320, 120, 50, 'insurance_policies', 'policies, documents (18)', WHITE, STEEL),
        'mnt': (186, 150, 120, 66, 'maintenance', 'the bill: category, amount, paid, vendor, km, next due', AMBER_L, AMBER),
        'tyre': (0, 150, 120, 50, 'tyre_stock', 'tyres bought and fitted (2)', WHITE, STEEL),
        'bat': (0, 80, 120, 50, 'batteries + battery_checks', 'serial, warranty, health (2 + 7)', WHITE, STEEL),
        'urea': (372, 150, 120, 50, 'urea_transactions', 'AdBlue stock in / out (4)', WHITE, STEEL),
        'cexp': (372, 80, 120, 50, 'compliance_expenses', 'fees for fitness, permit, PUC (2)', WHITE, STEEL),
        'vend': (186, 40, 120, 50, 'vendors', 'who was paid', RED_L, RED),
        'toll': (372, 250, 120, 50, 'toll_entries', 'tolls per vehicle / trip (94)', WHITE, STEEL),
    }
    links = [('veh', 'comp', 'has', 'l', 'r'), ('veh', 'chal', 'has', 'l', 'r'), ('veh', 'ins', 'has', 'r', 'l'), ('veh', 'toll', 'has', 'r', 'l'),
             ('veh', 'mnt', 'has', 'b', 't'), ('mnt', 'tyre', 'creates', 'l', 'r'), ('mnt', 'bat', 'creates', 'l', 'r'), ('mnt', 'urea', 'creates', 'r', 'l'),
             ('mnt', 'cexp', 'creates', 'r', 'l'), ('mnt', 'vend', 'paid to', 'b', 't')]
    g = _er('', boxes, links, 392)
    return figure(g, 'Figure 9 — Data model, part 2: the fleet. Insurance, tyres, batteries, urea and compliance fees all produce a maintenance bill so the money side stays in one place.')


def d_er_people():
    boxes = {
        'emp': (186, 250, 120, 60, 'employees', 'drivers and staff (2)', PURPLE_L, PURPLE),
        'sal': (0, 250, 120, 50, 'salaries + salary_items', 'monthly pay (6)', WHITE, STEEL),
        'adv': (372, 250, 120, 50, 'advances', 'given and repaid (4)', WHITE, STEEL),
        'att': (186, 150, 120, 50, 'attendance', 'present / absent per day', WHITE, STEEL),
        'usr': (0, 40, 120, 66, 'users', '4 rows · role, access level, screens, status', BLUE_L, BLUE),
        'log': (150, 40, 90, 50, 'access_logs', 'sign-ins (42)', WHITE, STEEL),
        'otp': (252, 40, 110, 50, 'login_otps + auth_attempts', 'SMS codes, throttling', WHITE, STEEL),
        'set': (374, 40, 118, 66, 'settings', '42 rows · company, bank, invoice, tax, keys', AMBER_L, AMBER),
        'co': (374, 150, 118, 50, 'companies', '1 row (multi-company ready)', WHITE, STEEL),
        'sync': (0, 150, 120, 50, 'sync_log', 'background sync history', WHITE, STEEL),
    }
    links = [('emp', 'sal', 'is paid', 'l', 'r'), ('emp', 'adv', 'borrows', 'r', 'l'), ('emp', 'att', 'marked', 'b', 't'), ('usr', 'log', 'writes', 'r', 'l'),
             ('set', 'co', 'belongs to', 't', 'b')]
    g = _er('', boxes, links, 330)
    return figure(g, 'Figure 10 — Data model, part 3: people and the system. Employees are linked to salaries and advances by name; users and settings run the app itself.')


def d_deploy():
    g = Diagram(W, 250)
    steps = [('Developer laptop', 'edit code · run the 17 test stages on throw-away databases', BLUE_L, BLUE),
             ('git commit and push', 'to the private GitHub repository (branches: main, maintenance)', GREY_L, STEEL),
             ('Render builds', 'pip install -r requirements.txt', ACCENT_L, ACCENT),
             ('Render starts', 'gunicorn app:app --worker-class=gthread --workers=2 --threads=4 --timeout=60', ACCENT_L, ACCENT),
             ('App wakes up', 'imports app.py: self-applying schema steps run, hooks register', GREEN_L, GREEN)]
    w, gap = 91, 9.5
    for i, (t, s, f, st) in enumerate(steps):
        x = i * (w + gap)
        g.box(x, 150, w, 92, t, s, f, st, size=7.6)
        if i: g.arrow([(x - gap, 196), (x, 196)], STEEL)
    g.lane(0, 6, W, 124, 'WHAT THE APP DOES BY ITSELF ON EVERY START  (safe to repeat, safe with 2 workers starting together)', PAPER)
    items = ['add payment_allocations.overhead_id (backup copy first)', 'create ~36 performance indexes', 'switch the database to WAL mode', 'create login_otps and auth_attempts',
             'make or load the private secret key', 'enforce one active Administrator (unique index)', 'delete any saved Twilio settings']
    for i, t in enumerate(items):
        col, row = i % 4, i // 4
        g.box(8 + col * 121, 62 - row * 34 + 22, 115, 28, t, None, WHITE, STEEL, size=6.8, bold=False)
    g.text(8, 14, 'Lesson learned: code once went live before its database change and the site errored — so every database change now ships with a guarded self-applying step.', 6.9, STEEL)
    return figure(g, 'Figure 11 — From a change on the laptop to a running site, and the housekeeping the app does each time it starts.')


def d_backup():
    g = Diagram(W, 280)
    g.cyl(0, 170, 110, 70, 'fleet.db', 'live database on the Render persistent disk', BLUE_L, BLUE)
    g.box(130, 196, 110, 44, 'Render snapshot', 'automatic, every 24 h, kept 7+ days', ACCENT_L, ACCENT, size=7.6)
    g.box(260, 196, 110, 44, 'Restore from dashboard', 'all-or-nothing, back to that day', WHITE, ACCENT, size=7.6)
    g.arrow([(110, 218), (130, 218)], ACCENT); g.arrow([(240, 218), (260, 218)], ACCENT)
    g.box(130, 106, 120, 50, '/internal/backup', 'POST with secret token; SQLite online backup, integrity-checked, gzip', PURPLE_L, PURPLE, size=7.4)
    g.arrow([(55, 170), (55, 130), (130, 130)], PURPLE)
    g.box(280, 106, 120, 50, 'pull_backup.py on your Mac', 'daily; refuses a copy that is empty, damaged or has lost 20% of rows', GREEN_L, GREEN, size=7.2)
    g.arrow([(250, 131), (280, 131)], PURPLE, label='download', loff=(0, 4))
    g.cyl(400, 100, 93, 62, 'render_*.db', 'newest 30 kept', GREEN_L, GREEN)
    g.arrow([(400, 131), (400, 131)], GREEN)
    g.arrow([(400, 131), (400, 131)], GREEN)
    g.box(130, 20, 120, 52, 'restore_check.py', 'restores a copy into a scratch folder, runs the app on it and opens 6 screens: PASS or FAIL', AMBER_L, AMBER, size=7.2)
    g.box(280, 20, 213, 52, 'Off-machine copy', 'put the folder in iCloud / Google Drive / external disk — a backup only on the laptop is lost with the laptop', RED_L, RED, size=7.4)
    g.arrow([(446, 100), (446, 72)], GREEN)
    g.arrow([(340, 106), (250, 72)], AMBER, dashed=True)
    g.box(0, 20, 118, 52, 'Not yet covered', 'files in static/uploads are NOT in the database copy', WHITE, RED, size=7.2)
    return figure(g, 'Figure 12 — Two independent safety nets: Render\'s own snapshots, and your own daily verified copy that you can prove restores.')


def d_routefinder():
    g = Diagram(W, 330)
    g.box(0, 250, 110, 62, '1  Type a place', 'From / To boxes; after 3 letters, 350 ms pause', BLUE_L, BLUE, size=7.6)
    g.box(126, 250, 110, 62, '2  Photon suggests', 'photon.komoot.io, India only, biased towards the other end; cached 1 h', TEAL_L, TEAL, size=7.2)
    g.box(252, 250, 110, 62, '3  Pick one (or free text)', 'a pick pins exact latitude/longitude; free text falls back to Nominatim (1 request/second)', TEAL_L, TEAL, size=7)
    g.box(378, 250, 115, 62, '4  Search route', 'OSRM driving routes, up to 3 alternatives, distance, time, highways', TEAL_L, TEAL, size=7.4)
    for x in (110, 236, 362):
        g.arrow([(x, 281), (x + 16, 281)], STEEL)
    g.box(0, 150, 150, 70, '5  Browser draws it', 'Leaflet map (vendored copy, no CDN) + route cards; also shows what your OWN past trips on that route earned (trips table)', ACCENT_L, ACCENT, size=7.2)
    g.box(172, 150, 150, 70, '6  Route Stops request', 'the chosen route\'s simplified path is sent back to /route-finder/extras so any worker can answer', PURPLE_L, PURPLE, size=7.2)
    g.box(344, 150, 149, 70, '7  Overpass questions', 'one query for toll booths, towns, petrol pumps, airports, junctions inside ~50 km boxes along the route; 3 tries main → mirror → main', TEAL_L, TEAL, size=7)
    g.arrow([(436, 250), (436, 220)], STEEL, dashed=True)
    g.arrow([(150, 185), (172, 185)], STEEL); g.arrow([(322, 185), (344, 185)], STEEL)
    g.box(0, 50, 150, 70, '8  Rank and trim', 'cities, towns and landmarks scored (size, airport, junction, notable); the best ones spread along the route are kept (6 on a phone, 8 tablet, 10 desktop)', GREEN_L, GREEN, size=7)
    g.box(172, 50, 150, 70, '9  Timeline', 'time to each stop from OSRM step durations; toll and petrol counts since the previous stop; unknown shows a dash, never 0', GREEN_L, GREEN, size=7)
    g.box(344, 50, 149, 70, 'Honest limits', 'OSM toll data is incomplete and has no prices; routes are CAR routes; Overpass is a free shared server and can be slow (7–30 s) or busy', AMBER_L, AMBER, size=7)
    g.arrow([(418, 150), (150, 120)], STEEL, dashed=True)
    g.arrow([(150, 85), (172, 85)], STEEL)
    g.text(0, 28, 'Nothing is stored: only a per-worker memory cache (1 hour) and a limit of 3 searches running at once protect the free services.', 7.2, STEEL)
    g.text(0, 14, 'Used on the website (Route Analytics → Route Finder tab) and in the app (/app/analytics?tab=finder).', 7.2, STEEL)
    return figure(g, 'Figure 13 — The live Route Finder: five free OpenStreetMap-based services, nothing saved.')


def d_frontend():
    g = Diagram(W, 330)
    g.box(168, 276, 157, 44, 'Flask render_template()', 'merges data + a template into HTML', INK, INK, WHITE, ACCENT_L, size=8)
    g.lane(0, 150, 240, 112, 'WEBSITE  (desktop / tablet)', BLUE_L, BLUE)
    g.box(10, 190, 100, 44, 'base.html', 'sidebar, top bar, theme, notifications', WHITE, BLUE, size=7.4)
    g.box(122, 190, 108, 44, 'style.css', '494 lines · tokens, tables, forms', WHITE, BLUE, size=7.4)
    g.box(10, 158, 220, 26, f'{F.T_ROOT} page templates + {F.T_PART} partials (tabs and modals)', None, WHITE, BLUE, size=7.4, bold=False)
    g.lane(253, 150, 240, 112, 'PHONE APP  (/app/)', ACCENT_L, ACCENT)
    g.box(263, 190, 100, 44, 'base_app.html', 'top bar, bottom bar, + and More fans, toast', WHITE, ACCENT, size=7.4)
    g.box(375, 190, 108, 44, 'app.css', '1,923 lines · cards, sheets, fans', WHITE, ACCENT, size=7.4)
    g.box(263, 158, 220, 26, f'{F.T_APP} mobile templates (pages + shared modals/scripts)', None, WHITE, ACCENT, size=7.4, bold=False)
    g.arrow([(220, 276), (120, 262)], STEEL); g.arrow([(273, 276), (373, 262)], STEEL)
    g.lane(0, 6, W, 124, 'SHARED PIECES', PAPER)
    items = [('mobile_input.js', 'phone-number and numeric input rules'), ('route_finder.js/.css', 'map, suggestions, timeline (856 + 232 lines)'),
             ('limited_access_picker.js/.css', 'screen picker for Limited Access users'), ('app_settings.css', 'Administrator screens in the app'),
             ('vendor/leaflet', 'map library, bundled'), ('app-icons, img', 'phone icons, login pictures'), ('Poppins (Google Fonts)', 'phone sign-in screens only'), ('Jinja2 macros', '_settings_macros, _nav_icons')]
    for i, (t, s) in enumerate(items):
        col, row = i % 4, i // 4
        g.box(8 + col * 121, 62 - row * 48 + 0, 115, 42, t, s, WHITE, STEEL, size=7)
    return figure(g, 'Figure 14 — Two front-ends, one back-end: both are built from Jinja2 templates; they differ in their base layout and stylesheet.')


def d_pwa():
    g = Diagram(W, 250)
    steps = [('Tap a link in the app', 'e.g. Ledger in the bottom bar', BLUE_L, BLUE), ('JavaScript intercepts', 'stops the full page reload', PURPLE_L, PURPLE),
             ('fetch() the next page', 'normal HTTP request to /app/...; all server rules apply', ACCENT_L, ACCENT), ('Swap the content', 'parse the HTML, replace the page body, keep top and bottom bars', TEAL_L, TEAL),
             ('history.pushState', 'Back button and URL still work', GREEN_L, GREEN)]
    w, gap = 91, 9.5
    for i, (t, s, f, st) in enumerate(steps):
        x = i * (w + gap)
        g.box(x, 150, w, 80, t, s, f, st, size=7.4)
        if i: g.arrow([(x - gap, 190), (x, 190)], STEEL)
    g.lane(0, 6, W, 124, 'INSTALLABLE APP (PWA) PIECES', PAPER)
    g.box(8, 30, 150, 66, 'manifest (/app-manifest.json)', 'name RATH, start /app/dashboard, standalone, orange theme, 3 icons', WHITE, STEEL, size=7.2)
    g.box(172, 30, 150, 66, 'service worker (static/app-sw.js)', 'scope /app/; caches ONLY two icons; never caches pages or numbers so data is always live', WHITE, STEEL, size=7.2)
    g.box(336, 30, 150, 66, 'No-cache pages', 'Cache-Control: no-store on every page, so Back shows live data (no stale dashboard)', WHITE, STEEL, size=7.2)
    return figure(g, 'Figure 15 — How the phone app feels instant: in-app navigation swaps page content; the installed shell stays put.')


def d_tests():
    g = Diagram(W, 330)
    g.box(0, 262, 150, 56, 'Fresh database per batch', 'built from schema.sql; the real fleet.db is never opened', BLUE_L, BLUE, size=7.4)
    g.box(172, 262, 150, 56, 'Real routes, forged login', 'test client posts the same forms the browser would; network blocked', PURPLE_L, PURPLE, size=7.4)
    g.box(344, 262, 149, 56, 'Independent model', 'recomputes every balance from raw amounts, without the app\'s code', GREEN_L, GREEN, size=7.4)
    g.arrow([(150, 290), (172, 290)], STEEL); g.arrow([(322, 290), (344, 290)], STEEL)
    g.box(100, 196, 293, 40, 'Compare what the rendered screens show with the model and with each other', 'every mismatch becomes a finding, grouped by cause', INK, INK, WHITE, ACCENT_L, size=8)
    g.arrow([(75, 262), (170, 236)], STEEL); g.arrow([(247, 262), (247, 236)], STEEL); g.arrow([(418, 262), (325, 236)], STEEL)
    stages = ['party 18,604', 'vendor 3,228', 'edge 83', 'maint 200', 'expense 40', 'employee 60', 'empedge 198', 'acct 9', 'invoice 24', 'invinput 36', 'urea 18', 'xbill 240', 'ownerinv 32', 'access 9', 'invreview 24', 'roles 10', 'fuzz 101']
    g.text(0, 170, '17 audit stages (scenarios per stage):', 8, INK, True)
    for i, s_ in enumerate(stages):
        col, row = i % 6, i // 6
        g.pill(col * 82, 140 - row * 22, s_, ACCENT_L, ACCENT_D, 7)
    g.box(0, 18, 150, 54, '12 older test files', 'ledger, floor, maintenance, financial, employee, invoice, cache, sorting, route finder, performance, phases 2-3', WHITE, STEEL, size=7)
    g.box(172, 18, 150, 54, 'Load test', '12,000 trips, 144 screens timed, real gunicorn with several users', WHITE, STEEL, size=7.2)
    g.box(344, 18, 149, 54, 'Safety scripts', 'db_health_check.py · restore_check.py · accounts-equivalence proof (fast = slow)', WHITE, STEEL, size=7.2)
    return figure(g, 'Figure 16 — How the app is tested: real screens versus an independent calculation, on throw-away data.')


def d_security():
    g = Diagram(W, 400)
    rings = [('NETWORK', 'HTTPS everywhere on Render · HSTS · secure cookies · real visitor address via ProxyFix · security headers (nosniff, frame, referrer)', BLUE_L, BLUE),
             ('IDENTITY', 'passwords hashed (werkzeug) · 8+ characters · 5 failures lock 15 min · SMS codes kept server-side, 5 tries, rate-limited · self sign-up closed', PURPLE_L, PURPLE),
             ('SESSION', 'signed cookie (private random key) · SameSite=Lax · 3-hour idle timeout · user re-checked on every request · cross-site POSTs refused', TEAL_L, TEAL),
             ('AUTHORIZATION', 'one Administrator · Full / Read Only / Limited (9 screens, View or Edit) · every route classified · enforced on the server', ACCENT_L, ACCENT),
             ('INPUT', 'every number validated (no NaN, no negatives, rounded to paise) · bad input becomes a friendly 400 · upload type and 16 MB cap · return links must be local', AMBER_L, AMBER),
             ('DATA', 'SQL always uses ? placeholders · foreign-key and CHECK rules · WAL journal · audit columns (created_by/at, updated_by/at) · private documents behind login', GREEN_L, GREEN),
             ('OPERATIONS', 'secrets only in environment variables or private DB rows · backup endpoint needs a token · daily snapshots + your own verified copy · tests prove each rule', GREY_L, STEEL)]
    y = 400 - 52
    for i, (t, s, f, st) in enumerate(rings):
        ind = i * 0
        g.d.add(Rect(0, y, 96, 46, rx=5, ry=5, fillColor=st, strokeColor=None))
        g.text(48, y + 18, t, 8.4, WHITE, True, 'middle')
        g.d.add(Rect(102, y, W - 102, 46, rx=5, ry=5, fillColor=f, strokeColor=st, strokeWidth=0.9))
        g.para(110, y + 33, W - 118, s, 7.6, INK)
        y -= 54
    return figure(g, 'Figure 17 — Seven layers of protection. An attacker has to get through all of them; one slipped layer is caught by the next.')


def d_tenancy():
    g = Diagram(W, 250)
    g.box(0, 160, 150, 80, 'TODAY: one company', 'every table already has company_id (default 1); settings are read per company; uploads go in uploads/<type>/<company_id>/', ACCENT_L, ACCENT, size=7.4)
    g.box(172, 160, 150, 80, 'STEP B-E (not started)', 'read session company_id everywhere, add WHERE company_id=? to queries, remove the 7 hard-coded company PDFs', AMBER_L, AMBER, size=7.4)
    g.box(344, 160, 149, 80, 'PHASE 1: many companies', 'PostgreSQL with schema_postgres.sql: real company_id foreign keys, row-level security, composite uniques', GREEN_L, GREEN, size=7.4)
    g.arrow([(150, 200), (172, 200)], STEEL); g.arrow([(322, 200), (344, 200)], STEEL)
    g.lane(0, 6, W, 130, 'RULES FOR ANY NEW CODE (from CLAUDE.md)', PAPER)
    rules = ['New tables get company_id NOT NULL DEFAULT 1 (and the Postgres twin with a foreign key + index)', 'Natural-key UNIQUE constraints are (company_id, name), never bare',
             'Settings go through _get_all_settings(company_id) — never ad-hoc SELECTs', 'Uploads: uploads/<type>/<company_id>/… ; never hard-code the company name in a page or PDF',
             'db.py is the only place a database swap happens']
    for i, r in enumerate(rules):
        g.text(10, 110 - i * 19, '• ' + r, 7.4, INK)
    return figure(g, 'Figure 18 — Built to become a multi-company product later without a redesign.')


def d_sync():
    g = Diagram(W, 250)
    g.box(0, 170, 150, 70, 'Scheduler tick (hourly)', 'APScheduler locally; on Render an outside cron calls POST /internal/sync-tick with a secret token', BLUE_L, BLUE, size=7.4)
    g.box(172, 170, 150, 70, 'Is a job due?', 'reads sync_log and the Settings intervals (RC every 15 days, challan every 3 by default)', WHITE, STEEL, size=7.4)
    g.box(344, 170, 149, 70, 'Weekly compliance job', 'Sunday 2 AM: fitness / PUC / permit status for the own fleet through provider classes', WHITE, STEEL, size=7.4)
    g.arrow([(150, 205), (172, 205)], STEEL); g.arrow([(322, 205), (344, 205)], STEEL, dashed=True)
    g.box(0, 70, 150, 70, 'RC sync', 'one eChallan RC call per own vehicle: caches the full answer, fills registration, chassis, engine, expiry dates', AMBER_L, AMBER, size=7.2)
    g.box(172, 70, 150, 70, 'Challan sync', 'one eChallan challan call per vehicle: replaces that vehicle\'s vehicle_challans rows and counts', AMBER_L, AMBER, size=7.2)
    g.box(344, 70, 149, 70, 'Writes', 'vehicles, vehicle_compliance, vehicle_challans and one sync_log row per run (what, when, counts)', GREEN_L, GREEN, size=7.2)
    g.arrow([(247, 170), (75, 140)], STEEL); g.arrow([(247, 170), (247, 140)], STEEL)
    g.arrow([(150, 105), (172, 105)], STEEL, dashed=True)
    g.arrow([(322, 105), (344, 105)], STEEL)
    g.box(0, 6, W, 46, 'Paid calls are opt-in', 'ENABLE_RC_SYNC / ENABLE_CHALLAN_SYNC / ENABLE_COMPLIANCE_SCHEDULER are off unless set; every call costs real credits, so nothing runs until you decide. Failures never overwrite good data.', RED_L, RED, size=7.4)
    return figure(g, 'Figure 19 — Background data refresh for vehicle documents and challans.')
