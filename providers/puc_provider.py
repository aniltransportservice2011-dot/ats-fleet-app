"""
PUC (Pollution Under Control) certificate provider. Same mock-until-configured contract as
every other provider in this package — see providers/vahan_provider.py for the pattern this
follows, and providers/base_provider.py for the interface it implements. Real data comes from
the shared eChallan client (providers/echallan_client.py) once the RC Lookup key is configured.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider
from providers.echallan_client import fetch_rc, parse_rc_date


class PucProvider(ComplianceProvider):
    provider_key = 'puc'
    label = 'PUC Certification'

    def fetch(self, vehicle_no, document_number=None, api_key=None, rc_result=None):
        if rc_result is not None or api_key:
            # See providers/vahan_provider.py — rc_result lets the caller share one already-
            # fetched eChallan response across all 3 providers instead of each paying for its
            # own API credit.
            result = rc_result if rc_result is not None else fetch_rc(vehicle_no, api_key)
            if not result['ok']:
                raise RuntimeError(result['error'])
            d = result['data']
            return {
                'status': 'Valid' if d.get('rc_pucc_upto') else 'Unknown',
                'expiry': parse_rc_date(d.get('rc_pucc_upto')),
                'document_number': d.get('rc_pucc_no') or document_number,
                'issuing_authority': None,
                'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'live',
            }

        # PUC certificates are short-lived (typically 3-6 months) in reality, so the mock uses
        # a tighter spread than VAHAN/permit to feel representative.
        seed = int(hashlib.md5(('puc:' + vehicle_no).encode()).hexdigest(), 16)
        days_out = 5 + (seed % 180)
        expiry = (datetime.date.today() + datetime.timedelta(days=days_out - 90)).isoformat()
        return {
            'status': 'Valid' if days_out >= 90 else 'Expired',
            'expiry': expiry,
            'document_number': document_number or f"PUC-{vehicle_no.replace(' ', '')}-{seed % 10000:04d}",
            'issuing_authority': 'Authorized PUC Center (Simulated)',
            'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'mock',
        }
