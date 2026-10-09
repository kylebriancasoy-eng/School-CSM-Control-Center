"""Windows host for the district-wide School CSM registration service.

This package is intentionally separate from the school Control Center desktop
application.  It owns only district registration metadata and the local
loopback service used by the district Internet Gateway.
"""

from __future__ import annotations


APPLICATION_NAME = "School CSM District Management Server"
APPLICATION_ID = "MoSSLab.SchoolCSMDistrictManagementServer"


__all__ = ["APPLICATION_ID", "APPLICATION_NAME"]
