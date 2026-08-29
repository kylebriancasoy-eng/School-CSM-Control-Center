"""Optional Internet Gateway support for the offline-first Control Center.

Importing this package never performs network access.  A deployment becomes
Internet-capable only after a non-secret provider configuration is installed,
the school is registered, and the operator explicitly connects the gateway.
"""

from .config import GatewayProviderConfig, load_gateway_provider_config
from .client import (
    GatewayAuthorization,
    GatewayClientError,
    GatewayProviderClient,
    verify_authorization_envelope,
)
from .tunnel import CloudflaredTunnel, TunnelComponentError, TunnelSnapshot

__all__ = [
    "CloudflaredTunnel",
    "GatewayAuthorization",
    "GatewayClientError",
    "GatewayProviderClient",
    "GatewayProviderConfig",
    "TunnelComponentError",
    "TunnelSnapshot",
    "load_gateway_provider_config",
    "verify_authorization_envelope",
]
