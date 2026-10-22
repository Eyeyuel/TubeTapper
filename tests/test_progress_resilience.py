"""Tests for status message progress resilience, retry handling, and format picker continuity."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import telegram
import telegram.error

from helpers.progress import (
    render_progress_bar,
    update_or_reply_status,
    update_status_message,
)
from bot import handle_single_track


@pytest.mark.asyncio
async def test_update_status_message_success():
    """Test standard update_status_message execution."""
    mock_msg = MagicMock()
    mock_msg.edit_text = AsyncMock()

    success = await update_status_message(mock_msg, "Updated text")
    assert success is True
    mock_msg.edit_text.assert_called_once_with("Updated text", parse_mode="Markdown")


@pytest.mark.asyncio
async def test_update_status_message_retries_on_timeout():
    """Test update_status_message retries when Telegram API times out."""
    mock_msg = MagicMock()
    mock_msg.edit_text = AsyncMock(
        side_effect=[
            telegram.error.TimedOut("Timed out"),
            MagicMock(),
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        success = await update_status_message(mock_msg, "Retried text")

    assert success is True
    assert mock_msg.edit_text.call_count == 2


@pytest.mark.asyncio
async def test_update_status_message_falls_back_on_markdown_entity_error():
    """Test update_status_message falls back to plain text if Markdown parsing fails."""
    mock_msg = MagicMock()
    mock_msg.edit_text = AsyncMock(
        side_effect=[
            telegram.error.BadRequest("Can't parse entities: can't find end of italic entity"),
            MagicMock(),
        ]
    )

    success = await update_status_message(mock_msg, "Text with _bad markdown*")
    assert success is True
    assert mock_msg.edit_text.call_count == 2

    # Second call should not have parse_mode='Markdown'
    _, second_kwargs = mock_msg.edit_text.call_args_list[1]
    assert "parse_mode" not in second_kwargs


@pytest.mark.asyncio
async def test_update_status_message_ignores_message_not_modified():
    """Test update_status_message treats 'Message is not modified' as success."""
    mock_msg = MagicMock()
    mock_msg.edit_text = AsyncMock(
        side_effect=telegram.error.BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")
    )

    success = await update_status_message(mock_msg, "Same text")
    assert success is True


@pytest.mark.asyncio
async def test_update_or_reply_status_fallback_to_reply():
    """Test update_or_reply_status posts a new message if editing fails permanently."""
    mock_old_msg = MagicMock()
    # Permanent failure on edit_text
    mock_old_msg.edit_text = AsyncMock(
        side_effect=telegram.error.BadRequest("Message to edit not found")
    )
    mock_new_msg = MagicMock()
    mock_old_msg.reply_text = AsyncMock(return_value=mock_new_msg)
    mock_old_msg.delete = AsyncMock()

    result = await update_or_reply_status(mock_old_msg, "Status update text")
    assert result == mock_new_msg
    mock_old_msg.reply_text.assert_called_once()
    mock_old_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_handle_single_track_ask_mode_escapes_special_characters():
    """Test ask mode escapes markdown special characters in titles so message edit never fails."""
    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.effective_user.id = 99999
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()

    mock_context = MagicMock()
    mock_downloader = MagicMock()
    mock_downloader.extract_video_id.return_value = "short_vid_123"
    mock_downloader.get_video_info.return_value = {
        "title": "Crazy_Short [1080p] *viral* #shorts",
        "duration": 45,
    }

    with patch("bot.get_user_preferences", new_callable=AsyncMock, return_value=("ask", "mp3_320", "mp4_720")), \
         patch("bot.cache_manager.get", new_callable=AsyncMock, return_value=None):
        await handle_single_track(
            "https://youtube.com/shorts/short_vid_123",
            mock_update,
            mock_context,
            mock_status,
            mock_downloader,
        )

    mock_status.edit_text.assert_called_once()
    args, kwargs = mock_status.edit_text.call_args
    sent_text = args[0]
    # Underscores and asterisks in title must be escaped for Telegram Markdown v1
    assert "Crazy\\_Short" in sent_text
    assert "\\*viral\\*" in sent_text
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_handle_single_track_ask_mode_timeout_resilience():
    """Test ask mode handles slow or hanging yt-dlp metadata calls without hanging the UI."""
    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.effective_user.id = 99999
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()

    mock_context = MagicMock()
    mock_downloader = MagicMock()
    mock_downloader.extract_video_id.return_value = "slow_vid_123"

    def slow_extract(url):
        import time
        time.sleep(10)
        return {"title": "Slow Video", "duration": 60}

    mock_downloader.get_video_info.side_effect = slow_extract

    async def mock_wait_for(coro, timeout):
        try:
            coro.close()
        except Exception:
            pass
        raise asyncio.TimeoutError()

    with patch("bot.get_user_preferences", new_callable=AsyncMock, return_value=("ask", "mp3_320", "mp4_720")), \
         patch("bot.cache_manager.get", new_callable=AsyncMock, return_value=None), \
         patch("asyncio.wait_for", side_effect=mock_wait_for):
        await handle_single_track(
            "https://youtube.com/shorts/slow_vid_123",
            mock_update,
            mock_context,
            mock_status,
            mock_downloader,
        )

    mock_status.edit_text.assert_called_once()
    args, kwargs = mock_status.edit_text.call_args
    sent_text = args[0]
    # Should cleanly fall back to generic title and render the format picker
    assert "YouTube Video" in sent_text
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_handle_single_track_ask_mode_uses_cache_when_available():
    """Test ask mode fast-path grabs title and duration from cache without calling yt-dlp."""
    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.effective_user.id = 99999
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()

    mock_context = MagicMock()
    mock_downloader = MagicMock()
    mock_downloader.extract_video_id.return_value = "cached_short_123"

    cached_entry = {
        "title": "Instant Cached Short Title",
        "duration": 55,
        "file_id": "BAADBAAD...",
    }

    with patch("bot.get_user_preferences", new_callable=AsyncMock, return_value=("ask", "mp3_320", "mp4_720")), \
         patch("bot.cache_manager.get", new_callable=AsyncMock, return_value=cached_entry):
        await handle_single_track(
            "https://youtube.com/shorts/cached_short_123",
            mock_update,
            mock_context,
            mock_status,
            mock_downloader,
        )

    # get_video_info should NOT have been called because cache hit was instant!
    mock_downloader.get_video_info.assert_not_called()
    mock_status.edit_text.assert_called_once()
    args, kwargs = mock_status.edit_text.call_args
    assert "Instant Cached Short Title" in args[0]
    assert "0:55" in args[0]
    assert "reply_markup" in kwargs
