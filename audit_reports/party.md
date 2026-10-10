# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **18604**  |  checks evaluated: **1032095**  |  disagreements found: **24804** in **12604** scenarios
- distinct symptoms after grouping identical ones: **17**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | BUG | `LEDGER_NET_BALANCE` | opening # + billed # advances # typed#received # payments | 34 | 34 |
| 2 | BUG | `LEDGER_RECEIVABLE_PAYABLE_SPLIT` |  | 34 | 34 |
| 3 | BUG | `TRIPS_ROW_BILLED` | P#T# | 32 | 32 |
| 4 | BUG | `TRIPS_ROW_PENDING` | P#T# | 32 | 32 |
| 5 | BUG | `TRIPS_KPI_TOTAL_BILLED` |  | 32 | 32 |
| 6 | BUG | `TRIP_VIEW_BALANCE_DUE` | P#T# | 32 | 32 |
| 7 | BUG | `DB_TRIP_BILLED` | P#T# | 32 | 32 |
| 8 | BUG | `ACCOUNTS_LIST_BALANCE` | Ledger list row | 32 | 32 |
| 9 | BUG | `ACCOUNTS_LIST_RECV_PAY` |  | 32 | 32 |
| 10 | BUG | `APP_LEDGER_CLOSING` | mobile app party ledger closing balance | 32 | 32 |
| 11 | BUG | `APP_TRIP_BALANCE_DUE` | mobile app trip detail P#T# | 32 | 32 |
| 12 | BUG | `PAYMENT_MODAL_OUTSTANDING` | "Outstanding Amount" in the Add Payment popup | 11 | 11 |
| 13 | BUG | `PAYMENT_MODAL_PENDING_TRIPS` | trips offered for allocation in the Add Payment popup | 10 | 10 |
| 14 | USER-VISIBLE | `PAID_IN_FULL_BUT_TRIPS_STILL_PENDING` |  | 2979 | 2695 |
| 15 | DESIGN | `LEDGER_VS_TRIPS_GAP` | opening balance (only the Ledger counts it) | 12946 | 9563 |
| 16 | DESIGN | `LEDGER_VS_TRIPS_GAP` | payment recorded but not allocated to a trip (only the Ledger counts it) | 4500 | 3021 |
| 17 | DESIGN | `LEDGER_VS_TRIPS_GAP` | opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it) | 4002 | 3167 |

## 1. `LEDGER_NET_BALANCE` — BUG — opening # + billed # advances # typed#received # payments
34 occurrences in 34 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · opening 0.0 + billed - advances - typed-received - payments
- `P9110` · step *after end_plain* · expected `0.0` · screen/DB showed `-400.0` · opening 0.0 + billed - advances - typed-received - payments
- `P9118` · step *after end_plain* · expected `4800.0` · screen/DB showed `4400.0` · opening 0.0 + billed - advances - typed-received - payments

## 2. `LEDGER_RECEIVABLE_PAYABLE_SPLIT` — BUG — 
34 occurrences in 34 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `recv 9600.0 / pay 0` · screen/DB showed `recv 9200.0 / pay 0.0` · 
- `P9110` · step *after end_plain* · expected `recv 0.0 / pay -0.0` · screen/DB showed `recv 0.0 / pay 400.0` · 
- `P9118` · step *after end_plain* · expected `recv 4800.0 / pay 0` · screen/DB showed `recv 4400.0 / pay 0.0` · 

## 3. `TRIPS_ROW_BILLED` — BUG — P#T#
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9102-T0
- `P9110` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9110-T0
- `P9118` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9118-T0

## 4. `TRIPS_ROW_PENDING` — BUG — P#T#
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9102-T0
- `P9110` · step *after end_plain* · expected `0.0` · screen/DB showed `-400.0` · P9110-T0
- `P9118` · step *after end_plain* · expected `4800.0` · screen/DB showed `4400.0` · P9118-T0

## 5. `TRIPS_KPI_TOTAL_BILLED` — BUG — 
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · 
- `P9110` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · 
- `P9118` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · 

## 6. `TRIP_VIEW_BALANCE_DUE` — BUG — P#T#
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `[9200.0, 9200.0]` · P9102-T0
- `P9110` · step *after end_plain* · expected `0.0` · screen/DB showed `[-400.0, -400.0]` · P9110-T0
- `P9118` · step *after end_plain* · expected `4800.0` · screen/DB showed `[4400.0, 4400.0]` · P9118-T0

## 7. `DB_TRIP_BILLED` — BUG — P#T#
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9102-T0
- `P9110` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9110-T0
- `P9118` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · P9118-T0

## 8. `ACCOUNTS_LIST_BALANCE` — BUG — Ledger list row
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *final* · expected `9600.0` · screen/DB showed `9200.0` · Ledger list row
- `P9110` · step *final* · expected `0.0` · screen/DB showed `-400.0` · Ledger list row
- `P9118` · step *final* · expected `4800.0` · screen/DB showed `4400.0` · Ledger list row

## 9. `ACCOUNTS_LIST_RECV_PAY` — BUG — 
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *final* · expected `recv 9600.0 / pay 0` · screen/DB showed `recv 9200.0 / pay 0.0` · 
- `P9110` · step *final* · expected `recv 0.0 / pay -0.0` · screen/DB showed `recv 0.0 / pay 400.0` · 
- `P9118` · step *final* · expected `recv 4800.0 / pay 0` · screen/DB showed `recv 4400.0 / pay 0.0` · 

## 10. `APP_LEDGER_CLOSING` — BUG — mobile app party ledger closing balance
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *final* · expected `9600.0` · screen/DB showed `9200.0` · mobile app party ledger closing balance
- `P9110` · step *final* · expected `0.0` · screen/DB showed `-400.0` · mobile app party ledger closing balance
- `P9118` · step *final* · expected `4800.0` · screen/DB showed `4400.0` · mobile app party ledger closing balance

## 11. `APP_TRIP_BALANCE_DUE` — BUG — mobile app trip detail P#T#
32 occurrences in 32 scenarios. Smallest examples:

- `P9102` · step *final* · expected `9600.0` · screen/DB showed `[9200.0, 9200.0]` · mobile app trip detail P9102-T0
- `P9110` · step *final* · expected `0.0` · screen/DB showed `[-400.0, -400.0]` · mobile app trip detail P9110-T0
- `P9118` · step *final* · expected `4800.0` · screen/DB showed `[4400.0, 4400.0]` · mobile app trip detail P9118-T0

## 12. `PAYMENT_MODAL_OUTSTANDING` — BUG — "Outstanding Amount" in the Add Payment popup
11 occurrences in 11 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · "Outstanding Amount" in the Add Payment popup
- `P9118` · step *after end_plain* · expected `4800.0` · screen/DB showed `4400.0` · "Outstanding Amount" in the Add Payment popup
- `P9126` · step *after end_plain* · expected `9600.0` · screen/DB showed `9200.0` · "Outstanding Amount" in the Add Payment popup

## 13. `PAYMENT_MODAL_PENDING_TRIPS` — BUG — trips offered for allocation in the Add Payment popup
10 occurrences in 10 scenarios. Smallest examples:

- `P9102` · step *after end_plain* · expected `{6: 9600.0}` · screen/DB showed `{6: 9200.0}` · trips offered for allocation in the Add Payment popup
- `P9118` · step *after end_plain* · expected `{22: 4800.0}` · screen/DB showed `{22: 4400.0}` · trips offered for allocation in the Add Payment popup
- `P9126` · step *after end_plain* · expected `{6: 9600.0}` · screen/DB showed `{6: 9200.0}` · trips offered for allocation in the Add Payment popup

## 14. `PAID_IN_FULL_BUT_TRIPS_STILL_PENDING` — USER-VISIBLE — 
2979 occurrences in 2695 scenarios. Smallest examples:

- `P0097` · step *after payment 1* · expected `Trips pending 0 once the party owes nothing` · screen/DB showed `Trips list still shows 2500.00 pending while the Ledger shows 0.0` · 
- `P0098` · step *after payment 1* · expected `Trips pending 0 once the party owes nothing` · screen/DB showed `Trips list still shows 2500.00 pending while the Ledger shows 5000.0` · 
- `P0099` · step *after payment 1* · expected `Trips pending 0 once the party owes nothing` · screen/DB showed `Trips list still shows 2500.00 pending while the Ledger shows -3000.0` · 

## 15. `LEDGER_VS_TRIPS_GAP` — DESIGN — opening balance (only the Ledger counts it)
12946 occurrences in 9563 scenarios. Smallest examples:

- `P0146` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 3000.0` · opening balance (only the Ledger counts it)
- `P0147` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -5000.0` · opening balance (only the Ledger counts it)
- `P0149` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 3000.0` · opening balance (only the Ledger counts it)

## 16. `LEDGER_VS_TRIPS_GAP` — DESIGN — payment recorded but not allocated to a trip (only the Ledger counts it)
4500 occurrences in 3021 scenarios. Smallest examples:

- `P0160` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -3000.0` · payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0124` · step *after payment 0* · expected `trips total pending 0.00` · screen/DB showed `ledger shows -1000.0` · payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0097` · step *after payment 1* · expected `trips total pending 2500.00` · screen/DB showed `ledger shows 0.0` · payment recorded but not allocated to a trip (only the Ledger counts it)

## 17. `LEDGER_VS_TRIPS_GAP` — DESIGN — opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
4002 occurrences in 3167 scenarios. Smallest examples:

- `P0161` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 2000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0162` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -6000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0125` · step *after payment 0* · expected `trips total pending 0.00` · screen/DB showed `ledger shows 4000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
