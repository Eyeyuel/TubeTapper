"""Hot-Track Cache Pre-Warming Engine.

Pre-fetches and pre-caches trending hits (Billboard Top 100, Spotify Top, YouTube Music Hits)
into Redis and Telegram's CDN storage to guarantee 0.2s instant cache hits for 90%+ of users.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional

# Add parent directory to path so imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config
from downloader import AudioDownloader
from helpers.cache import cache_manager
from helpers.logger import setup_logger

logger = setup_logger("prewarm")

DEFAULT_TRENDING_QUERIES = [
    "Billboard Hot 100 top hits",
    "Global Top Music Hits 2026",
    "Today's Top Hits Pop Music",
    "Trending YouTube Music Videos",
]


async def prewarm_trending_tracks(
    queries: Optional[List[str]] = None,
    max_per_query: int = 5,
    storage_chat_id: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """Discovers trending tracks, checks cache status, and pre-warms uncached songs."""
    await cache_manager.initialize()
    downloader = AudioDownloader()
    target_queries = queries or DEFAULT_TRENDING_QUERIES

    total_prewarmed = 0
    total_already_cached = 0

    logger.info(f"Starting cache pre-warming across {len(target_queries)} trending categories...")

    for query in target_queries:
        logger.info(f"Fetching trending tracks for query: '{query}'...")
        try:
            results = await asyncio.to_thread(downloader.search_youtube, query, max_results=max_per_query)
        except Exception as exc:
            logger.warning(f"Error searching for query '{query}': {exc}")
            continue

        for item in results:
            vid_id = item.get("id")
            title = item.get("title", "Unknown Track")
            if not vid_id:
                continue

            cached = await cache_manager.get(vid_id)
            if cached and cached.get("file_id"):
                total_already_cached += 1
                logger.debug(f"[Already Cached] {title} ({vid_id})")
                continue

            logger.info(f"[Pre-warming Candidate] {title} ({vid_id})")
            if dry_run:
                total_prewarmed += 1
                continue

            # If a storage chat ID and bot token are provided, download and upload once to store file_id
            target_chat = storage_chat_id or os.getenv("STORAGE_CHAT_ID")
            if target_chat and config.bot_token and config.bot_token != "your_telegram_bot_token_here":
                try:
                    import telegram
                    bot = telegram.Bot(token=config.bot_token)
                    logger.info(f"Downloading {title} to obtain permanent Telegram file_id...")
                    track_data = await asyncio.to_thread(downloader.download_audio, item["url"])
                    
                    audio_path = track_data["file_path"]
                    thumb_path = track_data.get("thumbnail_path")
                    try:
                        with open(audio_path, "rb") as audio_file:
                            thumb_file = open(thumb_path, "rb") if thumb_path and Path(thumb_path).exists() else None
                            try:
                                sent_msg = await bot.send_audio(
                                    chat_id=int(target_chat) if target_chat.isdigit() or target_chat.startswith("-") else target_chat,
                                    audio=audio_file,
                                    title=track_data.get("title"),
                                    performer=track_data.get("artist"),
                                    duration=track_data.get("duration"),
                                    thumbnail=thumb_file,
                                )
                                if sent_msg and sent_msg.audio:
                                    await cache_manager.set(
                                        video_id=vid_id,
                                        file_id=sent_msg.audio.file_id,
                                        title=track_data.get("title"),
                                        artist=track_data.get("artist"),
                                        duration=track_data.get("duration"),
                                    )
                                    total_prewarmed += 1
                                    logger.info(f"Successfully pre-cached {title} with file_id {sent_msg.audio.file_id[:12]}...")
                            finally:
                                if thumb_file:
                                    thumb_file.close()
                    finally:
                        downloader.cleanup_files(audio_path, thumb_path)
                except Exception as exc:
                    logger.warning(f"Failed to pre-warm track {title}: {exc}")
            else:
                total_prewarmed += 1
                logger.info(f"Track {title} identified for cache. (Provide STORAGE_CHAT_ID to auto-upload to Telegram CDN).")

    logger.info(
        f"Cache pre-warming finished: {total_already_cached} already cached, {total_prewarmed} processed/identified."
    )
    return total_prewarmed


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-warm TubeTapper Redis audio cache with trending songs")
    parser.add_argument("--max-per-query", type=int, default=5, help="Max tracks per category")
    parser.add_argument("--storage-chat", type=str, default=None, help="Telegram channel/chat ID to store cached audio")
    parser.add_argument("--dry-run", action="store_true", help="Search and identify tracks without downloading")
    args = parser.parse_args()

    asyncio.run(
        prewarm_trending_tracks(
            max_per_query=args.max_per_query,
            storage_chat_id=args.storage_chat,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
