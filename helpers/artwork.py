"""Cover and Album Art fetcher using iTunes, Deezer, and YouTube 1:1 Square Cropping."""

import concurrent.futures
import json
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

import asyncio
import httpx

from helpers.logger import setup_logger

logger = setup_logger("artwork")

async def crop_to_square_jpeg_async(source_input: str, output_path: Path) -> Optional[str]:
    """Crops an image or video/audio cover to a 1:1 centered square JPEG (320x320) for Telegram."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        is_url = str(source_input).startswith("http://") or str(source_input).startswith("https://")

        if is_url:
            try:
                async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
                    resp = await client.get(str(source_input))
                    if resp.status_code == 200 and resp.content:
                        proc = await asyncio.create_subprocess_exec(
                            "ffmpeg", "-y", "-i", "pipe:0",
                            "-an", "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=320:320",
                            "-frames:v", "1", "-update", "1", "-q:v", "2",
                            str(output_path),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.communicate(input=resp.content)
                        if output_path.exists() and output_path.stat().st_size > 0:
                            return str(output_path)
            except Exception as exc:
                logger.debug(f"HTTP image fetch failed, falling back to direct ffmpeg input: {exc}")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(source_input),
            "-an", "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=320:320",
            "-frames:v", "1", "-update", "1", "-q:v", "2",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path)
    except Exception as exc:
        logger.warning(f"Error cropping thumbnail to square: {exc}")
    return None


async def fetch_itunes_artwork_async(query: str, output_path: Path) -> Optional[Dict[str, str]]:
    if not query:
        return None

    try:
        url = (
            f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}"
            "&entity=song&limit=1"
        )
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    item = results[0]
                    raw_art = item.get("artworkUrl100", "")
                    hq_art = raw_art.replace("100x100bb", "600x600bb")

                    thumb_res = await crop_to_square_jpeg_async(hq_art, output_path)
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


async def fetch_deezer_artwork_async(query: str, output_path: Path) -> Optional[Dict[str, str]]:
    if not query:
        return None

    try:
        url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}&limit=1"
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=True) as client:
            resp = await client.get(url)
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
                        thumb_res = await crop_to_square_jpeg_async(art_url, output_path)
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


async def _resolve_album_art_async(
    artist: str,
    song_name: str,
    youtube_thumb_url: Optional[str],
    mp3_path: Optional[Path],
    output_thumb_path: Path,
    fallback_thumb_path: Optional[Path] = None,
) -> Optional[str]:
    search_query = f"{artist} {song_name}".strip() if artist else song_name.strip()

    if search_query:
        # Run iTunes and Deezer requests concurrently
        f_itunes = asyncio.create_task(fetch_itunes_artwork_async(search_query, output_thumb_path))
        f_deezer = asyncio.create_task(fetch_deezer_artwork_async(search_query, output_thumb_path))
        
        # We wait for iTunes (first choice) up to 1.5s
        try:
            itunes_res = await asyncio.wait_for(f_itunes, timeout=1.5)
            if itunes_res:
                f_deezer.cancel()
                return itunes_res["artwork_path"]
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug(f"iTunes artwork timeout or error: {exc}")

        # If iTunes failed, wait for Deezer (fallback)
        try:
            deezer_res = await asyncio.wait_for(f_deezer, timeout=1.5)
            if deezer_res:
                return deezer_res["artwork_path"]
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug(f"Deezer artwork timeout or error: {exc}")

    if fallback_thumb_path and Path(fallback_thumb_path).exists():
        local_res = await crop_to_square_jpeg_async(str(fallback_thumb_path), output_thumb_path)
        if local_res:
            return local_res
        return str(fallback_thumb_path)

    if youtube_thumb_url:
        yt_res = await crop_to_square_jpeg_async(youtube_thumb_url, output_thumb_path)
        if yt_res:
            logger.debug("Used YouTube thumbnail cropped to 1:1 square")
            return yt_res

    if mp3_path and mp3_path.exists():
        mp3_res = await crop_to_square_jpeg_async(str(mp3_path), output_thumb_path)
        if mp3_res:
            logger.debug("Extracted and cropped embedded cover art to 1:1 square")
            return mp3_res

    return None

def resolve_album_art(
    artist: str,
    song_name: str,
    youtube_thumb_url: Optional[str],
    mp3_path: Optional[Path],
    output_thumb_path: Path,
    fallback_thumb_path: Optional[Path] = None,
) -> Optional[str]:
    return asyncio.run(_resolve_album_art_async(
        artist, song_name, youtube_thumb_url, mp3_path, output_thumb_path, fallback_thumb_path
    ))


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

