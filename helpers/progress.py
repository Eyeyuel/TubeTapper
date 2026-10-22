"""Telegram chat progress, status message, and visual progress bar helpers."""

from typing import Optional

import telegram
from helpers.logger import setup_logger
from helpers.telegram_retry import send_with_retry

logger = setup_logger("progress")


def render_progress_bar(percentage: float, width: int = 10) -> str:
    """Renders a clean visual Unicode progress bar.

    Example: 60.0% -> '[██████░░░░] 60%'
    """
    clamped = max(0.0, min(100.0, float(percentage)))
    filled_count = int(round(width * (clamped / 100.0)))
    empty_count = width - filled_count
    return f"[{'█' * filled_count}{'░' * empty_count}] {clamped:.0f}%"


async def update_status_message(
    message: telegram.Message,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    reply_markup: Optional[telegram.InlineKeyboardMarkup] = None,
) -> bool:
    """Updates an existing status message safely with retries and plain-text fallback on parse errors.

    Returns:
        True if the message was successfully updated (or was already up to date), False otherwise.
    """
    kwargs = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup

    try:
        await send_with_retry(message.edit_text, text, **kwargs)
        return True
    except telegram.error.BadRequest as exc:
        # Ignore if the message was not modified
        if "Message is not modified" in str(exc):
            return True

        # If markdown entity parsing failed, retry cleanly in plain text
        err_msg = str(exc).lower()
        if "can't parse entities" in err_msg or "entity" in err_msg:
            logger.warning(
                f"Markdown parsing failed for status update ('{exc}'). Retrying in plain text..."
            )
            try:
                kwargs_plain = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                await send_with_retry(message.edit_text, text, **kwargs_plain)
                return True
            except Exception as e2:
                logger.debug(f"Plain text fallback update also failed: {e2}")
                return False

        logger.debug(f"BadRequest while updating status message: {exc}")
        return False
    except Exception as exc:
        logger.warning(f"Unexpected error updating status message: {exc}")
        return False


async def safe_delete_message(message: telegram.Message) -> None:
    """Safely deletes a message without raising exceptions if already deleted."""
    try:
        await message.delete()
    except Exception as exc:
        logger.debug(f"Error safely deleting message: {exc}")


async def update_or_reply_status(
    message: Optional[telegram.Message],
    text: str,
    parse_mode: Optional[str] = "Markdown",
    reply_markup: Optional[telegram.InlineKeyboardMarkup] = None,
    chat_id: Optional[int] = None,
    bot: Optional[telegram.Bot] = None,
) -> telegram.Message:
    """Attempts to edit an existing status message; if editing fails, sends a new message to guarantee UI continuity."""
    if message:
        success = await update_status_message(
            message, text, parse_mode=parse_mode, reply_markup=reply_markup
        )
        if success:
            return message

    logger.info("Editing status message failed or unavailable; falling back to sending fresh message.")
    kwargs = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup

    try:
        if message and hasattr(message, "reply_text"):
            new_msg = await send_with_retry(message.reply_text, text, **kwargs)
            await safe_delete_message(message)
            return new_msg
        elif chat_id and bot:
            new_msg = await send_with_retry(bot.send_message, chat_id=chat_id, text=text, **kwargs)
            if message:
                await safe_delete_message(message)
            return new_msg
    except telegram.error.BadRequest as exc:
        err_msg = str(exc).lower()
        if ("can't parse entities" in err_msg or "entity" in err_msg) and parse_mode:
            kwargs.pop("parse_mode", None)
            if message and hasattr(message, "reply_text"):
                new_msg = await send_with_retry(message.reply_text, text, **kwargs)
                await safe_delete_message(message)
                return new_msg
            elif chat_id and bot:
                new_msg = await send_with_retry(bot.send_message, chat_id=chat_id, text=text, **kwargs)
                if message:
                    await safe_delete_message(message)
                return new_msg
        logger.error(f"Failed to send fallback status message: {exc}")
    except Exception as exc:
        logger.error(f"Unexpected error sending fallback status message: {exc}")

    return message


async def repost_status_message(
    chat_id: int,
    bot: telegram.Bot,
    current_message: Optional[telegram.Message],
    text: str,
    parse_mode: str = "Markdown",
    reply_markup: Optional[telegram.InlineKeyboardMarkup] = None,
) -> telegram.Message:
    """Safely deletes the previous status message and posts a new one at the bottom of the chat."""
    if current_message:
        await safe_delete_message(current_message)
    kwargs = {"parse_mode": parse_mode}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        new_msg = await bot.send_message(
            chat_id=chat_id, text=text, **kwargs
        )
        return new_msg
    except Exception as exc:
        logger.warning(f"Error reposting status message to bottom: {exc}")
        return current_message

