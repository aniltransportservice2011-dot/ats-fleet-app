import os, sys
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_recon.db'
os.environ['DATABASE_PATH']=DB
import app as A
A.DB=DB
ctx=A.app.test_request_context('/'); ctx.push()
conn=A.get_db()
q=lambda s,*a:conn.execute(s,a).fetchall()
for pid in (2,33):
    r=A._get_party_ledger_entries(pid,conn); ents=r['entries'] if isinstance(r,dict) else r
    tb=[e for e in ents if e['kind']=='Trip Bill']; pi=[e for e in ents if e['kind']=='Payment In']
    print('party',pid,'ledger trip-bill rows',len(tb),round(sum(e['debit'] or 0 for e in tb),2),'| trips table',q("select count(*),round(sum(billed_amount),2) from trips where party_id=?",pid)[0][:])
    print('   ledger payment rows',len(pi),round(sum(e['credit'] or 0 for e in pi),2),'| payments table',q("select count(*),round(sum(amount),2) from payments where party_id=? and payment_type='received'",pid)[0][:],
          '| trips.payment_received',q("select round(sum(payment_received),2) from trips where party_id=?",pid)[0][0])
    print('   keys sample',list(ents[0].keys()))
    # trip-level: ledger bill vs trips.billed (maybe ledger nets advance?)
    for e in tb[:0]: pass
    # find trips where ledger debit != billed
    ids={}
    for e in tb:
        ids[e.get('ref') or e.get('lr')]=e['debit']
    bad=[]
    for t in q("select id,lr_number,billed_amount,payment_received,party_advance from trips where party_id=?",pid):
        pass
