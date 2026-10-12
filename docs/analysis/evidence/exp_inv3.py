import os, sys, shutil, sqlite3, io
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_inv3.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('x'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A, fitz
A.DB=DB
H={'Origin':'http://localhost'}
cl=A.app.test_client()
with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
t=c.execute("select id,party_id,billed_amount,rate,quantity from trips where party_id=17 and rate>0 and quantity>0 and rate_type='PER_MT' limit 1").fetchone()
print('trip',dict(t))
r=cl.post('/invoice-center/generate',data={'mode':'generate','invoice_type':'party','party_id':'17','trip_ids':[str(t['id'])],'reviewed_trip_ids':[str(t['id'])]},headers=H)
def pdf_amounts(data):
    d=fitz.open(stream=data,filetype='pdf'); return ' '.join(p.get_text() for p in d)
txt1=pdf_amounts(r.data)
bid=c.execute("select id,invoice_number from invoice_batches order by id desc limit 1").fetchone()
print('issued',bid['invoice_number'],'PDF contains billed:',f"{t['billed_amount']:,.2f}" in txt1 or f"{t['billed_amount']:,.0f}" in txt1)
# edit trip rate (website edit form needs many fields) -> use direct DB change that the edit route would do
newrate=t['rate']+100
c.execute("update trips set rate=?, billed_amount=? where id=?",(newrate,newrate*t['quantity'],t['id'])); c.commit()
r2=cl.get(f"/invoices/generated/{bid['id']}/pdf")
txt2=pdf_amounts(r2.data)
print('after raising the trip rate by 100/MT, re-downloaded PDF of the SAME invoice number shows new amount:', f"{newrate*t['quantity']:,.2f}" in txt2 or f"{newrate*t['quantity']:,.0f}" in txt2, '| shows old amount:', f"{t['billed_amount']:,.2f}" in txt2 or f"{t['billed_amount']:,.0f}" in txt2)
