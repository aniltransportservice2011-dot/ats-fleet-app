"""Regression test: "Empty Trip" is fixed once a trip is saved.

  A. Add Trip (website and phone): the switch is live; Edit Trip (website read-only view, website editing, website after a failed save, phone, phone after a failed
     save): the switch is disabled for an empty AND for a non-empty trip, and the website's "Edit Trip" button does not unlock it;
  B. the server holds the line: an edit that posts nothing for the switch (a disabled box is never posted) keeps an empty trip empty — and does not demand a party or
     quantity it never needed; a forged is_empty=on on a normal trip, or a missing one on an empty trip, changes nothing (website and phone);
  C. everything else on an empty / normal trip still saves as before (driver, charges ...), and Add Trip still stores the switch.

Runs on a disposable copy of fleet.db (the real file is never touched, network blocked).  Usage:  python3 test_empty_trip_locked.py
"""
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import requests                                                              # noqa: E402
def _blocked(*a, **k):
    raise RuntimeError('network blocked in tests')
requests.get = requests.post = _blocked
import app as m                                                               # noqa: E402
from test_support import ensure_test_user                                     # noqa: E402
from test_payment_combinations import _Form                                   # noqa: E402

ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


tmp = tempfile.mkdtemp(prefix='emptylock_')
dbp = os.path.join(tmp, 't.db')
src = sqlite3.connect('fleet.db'); src.backup(sqlite3.connect(dbp)); src.close()
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
one = lambda q_, *a: (c.execute(q_, a).fetchone() or [None])[0]
uid = ensure_test_user(dbp)
c.execute("INSERT INTO vehicles (vehicle_no, type) VALUES ('ELV01','own')"); veh = one("SELECT id FROM vehicles WHERE vehicle_no='ELV01'")
c.execute("INSERT INTO parties (name) VALUES ('EL PARTY')"); party = one("SELECT id FROM parties WHERE name='EL PARTY'")


def mk(lr, empty):
    cols = {'date': '2026-09-20', 'lr_number': lr, 'vehicle_id': veh, 'type': 'own', 'from_loc': 'A', 'to_loc': 'B', 'is_empty': 1 if empty else 0}
    if not empty:
        cols.update({'party_id': party, 'quantity': 10, 'rate': 1000, 'rate_type': 'PER_MT', 'billed_amount': 10000})
    c.execute(f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


EMPTY = mk('Empty-EL1', True); NORMAL = mk('EL-2', False)
c.commit()


def gdb():
    x = sqlite3.connect(dbp); x.row_factory = sqlite3.Row
    return x


m.get_db = gdb
m.app.config['TESTING'] = True
cl = m.app.test_client()
with cl.session_transaction() as s:
    s['user_id'] = uid


def fresh():
    global c
    c.close(); c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row


def page(url):
    r = cl.get(url)
    return r.status_code, r.data.decode('utf-8', 'ignore')


def switch_disabled(html, ident):
    mo = re.search(r'<input[^>]*id="%s"[^>]*>' % ident, html)
    return None if not mo else (' disabled' in mo.group(0))


def form_dict(url):
    f = _Form(); f.feed(page(url)[1])
    d = {}
    for n, v in f.fields:
        d.setdefault(n, v)
    return d


def flag(tid):
    fresh()
    return one('SELECT is_empty FROM trips WHERE id=?', tid)


# ---------------------------------------------------------------------------------------------------------------------  A. the switch on every screen
check(switch_disabled(page('/trips/add')[1], 'tfIsEmpty') is False, 'A: website Add Trip: the switch is live')
check(switch_disabled(page('/app/trips/add')[1], 'twIsEmpty') is False, 'A: phone Add Trip: the switch is live')
for tid, label in ((EMPTY, 'empty'), (NORMAL, 'normal')):
    check(switch_disabled(page(f'/trips/edit/{tid}')[1], 'tfIsEmpty') is True, f'A: website, {label} trip, read-only view: disabled')
    check(switch_disabled(page(f'/trips/edit/{tid}?edit=1')[1], 'tfIsEmpty') is True, f'A: website, {label} trip, editing: disabled')
    check(switch_disabled(page(f'/app/trips/{tid}/edit')[1], 'twIsEmpty') is True, f'A: phone, {label} trip: disabled')
    html = page(f'/trips/edit/{tid}')[1]
    check('em.disabled = !on || TF_MODE_EDIT' in html and 'var TF_MODE_EDIT = true' in html, f'A: website, {label} trip: the Edit Trip button cannot unlock the switch')
check('checked' in re.search(r'<input[^>]*id="tfIsEmpty"[^>]*>', page(f'/trips/edit/{EMPTY}')[1]).group(0), 'A: an empty trip still shows the switch ON')
check('checked' not in re.search(r'<input[^>]*id="tfIsEmpty"[^>]*>', page(f'/trips/edit/{NORMAL}')[1]).group(0), 'A: a normal trip shows it OFF')
check('checked' in re.search(r'<input[^>]*id="twIsEmpty"[^>]*>', page(f'/app/trips/{EMPTY}/edit')[1]).group(0), 'A: phone: an empty trip shows it ON')

# a failed save redraws the screen: still disabled
body = form_dict(f'/trips/edit/{NORMAL}'); body['lr_number'] = 'Empty-EL1'                       # duplicate LR -> error page
r = cl.post(f'/trips/edit/{NORMAL}', data={k: [v] for k, v in body.items()})
check(r.status_code == 200 and switch_disabled(r.data.decode(), 'tfIsEmpty') is True, 'A: website, after a failed save: still disabled')
body = form_dict(f'/app/trips/{NORMAL}/edit'); body['lr_number'] = 'Empty-EL1'
r = cl.post(f'/app/trips/{NORMAL}/edit', data={k: [v] for k, v in body.items()})
check(r.status_code == 200 and switch_disabled(r.data.decode(), 'twIsEmpty') is True, 'A: phone, after a failed save: still disabled')

# ---------------------------------------------------------------------------------------------------------------------  B. the server holds the line
for surface, url, post in (('website', f'/trips/edit/%d', f'/trips/edit/%d'), ('phone', f'/app/trips/%d/edit', f'/app/trips/%d/edit')):
    # an empty trip, saved the way the page posts it (no is_empty at all, no party)
    body = form_dict(url % EMPTY)
    body.pop('is_empty', None); body['party_name'] = ''; body['driver_name'] = f'DRV-{surface}'
    body['return_to'] = ''
    r = cl.post(post % EMPTY, data={k: [v] for k, v in body.items()})
    fresh()
    check(r.status_code == 302 and flag(EMPTY) == 1, f'B: {surface}: saving an empty trip keeps it empty, with no party and no quantity demanded ({r.status_code}, flag {flag(EMPTY)})')
    check(one('SELECT driver_name FROM trips WHERE id=?', EMPTY) == f'DRV-{surface}', f'B: {surface}: and the rest of the edit is saved')
    # forged: switch on for a normal trip
    body = form_dict(url % NORMAL); body['is_empty'] = 'on'; body['driver_name'] = f'N-{surface}'; body['return_to'] = ''
    r = cl.post(post % NORMAL, data={k: [v] for k, v in body.items()})
    fresh()
    check(flag(NORMAL) == 0 and one('SELECT party_id FROM trips WHERE id=?', NORMAL) == party, f'B: {surface}: a forged "empty" on a normal trip changes nothing (flag {flag(NORMAL)})')
    check(one('SELECT driver_name FROM trips WHERE id=?', NORMAL) == f'N-{surface}' and r.status_code == 302, f'B: {surface}: the normal trip still saves its other edits')
    # a normal trip saved with nothing posted for the switch (what a disabled box does) stays normal
    body = form_dict(url % NORMAL); body.pop('is_empty', None); body['return_to'] = ''
    r = cl.post(post % NORMAL, data={k: [v] for k, v in body.items()})
    fresh()
    check(r.status_code == 302 and flag(NORMAL) == 0, f'B: {surface}: a normal trip saved without the switch stays a normal trip ({r.status_code}, flag {flag(NORMAL)})')

# ---------------------------------------------------------------------------------------------------------------------  C. Add Trip still stores the switch
body = form_dict('/trips/add')
body.update({'date': '2026-10-01', 'lr_number': 'Empty-EL9', 'vehicle_no': 'ELV01', 'type': 'own', 'from_loc': 'X', 'to_loc': 'Y', 'is_empty': 'on'})
r = cl.post('/trips/add', data={k: [v] for k, v in body.items()})
fresh()
check(r.status_code == 302 and one("SELECT is_empty FROM trips WHERE lr_number='Empty-EL9'") == 1, 'C: website Add Trip stores an empty trip')
body = form_dict('/app/trips/add')
body.update({'date': '2026-10-01', 'lr_number': 'Empty-EL10', 'vehicle_no': 'ELV01', 'type': 'own', 'from_loc': 'X', 'to_loc': 'Y', 'is_empty': 'on'})
r = cl.post('/app/trips/add', data={k: [v] for k, v in body.items()})
fresh()
check(r.status_code == 302 and one("SELECT is_empty FROM trips WHERE lr_number='Empty-EL10'") == 1, 'C: phone Add Trip stores an empty trip')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
