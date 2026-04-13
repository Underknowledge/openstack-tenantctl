"""Tests for baseline security-group provisioning — ensure_baseline_sg."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from openstack.exceptions import ConflictException, HttpException

from src.resources.security_group import ensure_baseline_sg
from src.utils import ActionStatus, SharedContext

if TYPE_CHECKING:
    from src.models import ProjectConfig


class TestSecurityGroupCreation:
    """Creating new security groups with configured rules."""

    def test_create_sg_with_rules(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """New SG is created with correct name, project_id, and all rules."""
        shared_ctx.conn.network.find_security_group.return_value = None

        created_sg = MagicMock()
        created_sg.id = "sg-new-001"
        shared_ctx.conn.network.create_security_group.return_value = created_sg

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Verify action outcome
        assert action.status == ActionStatus.CREATED
        assert action.resource_type == "security_group"
        assert action.name == "default"
        assert "sg-new-001" in action.details
        assert "rules=2" in action.details

        # Verify SG was created with correct parameters
        create_call = shared_ctx.conn.network.create_security_group.call_args
        assert create_call[1]["name"] == "default"
        assert create_call[1]["project_id"] == "proj-123"
        assert create_call[1]["description"] == "Baseline security group"

        # Verify both rules were created for the new SG
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2
        rule_calls = shared_ctx.conn.network.create_security_group_rule.call_args_list

        # First rule: ICMP
        rule1_kwargs = rule_calls[0][1]
        assert rule1_kwargs["security_group_id"] == "sg-new-001"
        assert rule1_kwargs["project_id"] == "proj-123"
        assert rule1_kwargs["direction"] == "ingress"
        assert rule1_kwargs["protocol"] == "icmp"
        assert rule1_kwargs["remote_ip_prefix"] == "0.0.0.0/0"

        # Second rule: SSH (TCP port 22)
        rule2_kwargs = rule_calls[1][1]
        assert rule2_kwargs["security_group_id"] == "sg-new-001"
        assert rule2_kwargs["project_id"] == "proj-123"
        assert rule2_kwargs["direction"] == "ingress"
        assert rule2_kwargs["protocol"] == "tcp"
        assert rule2_kwargs["port_range_min"] == 22
        assert rule2_kwargs["port_range_max"] == 22
        assert rule2_kwargs["remote_ip_prefix"] == "0.0.0.0/0"

    def test_create_non_default_sg(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Non-default SG name that doesn't exist yet follows full create path."""
        from src.models import SecurityGroupConfig, SecurityGroupRule

        cfg = dataclasses.replace(
            sample_project_cfg,
            security_group=SecurityGroupConfig(
                name="my-app-sg",
                rules=[
                    SecurityGroupRule(
                        direction="ingress",
                        protocol="tcp",
                        port_range_min=80,
                        port_range_max=80,
                        remote_ip_prefix="0.0.0.0/0",
                        description="Allow HTTP",
                    ),
                    SecurityGroupRule(
                        direction="ingress",
                        protocol="tcp",
                        port_range_min=443,
                        port_range_max=443,
                        remote_ip_prefix="0.0.0.0/0",
                        description="Allow HTTPS",
                    ),
                ],
            ),
        )

        # SG does not exist yet
        shared_ctx.conn.network.find_security_group.return_value = None

        created_sg = MagicMock()
        created_sg.id = "sg-app-001"
        shared_ctx.conn.network.create_security_group.return_value = created_sg

        action = ensure_baseline_sg(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert action.name == "my-app-sg"
        assert "sg-app-001" in action.details
        assert "rules=2" in action.details

        # Verify SG was created with correct name
        create_call = shared_ctx.conn.network.create_security_group.call_args
        assert create_call[1]["name"] == "my-app-sg"

        # Verify both HTTP and HTTPS rules were created
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2

    def test_create_sg_with_empty_rules_list(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Security group with empty rules list creates SG with no rules."""
        from src.models import SecurityGroupConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            security_group=SecurityGroupConfig(name="empty-sg", rules=[]),
        )

        # SG does not exist yet
        shared_ctx.conn.network.find_security_group.return_value = None

        created_sg = MagicMock()
        created_sg.id = "sg-empty-001"
        shared_ctx.conn.network.create_security_group.return_value = created_sg

        action = ensure_baseline_sg(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.CREATED
        assert "sg-empty-001" in action.details
        assert "rules=0" in action.details

        shared_ctx.conn.network.create_security_group.assert_called_once()
        shared_ctx.conn.network.create_security_group_rule.assert_not_called()


class TestSkipExistingSecurityGroups:
    """Security groups that already exist are skipped appropriately."""

    def test_skip_non_default_sg(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Non-default SG names are always skipped when they exist."""
        # Use a non-default SG name to avoid the special default SG handling
        from src.models import SecurityGroupConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            security_group=SecurityGroupConfig(name="my-custom-sg", rules=[]),
        )

        existing_sg = MagicMock()
        existing_sg.id = "sg-existing"
        shared_ctx.conn.network.find_security_group.return_value = existing_sg

        action = ensure_baseline_sg(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert action.details == "already exists"
        shared_ctx.conn.network.create_security_group.assert_not_called()
        shared_ctx.conn.network.create_security_group_rule.assert_not_called()

    def test_skip_when_not_configured(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """When no security_group key is present in config, provisioning is skipped."""
        cfg = dataclasses.replace(sample_project_cfg, security_group=None)

        action = ensure_baseline_sg(cfg, "proj-123", shared_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert action.details == "no security_group configured"
        shared_ctx.conn.network.find_security_group.assert_not_called()
        shared_ctx.conn.network.create_security_group.assert_not_called()
        shared_ctx.conn.network.create_security_group_rule.assert_not_called()

    def test_dry_run_sg_not_found(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Online dry-run: SG not found → CREATED with rule count."""
        dry_run_ctx.conn.network.find_security_group.return_value = None

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", dry_run_ctx)

        assert action.status == ActionStatus.CREATED
        assert "would create with 2 rule(s)" in action.details
        # Reads happened
        dry_run_ctx.conn.network.find_security_group.assert_called_once()
        # No writes
        dry_run_ctx.conn.network.create_security_group.assert_not_called()
        dry_run_ctx.conn.network.create_security_group_rule.assert_not_called()

    def test_dry_run_sg_exists(
        self,
        dry_run_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Online dry-run: non-default SG exists → SKIPPED."""
        import dataclasses

        from src.models import SecurityGroupConfig

        cfg = dataclasses.replace(
            sample_project_cfg,
            security_group=SecurityGroupConfig(name="baseline", rules=[]),
        )
        existing = MagicMock()
        existing.id = "sg-existing"
        existing.name = "baseline"
        dry_run_ctx.conn.network.find_security_group.return_value = existing

        action = ensure_baseline_sg(cfg, "proj-123", dry_run_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "already exists" in action.details
        dry_run_ctx.conn.network.create_security_group.assert_not_called()

    def test_offline_dry_run_skips(
        self,
        offline_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Offline mode → SKIPPED with no API calls."""
        action = ensure_baseline_sg(sample_project_cfg, "proj-123", offline_ctx)

        assert action.status == ActionStatus.SKIPPED
        assert "offline" in action.details


class TestDefaultSecurityGroupRuleReconciliation:
    """Default SG gets additive reconciliation based on rule count threshold."""

    def test_configure_unconfigured_default_sg(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Unconfigured auto-created 'default' SG (<=4 rules) gets missing rules added."""
        # Simulate an unconfigured auto-created default SG with exactly 4 rules
        existing_default = MagicMock()
        existing_default.id = "sg-default-auto"
        existing_default.security_group_rules = [
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
            {"direction": "ingress", "ethertype": "IPv4"},
            {"direction": "ingress", "ethertype": "IPv6"},
        ]
        shared_ctx.conn.network.find_security_group.return_value = existing_default

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should UPDATE (add rules) not SKIP
        assert action.status == ActionStatus.UPDATED
        assert action.resource_type == "security_group"
        assert action.name == "default"
        assert "sg-default-auto" in action.details
        assert "added 2 rule(s)" in action.details

        # Should not create a new SG
        shared_ctx.conn.network.create_security_group.assert_not_called()

        # Should add the 2 missing configured rules (ICMP and SSH)
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2
        rule_calls = shared_ctx.conn.network.create_security_group_rule.call_args_list

        # Verify first added rule is ICMP
        rule1_kwargs = rule_calls[0][1]
        assert rule1_kwargs["security_group_id"] == "sg-default-auto"
        assert rule1_kwargs["direction"] == "ingress"
        assert rule1_kwargs["protocol"] == "icmp"
        assert rule1_kwargs["remote_ip_prefix"] == "0.0.0.0/0"

        # Verify second added rule is SSH
        rule2_kwargs = rule_calls[1][1]
        assert rule2_kwargs["security_group_id"] == "sg-default-auto"
        assert rule2_kwargs["direction"] == "ingress"
        assert rule2_kwargs["protocol"] == "tcp"
        assert rule2_kwargs["port_range_min"] == 22
        assert rule2_kwargs["port_range_max"] == 22

    def test_default_sg_exactly_four_rules(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Default SG with exactly 4 rules (at threshold) should be configured."""
        # Default SG with exactly 4 rules (at the threshold)
        existing_default = MagicMock()
        existing_default.id = "sg-default-four"
        existing_default.security_group_rules = [
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
            {"direction": "ingress", "ethertype": "IPv4"},
            {"direction": "ingress", "ethertype": "IPv6"},
        ]
        shared_ctx.conn.network.find_security_group.return_value = existing_default

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should UPDATE (add rules) because <=4 means unconfigured
        assert action.status == ActionStatus.UPDATED
        assert "added 2 rule(s)" in action.details

        # Verify the threshold decision logic works correctly
        shared_ctx.conn.network.create_security_group.assert_not_called()
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2

    def test_skip_configured_default_sg(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Already-configured 'default' SG (>4 rules) is skipped entirely."""
        # Simulate a default SG with >4 rules (already configured by project team)
        existing_default = MagicMock()
        existing_default.id = "sg-default-configured"
        existing_default.security_group_rules = [
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
            {"direction": "ingress", "ethertype": "IPv4"},
            {"direction": "ingress", "ethertype": "IPv6"},
            {"direction": "ingress", "protocol": "tcp", "port_range_min": 80},
        ]
        shared_ctx.conn.network.find_security_group.return_value = existing_default

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should SKIP — more than 4 rules means it's already been configured
        assert action.status == ActionStatus.SKIPPED
        assert action.details == "already configured (5 rules)"

        # Should not create SG or add rules
        shared_ctx.conn.network.create_security_group.assert_not_called()
        shared_ctx.conn.network.create_security_group_rule.assert_not_called()

    def test_default_sg_all_rules_present(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """Default SG with unconfigured count but all configured rules already present."""
        # Default SG with exactly 4 base rules, but they match our configured rules
        existing_default = MagicMock()
        existing_default.id = "sg-default-complete"
        existing_default.security_group_rules = [
            # The two configured rules from sample_project_cfg already present
            {
                "direction": "ingress",
                "protocol": "icmp",
                "remote_ip_prefix": "0.0.0.0/0",
                "port_range_min": None,
                "port_range_max": None,
            },
            {
                "direction": "ingress",
                "protocol": "tcp",
                "port_range_min": 22,
                "port_range_max": 22,
                "remote_ip_prefix": "0.0.0.0/0",
            },
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
        ]

        shared_ctx.conn.network.find_security_group.return_value = existing_default

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should SKIP because all configured rules are already present (fingerprint match)
        assert action.status == ActionStatus.SKIPPED
        assert action.details == "all configured rules present"

        # Should not create SG or add rules
        shared_ctx.conn.network.create_security_group.assert_not_called()
        shared_ctx.conn.network.create_security_group_rule.assert_not_called()


class TestConflictExceptionHandling:
    """409 ConflictException during rule creation is handled gracefully."""

    def test_partial_conflict_one_succeeds_one_fails(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """First rule succeeds, second gets 409 conflict — partial success."""
        # Unconfigured default SG with exactly 4 rules
        existing_default = MagicMock()
        existing_default.id = "sg-default-conflict"
        existing_default.security_group_rules = [
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
            {"direction": "ingress", "ethertype": "IPv4"},
            {"direction": "ingress", "ethertype": "IPv6"},
        ]
        shared_ctx.conn.network.find_security_group.return_value = existing_default

        # First rule succeeds, second gets a 409 conflict
        created_rule = MagicMock()
        created_rule.id = "rule-icmp-001"
        shared_ctx.conn.network.create_security_group_rule.side_effect = [
            created_rule,  # ICMP rule succeeds
            ConflictException("Rule already exists"),  # SSH rule conflicts
        ]

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should still report UPDATED with only the 1 successful rule
        assert action.status == ActionStatus.UPDATED
        assert action.resource_type == "security_group"
        assert "sg-default-conflict" in action.details
        assert "added 1 rule(s)" in action.details

        # Both rules were attempted
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2

        # Verify first rule was the ICMP rule (succeeded)
        first_call_kwargs = shared_ctx.conn.network.create_security_group_rule.call_args_list[0][1]
        assert first_call_kwargs["protocol"] == "icmp"

        # Verify second rule was the SSH rule (conflicted)
        second_call_kwargs = shared_ctx.conn.network.create_security_group_rule.call_args_list[1][1]
        assert second_call_kwargs["protocol"] == "tcp"
        assert second_call_kwargs["port_range_min"] == 22

    def test_all_rules_conflict_zero_added(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """All rule creations raise ConflictException — different outcome than partial."""
        # Unconfigured default SG with exactly 4 rules
        existing_default = MagicMock()
        existing_default.id = "sg-default-all-conflict"
        existing_default.security_group_rules = [
            {"direction": "egress", "ethertype": "IPv4"},
            {"direction": "egress", "ethertype": "IPv6"},
            {"direction": "ingress", "ethertype": "IPv4"},
            {"direction": "ingress", "ethertype": "IPv6"},
        ]
        shared_ctx.conn.network.find_security_group.return_value = existing_default

        # All rule creations return 409 conflict
        shared_ctx.conn.network.create_security_group_rule.side_effect = [
            ConflictException("Rule already exists"),
            ConflictException("Rule already exists"),
        ]

        action = ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # Should report UPDATED but with 0 rules added (all skipped due to conflicts)
        assert action.status == ActionStatus.UPDATED
        assert "added 0 rule(s)" in action.details

        # Both rules were attempted
        assert shared_ctx.conn.network.create_security_group_rule.call_count == 2


class TestErrorHandling:
    """Non-Conflict exceptions are raised (not swallowed)."""

    def test_rule_creation_non_conflict_exception(
        self,
        shared_ctx: SharedContext,
        sample_project_cfg: ProjectConfig,
    ) -> None:
        """First rule succeeds, second raises non-Conflict exception — propagates."""
        # SG does not exist yet
        shared_ctx.conn.network.find_security_group.return_value = None

        created_sg = MagicMock()
        created_sg.id = "sg-partial-fail"
        shared_ctx.conn.network.create_security_group.return_value = created_sg

        # First rule succeeds, second raises HttpException (not ConflictException)
        # Note: @retry() will retry the second call, so we need a callable that
        # succeeds once then always fails
        call_count = {"count": 0}

        def side_effect_fn(*args, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return MagicMock()  # First rule succeeds
            raise HttpException(message="Service unavailable")  # Second rule fails

        shared_ctx.conn.network.create_security_group_rule.side_effect = side_effect_fn

        # Should raise the HttpException (not handled like ConflictException)
        with pytest.raises(HttpException, match="Service unavailable"):
            ensure_baseline_sg(sample_project_cfg, "proj-123", shared_ctx)

        # SG was created
        shared_ctx.conn.network.create_security_group.assert_called_once()

        # First rule was added once, second failed and was retried (so >1 calls total)
        assert shared_ctx.conn.network.create_security_group_rule.call_count > 1
