from typing import Any, Callable

from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError

from dbzap.auth.models import UserRecord
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

    async def get_current_user(
        token: str | None = Depends(_oauth2_scheme),
    ) -> UserRecord:
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
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

    return get_current_user
