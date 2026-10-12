import os, sys, shutil
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_recon.db'
os.environ['DATABASE_PATH']=DB
import app as A
A.DB=DB
ctx=A.app.test_request_context('/'); ctx.push()
conn=A.get_db()
for pid in (47,66,54,20):
    print('=== party',pid, conn.execute("select name,opening_balance from parties where id=?",(pid,)).fetchone()[:])
    r=A._get_party_ledger_entries(pid,conn)
    ents=r['entries'] if isinstance(r,dict) else r
    import collections
    k=collections.OrderedDict()
    for e in ents:
        key=e.get('kind') or e.get('type')
        k.setdefault(key,[0,0,0]); k[key][0]+=1; k[key][1]+=e.get('debit') or 0; k[key][2]+=e.get('credit') or 0
    for key,v in k.items(): print('  ',key,v)
    print('   trips billed',conn.execute("select sum(billed_amount),sum(payment_received) from trips where party_id=?",(pid,)).fetchone()[:])
    print('   linked vendor', conn.execute("select id,name from vendors where linked_party_id=?",(pid,)).fetchall() and [tuple(x) for x in conn.execute("select id,name from vendors where linked_party_id=?",(pid,)).fetchall()])
