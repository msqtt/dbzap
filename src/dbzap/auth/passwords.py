"""Password hashing.

bcrypt silently truncates inputs at 72 bytes. UTF-8 multi-byte passwords
(CJK, emoji) hit that limit before the user expects, weakening the hash
and equating distinct passwords that share a 72-byte prefix.

Mitigation: SHA-256 pre-hash + base64 encode for any input that would
otherwise reach the bcrypt limit. Both ``hash_password`` and
``verify_password`` apply the same transform so verification stays
consistent. See ``specs/06-auth.md`` (Security Requirements > bcrypt
password length).
"""
from __future__ import annotations

import base64
import hashlib

import bcrypt

_BCRYPT_MAX_BYTES = 72


def _prepare_password(plain: str) -> bytes:
    """Encode the password for bcrypt, pre-hashing if it exceeds 72 bytes.

    The base64 of a SHA-256 digest is 44 ASCII bytes — well under the
    bcrypt limit and free of NUL bytes, which bcrypt also dislikes.
    """
    raw = plain.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    digest = hashlib.sha256(raw).digest()
    return base64.b64encode(digest)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prepare_password(plain), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prepare_password(plain), hashed.encode())


# A pre-computed dummy hash used by the auth dependency to keep the
# unknown-user path constant-time (see specs/06-auth.md). Generated lazily
# on first access to avoid paying the bcrypt cost at import time.
_dummy_hash: str | None = None


def get_dummy_hash() -> str:
    """Return a fixed bcrypt hash to verify against on the unknown-user
    branch. Same hash for every call so attackers cannot correlate it.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dbzap-constant-time-placeholder")
    return _dummy_hash
