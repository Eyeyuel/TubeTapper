"""High-performance audio caching layer supporting Redis with automatic SQLite fallback.

Caches Telegram file_id mappings for YouTube videos to enable instant (0.2s)
re-delivery of previously processed songs without downloading or CPU conversion.
"""

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Union

from config import config as global_config
from helpers.logger import setup_logger
from helpers.metrics import metrics

logger = setup_logger("cache")

try:
    import redis.asyncio as aioredis
    from redis.exceptions import ConnectionError as RedisConnectionError, RedisError
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    RedisConnectionError = Exception
    RedisError = Exception
    REDIS_AVAILABLE = False


class AudioCacheManager:
    """Manages audio file_id caching using Redis or fallback SQLite database."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        cache_dir: Optional[Union[str, Path]] = None,
        ttl_days: Optional[int] = None,
        enable_sqlite_ttl: bool = False,
    ) -> None:
        self.redis_url = redis_url if redis_url is not None else global_config.redis_url
        self.enable_sqlite_ttl = enable_sqlite_ttl
        self.ttl_seconds = (ttl_days * 86400) if ttl_days is not None else (30 * 86400)
        self.cache_dir = Path(cache_dir or global_config.download_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = self.cache_dir / "cache.db"

        self._redis_client: Optional[Any] = None
        self._is_redis_active: bool = False
        self._local_locks: Dict[str, asyncio.Lock] = {}
        self._lock_dict_mutex = asyncio.Lock()

        # Initialize SQLite schema and migration
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        """Initializes SQLite database and tables for permanent caching and settings."""
        try:
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.executescript(
                    """
                    -- Legacy table support
                    CREATE TABLE IF NOT EXISTS audio_cache (
                        video_id TEXT PRIMARY KEY,
                        file_id TEXT NOT NULL,
                        title TEXT,
                        artist TEXT,
                        duration INTEGER,
                        created_at REAL
                    );

                    -- Modern permanent media cache with composite primary key
                    CREATE TABLE IF NOT EXISTS media_cache (
                        video_id TEXT NOT NULL,
                        format_id TEXT NOT NULL,
                        file_id TEXT NOT NULL,
                        title TEXT,
                        artist TEXT,
                        duration INTEGER,
                        file_size INTEGER DEFAULT 0,
                        created_at REAL,
                        PRIMARY KEY (video_id, format_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_media_lookup ON media_cache (video_id, format_id);
                    CREATE INDEX IF NOT EXISTS idx_media_title ON media_cache (title);

                    -- User preference settings (mode, format, quality)
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id INTEGER NOT NULL,
                        setting_key TEXT NOT NULL,
                        setting_value TEXT NOT NULL,
                        PRIMARY KEY (user_id, setting_key)
                    );
                    """
                )

                # Migrate any existing records from legacy audio_cache to media_cache
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO media_cache (video_id, format_id, file_id, title, artist, duration, file_size, created_at)
                        SELECT 
                            CASE 
                                WHEN INSTR(video_id, '_') > 0 THEN SUBSTR(video_id, 1, INSTR(video_id, '_') - 1)
                                ELSE video_id
                            END as video_id,
                            CASE 
                                WHEN INSTR(video_id, '_') > 0 THEN SUBSTR(video_id, INSTR(video_id, '_') + 1)
                                ELSE 'mp3'
                            END as format_id,
                            file_id, title, artist, duration, 0, created_at
                        FROM audio_cache;
                        """
                    )
                    # Clean up any video formats from legacy audio_cache table so it remains audio-only
                    conn.execute("DELETE FROM audio_cache WHERE video_id LIKE '%_mp4%'")
                except Exception as mig_exc:
                    logger.debug(f"Cache migration notice: {mig_exc}")

                conn.commit()
        except Exception as exc:
            logger.warning(f"Failed to initialize SQLite fallback cache: {exc}")

    async def initialize(self) -> None:
        """Attempts connection to Redis. Falls back to SQLite if unreachable."""
        if not REDIS_AVAILABLE or not self.redis_url:
            self._is_redis_active = False
            logger.info("Using SQLite fallback for audio caching (Redis not configured/installed).")
            return

        try:
            client = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                max_connections=200,
                retry_on_timeout=True,
            )
            await client.ping()
            self._redis_client = client
            self._is_redis_active = True
            logger.info(f"Connected to Redis cache at {self.redis_url}")
        except Exception as exc:
            self._is_redis_active = False
            self._redis_client = None
            logger.warning(
                f"Could not connect to Redis ({exc}). Falling back to SQLite cache at {self.sqlite_path}."
            )

    @property
    def is_redis_active(self) -> bool:
        """Returns True if connected to an active Redis instance."""
        return self._is_redis_active

    async def get(self, video_id: str, format_id: str = "mp3") -> Optional[Dict[str, Any]]:
        """Retrieves cached track info (file_id, metadata) by YouTube video ID and format."""
        if not video_id:
            return None

        cache_key = f"{video_id}_{format_id}"

        # 1. Attempt Redis lookup
        if self._is_redis_active and self._redis_client:
            try:
                key = f"tubetapper:cache:audio:{cache_key}"
                raw = await self._redis_client.get(key)
                if raw:
                    logger.debug(f"Redis cache HIT for {video_id} ({format_id})")
                    metrics.increment("cache_hits")
                    return json.loads(raw)
            except Exception as exc:
                logger.warning(f"Redis get error for {video_id}: {exc}. Checking SQLite...")

        # 2. SQLite lookup
        result = await asyncio.to_thread(self._get_sqlite, video_id, format_id)
        if result is None:
            metrics.increment("cache_misses")
        else:
            metrics.increment("cache_hits")
        return result

    def _get_sqlite(self, video_id: str, format_id: str = "mp3") -> Optional[Dict[str, Any]]:
        """Synchronous SQLite cache lookup with strict format isolation and permanent persistence."""
        cache_key = f"{video_id}_{format_id}"
        is_video_request = format_id.startswith("mp4")
        try:
            with sqlite3.connect(self.sqlite_path) as conn:
                cursor = conn.cursor()

                # 1. Exact match in media_cache by composite primary key (video_id, format_id)
                cursor.execute(
                    """
                    SELECT file_id, title, artist, duration, file_size, created_at 
                    FROM media_cache 
                    WHERE video_id = ? AND format_id = ?
                    """,
                    (video_id, format_id),
                )
                row = cursor.fetchone()

                # 2. Controlled fallbacks ONLY for generic formats (never cross audio/video boundaries)
                if not row:
                    if format_id == "mp3":
                        # If generic MP3 requested without bitrate, match any existing MP3 bitrate in media_cache
                        for alt in ("mp3_320", "mp3_192"):
                            cursor.execute(
                                "SELECT file_id, title, artist, duration, file_size, created_at FROM media_cache WHERE video_id = ? AND format_id = ?",
                                (video_id, alt),
                            )
                            row = cursor.fetchone()
                            if row:
                                break
                    elif format_id == "mp4":
                        # If generic MP4 requested without resolution, match any video resolution in media_cache
                        for alt in ("mp4_720", "mp4_1080", "mp4_480"):
                            cursor.execute(
                                "SELECT file_id, title, artist, duration, file_size, created_at FROM media_cache WHERE video_id = ? AND format_id = ?",
                                (video_id, alt),
                            )
                            row = cursor.fetchone()
                            if row:
                                break

                # 3. Check legacy audio_cache table ONLY for audio requests (never for videos)
                if not row and not is_video_request:
                    # Check exact composite key in audio_cache
                    cursor.execute(
                        "SELECT file_id, title, artist, duration, 0, created_at FROM audio_cache WHERE video_id = ?",
                        (cache_key,),
                    )
                    row = cursor.fetchone()

                    # For MP3 requests, check legacy MP3 bitrates or bare legacy video_id
                    if not row and format_id.startswith("mp3"):
                        candidates = [f"{video_id}_mp3"]
                        if format_id == "mp3":
                            candidates.extend([f"{video_id}_mp3_320", f"{video_id}_mp3_192", video_id])
                        for alt in candidates:
                            cursor.execute(
                                "SELECT file_id, title, artist, duration, 0, created_at FROM audio_cache WHERE video_id = ?",
                                (alt,),
                            )
                            row = cursor.fetchone()
                            if row:
                                break

                if not row:
                    return None

                file_id, title, artist, duration, file_size, created_at = row

                # Optional TTL check (disabled by default for permanent storage)
                if self.enable_sqlite_ttl and self.ttl_seconds > 0 and (time.time() - created_at > self.ttl_seconds):
                    cursor.execute("DELETE FROM media_cache WHERE video_id = ? AND format_id = ?", (video_id, format_id))
                    cursor.execute("DELETE FROM audio_cache WHERE video_id = ?", (cache_key,))
                    conn.commit()
                    return None

                logger.debug(f"SQLite cache HIT for {video_id} ({format_id})")
                return {
                    "file_id": file_id,
                    "title": title,
                    "artist": artist,
                    "duration": duration,
                    "file_size": file_size,
                    "created_at": created_at,
                }
        except Exception as exc:
            logger.warning(f"SQLite cache lookup error for {video_id}: {exc}")
            return None

    async def set(
        self,
        video_id: str,
        file_id: str,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        duration: Optional[int] = None,
        format_id: str = "mp3",
        file_size: int = 0,
    ) -> None:
        """Saves a track's Telegram file_id and metadata permanently to cache."""
        if not video_id or not file_id:
            return

        cache_key = f"{video_id}_{format_id}"
        now = time.time()
        record = {
            "file_id": file_id,
            "title": title or "",
            "artist": artist or "",
            "duration": duration or 0,
            "file_size": file_size or 0,
            "created_at": now,
        }

        # 1. Attempt Redis write (with TTL to protect RAM)
        if self._is_redis_active and self._redis_client:
            try:
                key = f"tubetapper:cache:audio:{cache_key}"
                await self._redis_client.set(key, json.dumps(record), ex=self.ttl_seconds)
                logger.debug(f"Saved {cache_key} to Redis cache.")
            except Exception as exc:
                logger.warning(f"Redis set error for {cache_key}: {exc}. Writing to SQLite fallback...")

        # 2. Always persist permanently to SQLite
        await asyncio.to_thread(self._set_sqlite, video_id, format_id, record)

    def _set_sqlite(self, video_id: str, format_id: str, record: Dict[str, Any]) -> None:
        """Synchronous SQLite cache write across media_cache and legacy audio_cache."""
        cache_key = f"{video_id}_{format_id}"
        try:
            with sqlite3.connect(self.sqlite_path) as conn:
                # Insert to media_cache
                conn.execute(
                    """
                    INSERT OR REPLACE INTO media_cache (video_id, format_id, file_id, title, artist, duration, file_size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        format_id,
                        record["file_id"],
                        record["title"],
                        record["artist"],
                        record["duration"],
                        record.get("file_size", 0),
                        record["created_at"],
                    ),
                )

                # Maintain legacy audio_cache table for backwards compatibility ONLY for audio formats
                if not format_id.startswith("mp4"):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO audio_cache (video_id, file_id, title, artist, duration, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cache_key,
                            record["file_id"],
                            record["title"],
                            record["artist"],
                            record["duration"],
                            record["created_at"],
                        ),
                    )
                conn.commit()
                logger.debug(f"Saved {video_id} ({format_id}) to permanent SQLite cache.")
        except Exception as exc:
            logger.warning(f"SQLite set error for {video_id}: {exc}")

    @asynccontextmanager
    async def acquire_lock(
        self, video_id: str, format_id: str = "mp3", timeout: int = 120
    ) -> AsyncGenerator[bool, None]:
        """Distributed lock context manager to protect against cache stampedes.

        If multiple users request the exact same video concurrently, one acquires the lock
        and downloads, while subsequent users wait and then retrieve the cached file_id.
        """
        cache_key = f"{video_id}_{format_id}"
        lock_key = f"tubetapper:lock:audio:{cache_key}"
        acquired = False

        if self._is_redis_active and self._redis_client:
            try:
                # Redis atomic SET NX EX
                acquired = await self._redis_client.set(lock_key, "1", nx=True, ex=timeout)
                if not acquired:
                    # Wait up to 15s to see if the other worker finishes downloading
                    for _ in range(15):
                        await asyncio.sleep(1.0)
                        cached = await self.get(video_id, format_id)
                        if cached:
                            break
            except Exception as exc:
                logger.debug(f"Redis lock acquisition error: {exc}")
                acquired = True
        else:
            # Local in-memory lock fallback isolated by format
            async with self._lock_dict_mutex:
                if cache_key not in self._local_locks:
                    self._local_locks[cache_key] = asyncio.Lock()
                lock = self._local_locks[cache_key]

            await lock.acquire()
            acquired = True

        try:
            yield acquired
        finally:
            if self._is_redis_active and self._redis_client and acquired:
                try:
                    await self._redis_client.delete(lock_key)
                except Exception:
                    pass
            elif not self._is_redis_active and acquired:
                async with self._lock_dict_mutex:
                    lock = self._local_locks.get(cache_key)
                    if lock and lock.locked():
                        lock.release()

    async def get_user_setting(
        self, user_id: int, key: str, default: Optional[str] = None
    ) -> Optional[str]:
        """Gets a user preference setting (e.g., preferred audio format)."""
        if not user_id or not key:
            return default

        # 1. Try Redis
        if self._is_redis_active and self._redis_client:
            try:
                redis_key = f"tubetapper:user:{user_id}:{key}"
                val = await self._redis_client.get(redis_key)
                if val is not None:
                    return val
            except Exception as exc:
                logger.debug(f"Redis get_user_setting error: {exc}")

        # 2. Try SQLite
        def _read_sqlite() -> Optional[str]:
            try:
                with sqlite3.connect(self.sqlite_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT setting_value FROM user_settings WHERE user_id = ? AND setting_key = ?",
                        (user_id, key),
                    )
                    row = cursor.fetchone()
                    return row[0] if row else default
            except Exception:
                return default

        return await asyncio.to_thread(_read_sqlite)

    async def set_user_setting(self, user_id: int, key: str, value: str) -> None:
        """Saves a user preference setting to Redis and SQLite."""
        if not user_id or not key:
            return

        # 1. Write to Redis
        if self._is_redis_active and self._redis_client:
            try:
                redis_key = f"tubetapper:user:{user_id}:{key}"
                await self._redis_client.set(redis_key, str(value))
            except Exception as exc:
                logger.debug(f"Redis set_user_setting error: {exc}")

        # 2. Write to SQLite
        def _write_sqlite() -> None:
            try:
                with sqlite3.connect(self.sqlite_path) as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO user_settings (user_id, setting_key, setting_value)
                        VALUES (?, ?, ?)
                        """,
                        (user_id, key, str(value)),
                    )
                    conn.commit()
            except Exception as exc:
                logger.debug(f"SQLite set_user_setting error: {exc}")

        await asyncio.to_thread(_write_sqlite)

    async def close(self) -> None:
        """Closes Redis connections cleanly."""
        if self._redis_client:
            try:
                await self._redis_client.close()
            except Exception:
                pass
            self._redis_client = None
            self._is_redis_active = False


# Global singleton cache instance
cache_manager = AudioCacheManager()
