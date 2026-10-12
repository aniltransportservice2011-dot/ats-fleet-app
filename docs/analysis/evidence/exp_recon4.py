import os, sys, shutil
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_recon.db'
os.environ['DATABASE_PATH']=DB
import app as A
A.DB=DB
ctx=A.app.test_request_context('/'); ctx.push()
conn=A.get_db()
q=lambda s,*a:conn.execute(s,a).fetchall()
pb,vb=A._bulk_balances(conn)
resid=[];n=0
for p in q("select id,name,opening_balance from parties"):
    pid=p['id']
    r=A._get_party_ledger_entries(pid,conn); ents=r['entries'] if isinstance(r,dict) else r
    kinds={}
    for e in ents:
        k=e.get('kind'); kinds[k]=kinds.get(k,0)+(e.get('debit') or 0)-(e.get('credit') or 0)
    led=sum(kinds.values())
    # independent re-derivation from tables
    t=q("select coalesce(sum(billed_amount),0) b from trips where party_id=?",pid)[0]['b']
    pay=q("select coalesce(sum(amount),0) a from payments where party_id=? and payment_type='received'",pid)[0]['a']
    ob=p['opening_balance'] or 0
    adj=kinds.get('Expense Adj.',0)
    lv=q("select id from vendors where linked_party_id=?",pid)
    vbal=sum(vb.get(v['id'],0) for v in lv)
    indep=ob+t-pay+adj; vend_kinds=[k for k in kinds if k not in ('Payment In','Trip Bill','Expense Adj.','Opening Balance')]
    n+=1
    # also compare to bulk
    if abs(led-pb.get(pid,0))>0.01: resid.append(('led!=bulk',pid))
    if abs(indep-led)>0.01: resid.append(('formula',pid,round(indep,2),round(led,2),vend_kinds))
print('parties checked',n,'ledger!=bulk:',sum(1 for r in resid if r[0]=='led!=bulk'),'| formula(opening+billed-received+adj)!=ledger:',sum(1 for r in resid if r[0]=='formula'))
for r in resid[:6]: print(r)
