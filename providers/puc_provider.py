"""
PUC (Pollution Under Control) certificate provider. Same mock-until-configured contract as
every other provider in this package — see providers/vahan_provider.py for the pattern this
follows, and providers/base_provider.py for the interface it implements.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider


class PucProvider(ComplianceProvider):
    provider_key = 'puc'
    label = 'PUC Certification'

    def fetch(self, vehicle_no, document_number=None):
        if self.is_live():
            # TODO: real integration point once PUC_API_KEY / base_url are configured.
            raise NotImplementedError('PUC live API not yet integrated — remove this branch once it is.')

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
