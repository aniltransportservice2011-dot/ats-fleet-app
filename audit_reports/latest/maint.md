# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **200**  |  checks evaluated: **2040**  |  disagreements found: **120** in **100** scenarios
- distinct symptoms after grouping identical ones: **1**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `MAINT_HIRED_VEHICLE_BILL_MISSING_FROM_OVERVIEW` | the Maintenance Overview only counts OWN vehicles; the vendor is still owed this | 120 | 100 |

## 1. `MAINT_HIRED_VEHICLE_BILL_MISSING_FROM_OVERVIEW` — DESIGN — the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
120 occurrences in 100 scenarios. Smallest examples:

- `M0073` · step *after tyre add* · expected `7000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
- `M0074` · step *after tyre add* · expected `7000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
- `M0074` · step *after editing the bill amount* · expected `8000.0 of maintenance on a hired vehicle is in the vendor ledger and should be somewhere in Maintenance` · screen/DB showed `Maintenance Overview total cost 0.0` · the Maintenance Overview only counts OWN vehicles; the vendor is still owed this
