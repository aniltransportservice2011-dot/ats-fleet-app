# Anil Transport Service — Local Ledger App

A local web app that runs entirely on your computer. No internet, no subscription,
no cloud. Your data lives in one file: `fleet.db`.

## What's inside
- `fleet.db` — your actual data, already migrated from Fleet_Master_Tracker.xlsx
  (473 trips, 345 maintenance entries, 214 party payments, 1 vendor payment)
- `app.py` — the web app itself
- `templates/` — the two pages (home list, ledger detail)
- `migrate.py` / `schema.sql` — used to build the database; you won't need to run these again
  unless you want to re-import fresh data from an updated Excel file

## One-time setup (do this once)

1. Install Python if you don't already have it: https://www.python.org/downloads/
   (when installing on Windows, tick "Add Python to PATH")
2. Install what this app needs (Flask for the web app, plus Excel/PDF export support):
   Open a terminal / command prompt in this folder and run:
   ```
   pip install -r requirements.txt
   ```

## Running it (do this every time you want to use it)

1. Open a terminal / command prompt in this folder
2. Run:
   ```
   python app.py
   ```
3. Open your browser and go to: **http://127.0.0.1:5050**
4. Click "View Dashboard" for your key numbers, or click any party/vendor
   name for their full ledger.
5. From the Dashboard, use "Add Trip" or "Add Maintenance" to type in new
   entries directly — no Excel needed, saves instantly, shows up in the
   ledger immediately.
6. When you're done, go back to the terminal and press Ctrl+C to stop it.

## What's in this version
- Dashboard — Total Billed, Fuel, Driver Advance, Maintenance costs, Partial Profit
- Add Trip — a real form, saves directly to the database
- Add Maintenance — same idea
- Trips list, Maintenance list
- Party & Vendor ledgers with running balance (as before)

## Verified against your real numbers
Bright Steel's ledger shows exactly ₹3,29,359 — matching your Excel file.
Tested adding a real trip through the Add Trip form end-to-end: it saved
correctly and appeared in that party's ledger immediately, no extra step.

## Honest note on "Partial Profit"
This only subtracts Fuel, Driver Advance, and Maintenance from Billed — it
does not yet include Salaries or Overheads, since those aren't migrated into
this app yet. Treat it as directional, not your final profit figure, until
those are added.

## Not yet included (planned next)
Route Rates (Highest/Average/Lowest), Vehicles page, Salaries, Overheads.
Tell me when you're ready and I'll build these the same tested way.
