import os, sys, shutil, sqlite3, re
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_ten.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
from test_support import ensure_test_user
uid=ensure_test_user(DB)
c=sqlite3.connect(DB)
# pretend half the trips belong to company 2 (disposable copy only)
c.execute("update trips set company_id=2 where id%2=0"); c.execute("update parties set company_id=2 where id%2=0"); c.commit()
import app as A
A.DB=DB
def view(cid):
    cl=A.app.test_client()
    with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1; s['company_id']=cid
    h=cl.get('/trips?per_page=100').data.decode()
    return len(re.findall(r'LR-|ATS-|<tr',h)), cl.get('/accounts?per_page=100').status_code
print('trips rows visible as company 1:',view(1)[0],' as company 2:',view(2)[0],' (identical => no isolation)')
print('DB: trips co1',c.execute("select count(*) from trips where company_id=1").fetchone()[0],'co2',c.execute("select count(*) from trips where company_id=2").fetchone()[0])
cl=A.app.test_client()
with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1; s['company_id']=2
r=cl.get('/ledger/party/1'); print('company-2 session opening company-1 party 1 ledger (party id 1 is odd => company 1):',r.status_code)
r=cl.get('/trips/1'); print('company-2 session opening company-1 trip 1:',r.status_code)
