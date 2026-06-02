# AGENTS.md - AI Agent Development Guide

## Project Overview

**dbzap**: Python-based instant API application that connects to databases, reads DDL automatically, and generates CRUD APIs for all tables. Supports both REST API and GraphQL API, with built-in Auth.

## Mandatory Rules

All agents MUST follow these rules. Violations are blockers.

### 1. Spec-Driven Development (SDD)

- Before writing ANY code, read or create a Spec file under `specs/`.
- Spec files use Markdown format: `specs/<feature-name>.md`.
- A Spec must contain: goal, scope, API contract, data model, edge cases, and acceptance criteria.
- Never skip Spec. If no Spec exists, create one and get confirmation before coding.

### 2. Test-Driven Development (TDD)

- For core modules, write test cases BEFORE implementation logic.
- Test files live alongside source: `tests/test_<module>.py`.
- Minimum coverage target: core modules 90%, utilities 70%.
- Run `pytest` and ensure all tests pass before considering a task done.

### 3. Security Baseline

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

```
1. Read/create Spec in specs/
2. Write tests in tests/
3. Implement in src/
4. Run pytest - all green
5. Run mypy - no errors
6. Done
```

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

- Start by reading relevant Specs in `specs/`.
- If no Spec matches, create one using the template above.
- Write failing tests first, then implement until tests pass.
- Do not modify files outside the feature scope without explicit permission.

### Refactor Agent

- Read existing tests before changing any code.
- Ensure all tests pass before AND after refactoring.
- Do not change public APIs unless the Spec explicitly requires it.

### Bug Fix Agent

- Reproduce the bug with a failing test first.
- Fix the code to make the test pass.
- Verify no regressions in existing tests.

### Review Agent

- Check: SDD compliance (Spec exists), TDD compliance (tests exist), security baseline.
- Flag any hardcoded secrets immediately as critical.
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
