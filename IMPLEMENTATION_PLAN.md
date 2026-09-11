# YouTube to Telegram Music Downloader Bot - Master Implementation Plan

A production-grade, asynchronous Telegram bot that accepts single YouTube video or playlist links, extracts high-quality audio (MP3) with embedded metadata and artwork via `yt-dlp` and `ffmpeg`, and delivers them directly into Telegram's native audio player.

---

## 1. Prerequisites & Required User Inputs

Before running the bot in production, the user must provide the following:

| Requirement | Description | How to Obtain | Mandatory? |
| :--- | :--- | :--- | :--- |
| **Telegram Bot Token** | Unique API authentication token for your bot | Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, name it, and copy the token (`123456789:ABCdef...`). | **Yes** |
| **Python 3.12+** | Runtime environment | Already installed (`Python 3.12.3`). | **Yes (Ready)** |
| **FFmpeg 6.1+** | Audio extraction, conversion & tagging engine | Already installed (`ffmpeg 6.1.1`). | **Yes (Ready)** |
| **Admin User ID** *(Optional)* | Telegram numerical user ID to restrict bot usage to you only | Send any message to [@userinfobot](https://t.me/userinfobot) on Telegram to get your ID. | Optional |
| **YouTube Cookies** *(Optional)* | Netscape-format cookie file for age-restricted/bot-flagged tracks | Exported via browser extension (e.g., "Get cookies.txt locally") only if YouTube blocks your IP. | Optional |

---

## 2. Technical Architecture & Component Overview

```
testAntigravity/
├── .env.example              # Template for environment variables
├── .env                      # Local secret file (contains TELEGRAM_BOT_TOKEN)
├── .gitignore                # Excludes .venv, .env, downloads/, __pycache__
├── requirements.txt          # Production dependencies
├── config.py                 # Central configuration and validation
├── downloader.py             # yt-dlp & ffmpeg wrapper (metadata, extraction, cleanup)
├── bot.py                    # python-telegram-bot application & handlers
├── helpers/
│   ├── __init__.py
│   ├── logger.py             # Structured color logging
│   └── progress.py           # Telegram chat progress updater
├── tests/
│   ├── __init__.py
│   ├── test_config.py        # Config loader tests
│   ├── test_downloader.py    # Audio extraction & metadata tests
│   ├── test_playlist.py      # Playlist parsing & generator tests
│   └── test_bot.py           # Telegram handler routing tests
├── downloads/                # Ephemeral scratch folder for downloading tracks
├── IMPLEMENTATION_PLAN.md    # This plan & checklists
└── AGENT_COMMAND_PLAN.md     # AI agent execution protocol & commands
```

---

## 3. Phase-by-Phase Implementation Plan

---

### Phase 1: Environment Setup, Configuration & Scaffolding

**Objective**: Create the isolated virtual environment, define dependencies, set up configuration management, and create the foundational directory structure.

#### Deliverables:
- Python virtual environment `.venv`
- `requirements.txt` with locked major versions (`python-telegram-bot`, `yt-dlp`, `python-dotenv`, `pytest`, `pytest-asyncio`)
- `.gitignore` (ignoring `.env`, `.venv/`, `downloads/`, `*.mp3`, `*.jpg`, `__pycache__/`)
- `.env.example` and template `.env`
- `config.py` with pydantic-like dataclass validation (Bot Token, download directories, max file size 50MB, default audio bitrate 192/320kbps, allowed users list)
- `helpers/logger.py` for structured logging

#### Phase 1 Checklist:
- [x] Python 3 virtual environment created in `.venv`
- [x] All dependencies in `requirements.txt` installed and verified
- [x] `.gitignore` created to prevent leaks of `.env` and audio files
- [x] `.env.example` documented with descriptions
- [x] `config.py` implemented with validation for missing token and directory initialization
- [x] Unit test `tests/test_config.py` passes

---

### Phase 2: Core Audio Extraction & Metadata Processing (`downloader.py`)

**Objective**: Build a robust wrapper around `yt-dlp` to extract single audio tracks, convert them to MP3, embed cover art and ID3 metadata (title, artist, duration), and guarantee local file cleanup.

#### Deliverables:
- `downloader.py` class `AudioDownloader`
- Methods:
  - `get_video_info(url)`: Fast metadata extraction without downloading (title, artist, duration, thumbnail URL, estimated size)
  - `download_audio(url, output_dir)`: Downloads audio stream, invokes `ffmpeg` post-processor for MP3 conversion (192kbps), downloads thumbnail, tags ID3 (artist, title, album, cover art)
  - `cleanup_files(*file_paths)`: Safe removal of downloaded temporary files
- Edge case handling for sanitizing file names with special characters or emoji

#### Phase 2 Checklist:
- [x] `AudioDownloader.get_video_info()` implemented without triggering full stream download
- [x] `AudioDownloader.download_audio()` produces valid `.mp3` with embedded ID3 tags and thumbnail
- [x] File size check implemented (detects if output exceeds Telegram's 50MB limit)
- [x] Automated cleanup utility removes all temporary files after processing
- [x] Unit test `tests/test_downloader.py` verifies metadata extraction and audio conversion

---

### Phase 3: Playlist Parsing & Stream Processing Engine

**Objective**: Extend the download engine to support YouTube playlist URLs, extracting playlist items without crashing on private or deleted videos, and streaming results track-by-track.

#### Deliverables:
- Playlist detection regex & parsing logic in `downloader.py`
- Methods:
  - `is_playlist(url)`: Distinguish between single track and playlist URL
  - `get_playlist_info(url)`: Fetch playlist title and list of video items (id, title, duration) without downloading media
  - `stream_playlist_tracks(playlist_url)`: Asynchronous generator that yields one downloaded track at a time
- Error resilience: If video 4 in a 20-video playlist is copyright-blocked or deleted, log the error, notify user, and continue to video 5 without aborting the playlist.

#### Phase 3 Checklist:
- [x] `is_playlist()` correctly identifies playlist URLs (`list=` parameter) vs single video URLs
- [x] `get_playlist_info()` extracts track count and track list cleanly
- [x] Track-by-track generator implemented for streaming delivery
- [x] Skipped/deleted/restricted video handler implemented with clear error reporting
- [x] Unit test `tests/test_playlist.py` verifies playlist extraction logic

---

### Phase 4: Telegram Bot Interface & Interactive Workflows (`bot.py`)

**Objective**: Build the Telegram bot application using `python-telegram-bot` v21+, with command handlers, YouTube URL listener, and real-time user progress messages.

#### Deliverables:
- `bot.py` entry point with `ApplicationBuilder`
- Handlers:
  - `/start`: Welcome message, capabilities, usage examples
  - `/help`: Detailed instructions, file size limitations, troubleshooting
  - `/status`: System status and active download indicators
  - Text Message Handler: Detects YouTube URLs via regex
- Interactive UI Feedback:
  - Immediate reply: *"🔎 Analyzing YouTube link..."*
  - Single video: *"⬇️ Downloading: **Song Title**..."* -> *"📤 Uploading to Telegram..."* -> sends native audio via `context.bot.send_audio(chat_id, audio=file, title=title, performer=artist, duration=duration, thumbnail=thumb)`
  - Playlist: *"📋 Playlist detected: 12 tracks found. Starting sequential download..."* -> updates progress `[3/12]` -> delivers each track as soon as it is ready
- Inline cleanup: Deletes local file immediately after `send_audio` succeeds or fails

#### Phase 4 Checklist:
- [x] `/start` and `/help` commands configured and responding with rich Markdown
- [x] URL filter intercepts both `youtube.com` and `youtu.be` links
- [x] Live progress status updates sent and updated in chat
- [x] Native Telegram audio player integration (`send_audio`) with title, performer, duration, and cover art
- [x] Sequential delivery of playlist items with running progress counter
- [x] Local files strictly cleaned up after send

---

### Phase 5: Concurrency, Limits & Safeguards

**Objective**: Protect the bot from crashes, disk overflows, rate limits, and Telegram's 50MB file size ceiling.

#### Deliverables:
- **Telegram 50MB Guard**: If an audio file or long video exceeds 50MB, the bot catches it, aborts upload, and provides an informative message (e.g., *"File exceeds Telegram's 50MB bot upload limit"*).
- **Concurrency / Queueing**: Ensure multiple users or rapid requests don't choke the server's CPU or trigger YouTube IP rate-limiting.
- **Access Control**: Optional whitelist filter (if `ALLOWED_USERS` is set in `.env`, reject unauthorized users).
- **Auto-prune**: Background job or check to remove any orphaned `.mp3` or `.temp` files older than 30 minutes in `downloads/`.

#### Phase 5 Checklist:
- [x] 50MB file size check implemented before attempting Telegram upload
- [x] Unauthorized user rejection (if whitelist configured)
- [x] Disk space protection: ephemeral files automatically pruned
- [x] Graceful exception handling for network dropouts, Telegram flood limits, or YouTube blocks
- [x] Unit/Integration tests in `tests/test_safeguards.py` verify limits and error paths

---

### Phase 6: Final Verification, Production Readiness & Documentation

**Objective**: Complete end-to-end integration testing, package the project for production, and produce complete user documentation.

#### Deliverables:
- `README.md` with complete setup instructions, features, and troubleshooting
- Systemd service template (`youtube-bot.service`) or `docker-compose.yml` for 24/7 background running
- Final test suite execution (`pytest`)

#### Phase 6 Checklist:
- [x] Full test suite passes (`pytest tests/`)
- [x] `README.md` created with step-by-step setup guide
- [x] Bot startup script tested and verified in local environment
- [x] User review and operational acceptance completed

---

## 4. Collective Final Completion Checklist

This checklist confirms that the entire project is 100% complete and ready for deployment:

- [x] **Phase 1 Complete**: Virtual environment, dependencies, `.env.example`, `.gitignore`, `config.py` ready and tested.
- [x] **Phase 2 Complete**: `downloader.py` single video download, MP3 conversion, ID3 tagging, thumbnail embedding, and auto-cleanup tested.
- [x] **Phase 3 Complete**: Playlist parsing, stream generator, and failed-video skipping tested.
- [x] **Phase 4 Complete**: `bot.py` Telegram handlers, progress indicators, native audio player upload, and playlist loop tested.
- [x] **Phase 5 Complete**: 50MB limit guard, access control, and orphaned file cleanup verified.
- [x] **Phase 6 Complete**: All automated tests pass, `README.md` written, deployment template ready.
- [x] **User Acceptance**: User has added `TELEGRAM_BOT_TOKEN`, started the bot, and successfully received music files in Telegram.
