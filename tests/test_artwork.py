"""Unit tests for helpers/artwork.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from helpers.artwork import (
    crop_to_square_jpeg,
    embed_metadata_and_artwork_to_mp3,
    fetch_deezer_artwork,
    fetch_itunes_artwork,
    resolve_album_art,
)


def test_fetch_itunes_artwork_success(tmp_path):
    """Test fetching and parsing official artwork from iTunes Search API."""
    out_file = tmp_path / "itunes_cover.jpg"
    mock_payload = {
        "results": [
            {
                "collectionName": "Painting Pictures",
                "artistName": "Kodak Black",
                "trackName": "Tunnel Vision",
                "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/100x100bb.jpg",
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.Client.get", return_value=mock_resp), \
         patch("helpers.artwork.crop_to_square_jpeg", return_value=str(out_file)):
        res = fetch_itunes_artwork("Kodak Black Tunnel Vision", out_file)

        assert res is not None
        assert res["artwork_path"] == str(out_file)
        assert res["album"] == "Painting Pictures"
        assert res["artist"] == "Kodak Black"
        assert res["title"] == "Tunnel Vision"


def test_fetch_itunes_artwork_empty_results(tmp_path):
    """Test iTunes search returns None when no songs match."""
    out_file = tmp_path / "itunes_empty.jpg"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}

    with patch("httpx.Client.get", return_value=mock_resp):
        res = fetch_itunes_artwork("NonExistentTrackXYZ", out_file)
        assert res is None


def test_fetch_deezer_artwork_success(tmp_path):
    """Test fetching cover art from Deezer API."""
    out_file = tmp_path / "deezer_cover.jpg"
    mock_payload = {
        "data": [
            {
                "title": "Tunnel Vision",
                "artist": {"name": "Kodak Black"},
                "album": {
                    "title": "Painting Pictures",
                    "cover_big": "https://e-cdns-images.dzcdn.net/images/cover/500x500.jpg",
                },
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.Client.get", return_value=mock_resp), \
         patch("helpers.artwork.crop_to_square_jpeg", return_value=str(out_file)):
        res = fetch_deezer_artwork("Kodak Black Tunnel Vision", out_file)

        assert res is not None
        assert res["artwork_path"] == str(out_file)
        assert res["album"] == "Painting Pictures"
        assert res["artist"] == "Kodak Black"


def test_resolve_album_art_itunes_primary(tmp_path):
    """Test resolve_album_art uses iTunes as first choice."""
    out_file = tmp_path / "final_cover.jpg"
    with patch("helpers.artwork.fetch_itunes_artwork", return_value={"artwork_path": str(out_file)}):
        result = resolve_album_art(
            artist="Kodak Black",
            song_name="Tunnel Vision",
            youtube_thumb_url="https://youtube.com/thumb.jpg",
            mp3_path=None,
            output_thumb_path=out_file,
        )
        assert result == str(out_file)


def test_resolve_album_art_fallback_to_youtube_crop(tmp_path):
    """Test resolve_album_art falls back to YouTube thumbnail square crop when APIs fail."""
    out_file = tmp_path / "fallback_cover.jpg"

    with patch("helpers.artwork.fetch_itunes_artwork", return_value=None), \
         patch("helpers.artwork.fetch_deezer_artwork", return_value=None), \
         patch("helpers.artwork.crop_to_square_jpeg", return_value=str(out_file)):
        result = resolve_album_art(
            artist="Unknown Artist",
            song_name="Obscure Track",
            youtube_thumb_url="https://i.ytimg.com/vi/xyz/hqdefault.jpg",
            mp3_path=None,
            output_thumb_path=out_file,
        )
        assert result == str(out_file)


def test_embed_metadata_and_artwork_to_mp3_success(tmp_path):
    """Test embedding metadata and cover art calls ffmpeg and updates target file."""
    fake_mp3 = tmp_path / "test.mp3"
    fake_mp3.write_bytes(b"dummy mp3 data")
    fake_art = tmp_path / "art.jpg"
    fake_art.write_bytes(b"dummy art data")

    def mock_ffmpeg(cmd, **kwargs):
        # Simulate temp output file creation
        out_file = Path(cmd[-1])
        out_file.write_bytes(b"tagged mp3 data")
        res = MagicMock()
        res.returncode = 0
        return res

    with patch("subprocess.run", side_effect=mock_ffmpeg):
        success = embed_metadata_and_artwork_to_mp3(
            mp3_path=fake_mp3,
            art_path=fake_art,
            title="Tunnel Vision",
            artist="Kodak Black",
        )
        assert success is True
        assert fake_mp3.read_bytes() == b"tagged mp3 data"


def test_embed_metadata_and_artwork_to_mp3_missing_files(tmp_path):
    """Test embed returns False if files do not exist."""
    fake_mp3 = tmp_path / "nonexistent.mp3"
    fake_art = tmp_path / "art.jpg"
    assert embed_metadata_and_artwork_to_mp3(fake_mp3, fake_art, "Title", "Artist") is False


def test_resolve_album_art_concurrent_deezer_fallback(tmp_path):
    """Test concurrent resolution falls back to Deezer when iTunes is empty/unavailable."""
    out_file = tmp_path / "deezer_fallback.jpg"
    with patch("helpers.artwork.fetch_itunes_artwork", return_value=None), \
         patch("helpers.artwork.fetch_deezer_artwork", return_value={"artwork_path": str(out_file)}):
        result = resolve_album_art(
            artist="Kodak Black",
            song_name="Tunnel Vision",
            youtube_thumb_url=None,
            mp3_path=None,
            output_thumb_path=out_file,
        )
        assert result == str(out_file)


def test_crop_to_square_jpeg_from_url(tmp_path):
    """Test crop_to_square_jpeg fetches URL bytes and invokes ffmpeg pipe."""
    out_file = tmp_path / "cropped_url.jpg"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"FAKE_IMAGE_BYTES"

    def mock_ffmpeg(cmd, **kwargs):
        out_file.write_bytes(b"CROPPED_JPEG")
        res = MagicMock()
        res.returncode = 0
        return res

    with patch("httpx.Client.get", return_value=mock_resp), \
         patch("subprocess.run", side_effect=mock_ffmpeg):
        res = crop_to_square_jpeg("https://example.com/cover.jpg", out_file)
        assert res == str(out_file)
        assert out_file.read_bytes() == b"CROPPED_JPEG"


