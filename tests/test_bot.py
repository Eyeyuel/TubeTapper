"""Tests for bot handlers, regex parsing, and workflows in bot.py."""

from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest

from bot import (
    YOUTUBE_URL_REGEX,
    create_bot_app,
    handle_message,
    handle_playlist,
    handle_single_track,
    help_command,
    start_command,
    status_command,
)


def test_youtube_url_regex():
    """Test regex matching against various YouTube and non-YouTube URLs."""
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL1234567890abcdef",
        "https://youtube.com/shorts/abc12345678",
        "Check this out: https://youtu.be/dQw4w9WgXcQ! It is cool.",
    ]
    for text in valid_urls:
        match = YOUTUBE_URL_REGEX.search(text)
        assert match is not None, f"Failed to match valid URL in: {text}"

    invalid_texts = [
        "https://spotify.com/track/123",
        "https://soundcloud.com/artist/track",
        "Just a regular message without links",
        "www.notyoutube.com/video",
    ]
    for text in invalid_texts:
        match = YOUTUBE_URL_REGEX.search(text)
        assert match is None, f"Should not match invalid text: {text}"


def test_create_bot_app_handlers():
    """Test that create_bot_app registers the expected command and message handlers."""
    app = create_bot_app(token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ")
    handlers = app.handlers.get(0, [])

    # Verify command handlers are registered
    command_names = set()
    for h in handlers:
        if hasattr(h, "commands") and h.commands:
            command_names.update(h.commands)

    assert "start" in command_names
    assert "help" in command_names
    assert "status" in command_names


@pytest.mark.asyncio
async def test_start_command():
    """Test /start command sends a markdown welcome message."""
    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await start_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    args, kwargs = mock_update.effective_message.reply_text.call_args
    assert "Welcome" in args[0]
    assert kwargs.get("parse_mode") == "Markdown"


@pytest.mark.asyncio
async def test_help_command():
    """Test /help command sends instructions and limits."""
    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await help_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    args, _ = mock_update.effective_message.reply_text.call_args
    assert "How to Use" in args[0]


@pytest.mark.asyncio
async def test_status_command():
    """Test /status command reports online status."""
    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await status_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    args, _ = mock_update.effective_message.reply_text.call_args
    assert "Online" in args[0]


@pytest.mark.asyncio
async def test_handle_single_track_flow(tmp_path):
    """Test single track workflow downloads audio, calls send_audio, and cleans up."""
    fake_mp3 = tmp_path / "song.mp3"
    fake_mp3.write_bytes(b"TEST_AUDIO")

    mock_track_data = {
        "file_path": str(fake_mp3),
        "title": "Song Title",
        "artist": "Artist Name",
        "duration": 200,
        "thumbnail_path": None,
        "filesize": 1024,
        "exceeds_limit": False,
    }

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.effective_message.message_id = 99
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()
    mock_status.delete = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot.send_audio = AsyncMock()

    mock_downloader = MagicMock()
    mock_downloader.download_audio.return_value = mock_track_data

    await handle_single_track(
        "https://youtu.be/test_id",
        mock_update,
        mock_context,
        mock_status,
        mock_downloader,
    )

    # Verify send_audio was called with correct parameters
    mock_context.bot.send_audio.assert_called_once()
    _, kwargs = mock_context.bot.send_audio.call_args
    assert kwargs["chat_id"] == 12345
    assert kwargs["title"] == "Song Title"
    assert kwargs["performer"] == "Artist Name"
    assert kwargs["duration"] == 200

    # Verify cleanup was invoked
    mock_downloader.cleanup_files.assert_called_once_with(str(fake_mp3), None)


@pytest.mark.asyncio
async def test_handle_playlist_flow(tmp_path):
    """Test playlist workflow processes each item and delivers tracks."""
    fake_mp3_1 = tmp_path / "p1.mp3"
    fake_mp3_2 = tmp_path / "p2.mp3"
    fake_mp3_1.write_bytes(b"P1")
    fake_mp3_2.write_bytes(b"P2")

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_status = MagicMock()
    mock_status.edit_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot.send_audio = AsyncMock()

    mock_downloader = MagicMock()
    mock_downloader.get_playlist_info.return_value = {
        "title": "Test Playlist",
        "total_tracks": 2,
    }

    # Simulate generator yielding 2 tracks
    async def mock_stream(url):
        yield (
            1,
            2,
            {
                "file_path": str(fake_mp3_1),
                "title": "Track 1",
                "artist": "Artist 1",
                "duration": 100,
                "thumbnail_path": None,
                "exceeds_limit": False,
            },
            None,
        )
        yield (
            2,
            2,
            {
                "file_path": str(fake_mp3_2),
                "title": "Track 2",
                "artist": "Artist 2",
                "duration": 120,
                "thumbnail_path": None,
                "exceeds_limit": False,
            },
            None,
        )

    mock_downloader.stream_playlist_tracks = mock_stream

    await handle_playlist(
        "https://www.youtube.com/playlist?list=PL_test",
        mock_update,
        mock_context,
        mock_status,
        mock_downloader,
    )

    # Verify send_audio was called twice
    assert mock_context.bot.send_audio.call_count == 2
    assert mock_downloader.cleanup_files.call_count == 2
