"""
Shared HTTP client for the eChallan Vehicle RC Lookup API (api.echallan.app) — the one real,
live data source this project currently has an actual paid key for.

Two independent consumers share this module rather than each parsing the API's response shape
themselves:
  - app.py's on-demand "View RC Details" panel (vehicles_list.html's RC button) — shows every
    field the API returns, read-only, nothing persisted.
  - VahanProvider / PucProvider / PermitProvider (this package) — each pulls just its own
    slice (fitness / puc / permit) out of the same response to feed the existing
    vehicle_compliance sync (compliance_service.sync_vehicle), so the Vehicles list's
    Insurance/Fitness/PUC/Permit badges reflect real data once a key is configured, same as
    they always have — nothing in compliance_service.py or the UI had to change shape for this.

One eChallan call returns registration + owner + spec + insurance + PUC + permit data all at
once, so a single fetch() here can serve all three providers without three separate paid calls.
"""
import datetime
import requests

RC_LOOKUP_URL = 'https://api.echallan.app/vahanfin/vehicle'
CHALLAN_LOOKUP_URL = 'https://api.echallan.app/vahanfin/echallan'


def fetch_rc(vehicle_no, api_key):
    """Calls the API for one registration number. Returns {'ok': bool, 'error': str|None,
    'data': dict|None} — 'data' is the raw field set (rc_regn_no, rc_fit_upto, rc_owner_name,
    ...) exactly as the API returns it, unmodified, so every consumer reads the same values."""
    if not api_key:
        return {'ok': False, 'error': 'No RC Lookup API key configured. Add one in Settings > Vehicle RC Lookup.', 'data': None}
    try:
        resp = requests.get(
            RC_LOOKUP_URL,
            params={'rc_no': vehicle_no, 'refresh': 'false'},
            headers={'X-API-Key': api_key},
            timeout=20,
        )
        payload = resp.json()
    except requests.exceptions.RequestException as e:
        return {'ok': False, 'error': f'Could not reach the RC lookup service ({e}).', 'data': None}
    except ValueError:
        return {'ok': False, 'error': 'RC lookup service returned an unreadable response.', 'data': None}

    if not isinstance(payload, dict):
        return {'ok': False, 'error': 'Unexpected response from RC lookup service.', 'data': None}

    # Invalid key / auth failure shape: HTTP 401 + {"error":"Unauthorized - Please login"}
    if resp.status_code == 401 or payload.get('error'):
        msg = payload.get('error') or payload.get('message') or 'Invalid or unauthorized API key.'
        return {'ok': False, 'error': f'{msg} Check the key in Settings > Vehicle RC Lookup.', 'data': None}

    # Generic error shape: {"status":"error","message":"..."}
    if payload.get('status') == 'error':
        return {'ok': False, 'error': payload.get('message') or 'RC lookup failed.', 'data': None}

    # "No Records Found" (and possibly other queued/nested) shape:
    # {"response":[{"response":{...},"responseStatus":"..."}],"code":"200"}
    if isinstance(payload.get('response'), list):
        first = payload['response'][0] if payload['response'] else {}
        inner = first.get('response') if isinstance(first, dict) else None
        if isinstance(inner, dict) and 'rc_regn_no' not in inner:
            return {'ok': False, 'error': inner.get('message') or 'No records found for this vehicle.', 'data': None}
        if isinstance(inner, dict):
            payload = inner

    # First-time lookup / not-yet-verified shape: the record was just queued with the upstream
    # RTO service and only a stub (rc_regn_no + status placeholder) is available yet.
    if payload.get('verification_pending') or payload.get('provider_unavailable'):
        return {'ok': False, 'error': payload.get('message') or 'This vehicle is being verified with the RTO service. Please check back shortly.', 'data': None}

    if not payload.get('rc_regn_no'):
        return {'ok': False, 'error': 'No data returned for this vehicle.', 'data': None}

    return {'ok': True, 'error': None, 'data': payload}


def fetch_challans(vehicle_no, api_key):
    """Calls the eChallan Challan Lookup endpoint (a different endpoint from fetch_rc — pending/
    disposed traffic challans, not RC/registration data). Returns {'ok': bool, 'error': str|None,
    'data': dict|None} — 'data' is the raw payload: {'challans': [...], 'pending_count': int,
    'disposed_count': int, 'total_count': int, '_billing': {...}}, exactly as the API returns it."""
    if not api_key:
        return {'ok': False, 'error': 'No RC Lookup API key configured. Add one in Settings > Vehicle RC Lookup.', 'data': None}
    try:
        resp = requests.get(
            CHALLAN_LOOKUP_URL,
            params={'rc_no': vehicle_no, 'refresh': 'false', 'dispose': 'false'},
            headers={'X-API-Key': api_key},
            timeout=20,
        )
        payload = resp.json()
    except requests.exceptions.RequestException as e:
        return {'ok': False, 'error': f'Could not reach the challan lookup service ({e}).', 'data': None}
    except ValueError:
        return {'ok': False, 'error': 'Challan lookup service returned an unreadable response.', 'data': None}

    if not isinstance(payload, dict):
        return {'ok': False, 'error': 'Unexpected response from challan lookup service.', 'data': None}

    if resp.status_code == 401 or payload.get('error'):
        msg = payload.get('error') or payload.get('message') or 'Invalid or unauthorized API key.'
        return {'ok': False, 'error': f'{msg} Check the key in Settings > Vehicle RC Lookup.', 'data': None}

    if payload.get('status') == 'error':
        return {'ok': False, 'error': payload.get('message') or 'Challan lookup failed.', 'data': None}

    if payload.get('verification_pending') or payload.get('provider_unavailable'):
        return {'ok': False, 'error': payload.get('message') or 'This vehicle is being verified with the RTO service. Please check back shortly.', 'data': None}

    if 'challans' not in payload:
        return {'ok': False, 'error': 'No challan data returned for this vehicle.', 'data': None}

    return {'ok': True, 'error': None, 'data': payload}


def parse_challan_date(value):
    """eChallan's challan_date_time has been observed in two shapes: 'DD-MM-YYYY HH:MM:SS'
    (the format shown in the API's own example responses) and ISO 8601 with a trailing 'Z'
    (what live calls have actually returned). Tries both rather than trusting either
    documentation or one live sample alone. Returns (date_iso, time_str) or (None, None) if
    neither matches."""
    if not value:
        return None, None
    for fmt in ('%d-%m-%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%fZ'):
        try:
            dt = datetime.datetime.strptime(value, fmt)
            return dt.date().isoformat(), dt.strftime('%I:%M %p')
        except ValueError:
            continue
    return None, None


def parse_rc_date(value):
    """eChallan dates come as 'DD-Mon-YYYY' (e.g. '16-Mar-2027'). Converts to the app's
    standard 'YYYY-MM-DD', or None if missing/unparseable — never raises, since a single bad
    date shouldn't take down a whole sync."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, '%d-%b-%Y').date().isoformat()
    except ValueError:
        return None
