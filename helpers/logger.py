"""Structured, colorized logger for the YouTube to Telegram Music Downloader Bot."""

import json
import logging
import sys
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Custom formatter to provide colored, human-readable terminal output."""

    GREY = "\x1b[38;20m"
    BLUE = "\x1b[34;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    FORMAT_PREFIX = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    FORMATS = {
        logging.DEBUG: GREY + FORMAT_PREFIX + RESET,
        logging.INFO: GREEN + FORMAT_PREFIX + RESET,
        logging.WARNING: YELLOW + FORMAT_PREFIX + RESET,
        logging.ERROR: RED + FORMAT_PREFIX + RESET,
        logging.CRITICAL: BOLD_RED + FORMAT_PREFIX + RESET,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno, self.FORMAT_PREFIX)
        formatter = logging.Formatter(log_fmt, datefmt=self.DATE_FORMAT)
        return formatter.format(record)


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logger(name: str = "yt_telegram_bot", level: Optional[str] = None, log_format: str = "colored") -> logging.Logger:
    """Sets up and returns a configured logger instance."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_level = (level or "INFO").upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))

        handler = logging.StreamHandler(sys.stdout)
        if log_format == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(ColoredFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


# Default logger instance
logger = setup_logger()
