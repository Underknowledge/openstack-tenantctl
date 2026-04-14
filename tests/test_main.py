"""Tests for main CLI entry point (thin adapter over TenantCtl)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from src.client import RunResult
from src.main import _print_summary, main
from src.utils import Action, ActionStatus


class TestPrintSummary:
    """Tests for _print_summary helper."""

    def test_prints_dry_run_header_offline(self, capsys: pytest.CaptureFixture) -> None:
        """Should print offline dry-run header when had_connection is False."""
        result = RunResult(had_connection=False)

        exit_code = _print_summary(result, dry_run=True)

        captured = capsys.readouterr()
        assert "Dry-run: planned changes (offline)" in captured.out
        assert exit_code == 0

    def test_prints_dry_run_header_online(self, capsys: pytest.CaptureFixture) -> None:
        """Should print online dry-run header when had_connection is True."""
        result = RunResult(had_connection=True)

        exit_code = _print_summary(result, dry_run=True)

        captured = capsys.readouterr()
        assert "Dry-run: planned changes (live cloud reads)" in captured.out
        assert exit_code == 0

    def test_prints_action_summary(self, capsys: pytest.CaptureFixture) -> None:
        """Should print all actions with status and details."""
        actions = [
            Action(ActionStatus.CREATED, "project", "proj1", "created successfully"),
            Action(ActionStatus.SKIPPED, "network", "net1", "already exists"),
        ]
        result = RunResult(actions=actions)

        _print_summary(result, dry_run=False)

        captured = capsys.readouterr()
        assert "CREATED" in captured.out
        assert "project: proj1" in captured.out
        assert "created successfully" in captured.out
        assert "SKIPPED" in captured.out
        assert "network: net1" in captured.out
        assert "already exists" in captured.out

    def test_prints_counts(self, capsys: pytest.CaptureFixture) -> None:
        """Should print summary counts of all action statuses."""
        actions = [
            Action(ActionStatus.CREATED, "project", "p1"),
            Action(ActionStatus.CREATED, "network", "n1"),
            Action(ActionStatus.UPDATED, "quota", "q1"),
            Action(ActionStatus.SKIPPED, "sg", "sg1"),
            Action(ActionStatus.FAILED, "fip", "f1", "error"),
        ]
        result = RunResult(actions=actions)

        _print_summary(result, dry_run=False)

        captured = capsys.readouterr()
        assert "2 created, 1 updated, 0 deleted, 1 skipped, 1 failed" in captured.out

    def test_returns_1_when_projects_failed(self, capsys: pytest.CaptureFixture) -> None:
        """Should return exit code 1 when any projects failed."""
        result = RunResult(failed_projects=["project1"])

        exit_code = _print_summary(result, dry_run=False)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Failed projects: project1" in captured.err

    def test_returns_0_when_no_failures(self) -> None:
        """Should return exit code 0 when all projects succeeded."""
        actions = [Action(ActionStatus.CREATED, "project", "p1")]
        result = RunResult(actions=actions)

        exit_code = _print_summary(result, dry_run=False)

        assert exit_code == 0

    def test_returns_0_with_all_skipped(self, capsys: pytest.CaptureFixture) -> None:
        """Should return exit code 0 when all actions are SKIPPED."""
        actions = [
            Action(ActionStatus.SKIPPED, "project", "p1", "already exists"),
            Action(ActionStatus.SKIPPED, "network", "n1", "already exists"),
            Action(ActionStatus.SKIPPED, "quotas", "", "no changes"),
        ]
        result = RunResult(actions=actions)

        exit_code = _print_summary(result, dry_run=False)

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "0 created, 0 updated, 0 deleted, 3 skipped, 0 failed" in captured.out

    def test_returns_1_with_mixed_created_and_failed_actions(self, capsys: pytest.CaptureFixture) -> None:
        """Should return exit code 1 when FAILED actions exist in failed_projects."""
        actions = [
            Action(ActionStatus.CREATED, "network", "net1", "created successfully"),
            Action(ActionStatus.FAILED, "fip", "fip1", "allocation failed"),
        ]
        result = RunResult(actions=actions, failed_projects=["proj1"])

        exit_code = _print_summary(result, dry_run=False)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "1 created, 0 updated, 0 deleted, 0 skipped, 1 failed" in captured.out
        assert "Failed projects: proj1" in captured.err


class TestSetupLogging:
    """Tests for verbosity flag and logging setup."""

    @patch("src.main.TenantCtl")
    @patch("src.main.setup_logging")
    def test_verbose_flag_sets_info_level(
        self,
        mock_setup_logging: Mock,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=1 when -v is specified."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        main(["--config-dir", str(mock_config_dir), "--dry-run", "-v"])

        mock_setup_logging.assert_called_once_with(1)

    @patch("src.main.TenantCtl")
    @patch("src.main.setup_logging")
    def test_double_verbose_sets_debug_level(
        self,
        mock_setup_logging: Mock,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=2 when -vv is specified."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        main(["--config-dir", str(mock_config_dir), "--dry-run", "-vv"])

        mock_setup_logging.assert_called_once_with(2)

    @patch("src.main.TenantCtl")
    @patch("src.main.setup_logging")
    def test_no_verbose_flag_sets_default_level(
        self,
        mock_setup_logging: Mock,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should call setup_logging with verbose=0 when no -v flag is specified."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        main(["--config-dir", str(mock_config_dir), "--dry-run"])

        mock_setup_logging.assert_called_once_with(0)


class TestMainIntegration:
    """Integration tests for main() entry point."""

    @patch("src.main.TenantCtl")
    def test_main_dry_run_mode(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Should run in dry-run mode without connecting to OpenStack."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult(had_connection=False)

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.assert_called_once_with(str(mock_config_dir), cloud=None)
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project=None, dry_run=True, offline=False, only=None, auto_expand_deps=False
        )

    @patch("src.main.TenantCtl")
    def test_main_filters_single_project(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should filter to single project when --project is specified."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--project", "proj1"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project="proj1", dry_run=True, offline=False, only=None, auto_expand_deps=False
        )

    @patch("src.main.TenantCtl")
    def test_main_os_cloud_flag(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should pass --os-cloud value to TenantCtl constructor."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--os-cloud", "mycloud"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.assert_called_once_with(str(mock_config_dir), cloud="mycloud")

    @patch("src.main.TenantCtl")
    def test_main_verbosity_flags(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should accept verbosity flags -v and -vv."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        # Single -v should work
        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "-v"])
        assert exit_code == 0

        # Double -vv should work
        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "-vv"])
        assert exit_code == 0

    @patch("src.main.TenantCtl")
    def test_main_returns_1_when_filtered_project_not_found(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should return 1 when filtered project doesn't exist."""
        from src.utils import ProvisionerError

        mock_client_cls.from_config_dir.return_value.run.side_effect = ProvisionerError(
            "project 'nonexistent' not found"
        )

        exit_code = main(
            [
                "--config-dir",
                str(mock_config_dir),
                "--dry-run",
                "--project",
                "nonexistent",
            ]
        )

        assert exit_code == 1

    @patch("src.main.TenantCtl")
    def test_main_returns_1_when_projects_fail(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Should return exit code 1 when any projects fail."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult(failed_projects=["proj1"])

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Failed projects: proj1" in captured.err

    def test_version_flag(self, capsys: pytest.CaptureFixture) -> None:
        """Test --version flag displays version."""
        from src import __version__

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])

        # argparse --version calls sys.exit(0)
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert f"tenantctl {__version__}" in captured.out

    @patch("src.main.TenantCtl")
    def test_connection_closed_after_reconcile(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should return 0 when run completes successfully."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult(had_connection=True)

        exit_code = main(["--config-dir", str(mock_config_dir)])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once()

    @patch("src.main.TenantCtl")
    def test_connection_closed_even_when_reconcile_raises(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should propagate exceptions from client.run()."""
        mock_client_cls.from_config_dir.return_value.run.side_effect = Exception("Reconcile failed")

        with pytest.raises(Exception, match="Reconcile failed"):
            main(["--config-dir", str(mock_config_dir)])

    @patch("src.main.TenantCtl")
    def test_dry_run_mode_no_connection_to_close(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """Should work in dry-run mode with no connection."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult(had_connection=False)

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 0


class TestOnlyFlag:
    """Tests for the --only CLI flag."""

    @patch("src.main.TenantCtl")
    def test_only_single_scope(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """--only fips passes only={ReconcileScope.FIPS} to run()."""
        from src.reconciler import ReconcileScope

        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--only", "fips"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project=None, dry_run=True, offline=False, only={ReconcileScope.FIPS}, auto_expand_deps=False
        )

    @patch("src.main.TenantCtl")
    def test_only_multiple_scopes(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """--only fips quotas passes both scopes to run()."""
        from src.reconciler import ReconcileScope

        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--only", "fips", "quotas"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project=None,
            dry_run=True,
            offline=False,
            only={ReconcileScope.FIPS, ReconcileScope.QUOTAS},
            auto_expand_deps=False,
        )

    def test_only_invalid_returns_1(self, mock_config_dir: Path) -> None:
        """--only bogus returns exit code 1."""
        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--only", "bogus"])

        assert exit_code == 1

    @patch("src.main.TenantCtl")
    def test_no_only_passes_none(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """No --only passes only=None to run()."""
        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project=None, dry_run=True, offline=False, only=None, auto_expand_deps=False
        )

    @patch("src.main.TenantCtl")
    def test_auto_deps_flag_passed_to_run(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """--only fips --auto-deps passes auto_expand_deps=True to run()."""
        from src.reconciler import ReconcileScope

        mock_client_cls.from_config_dir.return_value.run.return_value = RunResult()

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--only", "fips", "--auto-deps"])

        assert exit_code == 0
        mock_client_cls.from_config_dir.return_value.run.assert_called_once_with(
            project=None,
            dry_run=True,
            offline=False,
            only={ReconcileScope.FIPS},
            auto_expand_deps=True,
        )

    @patch("src.main.TenantCtl")
    def test_only_without_deps_exits_1(
        self,
        mock_client_cls: Mock,
        mock_config_dir: Path,
    ) -> None:
        """--only fips (no --auto-deps) exits with code 1 due to ValueError."""
        mock_client_cls.from_config_dir.return_value.run.side_effect = ValueError(
            "Missing scope dependencies: scope 'fips' requires 'network'"
        )

        exit_code = main(["--config-dir", str(mock_config_dir), "--dry-run", "--only", "fips"])

        assert exit_code == 1
