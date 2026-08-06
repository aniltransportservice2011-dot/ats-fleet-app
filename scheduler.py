"""
Weekly compliance sync scheduler — the only place in this codebase that calls the compliance
providers' fetch() automatically. Every other read path (Vehicles page, badges, alerts) reads
straight from the database; this module is what keeps that database current.

Runs every Sunday at 2:00 AM: syncs Fitness/PUC/Permit for the whole own-fleet, compares each
result against what was already on file, and logs a summary. The per-vehicle "Refresh
Compliance" button and the Vehicles page's "Sync Compliance" button call the same
compliance_service functions on demand — this scheduler is just the unattended weekly trigger.
"""
import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import compliance_service as cs

logger = logging.getLogger('compliance_scheduler')
_scheduler = None


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


def start_scheduler(get_db_fn):
    """Call once, from app.py's `if __name__ == '__main__':` guard — never at import time, and
    never once per gunicorn worker, so the job only ever registers a single time."""
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
    _scheduler.start()
    logger.info("Compliance scheduler started — next weekly sync: %s", _scheduler.get_job('weekly_compliance_sync').next_run_time)
    return _scheduler
