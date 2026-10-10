# Balance consistency audit — findings (report only, nothing was changed)

- scenarios run: **24**  |  checks evaluated: **162**  |  disagreements found: **38** in **20** scenarios
- distinct symptoms after grouping identical ones: **21**

| # | severity | check | symptom | occurrences | scenarios |
|---|---|---|---|---:|---:|
| 1 | DESIGN | `INVOICE_TOTAL_DIFFERS_FROM_LEDGER_BECAUSE_OF_OTHERS_LINES` | Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them | 18 | 18 |
| 2 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | permt billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 3 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | permt billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 4 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | permt billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 5 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | fixed billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 6 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | fixed billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 7 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | fixed billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 8 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | guarantee billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 9 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | guarantee billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 10 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | guarantee billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 11 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | charged billing, others=[]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 12 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | charged billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 13 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | charged billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 14 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | charged billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 15 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | paise billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 16 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | paise billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 17 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | paise billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 18 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | shortage billing, others=[]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 19 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | shortage billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 20 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | shortage billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |
| 21 | DESIGN | `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` | shortage billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes | 1 | 1 |

## 1. `INVOICE_TOTAL_DIFFERS_FROM_LEDGER_BECAUSE_OF_OTHERS_LINES` — DESIGN — Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them
18 occurrences in 18 scenarios. Smallest examples:

- `I0002` · step *trip invoice page vs Ledger* · expected `invoice total = amount owed in the Ledger (10000.0)` · screen/DB showed `invoice asks the customer for 11500.0 but the Ledger / Trips / Dashboard say 10000.0` · Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them
- `I0003` · step *trip invoice page vs Ledger* · expected `invoice total = amount owed in the Ledger (10000.0)` · screen/DB showed `invoice asks the customer for 9800.0 but the Ledger / Trips / Dashboard say 10000.0` · Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them
- `I0004` · step *trip invoice page vs Ledger* · expected `invoice total = amount owed in the Ledger (10000.0)` · screen/DB showed `invoice asks the customer for 11300.0 but the Ledger / Trips / Dashboard say 10000.0` · Others lines on the trip are printed on the invoice but never reach trips.billed_amount, so Ledger, Trips, Dashboard and Business Performance ignore them

## 2. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — permt billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0002` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10000.0)` · screen/DB showed `PDF grand total 11500.0` · permt billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 3. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — permt billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0003` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10000.0)` · screen/DB showed `PDF grand total 9800.0` · permt billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 4. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — permt billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0004` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10000.0)` · screen/DB showed `PDF grand total 11300.0` · permt billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 5. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — fixed billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0006` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (25000.0)` · screen/DB showed `PDF grand total 26500.0` · fixed billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 6. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — fixed billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0007` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (25000.0)` · screen/DB showed `PDF grand total 24800.0` · fixed billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 7. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — fixed billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0008` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (25000.0)` · screen/DB showed `PDF grand total 26300.0` · fixed billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 8. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — guarantee billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0010` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (6000.0)` · screen/DB showed `PDF grand total 7500.0` · guarantee billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 9. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — guarantee billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0011` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (6000.0)` · screen/DB showed `PDF grand total 5800.0` · guarantee billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 10. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — guarantee billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0012` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (6000.0)` · screen/DB showed `PDF grand total 7300.0` · guarantee billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 11. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — charged billing, others=[]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0013` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10500.0)` · screen/DB showed `PDF grand total 10300.0` · charged billing, others=[]: the batch invoice total differs from what the Ledger says the party owes

## 12. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — charged billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0014` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10500.0)` · screen/DB showed `PDF grand total 11800.0` · charged billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 13. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — charged billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0015` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10500.0)` · screen/DB showed `PDF grand total 10100.0` · charged billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 14. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — charged billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0016` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (10500.0)` · screen/DB showed `PDF grand total 11600.0` · charged billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 15. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — paise billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0018` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9053.0)` · screen/DB showed `PDF grand total 10553.0` · paise billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 16. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — paise billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0019` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9053.0)` · screen/DB showed `PDF grand total 8853.0` · paise billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 17. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — paise billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0020` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9053.0)` · screen/DB showed `PDF grand total 10353.0` · paise billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 18. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — shortage billing, others=[]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0021` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9600.0)` · screen/DB showed `PDF grand total 10000.0` · shortage billing, others=[]: the batch invoice total differs from what the Ledger says the party owes

## 19. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — shortage billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0022` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9600.0)` · screen/DB showed `PDF grand total 11500.0` · shortage billing, others=[('Detention', 'charge', 1500)]: the batch invoice total differs from what the Ledger says the party owes

## 20. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — shortage billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0023` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9600.0)` · screen/DB showed `PDF grand total 9800.0` · shortage billing, others=[('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes

## 21. `INVOICE_CENTER_GRAND_TOTAL_VS_LEDGER` — DESIGN — shortage billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
1 occurrences in 1 scenarios. Smallest examples:

- `I0024` · step *invoice center PDF vs Ledger* · expected `grand total = amount owed in the Ledger (9600.0)` · screen/DB showed `PDF grand total 11300.0` · shortage billing, others=[('Detention', 'charge', 1500), ('Discount', 'deduction', 200)]: the batch invoice total differs from what the Ledger says the party owes
