"""
Compliance API configuration.

Every credential here is read from an environment variable — never hardcoded, never committed.
The Settings > API Integrations screen persists overrides (base URL, sync frequency, and a
key that's saved *encrypted-at-rest-by-obscurity-only for this local app*) into the existing
`settings` key/value table, the same store every other Settings tab already uses. Env vars are
only the fallback default a fresh install starts from.

Adding a new provider later (Road Tax, e-Challan, FASTag, GPS, Fuel Card) means adding one more
entry to PROVIDER_DEFAULTS and one more providers/<name>_provider.py — nothing here or in
compliance_service.py needs to change shape to support it.
"""
import os

# One entry per compliance provider. `env_key` is the environment variable that seeds the
# initial API key on a fresh database (Settings can override it afterwards). `types` lists
# which compliance_type value(s) this provider is responsible for syncing.
PROVIDER_DEFAULTS = {
    'vahan': {
        'label': 'VAHAN (Vehicle Registration)',
        'env_key': 'VAHAN_API_KEY',
        'base_url': 'https://api.vahan.example.gov.in/v1',  # placeholder — real gov endpoint TBD
        'sync_frequency_hours': 24,
        'types': ['fitness'],
    },
    'insurance': {
        'label': 'Insurance Aggregator',
        'env_key': 'INSURANCE_API_KEY',
        'base_url': 'https://api.insurance-aggregator.example.com/v1',
        'sync_frequency_hours': 24,
        'types': [],  # insurance is read from insurance_policies (already a full module) —
                       # this provider exists for a future cross-check/verification API, not storage.
    },
    'puc': {
        'label': 'PUC Certification',
        'env_key': 'PUC_API_KEY',
        'base_url': 'https://api.puc-registry.example.com/v1',
        'sync_frequency_hours': 24,
        'types': ['puc'],
    },
    'permit': {
        'label': 'Permit Authority',
        'env_key': 'PERMIT_API_KEY',
        'base_url': 'https://api.permit-authority.example.com/v1',
        'sync_frequency_hours': 24,
        'types': ['permit'],
    },
}


def env_api_key(provider_key):
    """The environment-variable fallback for a provider's API key. Empty string if unset —
    every provider treats an empty key as "no live connection, use mock data"."""
    meta = PROVIDER_DEFAULTS.get(provider_key)
    if not meta:
        return ''
    return os.environ.get(meta['env_key'], '') or ''
