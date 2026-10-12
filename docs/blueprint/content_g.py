"""Part G: the front-end."""
import re
from lib import *
import diagrams as D
import facts as F
from flowlib import C


def jsfuncs(path):
    t = open(F.p(path), encoding='utf-8', errors='ignore').read()
    return sorted(set(re.findall(r'function\s+([A-Za-z0-9_]+)', t)) - {''})


def part_g():
    st = [NextPageTemplate('divider'), PageBreak(),
          PartDivider('G', 'The front-end', 'What the user sees and touches: the two layouts, every template, the scripts, the installable app and the design rules.',
                      ['The two layouts (website and phone)', 'Template inventory', 'Static files: CSS, JavaScript, images', 'Behaviours that run in the browser', 'The installable app (PWA) and in-app navigation', 'Design system']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('44. The two layouts'))
    st.append(P('There is no JavaScript framework. Each screen is an HTML page produced on the server by Jinja2 from a template, styled with plain CSS, and made interactive by small amounts of vanilla JavaScript. Two "base" templates provide the frame; every other template fills in the middle.', 'lead'))
    st.append(D.d_frontend())
    st.append(H2('44.1 Website layout: templates/base.html'))
    st += bullets(['<b>Left sidebar</b> with the brand and four groups — Overview (Dashboard, Ledger), Operations (Trips, Maintenance, Vehicles), Analysis (Route Analytics, Fleet Utilization, Performance, Business Performance), Finance (Salaries, Invoices, Expenses) — then Profile and Settings and Logout. Each item is wrapped in <font face="Courier">can(\'Screen\')</font> so a Limited Access user only sees theirs; a group disappears when none of its items is allowed. The sidebar collapses to the hamburger (state kept in the browser\'s localStorage).',
                   '<b>Top bar</b> with the hamburger, a light/dark switch (stored in localStorage as <font face="Courier">theme</font>, applied by setting data-theme on the page root), the notification bell (a dropdown of vehicles with pending challans, supplied by the context processor) and the user\'s name, role and initials.',
                   '<b>Blocks</b> the child templates fill: <font face="Courier">title</font>, <font face="Courier">extra_head</font> (page-specific CSS), <font face="Courier">content</font> (inside the main container) and scripts.',
                   '<b>Soft navigation.</b> Clicking a sidebar link fetches the next page, parses it, and swaps only the main container and its page styles (function swapContent), so the sidebar and top bar do not flicker; any failure falls back to a normal page load.',
                   '<b>Shared popups.</b> Ledger rows open a centred record popup (trip) or a slide-in detail panel (payment) loaded from the read-only view routes; an RC modal shows cached vehicle data.',
                   '<b>Autocomplete.</b> Inputs with a datalist are enhanced (function enhanceAutocompleteInputs) into a styled suggestion list.'])
    st.append(P('JavaScript functions defined in base.html: ' + ', '.join(C(f) for f in jsfuncs('templates/base.html')) + '.', 'small'))
    st.append(H2('44.2 Phone layout: templates/app/base_app.html'))
    st += bullets(['<b>Top bar</b>: brand badge and name, notification bell and a round avatar with the user\'s initials linking to the profile.',
                   '<b>Main area</b> (<font face="Courier">#appMain</font>) holding each page; <b>extra scripts</b> (<font face="Courier">#appExtraScripts</font>) holding that page\'s own JavaScript.',
                   '<b>Bottom bar</b>: Home, Trips, a round + button, Ledger and More. The + button fans out four quick-add actions in an arc (Add Trip, Add Maintenance → a second arc of six kinds, Add Expense, Add Vehicle); More fans a vertical list (Maintenance, Vehicles, Expenses, Salary, Business Overview). For Limited Access users this whole block is generated from their screens by <font face="Courier">_limited_nav()</font>.',
                   '<b>Shared components</b>: a toast ("Saved"), a custom calendar and time picker that replace the phone\'s native pickers, a translucent loading overlay with an animated truck shown only when a real navigation is slow, scroll locking for sheets, and a viewport-height variable that keeps the layout stable when mobile address bars show and hide.',
                   '<b>Sheets, not pages</b> for most add/edit forms: a bottom sheet (class vam-modal) slides up over the list.'])
    st.append(P('JavaScript functions defined in base_app.html: ' + ', '.join(C(f) for f in jsfuncs('templates/app/base_app.html')) + '.', 'small'))
    st.append(PageBreak())

    st.append(H1('45. Template inventory'))
    st.append(P(f'All {F.T_ALL} templates. "Extends" names the layout they fill; "Rendered by" lists the route functions that return them (partials are included into other templates instead).', 'lead'))
    groups = [('Website pages', lambda m: '/' not in m['path']), ('Partials (tabs, modals, shared blocks)', lambda m: m['path'].startswith('partials/')), ('Phone-app templates', lambda m: m['path'].startswith('app/'))]
    for title, pred in groups:
        ms = [m for m in F.TEMPLATES if pred(m)]
        st.append(H2(f'{title} ({len(ms)})'))
        rows = [['File', 'Lines', 'Extends / includes', 'Title or role', 'Rendered by']]
        for m in ms:
            ext = m['extends'] or ''
            inc = ', '.join(m['includes'][:3])
            rows.append([C(m['path']), str(m['lines']), (C(ext) if ext else '') + ((' · includes ' + inc) if inc else ''), m['title'] or '', ', '.join(C(x) for x in m['used_by'][:3])])
        st.append(tbl(rows, [24, 6, 24, 20, 26]))
    st.append(PageBreak())

    st.append(H1('46. Static files: styles, scripts, images'))
    css = F.CSS_LINES; js = F.JS_LINES
    st.append(tbl([['File', 'Lines', 'What it is'],
                   [C('static/style.css'), str(css['style.css']), 'Website stylesheet: design tokens (colours), light and dark themes, tables, forms, KPI cards, modals, drawers, maintenance and employee layouts.'],
                   [C('static/app.css'), str(css['app.css']), 'Phone-app stylesheet: tokens (--a-*), top and bottom bars, + and More fans, cards, bottom sheets, calendar/time pickers, loading overlay.'],
                   [C('static/app-auth.css'), str(css['app-auth.css']), 'Extra rules for the phone sign-in screens.'],
                   [C('static/app_settings.css'), str(css['app_settings.css']), 'Administrator Profile and Settings screens in the app (hub, forms, switches, user list).'],
                   [C('static/route_finder.css'), str(css['route_finder.css']), 'Route Finder form, map box, route cards and stops timeline (used on both layouts).'],
                   [C('static/limited_access_picker.css'), str(css['limited_access_picker.css']), 'Screen picker for Limited Access users.'],
                   [C('static/route_finder.js'), str(js['route_finder.js']), 'Type-ahead, search, map drawing with Leaflet, route selection, stops timeline, retry logic and responsive layout switching (by the finder\'s own width).'],
                   [C('static/limited_access_picker.js'), str(js['limited_access_picker.js']), 'Presets, per-screen switches and View/Edit toggles, live phone preview; writes the hidden "modules" fields.'],
                   [C('static/mobile_input.js'), str(js['mobile_input.js']), 'Keeps mobile-number boxes to 10 digits (strips +91, leading 0, non-digits) while typing or pasting; the server stays the real check.'],
                   [C('static/app-sw.js'), str(js['app-sw.js']), 'Service worker for the phone app: caches two icons only; never pages or data.'],
                   [C('static/vendor/leaflet/'), '—', 'The Leaflet map library (bundled so no outside CDN is needed) and its images.'],
                   [C('static/app-icons/'), '—', 'Four PNG icons (180, 192, 512, 512 maskable) for the installed app.'],
                   [C('static/img/'), '—', 'Login hero picture and splash highway image.'],
                   [C('static/uploads/'), '—', 'Uploaded files: logo/<company>/, insurance/<company>/, toll_receipts/<company>/ (documents are served only to signed-in users).']], [27, 8, 65], bold_first=False))
    st.append(Spacer(1, 6))
    st.append(P('<b>Cache busting.</b> Templates link CSS and JavaScript through <font face="Courier">asset_v(\'file\')</font>, which appends the file\'s modification time as ?v=…, so a changed stylesheet is fetched at once even by a phone that has had the app open for days.'))
    st.append(PageBreak())

    st.append(H1('47. Behaviours that run in the browser'))
    beh = [['Behaviour', 'Where', 'What it does'],
           ['Light / dark theme', 'base.html + style.css', 'A switch in the top bar flips a data-theme attribute and remembers the choice in localStorage; because every colour is a CSS variable, the whole site re-themes at once.'],
           ['Sidebar collapse and soft navigation', 'base.html', 'Remembers whether the sidebar is closed; swaps the main area on link clicks instead of reloading.'],
           ['Notification bell', 'base.html', 'Shows how many vehicles have pending challans or documents expiring; a dismissal is remembered for the current sign-in.'],
           ['Record popups and panels', 'base.html + ledger.html', 'Clicking a ledger line fetches the trip or payment detail page and shows it in a popup or side panel, with previous/next buttons.'],
           ['Trip form intelligence', 'trip_form.html, app/trip_add.html', 'Live "Trip Summary" (net amount, estimated profit, payment tracking); the Type box follows the chosen vehicle (a normalised map of vehicle number to own/hired); extra "Others" lines can be added; hired-vehicle fields appear only for hired trucks.'],
           ['Invoice review gate', 'invoice_center.html', 'Tracks which LR links were opened. Generate and Draft stay locked until every selected trip is reviewed; a single speech balloon above the LR column explains what is missing and disappears after a few seconds. The server checks the same list.'],
           ['Payment popup', 'ledger.html', 'Lists the pending trips with tick boxes and an amount box; shows how the entered amount will be applied.'],
           ['Read-only layer', 'injected by the server', 'For Read Only users and View-only screens: a "View-only access" badge, add/edit/delete buttons hidden, forms and fetch() writes intercepted with a friendly message.'],
           ['Limited Access picker', 'static/limited_access_picker.js', 'Preset chips, per-screen switch and View/Edit toggle, phone preview; used in the website user modal and the phone user sheet.'],
           ['Custom date and time pickers', 'base_app.html', 'Replace the phone\'s native pickers with a themed calendar (day, month and year views) and a time wheel; date-range pairs stop a To before a From.'],
           ['Datalist enhancement', 'base.html and base_app.html', 'Turns native suggestion lists (vehicles, parties, vendors) into a styled list that works on phones.'],
           ['Mobile number guard', 'mobile_input.js', 'Keeps mobile boxes to ten digits.'],
           ['Route Finder', 'route_finder.js', 'Debounced type-ahead (350 ms), map, route cards, timeline with skeleton placeholders while stops load, automatic retry after 4 seconds when the free server is busy.'],
           ['Settings forms in the app', '_settings_scripts.html', 'Every Administrator form posts with fetch() and the header X-App-Request; success reloads the screen with a "Saved" toast, an error appears inside the form.']]
    st.append(tbl(beh, [22, 22, 56], bold_first=True))
    st.append(PageBreak())

    st.append(H1('48. The installable app (PWA) and in-app navigation'))
    st.append(D.d_pwa())
    st += bullets(['<b>Installability.</b> /app-manifest.json declares the app name (RATH), start page /app/dashboard, scope /app/, standalone display, orange theme and three icons. A service worker registered with scope /app/ satisfies the browser\'s install requirement.',
                   '<b>No offline data, on purpose.</b> The service worker caches only two icon files. Every page and every number comes from the server, so what a user sees is never stale.',
                   '<b>Navigation.</b> Tapping a link in the bottom bar calls navigate(url): it fetches the next page, parses it, replaces only #appMain and the page scripts, updates the address with history.pushState, re-wires the date and time pickers on the new content and highlights the right tab. If anything unexpected happens (not signed in, network error, odd response) it falls back to a normal page load.',
                   '<b>Page transitions.</b> If a real navigation takes longer than about 0.4 seconds a translucent overlay with a moving truck appears; it never shows for quick switches or for fetch() calls that stay on the page.'])
    st.append(PageBreak())

    st.append(H1('49. Design system'))
    st.append(tbl([['Item', 'Website', 'Phone app'],
                   ['Colour tokens', '--ink #14213d, --steel #5a6b8c, --line #e2e6ed, --paper #f5f6f9, --accent #c05621 (brand orange), --green #1a7a4c, --red #b3392f; dark theme: --ink #eef1f8, --paper #0d1220, --white #161c2e and brighter accent/green/red', '--a-ink, --a-steel, --a-line, --a-paper, --a-accent #c05621 and --a-accent-dark #9a4419, --a-green #1a9c5b, --a-red #e34948, --a-blue #2a78d6, --a-purple #7a5ad6, --a-amber #eda100, --a-teal #0f9b8e (light only)'],
                   ['Typeface', 'Segoe UI, then system fonts', 'System font stack for pages; Poppins on the sign-in, SMS-code and sign-up screens'],
                   ['Base size and spacing', '15 px body, cards with 12–16 px radius, tables with zebra rows and fixed dark header bars', 'Cards with 16 px radius, 0.7–0.9 rem text, bottom sheets with 20 px top radius, 44 px touch targets'],
                   ['Charts', 'SVG paths, CSS bars and conic-gradient donuts written by the templates', 'Same approach, simplified'],
                   ['Responsive', 'Breakpoints around 480, 640, 760, 900, 1000, 1100, 1200 and 1300 px; sidebar can collapse', 'Single column; layout locks to portrait; env(safe-area) insets respected'],
                   ['Motion', 'Short transitions on buttons and switches', 'Fans, sheets and the loading overlay; all disabled under prefers-reduced-motion'],
                   ['Dark mode', 'Yes (switch in the top bar)', 'No (single light theme)']], [18, 41, 41], bold_first=True))
    st.append(Spacer(1, 6))
    st.append(callout('note', 'A known inconsistency', 'Font sizes, spacing and a few colours are repeated as literal values in many page-level style blocks instead of coming from shared tokens, and the website and phone token sets are separate. It is cosmetic, tracked in code_health_report.md, and the clean-up path is to define one set of typography and spacing tokens and replace the literals.'))
    st.append(PageBreak())
    return st
