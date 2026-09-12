"""Download concurrency limiter and job queue manager.

Guarantees server CPU, RAM, and FFmpeg transcoding processes are bounded
by MAX_CONCURRENT_DOWNLOADS while notifying queued users of their live position.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable, Coroutine, Optional

from config import config as global_config
from helpers.logger import setup_logger

logger = setup_logger("queue")


class DownloadQueueManager:
    """Manages bounded download concurrency and waiting queue positions."""

    def __init__(self, max_concurrent: Optional[int] = None) -> None:
        self.max_concurrent = (
            max_concurrent
            if max_concurrent is not None
            else global_config.max_concurrent_downloads
        )
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._lock = asyncio.Lock()
        self._active_count: int = 0
        self._waiting_count: int = 0

    @property
    def active_count(self) -> int:
        """Returns the number of downloads currently in progress."""
        return self._active_count

    @property
    def waiting_count(self) -> int:
        """Returns the number of jobs currently waiting in line."""
        return self._waiting_count

    @property
    def available_slots(self) -> int:
        """Returns the number of free download slots."""
        return max(0, self.max_concurrent - self._active_count)

    @asynccontextmanager
    async def acquire_slot(
        self,
        notify_callback: Optional[Callable[[int], Coroutine[None, None, None]]] = None,
    ) -> AsyncGenerator[None, None]:
        """Acquires a download slot, notifying the user if queued behind others."""
        is_queued = False
        queue_pos = 0

        async with self._lock:
            if self._active_count >= self.max_concurrent:
                self._waiting_count += 1
                queue_pos = self._waiting_count
                is_queued = True
                logger.info(
                    f"Download queued: position #{queue_pos} (active: {self._active_count}/{self.max_concurrent})"
                )

        if is_queued and notify_callback:
            try:
                await notify_callback(queue_pos)
            except Exception as exc:
                logger.debug(f"Queue notification callback error: {exc}")

        # Wait for available semaphore slot
        await self._semaphore.acquire()

        async with self._lock:
            if is_queued:
                self._waiting_count -= 1
            self._active_count += 1
            logger.debug(
                f"Slot acquired (active: {self._active_count}/{self.max_concurrent}, waiting: {self._waiting_count})"
            )

        try:
            yield
        finally:
            async with self._lock:
                self._active_count = max(0, self._active_count - 1)
                logger.debug(
                    f"Slot released (active: {self._active_count}/{self.max_concurrent}, waiting: {self._waiting_count})"
                )
            self._semaphore.release()


# Global singleton queue manager
queue_manager = DownloadQueueManager()
