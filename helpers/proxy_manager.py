"""Anti-Ban Proxy Pool Manager for rotating requests across residential and datacenter IPs."""

import itertools
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from helpers.logger import setup_logger

logger = setup_logger("proxy_manager")


class ProxyManager:
    """Manages rotating proxy gateways and proxy pools to prevent YouTube rate-limits."""

    def __init__(
        self,
        single_proxy: Optional[str] = None,
        proxy_pool: Optional[str] = None,
        cooldown_seconds: int = 180,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._proxies: List[str] = []
        self._proxy_iterator: Optional[itertools.cycle] = None
        self._cooldowns: Dict[str, float] = {}

        # 1. Check if a proxy pool is defined (comma-separated or file path)
        if proxy_pool:
            pool_str = proxy_pool.strip()
            # If it's a file path
            if Path(pool_str).is_file():
                try:
                    with open(pool_str, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                        self._proxies.extend(lines)
                except Exception as exc:
                    logger.warning(f"Failed to read proxy pool file {pool_str}: {exc}")
            else:
                # Comma or newline separated string
                for item in pool_str.replace("\n", ",").split(","):
                    cleaned = item.strip()
                    if cleaned:
                        self._proxies.append(cleaned)

        # 2. Fallback to single proxy / rotating proxy gateway if provided
        if single_proxy and single_proxy.strip() and single_proxy.strip() not in self._proxies:
            self._proxies.append(single_proxy.strip())

        if self._proxies:
            self._proxy_iterator = itertools.cycle(self._proxies)
            logger.info(f"Initialized ProxyManager with {len(self._proxies)} proxy endpoints.")

    @property
    def total_proxies(self) -> int:
        """Returns total configured proxies in the pool."""
        return len(self._proxies)

    @property
    def is_active(self) -> bool:
        """Returns True if any proxy is configured."""
        return bool(self._proxies)

    def get_proxy(self) -> Optional[str]:
        """Returns the next healthy proxy in round-robin sequence."""
        if not self._proxies or not self._proxy_iterator:
            return None

        now = time.time()
        # Scan up to the length of proxies to find one that is not in cooldown
        for _ in range(len(self._proxies)):
            candidate = next(self._proxy_iterator)
            cooling_until = self._cooldowns.get(candidate, 0)
            if now >= cooling_until:
                return candidate

        # If all proxies are currently in cooldown, return candidate anyway as best-effort
        return next(self._proxy_iterator)

    def report_failure(self, proxy_url: Optional[str]) -> None:
        """Puts a proxy into temporary cooldown upon HTTP 429 or network block."""
        if not proxy_url:
            return
        self._cooldowns[proxy_url] = time.time() + self.cooldown_seconds
        logger.warning(
            f"Proxy {proxy_url.split('@')[-1]} put into cooldown for {self.cooldown_seconds}s."
        )

    def report_success(self, proxy_url: Optional[str]) -> None:
        """Clears failure cooldown on successful download."""
        if proxy_url in self._cooldowns:
            del self._cooldowns[proxy_url]


# Singleton instance initialized from global config
from config import config as global_config

proxy_manager = ProxyManager(
    single_proxy=global_config.youtube_proxy,
    proxy_pool=global_config.youtube_proxy_pool,
)
