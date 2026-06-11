"""Tests for the lifetime model and effective-state computation (DD-028)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from src.config_loader import ConfigValidationError, load_all_projects
from src.models import LifetimeConfig, ProjectConfig, ProjectState

UTC = dt.UTC


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


class TestLifetimeValidate:
    """Manual validation of the lifetime mapping."""

    def test_valid_lock(self) -> None:
        errors: list[str] = []
        cfg = LifetimeConfig.validate({"until": "2026-09-30", "action": "lock"}, errors, "test", "acme-trial")
        assert errors == []
        assert cfg is not None
        assert cfg.until == _utc(2026, 9, 30, 23, 59, 59)
        assert cfg.action == "lock"
        assert cfg.confirm_delete is None

    def test_valid_delete_with_confirmation(self) -> None:
        errors: list[str] = []
        cfg = LifetimeConfig.validate(
            {"until": "2026-07-31", "action": "delete", "confirm_delete": "cs101-spring"},
            errors,
            "test",
            "cs101-spring",
        )
        assert errors == []
        assert cfg is not None
        assert cfg.action == "delete"
        assert cfg.confirm_delete == "cs101-spring"

    def test_until_resolves_to_end_of_span(self) -> None:
        """until: "2026-12" means through 2026-12-31 23:59:59 UTC."""
        errors: list[str] = []
        cfg = LifetimeConfig.validate({"until": "2026-12", "action": "lock"}, errors, "test", "p")
        assert errors == []
        assert cfg is not None
        assert cfg.until == _utc(2026, 12, 31, 23, 59, 59)

    @pytest.mark.parametrize(
        ("data", "error_fragment"),
        [
            pytest.param({"action": "lock"}, "lifetime.until is required", id="missing-until"),
            pytest.param({"until": "2026-09-30"}, "required, no default", id="missing-action"),
            pytest.param(
                {"until": "2026-09-30", "action": "shelve"},
                "must be 'lock' or 'delete'",
                id="unknown-action",
            ),
            pytest.param(
                {"until": "2026-09-30", "action": "delete"},
                "confirm_delete must equal the project name",
                id="delete-without-confirmation",
            ),
            pytest.param(
                {"until": "2026-09-30", "action": "delete", "confirm_delete": "other-name"},
                "confirm_delete must equal the project name",
                id="delete-wrong-confirmation",
            ),
            pytest.param(
                {"until": "2026-09-30", "action": "lock", "confirm_delete": "p"},
                "only allowed with action 'delete'",
                id="lock-with-confirmation",
            ),
            pytest.param(
                {"until": "2026-09-30", "action": "lock", "on_expiry": {}},
                "unknown key",
                id="unknown-key",
            ),
            pytest.param(
                {"until": "2026-09-30T12:00:00", "action": "lock"},
                "day granularity at most",
                id="until-finer-than-day",
            ),
            pytest.param(
                {"until": dt.datetime(2026, 9, 30, 12, 0, 0), "action": "lock"},
                "quote the value",
                id="until-yaml-datetime",
            ),
            pytest.param("2026-09-30", "must be a mapping", id="not-a-mapping"),
        ],
    )
    def test_rejections(self, data: Any, error_fragment: str) -> None:
        """Errors accumulate; an instance may still be returned (project convention)."""
        errors: list[str] = []
        LifetimeConfig.validate(data, errors, "test", "p")
        assert any(error_fragment in e for e in errors), errors

    def test_yaml_coerced_date_is_normalized(self) -> None:
        """Unquoted until: 2026-09-30 arrives as a date object — normalized."""
        errors: list[str] = []
        cfg = LifetimeConfig.validate({"until": dt.date(2026, 9, 30), "action": "lock"}, errors, "test", "p")
        assert errors == []
        assert cfg is not None
        assert cfg.until == _utc(2026, 9, 30, 23, 59, 59)

    def test_from_dict_rejects_unknown_action(self) -> None:
        """A typo'd action must never reach effective_state, where it would mean delete."""
        with pytest.raises(ValueError, match=r"lifetime\.action"):
            LifetimeConfig.from_dict({"until": "2026-09-30", "action": "Lock"})


class TestEffectiveState:
    """Effective state: configured state tightened once the deadline passes."""

    BEFORE = _utc(2026, 9, 1)
    AFTER = _utc(2026, 10, 1)
    DEADLINE_DAY = "2026-09-30"

    def _cfg(self, state: str, action: str | None) -> ProjectConfig:
        lifetime = None
        if action is not None:
            lifetime = LifetimeConfig(
                until=_utc(2026, 9, 30, 23, 59, 59),
                action=action,
                confirm_delete="p" if action == "delete" else None,
            )
        return ProjectConfig(name="p", resource_prefix="p", state=ProjectState(state), lifetime=lifetime)

    @pytest.mark.parametrize(
        ("configured", "action", "now", "expected"),
        [
            # No lifetime: configured state passes through.
            pytest.param("present", None, AFTER, "present", id="no-lifetime"),
            # Before the deadline: configured state unchanged.
            pytest.param("present", "lock", BEFORE, "present", id="lock-before-deadline"),
            pytest.param("present", "delete", BEFORE, "present", id="delete-before-deadline"),
            # After the deadline: tightened to the action's floor.
            pytest.param("present", "lock", AFTER, "locked", id="lock-after-deadline"),
            pytest.param("present", "delete", AFTER, "absent", id="delete-after-deadline"),
            # Lifetime only tightens, never loosens (present < locked < absent).
            pytest.param("locked", "lock", AFTER, "locked", id="locked-stays-locked"),
            pytest.param("locked", "delete", AFTER, "absent", id="locked-raised-to-absent"),
            pytest.param("absent", "lock", AFTER, "absent", id="absent-never-loosened-by-lock"),
            pytest.param("absent", "delete", AFTER, "absent", id="absent-stays-absent"),
        ],
    )
    def test_effective_state(self, configured: str, action: str | None, now: dt.datetime, expected: str) -> None:
        cfg = self._cfg(configured, action)
        assert cfg.effective_state(now) == ProjectState(expected)

    def test_deadline_is_inclusive(self) -> None:
        """At exactly until (end of stated span) the state is not yet tightened."""
        cfg = self._cfg("present", "lock")
        assert cfg.effective_state(_utc(2026, 9, 30, 23, 59, 59)) == ProjectState.PRESENT
        assert cfg.effective_state(_utc(2026, 10, 1, 0, 0, 0)) == ProjectState.LOCKED

    def test_extending_until_restores_configured_state(self) -> None:
        """No stored timer: a later deadline immediately un-tightens."""
        expired = self._cfg("present", "lock")
        assert expired.effective_state(self.AFTER) == ProjectState.LOCKED
        import dataclasses

        extended = dataclasses.replace(
            expired,
            lifetime=LifetimeConfig(until=_utc(2027, 12, 31, 23, 59, 59), action="lock"),
        )
        assert extended.effective_state(self.AFTER) == ProjectState.PRESENT


class TestLifetimeRejectedInDefaults:
    """lifetime is per-project only — rejected in defaults.yaml."""

    def _write_config(self, tmp_path: Path, defaults: dict[str, Any]) -> Path:
        config_dir = tmp_path / "config"
        (config_dir / "projects").mkdir(parents=True)
        (config_dir / "defaults.yaml").write_text(yaml.dump(defaults))
        project = {
            "name": "proj1",
            "resource_prefix": "proj1",
            "network": {"subnet": {"cidr": "10.0.1.0/24"}},
        }
        (config_dir / "projects" / "proj1.yaml").write_text(yaml.dump(project))
        return config_dir

    def test_lifetime_in_defaults_rejected(self, tmp_path: Path) -> None:
        config_dir = self._write_config(
            tmp_path,
            {"lifetime": {"until": "2026-12-31", "action": "lock"}},
        )
        with pytest.raises(ConfigValidationError) as excinfo:
            load_all_projects(str(config_dir))
        assert any("rejected in defaults" in e for e in excinfo.value.errors)

    def test_lifetime_in_project_accepted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        (config_dir / "projects").mkdir(parents=True)
        (config_dir / "defaults.yaml").write_text(yaml.dump({}))
        project = {
            "name": "proj1",
            "resource_prefix": "proj1",
            "network": {"subnet": {"cidr": "10.0.1.0/24"}},
            "lifetime": {"until": "2026-12-31", "action": "lock"},
        }
        (config_dir / "projects" / "proj1.yaml").write_text(yaml.dump(project))

        projects, _defaults = load_all_projects(str(config_dir))
        assert projects[0].lifetime is not None
        assert projects[0].lifetime.action == "lock"
