"""
Compliance sync + status engine for Fitness, PUC and Permit (Insurance is handled by the
existing Insurance module — see providers/insurance_provider.py for why it's excluded here).

Scope: only the company's own fleet (vehicles.type IN ('Line','Local')) — Market vehicles are
hired, not owned, so their compliance paperwork isn't this company's responsibility to track,
matching the same own-fleet filter Maintenance's Overview tab has always used.

Three entry points, matching the brief:
  - sync_vehicle(conn, vehicle_id)   — call every provider for one vehicle, upsert results.
  - sync_all_vehicles(conn)          — sync_vehicle() for the whole own-fleet. This is the
                                        expensive, "network call" path — what the nightly
                                        scheduler runs.
  - refresh_compliance(conn)         — cheap, DB-only. Recomputes Valid/Expiring/Expired
                                        status for every vehicle from whatever's already
                                        stored (synced or manually entered). This is what
                                        every page read (Vehicles list, dashboard, alerts)
                                        calls — never a provider/network call on a page view.
"""
import datetime

from providers.vahan_provider import VahanProvider
from providers.puc_provider import PucProvider
from providers.permit_provider import PermitProvider

# One provider instance per compliance_type this service actually syncs. Insurance is
# deliberately absent — see providers/insurance_provider.py.
_PROVIDERS = {
    'fitness': VahanProvider(),
    'puc': PucProvider(),
    'permit': PermitProvider(),
}

# Alert thresholds per the spec — each compliance type gets its own warning window rather than
# one blanket number, since a PUC certificate lapsing is a same-week problem but a permit
# renewal can be planned a month out.
ALERT_WARN_DAYS = {
    'insurance': 30,
    'fitness': 15,
    'puc': 7,
    'permit': 30,
}

COMPLIANCE_TYPE_LABELS = {'insurance': 'Insurance', 'fitness': 'Fitness Certificate', 'puc': 'PUC', 'permit': 'Permit'}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def status_for_expiry(expiry_str, warn_days):
    """Valid / Expiring Soon / Expired / Unknown for one date against its own warning window.
    Mirrors the app's existing _expiry_bucket() convention (app.py) but takes a per-type
    window instead of a fixed 30 days, since Insurance/Fitness/PUC/Permit each have their own
    threshold here."""
    if not expiry_str:
        return 'Unknown'
    try:
        exp = datetime.datetime.strptime(expiry_str, '%Y-%m-%d').date()
    except ValueError:
        return 'Unknown'
    today = datetime.date.today()
    if exp < today:
        return 'Expired'
    if (exp - today).days <= warn_days:
        return 'Expiring Soon'
    return 'Valid'


def days_left_for_expiry(expiry_str):
    """Signed day count to an expiry date — negative once it's passed. None if there's no date
    to compute from. Kept separate from status_for_expiry() since the UI wants both the bucket
    label and the exact count (e.g. "14 days overdue")."""
    if not expiry_str:
        return None
    try:
        exp = datetime.datetime.strptime(expiry_str, '%Y-%m-%d').date()
    except ValueError:
        return None
    return (exp - datetime.date.today()).days


def own_fleet_vehicles(conn):
    """Line/Local vehicles only — see module docstring."""
    return conn.execute("SELECT id, vehicle_no, type FROM vehicles WHERE type IN ('Line','Local') ORDER BY vehicle_no").fetchall()


def sync_vehicle(conn, vehicle_id):
    """Call every provider (fitness/puc/permit) for one vehicle, compare each result against
    what was already on file, and upsert vehicle_compliance. Insurance is included in the
    results too — but never provider-synced, see providers/insurance_provider.py — its entry
    just re-confirms the current status already computed from the real Insurance module, so
    every sync summary reports on all 4 types without a second system ever being able to
    disagree with the real one.

    Returns {compliance_type: {'outcome': 'ok'|'failed'|'skipped', 'changed': bool,
                                'old_expiry': str|None, 'new_expiry': str|None}}
    so callers (sync_all_vehicles, the per-vehicle Refresh Compliance button, the weekly
    scheduler) can report exactly what changed, not just that something ran.
    """
    vehicle = conn.execute("SELECT id, vehicle_no, insurance_expiry FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not vehicle:
        return {}
    results = {}
    for ctype, provider in _PROVIDERS.items():
        existing = conn.execute(
            "SELECT id, document_number, valid_upto FROM vehicle_compliance WHERE vehicle_id=? AND compliance_type=?",
            (vehicle_id, ctype)).fetchone()
        old_expiry = existing['valid_upto'] if existing else None
        try:
            data = provider.fetch(vehicle['vehicle_no'], document_number=existing['document_number'] if existing else None)
            new_expiry = data.get('expiry')
            now = _now()
            if existing:
                conn.execute("""UPDATE vehicle_compliance SET document_number=?, issuing_authority=?,
                                permit_subtype=?, valid_upto=?, source=?, provider_name=?,
                                last_sync_time=?, sync_status='Synced', updated_at=? WHERE id=?""",
                             (data.get('document_number'), data.get('issuing_authority'),
                              data.get('permit_subtype'), new_expiry, data.get('source'),
                              provider.label, now, now, existing['id']))
            else:
                conn.execute("""INSERT INTO vehicle_compliance
                                (vehicle_id, compliance_type, document_number, issuing_authority,
                                 permit_subtype, valid_upto, source, provider_name, last_sync_time,
                                 sync_status, created_at, updated_at)
                                VALUES (?,?,?,?,?,?,?,?,?,'Synced',?,?)""",
                             (vehicle_id, ctype, data.get('document_number'), data.get('issuing_authority'),
                              data.get('permit_subtype'), new_expiry, data.get('source'),
                              provider.label, now, now, now))
            results[ctype] = {'outcome': 'ok', 'changed': old_expiry != new_expiry,
                               'old_expiry': old_expiry, 'new_expiry': new_expiry}
        except Exception as e:
            # A failed sync never touches existing data — it only marks sync_status so the UI
            # can show "last sync failed" without losing whatever was last known-good.
            now = _now()
            if existing:
                conn.execute("UPDATE vehicle_compliance SET sync_status='Failed', last_sync_time=?, updated_at=? WHERE id=?",
                             (now, now, existing['id']))
            results[ctype] = {'outcome': 'failed', 'changed': False, 'old_expiry': old_expiry,
                               'new_expiry': None, 'error': str(e)}

    # Insurance — read-only re-confirmation, not a provider sync. See docstring above and
    # providers/insurance_provider.py for why this never writes anywhere.
    results['insurance'] = {'outcome': 'skipped', 'changed': False,
                             'old_expiry': vehicle['insurance_expiry'], 'new_expiry': vehicle['insurance_expiry'],
                             'note': 'Insurance is managed in the Insurance tab, not synced here.'}
    return results


def sync_all_vehicles(conn):
    """Weekly-job entry point (also used by the manual 'Sync Compliance' button). Syncs every
    own-fleet vehicle's Fitness/PUC/Permit, commits once at the end, and returns a summary —
    including how many records actually *changed* vs just got re-confirmed — used by both the
    scheduler's log and the on-page result banner."""
    vehicles = own_fleet_vehicles(conn)
    summary = {'synced': 0, 'changed': 0, 'unchanged': 0, 'failed': 0, 'vehicles': len(vehicles), 'changes': []}
    for v in vehicles:
        results = sync_vehicle(conn, v['id'])
        for ctype, r in results.items():
            if ctype == 'insurance':
                continue  # not provider-synced, see sync_vehicle() — excluded from these counts
            if r['outcome'] == 'ok':
                summary['synced'] += 1
                if r['changed']:
                    summary['changed'] += 1
                    summary['changes'].append({'vehicle_id': v['id'], 'vehicle_no': v['vehicle_no'],
                                                'compliance_type': ctype, 'old_expiry': r['old_expiry'],
                                                'new_expiry': r['new_expiry']})
                else:
                    summary['unchanged'] += 1
            else:
                summary['failed'] += 1
    conn.commit()
    return summary


def refresh_compliance(conn):
    """Cheap, DB-only. One row per own-fleet vehicle with a computed status for each of the
    4 compliance types, ready for the Vehicles list badges / Compliance dashboard / alerts.
    Insurance status is read straight off vehicles.insurance_expiry (kept in sync by the real
    Insurance module already) — never re-derived from a second source.
    """
    vehicles = own_fleet_vehicles(conn)
    comp_rows = conn.execute("SELECT * FROM vehicle_compliance").fetchall()
    comp_by_vehicle = {}
    for r in comp_rows:
        comp_by_vehicle.setdefault(r['vehicle_id'], {})[r['compliance_type']] = dict(r)

    vfull = {v['id']: v for v in conn.execute(
        "SELECT id, vehicle_no, type, insurance_expiry, fitness_expiry, puc_valid_upto, permit_valid_upto FROM vehicles WHERE type IN ('Line','Local')").fetchall()}

    out = []
    for v in vehicles:
        vid = v['id']
        full = vfull.get(vid)
        comp = comp_by_vehicle.get(vid, {})

        # Fitness/PUC/Permit prefer a synced vehicle_compliance record's date; fall back to the
        # manually-entered vehicles.* column so vehicles nobody has synced yet still show real
        # data instead of "Unknown" — never fabricated, always one or the other real source.
        fitness_exp = (comp.get('fitness') or {}).get('valid_upto') or full['fitness_expiry']
        puc_exp = (comp.get('puc') or {}).get('valid_upto') or full['puc_valid_upto']
        permit_exp = (comp.get('permit') or {}).get('valid_upto') or full['permit_valid_upto']
        insurance_exp = full['insurance_expiry']

        out.append({
            'vehicle_id': vid, 'vehicle_no': v['vehicle_no'], 'type': v['type'],
            'insurance': {'expiry': insurance_exp, 'status': status_for_expiry(insurance_exp, ALERT_WARN_DAYS['insurance']),
                          'days_left': days_left_for_expiry(insurance_exp)},
            'fitness': {'expiry': fitness_exp, 'status': status_for_expiry(fitness_exp, ALERT_WARN_DAYS['fitness']),
                        'days_left': days_left_for_expiry(fitness_exp),
                        'document_number': (comp.get('fitness') or {}).get('document_number'),
                        'sync_status': (comp.get('fitness') or {}).get('sync_status'),
                        'last_sync_time': (comp.get('fitness') or {}).get('last_sync_time')},
            'puc': {'expiry': puc_exp, 'status': status_for_expiry(puc_exp, ALERT_WARN_DAYS['puc']),
                    'days_left': days_left_for_expiry(puc_exp),
                    'document_number': (comp.get('puc') or {}).get('document_number'),
                    'sync_status': (comp.get('puc') or {}).get('sync_status'),
                    'last_sync_time': (comp.get('puc') or {}).get('last_sync_time')},
            'permit': {'expiry': permit_exp, 'status': status_for_expiry(permit_exp, ALERT_WARN_DAYS['permit']),
                       'days_left': days_left_for_expiry(permit_exp),
                       'permit_subtype': (comp.get('permit') or {}).get('permit_subtype'),
                       'document_number': (comp.get('permit') or {}).get('document_number'),
                       'sync_status': (comp.get('permit') or {}).get('sync_status'),
                       'last_sync_time': (comp.get('permit') or {}).get('last_sync_time')},
        })
    return out


def get_compliance_alerts(conn):
    """Flat list of real alerts — one per (vehicle, compliance type) that's Expired or within
    its warning window — for the Compliance Dashboard and the Vehicles page's alert strip."""
    rows = refresh_compliance(conn)
    alerts = []
    for r in rows:
        for ctype in ('insurance', 'fitness', 'puc', 'permit'):
            info = r[ctype]
            if info['status'] in ('Expiring Soon', 'Expired') and info['expiry']:
                days = (datetime.datetime.strptime(info['expiry'], '%Y-%m-%d').date() - datetime.date.today()).days
                alerts.append({
                    'vehicle_id': r['vehicle_id'], 'vehicle_no': r['vehicle_no'],
                    'compliance_type': ctype, 'label': COMPLIANCE_TYPE_LABELS[ctype],
                    'expiry': info['expiry'], 'status': info['status'], 'days_left': days,
                })
    alerts.sort(key=lambda a: a['days_left'])
    return alerts
