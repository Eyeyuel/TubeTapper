"""Telegram chat progress and status message helpers."""

import telegram
from helpers.logger import setup_logger

logger = setup_logger("progress")


async def update_status_message(
    message: telegram.Message, text: str, parse_mode: str = "Markdown"
) -> None:
    """Updates an existing status message safely without crashing on BadRequest."""
    try:
        await message.edit_text(text, parse_mode=parse_mode)
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
