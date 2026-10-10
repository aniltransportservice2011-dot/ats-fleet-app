# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **200**  |  checks evaluated: **2040**  |  disagreements found: **180** in **130** scenarios
- distinct symptoms after grouping identical ones: **4**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | BUG | `MAINT_PAID_TWICE_AFTER_PAYMENT_AND_MARK_PAID` | paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid) | 30 | 30 |
| 2 | USER-VISIBLE | `MAINT_UNPAID_AFTER_VENDOR_PAYMENT` |  | 15 | 15 |
| 3 | USER-VISIBLE | `MAINT_APP_UNPAID_AFTER_VENDOR_PAYMENT` |  | 15 | 15 |
| 4 | DESIGN | `MAINT_HIRED_VEHICLE_BILL_MISSING_FROM_OVERVIEW` | the Maintenance Overview only counts OWN vehicles; the vendor is still owed this | 120 | 100 |

## 1. `MAINT_PAID_TWICE_AFTER_PAYMENT_AND_MARK_PAID` — BUG — paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid)
30 occurrences in 30 scenarios. Smallest examples:

- `M0003` · step *ledger payment, then the bill marked paid on Maintenance* · expected `0` · screen/DB showed `7000.0` · paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid)
- `M0008` · step *ledger payment, then the bill marked paid on Maintenance* · expected `0` · screen/DB showed `6000.0` · paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid)
- `M0018` · step *ledger payment, then the bill marked paid on Maintenance* · expected `0` · screen/DB showed `6000.0` · paying in the Ledger AND ticking the bill paid on Maintenance double-counts the payment (vendor shows overpaid)

## 2. `MAINT_UNPAID_AFTER_VENDOR_PAYMENT` — USER-VISIBLE — 
15 occurrences in 15 scenarios. Smallest examples:

- `M0002` · step *after paying the vendor in the Ledger* · expected `Maintenance shows the bill as paid (unpaid 0, no pending payment)` · screen/DB showed `Maintenance Overview still shows unpaid 7000.0 and 1.0 pending payment(s) while the Ledger shows the vendor settled` · 
- `M0007` · step *after paying the vendor in the Ledger* · expected `Maintenance shows the bill as paid (unpaid 0, no pending payment)` · screen/DB showed `Maintenance Overview still shows unpaid 6000.0 and 1.0 pending payment(s) while the Ledger shows the vendor settled` · 
- `M0017` · step *after paying the vendor in the Ledger* · expected `Maintenance shows the bill as paid (unpaid 0, no pending payment)` · screen/DB showed `Maintenance Overview still shows unpaid 6000.0 and 1.0 pending payment(s) while the Ledger shows the vendor settled` · 

## 3. `MAINT_APP_UNPAID_AFTER_VENDOR_PAYMENT` — USER-VISIBLE — 
15 occurrences in 15 scenarios. Smallest examples:

- `M0002` · step *after paying the vendor in the Ledger* · expected `0` · screen/DB showed `7000.0` · 
- `M0007` · step *after paying the vendor in the Ledger* · expected `0` · screen/DB showed `6000.0` · 
- `M0017` · step *after paying the vendor in the Ledger* · expected `0` · screen/DB showed `6000.0` · 

## 4. `MAINT_HIRED_VEHICLE_BILL_MISSING_FROM_OVERVIEW` — DESIGN — the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
120 occurrences in 100 scenarios. Smallest examples:

- `M0021` · step *after direct add* · expected `7000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
- `M0022` · step *after direct add* · expected `7000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
- `M0023` · step *after direct add* · expected `7000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
