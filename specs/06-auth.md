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
- [ ] `jwt_secret_key` is required - no insecure default.
- [ ] No `/auth/register` endpoint exists.
- [ ] Admin user is seeded from `EXPLORER_USERNAME` / `EXPLORER_PASSWORD` at startup.
- [ ] If admin user already exists, password hash is updated on restart.

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
