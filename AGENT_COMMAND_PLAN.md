# AI Agent Command Plan & Copy-Paste Execution Prompts

This document contains **ready-to-copy-paste prompts** for each phase of the project.

### How to Use This Document:
1. When starting a phase, copy the entire block inside the **"📋 Copy-Paste Prompt for AI Agent"** section for that phase.
2. Paste it directly into your AI coding assistant chat.
3. The prompt instructs the AI to read [IMPLEMENTATION_PLAN.md](file:///home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md), adhere to strict rules, build that phase's exact files and functions, run the automated tests, update the checklist in `IMPLEMENTATION_PLAN.md`, and pause at the evaluation gate to ask for your review before proceeding.

---

## Global Agent Operating Protocol & Governance

All AI agents executing these prompts must adhere to these 5 rules:
1. **Scope Boundary**: Build **only** the deliverables defined for the current phase. Do not jump ahead or write code for future phases prematurely.
2. **Implementation Plan Compliance**: Always read [IMPLEMENTATION_PLAN.md](file:///home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md) before writing code.
3. **Mandatory Automated Testing**: Execute the specified test commands. If any test fails, diagnose and fix it before finishing your turn. Every test must pass with exit code 0.
4. **Checklist Maintenance**: Update the corresponding `[ ]` checkboxes to `[x]` in [IMPLEMENTATION_PLAN.md](file:///home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md) upon passing all tests.
5. **Human Evaluation Gate**: Never automatically jump to the next phase. Present a summary of completed work, display test results, and ask the user for approval.

---

# Phase 1: Environment Setup, Configuration & Scaffolding

### 📋 Copy-Paste Prompt for AI Agent (Phase 1):
````text
You are an autonomous AI coding assistant. Execute Phase 1 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on the "Prerequisites" and "Phase 1: Environment Setup, Configuration & Scaffolding" sections.

STEP 2: STRICT RULES FOR THIS PHASE
- Build ONLY the deliverables assigned to Phase 1.
- Do NOT write downloader logic, playlist logic, or Telegram bot handlers yet.
- Use Python 3.12 and ensure all virtual environment paths point to /home/eyuel/Desktop/testAntigravity/.venv.
- Write clean, type-hinted, robust code with clear error handling.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Create a Python virtual environment (.venv) if it doesn't already exist.
2. Create `requirements.txt` containing:
   - python-telegram-bot>=21.0
   - yt-dlp>=2024.0.0
   - python-dotenv>=1.0.0
   - pytest>=8.0.0
   - pytest-asyncio>=0.23.0
3. Create `.gitignore` ignoring: `.venv/`, `.env`, `downloads/`, `*.mp3`, `*.jpg`, `*.webp`, `__pycache__/`, `.pytest_cache/`.
4. Create `.env.example` with placeholders and explanatory comments for:
   - TELEGRAM_BOT_TOKEN
   - ALLOWED_USERS (optional comma-separated Telegram user IDs)
   - DOWNLOAD_DIR (default: ./downloads)
   - AUDIO_BITRATE (default: 192)
   - MAX_FILE_SIZE_MB (default: 50)
5. Create `.env` copied from `.env.example` with placeholder values so the app can load in development mode.
6. Create `helpers/__init__.py` and `helpers/logger.py` providing colorized, structured logging with timestamp and level.
7. Create `config.py` that loads `.env`, validates that TELEGRAM_BOT_TOKEN is present (or issues a clear warning in test mode), sets sensible defaults, and ensures the `downloads/` directory exists.
8. Create `tests/__init__.py` and `tests/test_config.py` to test:
   - Config loads default settings properly.
   - Config correctly creates the downloads directory.
   - Config handles missing or valid environment variables.

STEP 4: EXECUTE VERIFICATION TESTS
Activate the virtual environment, install requirements, and run the automated tests:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pytest tests/test_config.py -v
```
Verify that the test suite passes with exit code 0.

STEP 5: UPDATE THE CHECKLIST
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update the checkboxes under "Phase 1 Checklist" from [ ] to [x].

STEP 6: USER EVALUATION GATE
Do NOT start Phase 2. Summarize what you have created, print the pytest output, and ask:
"Phase 1 is complete and all tests pass. Please evaluate my work. May I proceed to Phase 2?"
````

---

# Phase 2: Core Audio Extraction & Metadata Processing

### 📋 Copy-Paste Prompt for AI Agent (Phase 2):
````text
You are an autonomous AI coding assistant. Execute Phase 2 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on "Phase 2: Core Audio Extraction & Metadata Processing".

STEP 2: STRICT RULES FOR THIS PHASE
- Build ONLY Phase 2 deliverables.
- Use `yt-dlp` and `ffmpeg` for audio extraction and post-processing.
- Ensure audio is converted to high-quality MP3 (configurable bitrate from config.py, default 192kbps).
- Ensure thumbnails are extracted and embedded into the MP3 ID3 tags (cover art).
- Ensure temporary downloaded files are strictly cleaned up when requested.
- Do NOT implement Telegram bot handlers or playlist logic yet.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Create `downloader.py` with an `AudioDownloader` class:
   - `__init__(self, config=None)`: Initialize with download directory, audio bitrate, and yt-dlp options.
   - `get_video_info(self, url: str) -> dict`: Extract metadata (title, artist/uploader, duration, thumbnail_url, webpage_url, is_live) quickly without downloading the media stream.
   - `download_audio(self, url: str) -> dict`: Download the audio stream, invoke FFmpegExtractAudio post-processor to convert to MP3, embed thumbnail as cover art, and return a dictionary:
     `{ "file_path": str, "title": str, "artist": str, "duration": int, "thumbnail_path": str, "filesize": int }`
   - `cleanup_files(self, *paths: str) -> None`: Safely delete temporary audio and image files without raising exceptions if any file is already removed.
2. Create `tests/test_downloader.py`:
   - Test `get_video_info` against a short public YouTube audio/video URL or mock object.
   - Test `download_audio` to verify that an actual `.mp3` file is generated, has non-zero size, and metadata is populated.
   - Test `cleanup_files` to verify that all generated files are deleted cleanly.

STEP 4: EXECUTE VERIFICATION TESTS
Run the automated test suite using the virtual environment:
```bash
source .venv/bin/activate
pytest tests/test_downloader.py -v -s
```
Verify that all tests pass with exit code 0.

STEP 5: UPDATE THE CHECKLIST
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update the checkboxes under "Phase 2 Checklist" from [ ] to [x].

STEP 6: USER EVALUATION GATE
Do NOT start Phase 3. Summarize what was built, show test results, confirm audio extraction and cleanup work as expected, and ask:
"Phase 2 is complete and all tests pass. Please evaluate my work. May I proceed to Phase 3?"
````

---

# Phase 3: Playlist Parsing & Stream Processing Engine

### 📋 Copy-Paste Prompt for AI Agent (Phase 3):
````text
You are an autonomous AI coding assistant. Execute Phase 3 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on "Phase 3: Playlist Parsing & Stream Processing Engine".

STEP 2: STRICT RULES FOR THIS PHASE
- Build ONLY Phase 3 deliverables.
- Handle playlists sequentially so tracks can be streamed to the user one-by-one.
- If a track inside a playlist is deleted, private, or region-restricted, the engine MUST catch the exception, log the error, and continue to the next track without failing the entire playlist.
- Do NOT implement Telegram bot handlers yet.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Extend `downloader.py` (or add `helpers/playlist.py`):
   - `is_playlist(self, url: str) -> bool`: Accurately detect if a URL is a playlist (contains `list=` query parameter and is not an unextractable mix/radio).
   - `get_playlist_info(self, url: str) -> dict`: Fast metadata extraction of the playlist:
     `{ "title": str, "total_tracks": int, "tracks": list[dict] }` without downloading media.
   - `stream_playlist_tracks(self, url: str, max_tracks: int = None)`: Asynchronous generator that yields one downloaded track result at a time:
     `yield (track_index, total_tracks, track_data_or_none, error_or_none)`
     so the consumer can immediately upload each track to Telegram as soon as it is downloaded.
   - Robust error shielding: Wrap individual track downloads in try/except blocks so unplayable/copyright-blocked videos yield an error record rather than crashing the loop.
2. Create `tests/test_playlist.py`:
   - Test `is_playlist` with various YouTube URL formats (single video, video with playlist context, pure playlist URL).
   - Test `get_playlist_info` extracting playlist track count and metadata.
   - Test `stream_playlist_tracks` generator ensuring items are yielded sequentially and simulated unavailable videos are caught and skipped gracefully.

STEP 4: EXECUTE VERIFICATION TESTS
Run the playlist test suite using the virtual environment:
```bash
source .venv/bin/activate
pytest tests/test_playlist.py -v -s
```
Verify that all tests pass with exit code 0.

STEP 5: UPDATE THE CHECKLIST
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update the checkboxes under "Phase 3 Checklist" from [ ] to [x].

STEP 6: USER EVALUATION GATE
Do NOT start Phase 4. Summarize the playlist generator logic and error shielding, print test outputs, and ask:
"Phase 3 is complete and all tests pass. Please evaluate my work. May I proceed to Phase 4?"
````

---

# Phase 4: Telegram Bot Interface & Interactive Workflows

### 📋 Copy-Paste Prompt for AI Agent (Phase 4):
````text
You are an autonomous AI coding assistant. Execute Phase 4 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on "Phase 4: Telegram Bot Interface & Interactive Workflows".

STEP 2: STRICT RULES FOR THIS PHASE
- Build ONLY Phase 4 deliverables.
- Use `python-telegram-bot` v21+ (asyncio syntax, `ApplicationBuilder`).
- Always send audio using Telegram's native player `send_audio` (passing `title`, `performer`, `duration`, and `thumbnail`).
- Ensure all temporary files are strictly cleaned up immediately after sending.
- Do not block the event loop; run blocking download calls in `asyncio.to_thread`.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Create `helpers/progress.py`:
   - Helper class or functions to send and edit status messages in Telegram (e.g. "🔍 Fetching info...", "⬇️ Downloading track 3/10: Song Name...", "📤 Uploading audio to Telegram...").
2. Create `bot.py`:
   - Initialize Telegram `ApplicationBuilder` with token from `config.py`.
   - Command Handlers:
     - `/start`: Welcome message with nice formatting, explaining how to send single video or playlist links.
     - `/help`: Detailed instructions, supported links, and limitations.
     - `/status`: Health and uptime message.
   - Message Handler (Regex for YouTube URLs):
     - Catches both single video URLs and playlist URLs.
     - Single Video Flow:
       1. Send "🔍 Analyzing link..."
       2. Download audio asynchronously via `downloader.py`
       3. Update message to "📤 Uploading audio to Telegram..."
       4. Call `context.bot.send_audio(chat_id=..., audio=open(file_path, 'rb'), title=..., performer=..., duration=..., thumbnail=open(thumb_path, 'rb'))`
       5. Delete the temporary files.
       6. Delete or update the status message to "✅ Finished!".
     - Playlist Flow:
       1. Notify user: "📋 Playlist found: N tracks. Starting download..."
       2. Loop over `stream_playlist_tracks`:
          - Update status message with current track progress (e.g. `[3/12] Downloading...`).
          - Send each track with `send_audio`.
          - Clean up each file immediately after sending.
       3. Send completion summary: "🎉 Playlist download finished! Sent X tracks."
3. Create `tests/test_bot.py`:
   - Unit tests verifying handler registration and YouTube regex matching.
   - Mock test simulating single video delivery and playlist iteration.

STEP 4: EXECUTE VERIFICATION TESTS
Run the bot tests using the virtual environment:
```bash
source .venv/bin/activate
pytest tests/test_bot.py -v
```
Verify that all tests pass with exit code 0.

STEP 5: UPDATE THE CHECKLIST
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update the checkboxes under "Phase 4 Checklist" from [ ] to [x].

STEP 6: USER EVALUATION GATE
Do NOT start Phase 5. Summarize the bot handlers, user messaging UX, and test results, then ask:
"Phase 4 is complete and all tests pass. Please evaluate my work. May I proceed to Phase 5?"
````

---

# Phase 5: Concurrency, Limits & Safeguards

### 📋 Copy-Paste Prompt for AI Agent (Phase 5):
````text
You are an autonomous AI coding assistant. Execute Phase 5 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on "Phase 5: Concurrency, Limits & Safeguards".

STEP 2: STRICT RULES FOR THIS PHASE
- Protect the bot from crashing, filling up the disk, or hitting Telegram API upload limits.
- Telegram bots CANNOT upload files larger than 50MB via the standard Bot API. You must catch this before upload and notify the user.
- If `ALLOWED_USERS` is defined in config, block unauthorized users with a friendly notice.
- Ensure disk cleanup handles interrupted jobs or orphaned files.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Implement 50MB File Size Guard:
   - Before attempting `send_audio`, check `file_size <= 50 * 1024 * 1024`.
   - If exceeded, delete the file and inform the user:
     "⚠️ Audio file exceeds Telegram's 50MB limit (File size: X MB). Cannot upload."
2. Implement Access Control Middleware / Decorator:
   - If `ALLOWED_USERS` is configured, reject messages from users whose Telegram ID is not in the list.
3. Implement Orphaned File Auto-Pruning:
   - Create a helper `cleanup_orphaned_downloads(max_age_seconds=1800)` that scans the `downloads/` directory on startup and periodically, removing any files older than 30 minutes.
4. Enhance Error Handling:
   - Catch Telegram `NetworkError`, `TimedOut`, and YouTube `DownloadError` with friendly in-chat error messages instead of unhandled traceback crashes.
5. Create tests in `tests/test_safeguards.py`:
   - Test 50MB size rejection logic.
   - Test whitelist authorization logic.
   - Test orphaned file cleanup logic.

STEP 4: EXECUTE VERIFICATION TESTS
Run the safeguards test suite:
```bash
source .venv/bin/activate
pytest tests/test_safeguards.py -v
```
Verify that all tests pass with exit code 0.

STEP 5: UPDATE THE CHECKLIST
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update the checkboxes under "Phase 5 Checklist" from [ ] to [x].

STEP 6: USER EVALUATION GATE
Do NOT start Phase 6. Summarize the safeguards, rate limits, and test results, then ask:
"Phase 5 is complete and all tests pass. Please evaluate my work. May I proceed to Phase 6?"
````

---

# Phase 6: Final Verification, Production Readiness & Deployment

### 📋 Copy-Paste Prompt for AI Agent (Phase 6):
````text
You are an autonomous AI coding assistant. Execute Phase 6 of the YouTube to Telegram Music Downloader Bot project according to the following strict instructions:

STEP 1: READ THE IMPLEMENTATION PLAN
Read /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md thoroughly, focusing on "Phase 6: Final Verification, Production Readiness & Documentation" and the "Collective Final Completion Checklist".

STEP 2: STRICT RULES FOR THIS PHASE
- Run the entire test suite across all modules.
- Ensure zero syntax errors, type errors, or unhandled exceptions.
- Provide production-ready deployment assets and comprehensive documentation.

STEP 3: WHAT TO BUILD IN THIS PHASE
1. Run full test suite:
   ```bash
   source .venv/bin/activate
   pytest tests/ -v --tb=short
   ```
   Ensure 100% of tests pass.
2. Compile and lint check:
   ```bash
   python3 -m py_compile *.py helpers/*.py
   ```
3. Create `README.md`:
   - Comprehensive documentation with project overview.
   - Step-by-step instructions on obtaining a Telegram Bot Token from @BotFather.
   - Configuration guide (.env variables).
   - How to run locally (`python3 bot.py`).
   - How to run in background using systemd or tmux.
   - Troubleshooting common issues (FFmpeg paths, YouTube IP restrictions, file size limits).
4. Create `deploy/youtube-bot.service`:
   - Systemd unit file template for auto-starting and running the bot as a background service on Linux.

STEP 4: FINAL CHECKLIST VERIFICATION
Open /home/eyuel/Desktop/testAntigravity/IMPLEMENTATION_PLAN.md and update:
- "Phase 6 Checklist" from [ ] to [x].
- All items in "Collective Final Completion Checklist" from [ ] to [x].

STEP 5: FINAL SUMMARY & HANDOFF
Present a complete final summary of the project to the user, including:
1. Confirmation that all tests passed.
2. Clear instructions on how the user can set their `TELEGRAM_BOT_TOKEN` in `.env` and run `python3 bot.py`.
3. How to test it live by sending a YouTube link to their new bot in Telegram.
````
