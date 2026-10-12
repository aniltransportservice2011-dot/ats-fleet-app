import os, sys, shutil, sqlite3, re
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_inv2.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('x'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
H={'Origin':'http://localhost'}
cl=A.app.test_client()
with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
t=c.execute("select id,party_id from trips where party_id=17 limit 1").fetchone()
def gen(tid,pid,party=True):
    return cl.post('/invoice-center/generate',data={'mode':'generate','invoice_type':'party','party_id':str(pid),'trip_ids':[str(tid)],'reviewed_trip_ids':[str(tid)]},headers=H)
r1=gen(t['id'],t['party_id']); r2=gen(t['id'],t['party_id'])
print('same trip invoiced twice ->',r1.status_code,r2.status_code)
print([tuple(x) for x in c.execute("select ib.invoice_number,ibt.trip_id from invoice_batches ib join invoice_batch_trips ibt on ibt.invoice_batch_id=ib.id where ibt.trip_id=?",(t['id'],))])
# invoice for a trip that belongs to another party
other=c.execute("select id from trips where party_id!=17 and party_id is not null limit 1").fetchone()
r3=gen(other['id'],17)
print('invoice for party 17 using a trip of another party ->',r3.status_code,
      [tuple(x) for x in c.execute("select ib.party_id,t.party_id tp from invoice_batches ib join invoice_batch_trips ibt on ibt.invoice_batch_id=ib.id join trips t on t.id=ibt.trip_id where t.id=?",(other['id'],))])
# edit a generated invoice (status) and edit trip after invoice
bid=c.execute("select id from invoice_batches order by id desc limit 1").fetchone()['id']
print('batch edit page ->',cl.get(f'/invoices/generated/{bid}/edit').status_code)
# does trip edit change billed amount of an already-invoiced trip? any guard?
src=open('/Users/beenash/Downloads/fleet_local_app/app.py').read()
i=src.index("def edit_trip(")
print('edit_trip mentions invoice check:', 'invoice' in src[i:i+2500].lower())
i=src.index("def _update_trip_from_form(")
print('update trip mentions invoice check:', 'invoice_batch' in src[i:i+6000].lower())
