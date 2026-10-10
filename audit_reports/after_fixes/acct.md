# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **9**  |  checks evaluated: **504**  |  disagreements found: **68** in **9** scenarios
- distinct symptoms after grouping identical ones: **4**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `ACCT_DASHBOARD_PROFIT_MISSES_HIRED_OWNER_COST` | Dashboard 'Total Profit' leaves out what the owner of a hired vehicle is paid (7,000 on the hired trip) | 36 | 9 |
| 2 | DESIGN | `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` | Business Performance counts the owner's 7,000 AND the fuel/advance (800) the company paid on that hired trip, which is already inside the 7,000 | 16 | 4 |
| 3 | DESIGN | `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` | Business Performance counts the owner's 7,000 AND the fuel/advance (300) the company paid on that hired trip, which is already inside the 7,000 | 8 | 2 |
| 4 | DESIGN | `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` | Business Performance counts the owner's 7,000 AND the fuel/advance (500) the company paid on that hired trip, which is already inside the 7,000 | 8 | 2 |

## 1. `ACCT_DASHBOARD_PROFIT_MISSES_HIRED_OWNER_COST` — DESIGN — Dashboard 'Total Profit' leaves out what the owner of a hired vehicle is paid (7,000 on the hired trip)
36 occurrences in 9 scenarios. Smallest examples:

- `A0001` · step *period whole (2026-05-01 → 2026-06-30)* · expected `900.0` · screen/DB showed `7900.0` · Dashboard 'Total Profit' leaves out what the owner of a hired vehicle is paid (7,000 on the hired trip)
- `A0001` · step *period june (2026-06-01 → 2026-06-30)* · expected `2200.0` · screen/DB showed `9200.0` · Dashboard 'Total Profit' leaves out what the owner of a hired vehicle is paid (7,000 on the hired trip)
- `A0001` · step *period last_day_only (2026-06-30 → 2026-06-30)* · expected `3000.0` · screen/DB showed `10000.0` · Dashboard 'Total Profit' leaves out what the owner of a hired vehicle is paid (7,000 on the hired trip)

## 2. `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` — DESIGN — Business Performance counts the owner's 7,000 AND the fuel/advance (800) the company paid on that hired trip, which is already inside the 7,000
16 occurrences in 4 scenarios. Smallest examples:

- `A0005` · step *period whole (2026-05-01 → 2026-06-30)* · expected `900.0` · screen/DB showed `100.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (800) the company paid on that hired trip, which is already inside the 7,000
- `A0005` · step *period june (2026-06-01 → 2026-06-30)* · expected `2200.0` · screen/DB showed `1400.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (800) the company paid on that hired trip, which is already inside the 7,000
- `A0005` · step *period last_day_only (2026-06-30 → 2026-06-30)* · expected `3000.0` · screen/DB showed `2200.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (800) the company paid on that hired trip, which is already inside the 7,000

## 3. `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` — DESIGN — Business Performance counts the owner's 7,000 AND the fuel/advance (300) the company paid on that hired trip, which is already inside the 7,000
8 occurrences in 2 scenarios. Smallest examples:

- `A0002` · step *period whole (2026-05-01 → 2026-06-30)* · expected `900.0` · screen/DB showed `600.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (300) the company paid on that hired trip, which is already inside the 7,000
- `A0002` · step *period june (2026-06-01 → 2026-06-30)* · expected `2200.0` · screen/DB showed `1900.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (300) the company paid on that hired trip, which is already inside the 7,000
- `A0002` · step *period last_day_only (2026-06-30 → 2026-06-30)* · expected `3000.0` · screen/DB showed `2700.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (300) the company paid on that hired trip, which is already inside the 7,000

## 4. `ACCT_BP_PROFIT_DOUBLE_COUNTS_HIRED_FUEL` — DESIGN — Business Performance counts the owner's 7,000 AND the fuel/advance (500) the company paid on that hired trip, which is already inside the 7,000
8 occurrences in 2 scenarios. Smallest examples:

- `A0004` · step *period whole (2026-05-01 → 2026-06-30)* · expected `900.0` · screen/DB showed `400.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (500) the company paid on that hired trip, which is already inside the 7,000
- `A0004` · step *period june (2026-06-01 → 2026-06-30)* · expected `2200.0` · screen/DB showed `1700.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (500) the company paid on that hired trip, which is already inside the 7,000
- `A0004` · step *period last_day_only (2026-06-30 → 2026-06-30)* · expected `3000.0` · screen/DB showed `2500.0` · Business Performance counts the owner's 7,000 AND the fuel/advance (500) the company paid on that hired trip, which is already inside the 7,000
