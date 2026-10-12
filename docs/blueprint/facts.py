"""Numbers and inventories read from the real project at build time, so the document cannot drift from the code."""
import ast, glob, os, re, sqlite3, sys
from importlib.metadata import version

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
def p(*a): return os.path.join(ROOT, *a)

def lines(path):
    with open(path, encoding='utf-8', errors='ignore') as f:
        return sum(1 for _ in f)

SRC = open(p('app.py'), encoding='utf-8').read()
TREE = ast.parse(SRC)
FUNCS = [n for n in TREE.body if isinstance(n, ast.FunctionDef)]

def _route_of(f):
    for d in f.decorator_list:
        if isinstance(d, ast.Call) and getattr(d.func, 'attr', '') == 'route' and d.args:
            methods = ''
            for kw in d.keywords:
                if kw.arg == 'methods':
                    methods = ','.join(e.value for e in kw.value.elts)
            return d.args[0].value, methods or 'GET'
    return None

ROUTES = [(r[0], r[1], f.name, f.lineno, ast.get_docstring(f) or '') for f in FUNCS for r in [_route_of(f)] if r]
HELPERS = [f for f in FUNCS if not _route_of(f)]

APP_LINES = lines(p('app.py'))
N_FUNCS, N_ROUTES, N_HELPERS = len(FUNCS), len(ROUTES), len(HELPERS)

def count(glob_pat):
    return len(glob.glob(p(glob_pat)))

T_ROOT = len([f for f in glob.glob(p('templates', '*.html'))])
T_PART = count('templates/partials/*.html')
T_APP = count('templates/app/*.html')
T_ALL = T_ROOT + T_PART + T_APP

CSS_LINES = {n: lines(p('static', n)) for n in ('style.css', 'app.css', 'app_settings.css', 'route_finder.css', 'limited_access_picker.css', 'app-auth.css')}
JS_LINES = {n: lines(p('static', n)) for n in ('route_finder.js', 'mobile_input.js', 'limited_access_picker.js', 'app-sw.js')}

DB = sqlite3.connect(p('fleet.db'))
DB.row_factory = sqlite3.Row
TABLES = [r[0] for r in DB.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
N_TABLES = len(TABLES)
N_INDEXES = DB.execute("select count(*) from sqlite_master where type='index'").fetchone()[0]
def table_info(t): return DB.execute(f'pragma table_info("{t}")').fetchall()
def table_rows(t): return DB.execute(f'select count(*) from "{t}"').fetchone()[0]
def fk_list(t): return [(r['from'], r['table']) for r in DB.execute(f'pragma foreign_key_list("{t}")')]

ENV_VARS = sorted(set(re.findall(r"os\.environ\.get\('([A-Z_0-9]+)'", SRC)))
VER = {k: version(k) for k in ('flask', 'werkzeug', 'jinja2', 'itsdangerous', 'markupsafe', 'reportlab', 'openpyxl', 'requests', 'apscheduler', 'gunicorn')}
VER['python'] = sys.version.split()[0]
VER['sqlite'] = sqlite3.sqlite_version

TEST_FILES = sorted(glob.glob(p('test_*.py')))

# ---- which function renders which template; template inheritance / includes; titles
def _walk_render():
    use = {}
    for f in FUNCS:
        for node in ast.walk(f):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'render_template' and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    use.setdefault(a.value, []).append(f.name)
                elif isinstance(a, ast.IfExp):
                    for sub in (a.body, a.orelse):
                        if isinstance(sub, ast.Constant): use.setdefault(sub.value, []).append(f.name)
    return use
TEMPLATE_USE = _walk_render()

def template_meta():
    out = []
    for path in sorted(glob.glob(p('templates', '**', '*.html'), recursive=True)):
        rel = os.path.relpath(path, p('templates'))
        t = open(path, encoding='utf-8', errors='ignore').read()
        ext = re.findall(r'{%\s*extends\s+["\']([^"\']+)["\']', t)
        inc = sorted(set(re.findall(r'{%\s*include\s+["\']([^"\']+)["\']', t)))
        title = re.findall(r'{%\s*block title\s*%}(.*?){%\s*endblock', t, re.S)
        out.append({'path': rel, 'lines': t.count('\n') + 1, 'extends': ext[0] if ext else '', 'includes': inc,
                    'title': re.sub(r'\s+', ' ', re.sub(r'{{.*?}}', '…', title[0])).strip() if title else '', 'used_by': sorted(set(TEMPLATE_USE.get(rel, [])))})
    return out
TEMPLATES = template_meta()

MIGRATIONS = sorted(os.path.basename(x) for x in glob.glob(p('migrate*')))

def _literal(name):
    for n in TREE.body:
        if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == name for t in n.targets):
            return ast.literal_eval(n.value)
MODULE_ENDPOINTS = _literal('_MODULE_ENDPOINTS')
ENDPOINT_MODULE = {ep: m for m, eps in MODULE_ENDPOINTS.items() for ep in eps}
MODULE_LIST = _literal('MODULE_LIST')

# ---- what a template shows: headings, KPI labels, table columns, forms, buttons, field names (includes followed recursively)
_TAG = re.compile(r'<[^>]+>')
_JINJA = re.compile(r'{{.*?}}|{%.*?%}', re.S)

def _clean(s):
    s = _JINJA.sub('', s)
    s = _TAG.sub(' ', s)
    s = re.sub(r'&[a-z#0-9]+;', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

_UI_CACHE = {}
def ui_summary(rel, depth=0):
    if rel in _UI_CACHE:
        return _UI_CACHE[rel]
    path = p('templates', rel)
    out = {'headings': [], 'kpis': [], 'columns': [], 'forms': [], 'buttons': [], 'inputs': [], 'modals': [], 'tabs': []}
    if not os.path.exists(path) or depth > 4:
        return out
    t = open(path, encoding='utf-8', errors='ignore').read()
    for m in re.findall(r'<h([123])[^>]*>(.*?)</h\1>', t, re.S):
        c = _clean(m[1])
        if c and len(c) < 70: out['headings'].append(c)
    for m in re.findall(r'<div class="(?:label|lbl)"[^>]*>(.*?)</div>', t, re.S):
        c = _clean(m)
        if c and len(c) < 40: out['kpis'].append(c)
    for m in re.findall(r'<th[^>]*>(.*?)</th>', t, re.S):
        c = _clean(m)
        if c and len(c) < 40: out['columns'].append(c)
    for m in re.findall(r'<form[^>]*action="([^"]*)"', t):
        out['forms'].append(_JINJA.sub('…', m))
    for m in re.findall(r'<button[^>]*>(.*?)</button>', t, re.S):
        c = _clean(m)
        if c and len(c) < 36: out['buttons'].append(c)
    for m in re.findall(r'<(?:input|select|textarea)[^>]*\bname="([a-z_0-9\[\]]+)"', t):
        out['inputs'].append(m)
    for m in re.findall(r'id="([A-Za-z0-9_]*(?:Modal|modal|Sheet|sheet|Drawer|drawer)[A-Za-z0-9_]*)"', t):
        out['modals'].append(m)
    for m in re.findall(r'href="[^"]*[?&]tab=([a-z\-]+)', t):
        out['tabs'].append(m)
    for inc in re.findall(r'{%\s*include\s+["\']([^"\']+)["\']', t):
        sub = ui_summary(inc, depth + 1)
        for k in out:
            out[k] += sub[k]
    for k in out:
        seen, uniq = set(), []
        for x in out[k]:
            if x not in seen:
                seen.add(x); uniq.append(x)
        out[k] = uniq
    _UI_CACHE[rel] = out
    return out
