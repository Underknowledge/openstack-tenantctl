VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
PYTEST := $(VENV)/bin/pytest

.PHONY: fmt lint test install

install: $(VENV)
	$(PIP) install -e ".[dev]"

$(VENV):
	python -m venv $(VENV)

fmt:
	$(VENV)/bin/black src/ tests/
	$(RUFF) check --fix src/ tests/

lint:
	$(RUFF) check src/ tests/
	$(MYPY) src/

test:
	$(PYTEST) -v tests/

# Version management
.PHONY: bump-patch bump-minor bump-major bump-dry-run bump-revert reinstall version

bump-patch: fmt lint test
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Bumping patch version (bug fixes, no API changes)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	$(VENV)/bin/bump-my-version bump patch
	@$(MAKE) reinstall
	@echo ""
	@echo "Version bumped to: $$($(PYTHON) -c 'from src import __version__; print(__version__)')"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Edit CHANGELOG.md and add a new version section at the top:"
	@echo "     ## [$$($(PYTHON) -c 'from src import __version__; print(__version__)')] - $$(date +%Y-%m-%d)"
	@echo ""
	@echo "  2. Amend the version bump commit:"
	@echo "     git add CHANGELOG.md"
	@echo "     git commit --amend --no-edit"
	@echo ""
	@echo "  3. Push changes and tags:"
	@echo "     git push && git push --tags"
	@echo ""

bump-minor: fmt lint test
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Bumping minor version (new features, backward compatible)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	$(VENV)/bin/bump-my-version bump minor
	@$(MAKE) reinstall
	@echo ""
	@echo "Version bumped to: $$($(PYTHON) -c 'from src import __version__; print(__version__)')"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Edit CHANGELOG.md and add a new version section at the top:"
	@echo "     ## [$$($(PYTHON) -c 'from src import __version__; print(__version__)')] - $$(date +%Y-%m-%d)"
	@echo ""
	@echo "  2. Amend the version bump commit:"
	@echo "     git add CHANGELOG.md"
	@echo "     git commit --amend --no-edit"
	@echo ""
	@echo "  3. Push changes and tags:"
	@echo "     git push && git push --tags"
	@echo ""

bump-major: fmt lint test
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Bumping major version (breaking changes)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	$(VENV)/bin/bump-my-version bump major
	@$(MAKE) reinstall
	@echo ""
	@echo "Version bumped to: $$($(PYTHON) -c 'from src import __version__; print(__version__)')"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Edit CHANGELOG.md and add a new version section at the top:"
	@echo "     ## [$$($(PYTHON) -c 'from src import __version__; print(__version__)')] - $$(date +%Y-%m-%d)"
	@echo ""
	@echo "  2. Amend the version bump commit:"
	@echo "     git add CHANGELOG.md"
	@echo "     git commit --amend --no-edit"
	@echo ""
	@echo "  3. Push changes and tags:"
	@echo "     git push && git push --tags"
	@echo ""

bump-dry-run:
	@echo "Previewing version bump (dry-run)..."
	@echo ""
	$(VENV)/bin/bump-my-version bump --dry-run --verbose patch

bump-revert:
	@echo "Reverting last version bump..."
	@echo ""
	@LAST_TAG=$$(git describe --tags --abbrev=0 2>/dev/null || echo "none"); \
	if [ "$$LAST_TAG" = "none" ]; then \
		echo "No tags found - nothing to revert"; \
		exit 1; \
	fi; \
	LAST_COMMIT_MSG=$$(git log -1 --pretty=%B); \
	if echo "$$LAST_COMMIT_MSG" | grep -q "^Bump version:"; then \
		echo "Deleting tag: $$LAST_TAG"; \
		git tag -d $$LAST_TAG; \
		echo "Resetting commit: $$LAST_COMMIT_MSG"; \
		git reset --hard HEAD~1; \
		echo "Reinstalling package to update version metadata..."; \
		$(MAKE) reinstall; \
		echo ""; \
		echo "Version bump reverted successfully"; \
		echo ""; \
		echo "Current version: $$($(PYTHON) -c 'from src import __version__; print(__version__)')"; \
	else \
		echo "Last commit is not a version bump commit"; \
		echo "   Last commit: $$LAST_COMMIT_MSG"; \
		echo ""; \
		echo "Aborting - manual intervention required"; \
		exit 1; \
	fi

reinstall:
	@echo "Reinstalling package..."
	@$(PIP) install -e ".[dev]" > /dev/null

version:
	@$(PYTHON) -c "from src import __version__; print(__version__)"
