import os, sys, shutil, sqlite3
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_del.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('network blocked'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
def client():
    cl=A.app.test_client()
    with cl.session_transaction() as s: s['user_id']=uid; s['username']='test_runner'; s['is_admin']=1
    return cl
H={'Origin':'http://localhost'}
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
def q(sql,*a): return c.execute(sql,a).fetchall()
# vehicle with most links
v=q("""select v.id,v.vehicle_no,(select count(*) from trips where vehicle_id=v.id) t,(select count(*) from maintenance where vehicle_id=v.id) m,
 (select count(*) from vehicle_compliance where vehicle_id=v.id) vc,(select count(*) from insurance_policies where vehicle_id=v.id) ip,
 (select count(*) from toll_entries where vehicle_id=v.id) te from vehicles v order by t desc limit 1""")[0]
print('vehicle to delete:',dict(v))
# trip with payment allocation?
pa=q("select count(*) n from payment_allocations")[0]['n']
cl=client()
r=cl.post(f"/vehicles/delete/{v['id']}",headers=H); print('delete vehicle ->',r.status_code, r.headers.get('Location'))
for t,col in [('trips','vehicle_id'),('maintenance','vehicle_id'),('vehicle_compliance','vehicle_id'),('insurance_policies','vehicle_id'),('toll_entries','vehicle_id'),('batteries','vehicle_id'),('vehicle_challans','vehicle_id')]:
    try:
        n=q(f"select count(*) n from {t} where {col}=?",v['id'])[0]['n']
        print(f'  {t}.{col} still pointing at deleted vehicle: {n}')
    except Exception as e: print(t,e)
print('  trips with vehicle_id not matching any vehicle (all):',q("select count(*) n from trips where vehicle_id is not null and vehicle_id not in (select id from vehicles)")[0]['n'])
# does the trips page still render?
r=cl.get('/trips'); print('GET /trips ->',r.status_code, 'len',len(r.data))
r=cl.get('/dashboard'); print('GET /dashboard ->',r.status_code)
r=cl.get(f"/maintenance"); print('GET /maintenance ->',r.status_code)
# the orphaned trip detail page
t=q("select id from trips where vehicle_id=? limit 1",v['id'])
if t:
    r=cl.get(f"/trips/{t[0]['id']}"); print('GET trip detail of orphaned trip ->',r.status_code)
    r=cl.get("/export"); print('GET /export ->',r.status_code)
# invoice batch delete: what stays
b=q("select id,invoice_number from invoice_batches limit 1")[0]
print('batch',dict(b))
r=cl.post(f"/invoices/generated/{b['id']}/delete",headers=H); print('delete batch ->',r.status_code)
print('  batch still exists:',q("select count(*) n from invoice_batches where id=?",b['id'])[0]['n'],
      ' links left:',q("select count(*) n from invoice_batch_trips where invoice_batch_id=?",b['id'])[0]['n'],
      ' exclusions left (orphan):',q("select count(*) n from invoice_batch_charge_exclusions where invoice_batch_id=?",b['id'])[0]['n'])
print('  counter now',q("select value from settings where key='next_invoice_number'")[0][0],'(number',b['invoice_number'],'is now a gap / reusable?)')
print('  access_logs / audit entry of this delete:',q("select count(*) n from access_logs")[0]['n'])
