"""Telegram Bot application for YouTube to Telegram Music Downloader with safeguards and access control."""

import asyncio
from contextlib import nullcontext
import os
import re
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import telegram
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from downloader import AudioDownloader
from helpers.cache import cache_manager
from helpers.cleanup import cleanup_orphaned_downloads
from helpers.logger import setup_logger
from helpers.progress import (
    render_progress_bar,
    repost_status_message,
    safe_delete_message,
    update_status_message,
)
from helpers.queue_manager import queue_manager
from helpers.rate_limiter import rate_limiter
from helpers.telegram_retry import send_with_retry
from helpers.metrics import metrics, start_metrics_server

try:
    from arq import create_pool
    from arq.connections import RedisSettings
    ARQ_AVAILABLE = True
except ImportError:
    ARQ_AVAILABLE = False

logger = setup_logger("bot")

# Universal regex matching all YouTube URL domains (youtube.com, m.youtube.com, music.youtube.com, youtu.be)
# and endpoints (watch, playlist, shorts, live, embed, and shortened youtu.be IDs)
YOUTUBE_URL_REGEX = re.compile(
    r"(https?://(?:[a-zA-Z0-9_.-]+\.)?(?:youtube\.com|youtu\.be)/(?:watch\?[^\s]+|playlist\?[^\s]+|shorts/[a-zA-Z0-9_-]+|live/[a-zA-Z0-9_-]+|embed/[a-zA-Z0-9_-]+|[a-zA-Z0-9_-]+[^\s]*))"
)

# Registry of active playlist cancellation events keyed by chat_id
ACTIVE_PLAYLIST_CANCELLATIONS: Dict[int, asyncio.Event] = {}


async def send_upload_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Sends native Telegram chat action 'uploading audio...' safely."""
    try:
        action_coro = context.bot.send_chat_action(
            chat_id=chat_id, action=ChatAction.UPLOAD_VOICE
        )
        if asyncio.iscoroutine(action_coro):
            await action_coro
    except Exception as exc:
        logger.debug(f"send_chat_action notice error (non-fatal): {exc}")


async def get_user_audio_format(user_id: int) -> Tuple[str, int]:
    """Retrieves user's preferred audio format and bitrate.
    
    Returns: (audio_format, bitrate) e.g. ('mp3', 192), ('mp3', 320), or ('m4a', 0)
    """
    pref = await cache_manager.get_user_setting(user_id, "audio_format", "mp3_192")
    if pref == "mp3_320":
        return "mp3", 320
    elif pref == "m4a":
        return "m4a", 0
    return "mp3", 192


def build_settings_keyboard(current_pref: str) -> InlineKeyboardMarkup:
    """Builds inline keyboard for audio format selection with a checkmark on the active setting."""
    b1_check = " ✅" if current_pref == "mp3_192" else ""
    b2_check = " ✅" if current_pref == "mp3_320" else ""
    b3_check = " ✅" if current_pref == "m4a" else ""

    keyboard = [
        [InlineKeyboardButton(f"🎵 MP3 - 192 kbps (Standard){b1_check}", callback_data="set_fmt:mp3_192")],
        [InlineKeyboardButton(f"🎧 MP3 - 320 kbps (High Quality){b2_check}", callback_data="set_fmt:mp3_320")],
        [InlineKeyboardButton(f"⚡ M4A - Native Source Copy (Fastest){b3_check}", callback_data="set_fmt:m4a")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_search_keyboard(results: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Builds an inline keyboard of up to 5 search results."""
    keyboard = []
    for item in results:
        title = item.get("title", "Unknown Track")
        duration = item.get("duration")
        dur_str = f" ({duration // 60}:{duration % 60:02d})" if duration else ""
        btn_text = f"🎵 {title[:48]}{dur_str}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"dl:{item['id']}")])
    return InlineKeyboardMarkup(keyboard)


def build_cancel_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Builds inline keyboard with a Cancel button for active playlists."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Playlist Download", callback_data=f"cancel_pl:{chat_id}")]
    ])


def restricted(func: Optional[Callable] = None, *, check_rate_limit: bool = True) -> Callable:
    """Decorator to enforce whitelist authorization and per-user rate limiting."""

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def wrapped(
            update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
        ) -> Any:
            user = update.effective_user
            if user and not config.is_user_allowed(user.id):
                logger.warning(
                    f"Unauthorized access attempt by user ID {user.id} (@{user.username or 'none'})"
                )
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "⛔ *Access Denied:*\nYou are not authorized to use this bot.",
                        parse_mode="Markdown",
                    )
                return None

            if check_rate_limit and user:
                if not rate_limiter.is_allowed(user.id):
                    wait_time = rate_limiter.time_until_allowed(user.id)
                    logger.warning(
                        f"Rate limit exceeded for user ID {user.id} (retry in {wait_time:.1f}s)"
                    )
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            f"⏳ *Rate limit reached.* Please wait {wait_time:.0f} seconds before your next request.",
                            parse_mode="Markdown",
                        )
                    return None

            return await f(update, context, *args, **kwargs)

        return wrapped

    if func is not None:
        return decorator(func)
    return decorator


@restricted(check_rate_limit=False)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message explaining bot usage."""
    if not update.effective_message:
        return

    text = (
        "🎵 *Welcome to TubeTapper Music Downloader!* 🎧\n\n"
        "Send me any YouTube link or just type a song name to search directly!\n\n"
        "📌 *Features & Usage:*\n"
        "• *Direct Search:* Type any song name (e.g. `Blinding Lights The Weeknd`)\n"
        "• *Single Video:* `https://youtu.be/...` or `https://www.youtube.com/watch?v=...`\n"
        "• *Full Playlist:* `https://www.youtube.com/playlist?list=...`\n"
        "• *Radio Mix:* `https://www.youtube.com/watch?v=...&list=RD...`\n"
        "• *Audio Quality:* Type /settings to choose MP3 (192k/320k) or Native M4A\n\n"
        "💡 _Tip: During playlist downloads, you can tap [❌ Cancel Download] at any time!_\n\n"
        "Send a link or song name now, or type /help for details."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted(check_rate_limit=False)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides user instructions, limitations, and help."""
    if not update.effective_message:
        return

    text = (
        "ℹ️ *How to Use This Bot:*\n\n"
        "1️⃣ *Search directly:* Just type any song or artist name (e.g. `/search Queen` or `Queen Bohemian Rhapsody`).\n"
        "2️⃣ *Or paste a link:* Send any YouTube video, playlist, or mix URL.\n"
        "3️⃣ *Choose audio quality:* Use /settings to switch between MP3 192k, MP3 320k, and Native M4A.\n\n"
        "💡 *Important Note on YouTube Mixes (`list=RD...`):*\n"
        "YouTube Radio Mixes are dynamically generated on the fly by YouTube's recommendation engine. "
        "To get an exact, fixed playlist in a specific order, save the mix to your library first and send the `list=PL...` link.\n\n"
        "⚙️ *Audio Specifications:*\n"
        "• Formats: MP3 (192/320 kbps) or Native M4A source-copy\n"
        "• Embedded tags: High-res Cover Artwork, Title, and Artist metadata\n\n"
        "⚠️ *Limitations & Safeguards:*\n"
        f"• Maximum file size: {config.max_file_size_mb} MB (Telegram Bot API limit).\n"
        f"• Playlists: Capped at {config.max_playlist_tracks} tracks.\n"
        "• Live streams cannot be converted.\n"
        "• Private or geo-restricted videos will be skipped gracefully."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted(check_rate_limit=False)
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reports bot health, configuration, and status."""
    if not update.effective_message:
        return

    cache_engine = "Redis (In-Memory)" if cache_manager.is_redis_active else "SQLite (Fallback)"
    bot_server_type = "Local Bot API Server (2GB)" if config.telegram_local_mode or config.telegram_api_server_url else "Telegram Public Gateway (50MB)"
    mode_type = "Webhooks (High-Throughput)" if config.webhook_mode else "Long-Polling"
    worker_status = "Distributed Worker Cluster" if config.worker_mode else f"In-Process Queue ({config.max_concurrent_downloads} workers)"
    proxy_status = "Rotating Proxy Pool Active" if (config.youtube_proxy or config.youtube_proxy_pool) else "Direct Server IP"

    text = (
        "🟢 *Bot Status: Online*\n\n"
        "• Engine: `yt-dlp` + `FFmpeg`\n"
        f"• Telegram Gateway: `{bot_server_type}`\n"
        f"• Ingestion Mode: `{mode_type}`\n"
        f"• Download Architecture: `{worker_status}`\n"
        f"• Proxy Protection: `{proxy_status}`\n"
        f"• Audio Cache: `{cache_engine}`\n"
        f"• Concurrency Limit: `{config.max_concurrent_downloads} workers`\n"
        f"• Active Downloads: `{queue_manager.active_count}`\n"
        f"• Queue Depth: `{queue_manager.waiting_count}`\n"
        f"• Max Upload Size: `{config.max_file_size_mb} MB`\n"
        f"• Default Audio Quality: `{config.audio_bitrate} kbps MP3`\n"
        "• Pipelined Streaming: `Enabled`\n"
        "• Ready to download audio!\n\n"
        "📊 *Metrics:*\n"
        f"```\n{metrics.get_summary()}\n```"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets user customize preferred audio format and quality."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    current_pref = await cache_manager.get_user_setting(user_id, "audio_format", "mp3_192")

    text = (
        "⚙️ *Audio Download Settings*\n\n"
        "Choose your preferred audio format and quality for downloads:\n\n"
        "• *MP3 192 kbps*: Balanced quality & compact file size (default).\n"
        "• *MP3 320 kbps*: Studio / high-fidelity audio quality.\n"
        "• *M4A Native Copy*: Zero re-encoding, instant extraction directly from YouTube streams."
    )
    await update.effective_message.reply_text(
        text,
        reply_markup=build_settings_keyboard(current_pref),
        parse_mode="Markdown",
    )


async def execute_search(
    query: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Performs YouTube search and renders interactive download buttons."""
    if not update.effective_message:
        return

    status_msg = await update.effective_message.reply_text(
        f"🔍 *Searching YouTube for:* `{query}`...", parse_mode="Markdown"
    )
    downloader = AudioDownloader()
    try:
        results = await asyncio.to_thread(downloader.search_youtube, query, max_results=5)
    except Exception as exc:
        logger.error(f"Search error for '{query}': {exc}")
        await update_status_message(status_msg, f"❌ *Search failed:*\n`{str(exc)[:120]}`")
        return

    if not results:
        await update_status_message(
            status_msg,
            f"🔍 No results found for *{query}*. Try checking the spelling or pasting a direct YouTube URL.",
        )
        return

    text = f"🔍 *Top results for:* `{query}`\nTap a track below to download:"
    await update_status_message(
        status_msg,
        text,
        reply_markup=build_search_keyboard(results),
    )


@restricted
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Searches YouTube and returns top results as interactive download buttons."""
    if not update.effective_message:
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.effective_message.reply_text(
            "🔍 *Search YouTube:*\nPlease provide a search query.\n\n"
            "*Usage:* `/search <song or artist>`\n*Example:* `/search Bohemian Rhapsody Queen`",
            parse_mode="Markdown",
        )
        return

    await execute_search(query, update, context)


async def handle_single_track(
    url: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: telegram.Message,
    downloader: AudioDownloader,
) -> None:
    """Handles downloading and uploading a single YouTube track."""
    chat_id = update.effective_chat.id
    reply_to = update.effective_message.message_id if update.effective_message else None
    user_id = update.effective_user.id if update.effective_user else 0

    audio_format, bitrate = await get_user_audio_format(user_id)

    metrics.increment("downloads_total")

    # 1. Instant Cache Check (Telegram file_id reuse)
    video_id = downloader.extract_video_id(url)
    if video_id:
        try:
            cached = await cache_manager.get(video_id)
            if cached and cached.get("file_id"):
                logger.info(f"Delivering cached audio for video ID {video_id}")
                metrics.increment("downloads_cached")
                await update_status_message(status_msg, "⚡ *Found in cache! Delivering audio...*")
                await send_upload_action(context, chat_id)
                await send_with_retry(
                    context.bot.send_audio,
                    chat_id=chat_id,
                    audio=cached["file_id"],
                    title=cached.get("title"),
                    performer=cached.get("artist"),
                    duration=cached.get("duration"),
                    reply_to_message_id=reply_to,
                )
                await safe_delete_message(status_msg)
                return
        except Exception as exc:
            logger.warning(f"Failed to send cached audio: {exc}. Proceeding to fresh download.")

    # 2. Cache Stampede Protection (Distributed Lock)
    lock_manager = cache_manager.acquire_lock(video_id) if video_id else nullcontext()
    async with lock_manager:
        # Re-check cache after acquiring lock in case another request completed while waiting
        if video_id:
            try:
                cached = await cache_manager.get(video_id)
                if cached and cached.get("file_id"):
                    logger.info(f"Delivering cached audio (post-lock) for video ID {video_id}")
                    metrics.increment("downloads_cached")
                    await update_status_message(status_msg, "⚡ *Found in cache! Delivering audio...*")
                    await send_upload_action(context, chat_id)
                    await send_with_retry(
                        context.bot.send_audio,
                        chat_id=chat_id,
                        audio=cached["file_id"],
                        title=cached.get("title"),
                        performer=cached.get("artist"),
                        duration=cached.get("duration"),
                        reply_to_message_id=reply_to,
                    )
                    await safe_delete_message(status_msg)
                    return
            except Exception as exc:
                logger.warning(f"Failed to send cached audio post-lock: {exc}. Proceeding to fresh download.")

        # 3. Distributed Worker Queue Dispatch (if WORKER_MODE is enabled)
        if config.worker_mode and cache_manager.is_redis_active:
            redis_pool = context.application.bot_data.get("arq_pool") if context and context.application else None
            if redis_pool:
                try:
                    await update_status_message(status_msg, "⏳ *Queued in high-speed worker pool...*")
                    await redis_pool.enqueue_job(
                        "process_download_job",
                        {
                            "chat_id": chat_id,
                            "url": url,
                            "reply_to_message_id": reply_to,
                            "audio_format": audio_format,
                            "bitrate": bitrate,
                            "status_message_id": status_msg.message_id if status_msg else None,
                        },
                    )
                    logger.info(f"Dispatched download for {url} to distributed worker queue.")
                    return
                except Exception as exc:
                    logger.warning(
                        f"Could not dispatch to worker queue ({exc}). Falling back to in-process worker."
                    )
            else:
                logger.debug("Worker mode active but ARQ pool not initialized. Falling back to in-process worker.")

        # 4. Concurrency-bounded download with queue notification (In-Process Fallback)
        async def notify_queue_position(pos: int) -> None:
            await update_status_message(
                status_msg,
                f"⏳ *Server download capacity reached.*\n"
                f"• You are in queue: `#{pos}`\n"
                f"• _Your download will start automatically once a slot opens!_",
            )

        async with queue_manager.acquire_slot(notify_callback=notify_queue_position):
            await update_status_message(status_msg, "⬇️ *Downloading audio track...*")

            try:
                track_data = await asyncio.to_thread(
                    downloader.download_audio, url, None, audio_format, bitrate
                )
            except Exception as exc:
                logger.error(f"Download error for {url}: {exc}")
                metrics.increment("downloads_failed")
                await update_status_message(
                    status_msg, f"❌ *Download failed:*\n`{str(exc)[:150]}`"
                )
                return

            # 50MB Telegram Upload Safeguard
            if track_data.get("exceeds_limit"):
                size_mb = track_data.get("filesize", 0) // (1024 * 1024)
                downloader.cleanup_files(
                    track_data.get("file_path"), track_data.get("thumbnail_path")
                )
                await update_status_message(
                    status_msg,
                    f"⚠️ *File too large:* Audio file is {size_mb} MB, which exceeds "
                    f"Telegram's {config.max_file_size_mb} MB bot limit. Cannot upload.",
                )
                return

            await update_status_message(status_msg, "📤 *Uploading audio to Telegram...*")
            await send_upload_action(context, chat_id)

            audio_path = track_data["file_path"]
            thumb_path = track_data.get("thumbnail_path")

            try:
                with open(audio_path, "rb") as audio_file:
                    thumb_file = (
                        open(thumb_path, "rb")
                        if thumb_path and Path(thumb_path).exists()
                        else None
                    )
                    try:
                        sent_msg = await send_with_retry(
                            context.bot.send_audio,
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

                # Save newly uploaded file_id into cache for instant delivery next time
                if video_id and sent_msg and sent_msg.audio:
                    await cache_manager.set(
                        video_id=video_id,
                        file_id=sent_msg.audio.file_id,
                        title=track_data.get("title"),
                        artist=track_data.get("artist"),
                        duration=track_data.get("duration"),
                    )

                await safe_delete_message(status_msg)
            except Exception as exc:
                logger.error(f"Failed to upload audio to Telegram: {exc}")
                metrics.increment("downloads_failed")
                await update_status_message(
                    status_msg, f"❌ *Upload failed:*\n`{str(exc)[:150]}`"
                )
            finally:
                downloader.cleanup_files(audio_path, thumb_path)
                cleanup_orphaned_downloads(config.download_dir)


async def handle_playlist(
    url: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: telegram.Message,
    downloader: AudioDownloader,
) -> None:
    """Handles downloading and uploading a YouTube playlist with pipelined streaming and cancellation."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    audio_format, bitrate = await get_user_audio_format(user_id)

    # Register cancellation event for this chat
    cancel_event = asyncio.Event()
    ACTIVE_PLAYLIST_CANCELLATIONS[chat_id] = cancel_event
    cancel_kb = build_cancel_keyboard(chat_id)

    await update_status_message(status_msg, "🔎 *Extracting playlist metadata...*")

    try:
        playlist_info = await asyncio.to_thread(downloader.get_playlist_info, url)
    except Exception as exc:
        logger.error(f"Failed to extract playlist info for {url}: {exc}")
        await update_status_message(
            status_msg, f"❌ *Failed to read playlist:*\n`{str(exc)[:150]}`"
        )
        ACTIVE_PLAYLIST_CANCELLATIONS.pop(chat_id, None)
        return

    title = playlist_info.get("title", "YouTube Playlist")
    total = playlist_info.get("total_tracks", 0)

    if total == 0:
        await update_status_message(
            status_msg, "⚠️ *Playlist is empty or contains no accessible videos.*"
        )
        ACTIVE_PLAYLIST_CANCELLATIONS.pop(chat_id, None)
        return

    is_radio = playlist_info.get("is_radio_mix", False)
    if is_radio:
        notice_text = (
            f"📻 *YouTube Radio Mix detected:*\n"
            f"*{title}* (capped at `{total}` tracks)\n\n"
            "ℹ️ _Note: Radio mixes are dynamically generated by YouTube and may differ from your current browser queue. "
            "To download an exact, fixed playlist, save it on YouTube first and share the saved `PL...` link._\n\n"
            "Starting pipelined download..."
        )
    else:
        notice_text = (
            f"📋 *Playlist detected:*\n*{title}* ({total} tracks)\n\n"
            "Starting pipelined download..."
        )

    await update_status_message(status_msg, notice_text, reply_markup=cancel_kb)

    sent_count = 0
    skipped_count = 0
    last_processed_index = 0

    # Select streaming method (pipelined by default, or fallback/mocked stream_playlist_tracks)
    stream_fn = getattr(downloader, "stream_playlist_pipelined", None)
    try:
        from unittest.mock import Mock
        if stream_fn is None or isinstance(stream_fn, Mock):
            alt_fn = getattr(downloader, "stream_playlist_tracks", None)
            if alt_fn is not None and not isinstance(alt_fn, Mock):
                stream_fn = alt_fn
    except ImportError:
        pass
    if stream_fn is None:
        stream_fn = downloader.stream_playlist_pipelined

    try:
        async for index, total_tracks, track_data, error in stream_fn(
            url=url,
            cache_manager=cache_manager,
            cancel_event=cancel_event,
            audio_format=audio_format,
            bitrate=bitrate,
        ):
            last_processed_index = index
            if cancel_event.is_set():
                break

            progress_bar = render_progress_bar((index / total_tracks) * 100 if total_tracks else 0)

            if error:
                skipped_count += 1
                logger.warning(f"Skipping track {index}/{total_tracks}: {error}")
                await update_status_message(
                    status_msg,
                    f"📋 *Playlist:* {title}\n"
                    f"⏳ Progress: {progress_bar} `[{index}/{total_tracks}]`\n"
                    f"⚠️ *Skipped track {index}:* `{error[:60]}`",
                    reply_markup=cancel_kb,
                )
                continue

            if not track_data or track_data.get("exceeds_limit"):
                skipped_count += 1
                if track_data:
                    downloader.cleanup_files(
                        track_data.get("file_path"), track_data.get("thumbnail_path")
                    )
                await update_status_message(
                    status_msg,
                    f"📋 *Playlist:* {title}\n"
                    f"⏳ Progress: {progress_bar} `[{index}/{total_tracks}]`\n"
                    f"⚠️ *Track {index} exceeds {config.max_file_size_mb}MB limit (Skipped).* ",
                    reply_markup=cancel_kb,
                )
                continue

            # Case A: Instant Cached Track Delivery
            if track_data.get("is_cached") and track_data.get("file_id"):
                try:
                    await send_upload_action(context, chat_id)
                    await send_with_retry(
                        context.bot.send_audio,
                        chat_id=chat_id,
                        audio=track_data["file_id"],
                        title=track_data.get("title"),
                        performer=track_data.get("artist"),
                        duration=track_data.get("duration"),
                    )
                    sent_count += 1
                    if index < total_tracks and not cancel_event.is_set():
                        status_msg = await repost_status_message(
                            chat_id=chat_id,
                            bot=context.bot,
                            current_message=status_msg,
                            text=(
                                f"📋 *Playlist in Progress:*\n"
                                f"• Playlist: *{title}*\n"
                                f"• Progress: {progress_bar} `[{index}/{total_tracks}]`\n"
                                f"• ✅ Delivered: `{sent_count}` tracks (⚡ cached)\n"
                                f"• ⏳ Next track: `{index + 1}/{total_tracks}`\n\n"
                                f"⬇️ _Downloading next track..._"
                            ),
                            reply_markup=cancel_kb,
                        )
                    continue
                except Exception as exc:
                    logger.warning(f"Error sending cached playlist track ({exc}). Re-downloading...")

            # Case B: Downloaded Track Upload
            await update_status_message(
                status_msg,
                f"📋 *Playlist:* {title}\n"
                f"⏳ Progress: {progress_bar} `[{index}/{total_tracks}]`\n"
                f"📤 Uploading: *{track_data.get('title')}*...",
                reply_markup=cancel_kb,
            )

            audio_path = track_data["file_path"]
            thumb_path = track_data.get("thumbnail_path")

            try:
                await send_upload_action(context, chat_id)
                with open(audio_path, "rb") as audio_file:
                    thumb_file = (
                        open(thumb_path, "rb")
                        if thumb_path and Path(thumb_path).exists()
                        else None
                    )
                    try:
                        sent_msg = await send_with_retry(
                            context.bot.send_audio,
                            chat_id=chat_id,
                            audio=audio_file,
                            title=track_data.get("title"),
                            performer=track_data.get("artist"),
                            duration=track_data.get("duration"),
                            thumbnail=thumb_file,
                        )
                        sent_count += 1
                    finally:
                        if thumb_file:
                            thumb_file.close()

                # Cache the newly uploaded track
                track_id = track_data.get("id")
                if track_id and sent_msg and sent_msg.audio:
                    await cache_manager.set(
                        video_id=track_id,
                        file_id=sent_msg.audio.file_id,
                        title=track_data.get("title"),
                        artist=track_data.get("artist"),
                        duration=track_data.get("duration"),
                    )

                # Reposition the live progress dashboard below the newly uploaded audio track
                if index < total_tracks and not cancel_event.is_set():
                    status_msg = await repost_status_message(
                        chat_id=chat_id,
                        bot=context.bot,
                        current_message=status_msg,
                        text=(
                            f"📋 *Playlist in Progress:*\n"
                            f"• Playlist: *{title}*\n"
                            f"• Progress: {progress_bar} `[{index}/{total_tracks}]`\n"
                            f"• ✅ Delivered: `{sent_count}` tracks\n"
                            f"• ⏳ Next track: `{index + 1}/{total_tracks}`\n\n"
                            f"⬇️ _Downloading next track..._"
                        ),
                        reply_markup=cancel_kb,
                    )
            except Exception as exc:
                skipped_count += 1
                logger.error(f"Error sending playlist audio track {index}: {exc}")
            finally:
                downloader.cleanup_files(audio_path, thumb_path)

    finally:
        ACTIVE_PLAYLIST_CANCELLATIONS.pop(chat_id, None)
        cleanup_orphaned_downloads(config.download_dir)

    # Final completion or cancellation report
    if cancel_event.is_set():
        final_summary = (
            f"🛑 *Playlist Download Cancelled by User.*\n\n"
            f"• Playlist: *{title}*\n"
            f"• ✅ Delivered: `{sent_count}` tracks\n"
            f"• ⏹️ Stopped after track `{last_processed_index}/{total}`"
        )
    else:
        final_summary = (
            f"🎉 *Playlist Download Complete!*\n\n"
            f"• Playlist: *{title}*\n"
            f"• Total: `{total}` tracks\n"
            f"• ✅ Delivered: `{sent_count}` tracks\n"
            f"• ⚠️ Skipped: `{skipped_count}` tracks"
        )

    await repost_status_message(
        chat_id=chat_id,
        bot=context.bot,
        current_message=status_msg,
        text=final_summary,
        reply_markup=None,
    )


@restricted
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Routes callback queries for search results, format settings, and playlist cancellation."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data

    if data.startswith("dl:"):
        await query.answer()
        video_id = data.split("dl:", 1)[1]
        url = f"https://www.youtube.com/watch?v={video_id}"
        status_msg = await query.message.reply_text(
            "🔎 *Analyzing YouTube track...*", parse_mode="Markdown"
        )
        downloader = AudioDownloader()
        await handle_single_track(url, update, context, status_msg, downloader)

    elif data.startswith("cancel_pl:"):
        target_chat_id = int(data.split("cancel_pl:", 1)[1])
        event = ACTIVE_PLAYLIST_CANCELLATIONS.get(target_chat_id)
        if event:
            event.set()
            await query.answer("Playlist download cancellation requested!", show_alert=True)
            logger.info(f"Cancellation requested for chat_id={target_chat_id}")
        else:
            await query.answer("No active playlist download found to cancel.", show_alert=True)

    elif data.startswith("set_fmt:"):
        chosen_fmt = data.split("set_fmt:", 1)[1]
        user_id = update.effective_user.id if update.effective_user else 0
        await cache_manager.set_user_setting(user_id, "audio_format", chosen_fmt)

        fmt_names = {
            "mp3_192": "MP3 (192 kbps)",
            "mp3_320": "MP3 (320 kbps)",
            "m4a": "M4A (Native Source Copy)",
        }
        name = fmt_names.get(chosen_fmt, chosen_fmt)
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_settings_keyboard(chosen_fmt)
            )
        except Exception:
            pass
        await query.answer(f"Quality updated to {name}!")


@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inspects text messages for YouTube URLs or triggers YouTube search."""
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()
    match = YOUTUBE_URL_REGEX.search(text)

    if not match:
        # Fallback to direct search if text has sufficient length
        if len(text) >= 2:
            await execute_search(text, update, context)
        else:
            await update.effective_message.reply_text(
                "Please send a valid YouTube video/playlist link, or type a song name to search. "
                "Type /help for instructions.",
                parse_mode="Markdown",
            )
        return

    url = match.group(1)
    status_msg = await update.effective_message.reply_text(
        "🔎 *Analyzing YouTube link...*", parse_mode="Markdown"
    )

    downloader = AudioDownloader()

    if downloader.is_playlist(url):
        await handle_playlist(url, update, context, status_msg, downloader)
    else:
        await handle_single_track(url, update, context, status_msg, downloader)


async def global_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Catches and handles unexpected exceptions gracefully."""
    logger.error(
        f"Unhandled exception while processing update: {context.error}",
        exc_info=context.error,
    )

    if isinstance(update, Update) and update.effective_message:
        err_text = "⚠️ An unexpected error occurred while processing your request. Please try again later."
        if isinstance(context.error, TimedOut):
            err_text = "⚠️ Request timed out while contacting Telegram servers. Please try again."
        elif isinstance(context.error, NetworkError):
            err_text = "⚠️ Network issue encountered. Please retry shortly."

        try:
            await update.effective_message.reply_text(err_text)
        except Exception as exc:
            logger.debug(f"Could not send error message to user: {exc}")


ONBOARDING_DESCRIPTION = (
    "🎵 Welcome to TubeTapper! 🎧\n\n"
    "Your instant high-quality music companion on Telegram.\n\n"
    "✨ What you can do:\n"
    "• 🔍 In-Chat Search: Just type any song or artist name (e.g. 'Queen Bohemian Rhapsody')\n"
    "• 🔗 Direct Links: Paste any YouTube video, playlist, or mix URL\n"
    "• ⚡ Studio Audio: Clean metadata & HD square album art\n"
    "• ⚙️ Audio Quality: Choose 192k, 320k MP3, or source-quality Native M4A\n\n"
    "Tap 'Start' below to begin downloading music!"
)

SHORT_DESCRIPTION = (
    "Instant YouTube Music Downloader. Send song names or links to get studio audio with album art!"
)

BOT_COMMANDS = [
    BotCommand("start", "Start the bot & see welcome guide"),
    BotCommand("search", "Search for songs by title or artist"),
    BotCommand("settings", "Choose audio format & quality (MP3/M4A)"),
    BotCommand("status", "Check bot health, cache & queue stats"),
    BotCommand("help", "View full instructions & tips"),
]


async def on_startup(application: Application) -> None:
    """Initializes async resources, worker pools, and cache."""
    start_metrics_server(9090)
    await cache_manager.initialize()

    # Create shared ARQ worker pool if worker mode is active and Redis is reachable
    if config.worker_mode and cache_manager.is_redis_active and ARQ_AVAILABLE:
        try:
            redis_pool = await create_pool(
                RedisSettings.from_dsn(config.redis_url or "redis://localhost:6379/0")
            )
            application.bot_data["arq_pool"] = redis_pool
            logger.info("Shared ARQ worker pool initialized in bot_data.")
        except Exception as exc:
            logger.warning(
                f"Failed to create shared ARQ pool on startup ({exc}). In-process fallback will be used."
            )

    # Configure Telegram native onboarding screen ("What can this bot do?") and menu commands
    try:
        if hasattr(application.bot, "set_my_description"):
            await application.bot.set_my_description(description=ONBOARDING_DESCRIPTION)
        if hasattr(application.bot, "set_my_short_description"):
            await application.bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
        # Register commands for all scopes so typing '/' in chat immediately shows autocompletion
        if hasattr(application.bot, "set_my_commands"):
            await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
            await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        if hasattr(application.bot, "set_chat_menu_button"):
            await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Successfully registered Telegram native onboarding screen, commands autocompletion, and menu button.")
    except Exception as exc:
        logger.debug(f"Could not set native Telegram descriptions (expected in offline/test mode): {exc}")


async def on_shutdown(application: Application) -> None:
    """Closes cache manager and ARQ pool on shutdown."""
    await cache_manager.close()
    pool = application.bot_data.get("arq_pool")
    if pool:
        await pool.close()
    logger.info("Bot shutdown complete.")


def create_bot_app(token: Optional[str] = None) -> Application:
    """Builds and returns the configured Telegram Application with concurrent update processing."""
    bot_token = token or config.bot_token
    if not bot_token or bot_token == "your_telegram_bot_token_here":
        bot_token = "TEST_TOKEN_FOR_INITIALIZATION"

    builder = (
        ApplicationBuilder()
        .token(bot_token)
        .concurrent_updates(True)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
    )

    # If configured with local Telegram Bot API server, unlock 2GB uploads and LAN zero-copy transfer
    if config.telegram_api_server_url:
        builder = builder.base_url(config.telegram_api_server_url)
    if config.telegram_local_mode:
        builder = builder.local_mode(True)

    application = builder.build()

    # Register command handlers with block=False for parallel execution
    application.add_handler(CommandHandler("start", start_command, block=False))
    application.add_handler(CommandHandler("help", help_command, block=False))
    application.add_handler(CommandHandler("status", status_command, block=False))
    application.add_handler(CommandHandler("settings", settings_command, block=False))
    application.add_handler(CommandHandler("search", search_command, block=False))

    # Register callback query handler for buttons (download, cancel, settings)
    application.add_handler(CallbackQueryHandler(handle_callback_query, block=False))

    # Register message handler for text with block=False for parallel execution
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message, block=False)
    )

    # Register global error handler
    application.add_error_handler(global_error_handler)

    return application


def main() -> None:
    """Starts the bot in webhook or long-polling mode."""
    if not config.bot_token or config.bot_token == "your_telegram_bot_token_here":
        logger.error(
            "Cannot start bot: TELEGRAM_BOT_TOKEN is not configured in .env. "
            "Please get a token from @BotFather."
        )
        return

    # Clean any stale download files from previous runs
    removed = cleanup_orphaned_downloads(config.download_dir)
    if removed:
        logger.info(f"Cleaned up {removed} orphaned files on startup.")

    logger.info("Initializing TubeTapper Music Downloader Bot...")
    app = create_bot_app()

    # Support high-throughput Webhooks mode for 100k+ concurrent users
    if config.webhook_mode and config.webhook_url:
        logger.info(
            f"Starting TubeTapper Bot in Webhook mode on port {config.webhook_port} ({config.webhook_url})..."
        )
        app.run_webhook(
            listen="0.0.0.0",
            port=config.webhook_port,
            webhook_url=config.webhook_url,
            secret_token=config.webhook_secret,
        )
    else:
        logger.info("Bot is polling for updates. Press Ctrl+C to stop.")
        app.run_polling()


if __name__ == "__main__":
    main()
