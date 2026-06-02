import time

import pytest

from dbzap.auth.tokens import create_access_token, decode_access_token

SECRET = "test-secret-key-for-tokens"
ALGORITHM = "HS256"


def test_create_returns_string() -> None:
    token = create_access_token({"sub": "1"}, secret=SECRET, algorithm=ALGORITHM, expire_minutes=60)
    assert isinstance(token, str)
    assert len(token) > 0


def test_decode_returns_payload() -> None:
    token = create_access_token({"sub": "1"}, secret=SECRET, algorithm=ALGORITHM, expire_minutes=60)
    payload = decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)
    assert payload["sub"] == "1"


def test_decode_includes_exp() -> None:
    token = create_access_token({"sub": "1"}, secret=SECRET, algorithm=ALGORITHM, expire_minutes=60)
    payload = decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)
    assert "exp" in payload


def test_expired_token_raises() -> None:
    token = create_access_token({"sub": "1"}, secret=SECRET, algorithm=ALGORITHM, expire_minutes=0)
    # expire_minutes=0 means expires immediately; sleep briefly so it's past expiry
    time.sleep(1)
    with pytest.raises(Exception, match="[Ee]xpir"):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_wrong_secret_raises() -> None:
    token = create_access_token({"sub": "1"}, secret=SECRET, algorithm=ALGORITHM, expire_minutes=60)
    with pytest.raises(Exception):
        decode_access_token(token, secret="wrong-secret", algorithm=ALGORITHM)


def test_malformed_token_raises() -> None:
    with pytest.raises(Exception):
        decode_access_token("not.a.valid.token", secret=SECRET, algorithm=ALGORITHM)
