import os, sys, shutil, sqlite3, re
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_del2.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('x'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
cl=A.app.test_client()
with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1
def txt(url):
    rr=cl.get(url); print('   [status',rr.status_code,url+']'); h=rr.data.decode()
    h=re.sub(r'<script.*?</script>|<style.*?</style>','',h,flags=re.S)
    return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',h))
pages=['/dashboard','/accounts','/ledger/party/17','/ledger/party/1','/business-performance','/performance','/fleet-utilization','/route-analytics','/vehicles','/maintenance']
before={p:txt(p) for p in pages}
cl.post('/vehicles/delete/5',headers={'Origin':'http://localhost'})
for p in pages:
    a=txt(p)
    same=a==before[p]
    print(p,'identical' if same else 'CHANGED')
    if not same:
        import difflib
        w1=before[p].split(' '); w2=a.split(' ')
        d=[x for x in difflib.unified_diff(w1,w2,lineterm='',n=0) if not x.startswith(('---','+++','@@'))]
        print('   ',' '.join(d[:40])[:400])
