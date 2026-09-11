"""Tests for configuration loading and validation."""

import os
from pathlib import Path
import pytest

from config import AppConfig, load_config


def test_default_config_loading(monkeypatch, tmp_path):
    """Test loading configuration with default values."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALLOWED_USERS", raising=False)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "custom_downloads"))
    monkeypatch.delenv("AUDIO_BITRATE", raising=False)
    monkeypatch.delenv("MAX_FILE_SIZE_MB", raising=False)

    cfg = load_config()

    assert cfg.bot_token == ""
    assert cfg.allowed_users == set()
    assert cfg.audio_bitrate == 320
    assert cfg.max_file_size_mb == 50
    assert cfg.max_file_size_bytes == 50 * 1024 * 1024
    assert cfg.download_dir.exists()
    assert cfg.is_user_allowed(12345) is True  # Empty whitelist allows everyone


def test_custom_config_values(monkeypatch, tmp_path):
    """Test loading configuration with custom environment variables."""
    test_dir = tmp_path / "music_downloads"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token_123456")
    monkeypatch.setenv("ALLOWED_USERS", "111111, 222222, invalid, 333333")
    monkeypatch.setenv("DOWNLOAD_DIR", str(test_dir))
    monkeypatch.setenv("AUDIO_BITRATE", "256")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "45")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    cfg = load_config()

    assert cfg.bot_token == "test_token_123456"
    assert cfg.allowed_users == {111111, 222222, 333333}
    assert cfg.audio_bitrate == 256
    assert cfg.max_file_size_mb == 45
    assert cfg.max_file_size_bytes == 45 * 1024 * 1024
    assert cfg.log_level == "DEBUG"
    assert cfg.download_dir.resolve() == test_dir.resolve()
    assert cfg.download_dir.exists()

    # Test whitelist authorization
    assert cfg.is_user_allowed(111111) is True
    assert cfg.is_user_allowed(222222) is True
    assert cfg.is_user_allowed(999999) is False  # Not in whitelist


def test_directory_creation_on_init(tmp_path, monkeypatch):
    """Test that load_config automatically creates the downloads folder if it doesn't exist."""
    nested_dir = tmp_path / "deeply" / "nested" / "downloads"
    assert not nested_dir.exists()

    monkeypatch.setenv("DOWNLOAD_DIR", str(nested_dir))
    cfg = load_config()

    assert cfg.download_dir.exists()
    assert cfg.download_dir.is_dir()
