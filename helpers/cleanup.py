"""Orphaned download cleanup utility for disk space protection."""

import time
from pathlib import Path
from typing import Optional, Union

from config import config as global_config
from helpers.logger import setup_logger

logger = setup_logger("cleanup")


def cleanup_orphaned_downloads(
    download_dir: Optional[Union[str, Path]] = None,
    max_age_seconds: int = 1800,
) -> int:
    """Scans the downloads directory and removes files older than max_age_seconds.

    Args:
        download_dir: Target directory path (defaults to config.download_dir).
        max_age_seconds: Threshold age in seconds (default: 30 minutes / 1800s).

    Returns:
        Number of orphaned files successfully removed.
    """
    target_dir = Path(download_dir) if download_dir else Path(global_config.download_dir)
    if not target_dir.exists() or not target_dir.is_dir():
        return 0

    now = time.time()
    removed_count = 0

    for file_path in target_dir.iterdir():
        if not file_path.is_file():
            continue

        try:
            mtime = file_path.stat().st_mtime
            age = now - mtime
            if age > max_age_seconds:
                file_path.unlink(missing_ok=True)
                removed_count += 1
                logger.info(f"Removed orphaned file ({age:.0f}s old): {file_path.name}")
        except Exception as exc:
            logger.warning(f"Could not inspect or delete {file_path.name}: {exc}")

    return removed_count
