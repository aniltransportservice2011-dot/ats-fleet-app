"""
Insurance provider — INTENTIONALLY NOT used for storage. Real insurance data already lives in
the `insurance_policies` table (policy number, insurer, premium, IDV, agent, documents — a full
module, not a stub) and is the single source of truth everywhere else in this app (Vehicles >
Insurance tab, the vendor ledger, Overview's cost cards). Duplicating it into the generic
compliance table would let the two disagree, which this codebase has deliberately avoided
every time the same situation came up before (see the Insurance-tab/Overview reconciliation).

This provider exists purely as the future cross-check hook the spec asked for — e.g. verifying
a self-reported policy number against an insurer aggregator API — and is not called anywhere
in compliance_service.py today. It's here so that wiring one in later is additive.
"""
import datetime
from providers.base_provider import ComplianceProvider


class InsuranceProvider(ComplianceProvider):
    provider_key = 'insurance'
    label = 'Insurance Aggregator'

    def fetch(self, vehicle_no, document_number=None):
        if self.is_live():
            # TODO: real integration point once INSURANCE_API_KEY / base_url are configured.
            # Intended use: cross-check policy_number/expiry_date already on file in
            # insurance_policies, not to write new data — see module docstring above.
            raise NotImplementedError('Insurance live API not yet integrated — remove this branch once it is.')
        return {
            'status': 'Unknown',
            'expiry': None,
            'document_number': document_number,
            'issuing_authority': None,
            'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'mock',
            'note': 'Insurance is tracked in the Insurance tab, not synced through this provider.',
        }
