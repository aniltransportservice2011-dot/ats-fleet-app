# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **60**  |  checks evaluated: **2006**  |  disagreements found: **38** in **22** scenarios
- distinct symptoms after grouping identical ones: **1**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `EMP_REPAID_MORE_THAN_GIVEN` |  | 38 | 22 |

## 1. `EMP_REPAID_MORE_THAN_GIVEN` — DESIGN — 
38 occurrences in 22 scenarios. Smallest examples:

- `N0005` · step *after setup* · expected `repayments never exceed advances given` · screen/DB showed `advance balance is -1500.0 (employee has "repaid" more than was ever given)` · 
- `N0006` · step *after setup* · expected `repayments never exceed advances given` · screen/DB showed `advance balance is -1500.0 (employee has "repaid" more than was ever given)` · 
- `N0011` · step *after setup* · expected `repayments never exceed advances given` · screen/DB showed `advance balance is -1500.0 (employee has "repaid" more than was ever given)` · 
