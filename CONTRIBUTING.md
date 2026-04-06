# Contributing to OpenStack TenantCtl

Cool! Thanks for the interest in contributing back to the Provisioner!

## Table of Contents

1. [Getting Started](#getting-started)
2. [Development Workflow](#development-workflow)
3. [Git Workflow](#git-workflow)
4. [Code Standards](#code-standards)
5. [Testing](#testing)
6. [Documentation](#documentation)
7. [Security](#security)

---

## Getting Started

### Prerequisites

- Python 3.11 or later
- Git
- OpenStack admin credentials for testing (optional, can use mocking)
  - I can recommend [OpenStack a-universe-from-nothing](https://github.com/stackhpc/a-universe-from-nothing) if you have the patience.
  - OpenDev runs tests against [DevStack](https://docs.openstack.org/devstack/latest/) for CI/CD. 

### Setting Up Your Development Environment

```bash
# Clone the repository
git clone <repository-url>
cd openstack-tenantctl

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies
make install

# Verify installation
make lint
make test
```

---

## Development Workflow

> **Note for External Contributors:** I will just follow the established rules I work with daily. All contributions from external contributors have to go through feature branches and pull requests. Maintainers with commit access may push directly to `main`, but feature branches are recommended for larger changes.

### 1. Create a Feature Branch

**For external contributors** (required):
```bash
# Create feature branch
git checkout -b feat/your-feature-name
# or
git checkout -b fix/issue-123-description
```

### 2. Make Your Changes

Follow the [Code Standards](#code-standards) and add tests for new functionality.

### 3. Run Quality Checks

```bash
# Format code
make fmt

# Run linters
make lint

# Run tests
make test
```

**All checks must pass before committing.**

### 4. Commit Your Changes

```bash
# Stage your changes
git add src/resources/myfeature.py
git add tests/test_resources/test_myfeature.py

# Commit with descriptive message
git commit -m "Add support for resource X

- Implement ensure_resource_x() function
- Add comprehensive tests
- Update configuration schema documentation"
```

### 5. Push and Create Pull Request

**For external contributors:**
```bash
# Push to your fork
git push origin feat/your-feature-name

# Create pull request via GitHub web interface
# Point PR from your fork's feature branch to upstream's main branch
```

**For maintainers:**
```bash
# Option 1: Push directly to main (for quick fixes)
git checkout main
git push origin main

# Option 2: Use feature branch + PR (recommended for larger changes)
git push origin feat/your-feature-name
# Then create PR via GitHub web interface
```

---

## Version Management

### Bumping Version

Use Makefile targets for semantic versioning:

```bash
# Patch release (0.2.7 → 0.2.8) - Bug fixes
make bump-patch

# Minor release (0.2.7 → 0.3.0) - New features, backward compatible
make bump-minor

# Major release (0.2.7 → 1.0.0) - Breaking changes
make bump-major
```

This will:
1. Run quality checks (`make fmt lint test`) to ensure code is ready for release
2. Reinstall package to refresh metadata
3. Update version in `pyproject.toml`
4. Create a git commit
5. Create a git tag `vX.Y.Z`

**Note**: Version bumps will fail if tests don't pass or if there are linting errors.

### After Bumping

1. **Update CHANGELOG.md** - Add new version section at the top:
   ```markdown
   ## [0.2.8] - 2026-04-05

   ### Features
   - Added volume quota management
   - Improved error handling for missing services

   ### Bug Fixes
   - Fixed router IP allocation race condition

   ### Documentation
   - Updated configuration schema examples
   ```

   Follow the [Keep a Changelog](https://keepachangelog.com/) format with sections:
   - **Features** - New functionality
   - **Bug Fixes** - Bug fixes
   - **Documentation** - Documentation updates
   - **Code Refactoring** - Code improvements without behavior changes
   - **Tests** - Test additions/improvements
   - **Maintenance** - Dependency updates, tooling changes

2. **Amend the version bump commit** to include changelog:
   ```bash
   git add CHANGELOG.md
   git commit --amend --no-edit
   ```

3. Verify version:
   ```bash
   make version
   .venv/bin/tenantctl --version
   ```

4. Push changes and tags:
   ```bash
   git push && git push --tags
   ```

### Reverting a Version Bump

If you accidentally bumped the version, you can revert it:

```bash
make bump-revert
```

This will:
1. Verify the last commit is a version bump commit
2. Delete the most recent version tag
3. Reset to before the bump commit (restores all files)
4. Reinstall the package to update version metadata
5. Display the restored version

**Note**: Only works if the last commit is a version bump commit. The revert uses `git reset --hard HEAD~1`, so make sure you haven't made any other commits after the version bump.

### Semantic Versioning

Follow [semver.org](https://semver.org/):
- **Patch** (0.2.7 → 0.2.8): Bug fixes, no API changes
- **Minor** (0.2.7 → 0.3.0): New features, backward compatible
- **Major** (0.2.7 → 1.0.0): Breaking changes

---

## Git Workflow

### Branch Naming Convention

Use descriptive branch names with prefixes:

- `feat/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code refactoring
- `test/` - Test improvements
- `chore/` - Maintenance tasks

**Examples**:
- `feat/add-volume-management`
- `fix/quota-validation-edge-case`
- `docs/improve-configuration-examples`
- `refactor/simplify-retry-logic`

### Commit Messages

Write clear, descriptive commit messages following this format:

```
<type>: <short summary> (max 72 characters)

<optional detailed description>
- Key change 1
- Key change 2
- Key change 3

Closes #123
```

**Types**:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Formatting, no code change
- `refactor:` - Code restructuring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

**Good examples**:
```
feat: Add volume quota management

- Implement ensure_volume_quotas() function
- Add validation for volume-specific quota fields
- Include tests for all volume quota scenarios

Closes #45
```

```
fix: Handle missing external network gracefully

Previously, missing external network caused provisioning to fail.
Now logs warning and creates routers without external gateway.

Fixes #78
```

### What to Commit

**DO commit**:
- Source code (`src/**/*.py`)
- Tests (`tests/**/*.py`)
- Documentation (`docs/**/*.md`, `*.md`)
- Configuration examples (`config-sample/defaults.yaml`, `config-sample/projects/*.yaml`)
- Build configuration (`Makefile`, `pyproject.toml`)
- Git configuration (`.gitignore`, `.gitattributes`)

**DO NOT commit**:
- Virtual environment (`.venv/`, `venv/`)
- Python cache (`__pycache__/`, `*.pyc`)
- IDE files (`.vscode/`, `.idea/`)
- OpenStack credentials (`clouds.yaml`, `*.env`)
- Log files (`*.log`)
- Build artifacts (`dist/`, `*.egg-info/`)
- Lock files from development (`.pytest_cache/`, `.mypy_cache/`)

**NEVER commit**:
- Credentials or secrets
- API keys or tokens
- Personal OpenStack configuration
- Private or sensitive data

### Sensitive Files Protection

The `.gitignore` file is configured to prevent accidental commits of sensitive files:

```gitignore
# OpenStack credentials (NEVER commit these!)
clouds.yaml
secure.yaml
*.credentials
*.creds

# Environment files with secrets
.env.local
.env.*.local
*.env
```

**Before committing, always verify**:
```bash
# Check what will be committed
git status

# Review changes
git diff --cached

# Ensure no sensitive files
git diff --cached --name-only | grep -E "(clouds\.yaml|\.env|credentials)"
```

### Handling Configuration Files

**Example configurations** (tracked in Git):
- `config-sample/defaults.yaml` - Default configuration template
- `config-sample/projects/*.yaml` - Example project configurations

**Local configurations** (NOT tracked):
- Create your own `config/` directory for local testing
- Copy files from `config-sample/` as a starting point
- The `config/` directory is automatically ignored by `.gitignore`

### Pre-Commit Checks

Before committing, ensure:

```bash
# 1. Format code
make fmt

# 2. Lint code
make lint

# 3. Run tests
make test

# 4. Check for sensitive files
git status | grep -E "(clouds\.yaml|\.env|credentials)"

# 5. Review changes
git diff --cached
```

**All must be ✅ GREEN before committing.**

---

## Code Standards

### Python Style

We use **Ruff** for formatting and linting:

```bash
# Format code
ruff format src/ tests/

# Fix auto-fixable issues
ruff check --fix src/ tests/

# Check for remaining issues
ruff check src/ tests/
```

### Type Hints

Use type hints for all functions:

```python
from __future__ import annotations

from typing import Any

def ensure_resource(
    cfg: dict[str, Any],
    project_id: str,
    ctx: SharedContext,
) -> Action:
    """Ensure resource exists with correct configuration."""
    ...
```

### Docstrings

Use clear docstrings for all public functions:

```python
def ensure_resource(cfg: dict[str, Any], ctx: SharedContext) -> Action:
    """Ensure resource exists with correct configuration.

    Args:
        cfg: Project configuration dictionary.
        ctx: SharedContext with connection and shared state.

    Returns:
        Action recording what happened (CREATED/UPDATED/SKIPPED/FAILED).
    """
```

### Universal Resource Pattern

Follow the universal resource pattern for new resource modules:

```python
def ensure_resource(cfg, ctx, ...) -> Action:
    # 1. Extract configuration
    # 2. Dry-run check
    # 3. Find existing resource
    # 4. Create if missing
    # 5. Update if changed
    # 6. Skip if up to date
```

See [API-REFERENCE.md § Creating New Resource Types](docs/API-REFERENCE.md#10-creating-new-resource-types).

---

## Testing

### Writing Tests

All new features must include tests:

```python
# tests/test_resources/test_myresource.py
from src.resources.myresource import ensure_myresource

class TestEnsureMyResource:
    def test_creates_resource_when_missing(self, normal_ctx, mock_conn):
        """Test resource creation when it doesn't exist."""
        # Arrange
        cfg = {"name": "test", "resource_prefix": "test"}
        mock_conn.service.find_resource.return_value = None

        # Act
        action = ensure_myresource(cfg, "project-123", normal_ctx)

        # Assert
        assert action.status == ActionStatus.CREATED
        mock_conn.service.create_resource.assert_called_once()

    def test_updates_resource_when_changed(self, normal_ctx, mock_conn):
        """Test resource update when configuration differs."""
        # Test implementation
        pass

    def test_skips_when_up_to_date(self, normal_ctx, mock_conn):
        """Test skip when resource already matches config."""
        # Test implementation
        pass

    def test_dry_run_skips_all_operations(self, dry_run_ctx, mock_conn):
        """Test dry-run mode skips all operations."""
        # Test implementation
        pass
```

### Running Tests

```bash
# Run all tests
make test

# Run specific test file
pytest tests/test_resources/test_myresource.py

# Run specific test
pytest tests/test_resources/test_myresource.py::TestEnsureMyResource::test_creates_resource_when_missing

# Run with coverage
pytest --cov=src --cov-report=html
```

### Test Coverage

Aim for >80% test coverage for new code:

```bash
pytest --cov=src --cov-report=term-missing
```

---

## Documentation

### When to Update Documentation

Update documentation when:
- Adding new features
- Changing configuration schema
- Modifying behavior
- Making architectural decisions

### Documentation Files to Update

| Change Type | Files to Update |
|-------------|----------------|
| New configuration field | `docs/CONFIG-SCHEMA.md` |
| New resource type | `docs/SPECIFICATION.md`, `docs/API-REFERENCE.md` |
| Operational procedure | `docs/USER-GUIDE.md` |
| Architecture decision | `docs/DESIGN-DECISIONS.md` (add new ADR) |
| API change | `docs/API-REFERENCE.md` |

### Adding Architecture Decision Records (ADRs)

For significant design decisions, add an ADR to `docs/DESIGN-DECISIONS.md`:

```markdown
## DD-011: [Decision Title]

**Status**: Proposed/Accepted/Deprecated

### Context
[What problem are we solving?]

### Decision
[What we chose to do]

### Rationale
[Why we made this choice]

### Alternatives Considered
[What we didn't choose and why]

### Consequences
[Trade-offs and implications]
```

---

## Security

### Security-Sensitive Changes

If your change involves:
- Credential handling
- Authentication/authorization
- Data encryption
- Network security
- Input validation

Please:
1. Flag it in your pull request description
2. Request security review
3. Test edge cases thoroughly

### Reporting Security Issues

**DO NOT** open public issues for security vulnerabilities.

Instead:
- Use GitHub's private security advisory feature
- Or contact the project maintainers directly

---

## Pull Request Process

### Before Submitting

- ✅ All tests pass (`make test`)
- ✅ Linting passes (`make lint`)
- ✅ Code formatted (`make fmt`)
- ✅ Documentation updated
- ✅ No sensitive files committed
- ✅ Commit messages are clear

### Pull Request Description Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactoring
- [ ] Other (please describe)

## Changes Made
- Change 1
- Change 2
- Change 3

## Testing
Describe testing performed:
- Unit tests added/updated
- Manual testing performed
- Edge cases covered

## Documentation
- [ ] Updated relevant documentation
- [ ] Added/updated docstrings
- [ ] Updated CONFIG-SCHEMA.md (if applicable)
- [ ] Added ADR (if architectural change)

## Checklist
- [ ] My code follows the project style
- [ ] I have added tests for my changes
- [ ] All tests pass locally
- [ ] I have updated the documentation
- [ ] No sensitive files are committed

## Related Issues
Closes #123
Related to #456
```

### Review Process

1. Automated checks run (linting, tests)
2. Code review by maintainers
3. Address review comments
4. Approval and merge

---

## Getting Help

- **Documentation**: See `docs/` directory
- **Questions**: Open a GitHub Discussion
- **Issues**: Open a GitHub Issue
- **Security**: Use private security advisory feature

---

## Code of Conduct

Be respectful, professional, and inclusive. We're all here to build great software together.

---

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

---

**Thank you for contributing to OpenStack TenantCtl!** 🚀
