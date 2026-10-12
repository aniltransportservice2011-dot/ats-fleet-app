"""Part F: generated reference sections - an execution card for every URL, the life of every table, and the screen-to-data map."""
from lib import *
from lib import _ps
import facts as F
import analysis as A
import route_notes as RN
from flowlib import C

NOISE = A.NOISE
GROUPS = [('Open pages and sign-in', lambda ep, m: ep in ('login', 'logout', 'send_login_otp', 'verify_login_otp', 'app_login', 'app_login_otp', 'app_verify_login_otp', 'app_signup', 'app_manifest')),
          ('Internal endpoints (token protected)', lambda ep, m: ep in ('internal_sync_tick', 'internal_backup')),
          ('Profile, settings and users', lambda ep, m: ep in ('settings_page', 'website_update_my_profile', 'website_change_password', 'upload_company_logo', 'add_user', 'edit_user', 'make_administrator', 'update_user_phone', 'delete_user', 'export_users', 'app_profile', 'app_update_profile', 'app_change_password') or ep.startswith('app_settings_')),
          ('Screen: Dashboard', lambda ep, m: m == 'Dashboard'), ('Screen: Trips', lambda ep, m: m == 'Trips'), ('Screen: Vehicles', lambda ep, m: m == 'Vehicles'),
          ('Screen: Maintenance', lambda ep, m: m == 'Maintenance'), ('Screen: Ledger (including payments)', lambda ep, m: m == 'Ledger'), ('Screen: Invoices', lambda ep, m: m == 'Invoices'),
          ('Screen: Expenses', lambda ep, m: m == 'Expenses'), ('Screen: Salaries', lambda ep, m: m == 'Salaries'), ('Screen: Reports', lambda ep, m: m == 'Reports'),
          ('Whole-database export', lambda ep, m: ep == 'export_excel')]


def cols_for(c, table, op):
    cs = set(c['cols'].get((table, op), set()))
    if table == 'trips' and op == 'INSERT' and c['dyn_cols']:
        cs |= c['dyn_cols']
    cs -= {'updated_by', 'updated_at', 'created_by', 'created_at', 'company_id'}
    return sorted(cs)


def card(r):
    url, methods, name, line, doc = r
    c = A.ROUTE_CLOSURE[name]
    info = A.INFO[name]
    tpls = sorted(c['templates'])
    # header
    head = Paragraph(safe(f'<font color="#c05621"><b>{methods.replace(",", " / ")}</b></font>  <font face="Courier"><b>{esc(url)}</b></font>  →  <font face="Courier">{name}</font> (line {line})'), _ps('cardh', fontSize=8.8, leading=11.5, spaceAfter=0))
    rows = [[head, '']]
    note = RN.N.get(name, '')
    if note:
        rows.append([Paragraph(safe(note), _ps('cn', fontSize=8, leading=10.3, textColor=STEEL, spaceAfter=0)), ''])

    def line_row(label, text):
        rows.append([Paragraph(f'<b>{label}</b>', S['cell']), Paragraph(safe(text), S['cell'])])
    fields = sorted(c['fields'])
    args = sorted(c['args'])
    if fields:
        line_row('Form fields read', ', '.join(C(x) for x in fields[:40]) + (f' (+{len(fields) - 40} more)' if len(fields) > 40 else ''))
    if args:
        line_row('Query parameters', ', '.join(C(x) for x in args[:30]))
    chain = []
    for h in c['chain']:
        if h not in NOISE and h not in chain and h != name:
            chain.append(h)
    if chain:
        line_row('Helpers run', ', '.join(C(h) for h in chain[:14]) + (f' (+{len(chain) - 14} more)' if len(chain) > 14 else ''))
    if c['writes']:
        parts = []
        for t in sorted(c['writes']):
            for op in sorted(c['writes'][t], key=lambda o: ['INSERT', 'UPDATE', 'DELETE'].index(o) if o in ('INSERT', 'UPDATE', 'DELETE') else 9):
                cs = cols_for(c, t, op)
                txt = f'<b>{op}</b> {C(t)}'
                if cs:
                    txt += ' (' + ', '.join(cs[:16]) + (f', +{len(cs) - 16} more' if len(cs) > 16 else '') + ')'
                parts.append(txt)
        line_row('Writes', '; '.join(parts))
    else:
        line_row('Writes', 'nothing (read-only)')
    if c['reads']:
        line_row('Reads', ', '.join(C(t) for t in sorted(c['reads'])))
    if tpls:
        shown = []
        for t in tpls[:3]:
            u = F.ui_summary(t)
            bits = []
            if u['kpis']:
                bits.append('cards: ' + ', '.join(u['kpis'][:6]))
            if u['columns']:
                bits.append('columns: ' + ', '.join(u['columns'][:9]))
            if u['headings']:
                bits.append('sections: ' + ', '.join(u['headings'][:5]))
            shown.append(f'{C(t)}' + (' — ' + '; '.join(bits) if bits else ''))
        line_row('Page shown', ' | '.join(shown))
        if c['kwargs']:
            line_row('Data given to the page', ', '.join(C(k) for k in sorted(c['kwargs'])[:26]) + (f' (+{len(c["kwargs"]) - 26} more)' if len(c['kwargs']) > 26 else ''))
    then = []
    if c['redirects']:
        then.append('redirects to ' + ', '.join(C(x) for x in sorted(c['redirects'])[:5]))
    if c['json']:
        then.append('answers with JSON')
    if c['file']:
        then.append('sends a file (PDF / Excel)')
    if then:
        line_row('Then', '; '.join(then))
    t = Table(rows, colWidths=[24 * mm, CONTENT_W - 24 * mm], hAlign='LEFT')
    style = [('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (1, 0), ACCENT_L), ('BOX', (0, 0), (-1, -1), 0.6, LINE), ('LINEBELOW', (0, 0), (-1, -2), 0.3, LINE),
             ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 2.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.6)]
    if note:
        style += [('SPAN', (0, 1), (1, 1))]
    t.setStyle(TableStyle(style))
    return [KeepTogether([t, Spacer(1, 5)])]


def part_f():
    st = [NextPageTemplate('divider'), PageBreak(),
          PartDivider('F', 'Complete reference, generated from the code', 'An execution card for every one of the URLs, the life story of every table, and a map from every screen to the data behind it.',
                      ['Execution card per URL: inputs, helpers, tables written (with columns), tables read, page shown', 'Life of each table: who creates, changes, deletes and reads it', 'Screen-to-data map for every page template']),
          NextPageTemplate('body'), PageBreak()]
    st.append(H1('41. Execution cards: every URL'))
    st.append(P(f'One card per URL ({F.N_ROUTES} in all), grouped by the screen they belong to for Limited Access. Each card was produced by analysing the real source: which form fields and query parameters the code reads, which helper functions it runs (the generic ones such as get_db are omitted), which tables it writes and which columns it sets, which tables it reads, which template it renders and what that template shows (cards, table columns, sections), and where it goes next. Columns that are always set automatically (created_by, created_at, updated_by, updated_at, company_id) are left out to save space.', 'lead'))
    st.append(callout('note', 'How to use the cards', 'To answer "what does pressing this do?": find the URL, read Writes (what changes), Reads (what it consults) and Page shown (what you will see). To answer "which URLs can change this table?": use the table list in Chapter 42. Cards for phone routes (names beginning app_) show the same helpers as their website twins.'))
    byname = {r[2]: r for r in F.ROUTES}
    placed = set()
    for title, pred in GROUPS:
        members = [r for r in sorted(F.ROUTES, key=lambda r: (r[0].startswith('/app/'), r[0])) if r[2] not in placed and pred(r[2], F.ENDPOINT_MODULE.get(r[2]))]
        if not members:
            continue
        st.append(H2(f'{title} ({len(members)})'))
        for r in members:
            placed.add(r[2])
            st += card(r)
    rest = [r for r in F.ROUTES if r[2] not in placed]
    if rest:
        st.append(H2(f'Other ({len(rest)})'))
        for r in rest:
            st += card(r)
    st.append(PageBreak())

    # ---------------------------------------------------------------- table lifecycle
    st.append(H1('42. The life of each table'))
    st.append(P('For every table: which URLs create rows, which change them, which delete them, and which only read them. This is the answer to "when is this table written, and who looks at it?". It is computed from the same analysis as the cards (including everything each URL\'s helper functions do).', 'lead'))
    st.append(PageBreak()) if False else None
    for t in F.TABLES:
        ins_r, upd_r, del_r, rd_r = [], [], [], []
        for r in F.ROUTES:
            c = A.ROUTE_CLOSURE[r[2]]
            if t in c['writes']:
                ops = c['writes'][t]
                if 'INSERT' in ops: ins_r.append(r[2])
                if 'UPDATE' in ops: upd_r.append(r[2])
                if 'DELETE' in ops: del_r.append(r[2])
            elif t in c['reads']:
                rd_r.append(r[2])
        reads_all = sorted(set(rd_r) | set(upd_r) | set(del_r))
        def fmt(lst, cap=26):
            if not lst:
                return '—'
            return ', '.join(C(x) for x in lst[:cap]) + (f' (+{len(lst) - cap} more)' if len(lst) > cap else '')
        st.append(KeepTogether([H3(f'{C(t)}  —  {SN_TABLE.get(t, "")[:140]}'),
                                tbl([['Created by', fmt(sorted(set(ins_r)))], ['Changed by', fmt(sorted(set(upd_r)))], ['Deleted by', fmt(sorted(set(del_r)))], ['Read by (screens and reports)', fmt(sorted(set(rd_r)), 30)]],
                                    [20, 80], header=False, bold_first=True, zebra=False), Spacer(1, 3)]))
    st.append(PageBreak())

    # ---------------------------------------------------------------- screen map
    st.append(H1('43. Screen-to-data map'))
    st.append(P('Every page template with the route function that renders it, the tables behind it, and what it shows. Phone templates are listed with the website pages they mirror.', 'lead'))
    rows = [['Template', 'Rendered by', 'Tables read', 'Shows (cards / columns / sections)']]
    for m in F.TEMPLATES:
        if m['path'].startswith('partials/') or m['path'].startswith('app/_') or not m['used_by']:
            continue
        fnn = m['used_by'][0]
        c = A.ROUTE_CLOSURE.get(fnn)
        reads = ', '.join(sorted(c['reads'])) if c else ''
        u = F.ui_summary(m['path'])
        bits = []
        if u['kpis']: bits.append('cards: ' + ', '.join(u['kpis'][:6]))
        if u['columns']: bits.append('columns: ' + ', '.join(u['columns'][:8]))
        if u['headings']: bits.append('sections: ' + ', '.join(u['headings'][:5]))
        rows.append([C(m['path']), ', '.join(C(x) for x in m['used_by'][:3]), reads, '; '.join(bits)])
    st.append(tbl(rows, [15, 15, 28, 42]))
    st.append(PageBreak())
    return st


import schema_notes as _SN
SN_TABLE = _SN.TABLE
