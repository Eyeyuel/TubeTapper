"""Cover and Album Art fetcher using iTunes, Deezer, and YouTube 1:1 Square Cropping."""

import json
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from helpers.logger import setup_logger

logger = setup_logger("artwork")


def crop_to_square_jpeg(source_input: str, output_path: Path) -> Optional[str]:
    """Crops an image or video/audio cover to a 1:1 centered square JPEG (320x320) for Telegram."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Center-crop to 1:1 square, then scale to 320x320 JPEG
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_input),
            "-an",
            "-vf",
            "crop=min(iw\\,ih):min(iw\\,ih),scale=320:320",
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path)
    except Exception as exc:
        logger.warning(f"Error cropping thumbnail to square: {exc}")
    return None


def fetch_itunes_artwork(query: str, output_path: Path) -> Optional[Dict[str, str]]:
    """Queries Apple iTunes Search API for official high-resolution square album cover art.

    Returns:
        Dict with keys: 'artwork_path', 'album', 'artist', 'title' or None
    """
    if not query:
        return None

    try:
        url = (
            f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}"
            "&entity=song&limit=1"
        )
        with httpx.Client(timeout=3.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    item = results[0]
                    raw_art = item.get("artworkUrl100", "")
                    # Upgrade to 600x600 high resolution cover art
                    hq_art = raw_art.replace("100x100bb", "600x600bb")

                    # Download and scale to 320x320 JPEG
                    thumb_res = crop_to_square_jpeg(hq_art, output_path)
                    if thumb_res:
                        logger.info(f"Fetched official iTunes cover art for: {query}")
                        return {
                            "artwork_path": str(output_path),
                            "album": item.get("collectionName", ""),
                            "artist": item.get("artistName", ""),
                            "title": item.get("trackName", ""),
                        }
    except Exception as exc:
        logger.debug(f"iTunes artwork search skipped or unavailable: {exc}")

    return None


def fetch_deezer_artwork(query: str, output_path: Path) -> Optional[Dict[str, str]]:
    """Queries Deezer public search API for official square album cover art."""
    if not query:
        return None

    try:
        url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}&limit=1"
        with httpx.Client(timeout=3.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("data", [])
                if results:
                    item = results[0]
                    album = item.get("album", {})
                    art_url = (
                        album.get("cover_big")
                        or album.get("cover_medium")
                        or album.get("cover")
                    )
                    if art_url:
                        thumb_res = crop_to_square_jpeg(art_url, output_path)
                        if thumb_res:
                            logger.info(f"Fetched official Deezer cover art for: {query}")
                            return {
                                "artwork_path": str(output_path),
                                "album": album.get("title", ""),
                                "artist": item.get("artist", {}).get("name", ""),
                                "title": item.get("title", ""),
                            }
    except Exception as exc:
        logger.debug(f"Deezer artwork search skipped or unavailable: {exc}")

    return None


def resolve_album_art(
    artist: str,
    song_name: str,
    youtube_thumb_url: Optional[str],
    mp3_path: Optional[Path],
    output_thumb_path: Path,
    fallback_thumb_path: Optional[Path] = None,
) -> Optional[str]:
    """Retrieves the best available square album art for a music track.

    Strategy:
        1. Try Apple iTunes Search API for official 600x600 square album cover.
        2. Try Deezer Search API for official square cover.
        3. Try local fallback thumbnail if provided on disk.
        4. Fallback: Take YouTube's thumbnail URL and crop to a 1:1 square.
        5. Fallback: Embedded video stream from MP3 cropped to 1:1 square.
    """
    search_query = f"{artist} {song_name}".strip() if artist else song_name.strip()

    # 1. Try iTunes
    itunes_res = fetch_itunes_artwork(search_query, output_thumb_path)
    if itunes_res:
        return itunes_res["artwork_path"]

    # 2. Try Deezer
    deezer_res = fetch_deezer_artwork(search_query, output_thumb_path)
    if deezer_res:
        return deezer_res["artwork_path"]

    # 3. Local fallback thumbnail if present on disk
    if fallback_thumb_path and Path(fallback_thumb_path).exists():
        local_res = crop_to_square_jpeg(str(fallback_thumb_path), output_thumb_path)
        if local_res:
            return local_res
        return str(fallback_thumb_path)

    # 4. Fallback: YouTube thumbnail URL cropped to 1:1 square
    if youtube_thumb_url:
        yt_res = crop_to_square_jpeg(youtube_thumb_url, output_thumb_path)
        if yt_res:
            logger.debug("Used YouTube thumbnail cropped to 1:1 square")
            return yt_res

    # 5. Fallback: Embedded video stream from MP3 cropped to 1:1 square
    if mp3_path and mp3_path.exists():
        mp3_res = crop_to_square_jpeg(str(mp3_path), output_thumb_path)
        if mp3_res:
            logger.debug("Extracted and cropped embedded cover art to 1:1 square")
            return mp3_res

    return None


def embed_metadata_and_artwork_to_mp3(
    mp3_path: Path,
    art_path: Path,
    title: str,
    artist: str,
) -> bool:
    """Embeds ID3v2 tags (title, artist) and front cover art into MP3 file via FFmpeg."""
    if not mp3_path.exists() or not art_path.exists():
        return False

    temp_mp3 = mp3_path.with_name(f"{mp3_path.stem}_tagged.mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3_path),
        "-i",
        str(art_path),
        "-map",
        "0:a:0",
        "-map",
        "1:v:0",
        "-c",
        "copy",
        "-id3v2_version",
        "3",
        "-metadata:s:v",
        "title=Album cover",
        "-metadata:s:v",
        "comment=Cover (front)",
        "-metadata",
        f"title={title}",
        "-metadata",
        f"artist={artist}",
        str(temp_mp3),
    ]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode == 0 and temp_mp3.exists() and temp_mp3.stat().st_size > 0:
            temp_mp3.replace(mp3_path)
            logger.debug(
                f"Successfully embedded clean metadata and cover art into {mp3_path.name}"
            )
            return True
    except Exception as exc:
        logger.warning(f"Failed to embed metadata/artwork to MP3: {exc}")
    finally:
        if temp_mp3.exists():
            try:
                temp_mp3.unlink(missing_ok=True)
            except OSError:
                pass
    return False

