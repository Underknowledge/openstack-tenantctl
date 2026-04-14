# Architecture Decision Records

<!--
**Last Updated**: 2026-04-04
-->

We capture our key design decisions here, following the Architecture Decision Record (ADR) pattern.

Each decision includes:
- **Context**: The problem we're solving
- **Decision**: What we chose to do
- **Rationale**: Why we made this choice
- **Alternatives Considered**: What we didn't choose and why
- **Consequences**: Trade-offs and implications

## Table of Contents

### Core Architecture
- [DD-001: Three-Phase Execution Model](#dd-001-three-phase-execution-model)
- [DD-012: Project Lifecycle State Machine](#dd-012-project-lifecycle-state-machine)
- [DD-013: Teardown & Reverse Dependency Order](#dd-013-teardown--reverse-dependency-order)

### Configuration & Merging
- [DD-003: Deep-Merge Configuration Inheritance](#dd-003-deep-merge-configuration-inheritance)
- [DD-016: Security Group Rule Preset Expansion](#dd-016-security-group-rule-preset-expansion)

### Resource Patterns
- [DD-004: Universal Resource Pattern](#dd-004-universal-resource-pattern)
- [DD-009: Config Writeback for Idempotency](#dd-009-config-writeback-for-idempotency)
- [DD-011: Router IP Capture-and-Track Pattern](#dd-011-router-ip-capture-and-track-pattern)
- [DD-014: Drift Detection & Reconciliation Pattern](#dd-014-drift-detection--reconciliation-pattern)
- [DD-019: Optional FIP Reclamation](#dd-019-optional-fip-reclamation)
- [DD-020: Opt-In Resource Functions](#dd-020-opt-in-resource-functions)

### Operational Resilience
- [DD-005: Retry with Exponential Backoff](#dd-005-retry-with-exponential-backoff)
- [DD-007: Error Isolation Per Project](#dd-007-error-isolation-per-project)
- [DD-015: Graceful Service Degradation](#dd-015-graceful-service-degradation)

### Implementation Details
- [DD-006: SharedContext for Cross-Cutting Concerns](#dd-006-sharedcontext-for-cross-cutting-concerns)
- [DD-008: Federation Mapping as Shared Resource](#dd-008-federation-mapping-as-shared-resource)
- [DD-010: Deterministic Federation Rule Ordering](#dd-010-deterministic-federation-rule-ordering)
- [DD-017: Hardcoded Federation Defaults as Last-Resort Fallbacks](#dd-017-hardcoded-federation-defaults-as-last-resort-fallbacks)
- [DD-018: Separate State File for Observed State](#dd-018-separate-state-file-for-observed-state)
- [DD-022: File-Locking for State Store Concurrency](#dd-022-file-locking-for-state-store-concurrency)

### Archived/Rejected
- [DD-002: Allocate-Then-Lock Pattern (REJECTED)](#dd-002-allocate-then-lock-pattern-for-locked-resources)

---

## DD-001: Three-Phase Execution Model

**Status**: Accepted

### Context

We need the provisioner to:
1. Validate configuration before making any API calls
2. Connect to OpenStack and resolve shared resources
3. Provision resources for each project

We had to decide how to structure the execution flow to maximize reliability and minimize wasted effort.

### Decision

We implement a three-phase execution model:

1. **Phase 1 (Validate)**: Load and validate all configuration files
2. **Phase 2 (Connect & Resolve)**: Establish OpenStack connection and resolve shared resources
3. **Phase 3 (Reconcile)**: Provision resources for each project

Phases execute sequentially. Validation errors immediately exit before Phase 2. Connection errors exit before Phase 3.

### Rationale

**Fail-fast principle**: By catching configuration errors in Phase 1 (before any OpenStack API calls), we avoid wasted time and partial provisioning.

**Clear separation of concerns**:
- Phase 1: Pure configuration processing (no external dependencies)
- Phase 2: OpenStack API setup (connection, authentication, shared resource lookup)
- Phase 3: Actual provisioning work

**Dry-run with live reads**: In dry-run mode, Phase 2 connects normally so that Phase 3 can perform read-only cloud operations and produce field-level diffs (e.g., `cores: 10 → 20`). The `--offline` flag restores the old behavior of skipping Phase 2 entirely for instant, connectionless previews.

**Debugging**: Clear phase boundaries make it obvious where failures occur (config syntax vs. connectivity vs. provisioning logic).

### Alternatives Considered

**Alternative 1: Lazy validation**
- Validate each project's config only when processing that project
- **Rejected**: Leads to partial provisioning before we discover validation errors

**Alternative 2: Combined connect-and-provision**
- Connect to OpenStack then immediately start provisioning
- **Rejected**: No opportunity to resolve shared resources (external network, federation mapping) before project provisioning

**Alternative 3: Validate-on-the-fly**
- Check each configuration value right before using it
- **Rejected**: Scattered validation logic, harder to debug, slower fail-fast

### Consequences

**Positive**:
- Configuration errors caught immediately (before any API calls)
- Dry-run mode is zero-cost (no OpenStack connection)
- Clear failure points for debugging
- Shared resource resolution happens once, upfront

**Negative**:
- Slightly more complex orchestration code (three distinct phases)
- Can't provision any projects if connection fails (fail-together model)

**Mitigations**:
- We document the phase structure clearly in code and user-facing docs
- Connection retry logic minimizes "fail-together" impact

---

## DD-002: Allocate-Then-Lock Pattern (REJECTED)

**Status**: REJECTED

**Rejected**: Our original "lock quota to 0" approach doesn't work because Neutron rejects quotas below current usage. Even "locking to usage" isn't a true lock — users can still delete and recreate resources within quota limits. The actual working solution is DD-009 (Config Writeback) which tracks pre-allocated resources in the config file, combined with setting quota to the desired count.

**What actually works**: See DD-009 (Config Writeback for Idempotency). Our working pattern is **pre-allocation with quota enforcement**: pre-allocate resources → set quota to desired count → track allocated IDs in config → detect and reconcile drift. This isn't a true "lock" but rather pre-allocation with quota enforcement and drift tracking.

### Context

Some resources (floating IPs, networks) should be pre-allocated by us and then prevented from growing beyond the desired count. Our original idea was:
- Allocate the desired number of floating IPs for the project
- Lock the floating IP quota to 0 so users can't allocate additional IPs

The problem: **This doesn't work**. Neutron rejects quotas below current usage. If 3 FIPs exist, setting `floating_ips=0` fails. Even setting quota to current usage (3) isn't a "lock" — users can still delete and recreate FIPs within that quota.

### Decision (REJECTED)

Our originally proposed **allocate-then-lock** pattern was:

1. Temporarily **raise** the quota to allow allocation (e.g., `floating_ips: N`)
2. **Allocate** the resource(s) (create floating IPs)
3. **Persist** resource IDs/addresses back to the project YAML file
4. **Lock** the quota to 0 to prevent further allocation

**Why we rejected this**: Step 4 (lock to 0) is impossible. Neutron enforces quota >= usage. Setting quota to current usage (3) instead of 0 isn't a true lock.

On subsequent runs (in our working implementation):
- Check existing FIPs against desired count
- If equal: skip allocation and ensure quota is set to desired count
- If fewer: pre-allocate missing FIPs (scale-up)
- If more: release unused FIPs (scale-down); in-use FIPs produce a FAILED action
- Detect drift: adopt untracked FIPs, reclaim missing FIPs

### Rationale (Why We Thought This Would Work)

**Intent**: Lock quota to 0 to prevent users from creating resources, while our provisioner-allocated resources continue to exist.

**Problem**: We can't set quota below usage. Even setting quota to usage isn't a lock.

**What actually enables idempotency**: Config writeback (DD-009), not quota locking. Our config file tracks what was pre-allocated, enabling drift detection and reconciliation.

**Actual solution**: See DD-009. We set quota to desired count (not 0) and track pre-allocated resource IDs in config. Users can replace resources within quota but can't exceed the count. This is pre-allocation with quota enforcement, not a true lock.

### Alternatives Considered

**Alternative 1: Admin-allocated outside provisioner**
- Require an admin to manually allocate resources, then reference them in config
- **Rejected**: Extra manual steps, error-prone, not fully declarative

**Alternative 2: Lock-then-allocate**
- Lock quota to 1, then allocate one resource
- **Rejected**: Doesn't prevent users from deleting and re-creating (they still have quota of 1)

**Alternative 3: Post-allocation quota locking**
- Let users allocate freely, then periodically scan and lock quotas
- **Rejected**: Race condition window where users could allocate unwanted resources

**Alternative 4: RBAC-based restrictions**
- Use OpenStack RBAC to prevent users from creating floating IPs
- **Rejected**: More complex RBAC management, less transparent, harder to verify

**Alternative 5: Lock quota to current usage (not 0)**
- After allocating 3 FIPs, set quota to 3 instead of 0
- **We tried this, but it isn't a lock**: Users can delete FIP #1 and create FIP #4 (still within quota of 3)
- This limits total count but doesn't prevent deletion/replacement
- Not a true "lock" mechanism

### Consequences

**Positive**:
- Fully declarative: we specify `floating_ips: N` in config (any count)
- Idempotent: safe to re-run
- Self-documenting: allocated resources visible in config file
- No race conditions: quota locked immediately after allocation
- Scale-down support: reducing desired count automatically releases unused FIPs

**Negative**:
- Config file is mutated by us (not purely input)
- Manual edits to `locked_fips` section could cause issues
- Slightly complex logic (4-step process, plus scale-down branch)
- In-use FIPs can't be released automatically during scale-down

**Mitigations**:
- We clearly document that `locked_fips` is managed by the provisioner — don't edit manually
- Config file mutation is atomic (YAML write is transactional)
- Comprehensive tests ensure correctness of allocate-then-lock and scale-down logic
- FAILED action clearly reported when in-use FIPs block scale-down

---

## DD-003: Deep-Merge Configuration Inheritance

**Status**: Accepted

### Context

We want projects to inherit default configuration but allow overrides. The challenge is how to merge project-specific config with defaults:

- **Scalars** (strings, booleans, integers): Should the project value override the default?
- **Lists** (security group rules): Should they merge or override?
- **Dicts** (quotas): Should they merge recursively?

### Decision

We use **deep-merge** with the `deepmerge` library, configured with:

- **Dicts**: Merge recursively (both defaults and project values combined)
- **Lists**: Override completely (project value replaces default)
- **Scalars**: Override (project value replaces default)

```python
from deepmerge import Merger

_merger = Merger(
    type_strategies=[
        (list, ["override"]),    # Lists: project replaces defaults
        (dict, ["merge"]),       # Dicts: recursive merge
        (set, ["override"]),
    ],
    fallback_strategies=["override"],  # Scalars: project wins
)
```

### Rationale

**Dicts (merge)**: For nested configuration like quotas, we want projects to override specific values while inheriting others:
```yaml
# defaults: {compute: {cores: 20, ram: 51200}}
# project: {compute: {cores: 16}}
# result: {compute: {cores: 16, ram: 51200}}  # Merge
```

**Lists (override)**: For lists like security group rules, merging is rarely what we want. Projects usually need their own complete rule set:
```yaml
# defaults: [rule1, rule2]
# project: [rule3]
# result: [rule3]  # Override, not [rule1, rule2, rule3]
```

**Predictable**: We can reason about merge behavior without surprises.

### Alternatives Considered

**Alternative 1: Shallow merge**
- Only merge top-level keys, no recursion
- **Rejected**: Can't override individual quota values without duplicating the entire quota section

**Alternative 2: Merge lists**
- Append project lists to default lists
- **Rejected**: Security group rules would accumulate unexpectedly; no way to "remove" a default rule

**Alternative 3: Custom merge keys**
- Allow `__merge__: true` or `__override__: true` annotations in YAML
- **Rejected**: Too complex, non-standard YAML, harder to read

**Alternative 4: No inheritance**
- Require every project to specify full configuration
- **Rejected**: Massive duplication, hard to maintain consistent defaults

### Consequences

**Positive**:
- Minimal duplication: projects only specify what's different
- Intuitive: most merges work as expected
- Leverages the battle-tested `deepmerge` library

**Negative**:
- List override can surprise users expecting merge (e.g., security group rules)
- Can't selectively remove items from default lists

**Mitigations**:
- We clearly document merge behavior in CONFIG-SCHEMA.md and USER-GUIDE.md
- We provide examples showing list override behavior
- We use meaningful defaults that are generally applicable

---

## DD-004: Universal Resource Pattern

**Status**: Accepted

### Context

All our resource provisioning modules (project, network, quotas, etc.) need to:
1. Check if the resource exists
2. Create if missing, update if changed, skip if correct
3. Record the action taken
4. Support dry-run mode

We needed a consistent pattern to avoid duplicating this logic.

### Decision

All our resource modules follow the **universal resource pattern**:

```python
def ensure_<resource>(cfg, ctx, ...) -> Action:
    # 1. Extract config
    resource_config = cfg.get("field")

    # 2. Dry-run check
    if ctx.dry_run:
        return ctx.record(SKIPPED, "resource_type", name, "dry-run")

    # 3. Find existing resource
    existing = _find_resource(ctx.conn, name)

    # 4. Create if missing
    if existing is None:
        resource = _create_resource(ctx.conn, resource_config)
        return ctx.record(CREATED, "resource_type", name, details)

    # 5. Update if changed
    if _needs_update(existing, resource_config):
        resource = _update_resource(ctx.conn, existing.id, resource_config)
        return ctx.record(UPDATED, "resource_type", name, details)

    # 6. Skip if up to date
    return ctx.record(SKIPPED, "resource_type", name, "already up to date")
```

### Rationale

**Consistency**: All our resource modules share the same structure, making the codebase predictable and easy to navigate.

**Idempotency**: The find-create-update pattern ensures resources match configuration without unnecessary changes.

**Observability**: We record every operation, providing a complete audit trail.

**Testability**: The consistent structure makes it easy to write comprehensive tests for each resource type.

### Alternatives Considered

**Alternative 1: Declarative resource framework**
- Create a framework/DSL for resource definitions
- **Rejected**: Over-engineering for our 6 resource types; framework complexity not justified

**Alternative 2: Per-resource custom logic**
- Each resource module implements its own flow
- **Rejected**: Duplication, inconsistency, harder to maintain

**Alternative 3: Class-based resources**
- Define a `Resource` base class with `find()`, `create()`, `update()` methods
- **Rejected**: Overkill for simple functions; Python's functional style is clearer here

### Consequences

**Positive**:
- We know what to expect in every resource module
- Easy to add new resource types (copy the pattern)
- Tests follow the same structure for all resources
- Clear separation: config extraction, dry-run, find, create, update, skip

**Negative**:
- Some resources don't fit perfectly (e.g., federation mapping is shared, not per-project)
- The pattern is documented but not enforced by code

**Mitigations**:
- We document exceptions clearly (federation mapping's shared nature)
- Code reviews ensure new resources follow the pattern
- Tests verify pattern compliance

---

## DD-005: Retry with Exponential Backoff

**Status**: Accepted

### Context

Our OpenStack API calls can fail transiently due to:
- Network hiccups
- API server temporary overload (HTTP 5xx)
- Rate limiting (HTTP 429)
- Connection timeouts

We need to handle these gracefully without manual intervention.

### Decision

We wrap all OpenStack API calls with a `@retry()` decorator that:

- **Retries** on: HTTP 5xx, HTTP 429, connection errors, `SDKException`
- **Does not retry** on: HTTP 4xx client errors (except 429)
- **Backoff schedule**: Exponential (2s, 4s, 8s, 16s)
- **Max attempts**: 5 (total runtime: up to ~30s per call)

```python
@retry(max_attempts=5, backoff_base=2.0)
def _find_project(conn, name):
    return conn.identity.find_project(name, domain_id="default")
```

### Rationale

**Resilience**: Transient failures (network blips, temporary API overload) don't cause our provisioning to fail.

**Exponential backoff**: Gives overloaded systems time to recover without us hammering them with immediate retries.

**Selective retrying**: Client errors (4xx) indicate bugs or misconfiguration, not transient issues, so retrying would waste time.

**Logging**: We log each retry with reason and wait time, aiding debugging.

### Alternatives Considered

**Alternative 1: No retry**
- Let failures propagate immediately
- **Rejected**: Too brittle; transient failures would require manual re-runs

**Alternative 2: Infinite retry**
- Keep retrying until success
- **Rejected**: Could hang indefinitely on persistent failures; no timeout

**Alternative 3: Fixed-interval retry**
- Retry every 5 seconds
- **Rejected**: Doesn't adapt to severity; could overload struggling API servers

**Alternative 4: Circuit breaker pattern**
- Stop retrying after N consecutive failures across all calls
- **Rejected**: Over-engineering; our workload is sequential, not high-volume concurrent

### Consequences

**Positive**:
- Our provisioning survives transient network issues
- Reduces manual intervention for temporary failures
- Well-tested pattern (exponential backoff is industry standard)

**Negative**:
- Slow failures: a persistent error takes ~30s to fail (5 attempts)
- Could mask underlying issues if transient failures are frequent

**Mitigations**:
- Our logging shows retry attempts, making persistent issues visible
- 30s total is acceptable for the reliability we gain
- Max attempts configurable if needed

---

## DD-006: SharedContext for Cross-Cutting Concerns

**Status**: Accepted

### Context

Our resource provisioning functions need access to several shared values:
- OpenStack connection object
- Dry-run flag
- External network ID (resolved once, used by multiple resources)
- Federation mapping state
- Action recording list

Passing all these as individual parameters would create long function signatures.

### Decision

We created a `SharedContext` dataclass that bundles all cross-cutting concerns:

```python
@dataclass
class SharedContext:
    conn: openstack.connection.Connection
    dry_run: bool = False
    external_net_id: str = ""
    current_mapping_rules: list[Any] = field(default_factory=list)
    mapping_exists: bool = False
    static_mapping_rules: list[Any] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    failed_projects: list[str] = field(default_factory=list)
    current_project_id: str = ""
    current_project_name: str = ""
    state_store: StateStore | None = None  # DD-018

    def record(self, status, resource_type, name, details="") -> Action:
        """Record an action and return it."""
        action = Action(status, resource_type, name, details)
        self.actions.append(action)
        return action
```

All our resource functions take `ctx: SharedContext` as a parameter.

### Rationale

**Clean function signatures**: `ensure_project(cfg, ctx)` instead of `ensure_project(cfg, conn, dry_run, external_net_id, actions, ...)`

**Centralized action recording**: `ctx.record()` gives us a consistent interface.

**Easy to extend**: Adding new cross-cutting concerns (e.g., metrics, timing) just means adding a field to SharedContext.

**Type safety**: The dataclass with type hints catches errors at development time.

### Alternatives Considered

**Alternative 1: Global variables**
- Store connection, dry_run flag, etc. as module-level globals
- **Rejected**: Hard to test, not thread-safe, implicit dependencies

**Alternative 2: Passing individual parameters**
- `ensure_project(cfg, conn, dry_run, external_net_id, actions, failed_projects, ...)`
- **Rejected**: Long signatures, tedious to update when adding new concerns

**Alternative 3: Dependency injection container**
- Use a DI framework to inject dependencies
- **Rejected**: Overkill for a simple CLI tool; adds complexity

**Alternative 4: Class-based design**
- Create a `Provisioner` class with connection as instance variable
- **Rejected**: Stateful classes are harder to test than pure functions with explicit context

### Consequences

**Positive**:
- Clean, readable function signatures
- Easy to add new cross-cutting concerns
- Explicit dependency (context passed as parameter)
- Type-safe with mypy

**Negative**:
- One more concept to learn (SharedContext)
- Temptation to stuff unrelated data into context

**Mitigations**:
- We document SharedContext clearly in API-REFERENCE.md
- Code reviews ensure only true cross-cutting concerns go in context
- We keep context focused on provisioning-related state

---

## DD-007: Error Isolation Per Project

**Status**: Accepted

### Context

When provisioning multiple projects, we need to decide:
- Should one project's failure stop all provisioning?
- Should one project's failure affect other projects?

### Decision

We implement **per-project error isolation** in the reconciler:

```python
for cfg in projects:
    project_name = cfg["name"]
    try:
        _reconcile_project(cfg, ctx)
    except Exception:
        logger.error("Failed to reconcile project %s", project_name, exc_info=True)
        ctx.failed_projects.append(project_name)
        continue  # Don't block other projects
```

We record, log, and report failed projects, but other projects still attempt to provision.

### Rationale

**Maximize success**: One misconfigured or problematic project shouldn't prevent us from successfully provisioning other projects.

**Easier debugging**: We see which projects succeeded and which failed in one run, rather than fixing one failure and re-running to discover the next.

**Operational efficiency**: In a large deployment, partial success is better than total failure.

### Alternatives Considered

**Alternative 1: Fail-fast on first error**
- Stop all provisioning when any project fails
- **Rejected**: Inefficient for operators (must fix and re-run repeatedly)

**Alternative 2: Retry failed projects**
- Automatically retry failed projects after successful ones
- **Rejected**: Adds complexity; failures are usually config errors that won't fix themselves

**Alternative 3: Parallel provisioning with failure isolation**
- Provision all projects in parallel, isolate failures
- **Rejected**: Increases complexity; sequential provisioning is simpler and sufficient for us

### Consequences

**Positive**:
- Maximum provisioning in one run (all successful projects get provisioned)
- Easier troubleshooting (we see all failures at once)
- Clear failure reporting (list of failed project names)

**Negative**:
- Partial success can be confusing ("did it work or not?")
- Failed projects still leave partial state in OpenStack

**Mitigations**:
- Clear output: "11 created, 0 updated, 4 skipped, 2 failed"
- Exit code 1 if any failures (CI/CD detects partial failure)
- We list failed projects by name for easy identification

---

## DD-008: Federation Mapping as Shared Resource

**Status**: Accepted

### Context

Identity federation mapping in OpenStack:
- Is a single resource shared across all projects
- Contains rules mapping IdP groups to project roles
- Each project contributes its own rules to the mapping

We needed to decide how to manage this shared resource.

### Decision

**Federation mapping is a shared resource**, reconciled **after all projects**:

1. Each project config specifies its federation rules
2. During Phase 3, we collect all projects' federation configs
3. After all per-project resources are provisioned, we reconcile the federation mapping:
   - Build rules from all project configs
   - Merge with static rules from `federation_static.json`
   - Sort rules deterministically
   - Update mapping if changed

Even when `--project` filter is used, our federation mapping considers **all projects**, not just the filtered one.

### Rationale

**Shared resource nature**: Federation mapping is a single, global, shared resource. We can't provision "per-project" mappings.

**Complete picture**: The mapping must include rules for all projects, otherwise filtering to one project would delete other projects' rules.

**Idempotency**: Deterministic ordering ensures the mapping is stable across runs.

### Alternatives Considered

**Alternative 1: Per-project mappings**
- Create separate mapping for each project (e.g., `{project-name}-mapping`)
- **Rejected**: Not how OpenStack federation works; one mapping per identity provider

**Alternative 2: Skip federation when using --project filter**
- Don't update federation mapping when provisioning a single project
- **Rejected**: Would require manual federation management; not fully declarative

**Alternative 3: Append-only federation rules**
- Never remove rules, only add new ones
- **Rejected**: Deleted projects' rules would linger forever

### Consequences

**Positive**:
- Fully declarative: federation config lives in project files
- Consistent with shared resource nature
- Safe with `--project` filter (doesn't break other projects)

**Negative**:
- Even single-project runs (`--project`) must load all project configs for federation
- Slightly confusing: "I only provisioned one project but federation updated for all"

**Mitigations**:
- We document federation as a shared resource in USER-GUIDE.md
- We log clearly: "Reconciling federation mapping (shared resource)"

---

## DD-009: Config Writeback for Idempotency

**Status**: Superseded by DD-018

> **Status**: Superseded by DD-018 (Separate State File for Observed State)
>
> This design decision documents the original `locked_fips` approach where FIP IDs were written back to project YAML files. DD-018 replaced this with `preallocated_fips` stored in separate `config/state/<project>.state.yaml` files.

### Context

Pre-allocated resources (floating IPs) are allocated by us, then quota is set to the desired count. On subsequent runs, we need to know which resources were already allocated to avoid re-allocating.

We needed to decide how to track allocated pre-allocated resources.

### Decision

**Write allocated resource IDs back to the project YAML file**:

1. After allocating a floating IP, we write its ID and address to the project YAML:
   ```yaml
   locked_fips:
     - id: e7b5c8d4-...
       address: 203.0.113.42
   ```

2. On subsequent runs, we check for `locked_fips` in the config
3. If IDs exist, we skip allocation and just ensure quota is set to desired count

### Rationale

**Source of truth**: The config file becomes our authoritative record of what was allocated.

**Idempotency**: Re-running the provisioner won't re-allocate (we see existing entries in config).

**Auditability**: Easy to see which floating IPs belong to which project (just read the config file).

**Simplicity**: No external database or state file needed.

### Alternatives Considered

**Alternative 1: Query OpenStack API on every run**
- List floating IPs for project, check if count >= desired
- **Rejected**: Slower (API call every run); doesn't distinguish pre-existing FIPs from ones we pre-allocated

**Alternative 2: Separate state file**
- Maintain a separate `state.json` with allocated resource IDs
- **Originally rejected**: Extra file to manage, could get out of sync with config
- **Later adopted as DD-018**: The sync concern was resolved by loading state into the in-memory config dict during Phase 1, and the benefits (clean git diffs, swappable backend) outweighed the extra file cost

**Alternative 3: OpenStack resource tags**
- Tag pre-allocated FIPs with `provisioner-managed=true`
- **Rejected**: Relies on tagging support; tags could be removed

**Alternative 4: Don't track, always re-check quota**
- On every run, check current quota and FIP count
- **Rejected**: Can't distinguish "we pre-allocated this" from "a user manually allocated this"

### Consequences

**Positive**:
- Single source of truth (config file)
- Fast checks (read from file, not API)
- Clear audit trail
- Idempotent without external state

**Negative**:
- Config file is mutable (we write to it)
- Manual edits to `locked_fips` could cause confusion
- Git diffs show changes to `locked_fips` section

**Mitigations**:
- We clearly document: `locked_fips` is managed by the provisioner, don't edit manually
- Atomic writes (YAML dump is transactional)
- Git commits clearly show when FIPs were allocated

---

## DD-010: Deterministic Federation Rule Ordering

**Status**: Accepted

### Context

Our federation mapping contains a list of rules. OpenStack evaluates rules in order. We build rules from multiple project configs. Without deterministic ordering:
- The same config could produce different rule orders on different runs
- The mapping would appear "changed" even when rules haven't changed (just reordered)
- This breaks idempotency

### Decision

We **sort federation rules deterministically** before updating the mapping:

1. Build rules from all project configs
2. Merge with static rules
3. **Sort rules** by a stable key (group path, then role names)
4. Compare sorted rules with current mapping
5. Update mapping only if rules differ

```python
# Sort rules by group path, then by role names
rules.sort(key=lambda r: (r["group_path"], tuple(sorted(r["roles"]))))
```

### Rationale

**Idempotency**: Same input config always produces the same rule order.

**Minimal updates**: The mapping only gets updated when rules actually change (not just reordered).

**Predictability**: We can reason about rule order.

### Alternatives Considered

**Alternative 1: Insertion order from config files**
- Use order of project files in filesystem
- **Rejected**: Filesystem order isn't guaranteed stable (depends on OS, filesystem)

**Alternative 2: Don't sort, always update**
- Accept that rule order may vary, update mapping every run
- **Rejected**: Unnecessary updates, harder to detect real changes

**Alternative 3: Hash-based comparison**
- Hash the rule set, only update if hash differs
- **Rejected**: We still need deterministic ordering for the hash to be stable

### Consequences

**Positive**:
- Idempotent: same config → same rule order → no unnecessary updates
- Easy to verify: sorted rules are predictable
- Minimal mapping updates

**Negative**:
- Rule order may not match our intuition (sorted, not config file order)
- Sorting adds tiny overhead (negligible for typical rule counts)

**Mitigations**:
- We document that rules are sorted for idempotency
- Sorting is fast (typically <100 rules)

---

## DD-011: Router IP Capture-and-Track Pattern

**Status**: Accepted

### Context

When routers are created with external gateways in OpenStack, Neutron automatically allocates an IP address from the external network pool and assigns it to the router's external gateway port. This IP is used for SNAT (Source NAT) to enable instances without floating IPs to access external networks.

**Our problem**: These router IPs are important for operational visibility:
- Firewall rules may reference the router IP for ingress traffic
- External services may whitelist the router IP
- Monitoring systems track egress traffic by router IP
- Compliance requires knowing which IPs are associated with the project

However:
- Router IPs can change if the router is recreated or the gateway is modified
- There's no built-in OpenStack mechanism to track router IP history
- Manually inspecting each router's gateway info is tedious

We need visibility into router external IPs and detection of IP changes.

### Decision

We implement a **capture-and-track pattern** for router external IPs:

1. **Scan all routers** in the project (not just ones we created)
2. **Extract external gateway IPs** from `router.external_gateway_info["external_fixed_ips"]`
3. **Persist current snapshot** to `router_ips` in project config YAML
4. **Compare with previous snapshot** on each run
5. **Build audit trail** in `released_router_ips` for lost or changed IPs

**Our implementation** (`track_router_ips()` in `src/resources/network.py`):

```python
def track_router_ips(cfg, project_id, ctx) -> list[Action]:
    # 1. List all routers in the project
    routers = conn.network.routers(project_id=project_id)

    # 2. Build current snapshot
    current = [
        {"id": r.id, "name": r.name, "external_ip": extract_ip(r)}
        for r in routers if has_gateway(r)
    ]

    # 3. Load previous snapshot
    previous = cfg.get("router_ips", [])

    # 4. Detect changes (adopt, release, IP drift)
    actions = []
    new_releases = []

    for router in current:
        if router["id"] not in prev_ids:
            actions.append(ADOPTED)  # New router tracked

    for router in previous:
        if router["id"] not in curr_ids:
            new_releases.append({...})  # Router deleted
            actions.append(RELEASED)
        elif ip_changed(router):
            new_releases.append({...})  # IP drifted
            actions.append(IP_CHANGED)

    # 5. Persist snapshots
    if current != previous:
        write_config(["router_ips"], current)
    if new_releases:
        append_config(["released_router_ips"], new_releases)

    return actions
```

**Config schema** (auto-populated):

```yaml
# Current snapshot (overwritten on each run)
router_ips:
  - id: "router-uuid"
    name: "myproj-router"
    external_ip: "203.0.113.42"

# Audit trail (append-only, never cleared)
released_router_ips:
  - address: "203.0.113.10"
    router_name: "old-router"
    released_at: "2026-04-01T10:30:00+00:00"
    reason: "router no longer exists"
  - address: "203.0.113.42"
    router_name: "myproj-router"
    released_at: "2026-04-02T14:15:00+00:00"
    reason: "IP changed: 203.0.113.42 -> 203.0.113.99"
```

### Rationale

**Capture vs. Allocate**: Unlike floating IPs (where we explicitly allocate), router IPs are allocated implicitly by OpenStack when the external gateway is attached. We can't pre-allocate them or control which IP is assigned. So we **capture** the assigned IP rather than **allocate** a specific one.

**Track all routers**: We decided to track ALL routers in the project (not just ones we created) to provide complete visibility. If users manually create additional routers, we track them too. This prevents "blind spots" in our IP tracking.

**Audit trail benefits**:
- **Compliance**: Permanent record of which IPs were used by the project
- **Drift detection**: Easy to see when and why IPs changed
- **Troubleshooting**: "Why is traffic coming from a new IP?" → Check audit trail
- **Cost allocation**: Historical record for billing/chargeback

**No quota locking**: Router IPs are tied to the router lifecycle. When a router is deleted, its IP returns to the pool. There's no separate quota for "router external IPs" to lock. The router quota itself controls how many routers (and thus IPs) can exist.

### Alternatives Considered

**Alternative 1: Don't track router IPs**
- Rely on operators to manually inspect routers when needed
- **Rejected**: Loses operational visibility; no drift detection; no audit trail

**Alternative 2: Track only our provisioner-created router**
- Only track the single router created by `ensure_network_stack()`
- **Rejected**: Incomplete visibility if users create additional routers; inconsistent with our FIP pattern (which tracks all allocated FIPs)

**Alternative 3: Store router IP nested under `network.router`**
- Similar to the original proposal: `network: {router: {id, external_ip}}`
- **Rejected**: Assumes single router per project; doesn't scale to multiple routers; inconsistent with our `preallocated_fips` (formerly `locked_fips`, see DD-018) top-level list structure

**Alternative 4: Integrate into `ensure_network_stack()`**
- Capture router IP immediately after router creation in `ensure_network_stack()`
- **Rejected**: Doesn't track routers created outside our provisioner; doesn't detect drift on subsequent runs

**Alternative 5: No audit trail, just current snapshot**
- Track only `router_ips`, omit `released_router_ips`
- **Rejected**: Loses historical visibility; can't answer "what IP did we use last month?"

### Consequences

**Positive**:
- **Operational visibility**: We know all router IPs without manual inspection
- **Drift detection**: We're alerted when IPs change unexpectedly
- **Audit trail**: Permanent record of IP history for compliance/troubleshooting
- **Idempotent**: Safe to run multiple times; no unnecessary updates
- **Comprehensive**: Tracks all project routers, not just ones we created
- **Self-documenting**: Config file shows current and historical IPs

**Negative**:
- **Broader scope than proposed**: Tracks all routers (a deliberate choice for completeness)
- **Manual edits discouraged**: We shouldn't edit `router_ips` or `released_router_ips` by hand
- ~~**Config mutation / Git churn**~~: Resolved by DD-018 — state now lives in a separate state file, not in the project config YAML

**Mitigations**:
- State files are separate from config (DD-018), so no config mutation or git churn
- We document clearly: `router_ips` and `released_router_ips` are system-managed in the state file
- Atomic writes ensure consistency
- Git history provides an additional audit trail of when tracking started/changed

### Comparison with Floating IP Pattern

| Aspect | Router IPs (DD-011) | Floating IPs (DD-002) |
|--------|---------------------|----------------------|
| **Allocation** | Automatic (by OpenStack) | Manual (by us) |
| **Control** | None (tied to router lifecycle) | Full (allocate/release) |
| **Pattern** | Capture-and-track | Allocate-then-lock |
| **Scope** | All project routers | Only what we pre-allocated |
| **Quota** | No separate quota lock | Quota set to desired count |
| **State storage** | `router_ips` in state file | `preallocated_fips` in state file |
| **Audit trail** | `released_router_ips` | `released_fips` |
| **Idempotency** | Snapshot comparison | ID tracking + quota check |

**Key difference**: Floating IPs are managed resources (we allocate/release them), while router IPs are observed resources (we track what OpenStack assigns).

### Implementation Notes

**Edge case not yet handled**: Our code doesn't currently log a warning when a router has multiple external IPs (`len(external_fixed_ips) > 1`). The original proposal identified this edge case and suggested logging. **Improvement opportunity**: Add logging in `_get_router_external_ip()` when multiple IPs are detected.

**When to call**: Typically invoked in our main reconciliation flow after `ensure_network_stack()`, but can be called independently. Not currently integrated into the standard provisioning workflow — may require orchestration updates.

### See Also

- **DD-009 (Config Writeback for Idempotency)**: Original storage mechanism; superseded by DD-018
- **DD-018 (Separate State File for Observed State)**: Router IP state now persisted to state file instead of project YAML
- **DD-014 (Drift Detection & Reconciliation Pattern)**: Similar pattern (snapshot, compare, track releases) applied to floating IPs

---

## DD-012: Project Lifecycle State Machine

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/reconciler.py`, `src/config_loader.py`

### Context

Our projects need different operational modes beyond simple "provision" or "delete":

**Operational scenarios**:
- **Active development**: Full provisioning with all resources enabled
- **Temporary suspension**: Disable project access without destroying resources, reduce compute costs
- **Complete removal**: Clean teardown when a project is no longer needed

**Our requirements**:
- Clear lifecycle states with well-defined behavior
- Safe transitions between states
- Prevent accidental data loss during state changes
- Support cost optimization (suspend compute without losing data)

### Decision

We implement a **three-state lifecycle model** with dedicated reconciliation handlers:

**State 1: `present` (default)**
- Full provisioning pipeline (project, network, quotas, security groups, locked resources)
- Enable the project (`enabled=True`)
- Unshelve any previously shelved servers (handles `locked` → `present` transition)
- This is the normal active state for development/production projects

**State 2: `locked`**
- Disable project access (`enabled=False`)
- Shelve all ACTIVE servers to reduce compute costs
- Skip network/quota/security group provisioning (frozen state)
- Keep group role assignments intact (identity metadata preserved)

**State 3: `absent`**
- Safety-checked teardown in reverse dependency order
- Refuse deletion if VMs or volumes exist (prevent data loss)
- Revoke all group role assignments before resource deletion
- Delete resources: FIPs → snapshots → router interfaces → routers → subnets → networks → security groups → project

**Our state dispatch mechanism** (`src/reconciler.py` lines 129-162):
```python
_STATE_HANDLERS: dict[str, StateHandler] = {
    "present": _reconcile_present,
    "locked": _reconcile_locked,
    "absent": _reconcile_absent,
}

for cfg in projects:
    state: str = cfg.get("state", "present")
    handler = _STATE_HANDLERS.get(state)
    if handler is None:
        # Log error, continue with next project
    try:
        handler(cfg, ctx)
    except Exception:
        # Per-project error isolation
```

**State validation** (`src/config_loader.py` line 25):
```python
_VALID_STATES: set[str] = {"present", "locked", "absent"}
```

### Rationale

**Clear operational model**: Each state has well-defined semantics. We know exactly what happens in each state.

**Cost optimization**: The `locked` state lets us temporarily suspend projects without losing network/quota configuration. Shelved VMs don't consume compute quota but preserve disk images.

**Safety by design**: The `absent` state includes safety checks (refuse if VMs/volumes exist) to prevent accidental data loss.

**Reversible transitions**: `present` ↔ `locked` transitions are non-destructive. Unshelving automatically happens when we transition back to `present`.

**Complements DD-001**: State dispatch happens in Phase 3 (Reconcile), after validation and connection setup.

### Alternatives Considered

**Alternative 1: Separate CLI commands**
- Provide `provision`, `disable`, and `teardown` subcommands instead of a state field
- **Rejected**: Less declarative; state not visible in config file; requires different commands for the same project

**Alternative 2: Boolean flags**
- Use `enabled: false` and `delete: true` flags instead of a state enum
- **Rejected**: Ambiguous combinations (what if both false?); harder to validate; no clear lifecycle model

**Alternative 3: External workflow orchestration**
- Keep the provisioner simple (only provision/delete), use an external tool for state management
- **Rejected**: Splits lifecycle logic across tools; harder to reason about; loses declarative benefits

**Alternative 4: More granular states**
- Add states like `suspended`, `archived`, `maintenance`, etc.
- **Rejected**: Over-engineering; three states cover all our real operational needs

### Consequences

**Positive**:
- **Clear lifecycle**: Three well-defined states, easy to understand and use
- **Safe transitions**: Built-in safety checks prevent accidents
- **Cost optimization**: `locked` state reduces compute costs without data loss
- **Declarative**: Project state visible in config file, not hidden in external tools
- **Auditable**: State transitions tracked in Git history

**Negative**:
- **Reconciler complexity**: Adds state dispatch logic and three separate handler functions
- **Learning curve**: We need to understand state semantics and valid transitions
- **Not fully automated**: We must manually change state in config (no auto-suspend on idle)

**Mitigations**:
- Clear documentation in USER-GUIDE.md with state transition examples
- State validation catches typos/invalid states early (Phase 1)
- Error messages clearly explain which state failed and why
- State handlers share common patterns (reuse our resource modules)

### See Also

- **DD-001 (Three-Phase Execution Model)**: State dispatch happens in Phase 3; validation happens in Phase 1
- **DD-013 (Teardown & Reverse Dependency Order)**: Triggered by `absent` state; implements safe deletion
- **DD-007 (Error Isolation Per Project)**: State handler exceptions caught and isolated per project

---

## DD-013: Teardown & Reverse Dependency Order

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/resources/teardown.py`

### Context

When a project transitions to `state: absent`, we must delete all resources in dependency-safe order to avoid errors like:

- **Cannot delete network**: Subnets still attached
- **Cannot delete router**: Interfaces still connected
- **Cannot delete subnet**: Ports still exist
- **Cannot delete project**: Resources still exist

We need a deletion strategy that:
- Respects OpenStack resource dependencies
- Handles partial failures gracefully
- Prevents accidental data loss
- Gives us clear visibility into what was deleted

### Decision

We implement **reverse-order teardown** that mirrors the inverse of DD-001's provisioning order:

**Our 7-step deletion sequence** (`src/resources/teardown.py`):

1. **Floating IPs** (lines 168-192)
   - Delete all FIPs in the project
   - Reason: FIPs can be attached to ports; must be freed first

2. **Snapshots** (lines 194-214)
   - Delete all volume snapshots
   - Reason: Snapshots reference volumes; delete before attempting volume cleanup

3. **Router interfaces + Routers** (lines 216-253)
   - Detach all subnet interfaces from routers (`network:router_interface` ports)
   - Clear external gateway
   - Delete routers
   - Reason: Routers connect subnets; must be removed before subnet deletion

4. **Subnets** (lines 255-275)
   - Delete all subnets in the project
   - Reason: Subnets depend on networks; must be deleted before network

5. **Networks** (lines 277-297)
   - Delete all networks in the project
   - Reason: Networks are parent resources; delete after subnets

6. **Security groups** (lines 299-325)
   - Delete all non-default security groups
   - Skip the `default` security group (auto-managed by OpenStack)
   - Reason: SGs are independent; can be deleted after network resources

7. **Project** (lines 327-346)
   - Delete the project itself
   - Reason: Parent container; must be last

**Our safety checks before teardown** (`safety_check()` function, lines 22-66):
```python
def safety_check(conn, project_id, project_name) -> list[str]:
    """Return list of reasons the project cannot be safely torn down.

    Empty list = all checks passed and found no blocking resources.
    Each check is wrapped individually so one failure doesn't skip the rest.
    """
    errors = []

    # --- Servers (Nova) ---
    try:
        servers = list_project_servers(conn, project_id)
        if servers:
            errors.append(f"project has {len(servers)} server(s): {names}")
    except EndpointNotFound:
        pass  # Service absent → no resources possible
    except Exception:
        errors.append("server check inconclusive (API error)")

    # --- Volumes (Cinder) ---
    try:
        volumes = _list_volumes(conn, project_id)
        if volumes:
            errors.append(f"project has {len(volumes)} volume(s): {names}")
    except EndpointNotFound:
        pass  # Service absent → no resources possible
    except Exception:
        errors.append("volume check inconclusive (API error)")

    return errors  # Empty list = safe to delete
```

If the safety check fails, we **refuse teardown** with a clear error message (reconciler.py lines 111-114).

Our safety check uses **fail-safe** semantics:
- **`EndpointNotFound`** → service absent, skip check (no resources possible without the service).
- **Any other exception** → "inconclusive" error added to the list, which blocks deletion.
- **Empty return `[]`** now guarantees "all checks passed *and* found nothing" — not "we couldn't check."

This ensures we never delete a project based on incomplete information. If Nova or Cinder is temporarily unreachable, the check produces an inconclusive error and the reconciler's per-project `try/except` catches the resulting `RuntimeError`, adds the project to `failed_projects`, and continues with the next project.

**Per-resource error isolation** (lines 165-353):
```python
# For each resource type:
for resource in resources:
    try:
        _delete_resource(conn, resource.id)
        actions.append(DELETED)
    except NotFoundException:
        actions.append(DELETED, "already gone")  # Idempotent
    except Exception:
        logger.error("Failed to delete %s", resource, exc_info=True)
        actions.append(FAILED)
        failures.append(resource_label)
        continue  # Don't abort; try to delete remaining resources
```

After all resources are processed, if any failures occurred, we raise `RuntimeError` with a summary.

### Rationale

**Reverse provisioning order**: DD-001 provisions in order (project → network → router → ...). Our teardown reverses this to respect dependencies.

**Safety first**: Refusing to delete projects with VMs/volumes prevents accidental data loss. We require operators to explicitly delete/backup VMs before project deletion.

**Fail-safe safety checks**: API errors during safety checks produce "inconclusive" results that block deletion, ensuring we never delete based on incomplete information. `EndpointNotFound` is the sole exception: absent services can't have resources, so skipping them is safe.

**Graceful degradation**: Per-resource error isolation ensures maximum cleanup even if some deletions fail. We can see which resources succeeded/failed in one run.

**Idempotent**: We treat `NotFoundException` as success ("already gone"), allowing safe re-runs after partial failures.

**Clear visibility**: Each resource deletion produces an Action (DELETED/FAILED), giving us a complete audit trail.

### Alternatives Considered

**Alternative 1: Fail-fast on first error**
- Stop entire teardown if any resource deletion fails
- **Rejected**: Leaves the project in partially-deleted limbo; we'd have to manually clean up each failure before retry

**Alternative 2: Automatic VM/volume deletion**
- Delete VMs and volumes automatically during teardown
- **Rejected**: Too dangerous; risk of accidental data loss; we might forget to backup

**Alternative 3: Parallel deletion**
- Delete independent resources (FIPs, snapshots, SGs) in parallel for speed
- **Rejected**: Complexity not justified; sequential deletion is fast enough and easier to debug

**Alternative 4: Recursive cascade deletion**
- Let OpenStack handle cascading deletes (e.g., delete network → auto-delete subnets)
- **Rejected**: Not all OpenStack deployments support cascade; explicit deletion is more reliable

**Alternative 5: No safety checks**
- Attempt deletion and let OpenStack API errors guide retry
- **Rejected**: Cryptic API errors; harder to debug; we prefer upfront validation

### Consequences

**Positive**:
- **Safe deletion**: Safety checks prevent accidental data loss
- **Maximum cleanup**: Per-resource isolation ensures partial success even with failures
- **Clear visibility**: Our action log shows exactly what was deleted/failed
- **Idempotent**: Safe to re-run after partial failures
- **Predictable**: Reverse order mirrors provisioning, easy to understand
- **Fail-safe checks**: API failures during safety checks block teardown rather than allowing deletion based on incomplete data

**Negative**:
- **Manual VM deletion required**: We must delete VMs/volumes before project teardown
- **Sequential processing**: Slower than parallel deletion (not a problem in practice)
- **Partial state on failure**: Some resources deleted, others remain (but this is visible in our action log)

**Mitigations**:
- Clear error messages explain what resources block deletion
- Our action log shows exactly which resources succeeded/failed
- NotFoundException handling makes retry safe
- We document the teardown procedure in USER-GUIDE.md

### See Also

- **DD-001 (Three-Phase Execution Model)**: Teardown is the inverse of our provisioning order
- **DD-012 (Project Lifecycle State Machine)**: Teardown triggered by `state: absent`
- **DD-007 (Error Isolation Per Project)**: Same pattern (try-catch, continue on error)
- **DD-004 (Universal Resource Pattern)**: Each resource follows find/delete pattern

---

## DD-014: Drift Detection & Reconciliation Pattern

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/resources/prealloc/fip.py`

### Context

Pre-allocated resources (floating IPs tracked via DD-009 config writeback) can drift from the recorded state:

**Drift scenarios we handle**:
- **Manual deletion**: Someone deletes a FIP through OpenStack CLI/Horizon
- **Manual creation**: Someone creates additional FIPs not tracked in our config
- **External automation**: Other tools allocate/release FIPs in the project
- **Network failures**: FIP deleted during network reconfiguration

When drift occurs:
- Our config file shows 3 FIPs but OpenStack shows 2 (deletion drift)
- Our config file shows 2 FIPs but OpenStack shows 4 (creation drift)
- A FIP ID in our config no longer exists, but the address was reallocated to a different project (address stolen)

We need to detect drift and reconcile automatically without manual intervention.

> **Status**: Superseded by DD-018 (Separate State File for Observed State)
>
> The `locked_fips` references in this decision are now `preallocated_fips` stored in separate state files (`config/state/`), not in project YAML files.

### Decision

We implement a **4-step drift detection and reconciliation pattern** (`src/resources/prealloc/fip.py` lines 109-264):

**Step 1: Detect Drift** (`_detect_fip_drift()`, lines 109-125)
```python
def _detect_fip_drift(
    config_fips: list[dict[str, str]],  # From locked_fips in YAML
    openstack_fips: list[Any],          # From OpenStack API
) -> tuple[list[dict], list[Any]]:
    """Compare config vs OpenStack to find missing/untracked FIPs."""
    config_ids = {f["id"] for f in config_fips}
    openstack_ids = {f.id for f in openstack_fips}

    missing = [f for f in config_fips if f["id"] not in openstack_ids]
    untracked = [f for f in openstack_fips if f.id not in config_ids]

    return missing, untracked
```

**Key insight**: We compare by ID (not address), because addresses can be reused.

**Step 2: Adopt Untracked** (lines 175-191)
- FIPs exist in OpenStack but not in our config → add to config
- Scenario: Someone manually created FIPs; we adopt them
- Action: Append to `locked_fips` section, persist to YAML
- Result: Our config now matches reality

```python
for fip in untracked:
    adopted.append({"id": fip.id, "address": fip.floating_ip_address})
    actions.append(UPDATED, "adopted untracked FIP")

if adopted:
    updated_config_fips = config_fips + adopted
    _persist_fips_to_config(cfg, updated_config_fips)
```

**Step 3: Reclaim Missing** (lines 206-250)
- FIPs in our config but deleted from OpenStack → try to re-allocate the same address
- Scenario: Someone accidentally deleted a FIP; we try to recover it
- Action: Call `create_ip(floating_ip_address=address)` to reclaim the specific address
- Success: Reclaimed with new ID, update config
- Failure (ConflictException): Address taken by another project → move to `released_fips`

```python
for entry in missing:
    address = entry["address"]
    try:
        fip = _reclaim_floating_ip(ctx.conn, ctx.external_net_id, project_id, address)
        reclaimed.append({"id": fip.id, "address": fip.floating_ip_address})
        actions.append(UPDATED, "reclaimed with new id")
    except ConflictException:
        # Address taken by another project
        newly_released.append({
            "address": address,
            "released_at": datetime.now(UTC).isoformat(),
            "reason": "address taken by another project"
        })
        actions.append(FAILED, "moved to released_fips")
```

**Step 4: Track Released** (lines 258-262)
- FIPs that we couldn't reclaim → append to our audit trail
- Persist to `released_fips` section for compliance/troubleshooting
- Result: Permanent record of lost resources

```python
if newly_released:
    existing_released = cfg.get("released_fips", [])
    all_released = existing_released + newly_released
    _persist_released_fips(cfg, all_released)
```

**When drift detection runs** (lines 403-412):
- Always before scale-up/scale-down/quota-set operations
- Only if `locked_fips` already exists in our config (we have a baseline)
- We re-list FIPs after drift reconciliation to get fresh state

### Rationale

**Self-healing**: We automatically recover from manual deletions without operator intervention.

**Complete visibility**: We adopt untracked resources (don't ignore them), ensuring our config matches reality.

**Audit trail**: `released_fips` gives us a permanent record for compliance, troubleshooting, and cost allocation.

**ID-based comparison**: Comparing by ID (not address) handles address reuse correctly. If address X was deleted then reallocated to a different project, we detect this via ID mismatch.

**Graceful failure**: When we can't reclaim an address (it's taken), we record it rather than failing the entire provisioning run.

**Builds on DD-009**: Config writeback provides the baseline to compare against. Without writeback, drift detection would be impossible.

### Alternatives Considered

**Alternative 1: No drift detection**
- Rely on operators to manually fix config when drift occurs
- **Rejected**: Manual toil; error-prone; defeats our declarative model

**Alternative 2: Drift detection without reconciliation**
- Detect drift but only log warnings, don't auto-fix
- **Rejected**: Alerts the operator but doesn't solve the problem; partial value

**Alternative 3: Always delete and recreate on drift**
- Don't try to reclaim; just delete untracked and create new
- **Rejected**: Loses IP addresses (may be whitelisted in firewalls); unnecessary churn

**Alternative 4: Reclaim without adopt**
- Reclaim missing but ignore untracked
- **Rejected**: Our config would be incomplete; loses visibility into actual state

**Alternative 5: Compare by address instead of ID**
- Match FIPs by address, not ID
- **Rejected**: Addresses can be reused; would cause false matches after address recycling

### Consequences

**Positive**:
- **Self-healing**: Automatic recovery from manual deletions
- **Complete visibility**: Our config always reflects actual OpenStack state
- **Audit trail**: `released_fips` tracks resource history
- **Address preservation**: Reclaim attempts to keep the same IPs (important for firewall rules)
- **Graceful degradation**: Can't reclaim → record in audit trail, continue

**Negative**:
- **Complexity**: 4-step pattern is more complex than simple allocation
- **Potential confusion**: Users may not expect us to automatically adopt manually-created FIPs
- **API calls**: Extra API calls on every run (list FIPs, compare, potentially reclaim)

**Mitigations**:
- Clear logging: "Adopted untracked FIP", "Reclaimed FIP with new ID", "Cannot reclaim (address taken)"
- We document drift behavior in USER-GUIDE.md
- Audit trail in `released_fips` explains what happened and when
- Only runs if we have a baseline (not on first run)

### See Also

- **DD-009 (Config Writeback for Idempotency)**: Provides our baseline (`preallocated_fips`) to compare against
- **DD-018 (Separate State File for Observed State)**: Drift persistence now writes to state file instead of project YAML
- **DD-011 (Router IP Capture-and-Track Pattern)**: Similar pattern (snapshot, compare, track releases)
- **DD-005 (Retry with Exponential Backoff)**: Reclaim uses our `@retry()` decorator for transient failures
- **DD-004 (Universal Resource Pattern)**: Drift reconciliation extends our find/create/update pattern

---

## DD-015: Graceful Service Degradation

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/resources/quotas.py`

### Context

OpenStack deployments vary widely in which optional services are available:

**Service availability patterns**:
- **Full deployment**: Compute, Network, Block Storage (Cinder), Load Balancer (Octavia) all available
- **Minimal deployment**: Only Compute and Network available (common in dev/test clouds)
- **Partial deployment**: Some services enabled, others disabled based on requirements

**Our problem**:
- We configure quotas for `block_storage` (Cinder) and `load_balancer` (Octavia)
- If these services aren't deployed, our API calls fail with `EndpointNotFound`
- Should the entire provisioning fail? Or should we gracefully skip unavailable services?

**Our requirements**:
- Work across different OpenStack deployment types
- Don't fail entire provisioning if an optional service is missing
- Provide clear visibility when services are skipped
- Allow configuring quotas for services that might be enabled later

### Decision

We implement a **graceful degradation pattern** using try-catch with `EndpointNotFound`:

**Pattern 1: Load Balancer (Octavia) quotas** (`src/resources/quotas.py` lines 117-144):
```python
# Handle Load Balancer (Octavia) quotas with graceful degradation
if lb_quotas:
    try:
        current_lb = ctx.conn.load_balancer.get_quota(project_id)
        # ... update if needed ...
        ctx.conn.load_balancer.update_quota(project_id, **lb_quotas)
        logger.info("Updated load_balancer quotas for %s", project_label)
        lb_updated = True
    except openstack.exceptions.EndpointNotFound:
        logger.warning(
            "Skipping load_balancer quotas for %s (Octavia service not available)",
            project_label,
        )
    except Exception:
        logger.warning(
            "Skipping load_balancer quotas for %s (unexpected error)",
            project_label,
            exc_info=True,
        )
```

**Pattern 2: Block Storage (Cinder) quotas** (`src/resources/quotas.py` lines 234-249):
```python
try:
    actions.append(_ensure_block_storage_quotas(cfg, project_id, ctx))
except Exception:
    logger.warning(
        "Block-storage quotas skipped for %s — service may not be available",
        project_id,
    )
    actions.append(
        ctx.record(
            ActionStatus.SKIPPED,
            "block_storage_quota",
            "",
            "service not available",
        )
    )
```

**What we do**:
1. **Try** to access the service endpoint
2. **Catch** `EndpointNotFound` (or generic `Exception` for broader coverage)
3. **Log a warning** with clear message (service name, project, reason)
4. **Record a SKIPPED action** for visibility
5. **Continue** with remaining provisioning (don't abort)

**No retry**: Unlike DD-005 (retry transient errors), `EndpointNotFound` is permanent. The service either exists or it doesn't. Retrying wastes time.

### Rationale

**Deployment flexibility**: Our provisioner works on minimal OpenStack deployments (Compute + Network only) and full deployments (all services) without config changes.

**Fail gracefully, not catastrophically**: A missing optional service shouldn't block us from provisioning critical resources (project, network, compute quotas).

**Clear visibility**: Warning logs and SKIPPED actions clearly show which services were unavailable.

**Future-proof configs**: We can configure Octavia quotas in `defaults.yaml` even if Octavia isn't deployed yet. When the service is added later, quotas take effect automatically.

**Complements DD-005**: DD-005 handles transient failures (network blips, API overload). DD-015 handles permanent service unavailability. Different failure modes, different strategies.

### Alternatives Considered

**Alternative 1: Require all services**
- Fail fast if any service endpoint is missing
- **Rejected**: Too brittle; prevents use on minimal OpenStack deployments

**Alternative 2: Config-based service enable flags**
- Add `services.octavia.enabled: false` to config to skip unavailable services
- **Rejected**: Extra config burden; we'd have to know the deployment topology; not truly automatic

**Alternative 3: Pre-flight service discovery**
- Query the service catalog in Phase 2, skip services not listed
- **Rejected**: More complex; the service catalog can lie (endpoint exists but service is broken)

**Alternative 4: Silent failure**
- Catch `EndpointNotFound` but don't log or record action
- **Rejected**: Loses visibility; we wouldn't know why quotas weren't set

**Alternative 5: Retry EndpointNotFound**
- Apply DD-005 retry pattern to EndpointNotFound exceptions
- **Rejected**: Service availability is permanent, not transient; retrying wastes time

### Consequences

**Positive**:
- **Works everywhere**: Our provisioner is compatible with minimal and full OpenStack deployments
- **No config changes needed**: The same config works across different cloud environments
- **Clear feedback**: Warnings clearly tell us which services were skipped
- **Future-proof**: We can configure quotas before services are deployed
- **Graceful**: Optional service failures don't block critical provisioning

**Negative**:
- **Silent quota failures**: If a service exists but has bugs, quotas may not be set without us noticing
- **Broader exception handling**: Generic `except Exception` catches more than just EndpointNotFound
- **Incomplete provisioning**: We might not realize service quotas weren't set

**Mitigations**:
- Warning logs clearly state which service was skipped
- SKIPPED actions appear in our summary output
- We document expected services in the deployment guide
- We could add a `--strict` mode in the future (fail on any missing service)

### See Also

- **DD-005 (Retry with Exponential Backoff)**: Handles transient failures; DD-015 handles permanent unavailability
- **DD-004 (Universal Resource Pattern)**: Graceful degradation extends the pattern with a service availability check
- **DD-007 (Error Isolation Per Project)**: Same principle (isolate failures, continue processing)

---

## DD-016: Security Group Rule Preset Expansion

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/config_loader.py`

### Context

Security group rules in OpenStack require verbose configuration:

**Verbose rule format** (full specification):
```yaml
security_group:
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 22
      port_range_max: 22
      remote_ip_prefix: 0.0.0.0/0
      description: Allow SSH
```

**Our problem**:
- Common rules (SSH, HTTP, HTTPS) require 5-7 fields each
- Duplicating verbose rules across projects is tedious and error-prone
- Project configs become bloated with repetitive rule definitions
- Hard to scan a config and quickly see "which ports are open?"

**Our requirements**:
- Reduce config verbosity for common rules
- Support custom overrides (e.g., "SSH from 10.0.0.0/8 only")
- Maintain backward compatibility with full rule syntax
- Expand presets early (Phase 1) so validation sees full rules

### Decision

We implement **preset expansion** in our config loader with three usage formats:

**Format 1: Simple preset** (string shorthand):
```yaml
security_group:
  rules:
    - "SSH"      # Expands to full SSH rule
    - "HTTP"     # Expands to full HTTP rule
    - "HTTPS"    # Expands to full HTTPS rule
```

**Format 2: Preset with overrides** (dict with `rule` key):
```yaml
security_group:
  rules:
    - rule: "SSH"
      remote_ip_prefix: "10.0.0.0/8"  # Override default 0.0.0.0/0
      description: "SSH from internal network"
```

**Format 3: Custom rule** (dict without `rule` key, backward compatible):
```yaml
security_group:
  rules:
    - direction: ingress
      protocol: tcp
      port_range_min: 8080
      port_range_max: 8080
      remote_ip_prefix: 0.0.0.0/0
      description: Custom web app port
```

**Our available presets** (`src/config_loader.py` lines 38-107):
1. **SSH**: TCP port 22, ingress, 0.0.0.0/0
2. **HTTP**: TCP port 80, ingress, 0.0.0.0/0
3. **HTTPS**: TCP port 443, ingress, 0.0.0.0/0
4. **ICMP** / **All ICMP**: ICMP protocol, ingress, 0.0.0.0/0
5. **All TCP**: TCP ports 1-65535, ingress, 0.0.0.0/0
6. **All UDP**: UDP ports 1-65535, ingress, 0.0.0.0/0
7. **DNS**: UDP port 53, ingress, 0.0.0.0/0
8. **RDP**: TCP port 3389, ingress, 0.0.0.0/0

**Our expansion logic** (`_expand_security_group_rules()` lines 122-174):
```python
def _expand_security_group_rules(project: dict[str, Any], errors: list[str]):
    """Expand preset names to full rule dicts."""
    rules = project.get("security_group", {}).get("rules", [])
    expanded = []

    for idx, entry in enumerate(rules):
        if isinstance(entry, str):
            # Format 1: Simple preset
            preset = _PREDEFINED_RULES.get(entry)
            if preset is None:
                errors.append(f"unknown preset '{entry}'")
                continue
            expanded.append(copy.deepcopy(preset))

        elif isinstance(entry, dict):
            preset_name = entry.get("rule")
            if preset_name is not None:
                # Format 2: Preset with overrides
                preset = _PREDEFINED_RULES.get(preset_name)
                if preset is None:
                    errors.append(f"unknown preset '{preset_name}'")
                    continue
                merged_rule = copy.deepcopy(preset)
                # Overlay user overrides using DD-003 deep-merge strategy
                for key, value in entry.items():
                    if key != "rule":
                        merged_rule[key] = value
                expanded.append(merged_rule)
            else:
                # Format 3: Custom rule (no "rule" key)
                expanded.append(entry)

    project["security_group"]["rules"] = expanded
```

**When expansion happens**: During Phase 1 (config loading, line 573), before validation. Our validator sees fully-expanded rules.

**Validation**: Unknown preset names get appended to the errors list, causing Phase 1 validation failure.

### Rationale

**Reduces verbosity**: `"SSH"` (7 characters) vs. full rule definition.

**Maintains flexibility**: The override mechanism lets us customize presets without losing brevity.

**Backward compatible**: Full rule dict syntax still works (Format 3).

**Early expansion**: Happens in Phase 1 with validation, not during provisioning. Our validator sees full rules and can check port ranges, IP formats, etc.

**Uses DD-003**: Override merging uses the same deep-merge logic as our defaults inheritance.

**Clear errors**: Unknown preset names caught in Phase 1 with a helpful error message.

### Alternatives Considered

**Alternative 1: No presets, require full rules**
- Always specify complete rule dicts
- **Rejected**: Too verbose; reduces config readability

**Alternative 2: Presets without overrides**
- Only support simple preset strings, no customization
- **Rejected**: A common use case is "SSH from specific network"; we need override support

**Alternative 3: Template syntax with variables**
- Use template: `{preset: SSH, from: 10.0.0.0/8}` with custom DSL
- **Rejected**: More complex; harder to learn; YAML already supports dicts

**Alternative 4: Preset expansion during provisioning (Phase 3)**
- Keep presets in config, expand when creating the security group
- **Rejected**: Our validator can't check expanded rules; harder to debug; not truly declarative

**Alternative 5: More granular presets**
- Add presets like "SSH-from-10", "SSH-from-192", "HTTP-8080", etc.
- **Rejected**: Preset explosion; our override mechanism is more flexible

### Consequences

**Positive**:
- **Concise configs**: Common rules reduced to a single word
- **Readable**: Easy to scan config and see "SSH, HTTP, HTTPS open"
- **Flexible**: Override mechanism handles custom requirements
- **Safe**: Phase 1 validation catches unknown presets
- **Reusable**: Presets in `defaults.yaml` inherited by all projects

**Negative**:
- **Hidden complexity**: `"SSH"` hides actual port/protocol details
- **Learning curve**: Need to learn preset names and override syntax
- **Limited preset library**: Only 8 presets; may not cover all use cases

**Mitigations**:
- We document all presets with examples in CONFIG-SCHEMA.md
- Preset names match common terminology (SSH, HTTP, HTTPS)
- Custom rule syntax (Format 3) always available as an escape hatch
- We can easily add more presets to the `_PREDEFINED_RULES` dict

### See Also

- **DD-003 (Deep-Merge Configuration Inheritance)**: Override merging uses the same deep-merge logic
- **DD-001 (Three-Phase Execution Model)**: Expansion happens in Phase 1 (config loading)
- **DD-004 (Universal Resource Pattern)**: Simplifies security group configuration

---

## DD-017: Hardcoded Federation Defaults as Last-Resort Fallbacks

**Status**: Accepted
**Date**: 2026-04-04
**Location**: `src/resources/federation.py` lines 26-27

### Context

Our federation module defines two hardcoded constants:

```python
_DEFAULT_MAPPING_ID = "federated_mapping"
_DEFAULT_GROUP_PREFIX = "/services/openstack/"
```

Meanwhile, `defaults.yaml` already provides both values in the `federation` section:

```yaml
federation:
  mapping_id: "my-mapping"
  group_prefix: "/services/openstack/"
```

Our normal configuration flow (DD-003) deep-merges `defaults.yaml` into every project config, so every project inherits `mapping_id` and `group_prefix` automatically. This raises the question: should we remove the hardcoded constants in favor of centralizing all defaults in `defaults.yaml`?

### Decision

**We keep the hardcoded constants as last-resort defensive fallbacks.** They're intentional safety nets, not the primary configuration path.

**Three-tier precedence** (highest to lowest):

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Project YAML override | `federation: { group_prefix: "/custom/" }` |
| 2 | `defaults.yaml` inheritance | `federation: { group_prefix: "/services/openstack/" }` |
| 3 | Hardcoded fallback | `_DEFAULT_GROUP_PREFIX = "/services/openstack/"` |

**When the fallbacks activate** (edge cases only):

- `_DEFAULT_GROUP_PREFIX`: A project's merged config lacks `group_prefix` entirely — possible if `defaults.yaml` has no `federation` section or was stripped during custom config loading.
- `_DEFAULT_MAPPING_ID`: No project in the entire run defines `mapping_id` AND `defaults.yaml` doesn't define it — possible if federation config is completely absent from all sources.

Under normal operation with a well-formed `defaults.yaml`, the hardcoded constants are **never reached**.

### Rationale

**Defense in depth**: Our code shouldn't crash with a `KeyError` if configuration is incomplete. The hardcoded fallbacks ensure the federation module always has sensible values, even if `defaults.yaml` is misconfigured or minimal.

**Not worth centralizing further**: Moving these to a shared constants file or removing them in favor of `defaults.yaml` would gain nothing — `defaults.yaml` already provides them, and the fallbacks cost nothing (two lines, zero runtime overhead in the normal path).

**Precedent in our codebase**: Other modules use `cfg.get("field", default_value)` with inline defaults. Our federation module simply names its defaults as module-level constants for readability.

### Alternatives Considered

**Alternative 1: Remove hardcoded defaults, rely solely on `defaults.yaml`**
- Remove `_DEFAULT_*` constants; use `federation_cfg["group_prefix"]` (hard key access)
- **Rejected**: Fragile — any config path that skips `defaults.yaml` merging (e.g., testing, partial configs) would crash with `KeyError`

**Alternative 2: Move constants to a central `constants.py` file**
- Create `src/constants.py` with all default values
- **Rejected**: Over-engineering for two constants used only in one module; adds indirection without benefit

**Alternative 3: Remove from `defaults.yaml`, keep only hardcoded**
- Let the code provide the fallbacks, remove from `defaults.yaml`
- **Rejected**: `defaults.yaml` is our user-facing configuration surface; removing values from it hides defaults from operators

### Consequences

**Positive**:
- **Robust**: Our federation module can't crash due to missing config keys
- **Transparent**: `defaults.yaml` shows operators what values will be used
- **Zero overhead**: Fallbacks only evaluated when `.get()` finds no key
- **Self-documenting**: Named constants (`_DEFAULT_MAPPING_ID`) clarify intent better than inline strings

**Negative**:
- **Dual source of truth**: The same value (`"/services/openstack/"`) appears in both `defaults.yaml` and `federation.py`
- **Potential drift**: If we change `group_prefix` in `defaults.yaml`, the hardcoded fallback retains the old value

**Mitigations**:
- Fallbacks are last-resort only — `defaults.yaml` is our authoritative source for operators
- If the hardcoded fallback ever activates, it means configuration is incomplete, and the value is still reasonable
- The `_DEFAULT_` prefix naming convention signals "this is a fallback, not the primary config"

### See Also

- **DD-003 (Deep-Merge Configuration Inheritance)**: Defines how `defaults.yaml` values flow into projects
- **DD-008 (Federation Mapping as Shared Resource)**: Federation mapping lifecycle and shared resource model
- **DD-010 (Deterministic Federation Rule Ordering)**: Uses `group_prefix` to build sorted rules

---

## DD-018: Separate State File for Observed State

**Status**: Accepted (supersedes DD-009)
**Date**: 2026-04-04
**Implemented in**: `src/state_store.py`, `src/config_loader.py`, `src/reconciler.py`, `src/resources/prealloc/fip.py`, `src/resources/network.py`

### Context

DD-009 introduced config writeback — writing runtime-observed state (FIP IDs, router IPs, release audit trails) back into project config YAML files. While functional, this approach mixes declarative intent (what we want) with observed state (what exists in OpenStack). This creates several problems:

- **Noisy git diffs**: Every provisioning run changes the config file even when we didn't request anything
- **Merge conflicts**: Observed state fields cause conflicts in multi-operator environments
- **Unclear ownership**: We might accidentally edit system-managed fields
- **API incompatibility**: A future customer-facing API can't write to YAML files on disk

**What changed from DD-009**: We deleted `src/config_writer.py` entirely and replaced all `write_project_config()` calls with `ctx.state_store.save()` calls. The persist functions in `prealloc/fip.py` and `network.py` now take `ctx: SharedContext` and write to the state file instead of mutating project YAML.

### Decision

**We move observed state to a separate YAML file per project** using a `StateStore` protocol:

1. **State file location**: `config/state/<config_file_stem>.state.yaml`
2. **State file naming**: Uses the config file stem (e.g., `dev-team.yaml` → `config/state/dev-team.state.yaml`)
3. **Protocol-based design**: `StateStore` protocol with `load()` / `save()` methods, allowing us to swap the YAML-file backend for a database-backed implementation
4. **Automatic migration**: State keys found in project YAML but not in the state file are auto-migrated on first run

**State keys we moved**: `preallocated_fips`, `released_fips`, `router_ips`, `released_router_ips`

**Additional metadata we persist**: `project_id`, `domain_id`, `last_reconciled_at` under a `metadata` namespace

**Our StateStore protocol** (`src/state_store.py` lines 33-39):
```python
@runtime_checkable
class StateStore(Protocol):
    """Protocol for reading/writing per-project observed state."""

    def load(self, state_key: str) -> dict[str, Any]: ...

    def save(self, state_key: str, key_path: list[str], value: Any) -> None: ...
```

**Our YAML-file implementation** (`YamlFileStateStore`, lines 42-103):
- State files at `<state_dir>/<state_key>.state.yaml`
- Directory created lazily on first save
- `load()` returns empty dict if file is missing
- `save()` does read-modify-write with nested key traversal (same algorithm `config_writer.py` used)

**How state flows through the system**:

1. **Phase 1** (`config_loader.py`): `_load_state_into_config()` loads state from the state file and merges into the in-memory config dict. State keys from the state file take precedence. State keys found in project YAML but not in the state file are auto-migrated.
2. **Phase 3** (`reconciler.py`): After `ensure_project()`, we persist `project_id` and `domain_id` to the state file as metadata. After successful reconciliation, we persist `last_reconciled_at`.
3. **Resource modules** (`prealloc/fip.py`, `network.py`): All persist functions (`_persist_fips`, `_persist_released_fips`, `_persist_router_ips`, `_persist_released_router_ips`) write via `ctx.state_store.save()`.

**State key injection** (`config_loader.py` line 142):
```python
merged["_state_key"] = pfile.stem  # e.g. "dev-team" from "dev-team.yaml"
```

**SharedContext wiring** (`src/utils.py` line 85):
```python
@dataclass
class SharedContext:
    ...
    state_store: StateStore | None = None
```

The `state_store` is created in `main()` and threaded through to `load_all_projects()`, `SharedContext`, and all resource modules.

### Rationale

**Clean separation**: Our config files are purely declarative; state files capture observed reality. This is the fundamental architectural improvement — config describes intent, state records what exists.

**Forward compatible**: The `StateStore` protocol lets us swap `YamlFileStateStore` for a database-backed implementation when a customer-facing API arrives. The resource modules don't know or care which backend is used.

**Non-breaking migration**: Existing state keys in project YAML are auto-migrated to state files on first run. All reads via `cfg.get("preallocated_fips", [])` work unchanged because we merge state into the in-memory config dict during loading. This means existing deployments upgrade transparently.

**Eliminated `config_writer.py`**: We deleted the entire config writeback module. All writes now go through `StateStore.save()`, which has the same read-modify-write semantics but targets the state file instead of the project YAML.

**Metadata enrichment**: Persisting `project_id`, `domain_id`, and `last_reconciled_at` in the state file gives us operational visibility without polluting config files. We can answer "when was this project last reconciled?" and "what's the OpenStack project ID?" by reading the state file.

### Alternatives Considered

**Alternative 1: Keep config writeback (DD-009), add `.gitignore` guidance**
- Document that operators should `.gitignore` the state fields or use `git diff --ignore-matching-lines`
- **Rejected**: Doesn't solve the merge conflict problem or the unclear ownership issue; merely hides symptoms

**Alternative 2: Single global state file**
- Store all projects' state in one `state.yaml` file
- **Rejected**: Creates a serialization bottleneck; merge conflicts between projects; harder to `.gitignore` selectively; doesn't scale to parallel provisioning

**Alternative 3: OpenStack resource tags for state**
- Tag pre-allocated FIPs with `provisioner-managed=true` and store metadata in tags
- **Rejected**: Tags have size limits; not all OpenStack resources support tags; relies on tagging support being available; slower (API calls to read tags vs. local file read)

**Alternative 4: SQLite database for state**
- Use a local SQLite file instead of per-project YAML files
- **Rejected**: Harder to inspect/debug; YAML state files are human-readable and easy to edit in emergencies; SQLite adds a dependency; overkill for our per-project key-value needs

**Alternative 5: Abstract base class instead of Protocol**
- Use `abc.ABC` with `@abstractmethod` instead of `typing.Protocol`
- **Rejected**: Protocol is more Pythonic for structural subtyping; doesn't require inheritance; better for our use case where we want duck-typing compatibility (any object with `load()`/`save()` methods works)

### Consequences

**Positive**:
- **Pure declarative config**: Our config YAML files are never modified by the provisioner
- **Clean git diffs**: Only user-intended changes appear in version control
- **Flexible tracking**: `config/state/` can be `.gitignore`d, tracked separately, or backed up independently
- **Swappable backend**: The `StateStore` protocol lets us move to a database-backed implementation without changing resource modules
- **Transparent migration**: Existing state keys auto-migrate from project YAML to state file on first run
- **Operational metadata**: `last_reconciled_at`, `project_id`, and `domain_id` tracked without polluting config

**Negative**:
- **Extra file per project**: Each project gets a `.state.yaml` file in `config/state/`
- **One-time migration needed**: Existing deployments see a one-time migration on first run (handled automatically)
- **Two places to look**: Operators must check both config and state files to understand full project state

**Mitigations**:
- State files are human-readable YAML (easy to inspect with any text editor)
- Auto-migration means zero manual steps for existing deployments
- State is merged into the in-memory config dict during loading, so resource modules see a unified view
- We document the state file location and format in USER-GUIDE.md and CONFIG-SCHEMA.md
- `_state_key` injection ensures every project config knows where its state lives

### State File Format

```yaml
metadata:
  project_id: "550e8400-e29b-41d4-a716-446655440000"
  domain_id: "default"
  last_reconciled_at: "2026-04-04T14:10:00+00:00"
preallocated_fips:
  - id: "550e8400-e29b-41d4-a716-446655440001"
    address: "203.0.113.42"
router_ips:
  - id: "660f8400-e29b-41d4-a716-446655440000"
    name: "dev-router"
    external_ip: "203.0.113.50"
released_fips:
  - address: "203.0.113.44"
    released_at: "2026-04-04T14:10:00+00:00"
    reason: "address taken by another project"
```

### See Also

- **DD-009 (Config Writeback for Idempotency)**: The approach DD-018 supersedes; same semantics, different storage backend
- **DD-011 (Router IP Capture-and-Track Pattern)**: Router IP persistence now writes to state file via `ctx.state_store.save()`
- **DD-014 (Drift Detection & Reconciliation Pattern)**: FIP drift persistence now writes to state file; drift detection reads from state-merged config
- **DD-006 (SharedContext for Cross-Cutting Concerns)**: `state_store` added as a new cross-cutting concern on `SharedContext`

---

## DD-019: Optional FIP Reclamation

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/resources/prealloc/fip.py`, `src/config_validator.py`, `config/defaults.yaml`

### Context

DD-014 introduced FIP drift reconciliation with three behaviors:
1. **Adopt untracked** FIPs (in OpenStack but not in config)
2. **Reclaim missing** FIPs by re-allocating the exact same IP address
3. **Track released** FIPs in an audit trail

Step 2 (reclamation) is deep business logic that attempts to get back the *same* IP address when a FIP is deleted externally. This is only needed when IP stability matters (DNS records, firewall rules, partner allowlists). Most projects don't need this — they just need *any* floating IP, not a specific address.

With reclamation always on, a missing FIP triggers a `create_ip` call with `floating_ip_address=<specific address>`, which can fail with a 409 ConflictException if the address was already taken. This produces a FAILED action even though the system could simply allocate a different IP.

### Decision

**We make FIP reclamation opt-in** via a top-level boolean field `reclaim_floating_ips` (default `false`).

**Always on** (regardless of flag):
- Adopt untracked FIPs into config
- Detect missing FIPs
- Record releases in `released_fips` audit trail
- Persist updated `preallocated_fips` state

**When `reclaim_floating_ips: false`** (default):
- Missing FIPs are moved directly to `released_fips` with reason `"FIP deleted externally"`
- Removed from `preallocated_fips`
- Action status: UPDATED (not FAILED — system is working as configured)
- No `create_ip` call with a specific address
- Normal scale-up in `ensure_preallocated_fips()` allocates new (different) FIPs to reach desired count

**When `reclaim_floating_ips: true`**:
- Existing DD-014 behavior: raise quota, attempt `create_ip` with specific address
- On ConflictException: move to `released_fips` with FAILED status
- On success: new ID in `preallocated_fips`

**Config placement**: Top-level project field (not under `quotas.network`), because it's a behavior toggle, not a quota value.

```yaml
# defaults.yaml
reclaim_floating_ips: false

# projects/critical-prod.yaml (override for IP-stable projects)
reclaim_floating_ips: true
```

### Rationale

**Separation of concerns**: Most projects just need "N floating IPs" — they don't care *which* IPs. Only projects with external dependencies on specific addresses (DNS, firewall rules) need reclamation.

**Cleaner default behavior**: With reclamation off, a deleted FIP produces an UPDATED action (system handles it gracefully) rather than a FAILED action (ConflictException when the address is taken). The provisioner then allocates a replacement on the same run via normal scale-up.

**Top-level field**: Placed alongside `state`, `enabled`, `description` because it's a behavior toggle. Avoids polluting the quota validator (which checks all values are non-negative integers).

### Alternatives Considered

**Alternative 1: Always reclaim (was the current behavior)**
- Every missing FIP triggers a `create_ip` with the specific address
- **Rejected**: Produces unnecessary FAILED actions for projects that don't need IP stability; couples all projects to reclamation logic

**Alternative 2: Put flag under `quotas.network`**
- `quotas.network.reclaim_floating_ips: true`
- **Rejected**: `quotas.network` values are validated as non-negative integers; a boolean would need special-casing in the quota validator

**Alternative 3: Per-FIP reclamation policy**
- Tag individual FIPs as "reclaimable" vs "replaceable"
- **Rejected**: Over-engineering; the decision is per-project, not per-FIP

### Consequences

**Positive**:
- **Cleaner defaults**: Most projects never see FAILED actions from reclamation conflicts
- **Opt-in complexity**: Only projects that need IP stability opt in to reclamation
- **Same audit trail**: Released FIPs are always tracked regardless of reclamation mode
- **Backward compatible**: Existing projects that relied on reclamation just add `reclaim_floating_ips: true`

**Negative**:
- **New config field**: One more boolean to document and validate
- **Behavioral change**: Existing deployments upgrading will stop reclaiming by default (they must opt in)

**Mitigations**:
- Default in `defaults.yaml` makes it explicit
- Documentation clearly explains the two modes
- Validation rejects non-boolean values

### See Also

- **DD-014 (Drift Detection & Reconciliation Pattern)**: The parent pattern that DD-019 refines
- **DD-018 (Separate State File)**: Released FIPs are persisted to the state file in both modes

---

## DD-020: Opt-In Resource Functions

**Status**: Accepted
**Date**: 2026-04-04
**Implemented in**: `src/resources/network.py`, `src/resources/quotas.py`, `src/resources/prealloc/fip.py`, `src/resources/prealloc/network.py`

### Context

The reconciler calls all resource functions unconditionally for `present`-state projects. Two functions already guarded with `cfg.get()` + early return (`ensure_baseline_sg`, `ensure_group_role_assignments`). Four others crashed with `KeyError` when their config section was absent:

- `ensure_network_stack()` — `cfg["network"]`
- `ensure_quotas()` — `cfg["quotas"]`
- `ensure_preallocated_fips()` — `cfg["quotas"]["network"]`
- `ensure_preallocated_network()` — `cfg["quotas"]["network"]`

This meant every project config **must** include all sections, even if that resource type isn't needed.

### Decision

**Every resource function must be opt-in**: if its config section is absent, return a SKIPPED action instead of crashing.

**Guard pattern**:
```python
def ensure_X(cfg, project_id, ctx):
    x_cfg = cfg.get("x_section")
    if not x_cfg:
        return ctx.record(ActionStatus.SKIPPED, "resource_type", "all", "no x_section configured")
    # ... normal logic ...
```

**Regression test**: A single parametrized test (`tests/test_opt_in_guards.py`) covers all 6 resource functions. Each entry specifies the function, the config key to delete, and the expected SKIPPED resource type. Adding a new resource function requires adding one row to the parametrize table.

### Rationale

**Guards are cheap**: A single `cfg.get()` + early return adds negligible overhead and zero complexity to the happy path.

**Regression test catches violations**: Any new resource function that crashes on missing config will fail the parametrized test. This prevents the pattern from eroding over time.

**Reconciler stays simple**: The unconditional call pattern in `reconciler.py` is preserved — guards live inside each function, not in the caller. This keeps the reconciler's control flow clean and lets each function own its preconditions.

**Config flexibility**: Projects can now omit sections they don't need (e.g., a project with no network requirements can omit `network` entirely).

### Alternatives Considered

**Alternative 1: Guard in the reconciler (caller-side)**
- Check config keys in `_reconcile_present()` before calling each function
- **Rejected**: Scatters precondition logic across two files; the function signature implies it handles all cases but actually requires the caller to pre-filter

**Alternative 2: Require all config sections (status quo)**
- Keep mandatory sections, rely on `defaults.yaml` to always provide them
- **Rejected**: Fragile — any config path that skips defaults merging (testing, partial configs, future API) crashes with `KeyError`

### Consequences

**Positive**:
- **Robust**: No `KeyError` crashes from missing config sections
- **Flexible**: Projects can omit unused resource sections
- **Self-documenting**: Each function's guard shows exactly which config it requires
- **Regression-proof**: Parametrized test enforces the pattern for all functions

**Negative**:
- **Silent skips**: A typo in a config key (e.g., `quota` instead of `quotas`) silently skips instead of crashing
- **Test maintenance**: New resource functions must add a row to the parametrize table

**Mitigations**:
- Config validation (Phase 1) catches unknown/misspelled keys before reconciliation
- The parametrize table serves as a living inventory of all resource functions

### See Also

- **DD-004 (Universal Resource Pattern)**: Opt-in guards extend the standard check/find/create pattern with a precondition step
- **DD-012 (Project Lifecycle State Machine)**: Guards apply to `present`-state reconciliation; `locked` and `absent` have their own handlers

---

## DD-021: Opt-In CIDR Overlap Enforcement

**Status**: Accepted

**Date**: 2026-04-04

### Context

CIDR overlap checking was originally a hard validation error that blocked provisioning whenever two projects had overlapping `network.subnet.cidr` ranges. However, OpenStack uses overlay networks — each project's network is isolated at L2/L3 by Neutron. Reusing the same CIDR across projects is technically safe and a common practice.

Unique CIDRs are a business-level decision, not a technical requirement. They matter when projects share networks, use VPN peering, or have cross-project routing. For the majority of deployments where projects are fully isolated, enforcing unique CIDRs creates unnecessary friction.

### Decision

Make CIDR overlap enforcement opt-in via a top-level `enforce_unique_cidrs` boolean flag in `defaults.yaml`. The flag defaults to `false`. When `true`, the existing `check_cidr_overlaps()` function runs during config validation; when `false`, the check is skipped entirely.

### Rationale

**Overlay isolation is the norm**: Most OpenStack deployments use overlay networks (VXLAN, GRE, Geneve). Projects cannot see each other's networks, so overlapping CIDRs cause no conflicts.

**Business logic, not technical constraint**: Unique CIDRs are only needed when networks are interconnected — VPN peering, shared networks, or cross-project routing. This is an operator's architectural choice, not a universal requirement.

**Minimal implementation**: A single `if defaults.get("enforce_unique_cidrs", False)` guard before the existing `check_cidr_overlaps()` call. No changes to the overlap-detection logic itself.

**Consistent with existing patterns**: Follows the same opt-in boolean pattern as `reclaim_floating_ips` (DD-019).

### Alternatives Considered

**Alternative 1: Keep as hard error (status quo)**
- Forces unique CIDRs on all deployments
- **Rejected**: Penalizes the common case (isolated overlay networks) to protect the uncommon case (cross-project routing)

**Alternative 2: Downgrade to warning**
- Log a warning but allow provisioning to proceed
- **Rejected**: Warnings are easily ignored; an explicit opt-in flag is clearer about intent

### Consequences

**Positive**:
- Operators with isolated overlay networks can reuse CIDRs freely
- Environments that need unique CIDRs can enable the check explicitly
- Existing `check_cidr_overlaps()` logic is unchanged

**Negative**:
- Operators who previously relied on the hard error must now set the flag to maintain the same behavior

---

## DD-022: File-Locking for State Store Concurrency

**Status**: Accepted

### Context

The `YamlFileStateStore` uses an unprotected Read-Modify-Write (RMW) pattern that is vulnerable to race conditions:

**Current Pattern**:
1. Read YAML file into memory
2. Modify data structure in-memory
3. Write entire structure back to file

**Race Condition Scenarios**:
- **Multiple invocations**: Developer runs CLI locally while CI/CD pipeline runs simultaneously
- **Manual triggers**: Developer manually triggers workflow while auto-deploy runs
- **Data loss**: Process A reads → Process B reads → Process A writes → Process B writes (overwrites A's changes)
- **Partial writes**: Process crashes during YAML write → corrupted file

**Why This Matters**:
- GitHub Actions `concurrency` groups prevent *some* cases but not all (local + CI/CD can still overlap)
- FIP pre-allocation, router IPs, and metadata all use RMW pattern
- Silent data loss is unacceptable for infrastructure state

### Decision

Add file-level locking with atomic writes using the `filelock` library:

**Locking Strategy**:
- **Library**: `filelock>=3.12` (cross-platform, 25M+ downloads/month)
- **Scope**: One lock file per state file (`proj.state.yaml` → `proj.state.yaml.lock`)
- **Type**: Exclusive locks for both reads and writes (prevents load-during-write corruption)
- **Timeout**: 30 seconds with clear `Timeout` exceptions on contention
- **Atomic writes**: Write-temp-then-rename pattern (`Path.replace()` is atomic on all platforms)

**Implementation Pattern**:
```python
with FileLock(lock_path, timeout=30):
    # Read YAML
    data = yaml.safe_load(fh)
    # Modify in-memory
    data[key] = value
    # Atomic write: temp file then rename
    temp_path.write(yaml_content)
    temp_path.replace(final_path)  # atomic
```

### Rationale

**Why file-locking?**:
- Prevents concurrent access corruption in edge cases (local + CI/CD overlap)
- Provides crash safety (atomic writes prevent partial reads)
- Minimal overhead (~2-4ms per operation, <2% total impact)
- Battle-tested library with proven reliability

**Why exclusive locks for reads?**:
- Prevents load-during-write corruption (reader sees partial YAML)
- Simplifies implementation (no shared/exclusive lock management)
- State operations are fast (<100ms), exclusive-only is acceptable

**Why 30-second timeout?**:
- Average reconciliation: ~7s for 25 projects, single project << 1s
- 30s allows plenty of headroom for slow operations
- Timeout = genuine contention → fail fast is correct behavior

**Why atomic writes?**:
- Prevents partial writes visible to concurrent readers
- `Path.replace()` is atomic on POSIX and Windows (Python 3.3+)
- Crash safety: If process dies during write, original file intact

### Alternatives Considered

**Alternative 1: Use `fcntl` directly**:
- **Pros**: No external dependency
- **Cons**: Unix-only (doesn't work on Windows), more complex API
- **Rejected**: `filelock` provides cross-platform abstraction with better ergonomics

**Alternative 2: No locking**:
- **Pros**: Simplest implementation
- **Cons**: Silent data loss in concurrent scenarios (unacceptable for infrastructure state)
- **Rejected**: Risk of data corruption outweighs simplicity

**Alternative 3: Database-backed state store**:
- **Pros**: Built-in transaction support, better concurrency
- **Cons**: Requires external service, over-engineering for CLI tool usage
- **Rejected**: File-locking is sufficient for current scale; database can be added later via `StateStore` protocol

**Alternative 4: Advisory locking with cooperative behavior**:
- **Pros**: Lighter weight than exclusive locks
- **Cons**: Requires all processes to cooperate, no protection against non-cooperative processes
- **Rejected**: Exclusive locks provide stronger guarantees

**Alternative 5: Retry on lock failure**:
- **Pros**: More resilient to transient contention
- **Cons**: Lock timeout = genuine contention (not transient), retrying risks deadlock/cascading delays
- **Rejected**: Fail fast on timeout, mark project as failed, continue with next project

### Consequences

**Positive**:
- Prevents data loss in concurrent access scenarios
- Crash safety via atomic writes
- Zero API changes (backward compatible)
- Existing tests pass unchanged
- Minimal performance overhead (<2% at current scale)
- Cross-platform support (Linux, macOS, Windows)
- Clear exceptions on contention (`Timeout`)

**Negative**:
- ⚠️ New dependency (`filelock>=3.12`)
- ⚠️ New failure mode (lock timeout, though rare)
- ⚠️ Lock files (`.lock`) persist alongside state files (harmless, can be gitignored)

**Trade-offs**:
- **Contention handling**: 30s timeout = hard failure on genuine contention (acceptable, rare in sequential processing)
- **Performance**: ~2-4ms overhead per operation (negligible for current usage patterns)
- **Complexity**: Additional locking logic (mitigated by comprehensive concurrency tests)

**Migration Path**:
- No data migration required (existing `.state.yaml` files unchanged)
- Lock files created automatically on first access
- Rollback: Simply revert code changes and remove `filelock` dependency

**Future Considerations**:
- If contention becomes common, consider database-backed `StateStore` implementation
- `StateStore` protocol allows swapping backends without changing caller code
- File-locking remains appropriate for CLI tool usage patterns

---

## DD-023: Human-Readable Quota Units

**Status**: Accepted

### Context

OpenStack quota APIs expect specific units that users must remember and convert:
- **Compute RAM** → Megabytes (MB): `ram: 51200` means 50GB
- **Block storage** → Gigabytes (GB): `gigabytes: 500`, `backup_gigabytes: 100`

**Problems**:
- Users must do mental math to convert GB/GiB to MB for RAM quotas
- Config readability suffers: `ram: 102400` vs `ram: "100GB"`
- Error-prone: Easy to miscalculate conversions (50GB = 50000MB vs 51200MiB)
- No indication in config file what unit the value represents

### Decision

Support human-readable unit strings for RAM and storage quota fields while maintaining full backward compatibility with existing integer configs.

**Supported fields**:
- `quotas.compute.ram` → Converts to MB for Nova API
- `quotas.block_storage.gigabytes` → Converts to GB for Cinder API
- `quotas.block_storage.backup_gigabytes` → Converts to GB for Cinder API

**Supported units**:
- **Decimal** (base-10): KB, MB, GB, TB, PB (powers of 1000)
- **Binary** (base-2): KiB, MiB, GiB, TiB, PiB (powers of 1024)
- **Shorthand**: K, M, G, T, P (map to binary: G = GiB)

**Syntax**:
- Format: `"<number><unit>"` (e.g., `"50GB"`, `"2TB"`, `"100GiB"`)
- Whitespace allowed: `"50 GB"` and `"50GB"` are equivalent
- Fractional values supported: `"1.5TB"` → 1500 GB (rounded to nearest integer)
- Special value `-1` (unlimited) must be literal integer, not string

**Implementation**:
- Parse during config validation in `QuotaConfig.validate()` and `QuotaConfig.from_dict()`
- Store as integers internally (API-ready format)
- No changes to OpenStack API calls or state files

### Rationale

**Why support units?**:
- **Readability**: `ram: "100GB"` is immediately clear vs `ram: 102400`
- **Reduced errors**: No manual conversion math required
- **Self-documenting**: Units visible in config file
- **Flexibility**: Users can choose decimal (GB) or binary (GiB) based on context

**Why RAM and storage only?**:
- Count-based quotas (cores, instances, ports) don't need units
- RAM and storage are the only quotas where unit conversion is confusing
- Focused scope reduces complexity

**Why support both decimal and binary?**:
- **Decimal (GB)**: Marketing/sales use base-10 (50GB = 50,000MB)
- **Binary (GiB)**: Technical specs use base-2 (50GiB = 51,200MB)
- Users expect both, so support both explicitly

**Why parse in validation?**:
- Validation happens once during config load
- Runtime code (resource modules) receives integers unchanged
- No performance impact on API calls

### Alternatives Considered

**Alternative 1: Always require units (no plain integers)**:
- **Pros**: Enforces clarity, no ambiguity
- **Cons**: Breaks all existing configs (backward incompatible)
- **Rejected**: Backward compatibility is critical

**Alternative 2: Auto-detect units (assume MB/GB if < 1000)**:
- **Pros**: No user action needed
- **Cons**: Ambiguous (is `500` MB or GB?), error-prone
- **Rejected**: Implicit behavior is confusing

**Alternative 3: Separate fields (`ram_gb`, `ram_mb`)**:
- **Pros**: Explicit units in field name
- **Cons**: Config schema bloat, confusing for users
- **Rejected**: Unit strings are clearer

**Alternative 4: Support units for all quota fields**:
- **Pros**: Consistency across all quotas
- **Cons**: Unnecessary for count-based quotas (cores, instances)
- **Rejected**: Focused scope is better UX

### Consequences

**Positive**:
- ✅ Improved config readability
- ✅ Reduced user errors in quota configuration
- ✅ Self-documenting configs (units visible)
- ✅ Full backward compatibility (integers still work)
- ✅ No runtime performance impact
- ✅ Clear error messages for invalid units

**Negative**:
- ⚠️ Two valid formats (integers and unit strings) may confuse some users
- ⚠️ Slightly more complex validation logic
- ⚠️ Documentation must explain both formats

**Neutral**:
- Users can gradually migrate from integers to unit strings
- Recommend unit strings in docs, but don't require migration
- Mixed configs work (some fields with units, some without)

---

## Summary Table

| ID | Decision | Impact | Related |
|----|----------|--------|---------|
| DD-001 | Three-Phase Execution Model | High - Core architecture | DD-012, DD-013, DD-016 |
| DD-002 | Allocate-Then-Lock Pattern | High - REJECTED | DD-009 |
| DD-003 | Deep-Merge Configuration | High - Affects all config | DD-016 |
| DD-004 | Universal Resource Pattern | Medium - Code consistency | DD-014, DD-015 |
| DD-005 | Retry with Exponential Backoff | Medium - Resilience | DD-015 |
| DD-006 | SharedContext | Medium - Code organization | All |
| DD-007 | Error Isolation Per Project | Medium - Operational behavior | DD-013, DD-015 |
| DD-008 | Federation as Shared Resource | Medium - Affects --project usage | DD-010 |
| DD-009 | Config Writeback (superseded by DD-018) | High - Idempotency mechanism | DD-011, DD-014, DD-018 |
| DD-010 | Deterministic Rule Ordering | Low - Implementation detail | DD-008 |
| DD-011 | Router IP Capture-and-Track | Medium - Operational visibility | DD-009, DD-014 |
| DD-012 | Project Lifecycle State Machine | High - Core architecture | DD-001, DD-013 |
| DD-013 | Teardown & Reverse Dependency | High - Safe deletion | DD-001, DD-012 |
| DD-014 | Drift Detection & Reconciliation | High - Self-healing | DD-009, DD-011 |
| DD-015 | Graceful Service Degradation | Medium - Deployment flexibility | DD-005, DD-007 |
| DD-016 | Security Group Rule Presets | Medium - UX improvement | DD-003, DD-001 |
| DD-017 | Hardcoded Federation Defaults as Fallbacks | Low - Defensive coding | DD-003, DD-008, DD-010 |
| DD-018 | Separate State File for Observed State | High - State management | DD-009, DD-011, DD-014 |
| DD-019 | Optional FIP Reclamation | Medium - Behavior toggle | DD-014, DD-018 |
| DD-020 | Opt-In Resource Functions | Medium - Robustness | DD-004, DD-012 |
| DD-021 | Opt-In CIDR Overlap Enforcement | Low - Behavior toggle | DD-019 |
| DD-022 | File-Locking for State Store Concurrency | Medium - Data safety | DD-018 |
| DD-023 | Human-Readable Quota Units | Low - User experience | - |

---

**Related Documents**:
- [SPECIFICATION.md](SPECIFICATION.md) - Technical implementation details
- [USER-GUIDE.md](USER-GUIDE.md) - How these decisions affect operators
- [CONFIG-SCHEMA.md](CONFIG-SCHEMA.md) - Configuration deep-merge behavior
