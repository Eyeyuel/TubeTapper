"""Tests for helpers/rate_limiter.py sliding window user rate limiting."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from helpers.rate_limiter import UserRateLimiter, rate_limiter
from bot import restricted


def test_user_rate_limiter_basic_allowance():
    """Test rate limiter allows requests up to max_requests."""
    limiter = UserRateLimiter(max_requests=3, window_seconds=10)
    user_id = 99901

    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    # 4th request within window is rejected
    assert limiter.is_allowed(user_id) is False
    assert limiter.time_until_allowed(user_id) > 0


def test_user_rate_limiter_sliding_window_expiration():
    """Test rate limiter sliding window frees up slots as timestamps expire."""
    limiter = UserRateLimiter(max_requests=2, window_seconds=1)
    user_id = 99902

    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is False

    # Simulate 1.1s passage
    with patch("time.time", return_value=time.time() + 1.1):
        assert limiter.is_allowed(user_id) is True
        assert limiter.time_until_allowed(user_id) == 0.0


def test_user_rate_limiter_independent_users():
    """Test rate limits are strictly isolated between different user IDs."""
    limiter = UserRateLimiter(max_requests=1, window_seconds=10)
    assert limiter.is_allowed(1001) is True
    assert limiter.is_allowed(1001) is False
    assert limiter.is_allowed(1002) is True


@pytest.mark.asyncio
async def test_restricted_decorator_blocks_rate_limited_user():
    """Test @restricted decorator rejects rate-limited users with informative message."""
    mock_func = AsyncMock(return_value="OK")
    decorated = restricted(mock_func, check_rate_limit=True)

    mock_update = MagicMock()
    mock_update.effective_user.id = 88888
    mock_update.callback_query = None
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    # Exhaust rate limiter for this user
    rate_limiter.reset(88888)
    for _ in range(rate_limiter.max_requests):
        rate_limiter.is_allowed(88888)

    # Next attempt through decorated function should be blocked
    result = await decorated(mock_update, mock_context)
    assert result is None
    mock_func.assert_not_called()
    mock_update.effective_message.reply_text.assert_called_once()
    assert "going a bit too fast" in mock_update.effective_message.reply_text.call_args[0][0]

    # Clean up
    rate_limiter.reset(88888)


@pytest.mark.asyncio
async def test_restricted_decorator_exempt_handlers():
    """Test handlers with check_rate_limit=False bypass rate limiting."""
    mock_func = AsyncMock(return_value="START_OK")
    decorated = restricted(mock_func, check_rate_limit=False)

    mock_update = MagicMock()
    mock_update.effective_user.id = 77777
    mock_context = MagicMock()

    # Exhaust rate limiter for this user
    rate_limiter.reset(77777)
    for _ in range(rate_limiter.max_requests):
        rate_limiter.is_allowed(77777)

    result = await decorated(mock_update, mock_context)
    assert result == "START_OK"
    mock_func.assert_called_once()

    # Clean up
    rate_limiter.reset(77777)


@pytest.mark.asyncio
async def test_ui_callbacks_exempt_from_rate_limit():
    """Verify that settings buttons and other UI callbacks are completely exempt from rate limiting."""
    mock_func = AsyncMock(return_value="UI_CLICK_OK")
    decorated = restricted(mock_func, check_rate_limit=True)

    user_id = 66666
    # Exhaust rate limit completely for this user
    rate_limiter.reset(user_id)
    for _ in range(rate_limiter.max_requests):
        rate_limiter.is_allowed(user_id)

    # Test settings callbacks: set_mode, set_aud, set_vid, cancel_pl, cmd_settings
    test_callbacks = [
        "set_mode:ask",
        "set_aud:mp3_320",
        "set_vid:mp4_1080",
        "cancel_pl:1234",
        "cmd_settings",
        "cmd_help",
    ]

    for cb_data in test_callbacks:
        mock_update = MagicMock()
        mock_update.effective_user.id = user_id
        mock_update.callback_query.data = cb_data
        mock_context = MagicMock()

        res = await decorated(mock_update, mock_context)
        assert res == "UI_CLICK_OK", f"UI callback {cb_data} should be exempt from rate limiting"

    # Clean up
    rate_limiter.reset(user_id)


@pytest.mark.asyncio
async def test_download_callbacks_still_rate_limited():
    """Verify that actual heavy download callbacks (dl:, fmt:, plfmt:) are still protected by rate limiting."""
    mock_func = AsyncMock(return_value="DOWNLOAD_INIT")
    decorated = restricted(mock_func, check_rate_limit=True)

    user_id = 55555
    # Exhaust rate limit
    rate_limiter.reset(user_id)
    for _ in range(rate_limiter.max_requests):
        rate_limiter.is_allowed(user_id)

    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_update.callback_query.data = "fmt:mp4_720:vid123"
    mock_update.effective_message.reply_text = AsyncMock()
    mock_context = MagicMock()

    res = await decorated(mock_update, mock_context)
    assert res is None, "Heavy download callback should be blocked when user exceeds rate limit"
    mock_update.effective_message.reply_text.assert_called_once()
    assert "going a bit too fast" in mock_update.effective_message.reply_text.call_args[0][0]

    # Clean up
    rate_limiter.reset(user_id)

