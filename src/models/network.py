"""Network and subnet configuration models."""

from __future__ import annotations

import dataclasses
import ipaddress
from typing import Any


@dataclasses.dataclass(frozen=True)
class AllocationPool:
    """A subnet allocation pool range."""

    start: str
    end: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AllocationPool:
        """Create from a raw dict."""
        return cls(start=data["start"], end=data["end"])

    def to_dict(self) -> dict[str, str]:
        """Return a dict for the OpenStack API."""
        return {"start": self.start, "end": self.end}


@dataclasses.dataclass(frozen=True)
class SubnetConfig:
    """Subnet configuration within a network."""

    cidr: str
    gateway_ip: str
    allocation_pools: list[AllocationPool]
    dns_nameservers: list[str] = dataclasses.field(default_factory=list)
    enable_dhcp: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubnetConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            cidr=data["cidr"],
            gateway_ip=data.get("gateway_ip", ""),
            allocation_pools=[AllocationPool.from_dict(p) for p in data.get("allocation_pools", [])],
            dns_nameservers=data.get("dns_nameservers", []),
            enable_dhcp=data.get("enable_dhcp", data.get("dhcp", True)),
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> SubnetConfig | None:
        """Validate *data* and return a ``SubnetConfig``, or ``None`` if broken."""
        cidr_str = data.get("cidr")
        if not isinstance(cidr_str, str):
            errors.append(f"{label}: missing required field 'network.subnet.cidr'")
            return None

        network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
        try:
            network = ipaddress.ip_network(cidr_str, strict=True)
        except ValueError as exc:
            errors.append(f"{label}: invalid CIDR '{cidr_str}': {exc}")

        gateway_str = data.get("gateway_ip")
        if isinstance(gateway_str, str) and network is not None:
            try:
                gateway = ipaddress.ip_address(gateway_str)
                if gateway not in network:
                    errors.append(f"{label}: gateway {gateway_str} is not inside CIDR {cidr_str}")
            except ValueError as exc:
                errors.append(f"{label}: invalid gateway IP '{gateway_str}': {exc}")

        errors_before = len(errors)
        pools = data.get("allocation_pools")
        if isinstance(pools, list) and network is not None:
            for idx, pool in enumerate(pools):
                if not isinstance(pool, dict):
                    errors.append(f"{label}: allocation_pools[{idx}] is not a mapping")
                    continue
                start_str = pool.get("start")
                end_str = pool.get("end")
                if not isinstance(start_str, str) or not isinstance(end_str, str):
                    errors.append(f"{label}: allocation_pools[{idx}] missing 'start' or 'end'")
                    continue
                try:
                    start_ip = ipaddress.ip_address(start_str)
                except ValueError as exc:
                    errors.append(f"{label}: allocation_pools[{idx}].start '{start_str}' is invalid: {exc}")
                    continue
                try:
                    end_ip = ipaddress.ip_address(end_str)
                except ValueError as exc:
                    errors.append(f"{label}: allocation_pools[{idx}].end '{end_str}' is invalid: {exc}")
                    continue
                if start_ip not in network:
                    errors.append(
                        f"{label}: allocation_pools[{idx}].start " f"{start_str} is not inside CIDR {cidr_str}"
                    )
                if end_ip not in network:
                    errors.append(f"{label}: allocation_pools[{idx}].end " f"{end_str} is not inside CIDR {cidr_str}")
                if int(start_ip) > int(end_ip):
                    errors.append(f"{label}: allocation_pools[{idx}] start {start_str} > end {end_str}")

        if len(errors) > errors_before:
            return None

        return cls.from_dict(data)


@dataclasses.dataclass(frozen=True)
class NetworkConfig:
    """Network stack configuration."""

    subnet: SubnetConfig
    mtu: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkConfig:
        """Create from a pre-validated dict. Use ``validate()`` for untrusted input."""
        return cls(
            subnet=SubnetConfig.from_dict(data["subnet"]),
            mtu=data.get("mtu", 0),
        )

    @classmethod
    def validate(cls, data: dict[str, Any], errors: list[str], label: str) -> NetworkConfig | None:
        """Validate *data* and return a ``NetworkConfig``, or ``None`` if broken."""
        mtu = data.get("mtu")
        if mtu is not None and not isinstance(mtu, int):
            errors.append(f"{label}: network.mtu must be an integer, got {type(mtu).__name__}")

        subnet_data = data.get("subnet")
        if not isinstance(subnet_data, dict):
            errors.append(f"{label}: missing required field 'network.subnet.cidr'")
            return None

        subnet = SubnetConfig.validate(subnet_data, errors, label)
        if subnet is None:
            return None

        return cls(subnet=subnet, mtu=mtu if isinstance(mtu, int) else 0)
