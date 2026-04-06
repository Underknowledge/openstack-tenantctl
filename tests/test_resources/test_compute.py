"""Tests for compute module — shelve/unshelve server operations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from src.resources.compute import shelve_all_servers, unshelve_all_servers
from src.utils import ActionStatus, SharedContext

if TYPE_CHECKING:
    from src.models import ProjectConfig


def _make_server(name: str, status: str) -> MagicMock:
    """Create a mock server with the given name and status."""
    server = MagicMock()
    server.name = name
    server.id = f"{name}-id"
    server.status = status
    return server


class TestShelveAllServers:
    """Tests for shelve_all_servers function."""

    def test_shelves_active_skips_others(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Only ACTIVE servers should be shelved."""
        servers = [
            _make_server("active1", "ACTIVE"),
            _make_server("shelved1", "SHELVED"),
            _make_server("shutoff1", "SHUTOFF"),
            _make_server("active2", "ACTIVE"),
            _make_server("offloaded1", "SHELVED_OFFLOADED"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert actions[0].name == "active1"
        assert actions[0].details == "shelved (was ACTIVE)"
        assert actions[1].name == "active2"
        assert actions[1].details == "shelved (was ACTIVE)"

        assert shared_ctx.conn.compute.shelve_server.call_count == 2
        shared_ctx.conn.compute.shelve_server.assert_any_call("active1-id")
        shared_ctx.conn.compute.shelve_server.assert_any_call("active2-id")

    def test_no_servers_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """No servers in project returns SKIPPED with specific message."""
        shared_ctx.conn.compute.servers.return_value = []

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_shelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no servers in project"

    def test_no_active_servers_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """All servers already shelved/off returns SKIPPED with specific message."""
        servers = [
            _make_server("shelved1", "SHELVED"),
            _make_server("offloaded1", "SHELVED_OFFLOADED"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_shelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no active servers to shelve"
        shared_ctx.conn.compute.shelve_server.assert_not_called()

    def test_dry_run_no_servers(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Online dry-run with no servers → SKIPPED."""
        dry_run_ctx.conn.compute.servers.return_value = []

        actions = shelve_all_servers(sample_project_cfg, "proj-123", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_shelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no servers in project"
        # Reads happened
        dry_run_ctx.conn.compute.servers.assert_called_once()
        # No writes
        dry_run_ctx.conn.compute.shelve_server.assert_not_called()

    def test_dry_run_with_active_servers(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Online dry-run with active servers → UPDATED with server names."""
        servers = [
            _make_server("web1", "ACTIVE"),
            _make_server("db1", "SHELVED"),
            _make_server("web2", "ACTIVE"),
        ]
        dry_run_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "would shelve 2 server(s)" in actions[0].details
        assert "web1" in actions[0].details
        assert "web2" in actions[0].details
        dry_run_ctx.conn.compute.shelve_server.assert_not_called()

    def test_offline_dry_run_skips(
        self, offline_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Offline dry-run → SKIPPED with no API calls."""
        actions = shelve_all_servers(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    def test_shelve_failure_continues(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """A single shelve failure must not prevent remaining servers from being shelved."""
        servers = [
            _make_server("fail_server", "ACTIVE"),
            _make_server("ok_server", "ACTIVE"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        shared_ctx.conn.compute.shelve_server.side_effect = [
            RuntimeError("simulated shelve failure"),
            None,
        ]

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert actions[0].status == ActionStatus.FAILED
        assert actions[0].name == "fail_server"
        assert actions[0].details == "shelve failed (was ACTIVE)"
        assert actions[1].status == ActionStatus.UPDATED
        assert actions[1].name == "ok_server"
        assert actions[1].details == "shelved (was ACTIVE)"

        assert shared_ctx.conn.compute.shelve_server.call_count == 2
        shared_ctx.conn.compute.shelve_server.assert_any_call("fail_server-id")
        shared_ctx.conn.compute.shelve_server.assert_any_call("ok_server-id")

    def test_transient_states_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Servers in transient states should be skipped (not ACTIVE)."""
        servers = [
            _make_server("building", "BUILDING"),
            _make_server("migrating", "MIGRATING"),
            _make_server("resize", "RESIZE"),
            _make_server("verify", "VERIFY_RESIZE"),
            _make_server("reboot", "REBOOT"),
            _make_server("active", "ACTIVE"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert actions[0].name == "active"
        shared_ctx.conn.compute.shelve_server.assert_called_once_with("active-id")

    def test_shutoff_server_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """SHUTOFF servers should be skipped during shelve (not ACTIVE)."""
        servers = [
            _make_server("shutoff1", "SHUTOFF"),
            _make_server("shutoff2", "SHUTOFF"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].details == "no active servers to shelve"
        shared_ctx.conn.compute.shelve_server.assert_not_called()

    def test_server_with_none_or_empty_name(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Servers with missing or empty names should be handled gracefully."""
        server_no_name = MagicMock()
        server_no_name.name = None
        server_no_name.id = "no-name-id"
        server_no_name.status = "ACTIVE"

        server_empty_name = MagicMock()
        server_empty_name.name = ""
        server_empty_name.id = "empty-name-id"
        server_empty_name.status = "ACTIVE"

        servers = [server_no_name, server_empty_name]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert actions[0].name is None
        assert actions[1].name == ""

        assert shared_ctx.conn.compute.shelve_server.call_count == 2
        shared_ctx.conn.compute.shelve_server.assert_any_call("no-name-id")
        shared_ctx.conn.compute.shelve_server.assert_any_call("empty-name-id")

    def test_endpoint_not_found_raises(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """EndpointNotFound during server list should propagate (no graceful handling)."""
        from openstack.exceptions import EndpointNotFound

        shared_ctx.conn.compute.servers.side_effect = EndpointNotFound(
            message="Compute service not available"
        )

        with pytest.raises(EndpointNotFound, match="Compute service not available"):
            shelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)


class TestUnshelveAllServers:
    """Tests for unshelve_all_servers function."""

    def test_unshelves_shelved_skips_others(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Only SHELVED/SHELVED_OFFLOADED servers should be unshelved."""
        servers = [
            _make_server("shelved1", "SHELVED"),
            _make_server("active1", "ACTIVE"),
            _make_server("offloaded1", "SHELVED_OFFLOADED"),
            _make_server("shutoff1", "SHUTOFF"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert actions[0].name == "shelved1"
        assert actions[0].details == "unshelved (was SHELVED)"
        assert actions[1].name == "offloaded1"
        assert actions[1].details == "unshelved (was SHELVED_OFFLOADED)"

        assert shared_ctx.conn.compute.unshelve_server.call_count == 2
        shared_ctx.conn.compute.unshelve_server.assert_any_call("shelved1-id")
        shared_ctx.conn.compute.unshelve_server.assert_any_call("offloaded1-id")

    def test_no_servers_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """No servers in project returns SKIPPED with specific message."""
        shared_ctx.conn.compute.servers.return_value = []

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_unshelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no servers in project"

    def test_no_shelved_servers_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """All servers active/off returns SKIPPED with specific message."""
        servers = [
            _make_server("active1", "ACTIVE"),
            _make_server("shutoff1", "SHUTOFF"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_unshelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no shelved servers to unshelve"
        shared_ctx.conn.compute.unshelve_server.assert_not_called()

    def test_dry_run_no_servers(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Online dry-run with no servers → SKIPPED."""
        dry_run_ctx.conn.compute.servers.return_value = []

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert actions[0].resource_type == "server_unshelve"
        assert actions[0].name == "all"
        assert actions[0].details == "no servers in project"
        dry_run_ctx.conn.compute.servers.assert_called_once()
        dry_run_ctx.conn.compute.unshelve_server.assert_not_called()

    def test_dry_run_with_shelved_servers(
        self, dry_run_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Online dry-run with shelved servers → UPDATED with server names."""
        servers = [
            _make_server("web1", "SHELVED"),
            _make_server("db1", "ACTIVE"),
            _make_server("web2", "SHELVED_OFFLOADED"),
        ]
        dry_run_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", dry_run_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert "would unshelve 2 server(s)" in actions[0].details
        assert "web1" in actions[0].details
        assert "web2" in actions[0].details
        dry_run_ctx.conn.compute.unshelve_server.assert_not_called()

    def test_offline_dry_run_skips(
        self, offline_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Offline dry-run → SKIPPED with no API calls."""
        actions = unshelve_all_servers(sample_project_cfg, "proj-123", offline_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.SKIPPED
        assert "offline" in actions[0].details

    def test_unshelve_failure_continues(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """A single unshelve failure must not prevent remaining servers from being unshelved."""
        servers = [
            _make_server("fail_server", "SHELVED"),
            _make_server("ok_server", "SHELVED_OFFLOADED"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        shared_ctx.conn.compute.unshelve_server.side_effect = [
            RuntimeError("simulated unshelve failure"),
            None,
        ]

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 2
        assert actions[0].status == ActionStatus.FAILED
        assert actions[0].name == "fail_server"
        assert actions[0].details == "unshelve failed (was SHELVED)"
        assert actions[1].status == ActionStatus.UPDATED
        assert actions[1].name == "ok_server"
        assert actions[1].details == "unshelved (was SHELVED_OFFLOADED)"

        assert shared_ctx.conn.compute.unshelve_server.call_count == 2
        shared_ctx.conn.compute.unshelve_server.assert_any_call("fail_server-id")
        shared_ctx.conn.compute.unshelve_server.assert_any_call("ok_server-id")

    def test_transient_states_skipped(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Servers in transient states should be skipped (not SHELVED/SHELVED_OFFLOADED)."""
        servers = [
            _make_server("building", "BUILDING"),
            _make_server("migrating", "MIGRATING"),
            _make_server("shelved", "SHELVED"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert actions[0].name == "shelved"
        shared_ctx.conn.compute.unshelve_server.assert_called_once_with("shelved-id")

    def test_mixed_shelved_states_unshelved(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Unshelve should handle mixed SHELVED, SHELVED_OFFLOADED, and ACTIVE states."""
        servers = [
            _make_server("shelved1", "SHELVED"),
            _make_server("active1", "ACTIVE"),
            _make_server("offloaded1", "SHELVED_OFFLOADED"),
            _make_server("offloaded2", "SHELVED_OFFLOADED"),
            _make_server("shutoff1", "SHUTOFF"),
        ]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 3
        assert all(a.status == ActionStatus.UPDATED for a in actions)
        assert actions[0].name == "shelved1"
        assert actions[1].name == "offloaded1"
        assert actions[2].name == "offloaded2"

        assert shared_ctx.conn.compute.unshelve_server.call_count == 3
        shared_ctx.conn.compute.unshelve_server.assert_any_call("shelved1-id")
        shared_ctx.conn.compute.unshelve_server.assert_any_call("offloaded1-id")
        shared_ctx.conn.compute.unshelve_server.assert_any_call("offloaded2-id")

    def test_unshelve_server_with_none_name(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """Servers with missing name should be handled gracefully."""
        server_no_name = MagicMock()
        server_no_name.name = None
        server_no_name.id = "no-name-id"
        server_no_name.status = "SHELVED"

        servers = [server_no_name]
        shared_ctx.conn.compute.servers.return_value = servers

        actions = unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)

        assert len(actions) == 1
        assert actions[0].status == ActionStatus.UPDATED
        assert actions[0].name is None
        shared_ctx.conn.compute.unshelve_server.assert_called_once_with("no-name-id")

    def test_endpoint_not_found_raises(
        self, shared_ctx: SharedContext, sample_project_cfg: ProjectConfig
    ) -> None:
        """EndpointNotFound during server list should propagate (no graceful handling)."""
        from openstack.exceptions import EndpointNotFound

        shared_ctx.conn.compute.servers.side_effect = EndpointNotFound(
            message="Compute service not available"
        )

        with pytest.raises(EndpointNotFound, match="Compute service not available"):
            unshelve_all_servers(sample_project_cfg, "proj-123", shared_ctx)
