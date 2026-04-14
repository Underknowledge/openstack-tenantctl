from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

if TYPE_CHECKING:
    from openstack.identity.v3._proxy import Proxy as IdentityV3Proxy
    from openstack.network.v2.network import Network

    from src.models import ProjectConfig
    from src.state_store import StateStore

import openstack
import openstack.connection
import openstack.exceptions
import openstack.resource
import requests.exceptions
import tenacity

# Broad set of transient/connection exceptions to retry on.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    openstack.exceptions.HttpException,  # 5xx / 429
    openstack.exceptions.SDKException,  # wraps lower-level errors
    requests.exceptions.ConnectionError,  # socket-level failures
    ConnectionError,  # stdlib
)

# Known 4xx exception subclasses that should never be retried.
# In openstacksdk 4.x status_code is None unless a response is attached,
# so we check the exception type directly.
_NON_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    openstack.exceptions.BadRequestException,
    openstack.exceptions.ConflictException,
    openstack.exceptions.EndpointNotFound,
    openstack.exceptions.ForbiddenException,
    openstack.exceptions.NotFoundException,
    openstack.exceptions.PreconditionFailedException,
    openstack.exceptions.ResourceNotFound,
)


class ProvisionerError(Exception):
    """Base exception for expected provisioner failures."""


class DryRunUnsupportedError(ProvisionerError):
    """Raised when an operation requires a live connection unavailable in dry-run mode."""


class SafetyCheckError(ProvisionerError):
    """Raised when a safety check prevents a destructive operation."""


class TeardownError(ProvisionerError):
    """Raised when project teardown encounters one or more failures."""


class ActionStatus(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class Action:
    status: ActionStatus
    resource_type: str
    name: str
    details: str = ""
    project_id: str = ""
    project_name: str = ""


@dataclass
class SharedContext:
    """Mutable state shared across all resource reconciliation steps.

    This context is designed for **single-threaded, sequential** use.  The
    ``actions`` and ``failed_projects`` lists, as well as the
    ``current_project_*`` fields, are mutated in-place without
    synchronization.  If project reconciliation is ever parallelized, each
    project must receive its own context copy (or these fields must be made
    thread-safe) and results merged after a join.
    """

    conn: openstack.connection.Connection | None = None
    dry_run: bool = False
    external_net_id: str = ""
    external_subnet_id: str = ""
    external_network_map: dict[str, str] = field(default_factory=dict)
    _resolved_external_networks: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    current_mapping_rules: list[dict[str, Any]] = field(default_factory=list)
    mapping_exists: bool = False
    static_mapping_rules: list[dict[str, Any]] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    failed_projects: list[str] = field(default_factory=list)
    current_project_id: str = ""
    current_project_name: str = ""
    state_store: StateStore | None = None

    def record(
        self,
        status: ActionStatus,
        resource_type: str,
        name: str,
        details: str = "",
        project_id: str | None = None,
        project_name: str | None = None,
    ) -> Action:
        action = Action(
            status=status,
            resource_type=resource_type,
            name=name,
            details=details,
            project_id=(project_id if project_id is not None else self.current_project_id),
            project_name=(project_name if project_name is not None else self.current_project_name),
        )
        self.actions.append(action)
        return action


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception is transient and worth retrying."""
    if isinstance(exc, _NON_RETRYABLE_EXCEPTIONS):
        return False
    if isinstance(exc, openstack.exceptions.HttpException):
        status_code: int | None = getattr(exc, "status_code", None)
        return not (status_code is not None and status_code < 500 and status_code != 429)
    return isinstance(exc, RETRYABLE_EXCEPTIONS)


def _make_before_sleep(
    max_attempts: int,
) -> Callable[[tenacity.RetryCallState], None]:
    """Create a before-sleep callback that logs retry attempts."""

    def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
        log = logging.getLogger(__name__)
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, openstack.exceptions.HttpException):
            reason = f"HTTP {getattr(exc, 'status_code', None)}"
        else:
            reason = type(exc).__name__ if exc else "unknown"
        sleep_time = retry_state.next_action.sleep if retry_state.next_action else 0
        fn = retry_state.fn
        func_name = fn.__name__ if fn else "unknown"
        log.warning(
            "Retry %d/%d for %s (%s), sleeping %.1fs",
            retry_state.attempt_number,
            max_attempts,
            func_name,
            reason,
            sleep_time,
        )

    return _before_sleep


def retry(max_attempts: int = 5, backoff_base: float = 2.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Retry decorator with exponential backoff for transient OpenStack errors.

    Retries on:
    - HTTP 5xx server errors and 429 rate-limit responses
    - Connection-level errors (socket resets, remote disconnects, timeouts)
    Backoff schedule: backoff_base * 2^attempt (e.g. 2s, 4s, 8s, 16s).
    """
    return tenacity.retry(
        retry=tenacity.retry_if_exception(_is_retryable),
        stop=tenacity.stop_after_attempt(max_attempts),
        wait=tenacity.wait_exponential(multiplier=backoff_base / 2, exp_base=2),
        before_sleep=_make_before_sleep(max_attempts),
        reraise=True,
    )


def identity_v3(conn: openstack.connection.Connection) -> IdentityV3Proxy:
    """Return the Keystone v3 identity proxy.

    OpenStack SDK types ``conn.identity`` as ``v2.Proxy | v3.Proxy``.
    This project targets Keystone v3 exclusively (v2 was removed in 2020).
    """
    return conn.identity  # type: ignore[return-value,no-any-return]


@retry()
def find_network(conn: openstack.connection.Connection, net_name: str, project_id: str) -> Network | None:
    """Look up a network by name scoped to project_id.

    Args:
        conn: OpenStack connection object
        net_name: Name of the network to find
        project_id: Project/tenant ID to scope the search

    Returns:
        Network resource if found, None otherwise
    """
    return conn.network.find_network(net_name, project_id=project_id)  # type: ignore[no-any-return]


@retry()
def find_external_network(
    conn: openstack.connection.Connection,
    name: str,
) -> openstack.resource.Resource | None:
    """Look up an external network by name.

    Args:
        conn: OpenStack connection object
        name: Name of the external network to find

    Returns:
        Network resource if found, None otherwise
    """
    result: openstack.resource.Resource | None = conn.network.find_network(name)
    return result


@retry()
def find_subnet(
    conn: openstack.connection.Connection,
    name_or_id: str,
) -> openstack.resource.Resource | None:
    """Look up a subnet by name or ID.

    Args:
        conn: OpenStack connection object
        name_or_id: Name or ID of the subnet to find

    Returns:
        Subnet resource if found, None otherwise
    """
    result: openstack.resource.Resource | None = conn.network.find_subnet(name_or_id)
    return result


@retry()
def list_external_network_subnets(
    conn: openstack.connection.Connection,
    network_id: str,
) -> list[openstack.resource.Resource]:
    """List all subnets in an external network.

    Args:
        conn: OpenStack connection object
        network_id: ID of the external network

    Returns:
        List of subnet resources
    """
    return list(conn.network.subnets(network_id=network_id))


def resolve_external_subnet(
    conn: openstack.connection.Connection,
    external_net_id: str,
    configured_subnet: str,
) -> str:
    """Resolve external network subnet ID for router gateway and FIPs.

    Args:
        conn: OpenStack connection
        external_net_id: Resolved external network ID
        configured_subnet: User-configured subnet name or ID (may be empty)

    Returns:
        Subnet ID or empty string if not resolvable

    Strategy:
    1. If configured explicitly, look it up and validate it belongs to external network
    2. If single subnet exists in external network, use it
    3. If multiple subnets, prefer first IPv4 subnet with warning
    4. If none found, return empty string
    """
    log = logging.getLogger(__name__)

    # Case 1: User specified subnet (name or ID)
    if configured_subnet:
        subnet = find_subnet(conn, configured_subnet)
        if subnet is None:
            log.error(
                "Configured external subnet '%s' not found. Run "
                "'openstack subnet list --network <ext-net>' to see available subnets.",
                configured_subnet,
            )
            msg = f"Subnet '{configured_subnet}' not found"
            raise ProvisionerError(msg)

        # Validate subnet belongs to external network
        if str(subnet.network_id) != external_net_id:
            log.error(
                "Configured subnet '%s' belongs to network %s, but external "
                "network is %s. The subnet must belong to the external network. "
                "Run 'openstack subnet list --network <ext-net>' to see valid subnets.",
                configured_subnet,
                subnet.network_id,
                external_net_id,
            )
            msg = f"Subnet '{configured_subnet}' does not belong to external network"
            raise ProvisionerError(msg)

        # Nudge to use ID if name was provided
        if configured_subnet != str(subnet.id):
            log.info(
                "Resolved external subnet '%s' -> %s (%s). " "Tip: Use ID '%s' in config to skip name lookup",
                configured_subnet,
                subnet.id,
                subnet.cidr,
                subnet.id,
            )
        else:
            log.info(
                "Resolved external subnet %s (%s)",
                subnet.id,
                subnet.cidr,
            )
        return str(subnet.id)

    # Case 2: Auto-discover from external network
    if not external_net_id:
        return ""

    subnets = list_external_network_subnets(conn, external_net_id)

    if len(subnets) == 0:
        log.warning("External network %s has no subnets", external_net_id)
        return ""

    if len(subnets) == 1:
        subnet_id = str(subnets[0].id)
        log.info(
            "Auto-selected single external subnet %s (%s)",
            subnets[0].name or subnet_id,
            subnets[0].cidr,
        )
        return subnet_id

    # Multiple subnets: prefer IPv4, fall back to first
    ipv4_subnets = [s for s in subnets if s.ip_version == 4]
    chosen = ipv4_subnets[0] if ipv4_subnets else subnets[0]
    subnet_id = str(chosen.id)
    label = "first IPv4" if ipv4_subnets else "first"

    subnet_list = "\n".join(f"  - Name: {s.name or 'unnamed':20} | ID: {s.id} | CIDR: {s.cidr}" for s in subnets)
    log.warning(
        "Multiple external subnets found, auto-selected %s: %s (%s).\n"
        "Available subnets:\n%s\n"
        "Set 'external_network_subnet' in defaults.yaml to your chosen "
        "subnet ID (recommended) or name.\n"
        "Tip: Using ID avoids name lookup overhead.",
        label,
        chosen.name or subnet_id,
        chosen.cidr,
        subnet_list,
    )
    return subnet_id


def resolve_project_external_network(
    cfg: ProjectConfig,
    ctx: SharedContext,
) -> tuple[str, str]:
    """Resolve external network and subnet for a specific project.

    Returns (network_id, subnet_id) tuple.

    If project has explicit external_network_name or external_network_subnet,
    resolves those via the cached ``external_network_map``.  Otherwise returns
    global values from SharedContext.  Results are cached per
    ``(network_name, subnet_name)`` pair so repeated calls for the same project
    incur no API overhead.

    Args:
        cfg: Project configuration
        ctx: Shared context with global defaults

    Returns:
        (external_net_id, external_subnet_id) tuple
    """
    log = logging.getLogger(__name__)

    # Fast path: no per-project overrides → return global defaults.
    if not cfg.external_network_name and not cfg.external_network_subnet:
        return ctx.external_net_id, ctx.external_subnet_id

    # Check result cache (keyed by the per-project override pair).
    cache_key = (cfg.external_network_name, cfg.external_network_subnet)
    cached = ctx._resolved_external_networks.get(cache_key)
    if cached is not None:
        return cached

    external_net_id = ctx.external_net_id
    external_subnet_id = ctx.external_subnet_id

    # Per-project external network override
    if cfg.external_network_name:
        log.info(
            "Project %s: resolving per-project external network '%s'",
            cfg.name,
            cfg.external_network_name,
        )

        net_id = ctx.external_network_map.get(cfg.external_network_name)
        if net_id:
            external_net_id = net_id
            log.info(
                "Project %s: resolved external network '%s' -> %s",
                cfg.name,
                cfg.external_network_name,
                external_net_id,
            )
            external_subnet_id = ""
        else:
            msg = f"Project {cfg.name}: external network '{cfg.external_network_name}' not found"
            raise ProvisionerError(msg)

    # Per-project external subnet override
    if cfg.external_network_subnet:
        log.info(
            "Project %s: resolving per-project external subnet '%s'",
            cfg.name,
            cfg.external_network_subnet,
        )

        if ctx.conn and external_net_id:
            resolved_subnet_id = resolve_external_subnet(ctx.conn, external_net_id, cfg.external_network_subnet)
            if resolved_subnet_id:
                external_subnet_id = resolved_subnet_id
                log.info(
                    "Project %s: using external subnet %s",
                    cfg.name,
                    external_subnet_id,
                )

    result = (external_net_id, external_subnet_id)
    ctx._resolved_external_networks[cache_key] = result
    return result


def setup_logging(verbosity: int) -> None:
    """Configure the root logger based on verbosity level.

    Args:
        verbosity: 0 for WARNING, 1 for INFO, 2+ for DEBUG.
    """
    level_map: dict[int, int] = {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG,
    }
    level = level_map.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if verbosity >= 2:
        openstack.enable_logging(debug=True)
