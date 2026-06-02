# Feature: Authentication & Authorization

## Goal
Provide JWT-based authentication with user registration, login, and route protection middleware. All API endpoints require auth by default.

## Scope
- In scope: User registration, login (username/password), JWT token issuance and validation, FastAPI dependency for route protection, password hashing (bcrypt), public endpoint whitelist via config
- Out of scope: OAuth2/OIDC, role-based access control (RBAC), API key auth, token refresh tokens (future), session management, multi-tenancy

## API Contract

Auth endpoints (always public):

```
POST /auth/register   -> Create a new user, return 201
POST /auth/login      -> Validate credentials, return JWT token
GET  /auth/me         -> Return current authenticated user info
```

### POST /auth/register

Request:
```json
{
  "username": "alice",
  "password": "s3cureP@ss"
}
```

Response (201):
```json
{
  "id": 1,
  "username": "alice"
}
```

### POST /auth/login

Request:
```json
{
  "username": "alice",
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

### GET /auth/me

Headers: `Authorization: Bearer <token>`

Response (200):
```json
{
  "id": 1,
  "username": "alice"
}
```

### Route Protection

FastAPI dependency:
```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserRecord:
    """Validate JWT and return the current user. Raises 401 if invalid."""
```

Usage in generated routes:
```python
@router.get("/api/users")
async def list_users(user: UserRecord = Depends(get_current_user)):
    ...
```

## Data Model

### `_users` table (managed by dbzap, not introspected for API generation)

| Column     | Type         | Constraints           |
| ---------- | ------------ | --------------------- |
| id         | SERIAL       | PRIMARY KEY           |
| username   | VARCHAR(255) | UNIQUE, NOT NULL      |
| password_hash | VARCHAR(255) | NOT NULL           |
| created_at | TIMESTAMP    | DEFAULT now()         |

- Table name prefixed with `_` to signal it is internal and should be excluded from API generation.
- Passwords are hashed with bcrypt before storage. Never stored in plaintext.

## Edge Cases
- Duplicate username on register: return 409 Conflict.
- Wrong password on login: return 401 Unauthorized (same message as unknown username to avoid enumeration).
- Expired token: return 401 with `{"detail": "Token has expired"}`.
- Malformed token: return 401 with `{"detail": "Invalid token"}`.
- Password too short (< 8 chars): return 422 with validation error.
- `jwt_secret_key` not configured: fail at startup with clear error.
- User deleted from DB while token is still valid: `get_current_user` returns 401 (user not found).

## Acceptance Criteria
- [ ] `/auth/register` creates user with bcrypt-hashed password.
- [ ] `/auth/login` returns a valid JWT on correct credentials.
- [ ] `/auth/me` returns user info when authenticated.
- [ ] All generated CRUD routes require `get_current_user` dependency by default.
- [ ] Auth endpoints (`/auth/*`) are always accessible without a token.
- [ ] Expired/invalid tokens return 401 with descriptive error messages.
- [ ] `_users` table is created automatically at startup if it does not exist.
- [ ] `_users` table is excluded from introspection and API generation.
- [ ] Passwords are never logged, returned, or stored in plaintext.
- [ ] `jwt_secret_key` is required - no insecure default.

## Module Location
`src/dbzap/auth/`
- `auth/models.py` - UserRecord dataclass
- `auth/passwords.py` - bcrypt hash/verify
- `auth/tokens.py` - JWT create/decode
- `auth/dependencies.py` - FastAPI `get_current_user`
- `auth/routes.py` - `/auth/*` endpoints
- `auth/user_store.py` - `_users` table DDL and CRUD

## Dependencies
- `python-jose[cryptography]` (JWT)
- `passlib[bcrypt]` (password hashing)
- `fastapi` (routes, dependencies)
- `sqlalchemy[asyncio]` (user table queries)
- `src/dbzap/core/config.py` (JWT settings)
