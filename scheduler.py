"""
Background sync scheduler — the only place in this codebase that calls out to a real external
API automatically. Every other read path (Vehicles page, badges, alerts, the RC Lookup panel,
the eChallan tab) reads straight from the database; this module is what keeps that database
current.

Three jobs:
  - run_weekly_sync   — every Sunday 2 AM, fixed. Fitness/PUC/Permit for the whole own-fleet via
                         the mock/live ComplianceProvider system (unchanged from before).
  - run_rc_sync       — one eChallan RC Lookup call per own-fleet vehicle, caching the full
                         response into vehicles.rc_synced_data/rc_last_synced and backfilling
                         real gaps (registration date, an insurance policy).
  - run_challan_sync  — one eChallan Challan Lookup call per own-fleet vehicle, replacing that
                         vehicle's vehicle_challans rows with the latest fetched set and updating
                         vehicles.challan_count/challan_amount/challan_last_synced.

run_rc_sync and run_challan_sync are NOT registered on a fixed APScheduler interval — their
cadence (in days) is a Settings value (rc_sync_interval_days / challan_sync_interval_days) the
user can change at any time without restarting the app. A single lightweight tick job
(run_sync_tick) runs every SYNC_TICK_MINUTES and, on each tick, checks sync_log for when each
job last actually ran; if the configured number of days has elapsed, it runs that job now. This
is what makes the interval "live" — changing the Settings value takes effect on the next tick,
not on the next app restart.

Every real run of run_rc_sync / run_challan_sync writes one row to sync_log (job_name,
started_at, finished_at, synced/failed/skipped counts) — the audit trail the Settings page's
sync-history view reads.

Nothing here has been run against the live API yet in this codebase — run_rc_sync and
run_challan_sync should not be enabled until the eChallan API key and cost per call have been
deliberately confirmed, since both consume paid credits per vehicle.
"""
import datetime
import json
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import compliance_service as cs
from providers.echallan_client import fetch_rc, fetch_challans, parse_challan_date

logger = logging.getLogger('compliance_scheduler')
_scheduler = None

SYNC_TICK_MINUTES = 60


def _now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _log_sync_start(conn, job_name):
    cur = conn.execute("INSERT INTO sync_log (job_name, started_at) VALUES (?, ?)", (job_name, _now_str()))
    conn.commit()
    return cur.lastrowid


def _log_sync_finish(conn, log_id, synced, failed, skipped, note=None):
    conn.execute("UPDATE sync_log SET finished_at=?, synced_count=?, failed_count=?, skipped_count=?, note=? WHERE id=?",
                 (_now_str(), synced, failed, skipped, note, log_id))
    conn.commit()


def run_weekly_sync(get_db_fn):
    """The job body. Takes a get_db() callable rather than a live connection, since this runs
    on a background thread long after the request that started the app is gone."""
    conn = get_db_fn()
    try:
        summary = cs.sync_all_vehicles(conn)
        logger.info(
            "Weekly compliance sync: %d vehicle(s), %d synced (%d changed, %d unchanged), %d failed",
            summary['vehicles'], summary['synced'], summary['changed'], summary['unchanged'], summary['failed'])
        for c in summary['changes']:
            logger.info("  changed: %s %s  %s -> %s", c['vehicle_no'], c['compliance_type'], c['old_expiry'], c['new_expiry'])
        alerts = cs.get_compliance_alerts(conn)
        if alerts:
            logger.info("Weekly compliance sync: %d alert(s) now active (see Dashboard / Vehicles page).", len(alerts))
        return summary
    finally:
        conn.close()


def _parse_rc_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, '%d-%b-%Y').date().isoformat()
    except ValueError:
        return None


def _backfill_from_rc(conn, vehicle_id, vehicle_no, rc_data, now, created_by=None):
    """Fills real gaps from the synced RC response — never overwrites data that's already on
    file, since a manually-entered value is trusted over the cache.

    Only ever touches vehicles.registration_date (feeds the Age column), filled in only if
    currently blank.

    Deliberately does NOT touch insurance at all — an earlier version of this function also
    auto-created an insurance_policies row (and, to hold its insurer, a brand-new vendor record
    for whatever name the RC API returned as the insurance company) the first time RC data was
    synced for a vehicle with no policy on file. That's almost certainly what created a stray
    vendor literally named "None" on production: the manual "Sync Latest Data" button (app.py's
    vehicle_sync_now) reaches this same function and — unlike the scheduled sync job — writes
    nothing to sync_log, so that path was invisible to the investigation that first found that
    vendor. Insurance is a real, human-verified record (premium, IDV, agent, documents) — it
    should only ever be created by someone actually adding it via Vehicles > Insurance, never
    silently inferred from a lookup API's free-text company name field.
    """
    reg = conn.execute("SELECT registration_date FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if reg and not (reg['registration_date'] or '').strip():
        regn_dt = _parse_rc_date(rc_data.get('rc_regn_dt'))
        if regn_dt:
            conn.execute("UPDATE vehicles SET registration_date=?, updated_by=?, updated_at=? WHERE id=?",
                         (regn_dt, created_by, now, vehicle_id))


def run_rc_sync(get_db_fn):
    """One eChallan RC Lookup call per every own-fleet vehicle (hired vehicles are not
    owned, so their RC paperwork isn't this company's to track), caching the full response into
    vehicles.rc_synced_data (JSON) + rc_last_synced, then backfilling registration_date if it's
    still blank via _backfill_from_rc. A failed vehicle just keeps its last-known-good cache and
    gets retried next cycle — never blanks out existing data.
    Logs one row to sync_log. Returns a summary dict {'synced': n, 'failed': n, 'skipped': n}."""
    conn = get_db_fn()
    log_id = _log_sync_start(conn, 'rc_sync')
    try:
        key_row = conn.execute("SELECT value FROM settings WHERE key='rc_lookup_api_key'").fetchone()
        api_key = key_row['value'] if key_row else ''
        if not api_key:
            logger.info("RC sync skipped — no rc_lookup_api_key configured in Settings > Vehicle RC Lookup.")
            _log_sync_finish(conn, log_id, 0, 0, 0, note='No API key configured.')
            return {'synced': 0, 'failed': 0, 'skipped': 0}
        vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type IS NOT NULL AND type = 'own'").fetchall()
        synced = failed = 0
        now = _now_str()
        for v in vehicles:
            result = fetch_rc(v['vehicle_no'], api_key)
            if result['ok']:
                conn.execute("UPDATE vehicles SET rc_synced_data=?, rc_last_synced=?, updated_at=? WHERE id=?",
                             (json.dumps(result['data']), now, now, v['id']))
                _backfill_from_rc(conn, v['id'], v['vehicle_no'], result['data'], now, created_by=None)
                synced += 1
            else:
                logger.warning("RC sync failed for %s: %s", v['vehicle_no'], result['error'])
                failed += 1
        conn.commit()
        logger.info("RC sync: %d vehicle(s), %d synced, %d failed", len(vehicles), synced, failed)
        _log_sync_finish(conn, log_id, synced, failed, 0)
        return {'synced': synced, 'failed': failed, 'skipped': 0}
    finally:
        conn.close()


def run_challan_sync(get_db_fn):
    """One eChallan Challan Lookup call per every own-fleet vehicle. On success, that vehicle's
    vehicle_challans rows are fully replaced with the freshly-fetched set (the API returns the
    current state each time, not a delta, so replace-not-append is correct here) and
    vehicles.challan_count/challan_amount/challan_last_synced are updated from the response's
    pending_count and the sum of fine_imposed across Pending challans. A failed vehicle keeps
    whatever it last had. Logs one row to sync_log."""
    conn = get_db_fn()
    log_id = _log_sync_start(conn, 'challan_sync')
    try:
        key_row = conn.execute("SELECT value FROM settings WHERE key='rc_lookup_api_key'").fetchone()
        api_key = key_row['value'] if key_row else ''
        if not api_key:
            logger.info("Challan sync skipped — no rc_lookup_api_key configured in Settings > Vehicle RC Lookup.")
            _log_sync_finish(conn, log_id, 0, 0, 0, note='No API key configured.')
            return {'synced': 0, 'failed': 0, 'skipped': 0}
        vehicles = conn.execute("SELECT id, vehicle_no FROM vehicles WHERE type IS NOT NULL AND type = 'own'").fetchall()
        synced = failed = 0
        now = _now_str()
        for v in vehicles:
            result = fetch_challans(v['vehicle_no'], api_key)
            if result['ok']:
                data = result['data']
                challans = data.get('challans') or []
                conn.execute("DELETE FROM vehicle_challans WHERE vehicle_id=?", (v['id'],))
                pending_amount = 0.0
                for c in challans:
                    date_iso, time_str = parse_challan_date(c.get('challan_date_time'))
                    fine = c.get('fine_imposed')
                    try:
                        fine_val = float(fine) if fine not in (None, '') else None
                    except (TypeError, ValueError):
                        fine_val = None
                    if (c.get('challan_status') or '').lower() == 'pending' and fine_val:
                        pending_amount += fine_val
                    conn.execute("""INSERT INTO vehicle_challans
                        (vehicle_id, api_id, challan_no, challan_date_time, challan_place, challan_status,
                         fine_imposed, amount_of_fine_imposed, department, driver_name, name_of_violator,
                         owner_name, dl_no, document_impounded, remark, rto_distric_name, state_code,
                         court_name, court_address, date_of_proceeding, sent_to_court_on, sent_to_reg_court,
                         sent_to_virtual_court, offence_details, source_created_at, source_updated_at, last_synced,
                         created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (v['id'], c.get('_id'), c.get('challan_no'), c.get('challan_date_time'), c.get('challan_place'),
                         c.get('challan_status'), fine_val, c.get('amount_of_fine_imposed'), c.get('department'),
                         c.get('driver_name'), c.get('name_of_violator'), c.get('owner_name'), c.get('dl_no'),
                         c.get('document_impounded'), c.get('remark'), c.get('rto_distric_name'), c.get('state_code'),
                         c.get('court_name'), c.get('court_address'), c.get('date_of_proceeding'),
                         c.get('sent_to_court_on'), c.get('sent_to_reg_court'), c.get('sent_to_virtual_court'),
                         json.dumps(c.get('offence_details') or []), c.get('createdAt'), c.get('updatedAt'), now, now))
                pending_count = data.get('pending_count', sum(1 for c in challans if (c.get('challan_status') or '').lower() == 'pending'))
                conn.execute("UPDATE vehicles SET challan_count=?, challan_amount=?, challan_last_synced=?, updated_at=? WHERE id=?",
                             (pending_count, pending_amount, now, now, v['id']))
                synced += 1
            else:
                logger.warning("Challan sync failed for %s: %s", v['vehicle_no'], result['error'])
                failed += 1
        conn.commit()
        logger.info("Challan sync: %d vehicle(s), %d synced, %d failed", len(vehicles), synced, failed)
        _log_sync_finish(conn, log_id, synced, failed, 0)
        return {'synced': synced, 'failed': failed, 'skipped': 0}
    finally:
        conn.close()


def run_sync_tick(get_db_fn, enable_rc_sync, enable_challan_sync):
    """Runs every SYNC_TICK_MINUTES. Checks sync_log for each job's last real run and the
    currently-configured interval (Settings > rc_sync_interval_days / challan_sync_interval_days
    — editable at any time, no restart needed) and only actually syncs if that many days have
    elapsed. This is what makes the interval "live": changing the Settings value takes effect on
    the very next tick, since the threshold is read fresh every time, not baked into a fixed
    APScheduler trigger."""
    conn = get_db_fn()
    try:
        def _due(job_name, setting_key, default_days):
            interval_row = conn.execute("SELECT value FROM settings WHERE key=?", (setting_key,)).fetchone()
            try:
                interval_days = float(interval_row['value']) if interval_row and interval_row['value'] else default_days
            except ValueError:
                interval_days = default_days
            last = conn.execute("SELECT started_at FROM sync_log WHERE job_name=? ORDER BY id DESC LIMIT 1", (job_name,)).fetchone()
            if not last:
                return True
            last_dt = datetime.datetime.strptime(last['started_at'], '%Y-%m-%d %H:%M:%S')
            return (datetime.datetime.now() - last_dt).total_seconds() >= interval_days * 86400

        if enable_rc_sync and _due('rc_sync', 'rc_sync_interval_days', 15):
            conn.close()
            run_rc_sync(get_db_fn)
            conn = get_db_fn()
        if enable_challan_sync and _due('challan_sync', 'challan_sync_interval_days', 3):
            conn.close()
            run_challan_sync(get_db_fn)
            conn = get_db_fn()
    finally:
        conn.close()


def start_scheduler(get_db_fn, enable_rc_sync=False, enable_challan_sync=False):
    """Call once, from app.py's `if __name__ == '__main__':` guard — never at import time, and
    never once per gunicorn worker, so each job only ever registers a single time.
    enable_rc_sync / enable_challan_sync are independent opt-ins (see app.py) — the weekly
    compliance job runs regardless; the two eChallan-backed syncs (which spend real API credits)
    are each off unless explicitly turned on. Both share one tick job (run_sync_tick) rather
    than fixed per-job intervals, so their cadence stays editable from Settings at runtime."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_weekly_sync, args=[get_db_fn],
        trigger=CronTrigger(day_of_week='sun', hour=2, minute=0),
        id='weekly_compliance_sync', replace_existing=True,
        misfire_grace_time=3600,  # if the machine was off at 2 AM, still run within the hour it wakes
    )
    logger.info("Compliance scheduler started — next weekly sync: %s", _scheduler.get_job('weekly_compliance_sync').next_run_time)
    if enable_rc_sync or enable_challan_sync:
        _scheduler.add_job(
            run_sync_tick, args=[get_db_fn, enable_rc_sync, enable_challan_sync],
            trigger=IntervalTrigger(minutes=SYNC_TICK_MINUTES),
            id='sync_tick', replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("Sync tick job registered (checks every %d min; RC=%s, Challan=%s) — next check: %s",
                     SYNC_TICK_MINUTES, enable_rc_sync, enable_challan_sync, _scheduler.get_job('sync_tick').next_run_time)
    _scheduler.start()
    return _scheduler
