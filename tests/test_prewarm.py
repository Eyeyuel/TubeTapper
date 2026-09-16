"""Tests for hot-track cache pre-warming script."""

from unittest.mock import MagicMock, patch
import pytest

from scripts.prewarm_cache import prewarm_trending_tracks


@pytest.mark.asyncio
async def test_prewarm_trending_tracks_dry_run():
    """Test pre-warming in dry-run mode identifies tracks without downloading."""
    mock_results = [
        {"id": "prewarm_1", "title": "Trending Song 1", "url": "https://yt.com?v=prewarm_1"},
        {"id": "prewarm_2", "title": "Trending Song 2", "url": "https://yt.com?v=prewarm_2"},
    ]

    with patch("scripts.prewarm_cache.AudioDownloader.search_youtube", return_value=mock_results):
        count = await prewarm_trending_tracks(
            queries=["Test Query"],
            max_per_query=2,
            dry_run=True,
        )
        assert count == 2
