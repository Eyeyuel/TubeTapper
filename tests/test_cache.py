"""Tests for AudioCacheManager covering SQLite fallback, Redis mode, TTL, and locking."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from helpers.cache import AudioCacheManager


@pytest.mark.asyncio
async def test_sqlite_cache_set_and_get(tmp_path):
    """Test setting and retrieving cached audio file_id using SQLite fallback."""
    cache = AudioCacheManager(redis_url="", cache_dir=tmp_path, ttl_days=30)
    await cache.initialize()

    assert not cache.is_redis_active

    # Initially empty
    result = await cache.get("test_video_123")
    assert result is None

    # Save to cache
    await cache.set(
        video_id="test_video_123",
        file_id="CQACAgQAAxkBAAIC123",
        title="Test Song",
        artist="Test Artist",
        duration=180,
    )

    # Retrieve from cache
    cached = await cache.get("test_video_123")
    assert cached is not None
    assert cached["file_id"] == "CQACAgQAAxkBAAIC123"
    assert cached["title"] == "Test Song"
    assert cached["artist"] == "Test Artist"
    assert cached["duration"] == 180


@pytest.mark.asyncio
async def test_sqlite_cache_ttl_expiration(tmp_path):
    """Test that expired records are purged when TTL is explicitly enabled."""
    cache = AudioCacheManager(redis_url="", cache_dir=tmp_path, ttl_days=0, enable_sqlite_ttl=True)
    cache.ttl_seconds = 1
    await cache.initialize()

    await cache.set(
        video_id="expiring_vid",
        file_id="FILE_EXP",
        title="Expiring Track",
    )

    # Immediately available
    cached = await cache.get("expiring_vid")
    assert cached is not None

    # Wait for TTL expiry
    await asyncio.sleep(1.1)

    # Should now be expired and deleted
    expired = await cache.get("expiring_vid")
    assert expired is None


@pytest.mark.asyncio
async def test_sqlite_cache_permanent_persistence(tmp_path):
    """Test that records are permanently preserved by default."""
    cache = AudioCacheManager(redis_url="", cache_dir=tmp_path)
    cache.ttl_seconds = 1
    await cache.initialize()

    await cache.set(
        video_id="perm_vid",
        file_id="PERM_FILE",
        title="Permanent Track",
        format_id="mp4_720",
    )

    await asyncio.sleep(1.1)

    # Should remain accessible and permanent
    cached = await cache.get("perm_vid", format_id="mp4_720")
    assert cached is not None
    assert cached["file_id"] == "PERM_FILE"


@pytest.mark.asyncio
async def test_composite_key_formats_coexist(tmp_path):
    """Test that audio and video formats for the same video_id coexist independently."""
    cache = AudioCacheManager(redis_url="", cache_dir=tmp_path)
    await cache.initialize()

    # Store MP3 and MP4 for the exact same video_id
    await cache.set("multi_vid", "FILE_MP3", title="Multi Vid", format_id="mp3_320")
    await cache.set("multi_vid", "FILE_MP4", title="Multi Vid", format_id="mp4_1080")

    mp3_cached = await cache.get("multi_vid", format_id="mp3_320")
    mp4_cached = await cache.get("multi_vid", format_id="mp4_1080")

    assert mp3_cached is not None
    assert mp3_cached["file_id"] == "FILE_MP3"
    assert mp4_cached is not None
    assert mp4_cached["file_id"] == "FILE_MP4"


@pytest.mark.asyncio
async def test_cache_stampede_lock(tmp_path):
    """Test stampede lock acquires and releases safely."""
    cache = AudioCacheManager(redis_url="", cache_dir=tmp_path)
    await cache.initialize()

    executed = False
    async with cache.acquire_lock("lock_video_1"):
        executed = True

    assert executed


@pytest.mark.asyncio
async def test_redis_cache_operations(tmp_path):
    """Test Redis cache operations when Redis client is mocked."""
    cache = AudioCacheManager(redis_url="redis://fake:6379/0", cache_dir=tmp_path)
    
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(
        return_value='{"file_id": "REDIS_FILE", "title": "R Title", "artist": "R Artist", "duration": 200, "created_at": 1000}'
    )
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=True)
    mock_redis.close = AsyncMock()

    with patch("helpers.cache.aioredis.from_url", return_value=mock_redis):
        await cache.initialize()
        assert cache.is_redis_active

        # Test GET
        cached = await cache.get("vid_redis_1")
        assert cached is not None
        assert cached["file_id"] == "REDIS_FILE"
        mock_redis.get.assert_called_once_with("tubetapper:cache:audio:vid_redis_1_mp3")

        # Test SET
        await cache.set("vid_redis_2", file_id="NEW_FILE", title="New Title")
        mock_redis.set.assert_called_once()

        # Test Lock
        async with cache.acquire_lock("vid_redis_lock"):
            pass
        mock_redis.delete.assert_called_once_with("tubetapper:lock:audio:vid_redis_lock_mp3")

        await cache.close()
        assert not cache.is_redis_active
