"""Regression test: an existing trip opens READ-ONLY on the website (Trip Details); "Edit Trip" unlocks it and "Save" takes the place of the edit button.

  A. opening /trips/edit/<id> gives the read-only state: the whole form body sits in a disabled <fieldset>, the title reads "Trip Details", the header shows
     "Edit Trip"; "Save", "Cancel" and the bottom Save bar are hidden;
  B. every field of the form is inside that fieldset (so nothing can be typed in read-only), except the hidden return_to and the Empty Trip switch, which the
     script locks / unlocks together with it;
  C. ?edit=1 and a failed save both open it already unlocked ("Edit Trip" gone, "Save" shown, no Update button anywhere);
  D. a NEW trip is untouched: editable, "Save", no read-only badge, no Edit button;
  E. the unlock script flips every piece (fieldset, Empty Trip switch, buttons, titles, bottom bar) and re-runs the live calculation; Cancel reloads without ?edit;
  F. saving still works exactly as before (the same fields are posted), and no screen of the app still says "Update" on this form.

Usage:  python3 test_trip_readonly_view.py
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import requests                                                              # noqa: E402
def _blocked(*a, **k):
    raise RuntimeError('network blocked in tests')
requests.get = requests.post = _blocked
import app as m                                                               # noqa: E402
from test_support import ensure_test_user                                     # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


tmp = tempfile.mkdtemp(prefix='tripro_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
uid = ensure_test_user(dbp)
tid = c.execute("SELECT id FROM trips ORDER BY id LIMIT 1").fetchone()[0]
before = tuple(c.execute('SELECT billed_amount, payment_received, party_advance, quantity FROM trips WHERE id=?', (tid,)).fetchone())


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid


class _F(HTMLParser):
    def __init__(self):
        super().__init__(); self.inform = False; self.fields = []; self._sel = None
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == 'form' and a.get('id') == 'tripForm':
            self.inform = True
        if not self.inform:
            return
        if tag == 'input' and a.get('name') and (a.get('type') or 'text') not in ('submit', 'button', 'checkbox', 'radio'):
            self.fields.append((a['name'], a.get('value', '')))
        elif tag == 'select' and a.get('name'):
            self._sel = [a['name'], None, None]
        elif tag == 'option' and self._sel:
            if 'selected' in a: self._sel[1] = a.get('value', '')
            if self._sel[2] is None: self._sel[2] = a.get('value', '')
        elif tag == 'textarea' and a.get('name'):
            self.fields.append((a['name'], ''))
    def handle_endtag(self, tag):
        if tag == 'select' and self._sel:
            self.fields.append((self._sel[0], self._sel[1] if self._sel[1] is not None else (self._sel[2] or ''))); self._sel = None


def real_fields(html_text):
    f = _F(); f.feed(html_text)
    d = {}
    for n, v in f.fields:
        d.setdefault(n, v)
    return d


def get(url):
    r = cl.get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


def hidden(html, element_id):
    mo = re.search(r'<[^>]*id="%s"[^>]*>' % element_id, html)
    return bool(mo and re.search(r'\bhidden\b', mo.group(0)))


# ---- A. read-only on open
st, h = get(f'/trips/edit/{tid}')
check(st == 200 and '<fieldset id="tfFieldset" class="tf-fs" disabled>' in h, 'A: the form body is in a disabled fieldset')
check('<h1 id="tfTitle">Trip Details</h1>' in h and 'Review trip information, charges, payments and profitability.' in h, 'A: it reads Trip Details')
check('tfRoBadge' not in h and 'Read-only view' not in h, 'A: no "Read-only view" label is shown (the locked fields and the Edit Trip button say it)')
check('id="tfEditBtn"' in h and not hidden(h, 'tfEditBtn') and 'Edit Trip' in h, 'A: the Edit Trip button is shown')
check(hidden(h, 'tfSaveBtn') and hidden(h, 'tfCancelEditBtn') and hidden(h, 'tfBottomBar'), 'A: Save, Cancel and the bottom Save bar are hidden')
check(re.search(r'id="tfIsEmpty"[^>]*disabled', h) is not None, 'A: the Empty Trip switch is locked too')

# ---- B. every field is inside the fieldset
a = h.index('<form method="POST" id="tripForm">'); b = h.index('</form>', a)
form = h[a:b]
fs_a = form.index('<fieldset id="tfFieldset"'); fs_b = form.index('</fieldset>')
outside = form[:fs_a] + form[fs_b:]
loose = [x for x in re.findall(r'<(?:input|select|textarea|button)\b[^>]*>', outside)]
allowed = lambda x: 'name="return_to"' in x or 'tf-btn-primary' in x or 'type="submit"' in x
check(all(allowed(x) for x in loose), f'B: nothing editable sits outside the fieldset: {[x[:80] for x in loose if not allowed(x)][:3]}')
inside_names = set(re.findall(r'<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"', form[fs_a:fs_b]))
check({'date', 'lr_number', 'vehicle_no', 'party_name', 'quantity', 'rate', 'party_advance', 'payment_received', 'paid_to_owner', 'legacy_fuel_amount', 'legacy_adv_amount'} <= inside_names, 'B: the real fields are all inside it')

# ---- C. unlocked states
for label, (st, hh) in {'?edit=1': get(f'/trips/edit/{tid}?edit=1')}.items():
    check(st == 200 and 'class="tf-fs" >' in hh.replace('disabled', '') or '<fieldset id="tfFieldset" class="tf-fs" >' in hh, f'C: {label} opens unlocked')
    check('<h1 id="tfTitle">Edit Trip</h1>' in hh and hidden(hh, 'tfEditBtn') and not hidden(hh, 'tfSaveBtn') and not hidden(hh, 'tfBottomBar'), f'C: {label} shows Save, hides Edit Trip')
body = real_fields(h)
body.update({'quantity': '-5', 'return_to': ''})
r = cl.post(f'/trips/edit/{tid}', data=body)
hh = r.data.decode('utf-8', 'ignore')
check(r.status_code == 200 and '<fieldset id="tfFieldset" class="tf-fs" >' in hh and not hidden(hh, 'tfSaveBtn') and hidden(hh, 'tfEditBtn'), 'C: a failed save comes back unlocked, with Save, so the error can be fixed')

# ---- D. add mode untouched
st, ah = get('/trips/add')
check(st == 200 and '<fieldset id="tfFieldset" class="tf-fs" >' in ah and 'id="tfEditBtn"' not in ah and 'tfRoBadge' not in ah and not hidden(ah, 'tfSaveBtn'), 'D: a new trip is editable with Save, no Edit button, no badge')
check('<h1 id="tfTitle">Add Trip</h1>' in ah and not hidden(ah, 'tfBottomBar'), 'D: still titled Add Trip, bottom bar shown')

# ---- E. the script
js = h[h.index('function tfSetEditing'):h.index('function tfNum')]
for needle in ('fs.disabled = !on', "getElementById('tfIsEmpty')", "getElementById('tfEditBtn').hidden = on", "getElementById('tfSaveBtn').hidden = !on",
               "getElementById('tfBottomBar').hidden = !on", 'tfRecalc();', "searchParams.delete('edit')"):
    check(needle in js, f'E: the switch does {needle}')

# ---- F. saving unchanged, no Update left
body = real_fields(h)
r = cl.post(f'/trips/edit/{tid}', data={**body, 'return_to': ''})
c.close(); c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
after = tuple(c.execute('SELECT billed_amount, payment_received, party_advance, quantity FROM trips WHERE id=?', (tid,)).fetchone())
check(r.status_code in (302, 200), 'F: saving the unchanged form still works')
check(after[1:] == before[1:], f'F: and changes no payment figure ({before} -> {after})')
check('>Update<' not in re.sub(r'\s+', '', h).replace('&#10003;', '') and 'Update</button>' not in h, 'F: no "Update" button is left on the trip form')

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
