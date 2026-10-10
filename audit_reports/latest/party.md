# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **18604**  |  checks evaluated: **918301**  |  disagreements found: **15475** in **8816** scenarios
- distinct symptoms after grouping identical ones: **3**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `LEDGER_VS_TRIPS_GAP` | opening balance (only the Ledger counts it) | 13397 | 8050 |
| 2 | DESIGN | `LEDGER_VS_TRIPS_GAP` | opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it) | 1046 | 984 |
| 3 | DESIGN | `LEDGER_VS_TRIPS_GAP` | payment recorded but not allocated to a trip (only the Ledger counts it) | 1032 | 766 |

## 1. `LEDGER_VS_TRIPS_GAP` — DESIGN — opening balance (only the Ledger counts it)
13397 occurrences in 8050 scenarios. Smallest examples:

- `P0146` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 3000.0` · opening balance (only the Ledger counts it)
- `P0147` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -5000.0` · opening balance (only the Ledger counts it)
- `P0149` · step *after trips* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 3000.0` · opening balance (only the Ledger counts it)

## 2. `LEDGER_VS_TRIPS_GAP` — DESIGN — opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
1046 occurrences in 984 scenarios. Smallest examples:

- `P0161` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows 2000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0162` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -6000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0125` · step *after payment 0* · expected `trips total pending 0.00` · screen/DB showed `ledger shows 4000.0` · opening balance (only the Ledger counts it) + payment recorded but not allocated to a trip (only the Ledger counts it)

## 3. `LEDGER_VS_TRIPS_GAP` — DESIGN — payment recorded but not allocated to a trip (only the Ledger counts it)
1032 occurrences in 766 scenarios. Smallest examples:

- `P0160` · step *after payment 0* · expected `trips total pending -2000.00` · screen/DB showed `ledger shows -3000.0` · payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0124` · step *after payment 0* · expected `trips total pending 0.00` · screen/DB showed `ledger shows -1000.0` · payment recorded but not allocated to a trip (only the Ledger counts it)
- `P0016` · step *after payment 0* · expected `trips total pending 0.00` · screen/DB showed `ledger shows -1000.0` · payment recorded but not allocated to a trip (only the Ledger counts it)
