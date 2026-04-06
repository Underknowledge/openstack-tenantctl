"""Pre-allocated resources with quota enforcement.

Pre-allocates floating IPs and enforces network quotas, then tracks
allocated resource IDs in the config file for drift detection.
"""

from src.resources.prealloc.fip import ensure_preallocated_fips
from src.resources.prealloc.network import ensure_preallocated_network

__all__ = ["ensure_preallocated_fips", "ensure_preallocated_network"]
