"""Tests for dual-orientation video resolution selection and cache duplicate purging."""

import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from config import AppConfig
from downloader import AudioDownloader
from helpers.cache import AudioCacheManager


@pytest.fixture
def mock_config(tmp_path):
    return AppConfig(
        bot_token="mock_token",
        allowed_users=set(),
        download_dir=tmp_path / "test_downloads",
        audio_bitrate=192,
        max_file_size_mb=50,
    )


def test_dual_orientation_format_spec_generation(mock_config):
    """Test format_spec incorporates both width and height constraints for landscape & Shorts."""
    dl = AudioDownloader(config=mock_config)

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "id": "test_vid",
            "title": "Test Title",
            "duration": 60,
        }
        mock_ydl.return_value.__enter__.return_value = mock_instance

        # Test 480p format
        try:
            dl.download_audio("https://youtu.be/test_vid", audio_format="mp4_480")
        except Exception:
            pass

        assert mock_ydl.called
        opts = mock_ydl.call_args[0][0]
        fmt_spec = opts["format"]
        assert "height<=?480" in fmt_spec
        assert "width<=?480" in fmt_spec
        # Ensure unconstrained /best fallback was removed
        assert not fmt_spec.endswith("/best")


def test_adaptive_player_client_for_video_downloads(mock_config):
    """Test _apply_network_options allows video downloads to use android and web clients without unsupported clients."""
    dl = AudioDownloader(config=mock_config)

    audio_opts = dl._apply_network_options({}, is_video=False)
    assert audio_opts["extractor_args"]["youtube"]["player_client"] == ["android", "ios"]

    video_opts = dl._apply_network_options({}, is_video=True)
    clients = video_opts["extractor_args"]["youtube"]["player_client"]
    assert "android" in clients
    assert "web" in clients
    assert "android_creator" not in clients


@pytest.mark.asyncio
async def test_purge_duplicate_video_cache(tmp_path):
    """Test purge_duplicate_video_cache deletes duplicate video sizes while keeping distinct ones and audio."""
    db_path = tmp_path / "test_cache.db"
    mgr = AudioCacheManager(redis_url=None, cache_dir=tmp_path)
    mgr.sqlite_path = db_path
    mgr._init_sqlite()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Insert duplicate contaminated video records (same non-zero file_size for 480p and 720p)
        cursor.execute(
            "INSERT INTO media_cache (video_id, format_id, file_id, file_size, created_at) VALUES (?, ?, ?, ?, ?)",
            ("dup_vid", "mp4_480", "FID_1", 7687651, 1000.0)
        )
        cursor.execute(
            "INSERT INTO media_cache (video_id, format_id, file_id, file_size, created_at) VALUES (?, ?, ?, ?, ?)",
            ("dup_vid", "mp4_720", "FID_2", 7687651, 1000.0)
        )
        # Insert a distinct audio record for the same video
        cursor.execute(
            "INSERT INTO media_cache (video_id, format_id, file_id, file_size, created_at) VALUES (?, ?, ?, ?, ?)",
            ("dup_vid", "mp3_320", "FID_AUD", 4117577, 1000.0)
        )
        # Insert a non-duplicate video record for another video
        cursor.execute(
            "INSERT INTO media_cache (video_id, format_id, file_id, file_size, created_at) VALUES (?, ?, ?, ?, ?)",
            ("clean_vid", "mp4_720", "FID_CLEAN", 9999999, 1000.0)
        )
        conn.commit()

    deleted = await mgr.purge_duplicate_video_cache()
    assert deleted == 2

    # Verify only the duplicates were removed
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id, format_id FROM media_cache")
        remaining = cursor.fetchall()
        assert ("dup_vid", "mp3_320") in remaining
        assert ("clean_vid", "mp4_720") in remaining
        assert ("dup_vid", "mp4_480") not in remaining
        assert ("dup_vid", "mp4_720") not in remaining


def test_fetch_youtube_oembed(mock_config, monkeypatch):
    """Test fetch_youtube_oembed returns title and author from JSON response."""
    from unittest.mock import MagicMock
    import io

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"title": "Test Title", "author_name": "Test Author", "thumbnail_url": "https://img.jpg"}'
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=2.0: mock_resp)

    dl = AudioDownloader(config=mock_config)
    res = dl.fetch_youtube_oembed("https://www.youtube.com/watch?v=dummy123456")
    assert res is not None
    assert res["title"] == "Test Title"
    assert res["author_name"] == "Test Author"


def test_get_video_info_oembed_fallback_on_error(mock_config, monkeypatch):
    """Test get_video_info recovers title via oEmbed when yt-dlp raises an error."""
    dl = AudioDownloader(config=mock_config)

    monkeypatch.setattr(
        dl,
        "fetch_youtube_oembed",
        lambda url, timeout=2.0: {"title": "Resilient Title", "author_name": "Resilient Artist", "thumbnail_url": "http://thumb"}
    )

    class FailingYoutubeDL:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            raise RuntimeError("YouTube bot block simulation")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FailingYoutubeDL)

    info = dl.get_video_info("https://www.youtube.com/watch?v=dummy123456")
    assert info["id"] == "dummy123456"
    assert "Resilient Title" in info["title"]
    assert info["thumbnail_url"] == "http://thumb"

