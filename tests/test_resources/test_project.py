"""Tests for project provisioning — ensure_project."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from src.resources.project import ensure_project, is_project_disabled
from src.utils import ActionStatus, SharedContext

if TYPE_CHECKING:
    from src.models import ProjectConfig


def _stub_domain(mock_conn: MagicMock, domain_id: str = "domain-uuid-123") -> None:
    """Configure find_domain to return a mock domain with the given ID."""
    domain = MagicMock()
    domain.id = domain_id
    mock_conn.identity.find_domain.return_value = domain


class TestCreateNewProject:
    """When no project exists, ensure_project creates one."""

    def test_create_new_project(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        # find_project returns None → project does not exist
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(sample_project_cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-123"
        shared_ctx.conn.identity.find_domain.assert_called_once_with("default")
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="test_project",
            domain_id="domain-uuid-default",
            description="Test project",
            is_enabled=True,
        )


class TestUpdateExistingProject:
    """When the existing project has a different description, it is updated."""

    def test_update_existing_project(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        _stub_domain(shared_ctx.conn)
        existing_project = MagicMock()
        existing_project.id = "proj-123"
        existing_project.description = "Old description"
        existing_project.is_enabled = True
        shared_ctx.conn.identity.find_project.return_value = existing_project

        updated_project = MagicMock()
        updated_project.id = "proj-123"
        shared_ctx.conn.identity.update_project.return_value = updated_project

        action, project_id = ensure_project(sample_project_cfg, shared_ctx)

        assert action.status == ActionStatus.UPDATED
        assert project_id == "proj-123"
        shared_ctx.conn.identity.update_project.assert_called_once_with(
            "proj-123",
            description="Test project",
            is_enabled=True,
        )


class TestSkipMatchingProject:
    """When the existing project matches config, it is skipped."""

    def test_skip_matching_project(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        _stub_domain(shared_ctx.conn)
        existing_project = MagicMock()
        existing_project.id = "proj-123"
        existing_project.description = "Test project"
        existing_project.is_enabled = True
        shared_ctx.conn.identity.find_project.return_value = existing_project

        action, project_id = ensure_project(sample_project_cfg, shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert action.details == "already up to date"
        assert project_id == "proj-123"
        shared_ctx.conn.identity.create_project.assert_not_called()
        shared_ctx.conn.identity.update_project.assert_not_called()


class TestDryRunSkips:
    """Online dry-run reads state but makes no writes."""

    def test_dry_run_project_not_found(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project not found → CREATED with creation parameters."""
        _stub_domain(dry_run_ctx.conn, "domain-123")
        dry_run_ctx.conn.identity.find_project.return_value = None

        action, project_id = ensure_project(sample_project_cfg, dry_run_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == ""
        assert "would create" in action.details
        assert "description=" in action.details
        # Reads happened
        dry_run_ctx.conn.identity.find_domain.assert_called_once()
        dry_run_ctx.conn.identity.find_project.assert_called_once()
        # No writes
        dry_run_ctx.conn.identity.create_project.assert_not_called()

    def test_dry_run_project_needs_update(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project exists but differs → UPDATED with field-level diff."""
        _stub_domain(dry_run_ctx.conn, "domain-123")
        existing = MagicMock()
        existing.id = "proj-123"
        existing.description = "Old description"
        existing.is_enabled = True
        dry_run_ctx.conn.identity.find_project.return_value = existing

        action, project_id = ensure_project(sample_project_cfg, dry_run_ctx)

        assert action.status == ActionStatus.UPDATED
        assert project_id == "proj-123"
        assert "would update" in action.details
        assert "description:" in action.details
        # No writes
        dry_run_ctx.conn.identity.update_project.assert_not_called()

    def test_dry_run_project_up_to_date(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project matches config → SKIPPED."""
        _stub_domain(dry_run_ctx.conn, "domain-123")
        existing = MagicMock()
        existing.id = "proj-123"
        existing.description = sample_project_cfg.description
        existing.is_enabled = sample_project_cfg.enabled
        dry_run_ctx.conn.identity.find_project.return_value = existing

        action, project_id = ensure_project(sample_project_cfg, dry_run_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert project_id == "proj-123"
        assert "up to date" in action.details
        dry_run_ctx.conn.identity.create_project.assert_not_called()
        dry_run_ctx.conn.identity.update_project.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        action, project_id = ensure_project(sample_project_cfg, offline_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert project_id == ""
        assert "offline" in action.details


class TestDomainResolution:
    """Domain names are resolved to UUIDs before use."""

    def test_resolves_domain_name_to_uuid(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """A human-readable domain name is resolved to its UUID."""
        cfg = dataclasses.replace(sample_project_cfg, domain_id="acme-corp")
        _stub_domain(shared_ctx.conn, "aabbccdd-1234-5678-9012-acme00000001")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-456"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-456"
        shared_ctx.conn.identity.find_domain.assert_called_once_with("acme-corp")
        shared_ctx.conn.identity.find_project.assert_called_once_with(
            "test_project",
            domain_id="aabbccdd-1234-5678-9012-acme00000001",
        )
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="test_project",
            domain_id="aabbccdd-1234-5678-9012-acme00000001",
            description="Test project",
            is_enabled=True,
        )

    def test_uses_configured_domain_id(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        cfg = dataclasses.replace(sample_project_cfg, domain_id="my-custom-domain")
        _stub_domain(shared_ctx.conn, "custom-domain-uuid")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, _project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        shared_ctx.conn.identity.find_domain.assert_called_once_with("my-custom-domain")
        shared_ctx.conn.identity.find_project.assert_called_once_with("test_project", domain_id="custom-domain-uuid")
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="test_project",
            domain_id="custom-domain-uuid",
            description="Test project",
            is_enabled=True,
        )

    def test_defaults_to_default_domain(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        # Config already defaults to "default" domain via ProjectConfig dataclass default
        cfg = dataclasses.replace(sample_project_cfg, domain_id="default")
        _stub_domain(shared_ctx.conn, "default-domain-uuid")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        _action, _project_id = ensure_project(cfg, shared_ctx)

        shared_ctx.conn.identity.find_domain.assert_called_once_with("default")
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="test_project",
            domain_id="default-domain-uuid",
            description="Test project",
            is_enabled=True,
        )

    def test_unknown_domain_raises(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """ValueError is raised when the domain cannot be found."""
        cfg = dataclasses.replace(sample_project_cfg, domain_id="nonexistent-domain")
        shared_ctx.conn.identity.find_domain.return_value = None

        with pytest.raises(ValueError, match="Could not find domain: nonexistent-domain"):
            ensure_project(cfg, shared_ctx)


class TestDisabledProjects:
    """Projects can be created or updated with enabled=False."""

    def test_create_disabled_project(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """A new project can be created with enabled=False."""
        cfg = dataclasses.replace(sample_project_cfg, enabled=False)
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-disabled-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-disabled-123"
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="test_project",
            domain_id="domain-uuid-default",
            description="Test project",
            is_enabled=False,
        )

    def test_update_to_disabled(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """An existing enabled project can be updated to disabled."""
        cfg = dataclasses.replace(sample_project_cfg, enabled=False)
        _stub_domain(shared_ctx.conn)

        existing_project = MagicMock()
        existing_project.id = "proj-123"
        existing_project.description = "Test project"
        existing_project.is_enabled = True
        shared_ctx.conn.identity.find_project.return_value = existing_project

        updated_project = MagicMock()
        updated_project.id = "proj-123"
        shared_ctx.conn.identity.update_project.return_value = updated_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.UPDATED
        assert project_id == "proj-123"
        shared_ctx.conn.identity.update_project.assert_called_once_with(
            "proj-123",
            description="Test project",
            is_enabled=False,
        )

    def test_update_to_enabled(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """An existing disabled project can be updated to enabled."""
        cfg = dataclasses.replace(sample_project_cfg, enabled=True)
        _stub_domain(shared_ctx.conn)

        existing_project = MagicMock()
        existing_project.id = "proj-123"
        existing_project.description = "Test project"
        existing_project.is_enabled = False
        shared_ctx.conn.identity.find_project.return_value = existing_project

        updated_project = MagicMock()
        updated_project.id = "proj-123"
        shared_ctx.conn.identity.update_project.return_value = updated_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.UPDATED
        assert project_id == "proj-123"
        shared_ctx.conn.identity.update_project.assert_called_once_with(
            "proj-123",
            description="Test project",
            is_enabled=True,
        )

    def test_skip_disabled_project_when_matching(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """A disabled project that matches config is skipped."""
        cfg = dataclasses.replace(sample_project_cfg, enabled=False)
        _stub_domain(shared_ctx.conn)

        existing_project = MagicMock()
        existing_project.id = "proj-123"
        existing_project.description = "Test project"
        existing_project.is_enabled = False
        shared_ctx.conn.identity.find_project.return_value = existing_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert project_id == "proj-123"
        shared_ctx.conn.identity.create_project.assert_not_called()
        shared_ctx.conn.identity.update_project.assert_not_called()


class TestProjectNameCharacters:
    """Project names can contain various valid characters."""

    def test_project_name_with_spaces(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project name can contain spaces."""
        cfg = dataclasses.replace(sample_project_cfg, name="My Test Project")
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-with-spaces-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-with-spaces-123"
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="My Test Project",
            domain_id="domain-uuid-default",
            description="Test project",
            is_enabled=True,
        )

    def test_project_name_with_hyphens(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project name can contain hyphens."""
        cfg = dataclasses.replace(sample_project_cfg, name="my-test-project")
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-with-hyphens-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-with-hyphens-123"
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="my-test-project",
            domain_id="domain-uuid-default",
            description="Test project",
            is_enabled=True,
        )

    def test_project_name_with_mixed_characters(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project name can contain spaces, hyphens, and underscores."""
        cfg = dataclasses.replace(sample_project_cfg, name="My_Test-Project 2024")
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        shared_ctx.conn.identity.find_project.return_value = None

        created_project = MagicMock()
        created_project.id = "proj-mixed-123"
        shared_ctx.conn.identity.create_project.return_value = created_project

        action, project_id = ensure_project(cfg, shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert project_id == "proj-mixed-123"
        shared_ctx.conn.identity.create_project.assert_called_once_with(
            name="My_Test-Project 2024",
            domain_id="domain-uuid-default",
            description="Test project",
            is_enabled=True,
        )


class TestIsProjectDisabled:
    """Tests for is_project_disabled() — API fallback for locked->present detection."""

    def test_returns_true_when_project_disabled(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project exists and is_enabled=False → True."""
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        existing = MagicMock()
        existing.is_enabled = False
        shared_ctx.conn.identity.find_project.return_value = existing

        assert is_project_disabled(sample_project_cfg, shared_ctx) is True

    def test_returns_false_when_project_enabled(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project exists and is_enabled=True → False."""
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        existing = MagicMock()
        existing.is_enabled = True
        shared_ctx.conn.identity.find_project.return_value = existing

        assert is_project_disabled(sample_project_cfg, shared_ctx) is False

    def test_returns_false_when_project_not_found(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Project does not exist → False."""
        _stub_domain(shared_ctx.conn, "domain-uuid-default")
        shared_ctx.conn.identity.find_project.return_value = None

        assert is_project_disabled(sample_project_cfg, shared_ctx) is False

    def test_returns_false_when_conn_is_none(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """No connection available (offline mode) → False."""
        assert is_project_disabled(sample_project_cfg, offline_ctx) is False
