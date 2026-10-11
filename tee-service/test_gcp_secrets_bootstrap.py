"""Unit tests for the GCP Secret Manager bootstrap mapping logic."""

import os

import pytest

import gcp_secrets_bootstrap as bootstrap

SECRET_ENV_KEYS = (
    "GCP_SECRET_TEE_PRIVATE_KEY",
    "GCP_SECRET_PRIVATE_KEY",
    "GCP_SECRET_LLM_API_KEY",
    "GCP_SECRETS_ENABLED",
    "GOOGLE_CLOUD_PROJECT",
)


@pytest.fixture(autouse=True)
def _restore_environ(monkeypatch):
    for name in SECRET_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def test_without_mappings_nothing_is_loaded():
    assert bootstrap.load_secrets() == []


def test_enabled_without_mappings_fails_closed(monkeypatch):
    monkeypatch.setenv("GCP_SECRETS_ENABLED", "1")

    with pytest.raises(RuntimeError, match="no GCP_SECRET_"):
        bootstrap.load_secrets()


def test_mappings_are_resolved_into_env(monkeypatch):
    monkeypatch.setenv("GCP_SECRET_TEE_PRIVATE_KEY", "tee-secret-name")
    monkeypatch.setenv("GCP_SECRET_PRIVATE_KEY", "relayer-secret-name")
    monkeypatch.setattr(bootstrap, "_project_id", lambda: "proj")
    monkeypatch.setattr(bootstrap, "_metadata_token", lambda: "token")
    monkeypatch.setattr(
        bootstrap,
        "_secret_value",
        lambda project_id, secret_name, access_token: f"value-for-{secret_name}",
    )

    loaded = bootstrap.load_secrets()

    assert sorted(loaded) == ["PRIVATE_KEY", "TEE_PRIVATE_KEY"]
    assert os.environ["TEE_PRIVATE_KEY"] == "value-for-tee-secret-name"
    assert os.environ["PRIVATE_KEY"] == "value-for-relayer-secret-name"
