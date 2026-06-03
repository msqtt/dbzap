import base64
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError

from dbzap.auth.models import UserRecord
from dbzap.auth.passwords import get_dummy_hash, verify_password
from dbzap.auth.tokens import decode_access_token
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def make_get_current_user(
    *,
    store: UserStore,
    settings: Settings,
) -> Callable[..., Any]:
    from fastapi import Depends

    auth_mode = settings.auth_mode
    jwt_enabled = auth_mode in ("jwt", "both")
    basic_enabled = auth_mode in ("basic", "both")

    async def _try_bearer(token: str) -> UserRecord:
        if not jwt_enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT auth not enabled",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = decode_access_token(
                token,
                secret=settings.jwt_secret_key,
                algorithm=settings.jwt_algorithm,
            )
        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = await store.get_by_id(int(sub))
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    async def _try_basic(request: Request) -> UserRecord:
        if not basic_enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Basic auth not enabled",
                headers={"WWW-Authenticate": "Basic"},
            )
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Basic "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Basic Auth header",
                headers={"WWW-Authenticate": "Basic"},
            )
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Basic Auth header",
                headers={"WWW-Authenticate": "Basic"},
            )
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Basic Auth header",
                headers={"WWW-Authenticate": "Basic"},
            )
        user = await store.get_by_username(username)
        if user is None:
            # Constant-time path: still pay the bcrypt cost so an
            # attacker can't enumerate usernames via response timing
            # (specs/06-auth.md).
            verify_password(password, get_dummy_hash())
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        if not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return user

    async def get_current_user(
        token: str | None = Depends(_oauth2_scheme),
        request: Request = None,  # type: ignore[assignment]
    ) -> UserRecord:
        # Try Bearer token first
        if token and jwt_enabled:
            try:
                return await _try_bearer(token)
            except HTTPException:
                if not basic_enabled:
                    raise

        # Try Basic Auth
        if basic_enabled:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Basic "):
                return await _try_basic(request)

        # Nothing worked
        headers: dict[str, str] = {}
        if jwt_enabled:
            headers["WWW-Authenticate"] = "Bearer"
        if basic_enabled:
            headers["WWW-Authenticate"] = "Basic"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers=headers,
        )

    return get_current_user
