# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **83**  |  checks evaluated: **154**  |  disagreements found: **44** in **44** scenarios
- distinct symptoms after grouping identical ones: **40**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | BUG | `PAYMENT_ALLOCATED_TO_ANOTHER_PARTYS_TRIP` | party B pays, ticking party A's trip | 2 | 2 |
| 2 | BUG | `TRIP_SAVED_WITH_NEGATIVE_BILLED` | trip form {'tds': '999999'} | 1 | 1 |
| 3 | BUG | `END_TRIP_NEGATIVE_BILL` |  | 1 | 1 |
| 4 | BUG | `PAYMENT_FOR_NONEXISTENT_PARTY_RECORDED` | payment to party id 999999 via web | 1 | 1 |
| 5 | BUG | `PAYMENT_FOR_NONEXISTENT_PARTY_RECORDED` | payment to party id 999999 via app | 1 | 1 |
| 6 | BUG | `OPENING_BALANCE_SERVER_ERROR` | opening balance 'abc' | 1 | 1 |
| 7 | BUG | `TRIP_FORM_SERVER_ERROR` | trip form {'quantity': 'abc'} | 1 | 1 |
| 8 | BUG | `TRIP_FORM_SERVER_ERROR` | trip form {'rate': 'abc'} | 1 | 1 |
| 9 | BUG | `TRIP_SAVED_WITH_NEGATIVE_BILLED` | trip form {'quantity': '-10'} | 1 | 1 |
| 10 | BUG | `TRIP_SAVED_WITH_NEGATIVE_BILLED` | trip form {'rate': '-1000'} | 1 | 1 |
| 11 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount ' ' via web | 1 | 1 |
| 12 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount ' ' via app | 1 | 1 |
| 13 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount 'nan' via web | 1 | 1 |
| 14 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount 'nan' via app | 1 | 1 |
| 15 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount 'inf' via web | 1 | 1 |
| 16 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount 'inf' via app | 1 | 1 |
| 17 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-inf' via web | 1 | 1 |
| 18 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-inf' via app | 1 | 1 |
| 19 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '1e400' via web | 1 | 1 |
| 20 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '1e400' via app | 1 | 1 |
| 21 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '99999999999999999' via web | 1 | 1 |
| 22 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '99999999999999999' via app | 1 | 1 |
| 23 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0.001' via web | 1 | 1 |
| 24 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0.001' via app | 1 | 1 |
| 25 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0' via web | 1 | 1 |
| 26 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0' via app | 1 | 1 |
| 27 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0.00' via web | 1 | 1 |
| 28 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '0.00' via app | 1 | 1 |
| 29 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-100' via web | 1 | 1 |
| 30 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-100' via app | 1 | 1 |
| 31 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-0.01' via web | 1 | 1 |
| 32 | BUG | `PAYMENT_BAD_AMOUNT_ACCEPTED` | amount '-0.01' via app | 1 | 1 |
| 33 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount 'abc' via web | 1 | 1 |
| 34 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount 'abc' via app | 1 | 1 |
| 35 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount '1,000' via web | 1 | 1 |
| 36 | BUG | `PAYMENT_AMOUNT_SERVER_ERROR` | amount '1,000' via app | 1 | 1 |
| 37 | USER-VISIBLE | `WEBSITE_PAYMENT_REJECTED_SILENTLY` |  | 3 | 3 |
| 38 | DESIGN | `PAYMENT_DOUBLE_SUBMIT_RECORDED_TWICE` | the identical payment (same date, amount, reference) submitted twice | 2 | 2 |
| 39 | DESIGN | `TRIP_SAVED_WITH_ADVANCE_ABOVE_BILL_OR_NEGATIVE` | party advance -500.0 | 1 | 1 |
| 40 | DESIGN | `TRIP_SAVED_WITH_ADVANCE_ABOVE_BILL_OR_NEGATIVE` | party advance 99999.0 | 1 | 1 |

## 1. `PAYMENT_ALLOCATED_TO_ANOTHER_PARTYS_TRIP` — BUG — party B pays, ticking party A's trip
2 occurrences in 2 scenarios. Smallest examples:

- `E0047` · step *party B pays, ticking party A's trip* · expected `party A's trip untouched` · screen/DB showed `party A's trip payment_received 0.0 -> 4000.0` · 
- `E0048` · step *party B pays, ticking party A's trip* · expected `party A's trip untouched` · screen/DB showed `party A's trip payment_received 0.0 -> 4000.0` · 

## 2. `TRIP_SAVED_WITH_NEGATIVE_BILLED` — BUG — trip form {'tds': '999999'}
1 occurrences in 1 scenarios. Smallest examples:

- `E0074` · step *trip form {'tds': '999999'}* · expected `billed >= 0 or rejected` · screen/DB showed `billed -989999.0` · 

## 3. `END_TRIP_NEGATIVE_BILL` — BUG — 
1 occurrences in 1 scenarios. Smallest examples:

- `E0081` · step *shortage larger than the whole freight* · expected `billed >= 0 or rejected` · screen/DB showed `billed -5000.0` · 

## 4. `PAYMENT_FOR_NONEXISTENT_PARTY_RECORDED` — BUG — payment to party id 999999 via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0053` · step *payment to party id 999999 via web* · expected `no row` · screen/DB showed `1 orphan payment row(s) created (HTTP 302)` · 

## 5. `PAYMENT_FOR_NONEXISTENT_PARTY_RECORDED` — BUG — payment to party id 999999 via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0054` · step *payment to party id 999999 via app* · expected `no row` · screen/DB showed `1 orphan payment row(s) created (HTTP 200)` · 

## 6. `OPENING_BALANCE_SERVER_ERROR` — BUG — opening balance 'abc'
1 occurrences in 1 scenarios. Smallest examples:

- `E0057` · step *opening balance 'abc'* · expected `handled` · screen/DB showed `HTTP 500` · 

## 7. `TRIP_FORM_SERVER_ERROR` — BUG — trip form {'quantity': 'abc'}
1 occurrences in 1 scenarios. Smallest examples:

- `E0063` · step *trip form {'quantity': 'abc'}* · expected `handled` · screen/DB showed `HTTP 500` · 

## 8. `TRIP_FORM_SERVER_ERROR` — BUG — trip form {'rate': 'abc'}
1 occurrences in 1 scenarios. Smallest examples:

- `E0064` · step *trip form {'rate': 'abc'}* · expected `handled` · screen/DB showed `HTTP 500` · 

## 9. `TRIP_SAVED_WITH_NEGATIVE_BILLED` — BUG — trip form {'quantity': '-10'}
1 occurrences in 1 scenarios. Smallest examples:

- `E0065` · step *trip form {'quantity': '-10'}* · expected `billed >= 0 or rejected` · screen/DB showed `billed -10000.0` · 

## 10. `TRIP_SAVED_WITH_NEGATIVE_BILLED` — BUG — trip form {'rate': '-1000'}
1 occurrences in 1 scenarios. Smallest examples:

- `E0066` · step *trip form {'rate': '-1000'}* · expected `billed >= 0 or rejected` · screen/DB showed `billed -10000.0` · 

## 11. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount ' ' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0027` · step *amount ' ' via web* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as ' '

## 12. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount ' ' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0028` · step *amount ' ' via app* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as ' '

## 13. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount 'nan' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0029` · step *amount 'nan' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as 'nan'

## 14. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount 'nan' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0030` · step *amount 'nan' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as 'nan'

## 15. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount 'inf' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0031` · step *amount 'inf' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as 'inf'

## 16. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount 'inf' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0032` · step *amount 'inf' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as 'inf'

## 17. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-inf' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0033` · step *amount '-inf' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-inf'

## 18. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-inf' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0034` · step *amount '-inf' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-inf'

## 19. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '1e400' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0035` · step *amount '1e400' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as '1e400'

## 20. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '1e400' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0036` · step *amount '1e400' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as '1e400'

## 21. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '99999999999999999' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0037` · step *amount '99999999999999999' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as '99999999999999999'

## 22. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '99999999999999999' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0038` · step *amount '99999999999999999' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 10000.0` · amount typed as '99999999999999999'

## 23. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0.001' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0039` · step *amount '0.001' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0.001'

## 24. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0.001' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0040` · step *amount '0.001' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0.001'

## 25. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0013` · step *amount '0' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0'

## 26. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0014` · step *amount '0' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0'

## 27. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0.00' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0015` · step *amount '0.00' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0.00'

## 28. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '0.00' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0016` · step *amount '0.00' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '0.00'

## 29. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-100' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0017` · step *amount '-100' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-100'

## 30. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-100' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0018` · step *amount '-100' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-100'

## 31. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-0.01' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0019` · step *amount '-0.01' via web* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-0.01'

## 32. `PAYMENT_BAD_AMOUNT_ACCEPTED` — BUG — amount '-0.01' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0020` · step *amount '-0.01' via app* · expected `rejected (no payment recorded)` · screen/DB showed `1 payment row(s) created, trip payment_received 0.0 -> 0.0` · amount typed as '-0.01'

## 33. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount 'abc' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0021` · step *amount 'abc' via web* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as 'abc'

## 34. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount 'abc' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0022` · step *amount 'abc' via app* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as 'abc'

## 35. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount '1,000' via web
1 occurrences in 1 scenarios. Smallest examples:

- `E0023` · step *amount '1,000' via web* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as '1,000'

## 36. `PAYMENT_AMOUNT_SERVER_ERROR` — BUG — amount '1,000' via app
1 occurrences in 1 scenarios. Smallest examples:

- `E0024` · step *amount '1,000' via app* · expected `a handled response` · screen/DB showed `HTTP 500` · amount typed as '1,000'

## 37. `WEBSITE_PAYMENT_REJECTED_SILENTLY` — USER-VISIBLE — 
3 occurrences in 3 scenarios. Smallest examples:

- `E0041` · step *no date via web* · expected `the user is told why nothing was saved` · screen/DB showed `redirected to '/ledger/party/1' with no message — looks like it saved` · 
- `E0043` · step *no amount via web* · expected `the user is told why nothing was saved` · screen/DB showed `redirected to '/ledger/party/1' with no message — looks like it saved` · 
- `E0045` · step *no mode via web* · expected `the user is told why nothing was saved` · screen/DB showed `redirected to '/ledger/party/1' with no message — looks like it saved` · 

## 38. `PAYMENT_DOUBLE_SUBMIT_RECORDED_TWICE` — DESIGN — the identical payment (same date, amount, reference) submitted twice
2 occurrences in 2 scenarios. Smallest examples:

- `E0055` · step *the identical payment (same date, amount, reference) submitted twice* · expected `recorded once (duplicate)` · screen/DB showed `recorded 2 times` · 
- `E0056` · step *the identical payment (same date, amount, reference) submitted twice* · expected `recorded once (duplicate)` · screen/DB showed `recorded 2 times` · 

## 39. `TRIP_SAVED_WITH_ADVANCE_ABOVE_BILL_OR_NEGATIVE` — DESIGN — party advance -500.0
1 occurrences in 1 scenarios. Smallest examples:

- `E0067` · step *party advance -500.0* · expected `advance between 0 and the bill, or rejected` · screen/DB showed `advance -500.0 on a bill of 10000.0` · 

## 40. `TRIP_SAVED_WITH_ADVANCE_ABOVE_BILL_OR_NEGATIVE` — DESIGN — party advance 99999.0
1 occurrences in 1 scenarios. Smallest examples:

- `E0068` · step *party advance 99999.0* · expected `advance between 0 and the bill, or rejected` · screen/DB showed `advance 99999.0 on a bill of 10000.0` · 
