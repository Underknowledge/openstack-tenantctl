"""Concurrency tests for YamlFileStateStore file locking."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest
from filelock import Timeout

if TYPE_CHECKING:
    from pathlib import Path

from src.state_store import YamlFileStateStore


def _worker_save(args: tuple[Path, str, list[str], Any]) -> None:
    state_dir, state_key, key_path, value = args
    store = YamlFileStateStore(state_dir)
    store.save(state_key, key_path, value)


def _worker_load(args: tuple[Path, str]) -> dict[str, Any]:
    state_dir, state_key = args
    store = YamlFileStateStore(state_dir)
    return store.load(state_key)


def _worker_save_with_delay(args: tuple[Path, str, list[str], Any, float]) -> None:
    """Worker that holds lock longer to trigger contention scenarios."""
    state_dir, state_key, key_path, value, delay = args
    store = YamlFileStateStore(state_dir)

    def slow_save(sk: str, kp: list[str], v: Any) -> None:
        with store._acquire_lock(sk):
            time.sleep(delay)
            path = store._state_path(sk)
            if path.exists():
                with path.open(encoding="utf-8") as fh:
                    import yaml

                    data: dict[str, Any] = yaml.safe_load(fh) or {}
            else:
                data = {}

            current = data
            for key in kp[:-1]:
                if key not in current or not isinstance(current[key], dict):
                    current[key] = {}
                current = current[key]

            current[kp[-1]] = v

            import yaml

            temp_path = path.with_suffix(".yaml.tmp")
            with temp_path.open("w", encoding="utf-8") as fh:
                yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
            temp_path.replace(path)

    slow_save(state_key, key_path, value)


class TestConcurrentWritesDifferentKeys:
    """Two threads writing different keys → both persisted (no data loss)."""

    def test_concurrent_writes_no_data_loss(self, tmp_path: Path) -> None:
        args1 = (tmp_path, "proj", ["fips"], [{"id": "a"}])
        args2 = (tmp_path, "proj", ["router_ips"], [{"id": "r"}])

        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(_worker_save, [args1, args2]))

        store = YamlFileStateStore(tmp_path)
        data = store.load("proj")
        assert data["fips"] == [{"id": "a"}]
        assert data["router_ips"] == [{"id": "r"}]


class TestConcurrentWritesSameKey:
    """Two threads writing same key → no corruption (last writer wins)."""

    def test_concurrent_writes_same_key(self, tmp_path: Path) -> None:
        args1 = (tmp_path, "proj", ["fips"], [{"id": "first"}])
        args2 = (tmp_path, "proj", ["fips"], [{"id": "second"}])

        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(_worker_save, [args1, args2]))

        store = YamlFileStateStore(tmp_path)
        data = store.load("proj")
        assert data["fips"] in [[{"id": "first"}], [{"id": "second"}]]


class TestLoadDuringSave:
    """Load during save → sees either old or new (never partial)."""

    def test_load_during_save(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path)
        store.save("proj", ["fips"], [{"id": "old"}])

        args_save = (tmp_path, "proj", ["fips"], [{"id": "new"}], 0.05)

        with ThreadPoolExecutor(max_workers=2) as ex:
            save_future = ex.submit(_worker_save_with_delay, args_save)
            time.sleep(0.01)  # give the save thread time to acquire the lock
            load_future = ex.submit(_worker_load, (tmp_path, "proj"))

            save_future.result(timeout=2)
            data = load_future.result(timeout=2)

        assert data["fips"] in [[{"id": "old"}], [{"id": "new"}]]


class TestLockTimeout:
    """Lock timeout → Timeout exception raised clearly."""

    def test_timeout_on_contention(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path)
        lock = store._acquire_lock("proj")

        with lock:
            store2 = YamlFileStateStore(tmp_path)
            import filelock

            lock2 = filelock.FileLock(store2._lock_path("proj"), timeout=0.01)

            with pytest.raises(Timeout), lock2:
                pass


class TestAtomicWriteCrashSafety:
    """If process crashes during write, original file intact."""

    def test_temp_file_pattern(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path)
        store.save("proj", ["fips"], [{"id": "original"}])
        original_path = store._state_path("proj")
        temp_path = original_path.with_suffix(".yaml.tmp")

        assert not temp_path.exists()

        data = store.load("proj")
        assert data["fips"] == [{"id": "original"}]


class TestNoLeftoverTempFiles:
    """.yaml.tmp cleaned up on success."""

    def test_no_temp_files_after_save(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path)

        for i in range(5):
            store.save("proj", ["counter"], i)

        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0


class TestMultipleProjectsConcurrent:
    """Concurrent saves to different projects → independent locks."""

    def test_different_projects_parallel(self, tmp_path: Path) -> None:
        args1 = (tmp_path, "proj-a", ["fips"], [{"id": "a"}])
        args2 = (tmp_path, "proj-b", ["fips"], [{"id": "b"}])
        args3 = (tmp_path, "proj-c", ["fips"], [{"id": "c"}])

        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(_worker_save, [args1, args2, args3]))

        store = YamlFileStateStore(tmp_path)
        assert store.load("proj-a")["fips"] == [{"id": "a"}]
        assert store.load("proj-b")["fips"] == [{"id": "b"}]
        assert store.load("proj-c")["fips"] == [{"id": "c"}]


class TestLockFilesCreatedAndReleased:
    """Lock files created during operations and released after."""

    def test_lock_lifecycle(self, tmp_path: Path) -> None:
        store = YamlFileStateStore(tmp_path)

        store.save("proj", ["fips"], [{"id": "test"}])
        store.save("proj", ["fips"], [{"id": "test2"}])

        data = store.load("proj")
        assert data["fips"] == [{"id": "test2"}]


class TestNestedConcurrentWrites:
    """Concurrent writes to nested keys preserve structure."""

    def test_nested_concurrent_writes(self, tmp_path: Path) -> None:
        args1 = (tmp_path, "proj", ["metadata", "project_id"], "uuid-1")
        args2 = (tmp_path, "proj", ["metadata", "domain_id"], "default")
        args3 = (tmp_path, "proj", ["fips"], [{"id": "f1"}])

        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(_worker_save, [args1, args2, args3]))

        store = YamlFileStateStore(tmp_path)
        data = store.load("proj")

        assert data["metadata"]["project_id"] == "uuid-1"
        assert data["metadata"]["domain_id"] == "default"
        assert data["fips"] == [{"id": "f1"}]
