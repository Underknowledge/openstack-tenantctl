# CI/CD Pipeline

## Table of Contents

1. [Overview](#overview)
2. [CI Workflow](#ci-workflow-ciyml)
3. [Cloud Enforcement Sample](#cloud-enforcement-sample-cloud-integration-testymlsample)
4. [Local Development](#local-development)
5. [Using the Sample Workflow](#using-the-sample-workflow)
6. [Security](#security)
7. [Troubleshooting](#troubleshooting)

---

## Overview

The OpenStack TenantCtl uses GitHub Actions with two workflow files:

| File | Path | Purpose |
|------|------|---------|
| CI | `.github/workflows/ci.yml` | Lint, type-check, and test on every push/PR |
| Cloud Enforcement | `.github/workflows/cloud-integration-test.yml.sample` | Sample workflow that provisions and drift-checks a live cloud |

```
┌──────────────────────────────────────────────────┐
│              Push / Pull Request                 │
└──────────────┬───────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────┐
│  CI Workflow (ci.yml)                            │
│  ├─ Checkout                                     │
│  ├─ Setup Python 3.12 + venv cache               │
│  ├─ make fmt   (ruff format + ruff check --fix)  │
│  ├─ make lint  (ruff check + mypy)               │
│  └─ make test  (pytest)                          │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│  Cloud Enforcement (sample, opt-in)              │
│  Triggers: push to main, every 3h, manual        │
│  ├─ Install provisioner + openstackclient        │
│  ├─ Verify connectivity (openstack token issue)  │
│  ├─ Provision (tenantctl)                        │
│  ├─ Idempotency check (run again)                │
│  └─ Upload logs as artifact                      │
└──────────────────────────────────────────────────┘
```

---

## CI Workflow (`ci.yml`)

A single-job workflow that runs on every push and pull request.

**Triggers**: `push`, `pull_request`

**Concurrency**: Groups runs by branch (`ci-${{ github.ref }}`), cancels in-progress runs when a new commit is pushed.

**Permissions**: Read-only (`contents: read`).

**Steps**:

| # | Step | Details |
|---|------|---------|
| 1 | Checkout | `actions/checkout@v4` |
| 2 | Setup Python | `actions/setup-python@v5` with Python 3.12 |
| 3 | Cache venv | `actions/cache@v4` — key based on `pyproject.toml` hash |
| 4 | Install | Creates `.venv` if missing, runs `pip install -e ".[dev]"` |
| 5 | `make fmt` | `ruff format` + `ruff check --fix` on `src/` and `tests/` |
| 6 | `make lint` | `ruff check` + `mypy` on `src/` and `tests/` |
| 7 | `make test` | `pytest -v tests/` |

All steps must pass for the run to succeed.

---

## Cloud Enforcement Sample (`cloud-integration-test.yml.sample`)

A sample workflow named **"Enforce Cloud State"** that provisions an OpenStack cloud and verifies idempotency. The `.sample` extension means it will **not** run automatically — you must copy and rename it to activate it.

**Triggers** (when activated):

| Trigger | Condition |
|---------|-----------|
| `push` | Merges to `main` |
| `schedule` | Every 3 hours (`0 */3 * * *`) |
| `workflow_dispatch` | Manual trigger from Actions tab |

**Concurrency**: Group `cloud-provision`, `cancel-in-progress: false` — queued runs wait instead of cancelling a provisioning run mid-flight.

**Job**: `provision` (timeout: 30 minutes)

**Required secrets** (set as environment variables):

| Secret | Description |
|--------|-------------|
| `OS_AUTH_URL` | OpenStack Identity endpoint (e.g. `https://cloud.example.com:5000/v3`) |
| `OS_USERNAME` | Service account username |
| `OS_PASSWORD` | Service account password |
| `OS_PROJECT_NAME` | Target project |
| `OS_PROJECT_DOMAIN_NAME` | Project domain (e.g. `Default`) |
| `OS_USER_DOMAIN_NAME` | User domain (e.g. `Default`) |
| `OS_REGION_NAME` | Region (e.g. `RegionOne`) |

Additionally, the workflow hardcodes `OS_IDENTITY_API_VERSION: "3"` and `OS_INTERFACE: public`.

**Steps**:

| # | Step | Details |
|---|------|---------|
| 1 | Checkout | `actions/checkout@v4` |
| 2 | Setup Python | `actions/setup-python@v5` with Python 3.12 and pip caching |
| 3 | Install | `pip install -e . python-openstackclient` |
| 4 | Verify connectivity | `openstack token issue` — fails fast if credentials are wrong |
| 5 | Create logs dir | `mkdir -p logs` |
| 6 | Provision | `tenantctl --config-dir config -v`, output teed to `logs/provision.log` |
| 7 | Idempotency check | Same command again, output teed to `logs/idempotency.log` — should report no changes |
| 8 | Upload logs | `actions/upload-artifact@v4`, retained for 14 days, runs even on failure (`if: always()`) |

---

## Local Development

### Makefile Targets

```bash
make install   # Create .venv and pip install -e ".[dev]"
make fmt       # ruff format + ruff check --fix (auto-fix)
make lint      # ruff check + mypy (read-only checks)
make test      # pytest -v tests/
```

### Standard Dev Loop

```bash
# Create a branch
git checkout -b feature/my-changes

# Make changes, then validate
make fmt
make lint
make test

# Commit and push
git add <files>
git commit -m "feat: Description of change"
git push origin feature/my-changes
# Open a PR — CI runs automatically
```

### Version Management

The Makefile also provides version bump targets (all run `fmt`, `lint`, `test` first):

```bash
make bump-patch    # Bug fixes, no API changes
make bump-minor    # New features, backward compatible
make bump-major    # Breaking changes
make bump-dry-run  # Preview what would change
make bump-revert   # Undo the last version bump
make version       # Print current version
```

---

## Using the Sample Workflow

### Step 1: Copy and Rename

```bash
cp .github/workflows/cloud-integration-test.yml.sample \
   .github/workflows/cloud-integration-test.yml
```

### Step 2: Configure GitHub Secrets

Go to **Settings > Secrets and variables > Actions > New repository secret** and add:

- `OS_AUTH_URL`
- `OS_USERNAME`
- `OS_PASSWORD`
- `OS_PROJECT_NAME`
- `OS_PROJECT_DOMAIN_NAME`
- `OS_USER_DOMAIN_NAME`
- `OS_REGION_NAME`

Use a dedicated service account — not personal credentials.

### Step 3: Customize

Edit the copied workflow to match your environment:

- Change `--config-dir config` to point at your config directory
- Adjust the schedule cron expression if every 3 hours is too frequent
- Add or remove triggers as needed

### Step 4: Test

1. Push the workflow file to your repository
2. Go to **Actions > Enforce Cloud State > Run workflow** (manual dispatch)
3. Watch the run and check the uploaded logs artifact
4. Verify the idempotency check reports no changes on the second run

---

## Security

### Credential Management

**Do**:
- Store all OpenStack credentials in GitHub Secrets (never in code)
- Use a dedicated service account with minimum required permissions
- Use OpenStack application credentials when available
- Rotate credentials regularly

**Don't**:
- Commit `clouds.yaml` or any credential file to the repository
- Use personal accounts for CI automation
- Re-use credentials across unrelated environments

### How the Sample Workflow Uses Credentials

The sample workflow injects secrets as environment variables (`OS_AUTH_URL`, etc.) at the job level. The provisioner and `python-openstackclient` read these variables directly — no `clouds.yaml` file is needed in CI.

For local development, use either environment variables or a `clouds.yaml` file with the `--os-cloud` flag:

```bash
# Option A: environment variables
export OS_AUTH_URL=https://cloud.example.com:5000/v3
export OS_USERNAME=myuser
# ... etc
tenantctl --config-dir config --dry-run -v

# Option B: clouds.yaml
tenantctl --config-dir config --os-cloud mycloud --dry-run -v
```

---

## Troubleshooting

### CI Fails: Linting or Formatting Errors

```bash
# Auto-fix locally
make fmt

# Verify
make lint

# Commit the fix
git add -u
git commit -m "fix: Resolve lint errors"
git push
```

### CI Fails: Test Failures

```bash
# Run tests locally
make test

# Run a specific test for faster iteration
.venv/bin/pytest -v tests/test_specific.py

# Fix, then push
```

### Cloud Enforcement: Authentication Failed

1. Verify secrets are set correctly in GitHub (Settings > Secrets)
2. Test credentials locally:
   ```bash
   export OS_AUTH_URL=...
   export OS_USERNAME=...
   export OS_PASSWORD=...
   export OS_PROJECT_NAME=...
   export OS_PROJECT_DOMAIN_NAME=...
   export OS_USER_DOMAIN_NAME=...
   export OS_IDENTITY_API_VERSION=3
   openstack token issue
   ```
3. Check that `OS_AUTH_URL` includes the path (e.g. `/v3`)

### Cloud Enforcement: Idempotency Check Shows Changes

The second provisioner run should report all resources as `SKIPPED`. If it reports `CREATED` or `UPDATED`:

- An external process may have modified resources between runs
- There may be a bug in the provisioner's idempotency logic — check the uploaded logs and open an issue

### Cloud Enforcement: Timeout (30 min)

- The job has a 30-minute timeout. If provisioning takes longer, increase `timeout-minutes` in the workflow file
- Check if the OpenStack API is responding slowly (`openstack token issue` in the logs should complete quickly)

---

## CLI Reference

The provisioner supports these flags (from `src/main.py`):

```
tenantctl [OPTIONS]

  --config-dir DIR   Path to config directory (default: config/)
  --os-cloud NAME    Named cloud from clouds.yaml
  --project NAME     Filter to a single project
  --dry-run          Preview changes without applying
  --offline          Skip OpenStack connection (only with --dry-run)
  -v, --verbose      Increase verbosity (-v=INFO, -vv=DEBUG)
  --version          Show version and exit
```

---

## Further Reading

- [docs/USER-GUIDE.md](../docs/USER-GUIDE.md) — Configuration format and usage
- [GitHub Actions Documentation](https://docs.github.com/en/actions) — Workflow syntax reference
