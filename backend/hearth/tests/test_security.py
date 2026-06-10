from __future__ import annotations

from hearth import security


def test_password_roundtrip():
    h = security.hash_password("correct horse battery staple")
    ok, rehash = security.verify_password("correct horse battery staple", h)
    assert ok and rehash is None
    bad, _ = security.verify_password("wrong", h)
    assert not bad


def test_api_token_mint_verify():
    token, digest = security.mint_api_token("integration")
    assert token.startswith("hrt_")
    assert security.verify_api_token(token, digest)
    assert not security.verify_api_token("hrt_other", digest)


def test_secret_encryption_roundtrip():
    ct = security.encrypt_secret("very-secret-ha-token")
    assert ct != "very-secret-ha-token"
    assert security.decrypt_secret(ct) == "very-secret-ha-token"


def test_mask_never_leaks():
    assert "secret" not in security.mask("hrt_secret_value_here")
    assert security.mask("") == ""
