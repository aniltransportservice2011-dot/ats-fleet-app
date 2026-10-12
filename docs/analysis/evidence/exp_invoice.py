import os, sys, shutil, sqlite3, threading, time
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_inv.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('network blocked'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
pt=c.execute("select party_id,count(*) n from trips where party_id is not null group by party_id order by n desc limit 1").fetchone()
trips=[r[0] for r in c.execute("select id from trips where party_id=? limit 16",(pt['party_id'],))]
print('party',pt['party_id'],'trips',len(trips))
before_n=c.execute("select value from settings where key='next_invoice_number'").fetchone()
print('counter before',before_n[0] if before_n else None, 'batches before',c.execute('select count(*) from invoice_batches').fetchone()[0])
c.close()
def post(i,res):
    cl=A.app.test_client()
    with cl.session_transaction() as s: s['user_id']=uid; s['username']='test_runner'; s['is_admin']=1
    t=trips[i%len(trips)]
    r=cl.post('/invoice-center/generate',data={'mode':'draft','invoice_type':'party','party_id':str(pt['party_id']),'trip_ids':[str(t)],'reviewed_trip_ids':[str(t)]},headers={'Origin':'http://localhost'})
    res[i]=r.status_code
def run(n):
    res={}; ths=[threading.Thread(target=post,args=(i,res)) for i in range(n)]
    [t.start() for t in ths]; [t.join() for t in ths]; return res
print('--- natural concurrency: 12 simultaneous requests')
print(run(12))
c=sqlite3.connect(DB)
rows=c.execute("select invoice_number,count(*) from invoice_batches group by invoice_number having count(*)>1").fetchall()
print('duplicate numbers (natural):',rows)
print('counter after',c.execute("select value from settings where key='next_invoice_number'").fetchone())
print('batches',c.execute('select count(*) from invoice_batches').fetchone()[0])
c.close()
