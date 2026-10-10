# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **40**  |  checks evaluated: **396**  |  disagreements found: **12** in **12** scenarios
- distinct symptoms after grouping identical ones: **1**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `PENDING_EXPENSE_BILL_NOT_IN_VENDOR_LEDGER` | an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor's ledger / the Dashboard payables | 12 | 12 |

## 1. `PENDING_EXPENSE_BILL_NOT_IN_VENDOR_LEDGER` — DESIGN — an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor's ledger / the Dashboard payables
12 occurrences in 12 scenarios. Smallest examples:

- `X0025` · step *a Pending expense bill naming a vendor* · expected `vendor ledger shows -700 payable` · screen/DB showed `vendor ledger shows 0.0` · an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor's ledger / the Dashboard payables
- `X0026` · step *a Pending expense bill naming a vendor* · expected `vendor ledger shows -700 payable` · screen/DB showed `vendor ledger shows 0.0` · an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor's ledger / the Dashboard payables
- `X0027` · step *a Pending expense bill naming a vendor* · expected `vendor ledger shows -700 payable` · screen/DB showed `vendor ledger shows 0.0` · an expense recorded as Pending for a vendor is money owed, but it never reaches that vendor's ledger / the Dashboard payables
