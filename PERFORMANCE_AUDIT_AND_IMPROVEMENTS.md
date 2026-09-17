# TubeTapper — Full Project Audit & Improvement Plan

> **Audited:** September 17, 2026
> **Scope:** End-to-end — Telegram message ingestion → backend processing → audio output, Docker infrastructure, Redis caching, worker scaling, proxy management, security, observability.
> **Target:** 1,000,000+ concurrent users

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture Evaluation](#current-architecture-evaluation)
3. [What's Working Well ✅](#whats-working-well-)
4. [Critical Issues Found 🔴](#critical-issues-found-)
5. [High-Priority Improvements 🟡](#high-priority-improvements-)
6. [Medium-Priority Improvements 🟢](#medium-priority-improvements-)
7. [Detailed Implementation Plan](#detailed-implementation-plan)
8. [Verification Plan](#verification-plan)

---

## Executive Summary

TubeTapper has a solid foundation with well-structured, readable code and a thoughtful distributed architecture. The caching strategy (Redis + SQLite dual-write), pipelined playlist streaming, proxy rotation, and local Bot API server integration are all intelligently designed.

However, there are **15 critical and high-priority issues** that would prevent the system from reaching 100k+ users under real load, let alone 1,000,000+. The most severe are:

| Category | Issue | Impact |
|----------|-------|--------|
| **Architecture** | `arq` pool created per-request in `bot.py` (line 334) | Connection storm → Redis OOM at 1k+ concurrent users |
| **Architecture** | No cache stampede protection on hot download path | 500 users requesting same viral song = 500 parallel downloads |
| **Docker** | `telegram-bot-api` container crash-looping (no `depends_on` health gate) | Bot API server never stabilizes |
| **Security** | Bot token committed in `.env` tracked by git history | Token leaked permanently in repo |
| **Concurrency** | Worker `max_jobs=5` default is bottleneck for scaled workers | Each of 10 worker replicas limited to 5 jobs = 50 total |
| **Resilience** | No retry logic on Telegram API calls (429 rate limits) | Uploads silently fail under load |
| **Observability** | Zero metrics/monitoring | Blind to failures in production |

---

## Current Architecture Evaluation

```
                              1,000,000 Users
                                    │
                                    ▼
               ┌─────────── What Exists ───────────┐
               │  Nginx (config only, not in       │
               │  docker-compose — NOT DEPLOYED)    │
               └────────────────┬──────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │  Single Bot Instance   │◄── Long-Polling Mode
                    │  (bot.py, 1 process)   │    (WEBHOOK_MODE=false)
                    └───────────┬───────────┘
                                │
               ┌────────────────┼────────────────┐
               ▼                                  ▼
    ┌──────────────────┐              ┌──────────────────┐
    │  In-Process Queue │              │  ARQ Worker (1)  │
    │  (Semaphore=5)    │              │  (max_jobs=5)    │
    └──────────────────┘              └──────────────────┘
               │                                  │
               └──────────┬───────────────────────┘
                          ▼
              ┌──────────────────┐
              │  Redis 7 Alpine  │
              │  (single node)   │
              └──────────────────┘
              ┌──────────────────┐
              │ telegram-bot-api │ ◄── CRASH-LOOPING
              │ (no API creds)   │     (Restarting)
              └──────────────────┘
```

### Key Observations

1. **Bot runs in long-polling mode** — The `.env` has no `WEBHOOK_MODE`, `WORKER_MODE`, or `TELEGRAM_API_SERVER_URL` configured. Despite all the scaling code existing, none of it is activated.
2. **Single bot process** — Only 1 bot container, no horizontal scaling.
3. **telegram-bot-api is crash-looping** — Missing `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` environment variables.
4. **Nginx is not in docker-compose** — The config file exists at `nginx/nginx.conf` but is never deployed.
5. **Worker is running but idle** — `WORKER_MODE` isn't set in `.env`, so the bot never dispatches jobs to the ARQ worker.

---

## What's Working Well ✅

| Area | Details |
|------|---------|
| **Code Quality** | Clean, well-documented Python with type hints, docstrings, and consistent patterns |
| **Cache Architecture** | Dual Redis+SQLite with automatic failover is production-grade |
| **Stampede Locking** | `acquire_lock` with `SET NX EX` exists (but is not called from the download path) |
| **Pipelined Streaming** | `asyncio.Queue(maxsize=1)` producer-consumer for playlists is clever |
| **Proxy Rotation** | Round-robin with cooldowns and health recovery |
| **Title Cleaning** | Comprehensive regex patterns for YouTube metadata junk |
| **Album Art** | iTunes + Deezer parallel lookup with 4-tier fallback is excellent |
| **User Settings** | Per-user audio format preferences persisted in both Redis and SQLite |
| **Test Coverage** | 67 tests covering all modules with mocking |
| **Error Handling** | Graceful degradation everywhere with safe_delete, update_status fallbacks |
| **Onboarding UX** | Native Telegram description, commands, menu button registration |
| **Concurrent Updates** | `ApplicationBuilder.concurrent_updates(True)` enables parallel update handling |
| **Docker Healthchecks** | Redis has proper healthcheck with `service_healthy` dependency |

---

## Critical Issues Found 🔴

### 1. ARQ Redis Pool Created Per Request (Memory Leak + Connection Storm)

**File:** `bot.py` lines 328-354

Every single track download creates a brand new `arq.create_pool()` connection. Under load, this will:
- Exhaust Redis connection limits (~10,000 default)
- Leak async connections (no `await pool.close()`)
- Cause `ConnectionRefusedError` cascades

```python
# CURRENT (line 334) — called PER REQUEST
redis_pool = await create_pool(
    RedisSettings.from_dsn(config.redis_url or "redis://localhost:6379/0")
)
```

**Fix:** Create one shared pool at startup, store in `application.bot_data`.

---

### 2. Cache Stampede Protection Exists But Is Never Used

**File:** `helpers/cache.py` lines 217-265 — `acquire_lock()` is implemented
**File:** `bot.py` lines 292-437 — `handle_single_track()` never calls it

If 1,000 users all request the same trending song simultaneously, all 1,000 will download it from YouTube in parallel. Only the first download should run; the rest should wait and get the cached `file_id`.

---

### 3. Telegram Bot API Container Crash-Looping

**Observed:** `docker compose ps` shows `Restarting (1) 40 seconds ago`

The `telegram-bot-api` service requires `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` but `.env` doesn't set them. The container exits immediately and restarts infinitely, wasting CPU and generating noise.

**Fix:** Either conditionally include it via Docker profiles, or add a health gate so the bot doesn't try to use it when it's down.

---

### 4. Bot Token Exposed in `.env` (Committed to Git History)

**File:** `.env` line 6 — Contains real token `8854136034:AAGSJ-...`

While `.env` is in `.gitignore`, it was likely committed at some point. The token is visible in the file right now and could be in git history. This is a security incident.

---

### 5. No Retry / Backoff on Telegram API Calls

**Files:** `bot.py` line 406, `worker.py` line 115

Telegram returns HTTP 429 (Too Many Requests) with `retry_after` headers under load. The bot has zero retry logic — uploads just fail silently.

At 100k+ users, you'll hit Telegram's rate limits constantly, especially during playlist uploads.

---

### 6. Synchronous `subprocess.run` Calls for FFmpeg (Thread Pool Starvation)

**Files:** `helpers/artwork.py` lines 45-51, `downloader.py` lines 302-307

`subprocess.run()` blocks the calling thread. Under `asyncio.to_thread()` these land in the default thread pool (typically 40 threads in `cpython`). At scale, 40 concurrent FFmpeg processes + 40 concurrent yt-dlp processes = **80 CPU-intensive threads** exhausting the thread pool and blocking lightweight I/O operations.

---

### 7. SQLite Under Concurrent Write Contention

**File:** `helpers/cache.py` lines 194-215

SQLite's default journal mode (`DELETE`) allows only one writer at a time. Under 1,000+ concurrent writes from `asyncio.to_thread`:
- `SQLITE_BUSY` errors
- 5-second default lock timeout → cascading failures

---

## High-Priority Improvements 🟡

### 8. Nginx Not Deployed in Docker Compose

The `nginx/nginx.conf` exists but is not a service in `docker-compose.yml`. Webhooks mode is useless without a reverse proxy terminating TLS and load-balancing.

### 9. No Rate Limiting on User Requests

Any single user can send 100 messages/second and flood the download queue. There's no per-user cooldown or anti-spam mechanism.

### 10. Artwork External API Calls Block Downloads

**File:** `helpers/artwork.py` lines 183-204

`resolve_album_art()` makes **synchronous HTTP calls** to iTunes + Deezer APIs inside `concurrent.futures.ThreadPoolExecutor`. This runs inside `asyncio.to_thread(download_audio)`, which is already consuming a thread. The 1.5s timeout means every download adds 1.5s of latency even when the APIs are slow.

### 11. `httpx` Missing from `requirements.txt`

**File:** `requirements.txt`

`helpers/artwork.py` imports `httpx` but it's not in `requirements.txt`. The Docker build succeeds only because it's an implicit dependency of another package.

### 12. Download Directory Shared Volume Contention

All bot and worker containers write to the same `downloads_data` Docker volume. File ID collisions are possible when two workers download the same video simultaneously.

### 13. No Graceful Shutdown / Signal Handling

**File:** `bot.py` lines 874-904

No `SIGTERM`/`SIGINT` handling. Docker sends `SIGTERM` on `docker compose stop`, but the bot may be mid-upload. Active downloads and uploads should drain cleanly.

### 14. `.env.example` Missing Scaling Variables

**File:** `.env.example`

The scaling variables (`TELEGRAM_API_SERVER_URL`, `TELEGRAM_LOCAL_MODE`, `WEBHOOK_MODE`, `WEBHOOK_URL`, `WORKER_MODE`, etc.) exist in `config.py` but are **absent from `.env.example`**. Developers won't know they exist.

---

## Medium-Priority Improvements 🟢

### 15. Connection Pool Configuration for Redis

**File:** `helpers/cache.py` lines 88-93

`aioredis.from_url()` uses a default connection pool size of 10. At 1,000+ concurrent cache operations, this creates a bottleneck. Should configure `max_connections=200`.

### 16. No Health Endpoint in Bot Container

**File:** `Dockerfile` lines 34-35

The healthcheck just runs `python3 -c "import sys; sys.exit(0)"` — this verifies Python is installed, not that the bot is running. Should check bot connectivity.

### 17. Worker Job Timeout Too Low

**File:** `worker.py` line 187

`job_timeout = 600` (10 minutes) is tight for large playlists or slow YouTube servers behind proxies. Long downloads will be killed.

### 18. No Dead Letter Queue for Failed Jobs

When ARQ jobs fail, they vanish. Failed downloads should be retried or logged for investigation.

### 19. Cache Eviction Strategy Missing

Redis has no `maxmemory` policy configured. When Redis fills up, it will either OOM-crash or reject writes depending on default configuration.

### 20. No Prometheus/Grafana/StatsD Metrics

Zero observability. At 100k+ users, you need to know:
- Cache hit ratio, queue depth, download latency p50/p95/p99
- Worker utilization, FFmpeg duration, Telegram upload errors
- Redis memory, connection count

### 21. `concurrent_fragment_downloads=4` Per Download

**File:** `downloader.py` line 360

At 50 concurrent downloads × 4 fragments each = 200 simultaneous HTTP connections to YouTube. This accelerates individual downloads but compounds bandwidth and rate-limit pressure at scale.

### 22. No User-Facing Queue Position Updates

The queue manager notifies once on entry, but never updates the position as users ahead complete.

### 23. Shared Downloads Directory Not Namespaced

Downloads use `%(id)s.%(ext)s` — if the bot and 5 workers all process the same video, they'll write to the same file simultaneously.

### 24. No Structured Logging (JSON)

**File:** `helpers/logger.py`

Colorized console logging is great for development but unusable for log aggregation (ELK, CloudWatch, Datadog). Production needs JSON-formatted structured logging.

### 25. `mutagen` Not Used for ID3 Tagging

FFmpeg-based ID3 embedding (`embed_metadata_and_artwork_to_mp3`) creates a full file copy. `mutagen` can write tags in-place in <1ms.

---

## Detailed Implementation Plan

### Phase 1: Critical Fixes (Day 1-2)

#### 1.1 Fix ARQ Connection Pool Leak

**File:** `bot.py`

```diff
# In on_startup(), create a shared pool:
+from arq import create_pool
+from arq.connections import RedisSettings

 async def on_startup(application: Application) -> None:
     await cache_manager.initialize()
+    # Create shared ARQ connection pool for worker dispatch
+    if config.worker_mode and cache_manager.is_redis_active:
+        try:
+            application.bot_data["arq_pool"] = await create_pool(
+                RedisSettings.from_dsn(config.redis_url or "redis://localhost:6379/0")
+            )
+            logger.info("Shared ARQ worker pool initialized.")
+        except Exception as exc:
+            logger.warning(f"Could not create ARQ pool: {exc}")

# In handle_single_track(), use the shared pool:
-    redis_pool = await create_pool(...)
+    redis_pool = context.application.bot_data.get("arq_pool")
+    if not redis_pool:
+        logger.warning("ARQ pool not available. Falling back to in-process.")
```

---

#### 1.2 Wire Cache Stampede Protection Into Download Path

**File:** `bot.py`

```diff
 async def handle_single_track(...):
     video_id = downloader.extract_video_id(url)
+    # Use distributed lock to prevent stampede on same video
+    if video_id:
+        async with cache_manager.acquire_lock(video_id) as acquired:
+            # Re-check cache after acquiring lock (another worker may have just cached it)
+            if acquired:
+                cached = await cache_manager.get(video_id)
+                if cached and cached.get("file_id"):
+                    # Deliver from cache — skip download entirely
+                    ...
+                    return
+            # Proceed with download
+            ...
```

---

#### 1.3 Add Telegram API Retry with Exponential Backoff

**New file:** `helpers/telegram_retry.py`

```python
import asyncio
from functools import wraps
from telegram.error import RetryAfter, TimedOut, NetworkError

def telegram_retry(max_retries=3, base_delay=1.0):
    """Decorator for Telegram API calls with automatic retry on 429/network errors."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except RetryAfter as e:
                    wait = e.retry_after + 0.5
                    if attempt < max_retries:
                        await asyncio.sleep(wait)
                except (TimedOut, NetworkError) as e:
                    if attempt < max_retries:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                    else:
                        raise
            return await func(*args, **kwargs)
        return wrapper
    return decorator
```

---

#### 1.4 Fix SQLite WAL Mode for Concurrent Access

**File:** `helpers/cache.py`

```diff
 def _init_sqlite(self) -> None:
     try:
         with sqlite3.connect(self.sqlite_path) as conn:
+            conn.execute("PRAGMA journal_mode=WAL")
+            conn.execute("PRAGMA busy_timeout=5000")
             conn.executescript("""...""")
```

WAL mode allows concurrent readers + one writer without blocking. `busy_timeout=5000` retries for 5s instead of failing immediately.

---

#### 1.5 Secure Bot Token

```bash
# Revoke the compromised token via @BotFather
# Generate a new token
# Ensure .env is NEVER committed:
git rm --cached .env 2>/dev/null
echo ".env" >> .gitignore
```

---

### Phase 2: Infrastructure Hardening (Day 3-4)

#### 2.1 Add Nginx to Docker Compose

**File:** `docker-compose.yml`

```yaml
  nginx:
    image: nginx:alpine
    container_name: tubetapper_nginx
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - bot
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

#### 2.2 Conditional telegram-bot-api Service

Make the local bot API server only start when explicitly requested:

```yaml
  telegram-bot-api:
    image: aiogram/telegram-bot-api:latest
    profiles:
      - local-api  # Only starts with: docker compose --profile local-api up
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
```

This prevents the crash-loop when credentials aren't configured.

---

#### 2.3 Per-User Rate Limiting

**New file:** `helpers/rate_limiter.py`

```python
import time
from collections import defaultdict

class UserRateLimiter:
    """Token bucket rate limiter per Telegram user."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        bucket = self._buckets[user_id]
        self._buckets[user_id] = [t for t in bucket if now - t < self.window]
        if len(self._buckets[user_id]) >= self.max_requests:
            return False
        self._buckets[user_id].append(now)
        return True

    def time_until_allowed(self, user_id: int) -> float:
        if not self._buckets[user_id]:
            return 0
        oldest = min(self._buckets[user_id])
        return max(0, self.window - (time.time() - oldest))
```

---

#### 2.4 Redis Connection Pool Sizing

**File:** `helpers/cache.py`

```diff
 client = aioredis.from_url(
     self.redis_url,
     decode_responses=True,
     socket_timeout=2.0,
     socket_connect_timeout=2.0,
+    max_connections=200,
+    retry_on_timeout=True,
 )
```

---

#### 2.5 Redis Memory Policy

**File:** `docker-compose.yml`

```diff
  redis:
    image: redis:7-alpine
-   command: redis-server --save 60 1 --loglevel warning
+   command: >
+     redis-server
+     --save 60 1
+     --loglevel warning
+     --maxmemory 512mb
+     --maxmemory-policy allkeys-lru
+     --tcp-backlog 511
+     --timeout 300
```

---

### Phase 3: Performance Optimization (Day 5-6)

#### 3.1 Add `httpx` to Requirements

**File:** `requirements.txt`

```diff
+httpx>=0.27.0
```

---

#### 3.2 Namespace Download Directories per Worker

**File:** `downloader.py`

```diff
 def download_audio(self, url, output_dir=None, ...):
     target_dir = Path(output_dir).resolve() if output_dir else self.download_dir
+    # Namespace by PID to avoid inter-worker file collisions
+    import os
+    target_dir = target_dir / f"worker_{os.getpid()}"
     target_dir.mkdir(parents=True, exist_ok=True)
```

---

#### 3.3 Use `asyncio.create_subprocess_exec` for FFmpeg

Replace synchronous `subprocess.run` with async subprocess for artwork processing:

```python
async def crop_to_square_jpeg_async(source_input: str, output_path: Path) -> Optional[str]:
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
    return None
```

---

#### 3.4 Reduce `concurrent_fragment_downloads` at Scale

**File:** `downloader.py`

```diff
-"concurrent_fragment_downloads": 4,
+"concurrent_fragment_downloads": 2,  # Reduced to prevent YouTube rate-limiting at scale
```

---

#### 3.5 Proper Docker Healthcheck for Bot

**File:** `Dockerfile`

```diff
-HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
-    CMD python3 -c "import sys; sys.exit(0)" || exit 1
+HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
+    CMD python3 -c "import redis; r = redis.from_url('redis://redis:6379/0'); r.ping()" || exit 1
```

---

### Phase 4: Observability & Monitoring (Day 7-8)

#### 4.1 Add Structured JSON Logging

**File:** `helpers/logger.py`

```python
import json

class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production log aggregation."""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)
```

Switch based on environment: `LOG_FORMAT=json` for production, colored for development.

---

#### 4.2 Add Prometheus Metrics Endpoint

**New file:** `helpers/metrics.py`

Track key counters:
- `tubetapper_downloads_total{status="success|error|cached"}`
- `tubetapper_download_duration_seconds` (histogram)
- `tubetapper_cache_hits_total` / `tubetapper_cache_misses_total`
- `tubetapper_queue_depth` (gauge)
- `tubetapper_active_downloads` (gauge)
- `tubetapper_telegram_api_errors_total{type="429|timeout|network"}`

Expose via `/metrics` HTTP endpoint on port 9090.

---

#### 4.3 Add Docker Compose Monitoring Stack (Optional)

```yaml
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    profiles:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    profiles:
      - monitoring
```

---

### Phase 5: Complete `.env.example` & Documentation (Day 8)

#### 5.1 Update `.env.example` with All Variables

```env
# === SCALING & DISTRIBUTED ARCHITECTURE ===

# Local Telegram Bot API Server (2GB uploads, LAN transfer)
# Get from: https://my.telegram.org
TELEGRAM_API_ID=""
TELEGRAM_API_HASH=""
TELEGRAM_API_SERVER_URL=""
TELEGRAM_LOCAL_MODE=false

# Webhook mode for high-throughput ingestion (requires HTTPS domain)
WEBHOOK_MODE=false
WEBHOOK_URL=""
WEBHOOK_PORT=8000
WEBHOOK_SECRET=""

# Distributed worker mode (offloads downloads to ARQ workers via Redis)
WORKER_MODE=false

# Rotating proxy pool for YouTube anti-ban protection
# Single gateway: YOUTUBE_PROXY="http://user:pass@proxy:8080"
# Pool: YOUTUBE_PROXY_POOL="http://p1:8080,http://p2:8080"
# File: YOUTUBE_PROXY_POOL="/path/to/proxies.txt"
YOUTUBE_PROXY=""
YOUTUBE_PROXY_POOL=""

# Cache pre-warming storage channel (Telegram chat/channel ID)
STORAGE_CHAT_ID=""

# Concurrent downloads per node (bounds CPU/RAM/FFmpeg processes)
MAX_CONCURRENT_DOWNLOADS=5

# Per-user rate limit (max requests per minute)
USER_RATE_LIMIT=5
USER_RATE_WINDOW=60
```

---

## Priority Matrix

| # | Improvement | Phase | Effort | Impact at 100k+ | Impact at 1M+ |
|---|-------------|-------|--------|------------------|----------------|
| 1 | Fix ARQ pool leak | 1 | 1h | 🔴 Critical | 🔴 Critical |
| 2 | Wire stampede protection | 1 | 2h | 🔴 Critical | 🔴 Critical |
| 3 | Telegram retry/backoff | 1 | 2h | 🔴 Critical | 🔴 Critical |
| 4 | SQLite WAL mode | 1 | 15m | 🔴 Critical | 🟡 High |
| 5 | Secure bot token | 1 | 10m | 🔴 Critical | 🔴 Critical |
| 6 | Add Nginx to compose | 2 | 1h | 🟡 High | 🔴 Critical |
| 7 | Conditional bot-api service | 2 | 30m | 🟡 High | 🟡 High |
| 8 | User rate limiting | 2 | 2h | 🟡 High | 🔴 Critical |
| 9 | Redis pool sizing | 2 | 15m | 🟡 High | 🔴 Critical |
| 10 | Redis maxmemory policy | 2 | 10m | 🟡 High | 🔴 Critical |
| 11 | Add `httpx` to requirements | 3 | 5m | 🟡 High | 🟡 High |
| 12 | Namespace worker downloads | 3 | 30m | 🟡 High | 🔴 Critical |
| 13 | Async FFmpeg subprocess | 3 | 2h | 🟢 Medium | 🟡 High |
| 14 | Reduce fragment downloads | 3 | 5m | 🟢 Medium | 🟡 High |
| 15 | Real Docker healthcheck | 3 | 15m | 🟢 Medium | 🟡 High |
| 16 | JSON structured logging | 4 | 1h | 🟢 Medium | 🟡 High |
| 17 | Prometheus metrics | 4 | 3h | 🟢 Medium | 🔴 Critical |
| 18 | Update .env.example | 5 | 30m | 🟢 Medium | 🟢 Medium |
| 19 | Dead letter queue for ARQ | 4 | 1h | 🟢 Medium | 🟡 High |
| 20 | Graceful shutdown handling | 2 | 1h | 🟢 Medium | 🟡 High |

---

## Verification Plan

### Automated Tests
```bash
# Run full test suite after each phase
.venv/bin/pytest tests/ -v

# Run with coverage
.venv/bin/pytest tests/ -v --cov=. --cov-report=term-missing

# Validate Docker stack
docker compose config
docker compose up -d --build
docker compose ps  # All services should be "Up (healthy)"
```

### Load Testing
```bash
# Install locust for load testing
pip install locust

# Simulate 1000 concurrent users sending messages
# (Requires custom Telegram API mock)
```

### Manual Verification Checklist
- [ ] `telegram-bot-api` container is NOT crash-looping (or uses Docker profiles)
- [ ] Bot responds to `/start`, `/search`, `/settings`, `/status`, `/help`
- [ ] Cache hit delivers audio in <0.5s
- [ ] Two users requesting same song simultaneously: only one download occurs
- [ ] Queue position notification appears when all slots are occupied
- [ ] Rate limit kicks in after 5 requests in 60 seconds per user
- [ ] Redis `INFO memory` shows `maxmemory-policy: allkeys-lru`
- [ ] `docker compose logs bot` shows structured JSON (in production mode)
- [ ] Worker scales: `docker compose up -d --scale worker=5` starts 5 healthy workers

---

## Summary

The TubeTapper codebase has excellent foundations — the architecture vision is sound, the code is well-written, and the test coverage is strong. The critical gap is between **code that exists** and **code that's activated and battle-tested under load**. The 5 phases above close that gap systematically, transforming TubeTapper from a well-designed prototype into a production system capable of serving 1,000,000+ users.

**Estimated Total Effort:** 3-4 full days of engineering work across all 5 phases.
