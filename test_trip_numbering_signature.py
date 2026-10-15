"""Tests for: suggested LR / empty-trip numbers (Settings > Invoice > Trip Numbering), the one shared definition of an "empty run" in the
analytics, and the authorised signature + stamp picture on invoices. Runs against a disposable copy of fleet.db (the real file is never
modified); the signature pictures it writes under static/uploads/signature are removed again.

Usage:  python3 test_trip_numbering_signature.py
"""
import datetime
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
import requests                                                              # noqa: E402
def _blocked(*a, **k):
    raise RuntimeError('network blocked in tests')
requests.get = requests.post = _blocked                                       # never spend a real API credit or send a real SMS

from PIL import Image                                                         # noqa: E402
from flask import template_rendered                                           # noqa: E402
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


TMP = tempfile.mkdtemp(prefix='tripnum_')
DBP = os.path.join(TMP, 'test.db')
src = sqlite3.connect(os.environ.get('TEST_SOURCE_DB') or os.path.join(REPO, 'fleet.db'))
src.backup(sqlite3.connect(DBP))
src.close()
admin_id = ensure_test_user(DBP)


def gdb():
    c = sqlite3.connect(DBP)
    c.row_factory = sqlite3.Row
    return c


m.get_db = gdb
m.app.config['TESTING'] = True
db = gdb()
# SAFETY: this test uploads / replaces / removes signature pictures, and the app deletes the OLD picture's file when one is replaced. The database here
# is a copy of the real one, so it may still point at a real, user-uploaded picture in the real static/uploads/signature folder — forget that
# pointer first, or the test would delete the user's actual file (it once did).
db.execute("UPDATE settings SET value='' WHERE key='signature_path'")
db.commit()


def add_user(name, level, mods=None):
    db.execute("INSERT INTO users (username,password_hash,role,is_admin,full_name,access_level,status,phone,created_at,module_access) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", (name, 'x', 'Manager', 0, name, level, 'Active', name, '2026-01-01', mods))
    db.commit()
    return db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()[0]


def client_for(uid):
    cl = m.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'] = uid
    return cl


admin = client_for(admin_id)
full = client_for(add_user('9222222201', 'Full Access'))
readonly = client_for(add_user('9222222202', 'Read Only'))
lim_trips = client_for(add_user('9222222203', 'Limited Access', 'Trips:v'))
lim_none = client_for(add_user('9222222204', 'Limited Access', 'Vehicles:v'))
anon = m.app.test_client()
H = {'X-App-Request': '1', 'Accept': 'application/json'}
D = datetime.date(2026, 10, 15)

# ------------------------------------------------------------------ the format language
R = m._render_trip_number
check(R('ATS-{DATE}-LR-{SEQ:4}', D, 1) == 'ATS-151026-LR-0001', 'the example format renders ATS-151026-LR-0001')
check(R('{DD}/{MM}/{YY}/{YYYY}/{MON}', D, 1) == '15/10/26/2026/OCT', 'DD MM YY YYYY MON')
check(R('E-{SEQ:2}', D, 7) == 'E-07', 'SEQ:2 pads one digit to two')
check(R('E-{SEQ:2}', D, 100) == 'E-100' and R('E-{SEQ:2}', D, 101) == 'E-101', 'SEQ:2 simply grows past 99 (no 4-digit padding)')
check(R('LR-{SEQ:4}', D, 12345) == 'LR-12345', 'SEQ:4 grows past 9999')
check(R('LR-{SEQ}', D, 5) == 'LR-5', 'plain SEQ has no padding')
V = m._validate_number_format
for good in ('ATS-{DATE}-LR-{SEQ:4}', 'Empty-{DATE}-{SEQ:2}', 'LR/{YYYY}/{SEQ}', '{SEQ}', 'A B_c#1.2:{MON}-{SEQ:3}'):
    check(V(good) is None, f'valid format accepted: {good}')
for bad, why in (('', 'blank'), ('ATS-{DATE}', 'no SEQ'), ('{SEQ}-{SEQ}', 'two SEQ'), ('A-{FOO}-{SEQ}', 'unknown part'), ('A-{SEQ', 'stray brace'),
                 ('A%{SEQ}', 'percent sign'), ('A$-{SEQ}', 'dollar sign'), ('x' * 61 + '{SEQ}', 'over 60 characters'), ('x' * 45 + '{SEQ}', 'numbers over 40 characters'),
                 ('{SEQ:123}', 'width too big'), ('A\n{SEQ}', 'control character')):
    check(V(bad) is not None, f'bad format refused ({why})')
check(m._company_initials('Anil Transport Service') == 'ATS', 'initials: Anil Transport Service -> ATS')
check(m._company_initials('') == 'LR' and m._company_initials('a b c d e') == 'ABCD', 'initials: blank -> LR, capped at 4')

# ------------------------------------------------------------------ next number, against real rows
def add_trip(lr, date='2026-10-15', is_empty=None):
    db.execute("INSERT INTO trips (date, lr_number, is_empty) VALUES (?,?,?)", (date, lr, is_empty))
    db.commit()


FMT = 'ATS-{DATE}-LR-{SEQ:4}'
N = lambda fmt, d=D: m._next_trip_number(db, fmt, d)
check(N(FMT) == 'ATS-151026-LR-0001', 'first LR of a day is 0001')
add_trip('ATS-151026-LR-0001')
check(N(FMT) == 'ATS-151026-LR-0002', 'next is 0002')
add_trip('ATS-151026-LR-0002')
add_trip('ATS-151026-LR-0010')
check(N(FMT) == 'ATS-151026-LR-0011', 'a typed number (0010) moves the suggestion past it')
check(N(FMT, datetime.date(2026, 10, 16)) == 'ATS-161026-LR-0001', 'a new day starts again at 0001')
add_trip(' ats-151026-lr-0020 ')
check(N(FMT) == 'ATS-151026-LR-0021', 'case and spaces are ignored when counting')
add_trip('ATS-151026-LR-ABC')
add_trip('ATS-151026-LR-0005X')
check(N(FMT) == 'ATS-151026-LR-0021', 'look-alike numbers that are not in the format do not count')
EF = 'Empty-{DATE}-{SEQ:2}'
check(N(EF) == 'Empty-151026-01', 'first empty trip is 01 (two digits, not 0001)')
add_trip('Empty-151026-09')
check(N(EF) == 'Empty-151026-10', 'empty: 09 -> 10')
add_trip('Empty-151026-99')
check(N(EF) == 'Empty-151026-100', 'empty: after 99 it is 100 (integer, not 0100)')
add_trip('Empty-151026-100')
check(N(EF) == 'Empty-151026-101', 'empty: 101')
add_trip('AxB-5')
check(N('A_B-{SEQ}') == 'A_B-1', 'an underscore in the format is literal, not a wildcard')
add_trip('Z-OCT-3', '2026-10-02')
check(N('Z-{MON}-{SEQ}') == 'Z-OCT-4', 'monthly format: continues through the month')
check(N('Z-{MON}-{SEQ}', datetime.date(2026, 11, 1)) == 'Z-NOV-1', 'monthly format: restarts next month')
add_trip('PLAIN-7')
check(N('PLAIN-{SEQ}', datetime.date(2030, 1, 1)) == 'PLAIN-8', 'format without a date never restarts')
# a candidate that is still taken (different pattern holding the exact text) is skipped
db.execute("DELETE FROM trips WHERE lr_number LIKE 'Q%'"); db.commit()
add_trip('Q-0')            # not counted by the pattern (digits only after Q-? it IS counted -> 0)
check(N('Q-{SEQ}') == 'Q-1', 'sequence starts after the highest used')

# ------------------------------------------------------------------ settings: defaults, saving, validation
lr_fmt, empty_fmt = m._load_trip_number_formats(db)
company_name = (db.execute("SELECT value FROM settings WHERE key='company_name' AND company_id=1").fetchone() or {'value': ''})['value']
check(lr_fmt == f'{m._company_initials(company_name)}-{{DATE}}-LR-{{SEQ:4}}' and empty_fmt == 'Empty-{DATE}-{SEQ:2}', f'defaults when nothing is saved ({lr_fmt}, {empty_fmt})')
r = admin.post('/settings?tab=invoice', data={'form_type': 'trip_numbering', 'lr_format': 'XY-{DD}{MM}{YY}-{SEQ:3}', 'empty_format': 'KHALI-{SEQ}'})
check(r.status_code in (200, 302), 'website save accepted')
check(m._load_trip_number_formats(db) == ('XY-{DD}{MM}{YY}-{SEQ:3}', 'KHALI-{SEQ}'), 'saved formats are used')
page = admin.get('/settings?tab=invoice').data.decode()
check('XY-{DD}{MM}{YY}-{SEQ:3}' in page and 'Trip Numbering' in page and 'Authorised Signature' in page, 'the Invoice tab shows both new cards with the saved format')
r = admin.post('/settings?tab=invoice', data={'form_type': 'trip_numbering', 'lr_format': 'NOSEQ-{DATE}', 'empty_format': 'E-{SEQ}'})
check(r.status_code == 302 and 'tn_error=' in r.headers.get('Location', ''), 'website: a format without {SEQ} is refused with a message')
check(m._load_trip_number_formats(db)[0] == 'XY-{DD}{MM}{YY}-{SEQ:3}', 'website: the refused format was not saved')
check('exactly one' in admin.get(r.headers['Location']).data.decode(), 'website: the message is shown on the page')
r = admin.post('/app/settings/save', data={'form_type': 'trip_numbering', 'lr_format': 'A-{FOO}-{SEQ}', 'empty_format': 'E-{SEQ}'}, headers=H)
check(r.status_code == 400 and 'Unknown part' in (r.get_json() or {}).get('error', ''), 'phone app: unknown part refused with a message')
r = admin.post('/app/settings/save', data={'form_type': 'trip_numbering', 'lr_format': 'AP-{DATE}-{SEQ:4}', 'empty_format': 'AE-{DATE}-{SEQ:2}'}, headers=H)
check(r.status_code == 200 and m._load_trip_number_formats(db) == ('AP-{DATE}-{SEQ:4}', 'AE-{DATE}-{SEQ:2}'), 'phone app: valid formats saved')
for who, cl in (('Full Access (not the Administrator)', full), ('Limited', lim_trips)):
    r = cl.post('/settings?tab=invoice', data={'form_type': 'trip_numbering', 'lr_format': 'HACK-{SEQ}', 'empty_format': 'HACK-{SEQ}'})
    check(m._load_trip_number_formats(db)[0] == 'AP-{DATE}-{SEQ:4}', f'{who} cannot change the numbering')
db.execute("UPDATE settings SET value='broken {' WHERE key='lr_format' AND company_id=1"); db.commit()
check(m._load_trip_number_formats(db)[0].endswith('-{DATE}-LR-{SEQ:4}'), 'a corrupt saved format falls back to the default')
db.execute("DELETE FROM settings WHERE key IN ('lr_format','empty_format')"); db.commit()
admin.post('/settings?tab=invoice', data={'form_type': 'trip_numbering', 'lr_format': FMT, 'empty_format': EF})

# ------------------------------------------------------------------ suggestion endpoint + who may call it
r = admin.get('/trips/next-number?date=2026-10-15')
j = r.get_json() or {}
check(r.status_code == 200 and j.get('ok') and j.get('number') == 'ATS-151026-LR-0021', f'endpoint suggests the next LR ({j})')
j = (admin.get('/trips/next-number?date=2026-10-15&empty=1').get_json() or {})
check(j.get('number') == 'Empty-151026-101', f'endpoint suggests the next empty-trip number ({j})')
j = (admin.get('/trips/next-number?date=not-a-date').get_json() or {})
today_lr = m._render_trip_number(FMT, datetime.date.today(), 1)
check(j.get('ok') and j['number'].startswith(today_lr.rsplit('-', 1)[0]), 'a bad date falls back to today')
j = (admin.get('/app/trips/next-number?date=2026-10-15').get_json() or {})
check(j.get('number') == 'ATS-151026-LR-0021', 'the phone-app address answers the same')
check(readonly.get('/trips/next-number').status_code == 200 and lim_trips.get('/trips/next-number').status_code == 200, 'Read Only and Limited(Trips) users can use it')
r = lim_none.get('/trips/next-number')
check(r.status_code != 200 or not (r.get_json(silent=True) or {}).get('ok'), 'a Limited user without the Trips screen cannot use it')
r = anon.get('/trips/next-number')
check(r.status_code in (302, 401, 403) and not (r.get_json(silent=True) or {}).get('ok'), 'signed-out visitors cannot use it')
t_add = admin.get('/trips/add').data.decode()
check('trip_number_suggest.js' in t_add and "'/trips/next-number'" in t_add, 'Add Trip loads the suggestion script')
tid = db.execute("SELECT id FROM trips WHERE lr_number='ATS-151026-LR-0001'").fetchone()[0]
t_edit = admin.get(f'/trips/{tid}/edit').data.decode()
check('trip_number_suggest.js' not in t_edit, 'Edit Trip does not (an existing LR is never replaced)')
a_add = admin.get('/app/trips/add').data.decode()
check('trip_number_suggest.js' in a_add and "'/app/trips/next-number'" in a_add, 'phone Add Trip loads the suggestion script')

# ------------------------------------------------------------------ saving real trips with suggested numbers
veh = db.execute("SELECT vehicle_no FROM vehicles ORDER BY id LIMIT 1").fetchone()[0]
party = db.execute("SELECT name FROM parties ORDER BY id LIMIT 1").fetchone()[0]
SAVE_DATE = '2026-12-05'
def save_trip(cl, lr, empty=False, url='/trips/add'):
    form = {'date': SAVE_DATE, 'lr_number': lr, 'vehicle_no': veh, 'party_name': party, 'rate_type': 'PER_MT', 'rate': '100', 'quantity': '10'}
    if empty:
        form['is_empty'] = 'on'
        form.pop('party_name'); form.pop('rate'); form.pop('quantity')
    return cl.post(url, data=form)
sug = admin.get(f'/trips/next-number?date={SAVE_DATE}').get_json()['number']
check(sug == 'ATS-051226-LR-0001', f'a fresh day suggests 0001 ({sug})')
r = save_trip(admin, sug)
check(r.status_code == 302, f'a trip saved with the suggested LR ({r.status_code})')
check(db.execute("SELECT COUNT(*) FROM trips WHERE lr_number=?", (sug,)).fetchone()[0] == 1, 'it is in the database once')
sug2 = admin.get(f'/trips/next-number?date={SAVE_DATE}').get_json()['number']
check(sug2 == 'ATS-051226-LR-0002', f'the next suggestion moves on to 0002 ({sug2})')
r = save_trip(admin, sug)
check(r.status_code == 200 and 'already used' in r.data.decode(), 'saving the same LR again is refused with a message (no duplicates)')
r = save_trip(admin, sug.lower())
check(r.status_code == 200 and 'already used' in r.data.decode(), 'the duplicate check ignores upper/lower case')
check(db.execute("SELECT COUNT(*) FROM trips WHERE lr_number=? COLLATE NOCASE", (sug,)).fetchone()[0] == 1, 'still only one trip with that LR')
e1 = admin.get(f'/trips/next-number?date={SAVE_DATE}&empty=1').get_json()['number']
check(e1 == 'Empty-051226-01', f'first empty trip of the day is 01 ({e1})')
r = save_trip(admin, e1, empty=True)
check(r.status_code == 302, f'an empty trip saved with its number ({r.status_code})')
row = db.execute("SELECT is_empty FROM trips WHERE lr_number=?", (e1,)).fetchone()
check(row is not None and row['is_empty'] == 1, 'it is flagged as an empty trip')
check(admin.get(f'/trips/next-number?date={SAVE_DATE}&empty=1').get_json()['number'] == 'Empty-051226-02', 'the next empty trip is 02')
# the same two numbers from the phone app's add-trip address
sug3 = admin.get(f'/app/trips/next-number?date={SAVE_DATE}').get_json()['number']
check(sug3 == 'ATS-051226-LR-0002', 'phone app suggestion agrees')
r = save_trip(admin, sug3, url='/app/trips/add')
check(r.status_code in (200, 302), f'phone app add-trip accepts the suggested LR ({r.status_code})')
check(admin.get(f'/trips/next-number?date={SAVE_DATE}').get_json()['number'] == 'ATS-051226-LR-0003', 'and the suggestion moves on again')

# ------------------------------------------------------------------ "empty run" is one definition everywhere
db.execute("DELETE FROM trips WHERE lr_number IN ('E-LEGACY','E-SWITCH','E-LOADED','E-NULLS')"); db.commit()
add_trip('Empty_JULY_01', is_empty=0)       # old style: only the name says it is empty
add_trip('KHALI-1', is_empty=1)             # new style: the switch says it, the name is free
add_trip('PLAINLOAD-1', is_empty=0)
db.execute("INSERT INTO trips (date, lr_number, is_empty) VALUES ('2026-10-15', NULL, NULL)"); db.commit()
q = lambda extra: db.execute(f"SELECT lr_number FROM trips WHERE date='2026-10-15' AND {extra}").fetchall()
names = {r[0] for r in q(m._EMPTY_RUN_SQL)}
check('Empty_JULY_01' in names and 'KHALI-1' in names, 'empty = the switch OR an "Empty..." name')
check('PLAINLOAD-1' not in names and None not in names, 'a loaded trip and a trip with no LR/switch are not empty')
rest = {r[0] for r in q('NOT ' + m._EMPTY_RUN_SQL)}
check('PLAINLOAD-1' in rest and None in rest and 'KHALI-1' not in rest and 'Empty_JULY_01' not in rest, 'NOT-empty is exact, and NULL rows stay in (no SQL NULL trap)')
seen = []
def grab(sender, template, context, **kw):
    seen.append(context)
template_rendered.connect(grab, m.app)
try:
    seen.clear()
    admin.get('/fleet-utilization?date_from=2026-10-15&date_to=2026-10-15')
    ctx = next((c for c in seen if 'empty_run_trips' in c), None)
    check(ctx is not None and ctx['empty_run_trips'] >= 2, f"Fleet Utilization counts both the switch and the old-style name ({ctx and ctx['empty_run_trips']})")
finally:
    template_rendered.disconnect(grab, m.app)

# ------------------------------------------------------------------ signature + stamp
SIGDIR = m.SIGNATURE_UPLOAD_DIR
created = set()
def sig_files():
    out = set()
    for root, _, files in os.walk(SIGDIR):
        out |= {os.path.join(root, f) for f in files}
    return out
before_files = sig_files()
def sig_setting():
    row = db.execute("SELECT value FROM settings WHERE key='signature_path' AND company_id=1").fetchone()
    return row['value'] if row else ''
def img_bytes(fmt, size=(500, 260), mode='RGB', color=(255, 255, 255), draw=True, **kw):
    im = Image.new(mode, size, color)
    if draw:
        im.paste(Image.new(mode if mode != 'P' else 'RGB', (200, 80), (20, 40, 160)) if mode != 'P' else Image.new('P', (200, 80), 1), (150, 90))
    b = io.BytesIO(); im.save(b, fmt, **kw); return b.getvalue()
def up(cl, name, data, clear=None, hdr=H):
    d = {'signature': (io.BytesIO(data), name)}
    if clear is not None:
        d['clear_background'] = clear
    return cl.post('/settings/signature/upload', data=d, headers=hdr, content_type='multipart/form-data')
try:
    r = up(admin, 'stamp.png', img_bytes('PNG'), clear='1')
    j = r.get_json() or {}
    check(r.status_code == 200 and j.get('ok') and sig_setting().startswith('uploads/signature/1/signature_'), f'PNG upload accepted ({r.status_code})')
    first = sig_setting(); fpath = os.path.join(m.app.static_folder, first)
    im = Image.open(fpath); im.load()
    check(im.mode == 'RGBA' and im.getpixel((0, 0))[3] == 0, 'white background made see-through')
    check(im.size[0] < 500 and im.size[1] < 260, f'empty margin trimmed ({im.size})')
    r = up(admin, 'stamp2.png', img_bytes('PNG'), clear=None)
    second = sig_setting(); im2 = Image.open(os.path.join(m.app.static_folder, second)); im2.load()
    check(second != first and not os.path.exists(fpath), 'replacing deletes the old picture')
    check(im2.mode == 'RGB' or im2.getpixel((0, 0))[3] == 255, 'background kept when the box is not ticked')
    for name, data in (('s.jpg', img_bytes('JPEG')), ('s.jpeg', img_bytes('JPEG')), ('s.gif', img_bytes('GIF', mode='P', color=0)), ('s.webp', img_bytes('WEBP')),
                       ('s.bmp', img_bytes('BMP')), ('s.tif', img_bytes('TIFF')), ('noext', img_bytes('PNG'))):
        r = up(admin, name, data, clear='1')
        check(r.status_code == 200 and (r.get_json() or {}).get('ok'), f'signature format accepted: {name} ({r.status_code})')
    meta_png = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo
    pi = PngInfo(); pi.add_text('Description', '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    Image.new('RGB', (300, 120), (200, 30, 30)).save(meta_png, 'PNG', pnginfo=pi)
    check(up(admin, 'design.png', meta_png.getvalue()).status_code == 200, 'a PNG whose metadata mentions <svg> is accepted')
    keep = sig_setting()
    for name, data in (('fake.png', b'not a picture'), ('empty.png', b''), ('doc.pdf', b'%PDF-1.4 x'), ('tiny.png', img_bytes('PNG', size=(3, 3), draw=False))):
        r = up(admin, name, data)
        check(r.status_code == 400 and (r.get_json() or {}).get('error') and sig_setting() == keep, f'not a usable picture refused: {name}')
    check('signature' in (up(admin, 'tiny.png', img_bytes('PNG', size=(3, 3), draw=False)).get_json() or {}).get('error', ''), 'the "too small" message says signature, not logo')
    r = admin.post('/settings/signature/upload', data={}, headers=H, content_type='multipart/form-data')
    check(r.status_code == 400, 'no file chosen is refused')
    r = admin.post('/settings/signature/upload', data={'signature': (io.BytesIO(b'zzz'), 'x.png')}, content_type='multipart/form-data')
    check(r.status_code == 302 and 'sig_error=' in r.headers.get('Location', ''), 'website form: a refusal is shown on the Invoice tab')
    for who, cl in (('Full Access (not the Administrator)', full), ('Read Only', readonly), ('Limited', lim_trips), ('signed out', anon)):
        r = up(cl, 'x.png', img_bytes('PNG'))
        check(sig_setting() == keep and r.status_code != 200, f'{who} cannot upload the signature ({r.status_code})')
        r = cl.post('/settings/signature/remove', headers=H)
        check(sig_setting() == keep, f'{who} cannot remove it')
    # the picture is served only to signed-in users
    url = '/static/' + keep
    check(anon.get(url).status_code == 403, 'signed-out visitors cannot download the signature picture')
    check(admin.get(url).status_code == 200 and admin.get(url).data[:8] == b'\x89PNG\r\n\x1a\n', 'a signed-in user can')

    # ---- on the invoice itself
    batch = db.execute("SELECT id FROM invoice_batches ORDER BY id LIMIT 1").fetchone()
    if batch is None:                                  # a wiped / empty database has no invoice: make one from a trip we saved above
        tr = db.execute("SELECT t.id, t.party_id FROM trips t WHERE t.party_id IS NOT NULL AND COALESCE(t.is_empty,0)=0 ORDER BY t.id LIMIT 1").fetchone()
        cur = db.execute("INSERT INTO invoice_batches (invoice_number, invoice_type, party_id, invoice_date, due_date, status, payment_status, company_id) "
                         "VALUES ('TEST/INV/0001','party',?,'2026-12-05','2026-12-20','Generated','Pending',1)", (tr['party_id'],))
        db.execute("INSERT INTO invoice_batch_trips (invoice_batch_id, trip_id, company_id) VALUES (?,?,1)", (cur.lastrowid, tr['id']))
        db.commit()
        batch = db.execute("SELECT id FROM invoice_batches ORDER BY id LIMIT 1").fetchone()
    check(batch is not None, 'test database has an invoice to print')
    bid = batch['id']
    def pdf(label):
        r = admin.get(f'/invoices/generated/{bid}/pdf/preview')
        check(r.status_code == 200 and r.data[:4] == b'%PDF', f'invoice PDF builds ({label})')
        return r.data
    images = lambda b: len(re.findall(rb'/Subtype\s*/Image', b))
    db.execute("UPDATE settings SET value='' WHERE key='signature_path'"); db.execute("UPDATE settings SET value='1' WHERE key='show_signatory'"); db.commit()
    base = pdf('no signature')
    db.execute("UPDATE settings SET value=? WHERE key='signature_path'", (keep,)); db.commit()
    with_sig = pdf('with signature')
    check(images(with_sig) == images(base) + 1, f'the signature + stamp picture is printed on the invoice ({images(base)} -> {images(with_sig)} pictures)')
    db.execute("UPDATE settings SET value='0' WHERE key='show_signatory'"); db.commit()
    hidden = pdf('signatory switched off')
    check(images(hidden) == images(base), 'turning off Show Authorised Signatory removes the picture too')
    db.execute("UPDATE settings SET value='1' WHERE key='show_signatory'"); db.commit()
    shutil.copy(os.path.join(m.app.static_folder, keep), os.path.join(TMP, 'good.png'))
    open(os.path.join(m.app.static_folder, keep), 'wb').write(b'corrupt')
    pdf('unreadable signature file still prints the invoice')
    shutil.copy(os.path.join(TMP, 'good.png'), os.path.join(m.app.static_folder, keep))
    os.remove(os.path.join(m.app.static_folder, keep))
    pdf('signature file missing on disk still prints the invoice')
    db.execute("UPDATE settings SET value=? WHERE key='signature_path'", (keep,)); db.commit()
    open(os.path.join(m.app.static_folder, keep), 'wb').write(open(os.path.join(TMP, 'good.png'), 'rb').read())
    # ---- remove
    r = admin.post('/settings/signature/remove', headers=H)
    check(r.status_code == 200 and sig_setting() == '' and not os.path.exists(os.path.join(m.app.static_folder, keep)), 'Remove clears the setting and deletes the file')
    check('No signature / stamp' in admin.get('/settings?tab=invoice').data.decode(), 'the Invoice tab shows the empty state')
    page = admin.get('/app/settings/invoice').data.decode()
    check('Trip Numbering' in page and 'Authorised Signature' in page and 'tnLrFormat' in page, 'the phone app Invoice Settings shows both new cards')
finally:
    for f in sig_files() - before_files:
        try:
            os.remove(f)
        except OSError:
            pass
    for d in (os.path.join(SIGDIR, '1'), SIGDIR):
        try:
            os.rmdir(d)
        except OSError:
            pass

check(before_files <= sig_files(), 'this test did not delete any signature picture that existed before it ran (your real uploads are safe)')

# ------------------------------------------------------------------ repository hygiene
gi = open(os.path.join(REPO, '.gitignore')).read()
check('static/uploads/signature/' in gi, 'the signature folder is git-ignored (a stamp must never reach the repository)')

db.close()
print(f'\nSUMMARY: {ok} passed, {fail} failed')
shutil.rmtree(TMP, ignore_errors=True)
print('Disposable copy removed. Real fleet.db was never modified by this test.')
sys.exit(1 if fail else 0)
