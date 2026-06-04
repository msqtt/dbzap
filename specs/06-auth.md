# Feature: Authentication & Authorization

## Goal
Provide two authentication modes for generated API endpoints: JWT (token-based, with expiry) and Basic Auth (credential-based, stateless, no expiry). Mode is selected via `.env`. Credentials are defined in `.env` — no registration endpoint is needed.

## Scope
- In scope: Single admin user seeded from `.env` at startup, JWT login + token validation, Basic Auth validation on every request, FastAPI dependency for route protection, password hashing (bcrypt), configurable auth mode (`jwt` | `basic` | `both`)
- Out of scope: User registration, OAuth2/OIDC, role-based access control (RBAC), API key auth, token refresh tokens (future), session management, multi-tenancy, user management UI

## API Contract

Auth endpoints (always public, regardless of auth mode):

```
POST /auth/login      -> Validate credentials, return JWT token (available in jwt/both mode)
GET  /auth/me         -> Return current authenticated user info
```

### POST /auth/login (JWT mode only)

Request:
```json
{
  "username": "admin",
  "password": "s3cureP@ss"
}
```

Response (200):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

Not available when `AUTH_MODE=basic` — returns 404.

### GET /auth/me

Headers (JWT): `Authorization: Bearer <token>`
Headers (Basic): `Authorization: Basic base64(username:password)`

Response (200):
```json
{
  "id": 1,
  "username": "admin"
}
```

### Route Protection

`get_current_user` dependency tries both auth methods in order:

1. **Bearer token** (if `auth_mode` is `jwt` or `both`): validate JWT, return user.
2. **Basic Auth** (if `auth_mode` is `basic` or `both`): decode `Authorization: Basic <cred>`, verify username/password against DB, return user.

If neither succeeds, return 401.

Protected endpoints:
- All generated CRUD routes (`/api/*`)
- `/openapi.json` (OpenAPI schema endpoint)
- `/auth/me`

Public endpoints (no auth required):
- `/auth/login`
- `/healthz`, `/healthz/ready`, `/healthz/detail`
- `/metrics`
- `/explorer`, `/explorer/config`, `/explorer/static/*`

Usage in generated routes:
```python
@router.get("/api/users")
async def list_users(user: UserRecord = Depends(get_current_user)):
    ...
```

## Configuration

| Env Var | Default | Description |
| ------- | ------- | ----------- |
| `AUTH_MODE` | `jwt` | `jwt`, `basic`, or `both` |
| `EXPLORER_USERNAME` | (none) | Admin username for seeding and Basic Auth |
| `EXPLORER_PASSWORD` | (none) | Admin password for seeding and Basic Auth |
| `JWT_SECRET_KEY` | (required) | Secret for JWT signing (still required even in basic mode for startup) |
| `JWT_EXPIRE_MINUTES` | `60` | JWT token lifetime in minutes |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Max `POST /auth/login` attempts per IP per 60s sliding window. `0` disables the limiter. |

## Data Model

### `_users` table (managed by dbzap, not introspected for API generation)

| Column        | Type         | Constraints           |
| ------------- | ------------ | --------------------- |
| id            | SERIAL       | PRIMARY KEY           |
| username      | VARCHAR(255) | UNIQUE, NOT NULL      |
| password_hash | VARCHAR(255) | NOT NULL              |
| created_at    | TIMESTAMP    | DEFAULT now()         |

- Table name prefixed with `_` to signal it is internal and should be excluded from API generation.
- Passwords are hashed with bcrypt before storage. Never stored in plaintext.

### User Seeding

At startup, `UserStore.initialize()` creates the `_users` table if it does not exist, then upserts the admin user defined by `EXPLORER_USERNAME` and `EXPLORER_PASSWORD`:

- If `EXPLORER_USERNAME` and `EXPLORER_PASSWORD` are both set:
  - If the user does not exist: insert with bcrypt-hashed password.
  - If the user already exists: update the password hash if it no longer matches (allows password rotation via `.env` change + restart).
- If either is not set: skip seeding (no admin user is created; login will fail until credentials are configured).

#### Multi-worker concurrency

`uvicorn --workers N` spawns N processes that each run `create_app()`
and therefore each call `seed_admin_user()` on the same database. The
naive sequence — `get_by_username` → branch on `None` → `create_user` —
has a window where two workers both observe `None`, both try to insert,
and the second loses with `IntegrityError`.

`seed_admin_user` MUST be safe under this race:

1. Attempt the `INSERT`.
2. On `IntegrityError` (the row already exists, planted by a peer
   worker), recover by reading the row back and proceeding to the
   "user exists" branch — i.e. update the password hash if it no
   longer matches the configured password.
3. Never re-raise the `IntegrityError`; the desired end-state has been
   reached either by us or by the winning worker.

This is the standard "INSERT … ON CONFLICT UPDATE" pattern at the
application layer, written portably so it works on PostgreSQL, MySQL,
and SQLite without dialect-specific upsert syntax.

## Security Requirements

### Constant-time login

Authentication MUST NOT leak whether a username exists via response
timing. Both successful and failed paths MUST take comparable time:

- When the username is unknown, the server MUST still perform a bcrypt
  verify against a fixed, well-known dummy hash before returning 401.
- This applies to both `POST /auth/login` and Basic Auth on every
  request — anywhere `verify_password` is called, an unknown user must
  trigger an equivalent dummy verification.

bcrypt verification dominates the request time (~100 ms vs ~1 ms for a
DB lookup), so without this dummy step an attacker can enumerate valid
usernames simply by measuring response times.

### Rate limiting on /auth/login

`POST /auth/login` MUST be rate-limited to prevent online password
brute-force. Without this, an attacker enumerates ~36000 passwords
per hour against a single IP (bcrypt at ~100 ms each) — fast enough
to break short numeric passwords or weak passphrases.

Requirements:
- Per-IP sliding-window limiter, configurable. Default: 10 attempts
  per 60 seconds. Configurable via `LOGIN_RATE_LIMIT_PER_MINUTE` env
  var (set `0` to disable).
- IP key: `request.client.host`. When the request comes through a
  trusted proxy, the standard FastAPI proxy headers handling applies
  to populating `request.client.host`. dbzap does not implement its
  own `X-Forwarded-For` parsing.
- On limit-exceeded: respond `429 Too Many Requests` with
  `Retry-After: <seconds>` header pointing at the oldest in-window
  attempt's expiry. The body MUST NOT depend on the username — the
  same response is returned for any rate-limit hit.
- The 429 path MUST short-circuit BEFORE `verify_password` runs —
  otherwise the limiter would consume the bcrypt budget it is meant
  to protect (the limiter exists to prevent attackers from running
  bcrypt at all).
- Limiter state is in-process memory (a `SlidingWindowLimiter`
  instance lives on the `UserStore`'s app factory). For multi-worker
  deployments and horizontal scale, a shared backend (Redis) is the
  next step but out of scope for the initial fix — the in-process
  limiter still cuts the per-worker brute-force rate by orders of
  magnitude and is the difference between "trivial brute-force" and
  "needs a coordinated botnet".
- A counter MUST be exposed via `MetricsCollector` (or returned in a
  separate label on `http_requests_total`) so operators can see when
  rate-limit kicks in. Initial implementation: simply counts as a
  `429` request to `/auth/login` in `http_requests_total`, which
  already exists; no new metric is required for this fix.

Successful logins do NOT reset the counter. Each attempt — successful
or not — counts. This is the simplest correct behavior; otherwise an
attacker can interleave a known-good login to refresh the budget.

### bcrypt password length

bcrypt silently truncates the input to 72 bytes. UTF-8 passwords with
non-ASCII characters (CJK, emoji) reach the limit at fewer than 72
visible characters, weakening the hash without warning.

`hash_password` and `verify_password` MUST be safe for arbitrary-length
inputs. The standard mitigation is to pre-hash the password with SHA-256
when it would otherwise exceed bcrypt's limit, then base64-encode the
digest before passing it to bcrypt. Both functions MUST apply the same
pre-hash so verification stays consistent.

## Edge Cases
- Wrong password on login: return 401 Unauthorized (same message as unknown username to avoid enumeration).
- Expired JWT token: return 401 with `{"detail": "Token has expired"}`.
- Malformed JWT token: return 401 with `{"detail": "Invalid token"}`.
- Invalid Basic Auth credentials: return 401 with `{"detail": "Invalid credentials"}` and `WWW-Authenticate: Basic` header.
- `jwt_secret_key` not configured: fail at startup with clear error.
- User deleted from DB while JWT token is still valid: `get_current_user` returns 401 (user not found).
- `EXPLORER_USERNAME` / `EXPLORER_PASSWORD` not set: no admin user seeded; all auth attempts will fail with 401.
- Password changed in `.env` and server restarted: existing user row is updated; old JWT tokens remain valid until expiry; Basic Auth immediately uses new password.
- `AUTH_MODE=basic` with `POST /auth/login`: returns 404 (no login endpoint in basic-only mode).
- `AUTH_MODE=both`: both Bearer token and Basic Auth are accepted on every endpoint; `/auth/login` is available.
- Basic Auth with `EXPLORER_PASSWORD` containing `:` character: base64 decode splits on first `:` only.

## Acceptance Criteria
- [ ] `/auth/login` returns a valid JWT on correct credentials (jwt/both mode).
- [ ] `/auth/login` returns 404 in basic-only mode.
- [ ] `/auth/me` returns user info when authenticated via Bearer token.
- [ ] `/auth/me` returns user info when authenticated via Basic Auth.
- [ ] All generated CRUD routes accept both Bearer and Basic Auth when `AUTH_MODE=both`.
- [ ] `/openapi.json` requires authentication; returns 401 without valid credentials.
- [ ] `AUTH_MODE=basic` validates username/password on every request via Basic Auth header.
- [ ] `AUTH_MODE=jwt` only accepts Bearer tokens.
- [ ] Auth endpoints (`/auth/*`) are always accessible without a token.
- [ ] Invalid Basic Auth credentials return 401 with `WWW-Authenticate: Basic` header.
- [ ] Expired/invalid JWT tokens return 401 with descriptive error messages.
- [ ] `_users` table is created automatically at startup if it does not exist.
- [ ] `_users` table is excluded from introspection and API generation.
- [ ] Passwords are never logged, returned, or stored in plaintext.
- [ ] Passwords longer than 72 bytes are accepted and verified correctly (no silent truncation by bcrypt).
- [ ] Login responses for unknown usernames take comparable time to login responses for known usernames with a wrong password (dummy bcrypt verify on the unknown-user path).
- [ ] `jwt_secret_key` is required - no insecure default.
- [ ] No `/auth/register` endpoint exists.
- [ ] Admin user is seeded from `EXPLORER_USERNAME` / `EXPLORER_PASSWORD` at startup.
- [ ] If admin user already exists, password hash is updated on restart.
- [ ] `seed_admin_user` is safe under multi-worker concurrent startup: a `IntegrityError` from a peer worker's winning insert MUST NOT propagate; the loser falls through to the "user exists" branch and updates the password hash if needed.
- [ ] `POST /auth/login` is rate-limited per client IP via a sliding window (default 10/min). The 11th attempt within the window returns `429 Too Many Requests` with a `Retry-After` header.
- [ ] The 429 path MUST short-circuit before `verify_password` runs — the bcrypt budget is the resource the limiter protects.
- [ ] Different IPs are independent (one client's flood does not lock out another client).
- [ ] `LOGIN_RATE_LIMIT_PER_MINUTE=0` disables the limiter (e.g. for tests or for environments that put a CDN-level limiter in front).

## Module Location
`src/dbzap/auth/`
- `auth/models.py` - UserRecord dataclass
- `auth/passwords.py` - bcrypt hash/verify
- `auth/tokens.py` - JWT create/decode
- `auth/dependencies.py` - FastAPI `get_current_user` (supports Bearer + Basic)
- `auth/routes.py` - `/auth/*` endpoints (login + me only)
- `auth/user_store.py` - `_users` table DDL, CRUD, and seeding

## Dependencies
- `python-jose[cryptography]` (JWT)
- `bcrypt` (password hashing)
- `fastapi` (routes, dependencies)
- `sqlalchemy[asyncio]` (user table queries)
- `src/dbzap/core/config.py` (JWT settings, auth mode, explorer credentials)
