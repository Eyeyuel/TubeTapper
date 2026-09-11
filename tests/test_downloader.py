"""Tests for AudioDownloader in downloader.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import yt_dlp

from config import AppConfig
from downloader import AudioDownloader


@pytest.fixture
def mock_config(tmp_path):
    """Provides an AppConfig instance pointing to a temporary download directory."""
    return AppConfig(
        bot_token="mock_token",
        allowed_users=set(),
        download_dir=tmp_path / "test_downloads",
        audio_bitrate=192,
        max_file_size_mb=50,
    )


@pytest.fixture
def downloader(mock_config):
    """Instantiates AudioDownloader with mock_config."""
    return AudioDownloader(config=mock_config)


def test_get_video_info_success(downloader):
    """Test get_video_info correctly extracts and formats video metadata."""
    sample_info = {
        "id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "duration": 213,
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "is_live": False,
        "filesize_approx": 5000000,
    }

    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=sample_info) as mock_extract:
        result = downloader.get_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        mock_extract.assert_called_once_with(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False
        )
        assert result["id"] == "dQw4w9WgXcQ"
        assert result["title"] == "Never Gonna Give You Up"
        assert result["artist"] == "Rick Astley"
        assert result["duration"] == 213
        assert result["thumbnail_url"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        assert result["is_live"] is False


def test_get_video_info_fallback_artist(downloader):
    """Test artist fallback to uploader if artist is not populated."""
    sample_info = {
        "id": "sample123",
        "title": "Sample Title",
        "uploader": "Sample Channel",
        "duration": 120,
    }

    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=sample_info):
        result = downloader.get_video_info("https://www.youtube.com/watch?v=sample123")
        assert result["artist"] == "Sample Channel"


def test_download_audio_success(downloader, tmp_path):
    """Test download_audio processes stream, returns valid file metadata, and creates MP3."""
    video_id = "test_vid_001"
    target_dir = tmp_path / "test_downloads"
    target_dir.mkdir(parents=True, exist_ok=True)
    fake_mp3 = target_dir / f"{video_id}.mp3"
    fake_thumb = target_dir / f"{video_id}.jpg"

    # Simulate yt-dlp creating output files during extraction
    def fake_extract_info(url, download=True):
        fake_mp3.write_bytes(b"FAKE MP3 AUDIO STREAM DATA" * 1000)
        fake_thumb.write_bytes(b"FAKE JPEG IMAGE DATA")
        return {
            "id": video_id,
            "title": "Test Song Title",
            "artist": "Test Artist",
            "duration": 180,
        }

    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=fake_extract_info):
        result = downloader.download_audio("https://www.youtube.com/watch?v=test_vid_001", output_dir=target_dir)

        assert result["file_path"] == str(fake_mp3)
        assert result["title"] == "Test Song Title"
        assert result["artist"] == "Test Artist"
        assert result["duration"] == 180
        assert result["thumbnail_path"] == str(fake_thumb)
        assert result["filesize"] > 0
        assert result["exceeds_limit"] is False
        assert Path(result["file_path"]).exists()


def test_download_audio_exceeds_size_limit(downloader, tmp_path):
    """Test download_audio correctly detects when file size exceeds the max limit."""
    video_id = "huge_vid_001"
    target_dir = tmp_path / "test_downloads"
    target_dir.mkdir(parents=True, exist_ok=True)
    fake_mp3 = target_dir / f"{video_id}.mp3"

    # Create a config with 1MB limit for test
    downloader.config = AppConfig(
        bot_token="test",
        download_dir=target_dir,
        max_file_size_mb=1,
    )

    def fake_extract_huge(url, download=True):
        # Write ~2MB of data
        fake_mp3.write_bytes(b"X" * (2 * 1024 * 1024))
        return {
            "id": video_id,
            "title": "Huge Audio Track",
            "artist": "Huge Artist",
            "duration": 3600,
        }

    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=fake_extract_huge):
        result = downloader.download_audio("https://www.youtube.com/watch?v=huge_vid_001", output_dir=target_dir)
        assert result["exceeds_limit"] is True


def test_cleanup_files(downloader, tmp_path):
    """Test cleanup_files cleanly removes multiple temporary files and handles non-existent paths."""
    file1 = tmp_path / "temp1.mp3"
    file2 = tmp_path / "temp2.jpg"
    file1.write_text("dummy mp3")
    file2.write_text("dummy jpg")

    assert file1.exists()
    assert file2.exists()

    # Call cleanup including None and non-existent file
    downloader.cleanup_files(str(file1), file2, None, tmp_path / "non_existent.mp3")

    assert not file1.exists()
    assert not file2.exists()
