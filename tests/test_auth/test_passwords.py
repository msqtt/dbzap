import pytest

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
