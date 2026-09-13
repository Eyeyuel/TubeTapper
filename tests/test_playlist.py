"""Tests for playlist detection, parsing, and streaming in downloader.py."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import yt_dlp

from config import AppConfig
from downloader import AudioDownloader


@pytest.fixture
def downloader(tmp_path):
    """Instantiates AudioDownloader with a test directory."""
    cfg = AppConfig(
        bot_token="test_token",
        download_dir=tmp_path / "test_playlist_downloads",
    )
    return AudioDownloader(config=cfg)


def test_is_playlist_detection(downloader):
    """Test URL pattern detection for playlists vs single videos."""
    # Playlist URLs
    assert downloader.is_playlist("https://www.youtube.com/playlist?list=PL1234567890abcdef") is True
    assert downloader.is_playlist("https://youtube.com/playlist?list=PL1234567890abcdef") is True
    assert downloader.is_playlist("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890abcdef") is True
    assert downloader.is_playlist("https://youtu.be/dQw4w9WgXcQ?list=PL1234567890abcdef") is True

    # Single Video URLs (no playlist list parameter)
    assert downloader.is_playlist("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
    assert downloader.is_playlist("https://youtu.be/dQw4w9WgXcQ") is False

    # Radio mixes (RD...) are now supported as playlists
    assert downloader.is_playlist("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ") is True
    assert downloader.is_playlist("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ") is True

    # Non-YouTube or empty URLs
    assert downloader.is_playlist("https://vimeo.com/123456") is False
    assert downloader.is_playlist("") is False


def test_normalize_playlist_url(downloader):
    """Test playlist URL normalization for canonical vs radio vs watch formats."""
    # Standard watch + list
    target, is_radio, pid = downloader.normalize_playlist_url(
        "https://www.youtube.com/watch?v=hpyn5amYwCY&list=PLx3zFT7knx14i0_n7ou8AZ3h_dh-ARWh6"
    )
    assert target == "https://www.youtube.com/playlist?list=PLx3zFT7knx14i0_n7ou8AZ3h_dh-ARWh6"
    assert is_radio is False
    assert pid == "PLx3zFT7knx14i0_n7ou8AZ3h_dh-ARWh6"

    # Radio mix preserves video query context
    radio_url = "https://www.youtube.com/watch?v=i_kF4zLNKio&list=RDJzSUgOmP66Q&index=5"
    target_radio, is_radio_flag, radio_id = downloader.normalize_playlist_url(radio_url)
    assert target_radio == radio_url
    assert is_radio_flag is True
    assert radio_id == "RDJzSUgOmP66Q"



def test_get_playlist_info_success(downloader):
    """Test fast playlist metadata extraction without full stream download."""
    mock_playlist_payload = {
        "id": "PL_sample_list",
        "title": "Top Hits 2026",
        "entries": [
            {
                "id": "vid1",
                "title": "Hit Track One",
                "duration": 210,
                "url": "https://www.youtube.com/watch?v=vid1",
            },
            {
                "id": "vid2",
                "title": "Hit Track Two",
                "duration": 185,
                "url": "https://www.youtube.com/watch?v=vid2",
            },
            None,  # Simulate occasional empty entry from private video in flat extract
            {
                "id": "vid3",
                "title": "Hit Track Three",
                "duration": 240,
                # No URL provided, test fallback generation
            },
        ],
    }

    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=mock_playlist_payload) as mock_extract:
        info = downloader.get_playlist_info("https://www.youtube.com/playlist?list=PL_sample_list")

        assert info["title"] == "Top Hits 2026"
        assert info["id"] == "PL_sample_list"
        assert info["total_tracks"] == 3  # Non-empty entries
        assert info["tracks"][0]["title"] == "Hit Track One"
        assert info["tracks"][1]["id"] == "vid2"
        assert info["tracks"][2]["url"] == "https://www.youtube.com/watch?v=vid3"


@pytest.mark.asyncio
async def test_stream_playlist_tracks_success_and_resilience(downloader, tmp_path):
    """Test streaming tracks yields sequentially and gracefully handles errors on single tracks."""
    mock_playlist_info = {
        "id": "PL_resilience",
        "title": "Mixed Status Playlist",
        "total_tracks": 3,
        "tracks": [
            {"id": "vid_good_1", "title": "Good Track 1", "url": "https://www.youtube.com/watch?v=vid_good_1"},
            {"id": "vid_deleted", "title": "Deleted Track", "url": "https://www.youtube.com/watch?v=vid_deleted"},
            {"id": "vid_good_2", "title": "Good Track 2", "url": "https://www.youtube.com/watch?v=vid_good_2"},
        ],
    }

    # Mock download_audio: succeed for good tracks, raise for deleted track
    def mock_download(url, output_dir=None):
        if "vid_deleted" in url:
            raise RuntimeError("Video unavailable: This video is private or deleted.")
        return {
            "id": url.split("=")[-1],
            "file_path": f"/tmp/{url.split('=')[-1]}.mp3",
            "title": "Sample Title",
            "artist": "Sample Artist",
            "duration": 180,
            "thumbnail_path": None,
            "filesize": 5000000,
            "exceeds_limit": False,
        }

    with patch.object(downloader, "get_playlist_info", return_value=mock_playlist_info), \
         patch.object(downloader, "download_audio", side_effect=mock_download):

        results = []
        async for item in downloader.stream_playlist_tracks("https://www.youtube.com/playlist?list=PL_resilience"):
            results.append(item)

        # Check that all 3 tracks were processed sequentially without crashing
        assert len(results) == 3

        # Track 1: success
        idx1, total1, data1, err1 = results[0]
        assert idx1 == 1
        assert total1 == 3
        assert data1 is not None
        assert data1["id"] == "vid_good_1"
        assert err1 is None

        # Track 2: shielded failure (deleted video)
        idx2, total2, data2, err2 = results[1]
        assert idx2 == 2
        assert total2 == 3
        assert data2 is None
        assert "unavailable" in err2

        # Track 3: success continued after error on track 2
        idx3, total3, data3, err3 = results[2]
        assert idx3 == 3
        assert total3 == 3
        assert data3 is not None
        assert data3["id"] == "vid_good_2"
        assert err3 is None


@pytest.mark.asyncio
async def test_stream_playlist_tracks_max_tracks_limit(downloader):
    """Test max_tracks parameter caps the number of tracks processed."""
    mock_playlist_info = {
        "id": "PL_large",
        "title": "Large Playlist",
        "total_tracks": 10,
        "tracks": [{"id": f"v{i}", "title": f"T{i}", "url": f"https://yt.com?v=v{i}"} for i in range(10)],
    }

    def mock_download(url, output_dir=None):
        return {"id": "test", "file_path": "/tmp/test.mp3", "title": "T", "artist": "A", "duration": 100, "thumbnail_path": None, "filesize": 1000, "exceeds_limit": False}

    with patch.object(downloader, "get_playlist_info", return_value=mock_playlist_info), \
         patch.object(downloader, "download_audio", side_effect=mock_download):

        results = []
        async for item in downloader.stream_playlist_tracks("https://www.youtube.com/playlist?list=PL_large", max_tracks=2):
            results.append(item)

        assert len(results) == 2
        assert results[0][0] == 1
        assert results[0][1] == 2  # Total capped to 2
        assert results[1][0] == 2


@pytest.mark.asyncio
async def test_stream_playlist_pipelined_cancellation(downloader):
    """Test pipelined streaming stops immediately when cancel_event is set."""
    mock_playlist_info = {
        "id": "PL_cancel",
        "title": "Cancel Playlist",
        "total_tracks": 5,
        "tracks": [{"id": f"v{i}", "title": f"T{i}", "url": f"https://yt.com?v=v{i}"} for i in range(5)],
    }

    cancel_event = asyncio.Event()

    def mock_download(url, output_dir=None, **kwargs):
        return {"id": "test", "file_path": "/tmp/test.mp3", "title": "T", "artist": "A", "duration": 100, "thumbnail_path": None, "filesize": 1000, "exceeds_limit": False}

    with patch.object(downloader, "get_playlist_info", return_value=mock_playlist_info), \
         patch.object(downloader, "download_audio", side_effect=mock_download):

        results = []
        async for item in downloader.stream_playlist_pipelined(
            "https://www.youtube.com/playlist?list=PL_cancel",
            cancel_event=cancel_event,
        ):
            results.append(item)
            if len(results) == 2:
                # Cancel after 2 tracks
                cancel_event.set()

        # Should yield at most 2 or 3 items (buffered queue) and stop before reaching 5
        assert len(results) < 5

