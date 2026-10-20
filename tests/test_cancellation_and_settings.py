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
    get_user_preferences,
)
from helpers.progress import render_progress_bar
from helpers.cache import cache_manager


def test_render_progress_bar():
    """Test progress bar strings across various percentages."""
    bar_0 = render_progress_bar(0)
    assert "[░░░░░░░░░░] 0%" in bar_0

    bar_50 = render_progress_bar(50)
    assert "[█████░░░░░] 50%" in bar_50

    bar_100 = render_progress_bar(100)
    assert "[██████████] 100%" in bar_100

    bar_over = render_progress_bar(150)
    assert "[██████████] 100%" in bar_over


def test_build_settings_keyboard():
    """Test settings keyboard styles active preferences with style='success' across all 3 sections."""
    # Test audio preference styles (Row 2)
    kb_192 = build_settings_keyboard("auto", "mp3_192", "mp4_720")
    assert kb_192.inline_keyboard[2][0].style == "success"
    assert kb_192.inline_keyboard[2][1].style is None

    kb_320 = build_settings_keyboard("auto", "mp3_320", "mp4_720")
    assert kb_320.inline_keyboard[2][1].style == "success"

    kb_m4a = build_settings_keyboard("auto", "m4a", "mp4_720")
    assert kb_m4a.inline_keyboard[2][2].style == "success"

    # Test mode preference styles (Row 0 & 1)
    kb_ask = build_settings_keyboard("ask", "mp3_320", "mp4_720")
    assert kb_ask.inline_keyboard[0][1].style == "success"
    assert kb_ask.inline_keyboard[0][0].style is None

    # Test video quality styles (Row 3)
    kb_1080 = build_settings_keyboard("auto", "mp3_320", "mp4_1080")
    assert kb_1080.inline_keyboard[3][2].style == "success"
    assert kb_1080.inline_keyboard[3][0].style is None


def test_build_cancel_keyboard():
    """Test playlist cancel keyboard has danger red style."""
    kb = build_cancel_keyboard(9988)
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == "cancel_pl:9988"
    assert kb.inline_keyboard[0][0].style == "danger"
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
    assert "Download Settings" in call_args[0][0]
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


def test_build_format_picker_and_delivery_switch_keyboards():
    """Test 6-button format picker and 1-tap delivery quick switch keyboards."""
    from bot import build_format_picker_keyboard, build_delivery_switch_keyboard

    # Format picker for single track (fmt: prefix)
    single_kb = build_format_picker_keyboard("vid_123", is_playlist=False)
    assert len(single_kb.inline_keyboard) == 2
    assert "fmt:mp3_320:vid_123" in single_kb.inline_keyboard[0][1].callback_data
    assert "fmt:mp4_720:vid_123" in single_kb.inline_keyboard[1][1].callback_data

    # Format picker for playlist (plfmt: prefix)
    pl_kb = build_format_picker_keyboard("pl_abc", is_playlist=True)
    assert "plfmt:mp3_320:pl_abc" in pl_kb.inline_keyboard[0][1].callback_data
    assert "plfmt:mp4_1080:pl_abc" in pl_kb.inline_keyboard[1][2].callback_data

    # Delivery switch when delivered as video -> offers audio extraction with primary style
    vid_delivered_kb = build_delivery_switch_keyboard("vid_123", current_format="mp4_720")
    assert "fmt:mp3_320:vid_123" in vid_delivered_kb.inline_keyboard[0][0].callback_data
    assert "Extract" in vid_delivered_kb.inline_keyboard[0][0].text
    assert vid_delivered_kb.inline_keyboard[0][0].style == "primary"

    # Delivery switch when delivered as audio -> offers video download with primary style
    aud_delivered_kb = build_delivery_switch_keyboard("vid_123", current_format="mp3_320")
    assert "fmt:mp4_720:vid_123" in aud_delivered_kb.inline_keyboard[0][0].callback_data
    assert "Video" in aud_delivered_kb.inline_keyboard[0][0].text
    assert aud_delivered_kb.inline_keyboard[0][0].style == "primary"


@pytest.mark.asyncio
async def test_callback_mode_and_quality_updates():
    """Test callback queries for set_mode and set_vid."""
    from bot import get_user_preferences

    mock_update = MagicMock()
    mock_update.effective_user.id = 888
    mock_query = MagicMock()
    mock_query.answer = AsyncMock()
    mock_query.edit_message_reply_markup = AsyncMock()
    mock_update.callback_query = mock_query
    mock_context = MagicMock()

    # Test set_mode
    mock_query.data = "set_mode:ask"
    await handle_callback_query(mock_update, mock_context)
    mode, _, _ = await get_user_preferences(888)
    assert mode == "ask"

    # Test set_vid
    mock_query.data = "set_vid:mp4_1080"
    await handle_callback_query(mock_update, mock_context)
    _, _, vid = await get_user_preferences(888)
    assert vid == "mp4_1080"


@pytest.mark.asyncio
async def test_legacy_format_migration_and_video_quality_persistence():
    """Verify that old mp4 stored in audio_format is cleaned up and does not overwrite video_quality."""
    user_id = 999111
    # Simulate legacy state: audio_format has mp4_1080, and video_quality was just chosen as mp4_480
    await cache_manager.set_user_setting(user_id, "audio_format", "mp4_1080")
    await cache_manager.set_user_setting(user_id, "video_quality", "mp4_480")

    mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)
    assert vid_fmt == "mp4_480", "Explicit video_quality must NOT be overwritten by old audio_format"
    assert audio_fmt == "mp3_320", "Legacy audio_format must be normalized to mp3_320"

    # Verify settings keyboard renders green on 480p
    kb = build_settings_keyboard(mode, audio_fmt, vid_fmt)
    assert kb.inline_keyboard[3][0].style == "success"  # 480p is green
    assert kb.inline_keyboard[3][2].style is None       # 1080p is unselected
