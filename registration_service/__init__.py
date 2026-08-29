"""Reference central registration service for School CSM Internet Gateway.

This service stores infrastructure metadata only.  It never accepts or stores
survey responses, scanner images, narrative reports, or school data packages.
"""

from .core import RegistrationRepository, RegistrationService, ServiceError

__all__ = ["RegistrationRepository", "RegistrationService", "ServiceError"]
