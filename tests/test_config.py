from __future__ import annotations

import os

import pytest

from longplay_radio.config import ConfigurationError, Settings


def test_default_config_is_keyless(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("LONGPLAY_") or name.startswith("B2_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env()
    assert settings.renderer == "fake"
    assert settings.storage == "local"
    assert settings.b2_app_key is None


def test_b2_requires_credentials(monkeypatch):
    monkeypatch.setenv("LONGPLAY_STORAGE", "b2_s3")
    monkeypatch.setenv("B2_BUCKET", "valid-private-bucket")
    monkeypatch.setenv("B2_REGION", "us-west-004")
    monkeypatch.delenv("B2_KEY_ID", raising=False)
    monkeypatch.delenv("B2_APP_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="credentials"):
        Settings.from_env()


def test_removed_storage_modes_fail_closed(monkeypatch):
    monkeypatch.setenv("LONGPLAY_STORAGE", "unapproved_backend")
    with pytest.raises(ConfigurationError, match="local or b2_s3"):
        Settings.from_env()
