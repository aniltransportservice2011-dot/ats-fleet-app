import os, sys, shutil, sqlite3, threading, re, datetime
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_pay.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import requests
requests.get=requests.post=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('x'))
from test_support import ensure_test_user
uid=ensure_test_user(DB)
import app as A
A.DB=DB
H={'Origin':'http://localhost'}
def newcl():
    cl=A.app.test_client()
    with cl.session_transaction() as s: s['user_id']=uid; s['username']='t'; s['is_admin']=1
    return cl
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
q=lambda s,*a:c.execute(s,a).fetchall()
party=q("select party_id p,count(*) n from trips where party_id is not null group by party_id order by n desc limit 1")[0]['p']
today=datetime.date.today().isoformat()
# ---- C1 concurrent identical payments
pre=q("select count(*) n from payments where party_id=?",party)[0]['n']
res={}
def go(i):
    cl=newcl(); r=cl.post(f'/payment/party/{party}',data={'date':today,'amount':'777','mode':'UPI','reference_id':'DUP1'},headers=H); res[i]=(r.status_code,r.headers.get('Location','')[:70])
ths=[threading.Thread(target=go,args=(i,)) for i in range(8)]; [t.start() for t in ths]; [t.join() for t in ths]
post=q("select count(*) n from payments where party_id=? and reference_id='DUP1'",party)[0]['n']
print('C1 8 simultaneous identical payments of 777 -> rows stored:',post)
print('   responses:',sorted(set(v[1].split('?')[-1][:50] for v in res.values())))
# sequential repeat after >10s window is by design accepted; same payment twice within window refused:
cl=newcl(); r=cl.post(f'/payment/party/{party}',data={'date':today,'amount':'777','mode':'UPI','reference_id':'DUP1'},headers=H)
print('   sequential repeat within 10 s ->',r.headers.get('Location','')[-90:])
print('   payments.allocated vs sum(payment_allocations) for these:',q("select sum(amount) a,sum(allocated_amount) al from payments where reference_id='DUP1'")[0][:])
print('   sum of allocations rows for DUP1 payments:',q("select coalesce(sum(pa.amount),0) s from payment_allocations pa join payments p on p.id=pa.payment_id where p.reference_id='DUP1'")[0][0])
print('   trips over-received after race (payment_received > billed_amount+0.01):',q("select count(*) n from trips where party_id=? and payment_received>billed_amount+0.01",party)[0]['n'])
# ---- C2 employee
cl=newcl()
cl.post('/employees/add',data={'name':'Test Emp','type':'Driver','role':'Driver','basic_salary':'10000','next':'employees'},headers=H)
eid=q("select id from employees where name='Test Emp'")[0]['id']
cl.post(f'/employees/{eid}/advance/add',data={'amount':'1000','type':'given','date':today},headers=H)
r=cl.post(f'/employees/{eid}/advance/add',data={'amount':'5000','type':'repaid','date':today},headers=H)
adv=q("select type,sum(amount) s from advances where employee='Test Emp' group by type")
print('C2 given 1000 then repaid 5000 ->',[tuple(x) for x in adv], 'HTTP',r.status_code,r.headers.get('Location'))
bal=lambda n:[tuple(x) for x in q("select type,sum(amount) from advances where employee=? group by type",n)]
# rename employee
cl.post(f'/employees/{eid}/edit',data={'name':'Test Emp Renamed','type':'Driver','role':'Driver','basic_salary':'10000'},headers=H)
print('   after rename: advances under old name',bal('Test Emp'),' under new name',bal('Test Emp Renamed'))
page=cl.get('/employees/Test%20Emp%20Renamed/ledger') 
print('   ledger route for new name status',page.status_code)
# ---- D invalid input feedback
r=cl.post(f'/employees/{eid}/advance/add',data={'amount':'abc','type':'given','date':today},headers=H)
print('D1 advance with amount=abc ->',r.status_code,r.headers.get('Location'),'(no error param => user told nothing)')
r=cl.post(f'/employees/{eid}/edit',data={'name':'   ','type':'Driver','basic_salary':'x'},headers=H)
print('D2 edit employee with blank name + salary x ->',r.status_code)
r=cl.post(f'/employees/{eid}/edit',data={'name':'','type':'Driver','basic_salary':'10000'},headers=H)
print('D3 edit employee with empty name ->',r.status_code,'; name now:',repr(q("select name from employees where id=?",eid)[0][0]))
