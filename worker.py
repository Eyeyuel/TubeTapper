"""Distributed asynchronous worker node for processing YouTube downloads and audio transcoding."""

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

import telegram
from telegram.constants import ChatAction

from config import config
from downloader import AudioDownloader
from helpers.cache import cache_manager
from helpers.cleanup import cleanup_orphaned_downloads
from helpers.logger import setup_logger

logger = setup_logger("worker")


def get_worker_bot() -> telegram.Bot:
    """Instantiates a Telegram Bot client configured with local server and local mode if enabled."""
    kwargs = {}
    if config.telegram_api_server_url:
        kwargs["base_url"] = config.telegram_api_server_url
    if config.telegram_local_mode:
        kwargs["local_mode"] = True
    return telegram.Bot(token=config.bot_token, **kwargs)


async def process_download_job(ctx: Dict[str, Any], job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Processes a background download and uploads the converted audio to Telegram."""
    chat_id = job_data["chat_id"]
    url = job_data["url"]
    reply_to = job_data.get("reply_to_message_id")
    audio_format = job_data.get("audio_format", "mp3")
    bitrate = job_data.get("bitrate", 192)
    status_msg_id = job_data.get("status_message_id")

    downloader = AudioDownloader()
    bot = ctx.get("bot") or get_worker_bot()

    # 1. Check cache first
    video_id = downloader.extract_video_id(url)
    if video_id:
        cached = await cache_manager.get(video_id)
        if cached and cached.get("file_id"):
            logger.info(f"[Worker] Instant cache delivery for {video_id} to chat {chat_id}")
            await bot.send_audio(
                chat_id=chat_id,
                audio=cached["file_id"],
                title=cached.get("title"),
                performer=cached.get("artist"),
                duration=cached.get("duration"),
                reply_to_message_id=reply_to,
            )
            if status_msg_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
                except Exception:
                    pass
            return {"status": "cached", "video_id": video_id}

    # 2. Download audio track
    logger.info(f"[Worker] Downloading {url} for chat {chat_id}...")
    try:
        track_data = await asyncio.to_thread(
            downloader.download_audio, url, None, audio_format, bitrate
        )
    except Exception as exc:
        logger.error(f"[Worker] Download failed for {url}: {exc}")
        if status_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"❌ *Download failed:*\n`{str(exc)[:150]}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return {"status": "error", "error": str(exc)}

    # Safeguard file size limit
    if track_data.get("exceeds_limit"):
        size_mb = track_data.get("filesize", 0) // (1024 * 1024)
        downloader.cleanup_files(track_data.get("file_path"), track_data.get("thumbnail_path"))
        if status_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"⚠️ *File too large:* Audio file is {size_mb} MB, exceeding maximum bot limits.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return {"status": "too_large", "filesize": size_mb}

    # 3. Upload to Telegram
    audio_path = track_data["file_path"]
    thumb_path = track_data.get("thumbnail_path")

    try:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
        except Exception:
            pass

        with open(audio_path, "rb") as audio_file:
            thumb_file = (
                open(thumb_path, "rb")
                if thumb_path and Path(thumb_path).exists()
                else None
            )
            try:
                sent_msg = await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    title=track_data.get("title"),
                    performer=track_data.get("artist"),
                    duration=track_data.get("duration"),
                    thumbnail=thumb_file,
                    reply_to_message_id=reply_to,
                )
            finally:
                if thumb_file:
                    thumb_file.close()

        # Cache file_id
        if video_id and sent_msg and sent_msg.audio:
            await cache_manager.set(
                video_id=video_id,
                file_id=sent_msg.audio.file_id,
                title=track_data.get("title"),
                artist=track_data.get("artist"),
                duration=track_data.get("duration"),
            )

        if status_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
            except Exception:
                pass

        logger.info(f"[Worker] Successfully delivered {track_data.get('title')} to chat {chat_id}")
        return {"status": "success", "title": track_data.get("title")}
    except Exception as exc:
        logger.error(f"[Worker] Upload failed for {url}: {exc}")
        if status_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"❌ *Upload failed:*\n`{str(exc)[:150]}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return {"status": "upload_error", "error": str(exc)}
    finally:
        downloader.cleanup_files(audio_path, thumb_path)
        cleanup_orphaned_downloads(config.download_dir)


async def startup(ctx: Dict[str, Any]) -> None:
    """Initializes async resources for the worker process."""
    await cache_manager.initialize()
    ctx["bot"] = get_worker_bot()
    logger.info("Worker node initialized and ready to consume jobs from Redis queue.")


async def shutdown(ctx: Dict[str, Any]) -> None:
    """Cleans up worker connections on shutdown."""
    await cache_manager.close()
    logger.info("Worker node shutdown cleanly.")


# ARQ Worker Settings
try:
    from arq.connections import RedisSettings

    class WorkerSettings:
        functions = [process_download_job]
        on_startup = startup
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(config.redis_url or "redis://localhost:6379/0")
        max_jobs = config.max_concurrent_downloads
        job_timeout = 600
        keep_result = 60
except ImportError:
    WorkerSettings = None
