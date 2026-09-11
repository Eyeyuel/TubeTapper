"""YouTube music title cleaner and metadata parser."""

import re
from typing import Optional, Tuple

# Patterns representing metadata junk commonly found in YouTube music video titles
JUNK_PATTERNS = [
    r"\[\s*official\s+music\s+video\s*\]",
    r"\(\s*official\s+music\s+video\s*\)",
    r"\[\s*official\s+video\s*\]",
    r"\(\s*official\s+video\s*\)",
    r"\[\s*official\s+audio\s*\]",
    r"\(\s*official\s+audio\s*\)",
    r"\[\s*official\s+lyric\s+video\s*\]",
    r"\(\s*official\s+lyric\s+video\s*\)",
    r"\[\s*official\s+lyrics\s*\]",
    r"\(\s*official\s+lyrics\s*\)",
    r"\[\s*official\s+visualizer\s*\]",
    r"\(\s*official\s+visualizer\s*\)",
    r"\[\s*official\s+(?:4k|hd|hq)?\s*video\s*\]",
    r"\(\s*official\s+(?:4k|hd|hq)?\s*video\s*\)",
    r"\[\s*music\s+video\s*\]",
    r"\(\s*music\s+video\s*\)",
    r"\[\s*lyric\s+video\s*\]",
    r"\(\s*lyric\s+video\s*\)",
    r"\[\s*lyrics?\s*\]",
    r"\(\s*lyrics?\s*\)",
    r"\[\s*audio\s*\]",
    r"\(\s*audio\s*\)",
    r"\[\s*visualizer\s*\]",
    r"\(\s*visualizer\s*\)",
    r"\[\s*(?:hd|4k|hq|explicit|clean)\s*\]",
    r"\(\s*(?:hd|4k|hq|explicit|clean)\s*\)",
    r"\[\s*video\s*\]",
    r"\(\s*video\s*\)",
    r"\|\s*official.*$",
    r"//\s*official.*$",
]


def clean_music_title(raw_title: str) -> str:
    """Removes common YouTube promotional tags like [Official Music Video] from titles.

    Example:
        'Kodak Black - Tunnel Vision [Official Music Video]' -> 'Kodak Black - Tunnel Vision'
    """
    if not raw_title:
        return ""

    cleaned = raw_title
    for pattern in JUNK_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Remove empty brackets or parentheses leftover
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)

    # Normalize whitespace and strip trailing artifacts
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" -|/").strip()
    return cleaned


def parse_title_and_artist(
    raw_title: str, fallback_artist: Optional[str] = None
) -> Tuple[str, str, str]:
    """Parses a YouTube title into (cleaned_title, artist, song_name).

    Returns:
        (cleaned_title, artist, song_name)
    """
    cleaned = clean_music_title(raw_title)

    # Check if format is "Artist - Title"
    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        artist = parts[0].strip()
        song_name = parts[1].strip()
    else:
        artist = fallback_artist.strip() if fallback_artist else ""
        song_name = cleaned

    return cleaned, artist, song_name
