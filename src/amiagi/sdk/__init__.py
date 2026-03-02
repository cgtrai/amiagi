"""amiagi.sdk — public SDK for programmatic access to amiagi.

Usage::

    from amiagi.sdk import AmiagiClient

    client = AmiagiClient("http://localhost:8090", token="my-secret")
    agents = client.list_agents()
"""

from amiagi.infrastructure.sdk_client import AmiagiClient

__all__ = ["AmiagiClient"]
