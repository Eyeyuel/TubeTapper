"""Telegram chat progress, status message, and visual progress bar helpers."""

from typing import Optional

import telegram
from helpers.logger import setup_logger

logger = setup_logger("progress")


def render_progress_bar(percentage: float, width: int = 10) -> str:
    """Renders a clean visual Unicode progress bar.

    Example: 60.0% -> '▰▰▰▰▰▰▱▱▱▱ 60%'
    """
    clamped = max(0.0, min(100.0, float(percentage)))
    filled_count = int(round(width * (clamped / 100.0)))
    empty_count = width - filled_count
    return f"{'▰' * filled_count}{'▱' * empty_count} {clamped:.0f}%"


async def update_status_message(
    message: telegram.Message,
    text: str,
    parse_mode: str = "Markdown",
    reply_markup: Optional[telegram.InlineKeyboardMarkup] = None,
) -> None:
    """Updates an existing status message safely without crashing on BadRequest."""
    kwargs = {"parse_mode": parse_mode}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await message.edit_text(text, **kwargs)
    except telegram.error.BadRequest as exc:
        # Ignore if the message was not modified or already deleted
        if "Message is not modified" not in str(exc):
            logger.debug(f"BadRequest while updating status message: {exc}")
    except Exception as exc:
        logger.warning(f"Unexpected error updating status message: {exc}")


async def safe_delete_message(message: telegram.Message) -> None:
    """Safely deletes a message without raising exceptions if already deleted."""
    try:
        await message.delete()
    except Exception as exc:
        logger.debug(f"Error safely deleting message: {exc}")


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
