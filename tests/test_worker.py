"""Tests for distributed worker job processing and local bot API integration."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from worker import get_worker_bot, process_download_job
from helpers.cache import cache_manager


def test_get_worker_bot_configuration():
    """Test get_worker_bot respects local Bot API server configuration."""
    mock_cfg = MagicMock()
    mock_cfg.bot_token = "TEST_TOKEN"
    mock_cfg.telegram_api_server_url = "http://localhost:8081/bot"
    mock_cfg.telegram_local_mode = True

    with patch("worker.config", mock_cfg):
        bot = get_worker_bot()
        assert "http://localhost:8081/bot" in bot.base_url
        assert bot.local_mode is True


@pytest.mark.asyncio
async def test_process_download_job_cache_hit():
    """Test worker delivers cached track instantly without downloading."""
    await cache_manager.set(
        video_id="worker_cached_1",
        file_id="WORKER_FILE_ID_123",
        title="Worker Cached Song",
        artist="Worker Artist",
        duration=180,
    )

    mock_bot = MagicMock()
    mock_bot.send_audio = AsyncMock()
    mock_bot.delete_message = AsyncMock()

    job_data = {
        "chat_id": 99999,
        "url": "https://youtu.be/worker_cached_1",
        "reply_to_message_id": 12,
        "status_message_id": 15,
    }

    ctx = {"bot": mock_bot}
    result = await process_download_job(ctx, job_data)

    assert result["status"] == "cached"
    assert result["video_id"] == "worker_cached_1"
    mock_bot.send_audio.assert_called_once()
    mock_bot.delete_message.assert_called_once_with(chat_id=99999, message_id=15)


@pytest.mark.asyncio
async def test_process_download_job_fresh_download(tmp_path):
    """Test worker executes download, uploads audio, and caches file_id."""
    fake_audio = tmp_path / "song.mp3"
    fake_audio.write_bytes(b"FAKE_MP3_CONTENT")

    test_vid_id = f"fresh_test_{time.time()}"

    mock_bot = MagicMock()
    mock_audio_msg = MagicMock()
    mock_audio_msg.audio.file_id = "NEW_WORKER_FILE_ID"
    mock_bot.send_audio = AsyncMock(return_value=mock_audio_msg)
    mock_bot.delete_message = AsyncMock()
    mock_bot.send_chat_action = AsyncMock()

    mock_downloader = MagicMock()
    mock_downloader.extract_video_id.return_value = test_vid_id
    mock_downloader.download_audio.return_value = {
        "file_path": str(fake_audio),
        "title": "Fresh Song",
        "artist": "Fresh Artist",
        "duration": 210,
        "thumbnail_path": None,
        "exceeds_limit": False,
        "filesize": len(fake_audio.read_bytes()),
    }

    job_data = {
        "chat_id": 88888,
        "url": f"https://youtu.be/{test_vid_id}",
        "reply_to_message_id": 101,
        "status_message_id": 102,
        "audio_format": "mp3",
        "bitrate": 192,
    }

    with patch("worker.AudioDownloader", return_value=mock_downloader):
        ctx = {"bot": mock_bot}
        result = await process_download_job(ctx, job_data)

        assert result["status"] == "success"
        assert result["title"] == "Fresh Song"
        mock_bot.send_audio.assert_called_once()
        mock_bot.delete_message.assert_called_once_with(chat_id=88888, message_id=102)

        # Check newly uploaded file was saved to cache
        cached = await cache_manager.get(test_vid_id)
        assert cached is not None
        assert cached["file_id"] == "NEW_WORKER_FILE_ID"
