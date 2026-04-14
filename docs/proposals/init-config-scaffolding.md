# `tenantctl --init` Config Scaffolding

> **STATUS: PROPOSED**
>
> This document proposes adding a `tenantctl --init` flag that generates a complete,
> annotated config directory from the existing dataclass models. The generated files
> serve as both documentation and starting point for new deployments.
>
> - **Proposed**: 2026-04-06
> - **Decision**: Pending

---

## Problem Statement

Users who install tenantctl in a venv receive no config files. The `config-sample/`
directory ships in the git repository but is not included in the Python package
distribution. This means a new user must:

1. Discover that a `config/` directory is expected (by reading docs or error messages)
2. Learn the two-tier structure: `defaults.yaml` + `projects/*.yaml`
3. Figure out which fields exist, which are required, which are optional, and what
   values are valid — across six nested dataclass models and ~40 fields
4. Understand features like preset security-group rules, the `ram_gibibytes` alias,
   placeholder substitution (`{name}`), and CIDR auto-population

The `config-sample/` directory partially solves this but has a maintenance problem:
it is a hand-written copy of what the models support. When new fields or presets are
added to `src/models/`, the samples must be updated manually — and they drift.

---

## Proposed Solution

Add a `--init` flag to the CLI:

```
tenantctl --init [TARGET_DIR]
```

**Behavior:**

- `TARGET_DIR` defaults to `config/` (the normal working directory)
- If the target already contains `defaults.yaml`, refuse and print a message
  (never overwrite existing config)
- Generate a full annotated config directory:

```
config/
├── defaults.yaml          # All fields, commented except required
└── projects/
    └── example.yaml       # Minimal project with all optional fields commented
```

- Print a summary of what was created and suggest next steps

**Example session:**

```
$ pip install openstack-tenantctl
$ tenantctl --init
Created config/defaults.yaml
Created config/projects/example.yaml

Edit config/defaults.yaml to set shared defaults (quotas, security groups, etc.)
Then customize config/projects/example.yaml with your first project.

Validate with:  tenantctl --config-dir config --dry-run --offline
```

---

## Design: Model-Driven Generation

The core design principle is that **generated config comes from the models, not from
hardcoded template strings**. This ensures the output always reflects the actual
schema and cannot drift from the code.

### Why not just ship config-sample/?

Embedding `config-sample/` in the package (via `package_data` or `data_files`) would
be the simplest approach, but has two drawbacks:

1. **Drift** — `config-sample/` is maintained by hand. When a new field is added to a
   model, someone must remember to update the samples. A model-driven generator
   cannot drift because it reads the fields at generation time.

2. **No annotation** — A static file contains whatever comments the author wrote.
   A model-driven generator can attach help text, valid ranges, default values, and
   type information directly from field metadata — producing richer documentation
   that stays accurate automatically.

### How it works

The generator walks the frozen dataclass hierarchy using `dataclasses.fields()` and
renders each field as a YAML line with a comment. Required fields are uncommented;
optional fields are commented out with their defaults shown.

**Field metadata** on each dataclass field drives the output:

```python
@dataclasses.dataclass(frozen=True)
class ProjectConfig:
    name: str = dataclasses.field(
        metadata={
            "help": "OpenStack project name (letters, digits, spaces, hyphens, underscores)",
            "init": "required",         # uncommented in output
        }
    )
    resource_prefix: str = dataclasses.field(
        metadata={
            "help": "Lowercase prefix for all created resources (network, router, etc.)",
            "init": "required",
        }
    )
    description: str = dataclasses.field(
        default="",
        metadata={
            "help": "Human-readable project description",
            "init": "commented",        # commented out with default shown
        }
    )
    reclaim_floating_ips: bool = dataclasses.field(
        default=False,
        metadata={
            "help": "Re-allocate exact FIP addresses when they disappear from OpenStack",
            "init": "defaults_only",    # shown in defaults.yaml, omitted from project files
        }
    )
```

**`init` behaviors:**

| Value | `defaults.yaml` | `projects/example.yaml` |
|-------|-----------------|------------------------|
| `"required"` | Uncommented, placeholder value | Uncommented, placeholder value |
| `"commented"` | Commented with default | Commented with default |
| `"defaults_only"` | Commented with default | Omitted entirely |
| `"internal"` | Omitted | Omitted |
| *(not set)* | Commented with default | Commented with default |

Fields that are internal bookkeeping (`config_path`, `state_key`, `preallocated_fips`,
`released_fips`, `router_ips`, `released_router_ips`) use `"internal"` and never
appear in generated config.

### YAML renderer

A `render_yaml()` function walks `dataclasses.fields()` recursively:

```python
def render_yaml(
    model_cls: type,
    mode: Literal["defaults", "project"],
    indent: int = 0,
) -> str:
    """Render a YAML template from a frozen dataclass.

    For each field:
    - Reads metadata["help"] for the comment line
    - Reads metadata["init"] to decide commented vs uncommented
    - Uses the field's default (or default_factory) as the example value
    - Recurses into nested dataclass fields
    """
```

**Nested models** (e.g., `network: NetworkConfig | None`) are rendered as nested
YAML blocks. When the nested type is `Optional` and `init` is `"commented"`, the
entire block is commented out:

```yaml
# network:
#   # mtu: Network MTU (0 = use Neutron default)
#   # mtu: 0
#   subnet:
#     # cidr: Subnet CIDR notation (e.g., 10.0.0.0/24). Required for non-absent projects.
#     cidr: "10.0.0.0/24"
```

**Special-case fields:**

- `QuotaConfig` — rendered as three sub-dicts (`compute`, `network`, `block_storage`)
  with known quota keys listed as commented examples. The `ram_gibibytes` alias is
  shown alongside `ram` with a note explaining the choice.

- `SecurityGroupConfig.rules` — preset names are listed as comments showing available
  presets from `config_resolver.PREDEFINED_RULES`:

  ```yaml
  security_group:
    name: default
    rules:
      # Available presets: SSH, HTTP, HTTPS, ICMP, "All ICMP", "All TCP", "All UDP", DNS, RDP
      # - "SSH"                    # Allow TCP port 22
      # - rule: "HTTPS"            # Preset with override
      #   remote_ip_prefix: "10.0.0.0/8"
      # - direction: ingress       # Custom rule (no preset)
      #   protocol: tcp
      #   port_range_min: 8080
      #   port_range_max: 8080
      []
  ```

- Placeholder substitution (`{name}`) — documented in comments where relevant:

  ```yaml
  # Placeholders: use {name} in string values — replaced with the project name at load time.
  # Example: description: "{name} environment" → "prod environment"
  ```

---

## Generated Output

### `defaults.yaml`

Shows all configurable fields with help text. Fields shared across projects are
uncommented with sensible starting values; fields typically set per-project are
commented out.

```yaml
# tenantctl defaults — shared configuration inherited by all projects.
# Override any value in individual project files under projects/.
#
# Placeholders: {name} in string values is replaced with the project name.
# Docs: https://github.com/your-org/openstack-tenantctl/blob/main/docs/CONFIG-SCHEMA.md

# description: Human-readable project description
description: "Managed by tenantctl"

# enabled: Set to false to skip provisioning without removing the config file
enabled: true

# state: Lifecycle state — present (provision), locked (freeze), absent (teardown)
state: present

# domain_id: OpenStack domain for project creation.
# Set to null to auto-detect from OS_PROJECT_DOMAIN_ID / OS_USER_DOMAIN_NAME env vars.
# domain_id: "my-domain"
domain_id: null

# reclaim_floating_ips: Re-allocate exact FIP addresses when they disappear
reclaim_floating_ips: false

# reclaim_router_ips: Request same external IP when router is recreated
reclaim_router_ips: false

# track_fip_changes: Persist FIP state on every run to detect address swaps
track_fip_changes: false

# enforce_unique_cidrs: Refuse to run if any two projects share overlapping subnets
enforce_unique_cidrs: false

network:
  # mtu: Network MTU (0 = use Neutron default)
  mtu: 0
  subnet:
    # cidr: Subnet CIDR notation — REQUIRED for non-absent projects
    cidr: "10.0.0.0/24"
    # gateway_ip: Auto-calculated as first usable IP if omitted
    # gateway_ip: "10.0.0.1"
    # allocation_pools: Auto-calculated as all usable IPs minus gateway if omitted
    # allocation_pools:
    #   - start: "10.0.0.2"
    #     end: "10.0.0.254"
    dns_nameservers:
      - "9.9.9.9"
    # dhcp: Enable DHCP on the subnet (alias: enable_dhcp)
    dhcp: true

quotas:
  compute:
    cores: 1
    # ram: Accepts unit strings — "50 GiB", "50GB", or plain MiB integer
    # ram: "1 GiB"
    # ram_gibibytes: Convenience alias — plain integer in GiB (1 = 1024 MiB)
    ram_gibibytes: 1
    instances: 30
  network:
    floating_ips: 1
    networks: 1
    subnets: 1
    routers: 1
    ports: 50
    security_groups: 10
    security_group_rules: 100
    # load_balancers: Octavia quotas — safe to include without Octavia deployed
    load_balancers: 0
  block_storage:
    # gigabytes: Accepts unit strings — "2 TB", "500GB"
    gigabytes: 500
    volumes: 20
    snapshots: 10

# --- Optional sections (uncomment to enable) ---

# security_group:
#   name: default
#   rules:
#     # Available presets: SSH, HTTP, HTTPS, ICMP, "All ICMP", "All TCP", "All UDP", DNS, RDP
#     - "SSH"
#     - direction: ingress
#       protocol: icmp
#       remote_ip_prefix: "0.0.0.0/0"
#       description: "Allow ICMP"

# federation:
#   issuer: "https://myidp.corp/realms/myrealm"
#   mapping_id: "my-mapping"
#   group_prefix: "/services/openstack/"
#   role_assignments:
#     - idp_group: member
#       roles: [member, load-balancer_member]

# group_role_assignments:
#   - group: "my-admin-group"
#     roles: [admin]

# external_network_name: "public"
# external_network_subnet: "external0"
```

### `projects/example.yaml`

Minimal starting point — only required fields uncommented, with commented examples
of common overrides.

```yaml
# tenantctl project configuration.
# Values here override defaults.yaml for this project only.
# Only set fields you want to differ from the defaults.

# name: REQUIRED — OpenStack project name
name: example

# resource_prefix: REQUIRED — lowercase prefix for created resources
resource_prefix: example

# description: "{name} project"

# --- Common per-project overrides (uncomment as needed) ---

# network:
#   subnet:
#     cidr: "192.168.10.0/24"

# quotas:
#   compute:
#     cores: 16
#     ram_gibibytes: 32
#   network:
#     floating_ips: 2
#   block_storage:
#     gigabytes: 1000
```

---

## Files That Would Change

### Model files — add field metadata

Each model file in `src/models/` would gain `metadata=` kwargs on its
`dataclasses.field()` definitions. This is a non-breaking, additive change —
existing `from_dict()` and `validate()` methods ignore metadata entirely.

| File | Change |
|------|--------|
| `src/models/project.py` | Add `metadata={"help": ..., "init": ...}` to all fields |
| `src/models/network.py` | Add metadata to `NetworkConfig`, `SubnetConfig`, `AllocationPool` |
| `src/models/quotas.py` | Add metadata to `QuotaConfig` (plus known-key lists for each section) |
| `src/models/security.py` | Add metadata to `SecurityGroupConfig`, `SecurityGroupRule` |
| `src/models/federation.py` | Add metadata to `FederationConfig`, `FederationRoleAssignment` |
| `src/models/access.py` | Add metadata to `GroupRoleAssignment` |
| `src/models/state.py` | Mark all fields as `"internal"` (never shown in generated config) |

### New file: `src/init_config.py`

The config scaffolding module. Responsibilities:

- `render_defaults_yaml()` — generate `defaults.yaml` from model introspection
- `render_project_yaml()` — generate `projects/example.yaml`
- `init_config_dir(target: Path)` — create directory structure, write files, print summary
- Guard: refuse if `target/defaults.yaml` already exists

### Modified: `src/main.py`

- Add `--init` argument to argparse (mutually exclusive with `--config-dir` operations)
- Call `init_config_dir()` and exit before the normal load-validate-reconcile pipeline
- `--init` accepts an optional positional `TARGET_DIR` argument (default: `config/`)

### Tests

| File | What |
|------|------|
| `tests/test_init_config.py` | Core test suite for the scaffolding module |
| | Verify generated `defaults.yaml` parses as valid YAML |
| | Verify generated `projects/example.yaml` parses as valid YAML |
| | Verify round-trip: generated config passes `load_all_projects()` validation |
| | Verify refusal when target directory already contains config |
| | Verify all model fields with `init != "internal"` appear in output |
| `tests/test_main.py` | Add `--init` integration test (creates temp dir, runs CLI, checks files) |

### Guard test: no field left behind

A critical test ensures the generator stays in sync with the models:

```python
def test_all_user_facing_fields_have_init_metadata():
    """Every field that is not internal must have 'init' metadata.

    This test fails when someone adds a new field to a model but forgets
    to add metadata — preventing silent drift between models and the
    generated config.
    """
    for model_cls in [ProjectConfig, NetworkConfig, SubnetConfig, ...]:
        for f in dataclasses.fields(model_cls):
            if f.metadata.get("init") == "internal":
                continue
            assert "help" in f.metadata, (
                f"{model_cls.__name__}.{f.name} is missing 'help' metadata"
            )
            assert "init" in f.metadata, (
                f"{model_cls.__name__}.{f.name} is missing 'init' metadata"
            )
```

This test is the mechanism that prevents the same drift problem `config-sample/` has.
Adding a model field without metadata causes a test failure with a clear message.

---

## Interaction with Existing Features

### Placeholder substitution

Generated config shows `{name}` placeholders in comments as examples. The generator
does **not** perform substitution — that happens at load time in `config_loader.py`
as usual.

### Preset security-group rules

The generator reads `config_resolver.PREDEFINED_RULES.keys()` to list available
preset names in the comment block. If presets are added or removed, the generated
output reflects the change automatically.

### Config resolver auto-population

Fields like `gateway_ip` and `allocation_pools` are documented as "auto-calculated
when omitted" in comments. The generated config omits these fields (commented out
with example values) so the resolver handles them at load time.

### `config-sample/` directory

The existing `config-sample/` directory remains in the repository as a reference
for development and documentation. It is not replaced — `--init` is the
runtime equivalent for users who install from PyPI.

---

## Alternatives Considered

| Approach | Verdict |
|----------|---------|
| **Ship `config-sample/` in package** | Simplest, but static files drift from models. No help text beyond hand-written comments. Chosen as fallback if model-driven is deferred. |
| **Interactive wizard (`--init --interactive`)** | Nice UX but high implementation cost. Could be a follow-up — the model metadata added for `--init` would power the wizard's prompts too. |
| **JSON Schema generation** | Produces machine-readable schema but not human-editable YAML. Useful for editor validation (future enhancement) but does not solve the "give me a starting config" problem. |
| **Cookiecutter / copier template** | External dependency, overkill for two files. Template maintenance has the same drift problem as `config-sample/`. |

---

## References

- Model files: `src/models/project.py`, `network.py`, `quotas.py`, `security.py`, `federation.py`, `access.py`, `state.py`
- Config resolver (presets, auto-population): `src/config_resolver.py`
- Config loader (merge, validation pipeline): `src/config_loader.py`
- Existing config samples: `config-sample/defaults.yaml`, `config-sample/projects/`
- Config schema docs: `docs/CONFIG-SCHEMA.md`
- CLI entry point: `src/main.py`
