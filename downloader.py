"""Audio downloader and playlist processing module using yt-dlp and ffmpeg for the YouTube to Telegram Music Downloader Bot."""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import yt_dlp

from config import AppConfig, config as global_config
from helpers.logger import setup_logger

logger = setup_logger("downloader")


class AudioDownloader:
    """Handles audio extraction, MP3 conversion, metadata processing, and playlist streaming."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config or global_config
        self.download_dir = Path(self.config.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.bitrate = self.config.audio_bitrate

    def is_playlist(self, url: str) -> bool:
        """Accurately detects if a URL is a YouTube playlist."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            if "youtube.com" not in netloc and "youtu.be" not in netloc:
                return False

            # Explicit /playlist endpoint
            if "/playlist" in parsed.path:
                return True

            # Check query parameters
            qs = parse_qs(parsed.query)
            if "list" in qs and qs["list"]:
                playlist_id = qs["list"][0]
                # Algorithmic radio mixes (RD...) are not static extractable playlists
                if playlist_id.startswith("RD") and "v" in qs:
                    return False
                return True
        except Exception as e:
            logger.debug(f"Error parsing URL in is_playlist: {e}")
            return False

        return False

    def get_video_info(self, url: str) -> Dict[str, Any]:
        """Extracts video metadata without downloading the media stream."""
        ydl_opts = {
            "skip_download": True,
            "extract_flat": False,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Failed to extract info for {url}: {e}")
                raise

            if not info:
                raise ValueError(f"No metadata returned for {url}")

            title = info.get("title", "Unknown Title")
            artist = (
                info.get("artist")
                or info.get("creator")
                or info.get("uploader")
                or "Unknown Artist"
            )
            duration = int(info.get("duration") or 0)
            thumbnail_url = info.get("thumbnail")
            webpage_url = info.get("webpage_url", url)
            is_live = bool(info.get("is_live", False))
            filesize_approx = int(info.get("filesize_approx") or info.get("filesize") or 0)

            return {
                "id": info.get("id", ""),
                "title": title,
                "artist": artist,
                "duration": duration,
                "thumbnail_url": thumbnail_url,
                "webpage_url": webpage_url,
                "is_live": is_live,
                "filesize_approx": filesize_approx,
            }

    def get_playlist_info(self, url: str) -> Dict[str, Any]:
        """Fast extraction of playlist metadata and entry list without downloading media."""
        ydl_opts = {
            "skip_download": True,
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Failed to extract playlist info for {url}: {e}")
                raise

            if not info:
                raise ValueError(f"No playlist metadata returned for {url}")

            raw_entries = info.get("entries") or []
            tracks: List[Dict[str, Any]] = []

            for entry in raw_entries:
                if not entry:
                    continue
                video_id = entry.get("id") or ""
                video_url = entry.get("url")
                if not video_url and video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"

                tracks.append(
                    {
                        "id": video_id,
                        "title": entry.get("title", "Unknown Title"),
                        "duration": int(entry.get("duration") or 0),
                        "url": video_url or "",
                    }
                )

            return {
                "id": info.get("id", ""),
                "title": info.get("title", "YouTube Playlist"),
                "total_tracks": len(tracks),
                "tracks": tracks,
            }

    def download_audio(
        self, url: str, output_dir: Optional[Union[str, Path]] = None
    ) -> Dict[str, Any]:
        """Downloads audio stream, converts to MP3, embeds thumbnail, and returns track metadata."""
        target_dir = Path(output_dir).resolve() if output_dir else self.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # Output template using video ID to avoid collision or unsafe characters
        outtmpl = str(target_dir / "%(id)s.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(self.bitrate),
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                },
                {
                    "key": "EmbedThumbnail",
                    "already_have_thumbnail": False,
                },
            ],
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
            except Exception as e:
                logger.error(f"Failed to download audio for {url}: {e}")
                raise

            if not info:
                raise ValueError(f"Download failed for {url}")

            video_id = info.get("id", "")
            title = info.get("title", "Unknown Title")
            artist = (
                info.get("artist")
                or info.get("creator")
                or info.get("uploader")
                or "Unknown Artist"
            )
            duration = int(info.get("duration") or 0)

            # Locate the output MP3 file
            mp3_path = target_dir / f"{video_id}.mp3"
            if not mp3_path.exists():
                # Fallback: check for any mp3 matching video_id in the directory
                candidates = list(target_dir.glob(f"*{video_id}*.mp3"))
                if candidates:
                    mp3_path = candidates[0]
                else:
                    raise FileNotFoundError(f"Expected MP3 file not found in {target_dir}")

            file_size = mp3_path.stat().st_size

            # Locate thumbnail file if preserved on disk
            thumbnail_path = None
            for ext in [".jpg", ".jpeg", ".webp", ".png"]:
                candidate_thumb = target_dir / f"{video_id}{ext}"
                if candidate_thumb.exists():
                    thumbnail_path = candidate_thumb
                    break

            return {
                "id": video_id,
                "file_path": str(mp3_path),
                "title": title,
                "artist": artist,
                "duration": duration,
                "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
                "filesize": file_size,
                "exceeds_limit": file_size > self.config.max_file_size_bytes,
            }

    async def stream_playlist_tracks(
        self,
        url: str,
        max_tracks: Optional[int] = None,
        output_dir: Optional[Union[str, Path]] = None,
    ) -> AsyncGenerator[Tuple[int, int, Optional[Dict[str, Any]], Optional[str]], None]:
        """Asynchronously streams downloaded tracks from a playlist one by one.

        Yields:
            (track_index, total_tracks, track_data_or_none, error_or_none)
        """
        playlist_info = await asyncio.to_thread(self.get_playlist_info, url)
        tracks = playlist_info.get("tracks", [])

        if max_tracks and max_tracks > 0:
            tracks = tracks[:max_tracks]

        total = len(tracks)

        for index, track in enumerate(tracks, start=1):
            track_url = track.get("url")
            if not track_url:
                yield (index, total, None, "Invalid or missing video URL")
                continue

            try:
                track_data = await asyncio.to_thread(
                    self.download_audio, track_url, output_dir
                )
                yield (index, total, track_data, None)
            except Exception as exc:
                logger.warning(
                    f"Error downloading track {index}/{total} ({track.get('title')}): {exc}"
                )
                yield (index, total, None, str(exc))

    def cleanup_files(self, *paths: Optional[Union[str, Path]]) -> None:
        """Safely removes temporary files without raising errors if already removed."""
        for p in paths:
            if p is None:
                continue
            try:
                path_obj = Path(p)
                if path_obj.exists() and path_obj.is_file():
                    path_obj.unlink(missing_ok=True)
                    logger.debug(f"Removed temporary file: {path_obj}")
            except Exception as e:
                logger.warning(f"Failed to remove file {p}: {e}")
