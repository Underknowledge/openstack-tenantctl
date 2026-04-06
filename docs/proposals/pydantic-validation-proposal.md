# Pydantic-Based Config Validation

> **STATUS: EVALUATED - FROZEN DATACLASSES ADOPTED INSTEAD**
>
> This document records the evaluation of replacing manual `_validate_project()`
> validation with Pydantic v2 schema-based validation. After analysis, Pydantic
> was **not adopted**. The codebase subsequently migrated to **frozen dataclasses
> with distributed `validate()` classmethods**, achieving type safety and
> structured validation without the Pydantic dependency.
>
> - **Evaluated**: 2026-04-04
> - **Decision**: Keep manual validation (Pydantic rejected)
> - **Outcome**: Frozen dataclasses adopted instead — see `src/models/` package
> - **Rationale**: Dataclass migration achieved the key benefits (typed attribute access, IDE autocomplete, immutability) without adding a dependency

---

## Problem Statement

At the time of evaluation, `_validate_project()` in `src/config_loader.py` (~220 lines)
performed manual field-by-field validation using `isinstance()` checks, regex matches,
and `ipaddress` parsing. This pattern was verbose and required updating both the function
and tests whenever a new config field was added.

**What exists now**: The monolithic `_validate_project()` was removed from
`config_loader.py`. Validation now lives in two places:

- `src/config_validator.py` (61 lines) — entry point that delegates to model validators
  and performs cross-project checks (CIDR overlap detection)
- `src/models/` package — frozen dataclass hierarchy where each model has a `validate()`
  classmethod that validates its own fields and accumulates errors into a shared
  `list[str]`

---

## What We Evaluated

### Pydantic v2 Strengths

- **Eliminates ~220 lines of manual validation** — type checks become implicit from annotations
- **Zero new dependency** — already installed in the venv as a transitive dep of `bump-my-version`
- **Structured error messages** — `ValidationError.errors()` produces field paths (`network.subnet.cidr`) automatically
- **Custom validators** — `@field_validator` / `@model_validator` handle domain-specific checks (CIDR format, gateway-inside-subnet, quota non-negativity) cleanly
- **JSON Schema generation** — `model_json_schema()` produces a machine-readable schema for free
- **deepmerge compatible** — natural flow is `YAML → dict → deepmerge → model_validate(merged_dict)`; Pydantic validates after merge with no conflict

### Pydantic v2 Downsides (as evaluated)

The original evaluation noted these concerns. Annotations below show what actually happened:

- **Migration blast radius** — every resource module used `cfg["key"]` dict access; full migration would touch all files and 50+ tests
  - *Outcome*: This migration did happen — but to frozen dataclasses, not Pydantic. All modules now use `cfg.attribute` access on typed `ProjectConfig` objects.
- **Config mutation breaks** — `network.py` mutated config in-place (`cfg["router_ips"] = [...]`); Pydantic models are immutable by default
  - *Outcome*: Config mutation was eliminated. Runtime state (router IPs, FIPs) moved to `ctx.state_store.save()` (DD-018). Config objects are frozen dataclasses — immutable by design.
- **+200ms startup** — importing Pydantic + defining models adds ~200ms; negligible for a CLI tool but measurable
  - *Still valid*: Frozen dataclasses have zero import overhead beyond the stdlib.
- **Rust core opacity** — `pydantic-core` is compiled Rust; cannot step through in a Python debugger
  - *Still valid*: Dataclass validators are pure Python, fully debuggable.
- **Two-world maintenance** — unless migrated in one shot, some code uses attribute access, other code uses dict keys
  - *Outcome*: The full migration to attribute access has been completed. No dict-key access remains in resource modules.

### Alternatives Considered

| Library | Verdict |
|---------|---------|
| **attrs + cattrs** | Still requires manual validators; solves only the model definition part |
| **marshmallow** | Schema-as-separate-class doubles definitions; less modern |
| **TypedDict + typeguard** | Type checks only; domain validation still manual |
| **jsonschema** | Verbose JSON schemas; custom validators require manual extension |
| **dataclasses** | Chosen approach — frozen dataclasses with `validate()` classmethods provide type safety, immutability, and structured validation |

### Migration Strategies Evaluated

| Strategy | Risk | Effort | Benefit |
|----------|------|--------|---------|
| **Phase 1: Boundary-only** — validate at load, `model_dump()` back to dict | Low | Small | Better validation, zero downstream changes |
| **Phase 2: Gradual adoption** — models in some modules, dicts in others | Medium | Medium | Partial type safety, messy intermediate state |
| **Phase 3: Full migration** — models everywhere | High | Large | Full type safety, IDE autocomplete, mypy coverage |

*The codebase completed the equivalent of Phase 3 using frozen dataclasses.*

---

## Decision: Keep Current Approach (Pydantic Not Adopted)

### Why we did not adopt Pydantic

1. **Frozen dataclasses achieved the key benefits** — typed attribute access (`cfg.network.subnet.cidr`), IDE autocomplete, mypy coverage, and immutability — all without adding a dependency
2. **Config schema is stable** — we are not frequently adding new fields that would benefit from Pydantic's self-validating annotations over the current `validate()` classmethods
3. **Migration already completed** — the feared migration effort happened, but targeted dataclasses instead; there is no appetite for a second migration to Pydantic
4. **No remaining pain point** — the distributed `validate()` pattern is concise, testable, and covers all fields with good error messages

### When to reconsider

This decision should be revisited if:

- We need machine-readable JSON Schema generation for external tooling or editor integration (Pydantic's strongest remaining advantage)
- The number of config models grows to the point where hand-written `validate()` methods become a maintenance burden
- Pydantic becomes a direct dependency for another reason, eliminating the "extra dependency" downside

Note: The original triggers ("config complexity grows", "need for typed models", "major refactor needed") have already been addressed by the dataclass migration.

---

## What Was Done Instead

The codebase adopted **frozen dataclasses with distributed validators** in the `src/models/` package:

### Model hierarchy

```
ProjectConfig                          # src/models/project.py
├── NetworkConfig                      # src/models/network.py
│   └── SubnetConfig
│       └── AllocationPool
├── QuotaConfig                        # src/models/quotas.py
├── SecurityGroupConfig                # src/models/security.py
│   └── SecurityGroupRule
├── FederationConfig                   # src/models/federation.py
│   └── FederationRoleAssignment
└── GroupRoleAssignment                # src/models/access.py
```

### Design pattern

Each model provides two classmethods:

- **`from_dict(data)`** — creates an instance from a pre-validated dict (used after config loading when data is already trusted)
- **`validate(data, errors, label)`** — validates untrusted input, appends problems to `errors: list[str]`, and returns a constructed instance (or `None` for fatally broken data)

Validation errors accumulate across all nested models. At the end, `ConfigValidationError` is raised with the full list if any errors were found.

### Key properties

- **Immutable**: All models use `@dataclasses.dataclass(frozen=True)` — accidental config mutation is impossible
- **Typed attribute access**: Resource modules use `cfg.network.subnet.cidr` instead of `cfg["network"]["subnet"]["cidr"]`
- **No extra dependencies**: Only stdlib `dataclasses`, `ipaddress`, `re`
- **Fully debuggable**: Pure Python validators, no compiled extensions

---

## References

- Pydantic v2 docs: https://docs.pydantic.dev/latest/
- Current validation entry point: `src/config_validator.py`
- Typed config models: `src/models/` package (`project.py`, `network.py`, `quotas.py`, `security.py`, `federation.py`, `access.py`)
- Config schema docs: `docs/CONFIG-SCHEMA.md`
