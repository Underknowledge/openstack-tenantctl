"""OpenStack TenantCtl - Project-as-Code for OpenStack.

Declarative tenant provisioning tool that enables IaaS by automating OpenStack
project creation, network setup, quota configuration, and access control.
"""

from __future__ import annotations

__version__ = "0.0.0-dev"  # Fallback for development

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("openstack-tenantctl")
except PackageNotFoundError:
    # Package not installed - use fallback
    pass
