"""CLI entry point for the OpenStack provisioner.

Thin adapter: parses arguments, delegates to ``TenantCtl``, prints results.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import __version__
from src.client import RunResult, TenantCtl
from src.config_loader import ConfigValidationError
from src.reconciler import ReconcileScope
from src.scaffold import write_sample_config
from src.utils import ActionStatus, ProvisionerError, setup_logging

logger = logging.getLogger(__name__)


def _print_summary(result: RunResult, *, dry_run: bool) -> int:
    """Print action summary and return exit code.

    Returns:
        0 on success, 1 if any projects failed
    """
    if dry_run:
        if result.had_connection:
            print("\n--- Dry-run: planned changes (live cloud reads) ---")
        else:
            print("\n--- Dry-run: planned changes (offline) ---")

    for action in result.actions:
        parts = [f"  [{action.status.value:>7}]"]
        if action.project_id or action.project_name:
            parts.append(f"{action.project_id} {action.project_name}")
        parts.append(f"{action.resource_type}:")
        if action.name:
            parts[-1] += f" {action.name}"
        if action.details:
            parts.append(f"({action.details})")
        print(" ".join(parts))

    counts: dict[ActionStatus, int] = dict.fromkeys(ActionStatus, 0)
    for action in result.actions:
        counts[action.status] += 1

    created = counts[ActionStatus.CREATED]
    updated = counts[ActionStatus.UPDATED]
    skipped = counts[ActionStatus.SKIPPED]
    failed = counts[ActionStatus.FAILED]
    deleted = counts[ActionStatus.DELETED]

    print(f"\n{created} created, {updated} updated, {deleted} deleted," f" {skipped} skipped, {failed} failed")

    if result.failed_projects:
        print(f"Failed projects: {', '.join(result.failed_projects)}", file=sys.stderr)
        return 1

    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    """Handle ``tenantctl init``."""
    target = Path(args.config_dir)
    try:
        written = write_sample_config(target)
    except FileExistsError as exc:
        logger.error("%s", exc)
        return 1
    for path in written:
        print(f"  created {path}")
    print(f"\nConfig directory initialised at {target}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``tenantctl run`` (default when no subcommand given)."""
    setup_logging(args.verbose)

    # Convert --only strings to ReconcileScope enum set.
    only: set[ReconcileScope] | None = None
    if args.only:
        try:
            only = {ReconcileScope(s) for s in args.only}
        except ValueError:
            valid = ", ".join(s.value for s in ReconcileScope)
            logger.error("Invalid --only value. Valid scopes: %s", valid)
            return 1

    client = TenantCtl.from_config_dir(args.config_dir, cloud=args.os_cloud)

    try:
        result = client.run(
            project=args.project,
            dry_run=args.dry_run,
            offline=args.offline,
            only=only,
            auto_expand_deps=args.auto_deps,
        )
    except ConfigValidationError as exc:
        for err in exc.errors:
            logger.error("  %s", err)
        return 1
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    except ProvisionerError:
        return 1

    return _print_summary(result, dry_run=args.dry_run)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Provision OpenStack projects from declarative config.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tenantctl {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- init -----------------------------------------------------------
    init_parser = subparsers.add_parser(
        "init",
        help="Bootstrap a sample config directory",
    )
    init_parser.add_argument(
        "--config-dir",
        default="config/",
        help="Target directory for generated config (default: config/)",
    )

    # --- run (also the default when no subcommand is given) -------------
    run_parser = subparsers.add_parser(
        "run",
        help="Reconcile projects (default)",
    )
    _add_run_arguments(run_parser)

    # Also accept run flags on the top-level parser for backward compat.
    _add_run_arguments(parser)

    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the flags shared by the top-level and ``run`` subcommand."""
    parser.add_argument(
        "--config-dir",
        default="config/",
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--os-cloud",
        default=None,
        help="Named cloud from clouds.yaml",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Filter to a single project name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print planned actions without making changes",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Skip OpenStack connection in dry-run mode (no live cloud reads)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="SCOPE",
        help=(
            "Restrict reconciliation to specific resource scopes "
            f"(choices: {', '.join(s.value for s in ReconcileScope)})"
        ),
    )
    parser.add_argument(
        "--auto-deps",
        action="store_true",
        default=False,
        help="Auto-expand --only scopes to include prerequisite scopes",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (repeat for more: -v=INFO, -vv=DEBUG)",
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 on success, 1 on failure."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(args)

    # "run" subcommand or no subcommand (backward compat)
    return _cmd_run(args)


def cli() -> None:
    """Console script entry point."""
    sys.exit(main())
