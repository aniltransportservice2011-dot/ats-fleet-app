# Balance audit — findings (report only, nothing fixed yet)

Run on disposable DBs; real fleet.db untouched. ~22,200 scenarios, ~1.13 million checks.
Raw per-stage reports with reproducing scenario IDs: `audit_reports/*.md`. Re-run: `python3 test_balance_audit.py --stage party|vendor|edge|maint|expense [--replay ID]`.

## A. Real bugs (wrong number / crash / bad data)
1. End Trip deducts shortage twice (billed 9600 shown as 9200). Ledger, Trips, Trip detail, Payment popup, Dashboard all inherit it. [P9102, 32 scenarios]
2. Mobile Dashboard "Receivable" counts only role=Party; website Dashboard counts every account with a positive balance. They disagree whenever a vendor has a positive balance. [BATCH*]
3. Payment form accepts: 0, negative, NaN, inf, 1e400, 99999999999999999, 0.001 — recorded as payments (web + app). 'abc', ' ', '1,000' -> HTTP 500.
4. Payment can be recorded for a non-existent party id (web + app).
5. Payment can allocate against another party's trip (ticking party A's trip while paying as B).
6. Trip form: negative quantity/rate/huge TDS saves a negative bill; 'abc' in quantity/rate -> 500. End Trip can also produce a negative bill.
7. Opening balance 'abc' -> 500.
8. Maintenance: pay vendor in Ledger, then tick the bill "paid" on Maintenance -> vendor shows overpaid (payment counted twice). [M0028, 30 cases]

## B. Mismatches a user sees (your reported symptom)
9. PAID_IN_FULL_BUT_TRIPS_STILL_PENDING (2,979 cases): payment from the Ledger with no trips ticked lowers the Ledger balance but not the Trips "pending". Same for vendor side (2,111 cases: popup lists amounts owed after ledger says settled).
10. Maintenance Overview (web + app) still shows bill unpaid / pending count after the vendor was fully paid in the Ledger.
11. Website payment errors are swallowed silently (3 cases).

## C. Design gaps (numbers differ by design, but confusing)
12. Trips pending ignores opening balance and unallocated payments (only the Ledger counts them).
13. Vendor popup "owed" ignores company-paid fuel/driver advance and maintenance bills that the ledger nets.
14. Merged party+vendor ledger has no "pay owner" popup.
15. Maintenance Overview counts own vehicles only; bills on hired vehicles are in the vendor ledger but nowhere in Maintenance KPIs.
16. Mobile Maintenance Overview is hard-wired to LAST month; website lets you pick the period.
17. Pending expense bill naming a vendor never reaches that vendor's ledger or Dashboard payables.
18. Mobile Dashboard has no Payable tile.
19. Double-submitting an identical payment is recorded twice. Trip advance may exceed bill or be negative.

## Clean (no wrong numbers found)
Expenses totals/pending/edit/delete/mark-paid (web + app); trip billing recipes (per-MT, fixed, guarantee, charges, paise); Ledger balance vs independent model for ticked payments; maintenance bill create/edit/delete for direct/tyre/battery/service/insurance.

## Not yet covered
Employees/salary advances, invoices, exports, Business Performance period filters, fuel; earlier-noted M-BATTERY-2 and G3 not re-checked.

---
# After the fixes (Oct 10, 2026)
All real bugs above are fixed and re-tested (reports in `audit_reports/after_fixes/`). Extra stages added and run: employees, ledger exports (xlsx/pdf), invoices, Dashboard/Business Performance periods, urea stock, and a garbage-input sweep of all 97 POST routes.
Remaining items are business decisions, not bugs: Dashboard/Business Performance profit definitions for hired vehicles; invoice totals vs Ledger (Others lines, opt-in charges); hired-trip fuel/advance deduction rule (Ledger vs Invoice Center); no "pay owner" popup for a vendor who is also a party; pending expense bills not reaching the vendor ledger (needs a DB change); Maintenance Overview excludes hired vehicles; opening balance and surplus payments are not trips so Trips "pending" can't show them.

---
# Later the same day
- Hired-vehicle profit unified (Dashboard = Business Performance = billed − owner cost).
- Pending expense bills naming a vendor now reach that vendor's ledger; ledger payments settle them and mark them Paid. Database change: `migrate_payment_allocation_overhead.sql` (applied to the local fleet.db; run it on any other database BEFORE deploying the code). New test stage: `--stage xbill` (240 scenarios, all pass).

---
# Access levels
Read Only users can view everything and change nothing (enforced on the server for every POST; hides add/edit/delete buttons; "View-only access" badge). Full Access unchanged. Inactive/deleted users are logged out immediately. Limited Access is stored but not enforced yet. Test stage: `--stage access`.
