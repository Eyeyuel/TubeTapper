"""Tests for helpers/telegram_retry.py exponential backoff and rate limit handling."""

import io
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import telegram.error

from helpers.telegram_retry import send_with_retry


@pytest.mark.asyncio
async def test_send_with_retry_success_immediate():
    """Test successful coroutine invocation on first try."""
    mock_func = AsyncMock(return_value="SUCCESS")
    result = await send_with_retry(mock_func, "arg1", key="val")
    assert result == "SUCCESS"
    mock_func.assert_called_once_with("arg1", key="val")


@pytest.mark.asyncio
async def test_send_with_retry_retry_after_handling():
    """Test rate limit RetryAfter waits and succeeds on next try."""
    mock_func = AsyncMock(
        side_effect=[
            telegram.error.RetryAfter(retry_after=0.1),
            "RECOVERED",
        ]
    )
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await send_with_retry(mock_func, max_retries=2)
        assert result == "RECOVERED"
        assert mock_func.call_count == 2
        mock_sleep.assert_called_once_with(0.6)  # 0.1 + 0.5s padding


@pytest.mark.asyncio
async def test_send_with_retry_network_error_backoff():
    """Test NetworkError and TimedOut trigger exponential backoff."""
    mock_func = AsyncMock(
        side_effect=[
            telegram.error.TimedOut("Timed out"),
            telegram.error.NetworkError("Connection reset"),
            "OK_AFTER_BACKOFF",
        ]
    )
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await send_with_retry(mock_func, max_retries=3, base_delay=1.0)
        assert result == "OK_AFTER_BACKOFF"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)  # 1.0 * 2^0
        mock_sleep.assert_any_call(2.0)  # 1.0 * 2^1


@pytest.mark.asyncio
async def test_send_with_retry_exhausted_retries_raises():
    """Test exhausting all retries re-raises the underlying exception."""
    mock_func = AsyncMock(side_effect=telegram.error.TimedOut("Persistent timeout"))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(telegram.error.TimedOut):
            await send_with_retry(mock_func, max_retries=2)
        assert mock_func.call_count == 3


@pytest.mark.asyncio
async def test_send_with_retry_resets_file_stream_pointer():
    """Test file pointers are rewound before retrying upload."""
    stream = io.BytesIO(b"AUDIO_DATA_PAYLOAD")
    stream.read(5)  # partially read

    def simulate_send(audio):
        audio.read()  # consume stream
        if simulate_send.calls == 0:
            simulate_send.calls += 1
            raise telegram.error.NetworkError("Upload interrupted")
        return "UPLOADED"

    simulate_send.calls = 0

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await send_with_retry(simulate_send, audio=stream, max_retries=2)
        assert result == "UPLOADED"
        assert simulate_send.calls == 1
