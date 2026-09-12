"""Audio downloader and playlist processing module using yt-dlp and ffmpeg for the YouTube to Telegram Music Downloader Bot."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import yt_dlp

from config import AppConfig, config as global_config
from helpers.artwork import embed_metadata_and_artwork_to_mp3, resolve_album_art
from helpers.logger import setup_logger
from helpers.title_cleaner import clean_music_title, parse_title_and_artist

logger = setup_logger("downloader")


class AudioDownloader:
    """Handles audio extraction, MP3 conversion, metadata processing, and playlist streaming."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config or global_config
        self.download_dir = Path(self.config.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.bitrate = self.config.audio_bitrate

    def normalize_playlist_url(self, url: str) -> Tuple[str, bool, Optional[str]]:
        """Normalizes any YouTube playlist URL to its canonical target.

        Returns:
            (target_url, is_radio_mix, playlist_id)
        """
        if not url:
            return url, False, None
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            playlist_id = qs.get("list", [None])[0]

            if not playlist_id:
                return url, False, None

            is_radio = playlist_id.startswith("RD")
            if is_radio:
                # Radio Mixes require the video context query
                return url, True, playlist_id

            # Canonical playlist endpoint for standard playlists (PL, OLAK, UU, FL, etc.)
            canonical_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            return canonical_url, False, playlist_id
        except Exception as exc:
            logger.debug(f"Error normalizing playlist URL: {exc}")
            return url, False, None

    def is_playlist(self, url: str) -> bool:
        """Accurately detects if a URL is a YouTube playlist or Radio Mix."""
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

            # Check query parameters for playlist or radio mix (list=)
            qs = parse_qs(parsed.query)
            if "list" in qs and qs["list"]:
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

            raw_title = info.get("title", "Unknown Title")
            raw_artist = (
                info.get("artist")
                or info.get("creator")
                or info.get("uploader")
                or "Unknown Artist"
            )
            display_title, artist, song_name = parse_title_and_artist(
                raw_title, raw_artist
            )

            duration = int(info.get("duration") or 0)
            thumbnail_url = info.get("thumbnail")
            webpage_url = info.get("webpage_url", url)
            is_live = bool(info.get("is_live", False))
            filesize_approx = int(info.get("filesize_approx") or info.get("filesize") or 0)

            return {
                "id": info.get("id", ""),
                "title": display_title,
                "song_name": song_name,
                "artist": artist,
                "duration": duration,
                "thumbnail_url": thumbnail_url,
                "webpage_url": webpage_url,
                "is_live": is_live,
                "filesize_approx": filesize_approx,
            }

    def get_playlist_info(self, url: str) -> Dict[str, Any]:
        """Fast extraction of playlist metadata and entry list without downloading media."""
        target_url, is_radio, playlist_id = self.normalize_playlist_url(url)

        ydl_opts = {
            "skip_download": True,
            "extract_flat": "in_playlist",
            "quiet": True,
            "no_warnings": True,
        }
        if is_radio:
            ydl_opts["playlist_items"] = f"1-{self.config.max_playlist_tracks}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(target_url, download=False)
            except Exception as e:
                logger.error(f"Failed to extract playlist info for {target_url}: {e}")
                raise

            if not info:
                raise ValueError(f"No playlist metadata returned for {target_url}")

            # If yt-dlp returned a pointer/redirect object, follow the underlying target URL
            if info.get("_type") == "url" and info.get("url"):
                try:
                    info = ydl.extract_info(info["url"], download=False)
                except Exception as exc:
                    logger.warning(f"Error following playlist redirect URL: {exc}")

            raw_entries = info.get("entries") or []
            tracks: List[Dict[str, Any]] = []

            for entry in raw_entries:
                if not entry:
                    continue
                video_id = entry.get("id") or ""
                video_url = entry.get("url")
                if not video_url and video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"

                raw_title = entry.get("title", "Unknown Title")
                cleaned_title = clean_music_title(raw_title)

                tracks.append(
                    {
                        "id": video_id,
                        "title": cleaned_title,
                        "duration": int(entry.get("duration") or 0),
                        "url": video_url or "",
                    }
                )

            return {
                "id": info.get("id", playlist_id or ""),
                "title": info.get("title", "YouTube Playlist"),
                "total_tracks": len(tracks),
                "tracks": tracks,
                "is_radio_mix": is_radio,
            }

    def extract_telegram_thumbnail(
        self, mp3_path: Path, output_thumb_path: Path
    ) -> Optional[str]:
        """Extracts embedded cover artwork from MP3 and formats it to a 320x320 JPEG (<200KB) for Telegram."""
        if not mp3_path.exists():
            return None

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(mp3_path),
                "-an",
                "-vf",
                "scale=320:320:force_original_aspect_ratio=decrease",
                "-frames:v",
                "1",
                "-update",
                "1",
                "-q:v",
                "2",
                str(output_thumb_path),
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if output_thumb_path.exists() and output_thumb_path.stat().st_size > 0:
                logger.debug(f"Successfully extracted Telegram thumbnail: {output_thumb_path}")
                return str(output_thumb_path)
        except Exception as exc:
            logger.warning(f"Failed to extract telegram thumbnail: {exc}")

        return None

    def download_audio(
        self, url: str, output_dir: Optional[Union[str, Path]] = None
    ) -> Dict[str, Any]:
        """Downloads audio stream, converts to MP3, embeds thumbnail, and returns track metadata."""
        target_dir = Path(output_dir).resolve() if output_dir else self.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # Output template using video ID to avoid collision or unsafe characters
        outtmpl = str(target_dir / "%(id)s.%(ext)s")

        ydl_opts = {
            "format": "ba[ext=m4a]/ba[ext=opus]/bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(self.bitrate),
                },
            ],
            "writethumbnail": True,
            "concurrent_fragment_downloads": 4,
            "buffersize": 1024 * 64,
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
            raw_title = info.get("title", "Unknown Title")
            raw_artist = (
                info.get("artist")
                or info.get("creator")
                or info.get("uploader")
                or "Unknown Artist"
            )
            display_title, artist, song_name = parse_title_and_artist(
                raw_title, raw_artist
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

            # Locate any local candidate thumbnail left by yt-dlp on disk
            local_candidate_thumb = None
            for ext in [".jpg", ".jpeg", ".webp", ".png"]:
                candidate = target_dir / f"{video_id}{ext}"
                if candidate.exists():
                    local_candidate_thumb = candidate
                    break

            # Resolve best square cover/album artwork (iTunes -> Deezer -> Local -> YouTube Crop)
            thumb_target = target_dir / f"{video_id}_thumb.jpg"
            youtube_thumb_url = info.get("thumbnail")
            thumbnail_path = resolve_album_art(
                artist=artist,
                song_name=song_name,
                youtube_thumb_url=youtube_thumb_url,
                mp3_path=mp3_path,
                output_thumb_path=thumb_target,
                fallback_thumb_path=local_candidate_thumb,
            )

            # Embed ID3 tags and square cover art into the MP3 file itself
            if thumbnail_path and Path(thumbnail_path).exists():
                embed_metadata_and_artwork_to_mp3(
                    mp3_path=mp3_path,
                    art_path=Path(thumbnail_path),
                    title=display_title,
                    artist=artist,
                )

            return {
                "id": video_id,
                "file_path": str(mp3_path),
                "title": display_title,
                "song_name": song_name,
                "artist": artist,
                "duration": duration,
                "thumbnail_path": thumbnail_path,
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

        effective_max = max_tracks or self.config.max_playlist_tracks
        if playlist_info.get("is_radio_mix"):
            tracks = tracks[:effective_max]
        elif max_tracks and max_tracks > 0:
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
