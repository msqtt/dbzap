from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.models import UserRecord
from dbzap.auth.passwords import get_dummy_hash, verify_password
from dbzap.auth.rate_limit import SlidingWindowLimiter
from dbzap.auth.tokens import create_access_token
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class MeResponse(BaseModel):
    id: int
    username: str


def create_auth_router(
    *,
    store: UserStore,
    settings: Settings,
    login_limiter: SlidingWindowLimiter | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/auth")
    get_current_user = make_get_current_user(store=store, settings=settings)

    # P0-9 / spec 06: per-IP brute-force protection on /auth/login.
    # ``login_limiter`` is injectable so tests can use a fast clock and
    # production can swap in a Redis-backed implementation later.
    if login_limiter is None:
        login_limiter = SlidingWindowLimiter(
            max_calls=settings.login_rate_limit_per_minute,
            window_seconds=60.0,
        )

    if settings.auth_mode in ("jwt", "both"):

        @router.post("/login", response_model=TokenResponse)
        async def login(body: LoginRequest, request: Request) -> TokenResponse:
            # Limiter MUST run BEFORE verify_password — otherwise the bcrypt
            # cost the limiter is meant to protect is paid on every call.
            client = request.client
            ip = client.host if client is not None else "unknown"
            allowed, retry_after = login_limiter.check(ip)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts",
                    # Round up so Retry-After is never less than 1 second.
                    headers={"Retry-After": str(max(1, int(retry_after) + 1))},
                )

            _invalid = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
            user = await store.get_by_username(body.username)
            if user is None:
                # Constant-time path: still pay the bcrypt cost so an
                # attacker can't tell unknown user from wrong password
                # via response timing (specs/06-auth.md).
                verify_password(body.password, get_dummy_hash())
                raise _invalid
            if not verify_password(body.password, user.password_hash):
                raise _invalid
            token = create_access_token(
                {"sub": str(user.id)},
                secret=settings.jwt_secret_key,
                algorithm=settings.jwt_algorithm,
                expire_minutes=settings.jwt_expire_minutes,
            )
            return TokenResponse(
                access_token=token,
                token_type="bearer",
                expires_in=settings.jwt_expire_minutes * 60,
            )

    @router.get("/me", response_model=MeResponse)
    async def me(user: UserRecord = Depends(get_current_user)) -> MeResponse:
        return MeResponse(id=user.id, username=user.username)

    return router
