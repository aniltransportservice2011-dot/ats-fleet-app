"""
VAHAN provider — vehicle registration & fitness certificate lookups.

No real government VAHAN API key is configured anywhere in this project. Instead, when the
eChallan RC Lookup key (Settings > Vehicle RC Lookup) is configured, fetch() uses that as the
real, live data source for fitness — see providers/echallan_client.py for why one shared client
backs all three of VAHAN/PUC/Permit. Without a key, fetch() falls back to the same deterministic
mock response as before.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider
from providers.echallan_client import fetch_rc, parse_rc_date


class VahanProvider(ComplianceProvider):
    provider_key = 'vahan'
    label = 'VAHAN (Vehicle Registration)'

    def fetch(self, vehicle_no, document_number=None, api_key=None, rc_result=None):
        if rc_result is not None or api_key:
            # rc_result lets a caller pass in one already-fetched eChallan response so this
            # doesn't make its own HTTP call — see compliance_service.sync_vehicle(), which
            # fetches once per vehicle and hands the same result to all 3 providers instead of
            # each of Fitness/PUC/Permit paying for its own separate API credit.
            result = rc_result if rc_result is not None else fetch_rc(vehicle_no, api_key)
            if not result['ok']:
                raise RuntimeError(result['error'])
            d = result['data']
            return {
                'status': 'Valid' if d.get('rc_status') == 'ACTIVE' else 'Unknown',
                'expiry': parse_rc_date(d.get('rc_fit_upto')),
                'document_number': document_number,
                'issuing_authority': d.get('rc_registered_at'),
                'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'live',
            }

        # Deterministic mock: same vehicle always gets the same simulated expiry, so the UI
        # doesn't flicker between syncs, but different vehicles clearly differ from each other.
        seed = int(hashlib.md5(vehicle_no.encode()).hexdigest(), 16)
        days_out = 10 + (seed % 400)  # spread mock expiries across ~13 months, some already past
        expiry = (datetime.date.today() + datetime.timedelta(days=days_out - 200)).isoformat()
        return {
            'status': 'Valid' if days_out >= 200 else 'Expired',
            'expiry': expiry,
            'document_number': document_number or f"FC-{vehicle_no.replace(' ', '')}-{seed % 10000:04d}",
            'issuing_authority': 'RTO (Simulated)',
            'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'mock',
        }
