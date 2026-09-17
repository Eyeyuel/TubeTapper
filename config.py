"""Configuration module for YouTube to Telegram Music Downloader Bot."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

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
    audio_bitrate: int = 192
    max_file_size_mb: int = 50
    max_playlist_tracks: int = 25
    max_concurrent_downloads: int = 5
    redis_url: Optional[str] = "redis://localhost:6379/0"
    youtube_cookies_file: Optional[str] = None
    youtube_proxy: Optional[str] = None
    cache_ttl_days: int = 30
    default_audio_format: str = "mp3"
    log_level: str = "INFO"
    log_format: str = "colored"
    telegram_api_server_url: Optional[str] = None
    telegram_local_mode: bool = False
    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None
    webhook_mode: bool = False
    webhook_url: Optional[str] = None
    webhook_port: int = 8000
    webhook_secret: Optional[str] = None
    youtube_proxy_pool: Optional[str] = None
    worker_mode: bool = False
    user_rate_limit: int = 5
    user_rate_window: int = 60

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
        bitrate = int(os.getenv("AUDIO_BITRATE", "192").strip())
    except ValueError:
        bitrate = 192

    # Local Telegram Bot API Server configurations (2GB file limit and LAN transfer)
    raw_api_server = os.getenv("TELEGRAM_API_SERVER_URL", "").strip()
    telegram_api_server_url = raw_api_server if raw_api_server else None

    raw_local_mode = os.getenv("TELEGRAM_LOCAL_MODE", "").strip().lower()
    telegram_local_mode = raw_local_mode in ("true", "1", "yes") or bool(telegram_api_server_url)

    # If running with local Telegram Bot API server, default file size limit increases to 2000MB (2GB)
    default_max_size = 2000 if telegram_local_mode else 50
    try:
        max_size_mb = int(os.getenv("MAX_FILE_SIZE_MB", str(default_max_size)).strip())
    except ValueError:
        max_size_mb = default_max_size

    try:
        max_playlist = int(os.getenv("MAX_PLAYLIST_TRACKS", "25").strip())
    except ValueError:
        max_playlist = 25

    try:
        max_concurrent = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "5").strip())
    except ValueError:
        max_concurrent = 5

    raw_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
    redis_url = raw_redis_url if raw_redis_url else None

    raw_cookies = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    youtube_cookies_file = raw_cookies if raw_cookies else None

    raw_proxy = os.getenv("YOUTUBE_PROXY", "").strip()
    youtube_proxy = raw_proxy if raw_proxy else None

    raw_proxy_pool = os.getenv("YOUTUBE_PROXY_POOL", "").strip()
    youtube_proxy_pool = raw_proxy_pool if raw_proxy_pool else None

    try:
        cache_ttl = int(os.getenv("CACHE_TTL_DAYS", "30").strip())
    except ValueError:
        cache_ttl = 30

    raw_format = os.getenv("DEFAULT_AUDIO_FORMAT", "mp3").strip().lower()
    default_format = "m4a" if raw_format == "m4a" else "mp3"

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    log_format = os.getenv("LOG_FORMAT", "colored").strip().lower()

    # Telegram API ID & Hash for local Bot API server registration
    raw_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    telegram_api_id = raw_api_id if raw_api_id else None

    raw_api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    telegram_api_hash = raw_api_hash if raw_api_hash else None

    # Webhook mode options
    raw_webhook_mode = os.getenv("WEBHOOK_MODE", "false").strip().lower()
    webhook_mode = raw_webhook_mode in ("true", "1", "yes")

    raw_webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    webhook_url = raw_webhook_url if raw_webhook_url else None

    try:
        webhook_port = int(os.getenv("WEBHOOK_PORT", "8000").strip())
    except ValueError:
        webhook_port = 8000

    raw_webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    webhook_secret = raw_webhook_secret if raw_webhook_secret else None

    raw_worker_mode = os.getenv("WORKER_MODE", "false").strip().lower()
    worker_mode = raw_worker_mode in ("true", "1", "yes")

    try:
        user_rate_limit = int(os.getenv("USER_RATE_LIMIT", "5").strip())
    except ValueError:
        user_rate_limit = 5

    try:
        user_rate_window = int(os.getenv("USER_RATE_WINDOW", "60").strip())
    except ValueError:
        user_rate_window = 60

    return AppConfig(
        bot_token=raw_token,
        allowed_users=allowed_users,
        download_dir=download_path,
        audio_bitrate=bitrate,
        max_file_size_mb=max_size_mb,
        max_playlist_tracks=max_playlist,
        max_concurrent_downloads=max_concurrent,
        redis_url=redis_url,
        youtube_cookies_file=youtube_cookies_file,
        youtube_proxy=youtube_proxy,
        cache_ttl_days=cache_ttl,
        default_audio_format=default_format,
        log_level=log_level,
        log_format=log_format,
        telegram_api_server_url=telegram_api_server_url,
        telegram_local_mode=telegram_local_mode,
        telegram_api_id=telegram_api_id,
        telegram_api_hash=telegram_api_hash,
        webhook_mode=webhook_mode,
        webhook_url=webhook_url,
        webhook_port=webhook_port,
        webhook_secret=webhook_secret,
        youtube_proxy_pool=youtube_proxy_pool,
        worker_mode=worker_mode,
        user_rate_limit=user_rate_limit,
        user_rate_window=user_rate_window,
    )


# Global config instance
config = load_config()
logger = setup_logger(level=config.log_level, log_format=config.log_format)

# Issue an informative warning if the token is using the template placeholder
if not config.bot_token or config.bot_token == "your_telegram_bot_token_here":
    logger.warning(
        "TELEGRAM_BOT_TOKEN is not configured or using default placeholder. "
        "Set your bot token in .env before running the live bot."
    )
