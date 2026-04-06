# OpenStack TenantCtl

**One YAML file. One command. Fully provisioned OpenStack projects.**

### Project Status

The core provisioning engine is stable and battle-tested against production OpenStack deployments. Active development continues — new resource types, better ergonomics, sharper safety nets — but the foundation you build on today won't shift under you.

TenantCtl is designed for dual use: run it as a **standalone CLI** for day-to-day operations, or **import it as a Python library** to embed provisioning in your own tooling. The [`ConfigSource`](docs/API-REFERENCE.md) protocol means configuration can come from anywhere — YAML files today, a REST API or database tomorrow — without touching the provisioning logic.

---

You've done this before. Create the project. Build the network. Wire the router. Set quotas across four services. Allocate floating IPs. Lock down security groups. Configure federation. Now do it nine more times. Now do it again next month when someone "just needs one thing."

Stop.

A new project is **two lines of config** - a name, a prefix, and optionally a CIDR for the network. Everything else inherits from shared defaults. The entire stack provisions in **a couple of seconds** with direct API calls — no SSH, no inventory, no playbook compilation, no plan/apply ceremony. Where Ansible needs dozens of tasks and minutes of SSH overhead per project, this finishes before the kettle boils.

No Jinja templates to debug. No HCL. No state file to babysit - lose it and the next run rediscovers everything from the API. Just YAML you already know how to write.

Run it again tomorrow: zero drift. Run it after someone "fixes" something by hand: it snaps back. Put it in a CI or even a cron job and your projects stay correct while you sleep. Every run reproducible ❄️

---

## Before & After

**Without this tool** — a dozen manual steps per project:

> Create project. Create network. Create subnet with CIDR and pools. Create router, attach external gateway. Set compute quotas. Set network quotas. Set storage quotas. Set LB quotas. Allocate floating IPs. Create security groups. Assign group roles. Configure federation mapping. *Repeat for every project. Repeat next week when someone changes something.*

**With this tool** one file, one command:

```yaml
# config/projects/dev.yaml
name: dev
resource_prefix: dev

network:
  subnet:
    cidr: 192.168.30.0/24

quotas:
  compute:
    cores: 16
    ram: 32768
  network:
    floating_ips: 1

security_group:
  rules:
    - ICMP
    - SSH
    - HTTP
    - HTTPS
```

```bash
tenantctl --project dev -v
```

```
  [CREATED] project: dev
  [CREATED] network: dev-network
  [CREATED] subnet: dev-subnet
  [CREATED] router: dev-router
  [CREATED] quotas: dev
  [CREATED] security_group: default
  [CREATED] security_group_rule: default (Allow ICMP)
  [CREATED] security_group_rule: default (Allow SSH)
  [CREATED] security_group_rule: default (Allow HTTP)
  [SKIPPED] federation_mapping: my-mapping (no changes)

9 created, 0 updated, 0 deleted, 1 skipped, 0 failed
```

Run it again — nothing changes:

```
0 created, 0 updated, 0 deleted, 10 skipped, 0 failed
```

---

## Why This Tool

| | |
|---|---|
| **Idempotent** | Safe to run repeatedly — only makes necessary changes |
| **Declarative** | Define what you want in YAML, the tool figures out the rest |
| **Inheritable** | Projects inherit shared defaults, override only what's different |
| **Auditable** | Every action logged - created, updated, skipped, or failed |
| **Resilient** | Automatic retry with backoff; one project's failure doesn't block others |
| **Safe** | Dry-run mode, fail-fast validation, quota safety nets, teardown guards |

---

## What It Provisions

| Resource | What Happens |
|----------|-------------|
| **Projects** | Create or update tenants with domain resolution |
| **Network stacks** | Full stack - network, subnet, router with external gateway |
| **Quotas** | Compute, network, block storage, and load balancer quotas |
| **Floating IPs** | Pre-allocate a fixed pool with drift detection and reconciliation |
| **Security groups** | Baseline rules from presets (SSH, HTTP, HTTPS, ICMP) or custom |
| **Group roles** | Keystone group-to-project role grants and revocations |
| **Federation** | SAML/OIDC identity mapping with deterministic rule ordering |
| **Lifecycle** | `present` / `locked` / `absent` state machine with shelve/unshelve |
| **Teardown** | Safety-checked removal - refuses if VMs or volumes still exist |

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/Underknowlege/openstack-tenantctl.git
cd openstack-tenantctl
make install

# Set OpenStack credentials (option A: environment variables)
export OS_AUTH_URL=https://openstack.example.com:5000/v3
export OS_PROJECT_NAME=admin
export OS_USERNAME=admin
export OS_PASSWORD=your-password
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default

# Or use a named cloud from clouds.yaml (option B)
# tenantctl --os-cloud mycloud -v

# Preview changes (live cloud reads, field-level diffs)
.venv/bin/tenantctl --dry-run -v

# Preview changes (offline, no cloud connection)
.venv/bin/tenantctl --dry-run --offline -v

# Provision
.venv/bin/tenantctl -v
```

---

## Usage

```
tenantctl [OPTIONS]

Options:
  --version              Show version and exit
  --config-dir PATH      Configuration directory (default: config/)
  --os-cloud NAME        Named cloud from clouds.yaml
  --project NAME         Provision only the specified project
  --dry-run              Preview actions without making changes (connects to cloud for live diffs)
  --offline              Skip cloud connection in dry-run (use with --dry-run)
  -v, --verbose          Increase verbosity (-v=INFO, -vv=DEBUG)
  --help                 Show help message
```

---

## Configuration

Projects inherit shared settings from `defaults.yaml` and override only what's different:

```yaml
# config/projects/production.yaml
name: production
resource_prefix: prod

network:
  mtu: 9000
  subnet:
    cidr: 10.1.0.0/24
    gateway_ip: 10.1.0.254
    dns_nameservers: [8.8.8.8, 8.8.4.4]

quotas:
  compute:
    cores: 64
    ram: 131072
  network:
    floating_ips: 2
  block_storage:
    gigabytes: 2000

security_group:
  rules:
    - rule: ICMP
    - rule: SSH
      remote_ip_prefix: "10.0.0.0/8"
    - rule: HTTP
    - HTTPS      # both valid
```

Everything not specified, DHCP, DNS defaults, base quotas, federation mapping, can come from `defaults.yaml` automatically.

---

## Documentation

| Document | What's Inside |
|----------|--------------|
| [Full Documentation](docs/README.md) | Complete docs hub with role-based reading guides |
| [User Guide](docs/USER-GUIDE.md) | Setup, operations, troubleshooting |
| [Configuration Schema](docs/CONFIG-SCHEMA.md) | Every field, every option, with examples |
| [Specification](docs/SPECIFICATION.md) | Architecture, design patterns, internals |
| [Design Decisions](docs/DESIGN-DECISIONS.md) | 20 ADRs explaining the "why" |
| [API Reference](docs/API-REFERENCE.md) | Developer API for extending the tool |

---

## Development

```bash
make fmt          # Format code (ruff format + ruff check --fix)
make lint         # Lint (ruff check + mypy --strict)
make test         # Run tests (pytest)

make version      # Show current version
make bump-patch   # 0.2.8 → 0.2.9
make bump-minor   # 0.2.8 → 0.3.0
make bump-major   # 0.2.8 → 1.0.0
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow and guidelines.

---

## Requirements

- **Python** 3.11+ 
- **openstacksdk** 4.x, **pyyaml**, **deepmerge**, **tenacity**

---

## Related Projects

| Tool | What It Does |
|------|-------------|
| [Terraform OpenStack Provider](https://registry.terraform.io/providers/terraform-provider-openstack/openstack) | General-purpose IaC for OpenStack resources. Good for managing infrastructure *within* a project — VMs, volumes, load balancers |
| [Pulumi OpenStack Provider](https://www.pulumi.com/registry/packages/openstack/) | Same scope as Terraform but using real programming languages (Python<!--some say it's not a real programming language-->, TypeScript, Go) |
| [Ansible OpenStack Collection](https://github.com/openstack/ansible-collections-openstack) (`openstack.cloud`) | Per-resource Ansible modules for projects, quotas, networks, identity. Flexible, but you write the orchestration playbooks yourself |
| [OpenStack Adjutant](https://docs.openstack.org/adjutant/latest/) | Self-service project request and approval workflow — users request projects, admins approve them via API. A server-side OpenStack service that handles signup and role management, but assumes local users and has no support for federated identity |
| [stackHPC/openstack-config](https://github.com/stackhpc/openstack-config) | Ansible playbooks for cloud admin configuration — projects, networks, quotas, security groups, flavors, images, host aggregates, volume types, and Magnum cluster templates. Designed for single-cloud setup; multi-tenancy requires shell scripting around it since configuration lives in one monolithic definition file per cloud |


## Why Not Ansible?

Ansible has OpenStack modules (`openstack.cloud` collection), but the orchestration is yours to figure out. Here's where it broke down for me:

**Multi-project workflows** force you to iterate over dictionaries with project-specific variables. Sure, Ansible can do it, Ansible can do anything if you want it hard enough. But the moment you resort to `shell: openstack ...` commands because modules don't cover your use case, it's time to admit you're writing a worse version of a bash script with extra YAML ceremony.

**Execution time** stretches into minutes for operations that should take seconds. You iterate through projects one after another, ten projects means running the entire playbook or at least tasks ten times sequentially. One project hits an error? The whole run stops unless you've wrapped everything in rescue blocks. Direct API calls skip all of this.

**Federation mapping aggregation** turns into Jinja2 archaeology. You need double references just to support a for-loop lookup, or you cram everything into one massive dict object. Either way, the template grows until nobody can confidently say what it does. Onboarding a colleague means watching them squint at nested loops until they quietly back away.
<!--The role-to-project relationships get particularly uncomfortable when projects have different federation requirements.-->

Ansible is excellent for configuration management. But when you're writing shell tasks because the native modules don't cut it, that's your sign to stop and reach for a purpose-built tool, or build one.

## Why Not Terraform / Pulumi?

Terraform and Pulumi are excellent for managing infrastructure *within* a project. They're the wrong tool for provisioning the projects themselves:

- **Quotas** always exist - you set values, never "create" them. Terraform's resource lifecycle doesn't fit.
- **Federation** is a shared aggregation - one Keystone mapping built from *all* projects. Terraform has no cross-stack primitives.
- **FIPs** need drift reconciliation (adopt untracked, reclaim missing), not create/destroy cycles.
- **Teardown** needs safety gates - refuse deletion if VMs exist. `terraform destroy` can't check resources it doesn't manage.
- **State loss** is recoverable here (re-discover via API). Terraform requires manual imports.

Wrapping this in a custom provider would just reimplement the tool with Terraform as an unnecessary intermediary.


---

## License

Apache 2.0 — See [LICENSE](LICENSE) for details and [CHANGELOG.md](CHANGELOG.md) for release history.
