"""Static analysis of app.py: for every function, which database tables it reads / writes (and which columns), which form or query
fields it reads, which templates it renders, where it redirects, which other functions it calls; then the transitive closure per route.
Everything here is derived from the real source, so the document cannot claim something the code does not do."""
import ast, re, os
import facts as F

TABLES = set(F.TABLES)
FUNC_NAMES = {f.name for f in F.FUNCS}
FUNC_BY = {f.name: f for f in F.FUNCS}


def _strings(node):
    """All string literals (joined f-strings, concatenations) inside a node, as one text per literal expression."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            out.append(''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in n.values))
    return out


RE_INS = re.compile(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*(?:\(([^)]*)\))?', re.I)
RE_UPD = re.compile(r'UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\bWHERE\b|$)', re.I | re.S)
RE_DEL = re.compile(r'DELETE\s+FROM\s+(\w+)', re.I)
RE_FROM = re.compile(r'\b(?:FROM|JOIN)\s+(\w+)', re.I)


def analyse_function(f):
    info = {'reads': set(), 'writes': {}, 'cols': {}, 'fields': set(), 'args': set(), 'templates': set(), 'redirects': set(), 'calls': set(),
            'kwargs': set(), 'json': False, 'file': False, 'dyn_cols': set(), 'queries': []}
    texts = _strings(f)
    for t in texts:
        tt = re.sub(r'\s+', ' ', t).strip()
        if re.match(r'(?i)^select\b', tt):
            info['queries'].append(tt)
    for t in texts:
        for m in RE_INS.finditer(t):
            tb = m.group(1)
            if tb in TABLES:
                info['writes'].setdefault(tb, set()).add('INSERT')
                if m.group(2) and '{' not in m.group(2):
                    info['cols'].setdefault((tb, 'INSERT'), set()).update(c.strip() for c in m.group(2).split(',') if c.strip())
        for m in RE_UPD.finditer(t):
            tb = m.group(1)
            if tb in TABLES:
                info['writes'].setdefault(tb, set()).add('UPDATE')
                cols = re.findall(r'(\w+)\s*=', m.group(2))
                info['cols'].setdefault((tb, 'UPDATE'), set()).update(c for c in cols if c.lower() not in ('where',))
        for m in RE_DEL.finditer(t):
            if m.group(1) in TABLES:
                info['writes'].setdefault(m.group(1), set()).add('DELETE')
        for m in RE_FROM.finditer(t):
            if m.group(1) in TABLES and not re.search(r'DELETE\s+FROM\s+' + m.group(1), t, re.I) or (m.group(1) in TABLES and re.search(r'SELECT', t, re.I)):
                if re.search(r'SELECT', t, re.I):
                    info['reads'].add(m.group(1))
    # f-string table placeholders chosen between parties/vendors
    for t in texts:
        if re.search(r'(UPDATE|FROM|INTO)\s+\{\}', t):
            info['dyn_tables'] = True
    for n in ast.walk(f):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Name):
                if fn.id in FUNC_NAMES and fn.id != f.name:
                    info['calls'].add(fn.id)
                if fn.id == 'render_template' and n.args and isinstance(n.args[0], ast.Constant):
                    info['templates'].add(n.args[0].value)
                    info['kwargs'].update(k.arg for k in n.keywords if k.arg)
                if fn.id == 'render_template' and n.args and isinstance(n.args[0], ast.IfExp):
                    for sub in (n.args[0].body, n.args[0].orelse):
                        if isinstance(sub, ast.Constant): info['templates'].add(sub.value)
                    info['kwargs'].update(k.arg for k in n.keywords if k.arg)
                if fn.id in ('jsonify',):
                    info['json'] = True
                if fn.id == 'n' and n.args and isinstance(n.args[0], ast.Constant):
                    info['fields'].add(n.args[0].value)
                if fn.id == 'url_for' and n.args and isinstance(n.args[0], ast.Constant):
                    info['redirects'].add(n.args[0].value)
            elif isinstance(fn, ast.Attribute):
                if fn.attr in ('get', 'getlist') and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    base = ast.unparse(fn.value)
                    key = n.args[0].value
                    if base in ('f', 'request.form', 'form', 'request.files'):
                        info['fields'].add(key)
                    elif base in ('request.args',):
                        info['args'].add(key)
                if fn.attr == 'send_file':
                    info['file'] = True
                if fn.attr == 'send_file' or (isinstance(fn, ast.Name) and fn.id == 'send_file'):
                    info['file'] = True
    # trip-style dynamic INSERT: columns come from a list called cols
    for n in ast.walk(f):
        if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'cols' for t in n.targets) and isinstance(n.value, ast.List):
            vals = [e.value for e in n.value.elts if isinstance(e, ast.Constant)]
            if vals:
                info['dyn_cols'].update(vals)
    return info


INFO = {f.name: analyse_function(f) for f in F.FUNCS}


def closure(name, seen=None):
    seen = seen if seen is not None else set()
    if name in seen or name not in INFO:
        return {'queries': [], 'reads': set(), 'writes': {}, 'cols': {}, 'fields': set(), 'args': set(), 'templates': set(), 'redirects': set(), 'chain': [], 'json': False, 'file': False, 'dyn_cols': set(), 'kwargs': set()}
    seen.add(name)
    me = INFO[name]
    out = {'queries': list(me['queries']), 'reads': set(me['reads']), 'writes': {k: set(v) for k, v in me['writes'].items()}, 'cols': {k: set(v) for k, v in me['cols'].items()},
           'fields': set(me['fields']), 'args': set(me['args']), 'templates': set(me['templates']), 'redirects': set(me['redirects']),
           'chain': [], 'json': me['json'], 'file': me['file'], 'dyn_cols': set(me['dyn_cols']), 'kwargs': set(me['kwargs'])}
    for c in sorted(me['calls']):
        sub = closure(c, seen)
        out['reads'] |= sub['reads']
        for k, v in sub['writes'].items(): out['writes'].setdefault(k, set()).update(v)
        for k, v in sub['cols'].items(): out['cols'].setdefault(k, set()).update(v)
        out['fields'] |= sub['fields']; out['args'] |= sub['args']; out['templates'] |= sub['templates']; out['redirects'] |= sub['redirects']
        out['json'] = out['json'] or sub['json']; out['file'] = out['file'] or sub['file']; out['dyn_cols'] |= sub['dyn_cols']; out['kwargs'] |= sub['kwargs']
        out['chain'] += [c] + sub['chain']
    return out


# generic helpers that every route uses and that add noise to the per-route view
NOISE = {'get_db', '_wants_json', '_users_done', '_client_ip', '_safe_next', '_app_user_display', '_name_initials', '_get_all_settings', 'get_company_name', '_resolve_dashboard_period',
         '_normalized_date_range', '_paginate', '_page_tokens', '_current_fy_bounds', '_lr_label', '_clean_loc', '_wait_text', '_auth_wait_seconds', '_auth_record', '_auth_clear'}

ROUTE_CLOSURE = {r[2]: closure(r[2]) for r in F.ROUTES}

if __name__ == '__main__':
    import sys
    for nm in sys.argv[1:]:
        c = ROUTE_CLOSURE[nm]
        print(nm, '\n reads', sorted(c['reads']), '\n writes', {k: sorted(v) for k, v in c['writes'].items()}, '\n fields', sorted(c['fields'])[:30], '\n args', sorted(c['args']), '\n tpl', sorted(c['templates']), '\n redirects', sorted(c['redirects']), '\n chain', c['chain'][:12])
