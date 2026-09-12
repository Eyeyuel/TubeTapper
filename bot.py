"""Telegram Bot application for YouTube to Telegram Music Downloader with safeguards and access control."""

import asyncio
import os
import re
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import telegram
from telegram import Update
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from downloader import AudioDownloader
from helpers.cleanup import cleanup_orphaned_downloads
from helpers.logger import setup_logger
from helpers.progress import (
    repost_status_message,
    safe_delete_message,
    update_status_message,
)

logger = setup_logger("bot")

# Universal regex matching all YouTube URL domains (youtube.com, m.youtube.com, music.youtube.com, youtu.be)
# and endpoints (watch, playlist, shorts, live, embed, and shortened youtu.be IDs)
YOUTUBE_URL_REGEX = re.compile(
    r"(https?://(?:[a-zA-Z0-9_.-]+\.)?(?:youtube\.com|youtu\.be)/(?:watch\?[^\s]+|playlist\?[^\s]+|shorts/[a-zA-Z0-9_-]+|live/[a-zA-Z0-9_-]+|embed/[a-zA-Z0-9_-]+|[a-zA-Z0-9_-]+[^\s]*))"
)


def restricted(func: Callable) -> Callable:
    """Decorator to enforce whitelist authorization if ALLOWED_USERS is set."""

    @wraps(func)
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
        return await func(update, context, *args, **kwargs)

    return wrapped


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message explaining bot usage."""
    if not update.effective_message:
        return

    text = (
        "🎵 *Welcome to the YouTube Music Downloader Bot!* 🎧\n\n"
        "Send me any YouTube video or playlist link, and I will extract the audio as a "
        "high-quality MP3 (with album artwork and metadata) and send it directly to this chat!\n\n"
        "📌 *Supported Formats:*\n"
        "• Single track: `https://youtu.be/...`\n"
        "• Single video: `https://www.youtube.com/watch?v=...`\n"
        "• Full playlist: `https://www.youtube.com/playlist?list=...`\n"
        "• Radio Mix: `https://www.youtube.com/watch?v=...&list=RD...` (up to 25 tracks)\n\n"
        "💡 _Tip: Radio mixes are dynamically generated on the fly. To get an exact, unchangeable playlist, save the mix to a YouTube playlist first and share the saved `PL...` link!_\n\n"
        "Send a link now or type /help for details."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides user instructions, limitations, and help."""
    if not update.effective_message:
        return

    text = (
        "ℹ️ *How to Use This Bot:*\n\n"
        "1️⃣ Copy a YouTube link (single song, playlist, or mix).\n"
        "2️⃣ Paste and send the link here.\n"
        "3️⃣ The bot will extract, convert to MP3, and upload the tracks.\n\n"
        "💡 *Important Note on YouTube Mixes (`list=RD...`):*\n"
        "YouTube Radio Mixes are dynamically generated on the fly by YouTube's recommendation engine, "
        "so the songs may differ from your current browser session. If you want an exact, fixed playlist in a specific order, "
        "save the mix to your YouTube library first and send the saved playlist link (`list=PL...`).\n\n"
        "⚙️ *Audio Specifications:*\n"
        f"• Format: MP3 ({config.audio_bitrate} kbps)\n"
        "• Embedded tags: Title, Artist, and Cover Artwork\n\n"
        "⚠️ *Limitations & Safeguards:*\n"
        f"• Maximum file size: {config.max_file_size_mb} MB (Telegram Bot API limit).\n"
        f"• Playlists & Radio Mixes: Capped at {config.max_playlist_tracks} tracks.\n"
        "• Live streams cannot be converted to MP3.\n"
        "• Private or geo-restricted videos will be skipped gracefully."
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reports bot health, configuration, and status."""
    if not update.effective_message:
        return

    text = (
        "🟢 *Bot Status: Online*\n\n"
        "• Engine: `yt-dlp` + `FFmpeg`\n"
        f"• Max Upload Size: `{config.max_file_size_mb} MB`\n"
        f"• Audio Quality: `{config.audio_bitrate} kbps MP3`\n"
        "• Playlist Streaming: `Enabled`\n"
        "• Access Control: "
        f"`{'Restricted' if config.allowed_users else 'Public'}`\n"
        "• Ready to download audio!"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def handle_single_track(
    url: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Update,
    downloader: AudioDownloader,
) -> None:
    """Handles downloading and uploading a single YouTube track."""
    chat_id = update.effective_chat.id
    reply_to = update.effective_message.message_id

    await update_status_message(status_msg, "⬇️ *Downloading audio track...*")

    try:
        track_data = await asyncio.to_thread(downloader.download_audio, url)
    except Exception as exc:
        logger.error(f"Download error for {url}: {exc}")
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
                await context.bot.send_audio(
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

        await safe_delete_message(status_msg)
    except Exception as exc:
        logger.error(f"Failed to upload audio to Telegram: {exc}")
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
    status_msg: Update,
    downloader: AudioDownloader,
) -> None:
    """Handles downloading and uploading a YouTube playlist sequentially."""
    chat_id = update.effective_chat.id

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

    is_radio = playlist_info.get("is_radio_mix", False)
    if is_radio:
        notice_text = (
            f"📻 *YouTube Radio Mix detected:*\n"
            f"*{title}* (capped at `{total}` tracks)\n\n"
            "ℹ️ _Note: Radio mixes are dynamically generated by YouTube and may differ from your current browser queue. "
            "To download an exact, fixed playlist, save it on YouTube first and share the saved `PL...` link._\n\n"
            "Starting sequential download..."
        )
    else:
        notice_text = (
            f"📋 *Playlist detected:*\n*{title}* ({total} tracks)\n\n"
            "Starting sequential download..."
        )

    await update_status_message(status_msg, notice_text)

    sent_count = 0
    skipped_count = 0

    async for index, total_tracks, track_data, error in downloader.stream_playlist_tracks(url):
        if error:
            skipped_count += 1
            logger.warning(f"Skipping track {index}/{total_tracks}: {error}")
            await update_status_message(
                status_msg,
                f"📋 *Playlist:* {title}\n"
                f"⏳ Progress: `[{index}/{total_tracks}]`\n"
                f"⚠️ *Skipped track {index}:* `{error[:60]}`",
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
                f"⏳ Progress: `[{index}/{total_tracks}]`\n"
                f"⚠️ *Track {index} exceeds {config.max_file_size_mb}MB limit (Skipped).* ",
            )
            continue

        await update_status_message(
            status_msg,
            f"📋 *Playlist:* {title}\n"
            f"⏳ Progress: `[{index}/{total_tracks}]`\n"
            f"📤 Uploading: *{track_data.get('title')}*...",
        )

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
                    await context.bot.send_audio(
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

            # Reposition the live progress dashboard below the newly uploaded audio track
            if index < total_tracks:
                status_msg = await repost_status_message(
                    chat_id=chat_id,
                    bot=context.bot,
                    current_message=status_msg,
                    text=(
                        f"📋 *Playlist in Progress:*\n"
                        f"• Playlist: *{title}*\n"
                        f"• Total: `{total_tracks}` tracks\n"
                        f"• ✅ Delivered: `{sent_count}` tracks\n"
                        f"• ⏳ Next track: `{index + 1}/{total_tracks}`\n\n"
                        f"⬇️ _Downloading next track..._"
                    ),
                )
        except Exception as exc:
            skipped_count += 1
            logger.error(f"Error sending playlist audio track {index}: {exc}")
        finally:
            downloader.cleanup_files(audio_path, thumb_path)

    cleanup_orphaned_downloads(config.download_dir)

    # Place final completion report at the very bottom of the conversation
    final_summary = (
        f"🎉 *Playlist Download Complete!*\n\n"
        f"• Playlist: *{title}*\n"
        f"• Total: `{total}` tracks\n"
        f"• ✅ Delivered: `{sent_count}` tracks\n"
        f"• ⚠️ Skipped: `{skipped_count}` tracks"
    )
    status_msg = await repost_status_message(
        chat_id=chat_id,
        bot=context.bot,
        current_message=status_msg,
        text=final_summary,
    )


@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inspects text messages for YouTube URLs and dispatches downloads."""
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()
    match = YOUTUBE_URL_REGEX.search(text)

    if not match:
        await update.effective_message.reply_text(
            "Please send a valid YouTube video or playlist link (e.g., `https://youtu.be/...`). "
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


def create_bot_app(token: Optional[str] = None) -> Application:
    """Builds and returns the configured Telegram Application."""
    bot_token = token or config.bot_token
    if not bot_token or bot_token == "your_telegram_bot_token_here":
        bot_token = "TEST_TOKEN_FOR_INITIALIZATION"

    application = ApplicationBuilder().token(bot_token).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))

    # Register message handler for text
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Register global error handler
    application.add_error_handler(global_error_handler)

    return application


def main() -> None:
    """Starts the bot in long-polling mode."""
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

    logger.info("Initializing YouTube to Telegram Music Downloader Bot...")
    app = create_bot_app()
    logger.info("Bot is polling for updates. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
