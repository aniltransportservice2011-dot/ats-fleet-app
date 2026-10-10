# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **3228**  |  checks evaluated: **95213**  |  disagreements found: **10871** in **3152** scenarios
- distinct symptoms after grouping identical ones: **16**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | BUG | `TOTAL_APP_DASHBOARD_PAYABLE` | Mobile app Dashboard: Payable | 135 | 135 |
| 2 | BUG | `TOTAL_APP_DASHBOARD_RECEIVABLE` | Mobile app Dashboard: Receivable | 126 | 126 |
| 3 | BUG | `TOTAL_WEBSITE_VS_APP_DASHBOARD` | website Dashboard receivables vs the mobile app Dashboard re | 126 | 126 |
| 4 | USER-VISIBLE | `VENDOR_PAID_IN_FULL_BUT_TRIP_STILL_OWED` |  | 2111 | 999 |
| 5 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | fuel/driver advance paid by the company is deducted in the l | 3575 | 1228 |
| 6 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + fuel/driver advance paid by the company is | 3362 | 1191 |
| 7 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance | 402 | 219 |
| 8 | DESIGN | `MERGED_LEDGER_HAS_NO_PAY_OWNER_POPUP` | vendor who is also a party: payments to the owner cannot be  | 272 | 128 |
| 9 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + payment recorded but not allocated to a tr | 212 | 94 |
| 10 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | payment recorded but not allocated to a trip | 182 | 81 |
| 11 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | a trip already overpaid | 120 | 64 |
| 12 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + a trip already overpaid | 120 | 64 |
| 13 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | maintenance bills are in the ledger# not in the popup | 44 | 24 |
| 14 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + maintenance bills are in the ledger# not i | 43 | 18 |
| 15 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | maintenance bills are in the ledger# not in the popup + paym | 25 | 9 |
| 16 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | payment recorded but not allocated to a trip + a trip alread | 16 | 8 |

## 1. `TOTAL_APP_DASHBOARD_PAYABLE` — BUG — Mobile app Dashboard: Payable
135 occurrences in 135 scenarios. Smallest examples:

- `BATCH2` · step *48 accounts* · expected `25500.0` · screen/DB showed `None` · Mobile app Dashboard: Payable
  - scenario: `{"accounts": 48}`
- `BATCH0` · step *48 accounts* · expected `24000.0` · screen/DB showed `None` · Mobile app Dashboard: Payable
  - scenario: `{"accounts": 48}`
- `BATCH3` · step *48 accounts* · expected `5000.0` · screen/DB showed `None` · Mobile app Dashboard: Payable
  - scenario: `{"accounts": 48}`

## 2. `TOTAL_APP_DASHBOARD_RECEIVABLE` — BUG — Mobile app Dashboard: Receivable
126 occurrences in 126 scenarios. Smallest examples:

- `BATCH2` · step *48 accounts* · expected `289500.0` · screen/DB showed `240000.0` · Mobile app Dashboard: Receivable
  - scenario: `{"accounts": 48}`
- `BATCH0` · step *48 accounts* · expected `292000.0` · screen/DB showed `240000.0` · Mobile app Dashboard: Receivable
  - scenario: `{"accounts": 48}`
- `BATCH3` · step *48 accounts* · expected `330000.0` · screen/DB showed `240000.0` · Mobile app Dashboard: Receivable
  - scenario: `{"accounts": 48}`

## 3. `TOTAL_WEBSITE_VS_APP_DASHBOARD` — BUG — website Dashboard receivables vs the mobile app Dashboard re
126 occurrences in 126 scenarios. Smallest examples:

- `BATCH2` · step *48 accounts* · expected `289500.0` · screen/DB showed `240000.0` · website Dashboard receivables vs the mobile app Dashboard receivable
  - scenario: `{"accounts": 48}`
- `BATCH0` · step *48 accounts* · expected `292000.0` · screen/DB showed `240000.0` · website Dashboard receivables vs the mobile app Dashboard receivable
  - scenario: `{"accounts": 48}`
- `BATCH3` · step *48 accounts* · expected `330000.0` · screen/DB showed `240000.0` · website Dashboard receivables vs the mobile app Dashboard receivable
  - scenario: `{"accounts": 48}`

## 4. `VENDOR_PAID_IN_FULL_BUT_TRIP_STILL_OWED` — USER-VISIBLE — 
2111 occurrences in 999 scenarios. Smallest examples:

- `V0054` · step *after vendor payment 0* · expected `popup outstanding 0 once we owe nothing` · screen/DB showed `popup still lists 4000.00 owed to the owner while the ledger says we owe -1000.00` · 
- `V0054` · step *final* · expected `popup outstanding 0 once we owe nothing` · screen/DB showed `popup still lists 4000.00 owed to the owner while the ledger says we owe -1000.00` · 
- `V0055` · step *after vendor payment 0* · expected `popup outstanding 0 once we owe nothing` · screen/DB showed `popup still lists 8000.00 owed to the owner while the ledger says we owe -0.00` · 

## 5. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — fuel/driver advance paid by the company is deducted in the l
3575 occurrences in 1228 scenarios. Smallest examples:

- `V0065` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 6500.00` · fuel/driver advance paid by the company is deducted in the ledger, not in the popup
- `V0065` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 6500.00` · fuel/driver advance paid by the company is deducted in the ledger, not in the popup
- `V0067` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 6500.00` · fuel/driver advance paid by the company is deducted in the ledger, not in the popup

## 6. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + fuel/driver advance paid by the company is
3362 occurrences in 1191 scenarios. Smallest examples:

- `V0066` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 1500.00` · opening balance + fuel/driver advance paid by the company is deducted in the ledger, not in the popup
- `V0066` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 1500.00` · opening balance + fuel/driver advance paid by the company is deducted in the ledger, not in the popup
- `V0068` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 1500.00` · opening balance + fuel/driver advance paid by the company is deducted in the ledger, not in the popup

## 7. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance
402 occurrences in 219 scenarios. Smallest examples:

- `V0050` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance
- `V0050` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance
- `V0052` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance

## 8. `MERGED_LEDGER_HAS_NO_PAY_OWNER_POPUP` — DESIGN — vendor who is also a party: payments to the owner cannot be 
272 occurrences in 128 scenarios. Smallest examples:

- `V2137` · step *after trips* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen
- `V2137` · step *final* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen
- `V2138` · step *after trips* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen

## 9. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + payment recorded but not allocated to a tr
212 occurrences in 94 scenarios. Smallest examples:

- `V0056` · step *after vendor payment 0* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -5000.00` · opening balance + payment recorded but not allocated to a trip
- `V0056` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -5000.00` · opening balance + payment recorded but not allocated to a trip
- `V0058` · step *after vendor payment 0* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -0.00` · opening balance + payment recorded but not allocated to a trip

## 10. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — payment recorded but not allocated to a trip
182 occurrences in 81 scenarios. Smallest examples:

- `V0055` · step *after vendor payment 0* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -0.00` · payment recorded but not allocated to a trip
- `V0055` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -0.00` · payment recorded but not allocated to a trip
- `V0057` · step *after vendor payment 0* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable -0.00` · payment recorded but not allocated to a trip

## 11. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — a trip already overpaid
120 occurrences in 64 scenarios. Smallest examples:

- `V0769` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2400.00` · a trip already overpaid
- `V0769` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2400.00` · a trip already overpaid
- `V0771` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2400.00` · a trip already overpaid

## 12. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + a trip already overpaid
120 occurrences in 64 scenarios. Smallest examples:

- `V0770` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7400.00` · opening balance + a trip already overpaid
- `V0770` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7400.00` · opening balance + a trip already overpaid
- `V0772` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7400.00` · opening balance + a trip already overpaid

## 13. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — maintenance bills are in the ledger# not in the popup
44 occurrences in 24 scenarios. Smallest examples:

- `V2049` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup
- `V2049` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup
- `V2051` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup

## 14. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + maintenance bills are in the ledger# not i
43 occurrences in 18 scenarios. Smallest examples:

- `V2050` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup
- `V2050` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup
- `V2052` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup

## 15. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — maintenance bills are in the ledger# not in the popup + paym
25 occurrences in 9 scenarios. Smallest examples:

- `V2444` · step *after vendor payment 0* · expected `popup outstanding 9000.00` · screen/DB showed `ledger payable 9265.50` · maintenance bills are in the ledger, not in the popup + payment recorded but not allocated to a trip
- `V2444` · step *after vendor payment 1* · expected `popup outstanding 9000.00` · screen/DB showed `ledger payable -34.50` · maintenance bills are in the ledger, not in the popup + payment recorded but not allocated to a trip
- `V2444` · step *final* · expected `popup outstanding 9000.00` · screen/DB showed `ledger payable -34.50` · maintenance bills are in the ledger, not in the popup + payment recorded but not allocated to a trip

## 16. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — payment recorded but not allocated to a trip + a trip alread
16 occurrences in 8 scenarios. Smallest examples:

- `V0781` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3400.00` · payment recorded but not allocated to a trip + a trip already overpaid
- `V0781` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3400.00` · payment recorded but not allocated to a trip + a trip already overpaid
- `V0829` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3400.00` · payment recorded but not allocated to a trip + a trip already overpaid
