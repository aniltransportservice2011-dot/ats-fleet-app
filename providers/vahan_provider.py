"""
VAHAN provider — vehicle registration & fitness certificate lookups.

No real government VAHAN API key is configured anywhere in this project, so `fetch()` always
returns a deterministic mock response for now (see ComplianceProvider.is_live()). The response
shape is exactly what a real integration would need to return, so swapping the mock body below
for a real `requests.get(...)` call is the only change needed later — nothing in
compliance_service.py or the UI has to change.
"""
import datetime
import hashlib
from providers.base_provider import ComplianceProvider


class VahanProvider(ComplianceProvider):
    provider_key = 'vahan'
    label = 'VAHAN (Vehicle Registration)'

    def fetch(self, vehicle_no, document_number=None):
        if self.is_live():
            # TODO: real integration point. Once VAHAN_API_KEY / base_url are configured,
            # replace this block with the actual HTTP call and map its response into the
            # same dict shape returned below.
            raise NotImplementedError('VAHAN live API not yet integrated — remove this branch once it is.')

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
