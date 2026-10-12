import os, sys, shutil, sqlite3
SP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,'/Users/beenash/Downloads/fleet_local_app')
DB=SP+'/exp_recon.db'; shutil.copy(SP+'/copy.db',DB)
os.environ['DATABASE_PATH']=DB
import app as A
A.DB=DB
conn=A.get_db()
pb,vb=A._bulk_balances(conn)
q=lambda s,*a:conn.execute(s,a).fetchall()
print('totals: trips',q("select count(*),round(sum(billed_amount),2),round(sum(payment_received),2),round(sum(paid_to_owner),2) from trips")[0][:])
print('payments received',q("select count(*),round(sum(amount),2),round(sum(allocated_amount),2) from payments where payment_type='received'")[0][:])
print('payments paid',q("select count(*),round(sum(amount),2),round(sum(allocated_amount),2) from payments where payment_type='paid'")[0][:])
print('allocations',q("select count(*),round(sum(amount),2) from payment_allocations")[0][:])
print('payments.allocated_amount != sum(allocations):',q("""select count(*) from payments p where round(coalesce(allocated_amount,0),2)!=round(coalesce((select sum(amount) from payment_allocations where payment_id=p.id),0),2)""")[0][0])
print('payments with NULL party and NULL vendor:',q("select count(*) from payments where party_id is null and vendor_id is null")[0][0])
print('payments with both party and vendor:',q("select count(*) from payments where party_id is not null and vendor_id is not null")[0][0])
print('trips.payment_received != sum(allocations on trip) for trips with payment_received>0:')
r=q("""select count(*) n,round(sum(t.payment_received-coalesce(a.s,0)),2) d from trips t left join (select trip_id,sum(amount) s from payment_allocations group by trip_id) a on a.trip_id=t.id where t.payment_received>0 and round(t.payment_received,2)!=round(coalesce(a.s,0),2)""")[0]
print('  ',tuple(r),'(allocations table only holds payments recorded after the allocation feature; earlier ones live only in trips.payment_received)')
# per party: ledger balance vs trip pending
rows=[]
for p in q("select id,name,opening_balance,linked_party_id from parties") if False else q("select id,name,opening_balance from parties"):
    t=q("select coalesce(sum(billed_amount),0) b,coalesce(sum(payment_received),0) r from trips where party_id=?",p['id'])[0]
    pend=round(t['b']-t['r'],2)
    led=round(pb.get(p['id'],0),2)
    rows.append((p['id'],p['name'],p['opening_balance'] or 0,pend,led,round(led-pend,2)))
diff=[r for r in rows if abs(r[5])>0.01]
print('parties',len(rows),'where ledger balance != sum(trip billed - received):',len(diff))
for r in sorted(diff,key=lambda r:-abs(r[5]))[:8]: print('  ',r)
# explain differences: opening balance + unallocated payments
unexpl=0
for r in diff:
    ob=r[2]
    unalloc=q("select coalesce(sum(amount-coalesce(allocated_amount,0)),0) s from payments where party_id=? and payment_type='received'",r[0])[0]['s']
    ex=round(ob-unalloc,2)
    if abs(ex-r[5])>0.01: unexpl+=1; print('   unexplained',r[:2],'diff',r[5],'opening',ob,'unalloc',unalloc)
print('differences not explained by opening balance minus unallocated payments:',unexpl)
print('sum receivable (positive ledger balances parties):',round(sum(v for v in pb.values() if v>0),2),' vendors payable:',round(-sum(v for v in vb.values() if v<0),2))
# trips amounts rounding
print('billed_amount with more than 2 decimals:',q("select count(*) from trips where abs(billed_amount*100-round(billed_amount*100))>0.0001")[0][0])
print('REAL type used for money columns: yes (schema REAL)')
conn.close()
