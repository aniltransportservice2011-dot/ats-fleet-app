"""Regression test: every website table header uses the soft accent-tinted ("pink") header, never a dark-navy bar, and stays readable in both
themes. Static checks on static/style.css and the templates, plus a contrast calculation on the colour tokens; Route Rates is rendered to
check the group headers and column dividers.

Usage:  python3 test_table_header_style.py
"""
import glob
import os
import re
import sqlite3
import sys
import tempfile
import shutil

REPO = os.path.dirname(os.path.abspath(__file__))
os.chdir(REPO)
ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


css = open('static/style.css', encoding='utf-8').read()


def block(selector_re, text=css):
    m = re.search(selector_re + r'\s*\{([^}]*)\}', text, re.S)
    return m.group(1) if m else ''


def token(name, scope):
    body = block(scope)
    m = re.search(r'--' + re.escape(name) + r'\s*:\s*([^;]+);', body)
    return m.group(1).strip() if m else None


def lum(hexcolor):
    h = hexcolor.lstrip('#')
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


th = block(r'(?m)^th')
check('var(--th-bg)' in th and 'var(--ink)' in th, 'the global th rule uses the --th-bg token with ink text')
check('#14213d' not in th.lower() and '#fff' not in th.lower().replace('#fff3', ''), 'the global th rule no longer paints a dark-navy bar with white text')
light, dark = r'(?m)^:root', r'(?m)^:root\[data-theme="dark"\]'
for name in ('th-bg', 'th-bg-neutral', 'th-line', 'th-divider'):
    check(token(name, light) and token(name, dark), f'--{name} is defined for both the light and the dark theme')
for scope, label in ((light, 'light'), (dark, 'dark')):
    ink, steel, bg, neutral = token('ink', scope), token('steel', scope), token('th-bg', scope), token('th-bg-neutral', scope)
    check(contrast(ink, bg) >= 7, f'{label}: ink text on the header is easy to read ({contrast(ink, bg):.1f}:1)')
    check(contrast(steel, bg) >= 4.5, f'{label}: steel text on the header meets 4.5:1 ({contrast(steel, bg):.1f}:1)')
    check(contrast(ink, neutral) >= 7 and contrast(steel, neutral) >= 4.5, f'{label}: text on the neutral header cells is readable')

# no template paints a table header navy / near-black any more
navy = re.compile(r'#14213d|#0d1830|#142', re.I)
for f in sorted(glob.glob('templates/*.html') + glob.glob('templates/partials/*.html')):
    t = open(f, encoding='utf-8').read()
    for m in re.finditer(r'([^{};]*\bth\b[^{};]*)\{([^{}]*)\}', t):
        sel, body = m.group(1), m.group(2)
        if re.search(r'background(?:-color)?\s*:[^;]*(?:#14213d|var\(--ink\))', body, re.I):
            check(False, f'{f}: "{sel.strip()[:50]}" still paints a dark header')
    check(not re.search(r'<th[^>]*style="[^"]*background[^"]*(?:#14213d|var\(--ink\))', t, re.I), f'{f}: no inline dark th')
check('var(--th-bg)' in open('templates/invoice_preview.html', encoding='utf-8').read(), 'the invoice preview header uses the same tint')

# Route Rates: rendered, against a disposable copy of the data
sys.path.insert(0, REPO)
tmp = tempfile.mkdtemp(prefix='thstyle_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
from test_support import ensure_test_user
import app as m
def gdb():
    c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row; return c
m.get_db = gdb
m.app.config['TESTING'] = True
uid = ensure_test_user(dbp)
cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid
html = cl.get('/route-analytics?tab=route-rates&from_date=2020-01-01&to_date=2030-12-31').data.decode()
check('rr-table' in html, 'Route Rates table renders')
heads = re.findall(r'<th class="grp-title[^"]*"[^>]*>([^<]+)</th>', html)
check(heads == ['Own Vehicle → Party (₹)', 'Hired Vehicle → Party (₹)', 'Hired Vehicle → Owner (₹)', 'Hired Margin (₹ / %)'], f'Route Rates has the four colour-coded group headers {heads}')
check(html.count('grp-start') >= 9, 'each group starts after a divider (header and body cells)')
check(len(re.findall(r'<th class="(?:sortable )?num grp-sub', html)) == 14, 'fourteen sub-headings (High / Avg / Low / Trips x 3 groups, Avg ₹ / Avg % for the margin)')
check(re.search(r'th\.sticky-col\s*\{[^}]*background:var\(--th-bg-neutral\)', html) is not None, 'frozen From/To headers have their own readable background')
# Route Rates pinned columns: From / To have FIXED widths equal to their pin offsets (otherwise a short place name lets the pinned To column
# overlap Type, as it did on the live site), and the table lets its wrapper scroll so the pinning actually works
def css_rule(selector):
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', html)
    return m.group(1) if m else ''
def px(rule, prop):
    m = re.search(r'(?<![-\w])' + prop + r'\s*:\s*(\d+)px', rule)
    return int(m.group(1)) if m else None
f_rule, t_rule = css_rule('.rr-table .sticky-from'), css_rule('.rr-table .sticky-to')
f_w, f_min, f_max = px(f_rule, 'width'), px(f_rule, 'min-width'), px(f_rule, 'max-width')
t_w, t_min, t_max, t_left = px(t_rule, 'width'), px(t_rule, 'min-width'), px(t_rule, 'max-width'), px(t_rule, 'left')
check(f_w and f_w == f_min == f_max, f'From column has one fixed width ({f_w}/{f_min}/{f_max})')
check(t_w and t_w == t_min == t_max, f'To column has one fixed width ({t_w}/{t_min}/{t_max})')
check(t_left == f_w, f'To is pinned exactly where From ends (left {t_left}px = From width {f_w}px) — no gap, no overlap')
check(re.search(r'\.rr-table\s*\{[^}]*overflow:\s*visible', html) is not None, 'Route Rates table does not clip itself, so the pinned columns can stick while the wrapper scrolls')
shutil.rmtree(tmp, ignore_errors=True)
print(f'\nSUMMARY: {ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
