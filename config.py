"""Configuration module for YouTube to Telegram Music Downloader Bot."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

from helpers.logger import setup_logger

# Load environment variables from .env file
load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    """Application configuration container."""

    bot_token: str
    allowed_users: Set[int] = field(default_factory=set)
    download_dir: Path = field(default=Path("./downloads"))
    audio_bitrate: int = 320
    max_file_size_mb: int = 50
    log_level: str = "INFO"

    @property
    def max_file_size_bytes(self) -> int:
        """Returns max file size in bytes."""
        return self.max_file_size_mb * 1024 * 1024

    def is_user_allowed(self, user_id: int) -> bool:
        """Checks if a user is allowed to use the bot.

        If allowed_users is empty, all users are permitted.
        """
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users


def load_config() -> AppConfig:
    """Loads configuration from environment variables with sensible defaults."""
    raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    # Parse allowed users (comma-separated list of Telegram user IDs)
    raw_allowed = os.getenv("ALLOWED_USERS", "").strip()
    allowed_users: Set[int] = set()
    if raw_allowed:
        for item in raw_allowed.split(","):
            cleaned = item.strip()
            if cleaned.isdigit():
                allowed_users.add(int(cleaned))

    raw_download_dir = os.getenv("DOWNLOAD_DIR", "./downloads").strip()
    download_path = Path(raw_download_dir).resolve()
    # Ensure download directory exists
    download_path.mkdir(parents=True, exist_ok=True)

    try:
        bitrate = int(os.getenv("AUDIO_BITRATE", "320").strip())
    except ValueError:
        bitrate = 320

    try:
        max_size_mb = int(os.getenv("MAX_FILE_SIZE_MB", "50").strip())
    except ValueError:
        max_size_mb = 50

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    return AppConfig(
        bot_token=raw_token,
        allowed_users=allowed_users,
        download_dir=download_path,
        audio_bitrate=bitrate,
        max_file_size_mb=max_size_mb,
        log_level=log_level,
    )


# Global config instance
config = load_config()
logger = setup_logger(level=config.log_level)

# Issue an informative warning if the token is using the template placeholder
if not config.bot_token or config.bot_token == "your_telegram_bot_token_here":
    logger.warning(
        "TELEGRAM_BOT_TOKEN is not configured or using default placeholder. "
        "Set your bot token in .env before running the live bot."
    )
