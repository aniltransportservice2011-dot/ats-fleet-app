"""
Transport permit provider (National/State/Temporary permit validity). Same mock-until-configured
contract as every other provider in this package — see providers/vahan_provider.py for the
pattern, and providers/base_provider.py for the interface.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider

PERMIT_SUBTYPES = ['National Permit', 'State Permit', 'Temporary Permit']


class PermitProvider(ComplianceProvider):
    provider_key = 'permit'
    label = 'Permit Authority'

    def fetch(self, vehicle_no, document_number=None):
        if self.is_live():
            # TODO: real integration point once PERMIT_API_KEY / base_url are configured.
            raise NotImplementedError('Permit live API not yet integrated — remove this branch once it is.')

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
