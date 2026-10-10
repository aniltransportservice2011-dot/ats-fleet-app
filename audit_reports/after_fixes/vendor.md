# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **3228**  |  checks evaluated: **92992**  |  disagreements found: **6400** in **2481** scenarios
- distinct symptoms after grouping identical ones: **10**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance | 2041 | 762 |
| 2 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | a trip already overpaid | 1176 | 578 |
| 3 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + a trip already overpaid | 1089 | 554 |
| 4 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + maintenance bills are in the ledger# not i | 500 | 177 |
| 5 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | opening balance + payment recorded but not allocated to a tr | 434 | 210 |
| 6 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | maintenance bills are in the ledger# not in the popup | 333 | 116 |
| 7 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | payment recorded but not allocated to a trip | 243 | 118 |
| 8 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | payment recorded but not allocated to a trip + a trip alread | 227 | 108 |
| 9 | DESIGN | `MERGED_LEDGER_HAS_NO_PAY_OWNER_POPUP` | vendor who is also a party: payments to the owner cannot be  | 192 | 128 |
| 10 | DESIGN | `VENDOR_LEDGER_VS_POPUP_GAP` | maintenance bills are in the ledger# not in the popup + a tr | 165 | 57 |

## 1. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance
2041 occurrences in 762 scenarios. Smallest examples:

- `V0050` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance
- `V0050` · step *final* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance
- `V0052` · step *after trips* · expected `popup outstanding 8000.00` · screen/DB showed `ledger payable 3000.00` · opening balance

## 2. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — a trip already overpaid
1176 occurrences in 578 scenarios. Smallest examples:

- `V0529` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2000.00` · a trip already overpaid
- `V0529` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2000.00` · a trip already overpaid
- `V0531` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -2000.00` · a trip already overpaid

## 3. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + a trip already overpaid
1089 occurrences in 554 scenarios. Smallest examples:

- `V0530` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7000.00` · opening balance + a trip already overpaid
- `V0530` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7000.00` · opening balance + a trip already overpaid
- `V0532` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -7000.00` · opening balance + a trip already overpaid

## 4. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + maintenance bills are in the ledger# not i
500 occurrences in 177 scenarios. Smallest examples:

- `V2050` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup
- `V2050` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup
- `V2052` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 7000.00` · opening balance + maintenance bills are in the ledger, not in the popup

## 5. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — opening balance + payment recorded but not allocated to a tr
434 occurrences in 210 scenarios. Smallest examples:

- `V0062` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -6000.00` · opening balance + payment recorded but not allocated to a trip
- `V0062` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -6000.00` · opening balance + payment recorded but not allocated to a trip
- `V0014` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -6000.00` · opening balance + payment recorded but not allocated to a trip

## 6. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — maintenance bills are in the ledger# not in the popup
333 occurrences in 116 scenarios. Smallest examples:

- `V2049` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup
- `V2049` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup
- `V2051` · step *after trips* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable 5000.00` · maintenance bills are in the ledger, not in the popup

## 7. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — payment recorded but not allocated to a trip
243 occurrences in 118 scenarios. Smallest examples:

- `V0061` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -1000.00` · payment recorded but not allocated to a trip
- `V0061` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -1000.00` · payment recorded but not allocated to a trip
- `V0013` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -1000.00` · payment recorded but not allocated to a trip

## 8. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — payment recorded but not allocated to a trip + a trip alread
227 occurrences in 108 scenarios. Smallest examples:

- `V0541` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3000.00` · payment recorded but not allocated to a trip + a trip already overpaid
- `V0541` · step *final* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3000.00` · payment recorded but not allocated to a trip + a trip already overpaid
- `V0557` · step *after vendor payment 0* · expected `popup outstanding 0.00` · screen/DB showed `ledger payable -3000.00` · payment recorded but not allocated to a trip + a trip already overpaid

## 9. `MERGED_LEDGER_HAS_NO_PAY_OWNER_POPUP` — DESIGN — vendor who is also a party: payments to the owner cannot be 
192 occurrences in 128 scenarios. Smallest examples:

- `V2161` · step *after trips* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen
- `V2161` · step *final* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen
- `V2162` · step *after trips* · expected `a way to pay the owner for the hired trips` · screen/DB showed `popup lists no owner payables (owner is owed on 1 trip(s))` · vendor who is also a party: payments to the owner cannot be allocated from the ledger screen

## 10. `VENDOR_LEDGER_VS_POPUP_GAP` — DESIGN — maintenance bills are in the ledger# not in the popup + a tr
165 occurrences in 57 scenarios. Smallest examples:

- `V2331` · step *after trips* · expected `popup outstanding 9000.00` · screen/DB showed `ledger payable 7000.00` · maintenance bills are in the ledger, not in the popup + a trip already overpaid
- `V2331` · step *after vendor payment 0* · expected `popup outstanding 2000.00` · screen/DB showed `ledger payable -0.00` · maintenance bills are in the ledger, not in the popup + a trip already overpaid
- `V2331` · step *after vendor payment 1* · expected `popup outstanding 1700.00` · screen/DB showed `ledger payable -300.00` · maintenance bills are in the ledger, not in the popup + a trip already overpaid
