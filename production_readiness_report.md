# Production readiness review — fleet app (11 Oct 2026)

**Status after the fixes of 11 Oct 2026 (afternoon): the five blocking security items (A1–A5) are fixed, re-attacked and
covered by permanent tests. Remaining before real users: the "verify on Render" items in section C (persistent disk, backups,
SECRET_KEY/keys rotation) and the B items marked OPEN.** The business logic (balances, ledgers, invoices, roles) is well tested and passes.

How each item was confirmed: the ones marked **PROVEN** were reproduced against a throw-away copy of the database (never the real fleet.db).

## A. Blocking security items — ALL FIXED (re-tested the same way they were broken; test: roles stage, case `security`)

| Item | Fixed how |
|---|---|
| A1 sign-up | Self-sign-up is closed (403) and the "Create Account" link is hidden. Accounts are created by an Administrator only. (`ALLOW_SELF_SIGNUP=1` switches it back on.) |
| A2 SMS login | Code is kept on the server (keyed hash), never in the cookie; 5 wrong tries kills the code; max 3 codes per number / 10 per address; a used code can't be reused; unknown numbers get the same reply; `secrets` instead of `random`. New tables `login_otps`, `auth_attempts` (self-created at start-up, in both schema files). |
| A3 secret key | The fixed key in the source is gone. `SECRET_KEY` env var is used if set; otherwise a random private key is generated once and stored in the database. A cookie forged with the old key is rejected. **Everyone is signed out once.** |
| A4 password guessing | 5 wrong passwords per account (30 per address) in 15 min → locked for the rest of the window, website and app. New/changed passwords must be 8+ characters. |
| A5 documents | Insurance and toll-receipt files need a login (anonymous → 403); logos stay public. |

### Original findings (for the record) — all PROVEN against a throw-away copy before the fix

| # | Risk | What happens | Fix |
|---|---|---|---|
| A1 | **Anyone can sign up** (`/app/signup` is public, account is Active at once, "Read Only" can still *view* everything) | A stranger created an account and opened Accounts (balances), Trips, App ledger. Full business data exposure. | Turn self-signup off (or require an Administrator to approve/activate). |
| A2 | **SMS (OTP) login can be bypassed** — the code's hash sits in the browser cookie, which is readable | Requested a code for the Administrator's number, read the cookie, recovered the 6-digit code in 0.02 s, logged in as Administrator **without ever receiving the SMS**. Also no limit on attempts or on SMS sends (cost abuse, number enumeration). | Keep the OTP hash on the server (DB table) with expiry, 5-attempt limit, send rate limit; use `secrets` not `random`; same message whether or not the number exists. |
| A3 | **Default SECRET_KEY is in the code** (`fleet-local-app-anil-transport-secret-key-2026`) | If Render does not have its own `SECRET_KEY` env var, anyone who has seen the code can forge a login cookie. Forged an Administrator cookie in the test — worked. *(Cannot see Render's env from here — VERIFY.)* | Set a long random `SECRET_KEY` on Render; make the app refuse to start in production without it. |
| A4 | **No limit on password guesses** | 30 wrong passwords in a row, all accepted as attempts, no lockout/slow-down. Passwords only need 6 characters. | Rate-limit login per IP + per username (e.g. 5 tries then 15-min wait); raise minimum to 8. |
| A5 | **Uploaded documents are public by URL** (insurance papers, toll receipts live under `/static/`, which needs no login) | Anyone who gets/guesses a file link can open it. Logos are fine to be public; insurance documents should not be. | Serve documents through a login-checked route (outside `static/`). |

## B. Should fix soon (security hardening)

| # | Risk | Fix |
|---|---|---|
| B1 | **FIXED (cheap form):** cookie is now SameSite=Lax (+Secure on Render) and a POST that names another website as its origin is refused (403). Full CSRF tokens still not added. **No CSRF protection** on any form (cookie has no `SameSite`). A malicious web page opened by a logged-in user could submit actions (delete a trip, add a user…). | Set `SESSION_COOKIE_SAMESITE='Lax'`, `SESSION_COOKIE_SECURE=True` on Render; add CSRF tokens (Flask-WTF) for POSTs. |
| B2 | **OPEN — needs your OK** (rewrites GitHub history). **11 old database backups are committed to git** (`fleet.db.bak.*`) — real business data and password hashes sit in the repo history on GitHub. | Confirm repo is Private; remove from history (`git filter-repo`) and rotate all passwords afterwards. |
| B3 | **FIXED** (nosniff, frame-deny, referrer policy, HSTS on https; no CSP yet). **No security headers** (no clickjacking protection, no `nosniff`, no HSTS, no CSP). | Add a small `after_request` header set. |
| B4 | **OPEN — you must do it** (Twilio is gone — its saved keys were deleted; rotate/disable the old Twilio account in Twilio's console, and rotate the RC provider key). **Twilio token / RC API key** were shown in the website settings form earlier and sit unencrypted in the DB. | Rotate both keys (also needed because of A2). Now admin-only and masked in the app. |
| B5 | **FIXED** (only local paths accepted). **Open redirect**: `return_to` form field is redirected to without checking it is a local path (5 places). Low risk, needs login. | Accept only paths starting with `/`. |
| B6 | **FIXED 11 Oct (evening): Limited Access is enforced per screen (View/Edit), see roles-stage case `limited_access`.** **Limited Access is not enforced** — the Settings screen lets you pick it, but those users get full access. Misleading permission. | Either enforce the module list or remove the option until built. |
| B7 | **FIXED** (16 MB cap). **No upload size limit** (`MAX_CONTENT_LENGTH` unset) — one huge upload can fill memory/disk. | Set e.g. 10 MB. |
| B8 | Dev-server default is `debug=True` when started with `python app.py` (Render uses gunicorn, so fine there). | Default to off; require `FLASK_DEBUG=1` explicitly. |

Checked and OK: every page and POST needs login (anonymous requests redirect to login); Read Only is enforced on the server for all writes; Administrator-only screens/endpoints are blocked for Managers (tested); SQL is parameterised (the f-string SQL I found only substitutes fixed table names / placeholder lists); the 8 `|safe` template uses are all fixed icons; no hardcoded API keys in the code; passwords are hashed; idle logout after 3 h.

## C. Data safety and operations (verify on Render)

1. **Is the database on a persistent disk?** SQLite is one file. On Render, without a paid *persistent disk*, the file is wiped on each redeploy/restart. If this is not already a mounted disk, you will lose data. **VERIFY FIRST.**
2. **No automatic backups.** A tested script now exists (`python3 backup_db.py`: safe online backup, verified, keeps the newest 30) — schedule it daily and copy the folder off the machine. For Render you still need its own backup/disk snapshot. One file, no copy elsewhere. Needs a daily backup (SQLite `.backup`, not `cp`) to somewhere off the server, and one tested restore.
3. **Background sync does not run under gunicorn.** RC / eChallan / compliance sync needs an external cron calling `/internal/sync-tick` with `INTERNAL_SYNC_TOKEN` set (the endpoint correctly refuses without the token).
4. **`requirements.txt` has no version numbers.** A rebuild can silently pull a newer Flask/ReportLab and break something. Pin versions.
5. **Database migrations at deploy**: schema changes now self-apply at start-up (tested incl. two workers starting together). Always take a backup first (the app does this automatically for the overhead-allocation step).
6. **SQLite limits**: fine for the tested load (12,000 trips, 10 simultaneous users, ~1.7 s slowest-95%). Beyond roughly 25–30 people editing at once, or heavy report use, plan the move to Postgres (`schema_postgres.sql` is ready but has not been run against a live Postgres).
7. **Logging/monitoring**: no error alerting. A crash is invisible until someone complains. Add an uptime check and error email/Sentry.
8. Make sure the Render account has **one user with role exactly `Administrator`** (and it is the owner) *before* deploying the roles change, or nobody can open Settings.

## D. Code-breaking risk (tests)

Everything below was run today against throw-away databases:
- 17 balance/ledger audit stages (≈ 920,000 checks): **0 failures**; remaining differences are the known *business-definition* items (below), not bugs.
- 12 older regression suites: all pass. Load test (12,000 trips, 144 screens): all under 3 s, 76 of 87 website screens "Excellent".
- Roles/access: 115 checks incl. the new Administrator app screens: 0 failures.

Not covered by any test: the security items in A/B (found by this review), real phones/browsers (only one desktop browser and emulated phone size were looked at), backup/restore, Render's actual environment, and the live Postgres schema.

**Known design differences still waiting for your decision** (not bugs, but screens can show different totals): Ledger vs Trips differ when a party has an opening balance or an unallocated payment; invoice grand total vs ledger "Others" lines; merged party+vendor ledgers have no "pay owner" popup; hired-vehicle maintenance is excluded from Maintenance Overview totals; employee "repaid more than given" cases.

Leftover data items: trip 374 over-payment ₹434, duplicate LR `ATS-N/A` (trips 342/413), mock compliance rows on vehicles 71/73.

## E. Speed / UI

- Business Performance with a long date range ~1.0 s; Dashboard 0.27 s; Trips PDF of *all* trips ~5 s; `/export` ~1.1 s (a heavy export blocks one of 8 worker threads while it runs).
- **Route Finder "Route Stops" takes 7–30 s** — it depends on the free public Overpass server (busy/rate-limited, sometimes fails). Fix options: keep a 7-day saved copy of searched routes, or a paid provider.
- Mobile/phone layout: one emulated phone size was checked; test on at least one real iPhone and one Android before launch.
- Sidebar open at phone width squeezes pages (seen in the emulated browser; not specific to one page) — worth a look.
- Fonts: system font vs Poppins and spacing tokens are still inconsistent across pages (cosmetic).

## F. Maintainability (not a launch blocker)

`app.py` is ~14,300 lines with some very large functions and `SELECT *` in places (see `code_health_report.md`); splitting it will make future changes safer. The Postgres plan, multi-tenant `company_id` rules and hard-coded company name in 7 PDFs are tracked separately.

## Suggested order

1. A1–A5 (≈ half a day of work, then re-test) + close the old Twilio account + rotate the RC key + set SECRET_KEY on Render.
2. C1 (persistent disk) and C2 (daily off-server backup) — before any real data goes in.
3. B1–B3, B7, pin requirements, uptime alert.
4. Then go live with a small group, watch for a week.
