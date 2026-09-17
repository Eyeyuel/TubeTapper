# YouTube to Telegram Music Downloader Bot 🎵🤖

An asynchronous Telegram bot that converts YouTube video and playlist links into high-quality MP3 audio files with embedded metadata and album cover art, delivered straight to Telegram's native audio player.

---

## 🚀 Features

- **Single Tracks & Shorts**: Paste any `youtu.be` or `youtube.com/watch?v=...` link to download the audio track.
- **Whole Playlists**: Paste a `youtube.com/playlist?list=...` link to download the full playlist sequentially.
- **Sequential Streaming**: Tracks in a playlist are delivered one-by-one as soon as each is downloaded—no waiting for the whole playlist to finish before listening.
- **High-Quality Audio & Tags**: Converts streams to MP3 (192kbps or 320kbps) with embedded Title, Artist, and Cover Artwork.
- **Telegram 50MB Guard**: Detects files larger than Telegram's 50MB upload limit and alerts the user instead of crashing.
- **Error Resilience**: Private, deleted, or region-blocked videos in a playlist are skipped gracefully without aborting the rest of the playlist.
- **Disk Auto-Pruning**: Automatically cleans up temporary audio files and purges stale downloads older than 30 minutes.
- **Access Control (Whitelist)**: Optional restriction to authorized Telegram user IDs only.

---

## 📋 Prerequisites

1. **Python 3.12+**
2. **FFmpeg 6.1+** (for audio extraction and ID3 tagging)
3. **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))

---

## 🛠️ Step-by-Step Setup

### 1. Obtain Your Telegram Bot Token
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a name (e.g. `My Music Downloader`) and a unique username ending in `bot` (e.g. `my_yt_music_dl_bot`).
4. Copy the API Token provided (format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).

### 2. Configure Environment Variables
Open or edit the `.env` file in the project folder:
```bash
# In /home/eyuel/Desktop/testAntigravity/.env
TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"
```

Optional settings in `.env`:
- `ALLOWED_USERS`: Comma-separated list of Telegram numerical user IDs (get yours from [@userinfobot](https://t.me/userinfobot)) if you want to keep the bot private. Leave empty for public access.
- `AUDIO_BITRATE`: `192` (default) or `320` kbps.
- `MAX_FILE_SIZE_MB`: `50` (Telegram standard limit).

### 3. Activate the Virtual Environment
```bash
source .venv/bin/activate
```

---

## ▶️ Running the Bot

### Option A: Run Locally in Terminal
```bash
source .venv/bin/activate
python3 bot.py
```
Press `Ctrl+C` to stop.

### Option B: Run 24/7 as a Background Systemd Service (Linux)
A pre-configured service template is available in `deploy/youtube-bot.service`:
```bash
# Copy unit file to systemd directory
sudo cp deploy/youtube-bot.service /etc/systemd/system/

# Reload systemd daemon
sudo systemctl daemon-reload

# Enable auto-start on boot and start now
sudo systemctl enable --now youtube-bot

# Check status
sudo systemctl status youtube-bot

# View live logs
journalctl -u youtube-bot -f
```

---

## 🧪 Running the Automated Test Suite

The project includes unit and integration tests across all modules:
```bash
source .venv/bin/activate
pytest tests/ -v
```

---

## ❓ Troubleshooting & FAQs

### 1. "Telegram upload failed / File exceeds 50MB"
- Telegram's standard Bot API strictly enforces a 50MB per-file upload limit.
- If a video is a 2-hour podcast or DJ mix, the resulting audio file may exceed 50MB. The bot catches this, notifies you in chat, and deletes the temporary file to protect disk space.

### 2. "Video unavailable / Private video"
- The bot gracefully skips private, age-restricted, or country-blocked tracks in playlists without stopping the remaining tracks.

### 3. FFmpeg not found
- Ensure `ffmpeg` is installed and available in `$PATH`:
  ```bash
  sudo apt-get install -y ffmpeg
  ```

---

## 📁 Project Architecture

```
testAntigravity/
├── bot.py                  # Main Telegram application and handlers
├── downloader.py           # yt-dlp & FFmpeg extraction engine
├── config.py               # Configuration validation & environment loader
├── helpers/
│   ├── logger.py           # Colorized structured logging
│   ├── progress.py         # Telegram chat status message manager
│   └── cleanup.py          # Ephemeral disk file auto-pruner
├── tests/
│   ├── test_config.py      # Configuration tests
│   ├── test_downloader.py  # Audio extraction & metadata tests
│   ├── test_playlist.py    # Playlist detection & streaming tests
│   ├── test_bot.py         # Telegram handler tests
│   └── test_safeguards.py  # Whitelist, 50MB guard & cleanup tests
├── deploy/
│   └── youtube-bot.service # Systemd service unit file
├── docker-compose.yml      # Multi-container orchestration
├── monitoring/             # Prometheus & Grafana stack
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment template
└── .env                    # Active credentials
```

---

## 📈 Scaling for Production

For handling up to 100,000+ users, use Docker Compose profiles to unlock horizontal scaling, webhooks, and the local Bot API server.

1. **Local Telegram Bot API Server** (Removes 50MB limit -> 2GB):
   ```bash
   docker compose --profile local-api up -d
   ```
2. **High-Performance Nginx Webhook Mode**:
   ```bash
   docker compose --profile production up -d
   ```
3. **Distributed Worker Cluster** (Scale background downloaders via Redis ARQ):
   ```bash
   docker compose up -d --scale worker=10
   ```
4. **Monitoring & Observability**:
   ```bash
   docker compose --profile monitoring up -d
   ```
   *Access Grafana on port `3000` and Prometheus on port `9090`.*

### Rotating Proxies
To prevent IP bans from YouTube when running at scale, populate the proxy environment variables in `.env` or use `YOUTUBE_PROXY_POOL` to point to a text file containing one proxy URL per line.

---

## ⚙️ Configuration Reference

### Core Settings
- `TELEGRAM_BOT_TOKEN`: Your Telegram Bot API Token.
- `ALLOWED_USERS`: Comma-separated list of numerical user IDs allowed to use the bot.
- `DOWNLOAD_DIR`: Where ephemeral files are stored (default: `./downloads`).

### Audio Settings
- `AUDIO_BITRATE`: MP3 bitrate (default: `192`).
- `DEFAULT_AUDIO_FORMAT`: `mp3` or `m4a`.
- `MAX_FILE_SIZE_MB`: Max file size for upload (default: `50`).
- `MAX_PLAYLIST_TRACKS`: Playlist track limit (default: `25`).

### Cache & Distributed
- `REDIS_URL`: Redis connection URL for cache and ARQ queue.
- `CACHE_TTL_DAYS`: Cache expiry in days (default: `30`).
- `MAX_CONCURRENT_DOWNLOADS`: Thread/worker limit (default: `5`).
- `WORKER_MODE`: Set to `true` to enable distributed ARQ workers.
- `TELEGRAM_LOCAL_MODE` & `TELEGRAM_API_SERVER_URL`: Enables 2GB upload limit.
- `WEBHOOK_MODE`, `WEBHOOK_URL`, `WEBHOOK_PORT`, `WEBHOOK_SECRET`: Webhook config.

### Proxy & Protection
- `YOUTUBE_COOKIES_FILE`: Path to exported YouTube cookies.
- `YOUTUBE_PROXY`: Single proxy URL.
- `YOUTUBE_PROXY_POOL`: Path to a `.txt` file containing rotating proxies.

### Rate Limiting & Logs
- `USER_RATE_LIMIT`: Max requests per window.
- `USER_RATE_WINDOW`: Sliding window seconds.
- `LOG_LEVEL`: `INFO`, `DEBUG`, etc.
- `LOG_FORMAT`: `colored` or `json`.

