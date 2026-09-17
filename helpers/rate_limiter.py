"""Per-user sliding window rate limiter for spam prevention and fair usage."""

import threading
import time
from collections import defaultdict
from typing import Dict, List

from config import config
from helpers.metrics import metrics


class UserRateLimiter:
    """Sliding-window rate limiter tracking request timestamps per user ID."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._user_requests: Dict[int, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _prune(self, user_id: int, now: float) -> None:
        """Removes timestamps outside the current sliding window."""
        cutoff = now - self.window_seconds
        self._user_requests[user_id] = [
            ts for ts in self._user_requests[user_id] if ts > cutoff
        ]

    def is_allowed(self, user_id: int) -> bool:
        """Determines if a user is permitted to make a request within the rate window.

        Records the request timestamp if allowed.
        """
        now = time.time()
        with self._lock:
            self._prune(user_id, now)
            if len(self._user_requests[user_id]) < self.max_requests:
                self._user_requests[user_id].append(now)
                return True
            metrics.increment("rate_limit_hits")
            return False

    def time_until_allowed(self, user_id: int) -> float:
        """Returns the number of seconds until the user is allowed to make another request."""
        now = time.time()
        with self._lock:
            self._prune(user_id, now)
            timestamps = self._user_requests[user_id]
            if len(timestamps) < self.max_requests:
                return 0.0
            oldest = timestamps[0]
            remaining = (oldest + self.window_seconds) - now
            return max(0.0, remaining)

    def reset(self, user_id: int) -> None:
        """Resets rate limit counter for a specific user."""
        with self._lock:
            self._user_requests.pop(user_id, None)

    def clear(self) -> None:
        """Clears all stored rate limit history."""
        with self._lock:
            self._user_requests.clear()


# Global rate limiter instance initialized from config
rate_limiter = UserRateLimiter(
    max_requests=getattr(config, "user_rate_limit", 5),
    window_seconds=getattr(config, "user_rate_window", 60),
)
