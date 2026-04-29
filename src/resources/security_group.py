"""Baseline security-group provisioning for OpenStack projects.

Creates the initial security group with a defined set of rules when a
project is first seeded.  Non-default SGs are created once and then
left to project teams.

For the ``"default"`` SG (auto-created by OpenStack for every project),
rules are added only when the SG is unconfigured (exactly 4 auto-created
rules).  Once configured, the SG is left to the project team.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openstack.connection import Connection
    from openstack.network.v2.security_group import SecurityGroup
    from openstack.network.v2.security_group_rule import (
        SecurityGroupRule as SdkSecurityGroupRule,
    )

    from src.models import ProjectConfig

from openstack.exceptions import ConflictException

from src.models import (
    SecurityGroupRule,  # noqa: TC001
)
from src.utils import Action, ActionStatus, SharedContext, retry

logger = logging.getLogger(__name__)


@retry()
def _find_security_group(conn: Connection, sg_name: str, project_id: str) -> SecurityGroup | None:
    """Look up a security group by name scoped to *project_id*."""
    return conn.network.find_security_group(sg_name, project_id=project_id)  # type: ignore[no-any-return]


@retry()
def _create_security_group(
    conn: Connection,
    sg_name: str,
    project_id: str,
) -> SecurityGroup:
    """Create a new Neutron security group."""
    return conn.network.create_security_group(  # type: ignore[no-any-return]
        name=sg_name,
        project_id=project_id,
        description="Baseline security group",
    )


@retry()
def _create_security_group_rule(
    conn: Connection,
    security_group_id: str,
    project_id: str,
    rule: SecurityGroupRule,
) -> SdkSecurityGroupRule:
    """Add a single rule to an existing security group."""
    return conn.network.create_security_group_rule(  # type: ignore[no-any-return]
        security_group_id=security_group_id,
        project_id=project_id,
        **rule.to_api_dict(),
    )


_RuleFingerprint = tuple[str | None, str | None, int | None, int | None, str | None]


def _rule_fingerprint(rule: SecurityGroupRule | dict[str, Any]) -> _RuleFingerprint:
    """Create a comparable fingerprint for a security group rule.

    Two rules are considered equivalent when they match on direction,
    protocol, port range, and remote CIDR.  Accepts both typed
    ``SecurityGroupRule`` and raw dicts (from OpenStack SDK responses).
    """
    if isinstance(rule, dict):
        return (
            rule.get("direction"),
            rule.get("protocol"),
            rule.get("port_range_min"),
            rule.get("port_range_max"),
            rule.get("remote_ip_prefix"),
        )
    return (
        rule.direction or None,
        rule.protocol,
        rule.port_range_min,
        rule.port_range_max,
        rule.remote_ip_prefix,
    )


def _find_missing_rules(
    configured: list[SecurityGroupRule],
    existing: list[dict[str, Any]],
) -> list[SecurityGroupRule]:
    """Return configured rules not yet present in *existing*."""
    present = {_rule_fingerprint(r) for r in existing}
    return [r for r in configured if _rule_fingerprint(r) not in present]


def ensure_baseline_sg(
    cfg: ProjectConfig,
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Ensure the baseline security group and its configured rules exist.

    For the ``"default"`` SG, missing configured rules are added
    (additive reconciliation).  Non-default SGs are created once and
    then left to project teams.
    """
    sg_cfg = cfg.security_group
    if not sg_cfg:
        return ctx.record(
            ActionStatus.SKIPPED,
            "security_group",
            "all",
            "no security_group configured",
        )

    sg_name: str = sg_cfg.name

    # Offline mode: no connection available.
    if ctx.conn is None:
        rule_count = len(sg_cfg.rules)
        return ctx.record(
            ActionStatus.SKIPPED,
            "security_group",
            sg_name,
            f"would ensure SG with {rule_count} rules (offline)",
        )

    # Check if SG already exists
    existing = _find_security_group(ctx.conn, sg_name, project_id)
    if existing is not None:
        # Only seed rules into unconfigured default SGs (exactly 4 auto-created
        # rules).  Once configured (>4 rules), the SG belongs to the project team.
        if sg_name == "default":
            existing_rules = existing.security_group_rules or []
            configured_rules = sg_cfg.rules
            logger.debug(
                "Default SG %s has %d existing rule(s), config wants %d rule(s)",
                existing.id,
                len(existing_rules),
                len(configured_rules),
            )

            if len(existing_rules) > 4:
                logger.debug(
                    "Default SG %s already configured (%d rules), skipping",
                    existing.id,
                    len(existing_rules),
                )
                return ctx.record(
                    ActionStatus.SKIPPED,
                    "security_group",
                    sg_name,
                    f"already configured ({len(existing_rules)} rules)",
                )

            for idx, rule in enumerate(existing_rules):
                logger.debug("  existing[%d] fingerprint: %s", idx, _rule_fingerprint(rule))
            for idx, rule in enumerate(configured_rules):
                logger.debug("  configured[%d] fingerprint: %s", idx, _rule_fingerprint(rule))
            missing = _find_missing_rules(configured_rules, existing_rules)
            logger.debug("  missing rule(s): %d", len(missing))
            if missing:
                if ctx.dry_run:
                    return ctx.record(
                        ActionStatus.UPDATED,
                        "security_group",
                        sg_name,
                        f"would add {len(missing)} rule(s) to default SG (id={existing.id})",
                    )
                rules_added = 0
                for rule in missing:
                    try:
                        _create_security_group_rule(ctx.conn, existing.id, project_id, rule)
                        rules_added += 1
                    except ConflictException:
                        logger.debug(
                            "Rule already exists (409), skipping: %s",
                            _rule_fingerprint(rule),
                        )
                logger.info(
                    "Added %d rule(s) to default security group %s",
                    rules_added,
                    existing.id,
                )
                return ctx.record(
                    ActionStatus.UPDATED,
                    "security_group",
                    sg_name,
                    f"id={existing.id}, added {rules_added} rule(s)",
                )
            return ctx.record(
                ActionStatus.SKIPPED,
                "security_group",
                sg_name,
                "all configured rules present",
            )

        # For non-default SGs, skip
        project_label = f"{cfg.name} ({project_id})"
        logger.debug(
            "Security group %s already exists for project %s, skipping",
            sg_name,
            project_label,
        )
        return ctx.record(
            ActionStatus.SKIPPED,
            "security_group",
            sg_name,
            "already exists",
        )

    # SG doesn't exist — online dry-run: report what would be created.
    if ctx.dry_run:
        return ctx.record(
            ActionStatus.CREATED,
            "security_group",
            sg_name,
            f"would create with {len(sg_cfg.rules)} rule(s)",
        )

    # Create the baseline security group.
    sg = _create_security_group(ctx.conn, sg_name, project_id)
    logger.info("Created security group %s (%s)", sg_name, sg.id)

    # Add each configured rule.
    rules_added = 0
    for rule in sg_cfg.rules:
        _create_security_group_rule(ctx.conn, sg.id, project_id, rule)
        rules_added += 1

    logger.info("Added %d rule(s) to security group %s", rules_added, sg_name)

    return ctx.record(
        ActionStatus.CREATED,
        "security_group",
        sg_name,
        f"id={sg.id}, rules={rules_added}",
    )
