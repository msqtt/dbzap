# AGENTS.md - AI Agent Development Guide

## Project Overview

**dbzap**: Python-based instant API application that connects to databases, reads DDL automatically, and generates CRUD APIs for all tables. Supports both REST API and GraphQL API, with built-in Auth.

## Mandatory Rules

All agents MUST follow these rules. Violations are blockers.

### 1. Strict Development Order: SDD → TDD → Implementation

Every feature, enhancement, bug fix, or behavioral change MUST follow this exact order. **No exceptions.**

```
Phase 1: SPEC    — Write or update specs/<feature>.md first
Phase 2: TEST    — Write or update tests based on the spec
Phase 3: SOURCE  — Implement source code to pass the tests
```

**Phase 1 — Spec (gate: spec file committed/updated)**
- Before writing ANY code or test, read or create a Spec file under `specs/`.
- Spec files use Markdown format: `specs/<feature-name>.md`.
- A Spec must contain: goal, scope, API contract, data model, edge cases, and acceptance criteria.
- If no Spec exists, create one. If the change modifies existing behavior, update the existing Spec.
- Never skip Spec. No Spec = no work proceeds.

**Phase 2 — Tests (gate: failing tests exist that cover the spec)**
- Write test cases that cover every acceptance criterion in the Spec.
- Tests MUST fail initially (red) before implementation begins.
- Test files live in `tests/test_<module>.py`.
- Minimum coverage target: core modules 90%, utilities 70%.

**Phase 3 — Implementation (gate: all tests green)**
- Implement source code only after failing tests exist.
- Run `pytest` — all tests must pass (green) before considering the task done.
- Run `mypy` — no type errors.
- Do not add functionality beyond what the Spec defines.

**Anti-patterns (NEVER do these):**
- Writing source code first and adding tests/specs after the fact.
- Modifying source code without first updating the Spec if behavior changes.
- Skipping Spec for "small changes" — even bug fixes need a one-line Spec update.
- Writing tests that pass immediately without first verifying they would fail against unimplemented code.

### 2. Security Baseline

- NEVER hardcode API keys, passwords, or secrets in any file.
- All secrets come from environment variables or `.env` (which is gitignored).
- Sanitize all user inputs at API boundaries.
- SQL queries must use parameterized queries only - no string concatenation.

## Project Structure

```
dbzap/
  AGENTS.md
  pyproject.toml          # Poetry-managed
  poetry.lock
  .env.example
  specs/                  # Feature specifications
  src/
    dbzap/
      __init__.py
      core/               # DB connection, DDL parser, schema introspection
      generators/         # REST and GraphQL API generators
      auth/               # Authentication and authorization
      server/             # ASGI server, routing, middleware
        static/           # API Explorer frontend (HTML/CSS/JS)
  tests/
    conftest.py
    test_core/
    test_generators/
    test_auth/
    test_server/
```

## Tech Stack

| Layer          | Choice                  | Rationale                        |
| -------------- | ----------------------- | -------------------------------- |
| Language       | Python 3.11+            | Async support, type hints        |
| ASGI Framework | FastAPI                 | REST API with auto-docs          |
| GraphQL        | Strawberry              | Type-safe, dataclass-based       |
| ORM / DB       | SQLAlchemy 2.0 (async)  | DDL introspection, async queries |
| Auth           | JWT (python-jose)       | Stateless, standard              |
| Testing        | pytest + pytest-asyncio | Async test support               |
| Config         | pydantic-settings       | Type-safe env-based config       |

## Dependency Management

- **Poetry** is the sole dependency manager. Never use `pip install` directly.
- Add deps: `poetry add <package>`
- Add dev deps: `poetry add --group dev <package>`
- Install: `poetry install`
- Run commands: `poetry run <command>`
- Always commit `poetry.lock` - it ensures reproducible builds.
- Pin major versions in `pyproject.toml` for stability.

## Development Workflow

Every task follows this exact sequence. Do not skip or reorder steps.

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1: SPEC                                          │
│  1. Read relevant existing specs in specs/              │
│  2. Create new spec OR update existing spec             │
│  3. Define: goal, scope, API contract, edge cases,      │
│     acceptance criteria                                 │
│  GATE: Spec file is complete and saved                  │
├─────────────────────────────────────────────────────────┤
│  Phase 2: TEST                                          │
│  4. Write tests covering every acceptance criterion     │
│  5. Run pytest — confirm tests FAIL (red)               │
│  GATE: Failing tests exist, no source changes yet       │
├─────────────────────────────────────────────────────────┤
│  Phase 3: IMPLEMENT                                     │
│  6. Write source code to make tests pass                │
│  7. Run pytest — all tests PASS (green)                 │
│  8. Run mypy — no type errors                           │
│  GATE: All green, spec acceptance criteria checked off  │
├─────────────────────────────────────────────────────────┤
│  Phase 4: VERIFY                                        │
│  9. Re-read spec — confirm all criteria are met         │
│  10. Check no unintended side effects                   │
│  DONE                                                   │
└─────────────────────────────────────────────────────────┘
```

### Quick reference

| Change type         | Spec action                        | Test action                     |
| ------------------- | ---------------------------------- | ------------------------------- |
| New feature         | Create `specs/<feature>.md`        | Add new test class/functions    |
| Enhancement         | Update existing spec sections      | Add tests for new behavior      |
| Bug fix             | Update spec edge cases / contract  | Add regression test first       |
| Refactor            | Update spec if API changes         | Ensure existing tests still pass|
| Frontend-only UI    | Update spec UI contract section    | Update explorer test fixtures   |

## Code Conventions

- **Type hints**: Required on all public functions and classes.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes.
- **Imports**: Use absolute imports from `dbzap.*`.
- **Error handling**: Raise typed exceptions, catch at boundaries (API handlers).
- **Async**: Prefer `async/await` for all I/O operations.
- **Logging**: Use `structlog`, never `print()`.

## Spec File Template

```markdown
# Feature: <name>

## Goal
One sentence describing what this feature does.

## Scope
- In scope: ...
- Out of scope: ...

## API Contract
Endpoint / schema definition.

## Data Model
Tables, columns, relationships.

## Edge Cases
- ...

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
```

## Agent-Specific Instructions

### Feature Agent

1. **SPEC**: Read relevant Specs in `specs/`. If none matches, create one using the template below. Do not proceed until the Spec is complete.
2. **TEST**: Write failing tests that cover every acceptance criterion. Run `pytest` and confirm they fail (red).
3. **IMPLEMENT**: Write source code until all tests pass (green). Do not modify files outside the feature scope.
4. **VERIFY**: Re-read the Spec. Confirm every acceptance criterion is satisfied by a passing test.

### Refactor Agent

1. **SPEC**: Update the Spec if any public API or behavior changes.
2. **TEST**: Ensure existing tests pass. Add new tests if refactoring changes observable behavior.
3. **IMPLEMENT**: Refactor source code. Run `pytest` — all tests must pass before AND after.
4. **VERIFY**: Confirm no public APIs changed unless the Spec required it.

### Bug Fix Agent

1. **SPEC**: Update the Spec's edge cases or API contract to document the correct behavior.
2. **TEST**: Write a regression test that reproduces the bug. Confirm it fails (red).
3. **IMPLEMENT**: Fix the code to make the regression test pass.
4. **VERIFY**: Run full test suite — no regressions in existing tests.

### Review Agent

- Check SDD compliance: Does the Spec exist? Is it up to date? Does it match the implementation?
- Check TDD compliance: Do tests exist for every acceptance criterion? Were they written before source?
- Check security baseline: Flag any hardcoded secrets as critical.
- Verify type hints on all public interfaces.

## Database Introspection Rules

- Read DDL from the connected database at startup - never assume schema.
- Cache introspected schema; refresh on explicit reload signal only.
- Map SQL types to Python types using a defined type-mapping table.
- Respect database constraints (NOT NULL, UNIQUE, FK) in generated APIs.

## Auth Rules

- All endpoints require auth by default.
- Public endpoints must be explicitly declared in config.
- JWT tokens expire after a configurable TTL (default: 1h).
- Password hashing: bcrypt only, never MD5/SHA.
