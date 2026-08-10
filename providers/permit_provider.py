"""
Transport permit provider (National/State/Temporary permit validity). Same mock-until-configured
contract as every other provider in this package — see providers/vahan_provider.py for the
pattern, and providers/base_provider.py for the interface. Real data comes from the shared
eChallan client (providers/echallan_client.py) once the RC Lookup key is configured.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider
from providers.echallan_client import fetch_rc, parse_rc_date

PERMIT_SUBTYPES = ['National Permit', 'State Permit', 'Temporary Permit']


class PermitProvider(ComplianceProvider):
    provider_key = 'permit'
    label = 'Permit Authority'

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
                'status': 'Valid' if d.get('rc_permit_valid_upto') else 'Unknown',
                'expiry': parse_rc_date(d.get('rc_permit_valid_upto')),
                'document_number': d.get('rc_permit_no') or document_number,
                'issuing_authority': d.get('rc_permit_issuing_authority'),
                'permit_subtype': d.get('rc_permit_type'),
                'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'live',
            }

        seed = int(hashlib.md5(('permit:' + vehicle_no).encode()).hexdigest(), 16)
        days_out = 15 + (seed % 500)
        expiry = (datetime.date.today() + datetime.timedelta(days=days_out - 250)).isoformat()
        return {
            'status': 'Valid' if days_out >= 250 else 'Expired',
            'expiry': expiry,
            'document_number': document_number or f"PMT-{vehicle_no.replace(' ', '')}-{seed % 10000:04d}",
            'issuing_authority': 'RTO (Simulated)',
            'permit_subtype': PERMIT_SUBTYPES[seed % len(PERMIT_SUBTYPES)],
            'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'mock',
        }
