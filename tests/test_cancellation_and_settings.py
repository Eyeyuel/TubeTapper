"""Tests for playlist cancellation, audio settings, callback queries, and progress rendering."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bot import (
    ACTIVE_PLAYLIST_CANCELLATIONS,
    build_cancel_keyboard,
    build_settings_keyboard,
    handle_callback_query,
    settings_command,
    get_user_audio_format,
)
from helpers.progress import render_progress_bar
from helpers.cache import cache_manager


def test_render_progress_bar():
    """Test progress bar strings across various percentages."""
    bar_0 = render_progress_bar(0)
    assert "▱▱▱▱▱▱▱▱▱▱ 0%" in bar_0

    bar_50 = render_progress_bar(50)
    assert "▰▰▰▰▰▱▱▱▱▱ 50%" in bar_50

    bar_100 = render_progress_bar(100)
    assert "▰▰▰▰▰▰▰▰▰▰ 100%" in bar_100


def test_build_settings_keyboard():
    """Test settings keyboard marks the active preference."""
    kb_192 = build_settings_keyboard("mp3_192")
    assert "✅" in kb_192.inline_keyboard[0][0].text
    assert "✅" not in kb_192.inline_keyboard[1][0].text

    kb_320 = build_settings_keyboard("mp3_320")
    assert "✅" in kb_320.inline_keyboard[1][0].text

    kb_m4a = build_settings_keyboard("m4a")
    assert "✅" in kb_m4a.inline_keyboard[2][0].text


def test_build_cancel_keyboard():
    """Test playlist cancel keyboard."""
    kb = build_cancel_keyboard(9988)
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == "cancel_pl:9988"
    assert "Cancel" in kb.inline_keyboard[0][0].text


@pytest.mark.asyncio
async def test_settings_command_renders_options():
    """Test /settings command retrieves preference and replies with options."""
    mock_update = MagicMock()
    mock_update.effective_user.id = 555
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await settings_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    call_args = mock_update.effective_message.reply_text.call_args
    assert "Audio Download Settings" in call_args[0][0]
    assert call_args[1].get("reply_markup") is not None


@pytest.mark.asyncio
async def test_callback_set_format():
    """Test callback query for changing audio format."""
    mock_update = MagicMock()
    mock_update.effective_user.id = 777
    mock_query = MagicMock()
    mock_query.data = "set_fmt:mp3_320"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_reply_markup = AsyncMock()
    mock_update.callback_query = mock_query
    mock_context = MagicMock()

    await handle_callback_query(mock_update, mock_context)

    # Format updated
    fmt, bitrate = await get_user_audio_format(777)
    assert fmt == "mp3"
    assert bitrate == 320
    mock_query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_cancel_playlist():
    """Test callback query triggers active cancellation event."""
    chat_id = 443322
    cancel_event = asyncio.Event()
    ACTIVE_PLAYLIST_CANCELLATIONS[chat_id] = cancel_event

    try:
        assert not cancel_event.is_set()

        mock_update = MagicMock()
        mock_query = MagicMock()
        mock_query.data = f"cancel_pl:{chat_id}"
        mock_query.answer = AsyncMock()
        mock_update.callback_query = mock_query
        mock_context = MagicMock()

        await handle_callback_query(mock_update, mock_context)

        assert cancel_event.is_set()
        mock_query.answer.assert_called_once()
    finally:
        ACTIVE_PLAYLIST_CANCELLATIONS.pop(chat_id, None)


@pytest.mark.asyncio
async def test_callback_download_track():
    """Test callback query dl:<video_id> initiates download."""
    mock_update = MagicMock()
    mock_query = MagicMock()
    mock_query.data = "dl:test_vid_xyz"
    mock_query.answer = AsyncMock()
    mock_status_msg = MagicMock()
    mock_query.message.reply_text = AsyncMock(return_value=mock_status_msg)
    mock_update.callback_query = mock_query
    mock_context = MagicMock()

    with patch("bot.handle_single_track", new_callable=AsyncMock) as mock_handle:
        await handle_callback_query(mock_update, mock_context)

        mock_query.answer.assert_called_once()
        mock_handle.assert_called_once()
        args = mock_handle.call_args[0]
        assert "test_vid_xyz" in args[0]
