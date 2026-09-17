"""Telegram API retry wrapper with exponential backoff and rate limit handling."""

import asyncio
from typing import Any, Callable, Optional, TypeVar

import telegram.error
from helpers.logger import setup_logger

logger = setup_logger("telegram_retry")

T = TypeVar("T")


async def send_with_retry(
    coro_func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> Any:
    """Executes a Telegram API call with automatic retry on rate limits and network errors.

    Args:
        coro_func: The async Telegram API method or coroutine-returning callable.
        *args: Positional arguments to pass to coro_func.
        max_retries: Maximum number of retry attempts before raising.
        base_delay: Initial delay multiplier in seconds for exponential backoff.
        **kwargs: Keyword arguments to pass to coro_func.

    Returns:
        The result returned by coro_func.

    Raises:
        The last caught exception if all retry attempts are exhausted.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            # Reset file stream pointers if retrying file uploads
            if attempt > 0:
                for arg in args:
                    if hasattr(arg, "seek") and callable(arg.seek):
                        try:
                            arg.seek(0)
                        except Exception:
                            pass
                for v in kwargs.values():
                    if hasattr(v, "seek") and callable(v.seek):
                        try:
                            v.seek(0)
                        except Exception:
                            pass

            res = coro_func(*args, **kwargs)
            if asyncio.iscoroutine(res):
                return await res
            return res
        except telegram.error.RetryAfter as exc:
            last_exc = exc
            if attempt >= max_retries:
                logger.error(
                    f"Telegram RetryAfter limit reached after {max_retries} retries: {exc}"
                )
                raise
            retry_val = exc.retry_after
            if hasattr(retry_val, "total_seconds"):
                delay = retry_val.total_seconds() + 0.5
            else:
                delay = float(retry_val) + 0.5
            logger.warning(
                f"Telegram rate limited (RetryAfter: {retry_val}s). "
                f"Retrying attempt {attempt + 1}/{max_retries} in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
        except (telegram.error.TimedOut, telegram.error.NetworkError) as exc:
            last_exc = exc
            if attempt >= max_retries:
                logger.error(
                    f"Telegram network/timeout error after {max_retries} retries: {exc}"
                )
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"Telegram request failed ({exc.__class__.__name__}: {exc}). "
                f"Retrying attempt {attempt + 1}/{max_retries} in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)

    if last_exc:
        raise last_exc
