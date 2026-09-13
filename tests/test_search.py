"""Tests for direct in-chat YouTube search and interactive inline keyboard generation."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bot import build_search_keyboard, execute_search, search_command, handle_message
from downloader import AudioDownloader


def test_build_search_keyboard():
    """Test generating interactive inline keyboard with title and duration."""
    results = [
        {"id": "vid123", "title": "Track One", "duration": 185},
        {"id": "vid456", "title": "Track Two With a Very Long Name That Exceeds The Limit", "duration": 60},
    ]
    kb = build_search_keyboard(results)
    assert len(kb.inline_keyboard) == 2
    assert "vid123" in kb.inline_keyboard[0][0].callback_data
    assert "Track One" in kb.inline_keyboard[0][0].text
    assert "(3:05)" in kb.inline_keyboard[0][0].text
    assert "dl:vid456" == kb.inline_keyboard[1][0].callback_data


def test_search_youtube_extractor():
    """Test search_youtube runs extract_info with flat extraction without downloading."""
    downloader = AudioDownloader()

    mock_search_data = {
        "entries": [
            {
                "id": "abc1",
                "title": "Song A",
                "uploader": "Artist A",
                "duration": 200,
                "view_count": 1000,
            },
            {
                "id": "abc2",
                "title": "Song B",
                "uploader": "Artist B",
                "duration": 150,
            },
        ]
    }

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = mock_search_data

        results = downloader.search_youtube("Song", max_results=2)
        assert len(results) == 2
        assert results[0]["id"] == "abc1"
        assert results[0]["title"] == "Song A"
        assert results[1]["id"] == "abc2"
        mock_ydl.extract_info.assert_called_once_with("ytsearch2:Song", download=False)


@pytest.mark.asyncio
async def test_execute_search_renders_results():
    """Test execute_search posts status and updates with inline keyboard."""
    mock_update = MagicMock()
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()
    mock_update.effective_message.reply_text = AsyncMock(return_value=mock_status)

    mock_context = MagicMock()

    mock_results = [
        {"id": "search_res_1", "title": "Search Result 1", "duration": 210}
    ]

    with patch.object(AudioDownloader, "search_youtube", return_value=mock_results):
        await execute_search("Test Song", mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        mock_status.edit_text.assert_called_once()
        call_kwargs = mock_status.edit_text.call_args[1]
        assert "Top results for" in mock_status.edit_text.call_args[0][0]
        assert call_kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_search_command_missing_query():
    """Test /search without arguments prompts user for query."""
    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()
    mock_context.args = []

    await search_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    assert "Usage:" in mock_update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_falls_back_to_search():
    """Test non-URL text triggers YouTube search automatically."""
    mock_update = MagicMock()
    mock_update.effective_message.text = "Bohemian Rhapsody Queen"
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()
    mock_update.effective_message.reply_text = AsyncMock(return_value=mock_status)
    mock_context = MagicMock()

    with patch("bot.execute_search", new_callable=AsyncMock) as mock_exec:
        await handle_message(mock_update, mock_context)
        mock_exec.assert_called_once_with("Bohemian Rhapsody Queen", mock_update, mock_context)
