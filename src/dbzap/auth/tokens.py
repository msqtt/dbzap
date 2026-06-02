from datetime import datetime, timedelta, timezone
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt


def create_access_token(
    data: dict[str, Any],
    *,
    secret: str,
    algorithm: str,
    expire_minutes: int,
) -> str:
    payload = dict(data)
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload["exp"] = expire
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(
    token: str,
    *,
    secret: str,
    algorithm: str,
) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except ExpiredSignatureError:
        raise ExpiredSignatureError("Token has expired")
    except JWTError as exc:
        raise JWTError("Invalid token") from exc
