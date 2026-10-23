from portal.services import auth


def test_test_password_roundtrip():
    stored = auth.hash_test_password("hemmelig1")
    assert stored.startswith("scrypt$") and "hemmelig1" not in stored
    assert auth.check_test_password(stored, "hemmelig1")
    assert not auth.check_test_password(stored, "forkert")


def test_test_password_rejects_garbage_hash():
    assert not auth.check_test_password(None, "x")
    assert not auth.check_test_password("ikke-et-hash", "x")
    assert not auth.check_test_password("scrypt$zz$zz", "x")
