# Code, database and UI health report — 10 Oct 2026

Method: static analysis of every Python file (pyflakes + AST scans for dead code, duplicates, loops that run SQL),
a read-only audit of every table in a snapshot of the real database (`db_health_check.py`), an inventory of every
font / size / colour declared in 95 templates and 4 stylesheets, and the full regression suite (15 audit stages,
12 existing test files, equivalence test, load test of 144 screens on a 12,000-trip database).

## Regression result
No regression: every stage, every test file and every screen timing is identical to the run before the clean-up.
Only the known "design" differences remain (opening balances, invoice vs ledger, hired-vehicle maintenance).

## Done in this pass (all re-tested)
* `edit_trip` carried its own ~85-line copy of the trip update that the mobile app already shared via `_update_trip_from_form` — now one copy (the two had drifted before).
* Removed 4 functions nothing calls and 9 lint items (`requests` import, 5 unused variables/imports, a pointless f-string). pyflakes is now clean for the whole backend.
* Moved 8 files that nothing references out of the served folders into `archive/unused_ui/` — including two 242 KB design mockups that were publicly downloadable from `/static/`.
* New `db_health_check.py` — read-only, safe while the app runs, exit code 1 when something needs attention.

## Database (34 tables, 612 columns, 884 KB, integrity OK, schema.sql matches the live database exactly)
Needs a decision / cleanup (real data, so not changed automatically):
1. 3 trips where payments + advance exceed the bill: ids 374 (UNION_JUNE_57), 458 (ATS-5978), 473 (TNGR26002461).
2. LR number `ATS-N/A` is on two trips (342 and 413) — a placeholder used as if it were a real LR.
3. 4 rows point at vehicles that no longer exist: 3 `vehicle_compliance` rows (vehicle 71) and 1 `insurance_policies` row (vehicle 73).
4. 64 `created_by`/`updated_by` values point at deleted users (3 and 4). Harmless today; blocks ever switching foreign keys on.
Structural:
5. Foreign keys are not enforced (`PRAGMA foreign_keys` is off). Fix items 3–4 first, then turn it on.
6. 144 columns across tables with data have never been filled in; 25 columns of `trips` are always empty/zero (driver_payment, conductor_expense, wear_tear, owner_amount, pending_to_owner, permit_charges, …). `trips` has 80 columns — split charges, deductions and owner terms into child tables when convenient.
7. Money is stored as floating point: fine at this size, but half-paise drift appears in sums; integer paise is the safe long-term form.
8. 3 empty tables (`invoices`, `salary_items`, `maintenance_items`) are still referenced by code — keep or retire.
9. 308 of 452 maintenance bills have no vendor.

## Code (14,157 lines in one `app.py`: 420 functions, 189 routes)
Priority order (value / effort):
1. Split `app.py` into modules (auth, trips, ledger/payments, maintenance, invoices, employees, analytics, mobile `/app`). Largest single maintainability win; do it one module at a time with the audit suite as the safety net.
2. Break up the five giant functions: `_build_invoice_pdf` 581 lines, `business_performance` 345, `dashboard` 300, `app_dashboard` 212, `_get_vendor_ledger_entries` 212.
3. Website vs mobile twins: login/app_login, verify_otp pairs, invoice_batch_pdf vs _preview (88% identical) — extract shared helpers like `_update_trip_from_form`.
4. 81 `SELECT *` / `SELECT t.*` — `_period_financials` pulls all 80 trip columns once per month; naming the ~20 it needs would cut Business Performance (0.5–0.9 s) roughly in half.
5. 41 functions run SQL inside loops (N+1) — worst remaining: Business Performance, Dashboard trend, employees overview, fleet utilisation. Fast enough today (< 0.5 s) but they scale with data.
6. ~475 inline SQL call sites: a small data-access layer would make the Postgres move (schema_postgres.sql) mechanical.
7. Tests: 12 ad-hoc scripts + the audit harness → one pytest run in CI.
8. Still open from the first security audit: `@admin_required` on `/settings/*`, rate limiting, SECRET_KEY handling, rotate Twilio/RC keys.

## UI fonts and styling
* Three font families are named but only one is ever loaded: the website and most of the app use the system stack; Poppins is loaded only on the 4 mobile login pages (weights 400/500/600) yet is referenced by `app.css` for the splash and brand; Sora is referenced by the page loader but never loaded (it exists only in the archived mockup). Anything that asks for them silently falls back.
* Weights 700 and 800 are used ~400 times but Poppins is loaded only up to 600, so those render as faked bold.
* 58 different font sizes (1,154 declarations) — e.g. 0.76 / 0.78 / 0.8 / 0.82 / 0.85 / 0.86 / 0.88 rem are used 40–118 times each. An 8-step scale (0.68, 0.75, 0.82, 0.9, 1.0, 1.15, 1.4, 1.8 rem) would cover 83% of them without any visible change.
* 156 distinct hex colours, 33 inline `<style>` blocks (137 KB), 70 inline `<script>` blocks (277 KB), 1,763 inline `style=""` attributes — none of it cacheable and it blocks ever adding a strict Content-Security-Policy.
Recommendation: pick one family (system stack is fastest; Poppins gives the brand look but needs loading on every page), define `--font-sans` and `--fs-1…8` tokens in `static/style.css` / `app.css`, then convert declarations file by file; move inline CSS/JS into cached static files.

## Performance (12,000 trips, 144 screens)
Screens: 109 of 123 under 100 ms and none over 1 s; the slowest is Business Performance (0.9 s with a long date range). Downloads: Trips PDF of all 12,000 trips 5.0 s and `/export` 1.1 s are the only slow ones. See `load_test_report.md`.
