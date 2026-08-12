# Fleet App — Project Conventions

Flask + SQLite fleet management app for Anil Transport Service. Currently single-company
(Phase 0); designed to become a multi-tenant SaaS later (Phase 1) without a redesign. Full
context: `/Users/beenash/.claude/plans/whimsical-conjuring-parrot.md` (approved architecture)
and the `fleet-app-golive-roadmap` project memory (current progress).

## The one rule that matters most: don't quietly undo the multi-tenancy prep

Every table already has a `company_id` column (defaulting to 1 today — see `schema.sql` and
`migrate_company_id.sql`). A parallel, real Postgres schema with actual tenant isolation
(`schema_postgres.sql`) is written and offline-verified, ready for when Phase 1 starts for
real. None of this changes how the app behaves today — but new code can silently erode it if
it doesn't follow the same pattern. Concretely, when adding anything new:

- **New tables** get a `company_id INTEGER NOT NULL DEFAULT 1` column, same as every existing
  table. Add the matching column to `schema_postgres.sql` too (there: `NOT NULL REFERENCES
  companies(id)`, no default, plus an index and — if the table needs a natural-key UNIQUE
  constraint — make it `UNIQUE(company_id, ...)` from the start, not a bare `UNIQUE`. Bare
  `UNIQUE` on a natural key (a name, a number, a username) is exactly the bug that breaks the
  moment a second company exists — five of these already had to be fixed once
  (`vehicles.vehicle_no`, `parties.name`, `vendors.name`, `employees.name`, `users.username`).
- **New settings** go through `_get_invoice_settings()` / `_get_all_settings()` /
  `get_company_name()` (app.py) — all three already accept a `company_id` parameter (default
  1). Call them with `session.get('company_id', 1)`, not a bare call, so they're already
  correct once real multi-company sessions exist. Don't add a new ad-hoc
  `SELECT value FROM settings WHERE key=...` elsewhere without the same `company_id` reasoning.
- **New file uploads** follow the pattern in `_save_toll_receipt()` / `_save_insurance_doc()` /
  the logo upload route — save under `uploads/<type>/<company_id>/...`, directory created on
  demand, `company_id` from `session.get('company_id', 1)`.
- **Never hardcode "Anil Transport Service"** (or its address/GSTIN/phone) into a new PDF or
  page. Seven existing spots still do this and are tracked as a known gap (multi-tenancy Step
  E) — don't add an eighth. Use `get_company_name(session.get('company_id', 1))` and the
  settings functions above instead.
- **New raw queries** in app.py don't need a `WHERE company_id=?` yet (the app doesn't read
  `session['company_id']` anywhere yet — that starts at multi-tenancy Step B) but should be
  written in a style that makes adding one later mechanical, not a rewrite — a simple `WHERE`
  with `AND`-able conditions, not something that needs restructuring to add a filter.

## Testing discipline

Every change that touches the database — schema or data — gets tested against a **disposable
copy** first, never the real `fleet.db` directly:

```bash
cp fleet.db /path/to/scratchpad/test_x.db
# test against test_x.db, monkeypatch get_db() to point at it
# only apply to the real fleet.db once fully verified
```

Back up the real `fleet.db` before any migration touches it; delete the backup only after the
migration is verified against the real file too (row counts match, app smoke-tests pass).

For anything that would call a real paid external API (eChallan RC/challan lookup, Twilio SMS),
hard-block `requests.get`/`requests.post` during tests (monkeypatch to raise) so a bug can never
accidentally spend real API credits or send a real SMS during testing.

## Deployment discipline

See the `deploy-db-and-app-separately` memory — database changes and application code changes
are always deployed as two separate, independent steps, never bundled into one "push and
restart." Migrate and verify the database first; only then deploy the code that depends on it.

## Key files

| File | What it is |
|---|---|
| `schema.sql` | Authoritative current SQLite schema — regenerate from the live DB (`sqlite3 fleet.db ".schema"`) if it ever drifts, don't hand-edit it out of sync with reality |
| `schema_postgres.sql` | The future Postgres schema — company_id enforced for real, Row-Level Security, composite uniques. Written, offline-verified (`pglast`), **not yet tested against a live database** |
| `db.py` | Connection wrapper — the one place a future DB engine swap actually happens; don't bypass it with a raw `sqlite3.connect()` elsewhere |
| `migrate_company_id.sql` | The migration that added `company_id` to the live SQLite database (already applied — this is a historical record, not something to re-run) |
