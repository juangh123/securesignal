"""Key lifecycle tests for crypto/keys.py.

The production contract is fail-closed: a missing TEE_PRIVATE_KEY must abort
startup when ENV=prod. Ephemeral keys are a development-only convenience.
"""

import pytest

from crypto import keys

VALID_KEY_HEX = "22" * 32


@pytest.fixture(autouse=True)
def _clear_cached_key():
    """Keep the module-level cache from leaking between tests."""
    keys._private_key_bytes = None
    yield
    keys._private_key_bytes = None


def test_prod_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("TEE_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("ENV", "prod")

    with pytest.raises(RuntimeError, match="production"):
        keys.init_keys()


def test_prod_blank_key_fails_closed(monkeypatch):
    monkeypatch.setenv("TEE_PRIVATE_KEY", "   ")
    monkeypatch.setenv("ENV", "prod")

    with pytest.raises(RuntimeError, match="production"):
        keys.get_private_key_hex()


def test_dev_missing_key_generates_ephemeral_key(monkeypatch):
    monkeypatch.delenv("TEE_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("ENV", "dev")

    public_key = keys.get_public_key_hex()
    private_key = keys.get_private_key_hex()
    address = keys.get_tee_address()

    assert len(private_key) == 64
    assert len(public_key) == 130
    assert public_key.startswith("04")
    assert len(address) == 42
    assert address.startswith("0x")
    # The key is cached for the process lifetime, not regenerated per call.
    assert keys.get_private_key_hex() == private_key


def test_invalid_key_length_is_rejected(monkeypatch):
    monkeypatch.setenv("TEE_PRIVATE_KEY", "11" * 31)
    monkeypatch.setenv("ENV", "dev")

    with pytest.raises(ValueError, match="32 bytes"):
        keys.init_keys()


def test_valid_prefixed_key_is_loaded_verbatim_and_cached(monkeypatch):
    monkeypatch.setenv("TEE_PRIVATE_KEY", "0x" + VALID_KEY_HEX)
    monkeypatch.setenv("ENV", "prod")

    assert keys.get_private_key_hex() == VALID_KEY_HEX
    first_address = keys.get_tee_address()

    keys._private_key_bytes = None
    assert keys.get_tee_address() == first_address
