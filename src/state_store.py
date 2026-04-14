"""Separate state store for observed runtime state.

Moves runtime-observed state (FIP IDs/addresses, router IPs, release audit
trails) out of project config YAML into a dedicated state file per project.
Config files remain purely declarative.

The ``StateStore`` protocol allows swapping the YAML-file backend for a
database-backed implementation when a Customer-facing API arrives.

Thread Safety & Concurrency:
    ``YamlFileStateStore`` uses file-based locking to prevent race conditions
    in concurrent access scenarios (e.g., multiple CLI invocations, CI/CD
    pipelines). Each state file has an associated lock file (``.lock``)
    acquired during both read and write operations. Atomic file replacement
    (write-temp-then-rename) ensures readers never see partial writes.
"""

from __future__ import annotations

import dataclasses
import enum
import ipaddress
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

import yaml
from filelock import FileLock

__all__ = ["InMemoryStateStore", "StateStore", "YamlFileStateStore"]

logger = logging.getLogger(__name__)

STATE_KEYS: frozenset[str] = frozenset(
    {
        "preallocated_fips",
        "released_fips",
        "router_ips",
        "released_router_ips",
    }
)


def _serialize_value(value: Any) -> Any:
    """Coerce complex types to JSON-serializable primitives.

    Automatically converts known complex types to ensure consistent behavior
    across StateStore implementations:

    - ``enum.Enum`` → ``.value`` (plain string/int/etc.)
    - ``ipaddress`` objects → ``str()`` representation
    - Dataclass instances → ``TypeError`` (use ``.to_dict()`` explicitly)

    Args:
        value: The value to serialize.

    Returns:
        A JSON-serializable version of the value.

    Raises:
        TypeError: If *value* is a dataclass instance (must call ``.to_dict()``).
    """
    # Enum → plain value
    if isinstance(value, enum.Enum):
        return value.value

    # ipaddress objects → string representation
    if isinstance(
        value,
        (
            ipaddress.IPv4Address,
            ipaddress.IPv6Address,
            ipaddress.IPv4Network,
            ipaddress.IPv6Network,
        ),
    ):
        return str(value)

    # Dataclass instances → reject with helpful error
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        msg = (
            f"Cannot save dataclass instance {type(value).__name__} directly. "
            f"Call .to_dict() first: store.save(key, path, obj.to_dict())"
        )
        raise TypeError(msg)

    # Everything else (primitives, dicts, lists) passes through
    return value


@runtime_checkable
class StateStore(Protocol):
    """Protocol for reading/writing per-project observed state.

    **Value Serialization:**
        Values passed to ``save()`` are automatically coerced to ensure
        consistent behavior across implementations:

        - ``enum.Enum`` → ``.value``
        - ``ipaddress.IPv4Address``, ``IPv6Address``, networks → ``str()``
        - Dataclass instances → ``TypeError`` (use ``.to_dict()`` explicitly)

        Primitives (str, int, float, bool, None) and containers (list, dict)
        pass through unchanged.
    """

    def load(self, state_key: str) -> dict[str, Any]: ...

    def save(self, state_key: str, key_path: list[str], value: Any) -> None: ...


class YamlFileStateStore:
    """YAML-file-backed implementation of ``StateStore``.

    State files live at ``<state_dir>/<state_key>.state.yaml``.
    The directory is created lazily on first save.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir

    def _state_path(self, state_key: str) -> Path:
        return self._state_dir / f"{state_key}.state.yaml"

    def _lock_path(self, state_key: str) -> Path:
        """Return the lock file path for a given state file.

        Lock files are placed alongside state files with .lock extension.
        Example: proj.state.yaml -> proj.state.yaml.lock
        """
        return self._state_dir / f"{state_key}.state.yaml.lock"

    def _acquire_lock(self, state_key: str) -> FileLock:
        """Create a FileLock for the state file.

        Returns a FileLock instance (not yet acquired). Use as context manager:
            with self._acquire_lock(state_key):
                # perform locked operation

        Args:
            state_key: Identifies the state file to lock.

        Returns:
            FileLock instance with 30s timeout.

        Raises:
            Timeout: If lock cannot be acquired within 30 seconds.
        """
        return FileLock(self._lock_path(state_key), timeout=30)

    def load(self, state_key: str) -> dict[str, Any]:
        """Read state from YAML file with file locking.

        Acquires an exclusive lock before reading to prevent reading during
        a concurrent write operation. Returns empty dict if file is missing.

        Args:
            state_key: Identifies the state file to load.

        Returns:
            State dictionary, or empty dict if file doesn't exist.

        Raises:
            Timeout: If lock cannot be acquired within 30 seconds.
        """
        path = self._state_path(state_key)
        if not path.exists():
            return {}

        with self._acquire_lock(state_key):
            try:
                with path.open(encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except yaml.YAMLError:
                logger.warning(
                    "Corrupted state file %s — will be regenerated on next save",
                    path,
                )
                return {}
            return data if isinstance(data, dict) else {}

    def save(self, state_key: str, key_path: list[str], value: Any) -> None:
        """Read-modify-write a nested key with file locking and atomic writes.

        Acquires an exclusive lock, performs the read-modify-write operation,
        and uses atomic file replacement (write-temp-then-rename) to ensure
        no partial writes are visible to concurrent readers.

        Args:
            state_key: Identifies the state file (config file stem).
            key_path: List of keys to traverse, e.g. ``["preallocated_fips"]``.
            value: The value to set at the nested key path.

        Raises:
            ValueError: If *key_path* is empty.
            Timeout: If lock cannot be acquired within 30 seconds.
        """
        if not key_path:
            msg = "key_path must not be empty"
            raise ValueError(msg)

        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_path(state_key)

        with self._acquire_lock(state_key):
            # Read-Modify-Write inside locked section
            if path.exists():
                try:
                    with path.open(encoding="utf-8") as fh:
                        data: dict[str, Any] = yaml.safe_load(fh) or {}
                except yaml.YAMLError:
                    logger.warning(
                        "Corrupted state file %s — overwriting with clean data",
                        path,
                    )
                    data = {}
            else:
                data = {}

            # Modify in-memory
            current = data
            for key in key_path[:-1]:
                if key not in current or not isinstance(current[key], dict):
                    current[key] = {}
                current = current[key]

            # Coerce complex types to JSON-serializable primitives
            current[key_path[-1]] = _serialize_value(value)

            # Atomic write: write to temp file, then atomic rename
            temp_path = path.with_suffix(".yaml.tmp")
            with temp_path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)
            temp_path.replace(path)  # atomic on all platforms

        logger.debug(
            "Saved state %s = %r in %s",
            ".".join(key_path),
            value,
            path,
        )


class InMemoryStateStore:
    """Dict-backed ``StateStore`` for programmatic / library use.

    No filesystem I/O — state lives in a plain ``dict[str, dict]``.
    Pre-seed with *initial* to inject state loaded from an external system
    (database, REST API, etc.).  After reconciliation, call
    :meth:`snapshot` to bulk-read all updated state for write-back.
    """

    def __init__(self, initial: dict[str, dict[str, Any]] | None = None) -> None:
        import copy

        self._data: dict[str, dict[str, Any]] = copy.deepcopy(initial) if initial else {}

    def load(self, state_key: str) -> dict[str, Any]:
        """Return state for *state_key*, or ``{}`` if not present.

        Returns a deep copy so callers cannot mutate internal state.
        """
        import copy

        return copy.deepcopy(self._data.get(state_key, {}))

    def save(self, state_key: str, key_path: list[str], value: Any) -> None:
        """Set a nested key inside *state_key*.

        Semantics mirror :meth:`YamlFileStateStore.save`: *key_path* is
        traversed left-to-right, creating intermediate dicts as needed.

        Raises:
            ValueError: If *key_path* is empty.
        """
        if not key_path:
            msg = "key_path must not be empty"
            raise ValueError(msg)

        data = self._data.setdefault(state_key, {})

        current = data
        for key in key_path[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]

        current[key_path[-1]] = _serialize_value(value)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of **all** stored state, keyed by state_key.

        Use this after ``TenantCtl.run()`` to bulk-read reconciliation
        results for write-back to an external system.
        """
        import copy

        return copy.deepcopy(self._data)
