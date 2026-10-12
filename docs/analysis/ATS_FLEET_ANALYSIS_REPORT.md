# ATS Fleet — Evidence-Based Application Analysis and Requirements Assessment

**Phase 1 of 2 — analysis and proposal only. No application source file, database, template or configuration was changed.**
Prepared 12 Oct 2026 · Code analysed: branch `maintenance` (uncommitted working tree), `app.py` 14,834 lines · Live site at https://ats-fleet-app.onrender.com runs older code and was **not** inspected.

---

## 0. How to read this report

### 0.1 Evidence labels (used everywhere)

| Label | Meaning |
|---|---|
| **EXECUTED NOW** | I ran it during this phase, on a disposable copy of the database, with the network blocked. The script is in `docs/analysis/evidence/`. |
| **EXECUTED EARLIER** | Run earlier in this same project (before this phase), by me. I did not re-run it now; the date is given. Treat as supporting, not fresh, evidence. |
| **CODE** | Established by reading the named function; not exercised. |
| **INFERRED** | Follows from code or standard behaviour but I did not demonstrate it. |
| **UNVERIFIED** | Cannot be established with what I have (e.g. Render's configuration). |

Issue classification follows your definitions: **Confirmed**, **Partially confirmed**, **Not reproduced**, **Unverified**, **Not applicable**.

### 0.2 What I did and did not do

* **Did not** modify `app.py`, templates, schema, tests, configuration or `fleet.db`. The only files I created are this report and the evidence scripts under `docs/analysis/`. (Earlier in this project I also generated `ATS_Fleet_App_Architecture_Blueprint.pdf` and `docs/blueprint/`; those are documentation only.)
* All experiments ran against **copies** of `fleet.db` (taken with SQLite's read-only backup API) or against synthetic databases built from `schema.sql`. Uploads in the upload experiment were redirected to a scratch folder. `requests.get/post` were replaced with a function that raises, so no SMS, eChallan or map call could be made.
* The blueprint was **treated as a hypothesis**. Every claim in Section 4 below was re-verified against code or an experiment in this phase; where I could only rely on the blueprint, it is labelled.

### 0.3 Coverage — what has and has not been inspected (full checklist in Appendix B)

Inspected in this phase by direct reading or execution: database connection and FK behaviour; every delete route; invoice numbering, generation, edit/PDF regeneration; party and vendor payment allocation; employee, advance and salary routes; the three upload handlers; the backup endpoint; session/login hooks, security headers, error handlers; tenant (company_id) usage; schema constraints; git-tracked files; dependency list; the stored-vs-recomputed billing arithmetic on all 472 real trips; ledger-vs-trip reconciliation for all 70 real parties.

Inspected earlier in this project (blueprint work): an automated static analysis of **all 199 routes** (which tables each reads/writes, which templates it renders, which helper chain it calls) and a summary of all 107 templates. I did **not** re-read every one of the 14,834 lines of `app.py` or every line of the 2,939 lines of CSS / 991 lines of JavaScript during this phase.

Not inspectable from here: Render's real configuration (persistent disk, env vars, whether `SECRET_KEY` / `BACKUP_TOKEN` are set), whether the GitHub repository is private, real MSG91 / eChallan behaviour, real phones/browsers, a live PostgreSQL database.

---

## 1. Executive assessment

**What the system is.** A single-company transport back-office on Flask + SQLite (one file, WAL mode), 199 URLs, 36 tables, rendered server-side with Jinja2 (website) plus a second set of mobile templates (`/app/`). One Python file holds every business rule, so the website and the phone app cannot disagree about a calculation. Real data today: 472 trips, 60 vehicles, 70 parties, 38 vendors, 224 payments, 452 maintenance rows, 3 invoice batches, 4 users.

**Strengths (all with evidence).**
* The arithmetic of a trip is correct: on all 472 real trips the stored `billed_amount` equals freight + charges − deductions recomputed independently (0 mismatches) — **EXECUTED NOW**.
* The per-party ledger function and the fast bulk-balance function agree for all 70 real parties (0 differences) — **EXECUTED NOW**.
* Payment allocation, ledgers, maintenance, expenses and the access model are covered by a 17-stage audit harness and 13 older regression suites. In this phase the 13 older suites all exited 0 and the harness result is in Section 7/Appendix A.
* Authentication was hardened recently (login throttling, server-side OTP, one Administrator, Limited Access, cross-site POST refusal, private document folders).
* Performance at 12,000 trips is good (78 of 87 website screens < 100 ms, none slower than 1 s) — **EXECUTED NOW, Section 7**.

**Major risks (verified).**
1. **Referential integrity is not enforced and deletions are unguarded.** Deleting a vehicle removed the vehicle row while its 37 trips, 13 maintenance rows, 3 compliance rows and 1 insurance policy stayed; the **Performance report's headline total silently fell from Rs 90,86,519 to Rs 88,21,288** and the trip list showed "None" for the vehicle, while the Accounts balances and party ledgers did not change. **Confirmed, EXECUTED NOW.**
2. **Invoice numbers can be duplicated.** 12 simultaneous "generate invoice" requests produced 12 invoices but only 6 distinct counter values, and 3 numbers were each issued **three times**. There is no uniqueness constraint. **Confirmed, EXECUTED NOW.**
3. **Issued invoices are not immutable and can be deleted without trace.** Changing a trip's rate changed the amount on the re-downloaded PDF of the *same* invoice number; deleting an invoice batch removes it entirely and leaves a gap in the numbering; the same trip can be invoiced twice and under another party. **Confirmed, EXECUTED NOW.**
4. **There is no way to correct or reverse a recorded payment** (no edit/delete/reverse route exists), and there is **no audit trail for edits or deletions** beyond `created_by/updated_by` columns and a sign-in log. **Confirmed, CODE.**
5. **Three different "balance" definitions are on screen.** On the real data the Trips screen's per-trip "pending" totals about Rs 1.08 crore (Rs 1,07,76,946), while the Ledger's net receivable is Rs 53.6 lakh, because Rs 57.5 lakh of historical payments (94% of all payments received) were imported without being allocated to trips. This is a definition issue, not an arithmetic error, and needs your decision. **Confirmed, EXECUTED NOW.**
6. **Uploads accept any file type** (an `.html` and an `.exe` were stored), live inside the deployment folder (durability on Render **Unverified**), and are **not included in the database backup**. **Confirmed (acceptance) / Unverified (durability).**
7. **No tenant isolation exists in the code** (only settings are filtered by `company_id`; nothing sets `session['company_id']`). Harmless today with one company; a blocker for Phase 1 multi-company. **Confirmed, EXECUTED NOW.**
8. **A "Sync FASTag" button generates fictitious toll costs** (it is a mock) — 5 such rows (Rs 725) already sit in the real data and flow into vehicle costs. **Confirmed, CODE + EXECUTED NOW (read-only query).**
9. **14 old database backups are tracked in git** (containing real data and password hashes). Repository visibility is **Unverified**. **Confirmed (tracked), Unverified (exposure).**

**Readiness (honest).** For a small team entering and viewing data, with the money arithmetic as tested, the application is usable. It is **not yet safe as a system of record for invoices and payments**, because invoice numbering/immutability, payment correction and deletion safety are missing, and because the deployment's persistent storage and backups of uploaded documents are unproven. None of this requires a rewrite.

---

## 2. Application inventory

| Area | Facts (verified from code / database) |
|---|---|
| Stack | Python 3.14, Flask 3.1.3, Werkzeug 3.1.8, Jinja2 3.1.6, SQLite 3.50.4 (WAL), gunicorn 26 (`gthread`, 2 workers × 4 threads), ReportLab 5.0.0 (PDF), openpyxl 3.1.5 (Excel), APScheduler, requests; vanilla JavaScript, custom CSS, Leaflet bundled; installable PWA shell |
| Size | `app.py` 14,834 lines, 434 top-level functions = 199 routes + 235 helpers/hooks; support modules `scheduler.py`, `compliance_service.py`, `providers/*` (1,712 lines); 107 templates (39 website pages, 23 partials, 45 phone pages); 2,939 lines CSS; 991 lines own JavaScript |
| Database | 36 tables, 62 indexes, WAL; every table has `company_id DEFAULT 1`; `PRAGMA foreign_keys` is **never turned on** |
| Modules | Dashboard · Trips · Vehicles (+ compliance, insurance, challans, RC lookup) · Maintenance (service, tyres, batteries, urea, tolls, compliance fees, insurance) · Ledger (party/vendor, payments, opening balances) · Invoices (Invoice Center, generated batches, per-trip invoice) · Expenses (overheads) · Salaries (employees, attendance, advances, payslips) · Reports (Business Performance, Performance, Route Analytics, Fleet Utilization, empty-run/idle) · Settings/Users/Profile · Route Finder (live OpenStreetMap) · phone app `/app/*` (about 100 routes) |
| Roles | Exactly one active Administrator (DB-enforced unique index); Full Access; Read Only (server blocks every write except an allow-list); Limited Access (per-screen View/Edit over 9 screens) |
| Integrations | MSG91 (login OTP SMS), eChallan (RC + challans, **mock unless enabled**), OpenStreetMap services (Photon, Nominatim, OSRM, Overpass), Google Fonts |
| Background | APScheduler only if `ENABLE_COMPLIANCE_SCHEDULER=1`; otherwise an external caller must hit `/internal/sync-tick` with a token. Paid sync is opt-in per switch |
| Deployment | Render web service (`Procfile`), database file path from `DATABASE_PATH`; schema changes self-apply at start-up |
| Backups | `/internal/backup` (token-protected, DB only) + `pull_backup.py`, `restore_check.py`; Render disk snapshots (**Unverified**) |
| Tests | `test_balance_audit.py` (17 stages), 14 other test files (13 executed in this phase; `test_multitenancy_isolation.py` needs a live PostgreSQL and was not run) plus helper `test_support.py`, `load_test.py`, `db_health_check.py` |
| Complete detail | `ATS_Fleet_App_Architecture_Blueprint.pdf` (199 pages): per-URL execution cards (Ch. 41), life of every table (Ch. 42), screen-to-data map (Ch. 43), data dictionary (Ch. 24) |

---

## 3. Business workflow maps

Each workflow below answers your eight questions in condensed form. Function names are real; line numbers are in the blueprint.

### 3.1 Trips and freight (LR / consignment)

| Question | Answer (verified in code) |
|---|---|
| Business problem | Records one truck journey for one customer and computes what the customer owes. |
| Who / permission | Full Access and Limited-Access users with *Trips: Edit*; Read Only can view. |
| Data in | Vehicle number, party, LR number, from/to, material, quantity, rate type (PER_MT or FIXED), rate, guarantee quantity, charges (detention, loading, union…), deductions (brokerage, TDS, shortage…), fuel, driver advance, owner (hired) terms. Phone and website use the same insert helper. |
| Calculations | `billed_amount = freight + charges − deductions`; `freight` = fixed amount, or (guarantee_qty if filled else quantity) × rate (`_trip_freight`). End Trip sets `billed_amount = freight − shortage`. |
| Records written | `trips` (80 columns); creates `parties`/`vendors`/`vehicles` by name on the fly if new (`get_or_create_*`). |
| Dependants | Ledger (Trip Bill entries), Trips balance-due, Dashboard, Business Performance, Route Analytics, Fleet Utilization, Invoice Center, vendor ledger (owner freight, fuel, advance). |
| Normal / edge / cancel / reverse | Normal: add → edit until End Trip; ended trips cannot be edited. **Delete is blocked only if a payment allocation or toll entry exists.** There is no cancel state. No reversal: you edit or delete. A trip can be edited after being invoiced with no warning (see IS-05). |
| Not established | Whether LR numbers should be unique (a duplicate pair exists today). |

### 3.2 Customer billing: invoices

| Question | Answer |
|---|---|
| Problem | Produce a numbered PDF invoice (party tax/bill invoice, or owner Freight Bill for hired vehicles) for a set of trips. |
| Who | Full Access; Limited users with *Invoices*. |
| Data in | Trips selected in Invoice Center (each LR must have been "reviewed"), invoice type, dates, GST/TDS %, extra charge/deduction lines. |
| Calculations | Per-trip lines from the trip; GST (CGST+SGST) for tax invoices; TDS; round-off; optional charges and deductions are opt-in per trip (`included_charge_keys`/`excluded_charge_keys`). |
| Records | `invoice_batches` (header, status draft/generated), `invoice_batch_trips`, `invoice_batch_items`, `invoice_batch_charge_exclusions`; counter in `settings.next_invoice_number`. |
| Dependants | Ledger shows "Expense Adj." entries from invoice items; invoice list/PDF. The party ledger bills **trips**, not invoices. |
| Cancel / reverse | **No cancel status.** An invoice can be edited, regenerated (PDF rebuilt from live data) or **deleted outright**. |
| Not established | Immutability policy, cancellation/credit-note policy, number reset each financial year. |

### 3.3 Payments, allocations, advances, reversals

| Question | Answer |
|---|---|
| Problem | Record money received from a party or paid to a vendor and apply it so balances reflect it. |
| Data in | Date, amount, mode, reference, optional ticked trips. |
| Calculations | Party payment: ticked trips first (each capped at its pending), then oldest pending trips; surplus stays unallocated. Vendor payment: ticked owner trips → oldest owner trips → unpaid maintenance bills → pending expense bills. Pending = billed − payment_received − party_advance. |
| Records | `payments` (header), `payment_allocations` (exactly one of trip/maintenance/overhead, enforced by CHECK), and the running total `trips.payment_received` / `maintenance.paid_amount` / `overheads.status`. |
| Dependants | Ledger balance, Trips pending, Maintenance unpaid, Expenses pending, Dashboard receivable/payable. |
| Duplicates | Identical payment within **10 seconds** is refused (`_payment_is_duplicate`). |
| Reversal | **Not implemented.** No route edits or deletes a payment. The only indirect effects: deleting a *maintenance row or expense* un-allocates its payments (`_delete_maintenance_row`, `_delete_overhead_row`); deleting a trip with allocations is refused. |
| Not established | How a mistaken payment should be corrected (reversal entry vs deletion). |

### 3.4 Ledgers and outstanding balances

Ledger balance is **computed on demand**, not stored (debit − credit; positive = the party owes you). For a party: opening balance + trip bills − trip advances − payments received ± invoice "Expense Adj." lines ± entries of a linked vendor account (merged ledger). For a vendor: opening balance + maintenance/tyre/battery/insurance bills, owner freight, fuel/advance the company paid, pending expense bills − payments paid. Because nothing is frozen, **editing or deleting an underlying record changes historical balances** by design; there are no period closes or snapshots.

### 3.5 Maintenance, fuel, tolls, insurance and compliance

Maintenance rows (service, tyre, battery, insurance premium, compliance fees, toll mirror rows) are bills against a vehicle and vendor with `amount` and `paid_amount`; they flow into the vendor ledger and Maintenance Overview. Fuel is held on the trip (`fuel_amount`, vendor, litres, price) and is *company cost* on own vehicles but a **deduction from the owner's dues** only when the company paid it on a hired vehicle (rule `_company_paid_on_hired_trip`, your "Option A"). Toll entries (`toll_entries`, source `fastag` or `manual`) mirror into maintenance as category Toll. In the real data all 94 toll rows are `fastag`-sourced: **89 are imported BOSS statement lines whose plaza is "FasTag Recharge"** (wallet recharges, i.e. cash top-ups rather than individual toll usage) and **5 were generated by the mock "Sync FASTag" button** (`sync_fastag_toll`, line 5464 — fabricated plazas/amounts, written as real cost rows). Insurance policies (`insurance_policies`) and compliance (`vehicle_compliance`) are records with expiry dates; eChallan data are mock unless a key and switch are set.

### 3.6 Employees, salaries, advances

Employees (`employees`), monthly salary rows (`salaries`, with `advance_recovery`), advances table (`advances`: type given/repaid). **Salaries and advances are linked to the employee by name text** (`advances.employee`, `salaries.employee`, plus `salaries.employee_id` for newer rows). The employee-ledger entry route (`_insert_employee_payment_from_form`) inserts salary rows with `advance_recovery = 0` and handles recovery as a separate "repaid" entry in `advances`; whether any other path fills `salaries.advance_recovery` was **not examined** (Question Q9). No route edits or deletes an advance.

### 3.7 Reports, exports, audit

Reports are read-only groupings of the same tables. Exports: ledger Excel/PDF, trips PDF/Excel, maintenance, whole-database Excel `/export`. "Audit records" are `created_by/at` and `updated_by/at` columns on most tables and an `access_logs` table of **sign-ins only** — there is no log of who edited or deleted what.

---

## 4. Verified issue register

Legend — **Sev**: Critical / High / Medium / Low · **Conf**: confidence in the finding (High/Med/Low).

### 4.1 Summary table

| ID | Area | Issue | Status | Sev | Conf | Evidence |
|---|---|---|---|---|---|---|
| DI-01 | Integrity | Foreign keys never enforced; schema has no `ON DELETE` rules | **Confirmed** | High | High | CODE `get_db()`; `PRAGMA foreign_key_check` on live copy: 4 orphan rows |
| DI-02 | Integrity | `delete_vehicle` has no guard: orphans trips, maintenance, compliance, insurance; changes reports | **Confirmed** | **Critical** | High | EXECUTED NOW `exp_delete.py`, `exp_delete2.py` |
| DI-03 | Integrity | Orphans already exist in the real database (3 `vehicle_compliance`, 1 `insurance_policies`) | **Confirmed** | Low | High | EXECUTED NOW (read-only check of copy) |
| DI-04 | Integrity | Employee advances/salaries keyed by name text: renaming an employee strands history | **Confirmed** | High | High | EXECUTED NOW `exp_pay.py` |
| DI-05 | Integrity | `overheads.vendor` is a name text matched case-insensitively; a vendor name change would detach expense bills (no UI to rename vendors today) | **Partially confirmed** | Low | Med | CODE `_bulk_balances`; no rename route exists |
| DI-06 | Integrity | Deletions leave no audit record; only sign-ins are logged | **Confirmed** | High | High | CODE; `access_logs` unchanged by delete (EXECUTED NOW) |
| DI-07 | Integrity | LR number not unique; 1 duplicate pair (`ATS-N/A`) exists | **Confirmed** | Low | High | EXECUTED NOW `db_health_check.py` |
| DI-08 | Integrity | One trip is over-received (payment + advance > billed by Rs 434) | **Confirmed** | Low | High | EXECUTED NOW `db_health_check.py` |
| DM-01 | Data authenticity | "Sync FASTag" button (`sync_fastag_toll`) invents toll transactions and posts them as real maintenance cost; 5 rows (Rs 725, created 21 Aug) are in the real data | **Confirmed** | Medium | High | CODE + EXECUTED NOW (read-only query of copy) |
| DM-02 | Data semantics | 89 imported toll rows are "FasTag Recharge" (wallet top-ups) treated as toll expense | **Confirmed** (treatment is a policy question, Q13) | Low-Med | High | EXECUTED NOW (read-only query) |
| IS-01 | Invoice | Concurrent generation duplicates invoice numbers; counter loses increments | **Confirmed** | **Critical** | High | EXECUTED NOW `exp_invoice.py` |
| IS-02 | Invoice | No UNIQUE constraint on `invoice_batches.invoice_number` | **Confirmed** | High | High | CODE `schema.sql` |
| IS-03 | Invoice | Same trip can be invoiced repeatedly | **Confirmed** | Medium | High | EXECUTED NOW `exp_inv2.py` |
| IS-04 | Invoice | An invoice can be issued to party A containing party B's trip | **Confirmed** | Medium | High | EXECUTED NOW `exp_inv2.py` |
| IS-05 | Invoice | Issued invoice is rebuilt from live trip data; editing the trip silently changes the "same" invoice | **Confirmed** | High | High | EXECUTED NOW `exp_inv3.py` (+CODE: no guard in `edit_trip`/`_update_trip_from_form`) |
| IS-06 | Invoice | Batch delete removes invoice and creates a numbering gap, no cancel/credit-note state, no log | **Confirmed** | High | High | EXECUTED NOW `exp_delete.py`; CODE `invoice_batch_delete` |
| IS-07 | Invoice | Counter never resets by year though the number contains the year | **Confirmed** | Low | Med | CODE lines 9257–9258 — may be intended (Q4) |
| IS-08 | Invoice | Invoice grand total can differ from Ledger billed amount (optional lines, "Others", RCM) | **Confirmed (design)** | Medium | High | EXECUTED EARLIER (harness DESIGN class) + EXECUTED NOW (Section 5) |
| PM-01 | Payments | Duplicate-payment guard is a 10-second window on identical fields | **Partially confirmed** | Medium | Med | CODE; EXECUTED NOW: 8 simultaneous identical posts → 1 stored (**concurrency not reproduced**) |
| PM-02 | Payments | No edit/delete/reverse for a recorded payment | **Confirmed** | High | High | CODE (no route; no `DELETE FROM payments`) |
| PM-03 | Payments | Employee repayment can exceed advance given (given 1,000, repaid 5,000 accepted) | **Confirmed** | Medium | High | EXECUTED NOW `exp_pay.py` |
| PM-04 | Payments | Trip over-payment possible after later edits (see DI-08) | **Partially confirmed** | Low | Med | one real case; the payment form itself caps allocation (harness) |
| FN-01 | Financial | Trips "pending", Ledger balance and invoice totals use different definitions | **Confirmed (design)** | High | High | EXECUTED NOW (Section 5) |
| FN-02 | Financial | Money is stored as binary floating point (REAL), rounded at display with a 0.004 tolerance | **Partially confirmed** | Low | Med | CODE; no discrepancy found in 920k harness checks (EXECUTED NOW) |
| FN-03 | Financial | Historical balances change when any underlying record is edited/deleted (no snapshots, no period close) | **Confirmed (design)** | Medium | High | CODE; EXECUTED NOW (DI-02) |
| VL-01 | Validation | Some routes swallow errors: invalid employee advance amount redirects with no message | **Confirmed** | Medium | High | EXECUTED NOW `exp_pay.py` (`add_employee_advance`) |
| VL-02 | Validation | Legacy routes use bare `float()`; bad input gives a generic "could not save" page, not a field message | **Confirmed** | Low | High | CODE (`add_advance`, `edit_maintenance`, `edit_employee`, `add_insurance`) + global handler |
| VL-03 | Validation | `edit_employee` accepts an empty name | **Confirmed** | Low | High | EXECUTED NOW `exp_pay.py` |
| VL-04 | Validation | Routes that "commit then continue" could keep partial writes after an error | **Unverified** | Low | Low | not systematically searched |
| UP-01 | Uploads | Insurance/toll-receipt uploads accept any file type (`.html`, `.exe` stored) | **Confirmed** | Medium | High | EXECUTED NOW `exp_upload.py` |
| UP-02 | Uploads | Stored `.html` would execute in the browser if opened (same site, after login) | **Partially confirmed** | Medium | Med | INFERRED (served from `/static/` with a guessed content type; not opened in a browser) |
| UP-03 | Uploads | Files are saved inside the application folder (`static/uploads/...`); survival across Render deploys/restarts depends on a disk that the code does not mount | **Unverified** | High | — | CODE location; Render config not visible |
| UP-04 | Uploads | Database backup (`/internal/backup`) contains the database only; uploaded documents are not backed up | **Confirmed** | High | High | CODE |
| UP-05 | Uploads | Size cap exists (16 MB per request); a 5 MB file was accepted | **Confirmed (cap present)** | Low | High | EXECUTED NOW |
| SC-01 | Security | No CSRF tokens; protection = SameSite=Lax + refuse cross-origin POSTs that carry Origin/Referer | **Partially confirmed** (weakness) | Low-Med | Med | CODE `reject_cross_site_posts` |
| SC-02 | Security | No tenant isolation in queries; `session['company_id']` never set | **Confirmed** | Not applicable today / High for Phase 1 | High | EXECUTED NOW `exp_tenant.py`; CODE: only 5 queries use `company_id` (all settings) |
| SC-03 | Security | `python app.py` defaults to debug mode ON (Werkzeug debugger) | **Confirmed** (dev path) | Low | High | CODE line 14792; Render uses gunicorn (`Procfile`) |
| SC-04 | Security | `requirements.txt` has no version pins | **Confirmed** | Medium | High | CODE |
| SC-05 | Security | 14 database backup files (real data, hashed passwords) are tracked in git | **Confirmed (tracked)** / **Unverified (public?)** | High | High | EXECUTED NOW `git ls-files` |
| SC-06 | Security | No Content-Security-Policy; no error alerting/monitoring | **Confirmed** | Low-Med | High | CODE |
| SC-07 | Security | API keys (eChallan, MSG91) stored in plain text in the settings table | **Confirmed** | Medium | High | CODE (masked in UI; not encrypted) |
| SC-08 | Security | Route-level authorization: bypass by anonymous / Read-Only / Limited users | **Not reproduced** | — | High | EXECUTED NOW harness `roles` stage (Appendix A) |
| SC-09 | Security | Error messages expose internals | **Not reproduced** | — | Med | CODE: global handler returns fixed text; login errors generic |
| PF-01 | Performance | Query-count N+1 shapes: `/salaries` 1,809 queries, `/fleet-utilization` 1,454 queries per page | **Confirmed (measured)** | Low | High | see Section 7 |
| PF-02 | Performance | Slowest screens: Business Performance (long range ~1 s), Trips PDF of all trips (~5 s earlier) | **Confirmed (measured)** | Low | High | Section 7 |
| PF-03 | Performance | SQLite write concurrency under real workload | **Partially confirmed** | Low | Med | Section 7 |
| MT-01 | Maintainability | One 14,834-line file; 235 helpers; some very large functions | **Confirmed** | Medium | High | CODE; consequence is slower, riskier change, not wrong results |
| MT-02 | Maintainability | Dead/duplicate code | **Partially confirmed** | Low | Med | Earlier cleanup removed some; remaining list in `code_health_report.md` — **removal not recommended until dependencies are re-verified** |

### 4.2 Detail for the issues that matter most

**DI-02 — vehicle deletion (EXECUTED NOW).** `delete_vehicle` (`app.py:7642`) is a single `DELETE FROM vehicles WHERE id=?`.
* Vehicle OR14Q8217 (id 5): 37 trips, 13 maintenance rows, 3 compliance rows, 1 insurance policy.
* After deletion: `trips.vehicle_id` still points at id 5 for all 37 trips; maintenance 13, compliance 3, insurance 1 likewise.
* `GET /trips` still works but shows "None" for the vehicle; the orphaned trip's detail page returned **404**.
* `/performance` changed: its trip count fell 428 → 391 and three money totals fell (Rs 90,86,519 → Rs 88,21,288; Rs 57,61,991 → Rs 56,80,820; Rs 18,71,638 → Rs 17,87,028 — the first is the revenue figure quoted in Section 1). `/fleet-utilization` also changed (the deleted vehicle's row and its km/utilisation totals disappeared). `/vehicles` (60 → 59 vehicles) and `/maintenance` (20 → 19 in its vehicle-based counters) changed too. **`/accounts`, the party ledgers (checked parties 1 and 17) and `/business-performance` (default period) were unchanged**, so reports now disagree with each other.
* *Expected:* deleting a vehicle with business history should be refused (or hide the vehicle without removing it); reports should not change.
* *Impact:* an accidental click (or a deliberate one) silently rewrites management reports. Severity Critical for data integrity because it is silent and unrecoverable except from backup.

**IS-01 — duplicate invoice numbers (EXECUTED NOW).** `invoice_center_generate` reads `next_invoice_number` (line 9257), builds the number, and only later inserts the batch and increments the counter (lines 9271–9296) — no transaction or lock around the read, and no UNIQUE index. Twelve requests fired together produced numbers `…0004`, `…0008`, `…0009` three times each; the counter ended at 10 instead of 16. gunicorn runs 2 workers × 4 threads, so two staff generating invoices at the same moment can hit this. *Expected:* each invoice number is issued exactly once.

**IS-05 — issued invoice is not a frozen document (EXECUTED NOW).** I generated ATS/INV/2026/0004 for trip 24 (Rs 36,800), raised the trip rate by Rs 100/MT and re-downloaded the PDF of the same invoice number: it showed the new amount, not the old one. `invoice_batch_pdf` rebuilds from live trip rows by design ("View/Regenerate"), and no route stops editing an invoiced trip.

**FN-01 — different balance definitions**: see Section 5.

**UP-01/UP-03/UP-04 — uploads:** the three handlers (`_save_toll_receipt` line 3857, `_save_insurance_doc` line 4628, the logo route line 8512) use `secure_filename` (path safety) but only the logo route checks an extension. A stored `evil.html` and `run.exe` were accepted. Files go to `static/uploads/<type>/<company_id>/` under the program folder, which on most hosts (including a default Render service) is rebuilt on every deploy unless a disk is mounted there; the backup endpoint archives only the SQLite file.

---

## 5. Financial reconciliation assessment

### 5.1 Definitions and where they are used

| Metric | Business definition (as implemented) | Formula | Source records | Includes opening / unallocated / adjustments? | Shown on |
|---|---|---|---|---|---|
| Trip billed amount | What the party owes for the trip | freight + charges − deductions (`_trip_freight`; End Trip: freight − shortage) | `trips` | n/a | Trips, Trip detail, Ledger, Dashboard, performance reports |
| Trip pending ("Balance Due") | Unpaid part of one trip | billed − payment_received − party_advance | `trips` (running totals kept in step with allocations) | **No** opening balance, **no** unallocated payments, **no** invoice adjustments | Trips list, payment popup |
| Party ledger balance (receivable) | What the party owes in total | opening + trip bills − trip advances − payments received ± invoice "Expense Adj." ± linked-vendor entries | `parties`, `trips`, `payments`, `invoice_items`, `invoice_batch_items`, linked `vendors` | **Yes** | Ledger, Accounts, Dashboard "Receivable" |
| Vendor ledger balance (payable) | What you owe the vendor | opening + bills (maintenance, owner freight, expenses) − company-paid fuel/advance rule − payments paid | `vendors`, `maintenance`, `trips`, `overheads`, `payments` | Yes | Ledger, Dashboard "Payable" |
| Invoice grand total | Document total for selected trips | trip lines + opted-in charges ± GST/TDS ± round-off | `invoice_batch*` + live trips | Optional lines can add or remove amounts the ledger does not bill the same way | Invoice PDF / list |
| Vehicle profit | Owner vs hired rule | own: billed − own costs; hired: billed − owner cost | `trips`, `maintenance` | n/a | Dashboard, Business Performance |

### 5.2 Real-data reconciliation (EXECUTED NOW on a copy of the live database)

| Check | Result |
|---|---|
| Stored `billed_amount` vs independent recompute, 472 trips | **0 mismatches** |
| `payments.allocated_amount` vs sum of its allocation rows | **0 mismatches** |
| Trips with `payment_received` vs allocations | **0 mismatches** (all received amounts on trips come from allocations) |
| Per-party ledger function vs bulk function, 70 parties | **0 differences** |
| Total billed on trips | Rs 1,11,83,372.58 |
| Payments received (223 rows) | Rs 61,30,499.00 — of which allocated to trips only **Rs 3,76,426.40** |
| Trips "pending" (billed − payment_received − party advance of Rs 30,000 on one trip) | ≈ Rs 1,07,76,946 |
| Ledger net receivable (sum of positive party balances) | Rs 53,56,533.18 |
| Vendor payable (sum of negative vendor balances) | Rs 13,26,272.00 |
| Parties whose Ledger balance ≠ their trips' pending | **48 of 70** |

**Why they differ:** the importer loaded Rs 57.5 lakh of historical receipts into `payments` without allocations, so they reduce the Ledger but not any trip's pending. For 68 of 70 parties the Ledger equals *opening balance + billed − received payments ± invoice adjustments* exactly. The remaining two (ASHOKA ISPAT UDYOG, TUBES & PIPES) differ by the trip *advances* (credit rows inside the Trip Bill entries) and one merged linked-vendor row; after allowing for those the ledger is again exact. I found **no unexplained arithmetic difference**.

### 5.3 Auditability after edits

Not auditable. No immutable ledger, no payment reversal entries, no change log (DI-06, PM-02, FN-03), and issued invoices re-render from live data (IS-05). Rounding: paise precision floats (0 trips with more than 2 decimals today); allocation uses a 0.004 tolerance; no failure in 918,301 party-stage checks (**EXECUTED NOW**, Appendix A) — risk is theoretical, not demonstrated.

### 5.4 Interpretations that need your policy decision (not decided by me)

1. **Which "balance" is the official one on the Trips screen** — per-trip pending (allocation-based) or the Ledger? Option A: leave both, label them. Option B: one-time allocation of historical unallocated payments to trips oldest-first (changes per-trip pending, not the Ledger). Option C: show the Ledger figure everywhere.
2. **TDS** is currently subtracted inside the trip bill (reducing receivable). Accounting alternative: record gross receivable and a separate "TDS receivable" asset. Which is your policy?
3. **Invoice vs ledger**: should optional charges/deductions on an invoice always flow to the ledger, or remain invoice-only?
4. **Opening balances and unallocated payments**: remain ledger-only, or become allocatable documents?

---

## 6. Security, data integrity and recovery

| Topic | Verified state |
|---|---|
| Authentication | Password login throttled 5/account and 30/IP per 15 min; SMS OTP stored server-side (keyed hash, 5 tries, expiry); sign-up closed; session cookie SameSite=Lax (+Secure on Render); 3-hour idle timeout. (CODE + EXECUTED NOW harness `roles` stage) |
| Authorization | Every one of the 199 routes is classified to a screen (a test fails if one is not); Read Only and Limited Access enforced server-side; Administrator-only pages blocked for others. **No bypass found** in the executed stage (SC-08). |
| Tenant isolation | **None in queries** (SC-02). Only settings are company-filtered. The Postgres schema has row-level security (written, parse-verified, **never run against a live PostgreSQL**; `test_multitenancy_isolation.py` requires one and was **not executed**). |
| Transactions | Each route opens its own connection, commits explicitly; teardown closes leaked connections. Multi-step writes mostly commit once at the end. The invoice counter read-modify-write is outside any lock (IS-01). |
| Uploads | UP-01 … UP-05 above. |
| Backups | Database: online-backup endpoint with integrity check + pull script + restore checker (**EXECUTED EARLIER**: restore verified on a copy). Uploads: not backed up (UP-04). Whether Render has a persistent disk and snapshots: **Unverified**. |
| Recovery | A deleted vehicle/invoice/record can only be restored from a database backup (no soft delete). |
| Secrets | `SECRET_KEY` from environment or a random key stored in the database; API keys stored plaintext in `settings` (SC-07); 14 old database files tracked in git (SC-05) — rotate keys and decide history clean-up (needs your explicit approval; rewrites GitHub history). |
| Dependencies | Unpinned (SC-04); installed versions are recent. |
| Debug | `python app.py` defaults to debug ON (SC-03); gunicorn on Render does not use it. |

---

## 7. Performance assessment

All numbers below were **measured in this phase (EXECUTED NOW, 12 Oct 2026 01:26)** on a developer Mac (8 cores) against a **synthetic database of 12,000 trips, 4,215 payments (17,086 allocations), 4,500 maintenance bills, 2,280 salaries, 3,500 expenses, 150 parties** built by `load_test.py` (never `fleet.db`). They are **server time**; a phone and Render's smaller instance will be slower (my expectation is a few times slower — a **hypothesis**, not measured).

### 7.1 Measured: single-user screen times (149 screens, median of 3 runs)

| Group | Result |
|---|---|
| Website screens (87) | Excellent (<100 ms) 78 · Good 6 · OK 3 · Slow 0 |
| Phone screens (41) | Excellent 36 · Good 5 |
| Downloads (21) | Excellent 11 · Good 4 · OK 4 · **Slow 1** (`/export` whole-database Excel, 1.1 s) · **Poor 1** (`/trips/export/pdf`, all trips, 5.1 s) |
| Buttons that save (13) | all Excellent (2–94 ms) |

Slowest screens: `/business-performance` over 18 months 905 ms (189 queries); `/dashboard` over 18 months 440 ms; Dashboard default 216 ms; fleet utilisation / empty-runs / idle-tracker about 115 ms.

### 7.2 Measured: N+1-shaped query counts (screens are still fast)
`/salaries` runs 1,809 queries per page; `/fleet-utilization`, `/empty-runs`, `/idle-tracker` 1,454; `/dashboard` 298; `/business-performance` 189; ledger pages 120–130. At 12,000 trips these still finish in 30–220 ms because SQLite is in-process; they would grow with data and are the first things to restructure if volume rises. (The previous worst case, the Accounts list at ~9,000 queries, was already fixed earlier and is now 24 queries.)

### 7.3 Measured: many people at once (real gunicorn, 2 workers × 4 threads, same as Render)
Each virtual user logs in and then clicks 1–2 times a second (much faster than a person) with about 10% saves, for 25 seconds per level:

| Users | Requests | Req/s | Median | 95th percentile | Errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 37 | 1.5 | 84 ms | 307 ms | 0 |
| 5 | 175 | 6.8 | 67 ms | 322 ms | 0 |
| 10 | 239 | 9.3 | 73 ms | 2,038 ms | 0 |
| 25 | 185 | 6.4 | 2,159 ms | 5,823 ms | 0 |
| 50 | 89 | 2.9 | 6,900 ms | 16,395 ms | 0 |

Reading: no errors and no "database is locked", but throughput stops growing at about 10 users and delays grow quickly beyond 10–15 simultaneous heavy clickers. The likely cause is that 8 threads are shared by heavier screens (Dashboard, Business Performance, all-trips PDF) — **hypothesis, not isolated by profiling**. Your team is about 5 people, so this is comfortable today; it matters if you add many simultaneous users.

### 7.4 Concurrency of writes (SQLite)
* 12 simultaneous invoice generations: all completed (HTTP 302), no lock errors — but produced duplicate numbers (IS-01): SQLite serialises *writes* safely; the defect is the application's read-then-write of the counter, not SQLite.
* 8 simultaneous identical payments: one stored (PM-01).
* **Not measured:** sustained concurrent writes from many users; behaviour on Render's actual disk.

### 7.5 What is evidence and what is hypothesis
| Statement | Status |
|---|---|
| Screens are fast at 12,000 trips | Measured |
| `/salaries` etc. do ~1,500–1,800 queries | Measured |
| Throughput saturates around 10 users on this laptop | Measured |
| Cause is heavy screens occupying the 8 threads | Hypothesis |
| Render will be a few times slower | Hypothesis |
| SQLite is the bottleneck at 25–50 users | Hypothesis (single-writer limits exist, but the measured delays coincide with heavy reads) |
| The large `app.py` hurts runtime | Not supported — it affects change risk, not speed |


---

## 8. Required versus optional changes

### 8.1 Critical correctness, security or data-loss fixes
| # | Change | Why (evidence) |
|---|---|---|
| C1 | Make invoice numbering atomic and unique (take the number inside one write transaction; add `UNIQUE(company_id, invoice_number)`) | IS-01, IS-02 — duplicate legal document numbers |
| C2 | Refuse to delete a vehicle (and trip/party/vendor equivalents) that has history; offer "Inactive" instead | DI-02 — silently rewrites reports |
| C3 | Stop invoice deletion of issued invoices: replace with a Cancelled status that keeps the number | IS-06 — numbering gaps; needs your policy (Q2) |
| C4 | Restrict upload types for insurance/toll documents (PDF/JPG/PNG), serve as attachment | UP-01, UP-02 |
| C4b | Remove (or clearly label as demo) the mock "Sync FASTag" button; decide on the 5 existing mock rows | DM-01 — fabricated costs in real data |
| C5 | Make uploaded documents durable and backed up (disk path + include in backup) | UP-03, UP-04 — depends on Render facts (Q8) |
| C6 | Decide and act on the 14 tracked database backups and rotate secrets | SC-05 — needs your approval |

### 8.2 High-priority operational reliability
| # | Change | Why |
|---|---|---|
| H1 | Turn on `PRAGMA foreign_keys=ON` per connection **after** cleaning the 4 orphan rows and testing every delete route | DI-01 — safety net; risky if done first |
| H2 | Payment correction workflow (reversal entry) | PM-02 |
| H3 | Change log for edits/deletes of money records | DI-06 |
| H4 | Freeze issued invoices (store a snapshot or lock the trips) | IS-05 — needs your policy (Q1) |
| H5 | Warn/refuse invoicing a trip twice or for another party | IS-03, IS-04 |
| H6 | Employee advances: link by id; cap repayment; show error messages | DI-04, PM-03, VL-01 |
| H7 | Pin dependency versions; add uptime/error alerting | SC-04, SC-06 |
| H8 | Verify Render persistent disk, `SECRET_KEY`, `BACKUP_TOKEN`, daily pull | Unverified items |

### 8.3 Performance improvements supported by evidence
Only where measured: reduce query counts on `/salaries` and `/fleet-utilization`; cache/precompute Business Performance for long ranges; stream the all-trips PDF. None is urgent at today's volume (Section 7).

### 8.4 Maintainability improvements with measurable benefit
Split `app.py` into modules *only* along existing section boundaries, behind the existing harness (benefit: change risk). Replace the 10 hand-copied validation patterns with the shared `_parse_money` helper. Remove dead code only after dependency checks.

### 8.5 Optional
Multi-company retrofit (needs Phase 1 decision), PostgreSQL move, integer-paise money, full CSRF tokens and CSP, design-token clean-up, route-finder disk cache.

---

## 9. Questions requiring your decision

| # | Question | Why it matters |
|---|---|---|
| Q1 | Must an issued invoice be **frozen** exactly as issued, even if the trip is edited later? | Decides snapshot vs. lock approach (IS-05) |
| Q2 | May an issued invoice ever be **deleted**, or only **cancelled** (number kept, optional credit note)? | GST audit expectation of serial numbers — please confirm with your accountant (IS-06) |
| Q3 | May the **same trip** appear on more than one invoice (e.g. supplementary invoice)? And may a trip of party B be on party A's invoice? | IS-03, IS-04 |
| Q4 | Should invoice numbers **restart each financial year**? Format currently carries the calendar year but the counter never resets | IS-07 |
| Q5 | **Vehicle/party/vendor deletion policy**: block if history exists, or archive/inactive? | DI-02 |
| Q6 | **Payment corrections**: reversal entry, or edit/delete with log? Who may do it? | PM-02 |
| Q7 | **Official balance** on the Trips screen, and what to do with the Rs 57.5 lakh unallocated historical receipts (Section 5.4) | FN-01 |
| Q8 | Does the Render service have a **persistent disk** (which path)? Is the GitHub repo **private**? Are `SECRET_KEY`/`BACKUP_TOKEN` set? | UP-03, SC-05 |
| Q9 | Employee advances: may repayment exceed what was given (becomes owed to the employee)? Should salary "advance recovery" create a repayment entry? | PM-03, 3.6 |
| Q10 | **TDS** treatment (deduct from receivable vs. separate asset) and the rounding rule for invoices (whole rupee?) | FN-04 policy |
| Q11 | Should **LR numbers be unique** (per company)? What to do with the existing duplicate `ATS-N/A`? | DI-07 |
| Q12 | Is **multi-company** really planned, and when? | decides how much of SC-02 matters now |
| Q13 | FASTag: should a wallet *recharge* count as the toll cost when recharged, or should only actual toll deductions count? And should the mock "Sync FASTag" button be removed until a real feed exists? | DM-01, DM-02 |

---

## 10. Phased implementation plan (proposal only — nothing starts without your approval)

Each phase is small, independently testable, deployed **database first, then code** (as your project rules require), and starts with a backup.

| Phase | Scope | Depends on | Risks | Acceptance criteria | Regression tests | Rollback |
|---|---|---|---|---|---|---|
| **P2.1 Invoice numbering safety** | Atomic number allocation; unique index (after checking existing numbers are unique); use same transaction for counter | none (Q4 for year rule) | Numbering gaps if a PDF build fails after insert — decide order | 50 concurrent requests → 50 distinct numbers, counter = n+50 | new concurrency test + `invoice`, `invinput`, `ownerinv` stages + `test_invoice_charge_exclusions` | restore DB backup; drop index; revert code |
| **P2.2 Deletion guards** | Vehicle delete blocked/inactive when history exists; same check list for other deletes | Q5 | Users used to deleting test vehicles | Delete of vehicle with trips refused with message; reports unchanged; orphan check = 0 | new delete-matrix test; `db_health_check`; full harness | code-only revert |
| **P2.3 Upload hardening** | Extension+content check, attachment download, disk path | Q8 | Existing odd files | `.html/.exe` rejected; existing files still open | upload test; roles `security` case | code revert |
| **P2.4 Backups include uploads; durability proof** | Add uploads to backup pull; restore drill | Q8 | Backup size | Restore drill passes incl. documents | `restore_check` extended | n/a |
| **P2.5 Invoice immutability/cancel** | Cancel status, lock/snapshot per Q1–Q3 | Q1–Q3, P2.1 | Touches PDF regeneration; biggest design decision | Edited trip does not alter issued invoice; cancelled number remains | invoice stages + new tests | DB backup + code revert |
| **P2.6 Payment reversal + change log** | Reversal entries; log table for money edits/deletes | Q6 | New table → schema+Postgres twin, company_id | Reversal restores trip pending and ledger exactly | party/vendor stages + new reversal scenarios | DB backup + code revert |
| **P2.7 Employee ledger by id** | link by id, cap repayment, messages | Q9 | Data migration of names → ids | rename keeps history | `employee`, `empedge` stages | DB backup |
| **P2.8 FK enforcement** | Clean 4 orphans, `PRAGMA foreign_keys=ON`, test all delete routes | P2.2 | Hidden delete paths start failing | Harness fuzz shows no new 500s | full harness + all older suites | revert pragma |
| **P2.9 Hygiene** | Pin versions, alerting, debug default off | none | none | clean install reproduces tests | all suites | revert |
| **P2.10 Balance definition alignment** | Per Q7 decision | Q7 | Changes numbers users see | agreed definition identical on all screens | party stage design classes | DB backup |

Performance and refactoring work is deliberately **not** scheduled until the above are done.

---

## 11. Final recommendation

**Fix first (in this order):** P2.1 invoice numbering; P2.2 deletion guards; P2.3/P2.4 uploads and backups; then P2.5 invoice immutability and P2.6 payment reversal once you answer Q1–Q3 and Q6.

**Leave unchanged:** the Flask/Jinja/vanilla-JS/custom-CSS architecture; the trip arithmetic, ledger engine and allocation order (all verified); the role model; the mobile/website parity design; the self-applying schema steps; the existing UI.

**Can wait:** performance tuning, splitting `app.py`, PostgreSQL, multi-company, CSP/CSRF tokens, design tokens.

**Evidence still missing:** Render's real configuration (disk, env vars, backups, repo privacy); a test in a real browser/phone for the upload-HTML behaviour (UP-02); a search for "commit-then-error" partial writes (VL-04); the live PostgreSQL schema; real MSG91/eChallan behaviour; whether the uploaded-documents folder on Render persists.

**Approval requested:** please review Sections 4 and 9 and tell me (a) which phases to proceed with and (b) your answers to Q1–Q13 (any you cannot decide yet can stay open; the affected phase will wait). I will not modify any application file until you approve a scope.

---

## Appendix A — Tests: executed vs proposed

### A.1 Executed in this phase (12 Oct 2026)

| Test | Result |
|---|---|
| **Full audit harness `test_balance_audit.py` (all 17 stages)** — drives the real app through its test client on disposable databases, network blocked, compares every website and phone screen with an independent model | 22,916 scenarios, **1,025,186 checks**; 22,071 disagreements in 38 distinct symptoms; **all 38 are classified DESIGN (business-definition differences, Section 5); none classified as a bug** |
| `--stage roles` run on its own (access levels, Administrator-only, Limited Access, security, single Administrator, MSG91) | 10 scenarios, **218 checks, 0 disagreements** |
| 13 older regression suites: `test_accounts_equivalence`, `_business_performance`, `_employee_ledger`, `_financial_integrity`, `_floor_protection`, `_invoice_charge_exclusions`, `_ledger_integrity`, `_maintenance_ledger`, `_no_bfcache`, `_phase2_ledger_trips_maintenance`, `_phase3_analysis_combinations`, `_route_finder`, `_vehicles_sort_persist` | all **exit code 0** (I did not read their individual assertion counts) |
| `db_health_check.py` (read-only) | integrity ok; schema = `schema.sql`; 4 orphan rows; 1 over-received trip; 1 duplicate LR |
| `PRAGMA foreign_key_check` on a copy of the live database | 4 violations |
| 13 evidence experiments (Appendix C) | results quoted in Sections 1, 4, 5 |
| `load_test.py` pages (149 screens) and users (1–50) on a fresh synthetic database | Section 7 |

### A.2 Executed earlier in this project (not re-run now)
`restore_check.py` on a pulled backup (11 Oct); MSG91 flow against a **faked** provider (never against real MSG91); a visual look at one emulated phone size in a browser; earlier runs of the same harness and load test on 10–11 Oct (superseded by the runs above).

### A.3 What the existing tests do **not** cover (verified by reading what they exercise)
* Concurrency *within one database* (each scenario has its own database; the 7 workers are separate databases) — hence IS-01 was never caught.
* Deleting vehicles / invoice batches and checking related records or reports (fuzz feeds garbage to POST routes and looks for crashes, not integrity).
* Invoice number uniqueness, invoice immutability, double-invoicing.
* File uploads (type, size, persistence), backup of uploaded files, restore of documents.
* Anything on a real browser, real phone, Render, or PostgreSQL (`test_multitenancy_isolation.py` needs a live PostgreSQL and was **not executed**).
* Payment reversal (the feature does not exist).

### A.4 Proposed (not run)
Permanent tests for: 50-way concurrent invoice generation; delete-guard matrix across every delete route; upload type/size; same-trip double invoicing; invoice frozen after trip edit; employee rename; backup + restore including documents; a browser check that a stored `.html` does or does not execute; a search for "commit then error" partial writes (VL-04); and PostgreSQL isolation once a database exists.


## Appendix B — Coverage checklist

| Area | Inspected how | Status |
|---|---|---|
| Database connection / FK / WAL | read `get_db`, schema; executed `foreign_key_check` | Done |
| Delete routes (vehicle, trip, invoice batch, maintenance, overhead, salary, tyre/battery/urea/toll/compliance-fee/insurance) | read; vehicle & invoice batch executed | Done (trip delete: code only) |
| Invoice numbering/generation/edit/PDF | read + executed (concurrency, double invoice, cross-party, immutability) | Done |
| Payments (party/vendor allocation, duplicate guard) | read + executed | Done |
| Employee/advance/salary | read + executed | Done (salary generation logic read only partly) |
| Trips (insert/update/end) | read arithmetic; recomputed all 472 real trips | Done (edit flow not executed) |
| Uploads | read all 3 handlers; executed insurance upload | Done (toll receipt handler: code only) |
| Auth/session/roles | read hooks; harness `roles` stage executed | Done |
| Tenant isolation | grep + executed session test | Done |
| Backup / deployment | read endpoint, Procfile, requirements; Render not visible | Partial |
| Templates (107), JS (991 lines), CSS (2,939 lines) | automated summaries only (earlier) | **Not line-by-line** |
| Remaining ~190 routes' internals | AST analysis (earlier) + harness fuzz | **Not read line-by-line** |
| Maintenance, tolls, insurance, compliance, Route Finder, scheduler logic | summarised; covered by suites | Partial |
| Real browser / phone | not done | **Not done** |
| PostgreSQL | not run | **Not done** |

## Appendix C — Evidence scripts

All in `docs/analysis/evidence/` (each copies a database first and never touches `fleet.db`): `exp_invoice.py` (IS-01), `exp_delete.py` / `exp_delete2.py` (DI-02, IS-06), `exp_inv2.py` (IS-03/04), `exp_inv3.py` (IS-05), `exp_pay.py` (PM-01, PM-03, DI-04, VL-01/03), `exp_upload.py` (UP-01), `exp_tenant.py` (SC-02), `exp_recon*.py` (Section 5).
