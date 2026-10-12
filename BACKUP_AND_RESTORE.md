# Backups and restore (Render)

Two independent layers. Do both.

## Layer 1 — Render's own disk snapshots (automatic)
Render takes a snapshot of every persistent disk once every 24 hours and keeps it for at least 7 days.
**Needs:** the web service must have a **Persistent Disk** (Dashboard → your service → *Disks* → Add Disk, mount path e.g. `/var/data`) and the app
must keep its database on it: set the environment variable `DATABASE_PATH=/var/data/fleet.db` (and copy the current `fleet.db` there once).
Without a disk the database is erased at every deploy/restart.
**Check:** Dashboard → service → *Disks* → you should see a list of snapshots with dates.
**Restore (all-or-nothing, changes after the snapshot are lost):** Dashboard → service → *Disks* → pick a snapshot → *Restore*.
Use this for "everything went wrong, go back to yesterday".

## Layer 2 — your own off-site copy (daily, kept on your computer)
Render's disk can only be read from inside the web service, so the app offers a locked download, and a small script on your Mac pulls it.

1. **On Render** (service → *Environment*): add `BACKUP_TOKEN` = a long random secret (e.g. run `python3 -c "import secrets;print(secrets.token_urlsafe(40))"`). Save → the service redeploys.
   Without this variable the download refuses everyone.
2. **On your Mac**, test once by hand (replace the address and secret):
   ```bash
   cd /Users/beenash/Downloads/fleet_local_app
   BACKUP_URL=https://ats-fleet-app.onrender.com BACKUP_TOKEN='your-secret' python3 pull_backup.py
   ```
   Expected: `Backup OK: ~/Downloads/fleet_db_backups/render/render_YYYYMMDD_HHMMSS.db …`. The script checks the copy and refuses (exit code 1) if it
   fails the integrity check, is empty, or has lost more than 20% of its rows since the previous copy. It keeps the newest 30 copies.
3. **Run it every day automatically** (Mac). Create `~/Library/LaunchAgents/com.ats.fleetbackup.plist`:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0"><dict>
     <key>Label</key><string>com.ats.fleetbackup</string>
     <key>ProgramArguments</key><array>
       <string>/usr/bin/python3</string><string>/Users/beenash/Downloads/fleet_local_app/pull_backup.py</string></array>
     <key>EnvironmentVariables</key><dict>
       <key>BACKUP_URL</key><string>https://ats-fleet-app.onrender.com</string>
       <key>BACKUP_TOKEN</key><string>PASTE-YOUR-SECRET</string></dict>
     <key>StartCalendarInterval</key><dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
     <key>StandardOutPath</key><string>/tmp/fleetbackup.log</string>
     <key>StandardErrorPath</key><string>/tmp/fleetbackup.err</string>
   </dict></plist>
   ```
   then `launchctl load ~/Library/LaunchAgents/com.ats.fleetbackup.plist`. (A Mac that is asleep at 21:00 runs the job when it wakes.)
   Check `/tmp/fleetbackup.log` next morning; `/tmp/fleetbackup.err` means a backup failed.
4. Also copy `~/Downloads/fleet_db_backups/render` to somewhere that is not this Mac (iCloud Drive / Google Drive folder, or an external disk) — a backup on the same laptop is not a backup if the laptop is lost.

## Test that a backup really restores (do it now, then monthly)
```bash
python3 restore_check.py ~/Downloads/fleet_db_backups/render/render_YYYYMMDD_HHMMSS.db
```
It copies the backup to a scratch folder, health-checks it and starts the real app on it (login, dashboard, trips, ledger, invoices, expenses, settings). `RESULT: PASS` = this file can be restored. It never touches the live database.

## Restoring from your own copy onto Render (when a snapshot is not enough)
1. Run `restore_check.py` on the copy you intend to use. 2. Stop traffic (Render → *Suspend* the service). 3. Put the file on the disk through Render's SSH/Shell
(*Connect → SSH*; copy with `scp` to `/var/data/fleet.db`, and delete any `fleet.db-wal` / `fleet.db-shm` beside it). 4. *Resume*. The app adds its own
tables/indexes at start-up. 5. Sign in and spot-check the latest trip and the Ledger totals.
Keep the previous (bad) file as `fleet.db.bad` until you are sure.
