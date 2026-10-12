import os, sys, shutil, sqlite3, io
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_up.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('x'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
# redirect uploads to scratch so the project's static/uploads is never touched
A.INSURANCE_UPLOAD_DIR=SP+'/up_ins'
cl=A.app.test_client()
with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1
H={'Origin':'http://localhost'}
data={'vehicle_no':'OR14Q8217','insurer_name':'TestInsurer','premium_amount':'100','start_date':'2026-01-01','expiry_date':'2027-01-01','insurance_type':'Comprehensive',
 'policy_doc':(io.BytesIO(b'<script>alert(document.cookie)</script>'),'evil.html'),'invoice_doc':(io.BytesIO(b'MZ\x90\x00'),'run.exe'),'rc_doc':(io.BytesIO(b'x'*5_000_000),'big.pdf')}
r=cl.post('/maintenance/insurance/add',data=data,headers=H,content_type='multipart/form-data')
print('upload html/exe/5MB ->',r.status_code)
c=sqlite3.connect(DB)
print(c.execute("select policy_doc_path,invoice_doc_path,rc_doc_path from insurance_policies order by id desc limit 1").fetchone())
print('files on disk:',[(root.split('up_ins')[-1],f) for root,_,fs in os.walk(SP+'/up_ins') for f in fs] if os.path.isdir(SP+'/up_ins') else 'dir not created in redirected path')
