"""
Shared provider interface. Every compliance data source — mock or, later, real — implements
this exact shape so compliance_service.py never needs to know which provider it's talking to.

A real provider (once a real API key + base URL exist) would replace `fetch()`'s body with an
actual HTTP call; everything calling it stays unchanged.
"""


class ComplianceProvider:
    """Base class for a single compliance data source (VAHAN, PUC registry, permit authority, ...).

    Subclasses set `provider_key` (must match a key in config.apis.PROVIDER_DEFAULTS) and
    implement `fetch()`.
    """
    provider_key = None
    label = None

    def is_live(self):
        """True once a real API key is configured for this provider. Until then every fetch()
        call returns clearly-labeled mock data — the UI must never present mock data as real."""
        from config.apis import env_api_key
        return bool(env_api_key(self.provider_key))

    def fetch(self, vehicle_no, document_number=None):
        """Return a dict shaped exactly like:
        {
            'status': 'Valid' | 'Expired' | 'Unknown',
            'expiry': 'YYYY-MM-DD' or None,
            'document_number': str or None,
            'issuing_authority': str or None,
            'last_updated': 'YYYY-MM-DD HH:MM:SS',
            'source': 'mock' | 'live',
        }
        Raising is allowed on genuine failure — compliance_service.sync_vehicle() catches it
        and records sync_status='Failed' rather than corrupting existing data.
        """
        raise NotImplementedError
