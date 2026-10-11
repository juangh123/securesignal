"""Unit tests for the enclave bootstrap environment allowlist."""

import os

import pytest

import aws_nitro_bootstrap


@pytest.fixture(autouse=True)
def _restore_environ():
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def test_applies_allowlisted_keys():
    skipped = aws_nitro_bootstrap.apply_bundle(
        {
            "TEE_PRIVATE_KEY": "0x" + "11" * 32,
            "LLM_MODEL": "deepseek-flash",
            "AWS_NITRO_PCR0": "aa" * 48,
            "KMS_VSOCK_PORT": "8600",
        }
    )

    assert skipped == []
    assert os.environ["TEE_PRIVATE_KEY"] == "0x" + "11" * 32
    assert os.environ["LLM_MODEL"] == "deepseek-flash"
    assert os.environ["AWS_NITRO_PCR0"] == "aa" * 48
    assert os.environ["KMS_VSOCK_PORT"] == "8600"


def test_unknown_keys_are_skipped_not_applied():
    os.environ.pop("SOMETHING_ELSE", None)

    skipped = aws_nitro_bootstrap.apply_bundle({"SOMETHING_ELSE": "1"})

    assert skipped == ["SOMETHING_ELSE"]
    assert "SOMETHING_ELSE" not in os.environ


@pytest.mark.parametrize(
    "reserved", ["PATH", "PYTHONPATH", "LD_PRELOAD", "NODE_OPTIONS"]
)
def test_reserved_keys_are_rejected(reserved):
    with pytest.raises(ValueError, match="reserved"):
        aws_nitro_bootstrap.apply_bundle({reserved: "/tmp/evil"})


def test_non_string_values_are_rejected():
    with pytest.raises(ValueError, match="strings"):
        aws_nitro_bootstrap.apply_bundle({"TEE_PRIVATE_KEY": 123})
