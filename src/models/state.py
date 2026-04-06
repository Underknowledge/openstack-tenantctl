"""State-tracking models for floating IPs and router IPs."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openstack.network.v2.floating_ip import FloatingIP


@dataclasses.dataclass(frozen=True)
class FipEntry:
    """A pre-allocated floating IP snapshot persisted to state."""

    id: str
    address: str
    port_id: str | None = None
    fixed_ip_address: str | None = None
    status: str | None = None
    router_id: str | None = None
    created_at: str | None = None
    device_id: str | None = None
    device_owner: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FipEntry:
        """Create from a raw state dict, ignoring unknown keys."""
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_sdk(cls, fip: FloatingIP) -> FipEntry:
        """Create from an openstacksdk FloatingIP object.

        Extracts ``device_id`` and ``device_owner`` from the ``port_details``
        attribute (provided by the ``fip-port-details`` Neutron extension).
        """
        port_details = getattr(fip, "port_details", None) or {}
        return cls(
            id=fip.id,
            address=fip.floating_ip_address,
            port_id=fip.port_id,
            fixed_ip_address=fip.fixed_ip_address,
            status=fip.status,
            router_id=fip.router_id,
            created_at=fip.created_at,
            device_id=port_details.get("device_id"),
            device_owner=port_details.get("device_owner"),
        )

    def to_dict(self) -> dict[str, str | None]:
        """Return a dict for YAML state persistence."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


@dataclasses.dataclass(frozen=True)
class ReleasedFipEntry:
    """A floating IP that was released (lost or reclaimed by another project)."""

    address: str
    released_at: str
    reason: str
    port_id: str | None = None
    device_id: str | None = None
    device_owner: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReleasedFipEntry:
        """Create from a raw state dict, ignoring unknown keys."""
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, str | None]:
        """Return a dict for YAML state persistence."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


@dataclasses.dataclass(frozen=True)
class RouterIpEntry:
    """A router external IP snapshot persisted to state."""

    id: str
    name: str
    external_ip: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouterIpEntry:
        """Create from a raw state dict."""
        return cls(id=data["id"], name=data["name"], external_ip=data["external_ip"])

    def to_dict(self) -> dict[str, str]:
        """Return a dict for YAML state persistence."""
        return {"id": self.id, "name": self.name, "external_ip": self.external_ip}


@dataclasses.dataclass(frozen=True)
class ReleasedRouterIpEntry:
    """A router IP that was released (router removed or IP changed)."""

    address: str
    router_name: str
    released_at: str
    reason: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReleasedRouterIpEntry:
        """Create from a raw state dict."""
        return cls(
            address=data["address"],
            router_name=data["router_name"],
            released_at=data["released_at"],
            reason=data["reason"],
        )

    def to_dict(self) -> dict[str, str]:
        """Return a dict for YAML state persistence."""
        return {
            "address": self.address,
            "router_name": self.router_name,
            "released_at": self.released_at,
            "reason": self.reason,
        }
