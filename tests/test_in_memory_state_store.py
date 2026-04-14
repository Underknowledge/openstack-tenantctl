"""Tests for InMemoryStateStore."""

from __future__ import annotations

import dataclasses
import ipaddress
from enum import StrEnum

import pytest

from src.state_store import InMemoryStateStore, StateStore


class _Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class TestInMemoryStateStore:
    """Unit tests for InMemoryStateStore."""

    def test_load_empty(self) -> None:
        """Unknown key returns empty dict."""
        store = InMemoryStateStore()
        assert store.load("nonexistent") == {}

    def test_save_and_load(self) -> None:
        """Round-trip for top-level keys."""
        store = InMemoryStateStore()
        store.save("proj-a", ["preallocated_fips"], [{"id": "fip-1"}])
        state = store.load("proj-a")
        assert state == {"preallocated_fips": [{"id": "fip-1"}]}

    def test_save_nested_key_path(self) -> None:
        """Nested key_path creates intermediate dicts."""
        store = InMemoryStateStore()
        store.save("proj-a", ["metadata", "project_id"], "abc-123")
        state = store.load("proj-a")
        assert state == {"metadata": {"project_id": "abc-123"}}

    def test_save_overwrites(self) -> None:
        """Second save replaces value at same key_path."""
        store = InMemoryStateStore()
        store.save("proj-a", ["router_ips"], ["10.0.0.1"])
        store.save("proj-a", ["router_ips"], ["10.0.0.2"])
        state = store.load("proj-a")
        assert state == {"router_ips": ["10.0.0.2"]}

    def test_load_returns_deep_copy(self) -> None:
        """Mutation of returned dict does not affect internal store."""
        store = InMemoryStateStore()
        store.save("proj-a", ["items"], [1, 2, 3])

        loaded = store.load("proj-a")
        loaded["items"].append(4)
        loaded["extra"] = "injected"

        assert store.load("proj-a") == {"items": [1, 2, 3]}

    def test_initial_data(self) -> None:
        """Constructor pre-seeding populates store."""
        initial = {"proj-a": {"preallocated_fips": [{"id": "fip-1"}]}}
        store = InMemoryStateStore(initial=initial)
        assert store.load("proj-a") == {"preallocated_fips": [{"id": "fip-1"}]}

    def test_initial_data_is_copied(self) -> None:
        """Mutation of initial dict does not affect store."""
        initial: dict[str, dict[str, object]] = {"proj-a": {"key": "original"}}
        store = InMemoryStateStore(initial=initial)

        initial["proj-a"]["key"] = "mutated"
        initial["proj-b"] = {"key": "new"}

        assert store.load("proj-a") == {"key": "original"}
        assert store.load("proj-b") == {}

    def test_save_empty_key_path_raises(self) -> None:
        """Empty key_path raises ValueError."""
        store = InMemoryStateStore()
        with pytest.raises(ValueError, match="key_path must not be empty"):
            store.save("proj-a", [], "value")

    def test_snapshot_returns_all_state(self) -> None:
        """snapshot() returns all state keys."""
        store = InMemoryStateStore()
        store.save("proj-a", ["key"], "a")
        store.save("proj-b", ["key"], "b")

        snap = store.snapshot()
        assert snap == {
            "proj-a": {"key": "a"},
            "proj-b": {"key": "b"},
        }

    def test_snapshot_returns_deep_copy(self) -> None:
        """Mutation of snapshot does not affect store."""
        store = InMemoryStateStore()
        store.save("proj-a", ["items"], [1, 2])

        snap = store.snapshot()
        snap["proj-a"]["items"].append(3)
        snap["proj-c"] = {"injected": True}

        assert store.load("proj-a") == {"items": [1, 2]}
        assert store.load("proj-c") == {}

    def test_protocol_conformance(self) -> None:
        """InMemoryStateStore satisfies the StateStore protocol."""
        store = InMemoryStateStore()
        assert isinstance(store, StateStore)

    def test_save_enum_coercion(self) -> None:
        """StrEnum values are stored as plain strings."""
        store = InMemoryStateStore()
        store.save("proj-a", ["color"], _Color.RED)

        state = store.load("proj-a")
        assert state["color"] == "red"
        assert type(state["color"]) is str


class TestIpAddressCoercion:
    """save() automatically converts ipaddress objects to strings."""

    def test_ipv4_address_stored_as_string(self) -> None:
        """IPv4Address objects are coerced to string."""
        store = InMemoryStateStore()
        store.save("proj", ["gateway"], ipaddress.IPv4Address("192.168.1.1"))

        data = store.load("proj")
        assert data["gateway"] == "192.168.1.1"
        assert isinstance(data["gateway"], str)

    def test_ipv6_address_stored_as_string(self) -> None:
        """IPv6Address objects are coerced to string."""
        store = InMemoryStateStore()
        store.save("proj", ["gateway"], ipaddress.IPv6Address("::1"))

        data = store.load("proj")
        assert data["gateway"] == "::1"
        assert isinstance(data["gateway"], str)

    def test_ipv4_network_stored_as_string(self) -> None:
        """IPv4Network objects are coerced to string."""
        store = InMemoryStateStore()
        store.save("proj", ["cidr"], ipaddress.IPv4Network("10.0.0.0/24"))

        data = store.load("proj")
        assert data["cidr"] == "10.0.0.0/24"
        assert isinstance(data["cidr"], str)

    def test_ipv6_network_stored_as_string(self) -> None:
        """IPv6Network objects are coerced to string."""
        store = InMemoryStateStore()
        store.save("proj", ["cidr"], ipaddress.IPv6Network("2001:db8::/32"))

        data = store.load("proj")
        assert data["cidr"] == "2001:db8::/32"
        assert isinstance(data["cidr"], str)

    def test_snapshot_contains_serialized_values(self) -> None:
        """snapshot() returns ipaddress values as strings, not objects."""
        store = InMemoryStateStore()
        store.save("proj-a", ["gateway"], ipaddress.IPv4Address("10.0.0.1"))
        store.save("proj-b", ["cidr"], ipaddress.IPv4Network("192.168.0.0/16"))

        snap = store.snapshot()
        assert snap["proj-a"]["gateway"] == "10.0.0.1"
        assert snap["proj-b"]["cidr"] == "192.168.0.0/16"
        assert isinstance(snap["proj-a"]["gateway"], str)
        assert isinstance(snap["proj-b"]["cidr"], str)


@dataclasses.dataclass
class _FakeEntry:
    """Test dataclass for verifying rejection."""

    id: str
    value: int


class TestDataclassRejection:
    """save() rejects dataclass instances with a helpful error."""

    def test_dataclass_raises_type_error(self) -> None:
        """Dataclass instances raise TypeError."""
        store = InMemoryStateStore()
        entry = _FakeEntry(id="abc", value=42)

        with pytest.raises(TypeError, match="Cannot save dataclass instance _FakeEntry directly"):
            store.save("proj", ["entry"], entry)

    def test_error_message_suggests_to_dict(self) -> None:
        """TypeError message guides user to call .to_dict()."""
        store = InMemoryStateStore()
        entry = _FakeEntry(id="abc", value=42)

        with pytest.raises(TypeError, match=r"Call \.to_dict\(\) first"):
            store.save("proj", ["entry"], entry)

    def test_dataclass_dict_works_fine(self) -> None:
        """Calling .to_dict() on dataclass before save() works."""
        store = InMemoryStateStore()
        entry = _FakeEntry(id="abc", value=42)

        # Manual .to_dict() equivalent
        store.save("proj", ["entry"], {"id": entry.id, "value": entry.value})

        data = store.load("proj")
        assert data["entry"] == {"id": "abc", "value": 42}
