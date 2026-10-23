"""Tests for user-friendly error formatting and download resilience in downloader.py."""

import pytest
from config import AppConfig
from downloader import AudioDownloader, format_download_error, get_short_error_reason


def test_format_download_error_http_500():
    """Test HTTP Error 500 maps to friendly server issue message without raw logs."""
    raw_error = "ERROR: [download] Got error: HTTP Error 500: Internal Server Error"
    msg = format_download_error(raw_error)

    assert "YouTube Server Issue" in msg
    assert "ERROR: [download]" not in msg
    assert "500" in msg
    assert "💡 *Tip:*" in msg


def test_format_download_error_network_unreachable():
    """Test network unreachable / socket drop maps to connection timeout without raw errno."""
    raw_error = "ERROR: [download] Got error: [Errno 101] Network is unreachable"
    msg = format_download_error(raw_error)

    assert "Connection Timeout" in msg
    assert "Errno 101" not in msg
    assert "ERROR: [download]" not in msg


def test_format_download_error_bot_verification():
    """Test YouTube bot verification notice is user-friendly."""
    raw_error = "Sign in to confirm you’re not a bot. This helps protect our community."
    msg = format_download_error(raw_error)

    assert "Download Blocked by YouTube" in msg
    assert "protect our community" not in msg


def test_format_download_error_age_restricted():
    """Test age restricted notice."""
    raw_error = "Sign in to confirm your age. This video may be inappropriate for some users."
    msg = format_download_error(raw_error)

    assert "Age-Restricted Track" in msg
    assert "account authentication" in msg


def test_format_download_error_private_video():
    """Test private or removed video notice."""
    raw_error = "ERROR: [youtube] Private video. Sign in if you've been granted access."
    msg = format_download_error(raw_error)

    assert "Video Unavailable" in msg
    assert "ERROR: [youtube]" not in msg


def test_format_download_error_members_only():
    """Test members-only content notice."""
    raw_error = "Join this channel to get access to members-only content like this video."
    msg = format_download_error(raw_error)

    assert "Members-Only Content" in msg


def test_format_download_error_region_blocked():
    """Test region and copyright restriction notice."""
    raw_error = "The uploader has not made this video available in your country on copyright grounds."
    msg = format_download_error(raw_error)

    assert "Region Restricted" in msg


def test_format_download_error_format_unavailable():
    """Test format unavailable notice suggests alternative qualities."""
    raw_error = "ERROR: [youtube] Requested format is not available. Use --list-formats for a list."
    msg = format_download_error(raw_error)

    assert "Format Unavailable" in msg
    assert "--list-formats" not in msg
    assert "💡 *Tip:*" in msg


def test_format_download_error_unexpected_fallback():
    """Test completely unknown/internal exception produces a polite message without exposing code traces."""
    raw_error = "RuntimeError: Internal core dump at memory address 0x7ffd98b0 inside libavcodec.so"
    msg = format_download_error(raw_error)

    assert "Download Failed" in msg
    assert "0x7ffd98b0" not in msg
    assert "libavcodec.so" not in msg
    assert "💡 *Tip:*" in msg


def test_get_short_error_reason():
    """Test get_short_error_reason returns concise descriptions for playlist status updates."""
    assert get_short_error_reason("HTTP Error 500: Internal Server Error") == "YouTube server error"
    assert get_short_error_reason("[Errno 101] Network is unreachable") == "Connection timeout"
    assert get_short_error_reason("Sign in to confirm your age") == "Age-restricted"
    assert get_short_error_reason("Private video") == "Video unavailable"
    assert get_short_error_reason("Join this channel") == "Members-only"
    assert get_short_error_reason("Not available in your country") == "Region-blocked"
    assert get_short_error_reason("Requested format is not available") == "Format unavailable"
    assert get_short_error_reason("Unknown crash") == "Download error"


def test_network_options_registers_node_runtime(tmp_path):
    """Test _apply_network_options registers node in js_runtimes."""
    cfg = AppConfig(
        bot_token="mock_token",
        allowed_users=set(),
        download_dir=tmp_path / "test_downloads",
    )
    dl = AudioDownloader(config=cfg)

    opts = dl._apply_network_options({})
    assert "js_runtimes" in opts
    assert "node" in opts["js_runtimes"]
