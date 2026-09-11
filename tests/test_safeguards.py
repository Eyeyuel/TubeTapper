"""Tests for system safeguards, access control, and cleanup in test_safeguards.py."""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import Update

from bot import (
    global_error_handler,
    handle_single_track,
    restricted,
    start_command,
)
from config import AppConfig
from helpers.cleanup import cleanup_orphaned_downloads


def test_orphaned_file_cleanup(tmp_path):
    """Test cleanup_orphaned_downloads deletes files older than threshold and preserves recent ones."""
    test_dir = tmp_path / "cleanup_test"
    test_dir.mkdir(parents=True, exist_ok=True)

    old_file = test_dir / "old_track.mp3"
    recent_file = test_dir / "recent_track.mp3"

    old_file.write_bytes(b"OLD DATA")
    recent_file.write_bytes(b"RECENT DATA")

    # Artificially modify mtime of old_file to 3600 seconds ago (1 hour ago)
    now = time.time()
    os.utime(str(old_file), (now - 3600, now - 3600))
    # recent_file is 10 seconds old
    os.utime(str(recent_file), (now - 10, now - 10))

    # Run cleanup with threshold 1800s (30 mins)
    removed_count = cleanup_orphaned_downloads(download_dir=test_dir, max_age_seconds=1800)

    assert removed_count == 1
    assert not old_file.exists()
    assert recent_file.exists()


@pytest.mark.asyncio
async def test_access_control_whitelist_rejected(monkeypatch):
    """Test that unauthorized user ID receives Access Denied notice."""
    test_cfg = AppConfig(
        bot_token="test_token",
        allowed_users={99999},
    )
    monkeypatch.setattr("bot.config", test_cfg)

    mock_update = MagicMock(spec=Update)
    mock_update.effective_user.id = 11111  # Unauthorized ID
    mock_update.effective_user.username = "unauthorized_user"
    mock_update.effective_message.reply_text = AsyncMock()

    mock_context = MagicMock()

    # Call restricted handler
    await start_command(mock_update, mock_context)

    # Verify access denied reply was sent
    mock_update.effective_message.reply_text.assert_called_once()
    args, kwargs = mock_update.effective_message.reply_text.call_args
    assert "Access Denied" in args[0]


@pytest.mark.asyncio
async def test_access_control_whitelist_allowed(monkeypatch):
    """Test that authorized user ID is permitted through."""
    test_cfg = AppConfig(
        bot_token="test_token",
        allowed_users={99999},
    )
    monkeypatch.setattr("bot.config", test_cfg)

    mock_update = MagicMock(spec=Update)
    mock_update.effective_user.id = 99999  # Authorized ID
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await start_command(mock_update, mock_context)

    # Verify welcome message was sent, NOT access denied
    mock_update.effective_message.reply_text.assert_called_once()
    args, _ = mock_update.effective_message.reply_text.call_args
    assert "Welcome" in args[0]
    assert "Access Denied" not in args[0]


@pytest.mark.asyncio
async def test_50mb_size_limit_guard_aborts_upload(tmp_path):
    """Test that files exceeding 50MB abort upload and inform the user."""
    fake_huge_file = tmp_path / "oversized.mp3"
    fake_huge_file.write_bytes(b"X" * 1024)

    mock_track_data = {
        "file_path": str(fake_huge_file),
        "title": "Huge Symphony",
        "artist": "Orchestra",
        "duration": 5400,
        "thumbnail_path": None,
        "filesize": 60 * 1024 * 1024,  # 60MB
        "exceeds_limit": True,
    }

    mock_update = MagicMock(spec=Update)
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()
    mock_context = MagicMock()
    mock_context.bot.send_audio = AsyncMock()

    mock_downloader = MagicMock()
    mock_downloader.download_audio.return_value = mock_track_data

    await handle_single_track(
        "https://youtu.be/oversized",
        mock_update,
        mock_context,
        mock_status,
        mock_downloader,
    )

    # Verify send_audio was NOT called
    mock_context.bot.send_audio.assert_not_called()

    # Verify user received a size warning message
    mock_status.edit_text.assert_called()
    args, _ = mock_status.edit_text.call_args
    assert "exceeds" in args[0]
    assert "50 MB" in args[0] or "50MB" in args[0]

    # Verify cleanup was called on the oversized file
    mock_downloader.cleanup_files.assert_called_once_with(str(fake_huge_file), None)


@pytest.mark.asyncio
async def test_global_error_handler():
    """Test global error handler catches exceptions and notifies user."""
    mock_update = MagicMock(spec=Update)
    mock_update.effective_message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.error = ValueError("Something unexpected broke")

    await global_error_handler(mock_update, mock_context)

    mock_update.effective_message.reply_text.assert_called_once()
    args, _ = mock_update.effective_message.reply_text.call_args
    assert "unexpected error" in args[0]
