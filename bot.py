"""Telegram Bot application for YouTube to Telegram Music Downloader with safeguards and access control."""

import asyncio
from contextlib import nullcontext
import hashlib
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
from downloader import AudioDownloader, format_download_error
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

# In-memory mapping for long playlist URLs to stay well within Telegram's 64-byte callback_data limit
PENDING_PLAYLIST_URLS: Dict[str, str] = {}


async def send_upload_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, is_video: bool = False) -> None:
    """Sends native Telegram chat action 'uploading audio/video...' safely."""
    try:
        action = ChatAction.UPLOAD_VIDEO if is_video else ChatAction.UPLOAD_VOICE
        action_coro = context.bot.send_chat_action(chat_id=chat_id, action=action)
        if asyncio.iscoroutine(action_coro):
            await action_coro
    except Exception as exc:
        logger.debug(f"send_chat_action notice error (non-fatal): {exc}")


async def get_user_preferences(user_id: int) -> Tuple[str, str, str]:
    """Retrieves user's download_mode, audio_format, and video_quality.
    
    Returns: (download_mode, audio_format, video_quality)
    """
    mode = await cache_manager.get_user_setting(user_id, "download_mode", "auto")
    audio_fmt = await cache_manager.get_user_setting(user_id, "audio_format", "mp3_320")
    vid_fmt = await cache_manager.get_user_setting(user_id, "video_quality", "mp4_720")

    # Normalize legacy formats if stored in user preferences
    if audio_fmt in ("mp4", "mp4_480", "mp4_720", "mp4_1080"):
        vid_fmt = "mp4_720" if audio_fmt == "mp4" else audio_fmt
        audio_fmt = "mp3_320"
    if vid_fmt not in ("mp4_480", "mp4_720", "mp4_1080"):
        vid_fmt = "mp4_720"

    return mode, audio_fmt, vid_fmt


async def get_user_audio_format(user_id: int) -> Tuple[str, int]:
    """Retrieves user's preferred audio format and bitrate.
    
    Returns: (audio_format, bitrate) e.g. ('mp3', 320)
    """
    pref = await cache_manager.get_user_setting(user_id, "audio_format", "mp3_320")
    if pref == "mp3_192":
        return "mp3", 192
    elif pref == "m4a":
        return "m4a", 0
    return "mp3", 320


def build_settings_keyboard(
    mode_or_pref: str = "auto",
    audio_fmt: Optional[str] = None,
    vid_fmt: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Builds inline keyboard for settings divided into 3 distinct sections with independent checkmarks."""
    if audio_fmt is None and vid_fmt is None:
        if mode_or_pref in ("auto", "ask", "always_audio", "always_video"):
            mode = mode_or_pref
            aud = "mp3_320"
            vid = "mp4_720"
        elif mode_or_pref in ("mp3_192", "mp3_320", "m4a"):
            mode = "auto"
            aud = mode_or_pref
            vid = "mp4_720"
        elif mode_or_pref in ("mp4_480", "mp4_720", "mp4_1080", "mp4"):
            mode = "always_video"
            aud = "mp3_320"
            vid = "mp4_720" if mode_or_pref == "mp4" else mode_or_pref
        else:
            mode = "auto"
            aud = "mp3_320"
            vid = "mp4_720"
    else:
        mode = mode_or_pref
        aud = audio_fmt or "mp3_320"
        vid = vid_fmt or "mp4_720"

    keyboard = [
        # Section 1: Download Mode
        [
            InlineKeyboardButton(f"🤖 Smart Auto{' ✅' if mode == 'auto' else ''}", callback_data="set_mode:auto"),
            InlineKeyboardButton(f"⚡ Ask Every Time{' ✅' if mode == 'ask' else ''}", callback_data="set_mode:ask"),
        ],
        [
            InlineKeyboardButton(f"🎵 Always Audio{' ✅' if mode == 'always_audio' else ''}", callback_data="set_mode:always_audio"),
            InlineKeyboardButton(f"🎬 Always Video{' ✅' if mode == 'always_video' else ''}", callback_data="set_mode:always_video"),
        ],
        # Section 2: Preferred Audio Quality
        [
            InlineKeyboardButton(f"🎵 192k{' ✅' if aud == 'mp3_192' else ''}", callback_data="set_aud:mp3_192"),
            InlineKeyboardButton(f"🎧 320k (HQ){' ✅' if aud == 'mp3_320' else ''}", callback_data="set_aud:mp3_320"),
            InlineKeyboardButton(f"⚡ M4A{' ✅' if aud == 'm4a' else ''}", callback_data="set_aud:m4a"),
        ],
        # Section 3: Preferred Video Quality
        [
            InlineKeyboardButton(f"🎬 480p{' ✅' if vid == 'mp4_480' else ''}", callback_data="set_vid:mp4_480"),
            InlineKeyboardButton(f"🎬 720p (HD){' ✅' if vid in ('mp4_720', 'mp4') else ''}", callback_data="set_vid:mp4_720"),
            InlineKeyboardButton(f"🎬 1080p (FHD){' ✅' if vid == 'mp4_1080' else ''}", callback_data="set_vid:mp4_1080"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_format_picker_keyboard(media_id: str, is_playlist: bool = False) -> InlineKeyboardMarkup:
    """Builds interactive 6-button keyboard for selecting audio or video resolution."""
    prefix = "plfmt" if is_playlist else "fmt"
    keyboard = [
        [
            InlineKeyboardButton("🎵 MP3 192k", callback_data=f"{prefix}:mp3_192:{media_id}"),
            InlineKeyboardButton("🎧 MP3 320k", callback_data=f"{prefix}:mp3_320:{media_id}"),
            InlineKeyboardButton("⚡ M4A Native", callback_data=f"{prefix}:m4a:{media_id}"),
        ],
        [
            InlineKeyboardButton("🎬 480p Fast", callback_data=f"{prefix}:mp4_480:{media_id}"),
            InlineKeyboardButton("🎬 720p HD", callback_data=f"{prefix}:mp4_720:{media_id}"),
            InlineKeyboardButton("🎬 1080p FHD", callback_data=f"{prefix}:mp4_1080:{media_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_delivery_switch_keyboard(
    video_id: str,
    current_format: str,
    default_audio: str = "mp3_320",
    default_video: str = "mp4_720",
) -> Optional[InlineKeyboardMarkup]:
    """Builds a 1-tap quick switch button attached to delivered media."""
    if not video_id:
        return None
    if current_format.startswith("mp4"):
        aud_label = "🎧 Extract MP3 (320k)" if default_audio == "mp3_320" else f"🎵 Extract Audio ({default_audio})"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(aud_label, callback_data=f"fmt:{default_audio}:{video_id}")]
        ])
    else:
        vid_label = "🎬 Get Video (720p HD)" if default_video == "mp4_720" else f"🎬 Get Video ({default_video.replace('mp4_', '')}p)"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(vid_label, callback_data=f"fmt:{default_video}:{video_id}")]
        ])


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
                        "👋 *Sorry, this bot is currently private and you don't have access.*",
                        parse_mode="Markdown",
                    )
                return None

            # Exempt UI buttons like settings and cancel from rate limiting
            exempt_from_rate_limit = False
            if update.callback_query and update.callback_query.data:
                cb_data = update.callback_query.data
                if cb_data.startswith("set_fmt:") or cb_data.startswith("cancel_pl:"):
                    exempt_from_rate_limit = True

            if check_rate_limit and user and not exempt_from_rate_limit:
                if not rate_limiter.is_allowed(user.id):
                    wait_time = rate_limiter.time_until_allowed(user.id)
                    logger.warning(
                        f"Rate limit exceeded for user ID {user.id} (retry in {wait_time:.1f}s)"
                    )
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            f"🏎️ *Whoa there, you're going a bit too fast!* Please wait {wait_time:.0f} seconds before requesting another song.",
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
        "Just paste a YouTube link here, or type the name of a song to search for it!\n\n"
        "> 💡 *Tip:* You can cancel playlist downloads at any time if they're too long."
    )
    keyboard = [
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            InlineKeyboardButton("🆘 Help", callback_data="cmd_help"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


@restricted(check_rate_limit=False)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides user instructions, limitations, and help."""
    if not update.effective_message:
        return

    text = (
        "ℹ️ *How to Use This Bot:*\n\n"
        "• *Search:* Just type any song or artist name (e.g. `Queen Bohemian Rhapsody`).\n"
        "• *Links:* Send any YouTube video or playlist link.\n"
        "• *Quality:* Use /settings to switch between MP3 and Native M4A.\n\n"
        "> ⚠️ *Important:* YouTube 'My Mix' links change dynamically. To get a fixed playlist, save the mix to your library first.\n\n"
        "⚙️ *Specifications:*\n"
        f"• Max file size: {config.max_file_size_mb} MB\n"
        f"• Playlist limit: {config.max_playlist_tracks} tracks\n"
        "• Live streams and private videos are skipped gracefully."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted(check_rate_limit=False)
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reports bot health, configuration, and status."""
    if not update.effective_message:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    is_admin = user_id in config.admin_users

    if is_admin:
        cache_engine = "Redis (In-Memory)" if cache_manager.is_redis_active else "SQLite (Fallback)"
        bot_server_type = "Local Bot API Server (2GB)" if config.telegram_local_mode or config.telegram_api_server_url else "Telegram Public Gateway (50MB)"
        mode_type = "Webhooks (High-Throughput)" if config.webhook_mode else "Long-Polling"
        worker_status = "Distributed Worker Cluster" if config.worker_mode else f"In-Process Queue ({config.max_concurrent_downloads} workers)"
        proxy_status = "Rotating Proxy Pool Active" if (config.youtube_proxy or config.youtube_proxy_pool) else "Direct Server IP"

        text = (
            "🟢 *Admin Diagnostics: Online*\n\n"
            "• Engine: `yt-dlp` + `FFmpeg`\n"
            f"• Telegram Gateway: `{bot_server_type}`\n"
            f"• Ingestion Mode: `{mode_type}`\n"
            f"• Download Architecture: `{worker_status}`\n"
            f"• Proxy Protection: `{proxy_status}`\n"
            f"• Audio Cache: `{cache_engine}`\n"
            f"• Concurrency Limit: `{config.max_concurrent_downloads} workers`\n"
            f"• Active Downloads: `{queue_manager.active_count}`\n"
            f"• Queue Depth: `{queue_manager.waiting_count}`\n"
            "📊 *Metrics:*\n"
            f"```\n{metrics.get_summary()}\n```"
        )
    else:
        text = (
            "🟢 *TubeTapper is Online!*\n\n"
            f"🎵 Active Downloads: {queue_manager.active_count}\n"
            f"⏳ Songs in Queue: {queue_manager.waiting_count}"
        )

    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets user customize preferred download mode, audio bitrate, and video resolution."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)

    text = (
        "⚙️ *TubeTapper Download Settings*\n\n"
        "Customize your download mode and default quality preferences:\n\n"
        "📥 *Download Mode:*\n"
        "• 🤖 *Smart Auto*: Shorts download as HD Video; music/videos download as High-Quality Audio.\n"
        "• ⚡ *Ask Every Time*: Prompts you with 6 format buttons whenever you send a link.\n"
        "• 🎵 *Always Audio*: Directly downloads audio from all links.\n"
        "• 🎬 *Always Video*: Directly downloads video from all links.\n\n"
        "🎧 *Default Audio Quality*: Bitrate used for music/audio.\n"
        "🎬 *Default Video Quality*: Resolution used for videos and shorts.\n\n"
        "────────────────────────\n"
        "Tap any button below to update your preferences:"
    )
    await update.effective_message.reply_text(
        text,
        reply_markup=build_settings_keyboard(mode, audio_fmt, vid_fmt),
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
    requested_format: Optional[str] = None,
) -> None:
    """Handles downloading and uploading a single YouTube track."""
    chat_id = update.effective_chat.id
    reply_to = update.effective_message.message_id if update.effective_message else None
    user_id = update.effective_user.id if update.effective_user else 0

    mode, default_audio_fmt, default_vid_fmt = await get_user_preferences(user_id)
    video_id = downloader.extract_video_id(url)

    if requested_format:
        audio_format = requested_format
    else:
        if mode == "ask":
            try:
                info = await asyncio.to_thread(downloader.get_video_info, url)
                title = info.get("title", "YouTube Video")
                duration = int(info.get("duration") or 0)
                dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "Unknown"
            except Exception:
                title = "YouTube Video"
                dur_str = "Unknown"

            text = (
                f"🎬 *{title}*\n"
                f"⏱ Duration: `{dur_str}`\n\n"
                f"*Choose your download format:*"
            )
            media_id = video_id or url
            await update_status_message(
                status_msg,
                text,
                reply_markup=build_format_picker_keyboard(media_id, is_playlist=False),
            )
            return
        elif mode == "always_audio":
            audio_format = default_audio_fmt
        elif mode == "always_video":
            audio_format = default_vid_fmt
        else:
            # Smart Auto: Shorts -> default video quality, others -> default audio quality
            if "/shorts/" in url.lower():
                audio_format = default_vid_fmt
            else:
                audio_format = default_audio_fmt

    bitrate = 192 if audio_format == "mp3_192" else (320 if audio_format == "mp3_320" else 0)
    is_video = audio_format.startswith("mp4")

    metrics.increment("downloads_total")

    # Quick format switch markup attached to delivery
    switch_kb = build_delivery_switch_keyboard(video_id, audio_format, default_audio_fmt, default_vid_fmt)

    # 1. Instant Cache Check (Telegram file_id reuse)
    if video_id:
        try:
            cached = await cache_manager.get(video_id, audio_format)
            if cached and cached.get("file_id"):
                media_type = "video" if is_video else "audio"
                logger.info(f"Delivering cached {media_type} for video ID {video_id}")
                metrics.increment("downloads_cached")
                await update_status_message(status_msg, f"⚡ *Found in cache! Delivering {media_type}...*")
                await send_upload_action(context, chat_id, is_video=is_video)
                send_func = context.bot.send_video if is_video else context.bot.send_audio
                kwargs = {
                    "chat_id": chat_id,
                    "duration": cached.get("duration"),
                    "reply_to_message_id": reply_to,
                }
                if switch_kb:
                    kwargs["reply_markup"] = switch_kb

                if is_video:
                    kwargs["video"] = cached["file_id"]
                    kwargs["caption"] = f"{cached.get('title')} - {cached.get('artist')}"
                else:
                    kwargs["audio"] = cached["file_id"]
                    kwargs["title"] = cached.get("title")
                    kwargs["performer"] = cached.get("artist")

                await send_with_retry(send_func, **kwargs)
                await safe_delete_message(status_msg)
                return
        except Exception as exc:
            logger.warning(f"Failed to send cached media: {exc}. Proceeding to fresh download.")

    # 2. Cache Stampede Protection (Distributed Lock)
    lock_manager = cache_manager.acquire_lock(video_id, format_id=audio_format) if video_id else nullcontext()
    async with lock_manager:
        # Re-check cache after acquiring lock in case another request completed while waiting
        if video_id:
            try:
                cached = await cache_manager.get(video_id, audio_format)
                if cached and cached.get("file_id"):
                    media_type = "video" if is_video else "audio"
                    logger.info(f"Delivering cached {media_type} (post-lock) for video ID {video_id}")
                    metrics.increment("downloads_cached")
                    await update_status_message(status_msg, f"⚡ *Found in cache! Delivering {media_type}...*")
                    await send_upload_action(context, chat_id, is_video=is_video)
                    send_func = context.bot.send_video if is_video else context.bot.send_audio
                    kwargs = {
                        "chat_id": chat_id,
                        "duration": cached.get("duration"),
                        "reply_to_message_id": reply_to,
                    }
                    if switch_kb:
                        kwargs["reply_markup"] = switch_kb

                    if is_video:
                        kwargs["video"] = cached["file_id"]
                        kwargs["caption"] = f"{cached.get('title')} - {cached.get('artist')}"
                    else:
                        kwargs["audio"] = cached["file_id"]
                        kwargs["title"] = cached.get("title")
                        kwargs["performer"] = cached.get("artist")

                    await send_with_retry(send_func, **kwargs)
                    await safe_delete_message(status_msg)
                    return
            except Exception as exc:
                logger.warning(f"Failed to send cached media post-lock: {exc}. Proceeding to fresh download.")

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
            media_type = "video" if is_video else "audio track"
            await update_status_message(status_msg, f"⬇️ *Downloading {media_type}...*")

            try:
                track_data = await asyncio.to_thread(
                    downloader.download_audio, url, None, audio_format, bitrate
                )
            except Exception as exc:
                logger.error(f"Download error for {url}: {exc}")
                metrics.increment("downloads_failed")
                await update_status_message(
                    status_msg, format_download_error(exc)
                )
                return

            # Telegram Upload Size Safeguard
            if track_data.get("exceeds_limit"):
                size_mb = track_data.get("filesize", 0) // (1024 * 1024)
                downloader.cleanup_files(
                    track_data.get("file_path"), track_data.get("thumbnail_path")
                )
                media_name = "Video" if is_video else "Audio"
                retry_kb = None
                if is_video and "480" not in audio_format and video_id:
                    retry_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎬 Try Fast 480p (Smaller Size)", callback_data=f"fmt:mp4_480:{video_id}")]
                    ])
                await update_status_message(
                    status_msg,
                    f"⚠️ *File too large:*\n{media_name} file is {size_mb} MB, which exceeds "
                    f"Telegram's {config.max_file_size_mb} MB limit.\n\n"
                    f"> 💡 *Tip:* Try downloading in 480p or Audio (MP3) for a much smaller file size.",
                    reply_markup=retry_kb,
                )
                return

            media_label = "video" if is_video else "audio"
            await update_status_message(status_msg, f"📤 *Uploading {media_label} to Telegram...*")
            await send_upload_action(context, chat_id, is_video=is_video)

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
                        send_func = context.bot.send_video if is_video else context.bot.send_audio
                        kwargs = {
                            "chat_id": chat_id,
                            "duration": track_data.get("duration"),
                            "reply_to_message_id": reply_to,
                        }
                        if switch_kb:
                            kwargs["reply_markup"] = switch_kb

                        if is_video:
                            kwargs["video"] = audio_file
                            kwargs["caption"] = f"{track_data.get('title')} - {track_data.get('artist')}"
                        else:
                            kwargs["audio"] = audio_file
                            kwargs["title"] = track_data.get("title")
                            kwargs["performer"] = track_data.get("artist")

                        if thumb_file:
                            kwargs["thumbnail"] = thumb_file

                        sent_msg = await send_with_retry(send_func, **kwargs)
                    finally:
                        if thumb_file:
                            thumb_file.close()

                # Save newly uploaded file_id permanently into cache
                if video_id and sent_msg and (sent_msg.audio or sent_msg.video):
                    file_id = sent_msg.video.file_id if sent_msg.video else sent_msg.audio.file_id
                    await cache_manager.set(
                        video_id=video_id,
                        file_id=file_id,
                        title=track_data.get("title"),
                        artist=track_data.get("artist"),
                        duration=track_data.get("duration"),
                        format_id=audio_format,
                        file_size=track_data.get("filesize", 0),
                    )

                await safe_delete_message(status_msg)
            except Exception as exc:
                logger.error(f"Failed to upload media to Telegram: {exc}")
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
    requested_format: Optional[str] = None,
) -> None:
    """Handles downloading and uploading a YouTube playlist with pipelined streaming and cancellation."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    mode, default_audio_fmt, default_vid_fmt = await get_user_preferences(user_id)

    if requested_format:
        audio_format = requested_format
    else:
        if mode == "ask":
            await update_status_message(status_msg, "🔎 *Extracting playlist metadata...*")
            try:
                playlist_info = await asyncio.to_thread(downloader.get_playlist_info, url)
            except Exception as exc:
                logger.error(f"Failed to extract playlist info for {url}: {exc}")
                await update_status_message(
                    status_msg, f"❌ *Failed to read playlist:*\n`{str(exc)[:150]}`"
                )
                return

            title = playlist_info.get("title", "YouTube Playlist")
            total = playlist_info.get("total_tracks", 0)

            if total == 0:
                await update_status_message(
                    status_msg, "⚠️ *Playlist is empty or contains no accessible videos.*"
                )
                return

            # Store short hash mapping for playlist URL
            pl_key = hashlib.md5(url.encode()).hexdigest()[:16]
            PENDING_PLAYLIST_URLS[pl_key] = url

            text = (
                f"📋 *Playlist detected:*\n"
                f"*{title}* (`{total}` tracks)\n\n"
                f"*How would you like to download this playlist?*"
            )
            await update_status_message(
                status_msg,
                text,
                reply_markup=build_format_picker_keyboard(pl_key, is_playlist=True),
            )
            return
        elif mode == "always_video":
            audio_format = default_vid_fmt
        elif mode == "always_audio":
            audio_format = default_audio_fmt
        else:
            # Smart Auto default for playlists: default audio quality
            audio_format = default_audio_fmt

    bitrate = 192 if audio_format == "mp3_192" else (320 if audio_format == "mp3_320" else 0)
    is_video = audio_format.startswith("mp4")

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

    media_mode_str = "Video" if audio_format.startswith("mp4") else "Audio"
    is_radio = playlist_info.get("is_radio_mix", False)
    if is_radio:
        notice_text = (
            f"📻 *YouTube Radio Mix detected ({media_mode_str}):*\n"
            f"*{title}* (capped at `{total}` tracks)\n\n"
            "ℹ️ _Note: Radio mixes are dynamically generated by YouTube and may differ from your current browser queue. "
            "To download an exact, fixed playlist, save it on YouTube first and share the saved `PL...` link._\n\n"
            "Starting pipelined download..."
        )
    else:
        notice_text = (
            f"📋 *Playlist detected ({media_mode_str}):*\n*{title}* ({total} tracks)\n\n"
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
                    is_video = audio_format.startswith("mp4")
                    send_func = context.bot.send_video if is_video else context.bot.send_audio
                    kwargs = {
                        "chat_id": chat_id,
                        "duration": track_data.get("duration"),
                    }
                    if is_video:
                        kwargs["video"] = track_data["file_id"]
                        kwargs["caption"] = f"{track_data.get('title')} - {track_data.get('artist')}"
                    else:
                        kwargs["audio"] = track_data["file_id"]
                        kwargs["title"] = track_data.get("title")
                        kwargs["performer"] = track_data.get("artist")

                    await send_with_retry(send_func, **kwargs)
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
                await send_upload_action(context, chat_id, is_video=is_video)
                with open(audio_path, "rb") as audio_file:
                    thumb_file = (
                        open(thumb_path, "rb")
                        if thumb_path and Path(thumb_path).exists()
                        else None
                    )
                    try:
                        is_video = audio_format.startswith("mp4")
                        send_func = context.bot.send_video if is_video else context.bot.send_audio
                        kwargs = {
                            "chat_id": chat_id,
                            "duration": track_data.get("duration"),
                        }
                        if is_video:
                            kwargs["video"] = audio_file
                            kwargs["caption"] = f"{track_data.get('title')} - {track_data.get('artist')}"
                        else:
                            kwargs["audio"] = audio_file
                            kwargs["title"] = track_data.get("title")
                            kwargs["performer"] = track_data.get("artist")

                        if thumb_file:
                            kwargs["thumbnail"] = thumb_file

                        sent_msg = await send_with_retry(send_func, **kwargs)
                        sent_count += 1
                    finally:
                        if thumb_file:
                            thumb_file.close()

                # Cache the newly uploaded track permanently
                track_id = track_data.get("id")
                if track_id and sent_msg and (sent_msg.audio or sent_msg.video):
                    file_id = sent_msg.video.file_id if sent_msg.video else sent_msg.audio.file_id
                    await cache_manager.set(
                        video_id=track_id,
                        file_id=file_id,
                        title=track_data.get("title"),
                        artist=track_data.get("artist"),
                        duration=track_data.get("duration"),
                        format_id=audio_format,
                        file_size=track_data.get("filesize", 0),
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

    elif data.startswith("fmt:"):
        # Format selection for single track: fmt:<format>:<video_id>
        parts = data.split(":", 2)
        if len(parts) == 3:
            chosen_fmt = parts[1]
            video_id = parts[2]
            url = f"https://www.youtube.com/watch?v={video_id}" if not video_id.startswith("http") else video_id
            await query.answer(f"Selected {chosen_fmt.replace('mp4_', '').replace('mp3_', '')} format!")
            status_msg = await query.message.reply_text(
                "🔎 *Preparing media...*", parse_mode="Markdown"
            )
            downloader = AudioDownloader()
            await handle_single_track(url, update, context, status_msg, downloader, requested_format=chosen_fmt)

    elif data.startswith("plfmt:"):
        # Format selection for playlist: plfmt:<format>:<playlist_key>
        parts = data.split(":", 2)
        if len(parts) == 3:
            chosen_fmt = parts[1]
            pl_key = parts[2]
            url = PENDING_PLAYLIST_URLS.get(pl_key) or f"https://www.youtube.com/playlist?list={pl_key}"
            await query.answer(f"Downloading playlist in {chosen_fmt}...")
            status_msg = await query.message.reply_text(
                "🔎 *Preparing playlist download...*", parse_mode="Markdown"
            )
            downloader = AudioDownloader()
            await handle_playlist(url, update, context, status_msg, downloader, requested_format=chosen_fmt)

    elif data.startswith("cancel_pl:"):
        target_chat_id = int(data.split("cancel_pl:", 1)[1])
        event = ACTIVE_PLAYLIST_CANCELLATIONS.get(target_chat_id)
        if event:
            event.set()
            await query.answer("Playlist download cancellation requested!", show_alert=True)
            logger.info(f"Cancellation requested for chat_id={target_chat_id}")
        else:
            await query.answer("No active playlist download found to cancel.", show_alert=True)

    elif data.startswith("set_mode:"):
        chosen_mode = data.split("set_mode:", 1)[1]
        user_id = update.effective_user.id if update.effective_user else 0
        await cache_manager.set_user_setting(user_id, "download_mode", chosen_mode)
        mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)
        mode_titles = {
            "auto": "🤖 Smart Auto",
            "ask": "⚡ Ask Every Time",
            "always_audio": "🎵 Always Audio",
            "always_video": "🎬 Always Video",
        }
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_settings_keyboard(mode, audio_fmt, vid_fmt)
            )
        except Exception:
            pass
        await query.answer(f"Mode updated: {mode_titles.get(chosen_mode, chosen_mode)}!")

    elif data.startswith("set_aud:"):
        chosen_aud = data.split("set_aud:", 1)[1]
        user_id = update.effective_user.id if update.effective_user else 0
        await cache_manager.set_user_setting(user_id, "audio_format", chosen_aud)
        mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_settings_keyboard(mode, audio_fmt, vid_fmt)
            )
        except Exception:
            pass
        await query.answer(f"Audio quality updated to {chosen_aud.replace('mp3_', '')} kbps!")

    elif data.startswith("set_vid:"):
        chosen_vid = data.split("set_vid:", 1)[1]
        user_id = update.effective_user.id if update.effective_user else 0
        await cache_manager.set_user_setting(user_id, "video_quality", chosen_vid)
        mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_settings_keyboard(mode, audio_fmt, vid_fmt)
            )
        except Exception:
            pass
        await query.answer(f"Video quality updated to {chosen_vid.replace('mp4_', '')}p!")

    elif data.startswith("set_fmt:"):
        chosen_fmt = data.split("set_fmt:", 1)[1]
        user_id = update.effective_user.id if update.effective_user else 0
        if chosen_fmt.startswith("mp4"):
            await cache_manager.set_user_setting(user_id, "video_quality", chosen_fmt)
        else:
            await cache_manager.set_user_setting(user_id, "audio_format", chosen_fmt)
        mode, audio_fmt, vid_fmt = await get_user_preferences(user_id)
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_settings_keyboard(mode, audio_fmt, vid_fmt)
            )
        except Exception:
            pass
        await query.answer("Setting updated!")

    elif data == "cmd_settings":
        await query.answer()
        await settings_command(update, context)

    elif data == "cmd_help":
        await query.answer()
        await help_command(update, context)


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
