
from dbzap.auth.passwords import hash_password, verify_password


def test_hash_returns_string() -> None:
    h = hash_password("s3cureP@ss")
    assert isinstance(h, str)
    assert h != "s3cureP@ss"


def test_hash_is_bcrypt() -> None:
    h = hash_password("s3cureP@ss")
    assert h.startswith("$2")


def test_verify_correct_password() -> None:
    h = hash_password("s3cureP@ss")
    assert verify_password("s3cureP@ss", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("s3cureP@ss")
    assert verify_password("wrongpass", h) is False


def test_two_hashes_differ() -> None:
    h1 = hash_password("s3cureP@ss")
    h2 = hash_password("s3cureP@ss")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Long password handling (spec 06: bcrypt silent truncation MUST NOT happen).
# ---------------------------------------------------------------------------


def test_hash_handles_password_longer_than_72_bytes() -> None:
    """Passwords > 72 bytes must hash and verify intact (no silent truncation)."""
    long_pw = "a" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True


def test_long_passwords_with_different_tails_are_distinguishable() -> None:
    """Bcrypt's 72-byte truncation would equate two long passwords sharing
    the first 72 bytes. The pre-hash mitigation must prevent that."""
    pw_a = ("x" * 72) + "AAAA"
    pw_b = ("x" * 72) + "ZZZZ"
    h_a = hash_password(pw_a)
    assert verify_password(pw_a, h_a) is True
    # Critical: pw_b must NOT verify against pw_a's hash (they differ
    # only after byte 72 — only safe if we pre-hash).
    assert verify_password(pw_b, h_a) is False


def test_unicode_password_longer_than_72_bytes() -> None:
    """A CJK password where every char is 3 UTF-8 bytes — 25 chars = 75 bytes."""
    pw = "密码" * 30  # 60 chars × 3 bytes = 180 bytes
    h = hash_password(pw)
    assert verify_password(pw, h) is True
