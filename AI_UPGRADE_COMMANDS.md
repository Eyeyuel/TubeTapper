# TubeTapper — AI Upgrade Commands (Phase-by-Phase)

> **Purpose:** Copy-paste ONE phase command at a time to any AI coding assistant.
> Each command is self-contained with rules, exact tasks, file references, test commands, and a verification checklist.
> **DO NOT run multiple phases at once.** Complete one, verify all checks pass, then proceed to the next.

---

## How to Use This File

1. **Copy** the entire command block for the current phase (everything between the `---` separators)
2. **Paste** it into your AI coding assistant's chat
3. **Wait** for the AI to complete all tasks and run the verification checklist
4. **Confirm** all checklist items are checked ✅
5. **Only then** proceed to the next phase
6. After all 5 phases: run the Final Verification command at the bottom

---

## Pre-Flight: Give This Context First

Copy and paste this block BEFORE any phase command to give the AI context about the project:

```
You are working on TubeTapper — a high-performance YouTube Music to Telegram Downloader Bot.

Tech Stack: Python 3.12, python-telegram-bot v21+, yt-dlp, FFmpeg, Redis, Docker, arq (async Redis queue).

Project root: /home/eyuel/Desktop/testAntigravity

MANDATORY RULES:
1. Read the file PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root FIRST. This is the master reference document for all improvements.
2. Do NOT modify any existing functionality that already works. All changes must be additive or surgical fixes.
3. Do NOT remove existing comments or docstrings unless directly related to your code change.
4. After making changes, run the test suite: .venv/bin/pytest tests/ -v
5. All 67 existing tests MUST continue to pass after your changes. If any test breaks, fix it before proceeding.
6. Do NOT commit or push to git unless I explicitly tell you to.
7. Use the existing code style: type hints, docstrings, logging patterns, and import conventions already established in the codebase.
8. If you need to create new files, follow the existing naming conventions (snake_case, descriptive names).
9. Test your changes work by running appropriate commands before marking checklist items as done.
```

---
---
---

## PHASE 1 COMMAND: Critical Fixes (Estimated: 3-5 hours)

```
TASK: Implement Phase 1 (Critical Fixes) from PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md

Read PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root first. You are implementing Phase 1: Critical Fixes (items 1.1 through 1.5).

=== TASK 1.1: Fix ARQ Connection Pool Leak ===

PROBLEM: In bot.py, the function handle_single_track() creates a NEW arq.create_pool() Redis connection on EVERY single download request (line ~334). This leaks connections and will crash Redis under load.

WHAT TO DO:
1. In bot.py, modify the on_startup() function to create ONE shared ARQ connection pool and store it in application.bot_data["arq_pool"]
2. Only create the pool if config.worker_mode is True AND cache_manager.is_redis_active is True
3. Wrap the pool creation in a try/except so it doesn't crash the bot if Redis is temporarily down
4. In handle_single_track(), replace the per-request create_pool() call with: context.application.bot_data.get("arq_pool")
5. If the pool is not available, fall through to the existing in-process fallback (don't crash)
6. Remove the per-request "from arq import create_pool" and "from arq.connections import RedisSettings" imports from inside handle_single_track() — move them to the top of the file or into on_startup()

FILES TO MODIFY: bot.py

=== TASK 1.2: Wire Cache Stampede Protection Into Download Path ===

PROBLEM: helpers/cache.py has an acquire_lock() method that implements distributed locking (SET NX EX), but handle_single_track() in bot.py NEVER calls it. If 500 users request the same viral song, all 500 download it simultaneously from YouTube.

WHAT TO DO:
1. In bot.py handle_single_track(), AFTER the initial cache check (which already exists around line 307-326) and BEFORE the download section, wrap the download logic in: async with cache_manager.acquire_lock(video_id) as acquired:
2. After acquiring the lock, re-check the cache (another concurrent request may have just finished downloading and caching it while we waited for the lock)
3. If the re-check finds a cached file_id, deliver it from cache and return early — skip the download entirely
4. If no cache hit after lock acquisition, proceed with the existing download logic
5. Make sure the lock is only used when video_id is not None
6. The existing acquire_lock() in cache.py handles both Redis (distributed) and local (in-memory asyncio.Lock) cases — don't change it

FILES TO MODIFY: bot.py

=== TASK 1.3: Add Telegram API Retry with Exponential Backoff ===

PROBLEM: All Telegram API calls (send_audio, edit_message_text, send_chat_action, delete_message) have zero retry logic. Telegram returns HTTP 429 (RetryAfter) under load, and the calls just fail silently.

WHAT TO DO:
1. Create a new file: helpers/telegram_retry.py
2. Implement a reusable async function (not just a decorator) called send_with_retry(coro_func, *args, max_retries=3, base_delay=1.0, **kwargs) that:
   - Catches telegram.error.RetryAfter: waits e.retry_after + 0.5 seconds, then retries
   - Catches telegram.error.TimedOut and telegram.error.NetworkError: waits base_delay * (2 ** attempt) seconds, then retries
   - After max_retries exhausted, raises the last exception
   - Logs each retry attempt at WARNING level using helpers.logger.setup_logger
3. In bot.py, wrap the critical send_audio() calls in handle_single_track() and handle_playlist() with this retry function
4. In worker.py, wrap the send_audio() calls in process_download_job() with this retry function
5. Don't wrap every single Telegram call — focus on send_audio (the most important one that does the actual upload) and edit_message_text

FILES TO CREATE: helpers/telegram_retry.py
FILES TO MODIFY: bot.py, worker.py

=== TASK 1.4: Fix SQLite WAL Mode for Concurrent Access ===

PROBLEM: SQLite uses DELETE journal mode by default, which allows only one writer at a time. Under concurrent access from asyncio.to_thread(), this causes SQLITE_BUSY errors.

WHAT TO DO:
1. In helpers/cache.py, in the _init_sqlite() method, add these two PRAGMA statements BEFORE the CREATE TABLE executescript:
   conn.execute("PRAGMA journal_mode=WAL")
   conn.execute("PRAGMA busy_timeout=5000")
2. WAL mode allows concurrent readers + one writer without blocking
3. busy_timeout=5000 makes SQLite retry for 5 seconds on lock contention instead of failing immediately

FILES TO MODIFY: helpers/cache.py

=== TASK 1.5: Security — Verify Bot Token is Not in Git ===

WHAT TO DO:
1. Check if .env is in .gitignore (it should already be there — verify it)
2. Run: git log --all --oneline -- .env  to check if .env was ever committed to git history
3. If it was committed, run: git rm --cached .env (if it's currently tracked)
4. Report the findings but do NOT revoke the token — that's for the human to do manually
5. Verify .env is listed in .dockerignore as well

FILES TO CHECK: .gitignore, .dockerignore, .env

=== VERIFICATION CHECKLIST (Phase 1) ===

After completing all tasks above, verify EACH of these items and report the results:

- [ ] Run: .venv/bin/pytest tests/ -v — All 67+ tests must pass
- [ ] Run: python -c "from helpers.telegram_retry import send_with_retry; print('OK')" — Import must succeed
- [ ] Read bot.py on_startup() — Confirm ARQ pool is created once and stored in bot_data
- [ ] Read bot.py handle_single_track() — Confirm NO more per-request create_pool() calls exist
- [ ] Read bot.py handle_single_track() — Confirm cache_manager.acquire_lock() is used before download
- [ ] Read helpers/cache.py _init_sqlite() — Confirm PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000 are present
- [ ] Read bot.py — Confirm send_audio calls use retry wrapper
- [ ] Read worker.py — Confirm send_audio calls use retry wrapper
- [ ] Run: grep -r "create_pool" bot.py — Should NOT appear inside handle_single_track function body
- [ ] Run: grep "acquire_lock" bot.py — Should appear at least once in handle_single_track
- [ ] Run: git status -- .env — Should show .env is untracked (not staged)
- [ ] Run: docker compose config — Should exit with code 0 (valid config)

STOP HERE. Do not proceed to Phase 2. Report all checklist results and wait for my next command.
```

---
---
---

## PHASE 2 COMMAND: Infrastructure Hardening (Estimated: 3-4 hours)

```
TASK: Implement Phase 2 (Infrastructure Hardening) from PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md

Read PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root first. You are implementing Phase 2: Infrastructure Hardening (items 2.1 through 2.5).

IMPORTANT: Phase 1 must already be completed. Verify by running: .venv/bin/pytest tests/ -v (all tests must pass before you start).

=== TASK 2.1: Add Nginx Service to Docker Compose ===

PROBLEM: nginx/nginx.conf exists but Nginx is not a service in docker-compose.yml. Without it, webhook mode has no reverse proxy.

WHAT TO DO:
1. Add an nginx service to docker-compose.yml using image nginx:alpine
2. Container name: tubetapper_nginx
3. Map ports 80:80 and 443:443
4. Mount ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro (read-only)
5. Mount ./nginx/ssl:/etc/nginx/ssl:ro (for future SSL certs)
6. Use Docker profiles so nginx only starts when explicitly requested: profiles: ["production"]
7. Set depends_on: bot
8. Add restart: unless-stopped
9. Add json-file logging with max-size: "10m" and max-file: "3"
10. Create the empty directory nginx/ssl/ so Docker doesn't error

FILES TO MODIFY: docker-compose.yml
DIRECTORIES TO CREATE: nginx/ssl/

=== TASK 2.2: Fix telegram-bot-api Crash-Loop ===

PROBLEM: The telegram-bot-api container requires TELEGRAM_API_ID and TELEGRAM_API_HASH env vars. Without them (which is the default), it crash-loops infinitely with "Restarting (1) every 40 seconds", wasting CPU.

WHAT TO DO:
1. In docker-compose.yml, add Docker profiles to the telegram-bot-api service: profiles: ["local-api"]
2. This means it will ONLY start when the user explicitly runs: docker compose --profile local-api up -d
3. Add a proper healthcheck to the telegram-bot-api service:
   test: ["CMD-SHELL", "curl -sf http://localhost:8081/ || exit 1"]
   interval: 10s
   timeout: 5s
   retries: 10
   start_period: 30s
4. Make the bot service NOT depend on telegram-bot-api (it should work fine without it — it already has graceful fallback in config.py)
5. Verify the bot service's depends_on only lists redis, NOT telegram-bot-api

FILES TO MODIFY: docker-compose.yml

=== TASK 2.3: Implement Per-User Rate Limiting ===

PROBLEM: Any user can send 100 messages/second and flood the download queue. No anti-spam protection.

WHAT TO DO:
1. Create a new file: helpers/rate_limiter.py
2. Implement a UserRateLimiter class with:
   - __init__(self, max_requests: int = 5, window_seconds: int = 60)
   - is_allowed(self, user_id: int) -> bool — Returns True if user hasn't exceeded rate limit
   - time_until_allowed(self, user_id: int) -> float — Returns seconds until user can make next request
   - Uses a sliding window approach: store timestamps of recent requests per user, prune expired ones
3. Create a global singleton instance: rate_limiter = UserRateLimiter()
4. In bot.py, modify the restricted() decorator to check rate_limiter.is_allowed(user.id) AFTER the whitelist check
5. If rate limited, reply with a friendly message: "⏳ *Rate limit reached.* Please wait {seconds:.0f} seconds before your next request."
6. Don't rate-limit the /start and /help commands — only rate-limit /search, /settings, handle_message (which triggers downloads/searches)
7. Add config support: read USER_RATE_LIMIT and USER_RATE_WINDOW from environment variables in config.py

FILES TO CREATE: helpers/rate_limiter.py
FILES TO MODIFY: bot.py, config.py

=== TASK 2.4: Redis Connection Pool Sizing ===

PROBLEM: Redis connection pool defaults to 10 connections. Under 1,000+ concurrent cache operations this becomes a bottleneck.

WHAT TO DO:
1. In helpers/cache.py, in the initialize() method, add max_connections=200 and retry_on_timeout=True to the aioredis.from_url() call
2. These go as keyword arguments alongside the existing socket_timeout and socket_connect_timeout

FILES TO MODIFY: helpers/cache.py

=== TASK 2.5: Redis Memory Eviction Policy ===

PROBLEM: Redis has no maxmemory configured. When it fills up, it will either OOM or reject writes.

WHAT TO DO:
1. In docker-compose.yml, update the redis service command to include:
   --maxmemory 512mb
   --maxmemory-policy allkeys-lru
   --tcp-backlog 511
   --timeout 300
2. Use YAML multi-line format (>) for readability
3. Keep the existing --save 60 1 and --loglevel warning flags

FILES TO MODIFY: docker-compose.yml

=== VERIFICATION CHECKLIST (Phase 2) ===

After completing all tasks above, verify EACH of these items and report the results:

- [ ] Run: .venv/bin/pytest tests/ -v — All 67+ tests must pass (existing + any new tests you added)
- [ ] Run: docker compose config — Must exit with code 0
- [ ] Run: docker compose config | grep -A2 "profiles" — nginx and telegram-bot-api should show profiles
- [ ] Run: docker compose up -d --build — Should start redis, bot, and worker WITHOUT nginx or telegram-bot-api
- [ ] Run: docker compose ps — telegram-bot-api should NOT appear (it's behind a profile now)
- [ ] Run: docker compose ps — nginx should NOT appear (it's behind a profile now)
- [ ] Run: docker compose ps — redis, bot, worker should be Up/Healthy
- [ ] Read helpers/rate_limiter.py — Confirm UserRateLimiter class exists with is_allowed() and time_until_allowed()
- [ ] Read bot.py — Confirm rate limiting is applied to download/search handlers but NOT to /start and /help
- [ ] Read helpers/cache.py initialize() — Confirm max_connections=200 and retry_on_timeout=True are set
- [ ] Read docker-compose.yml redis command — Confirm maxmemory 512mb and allkeys-lru are present
- [ ] Run: python -c "from helpers.rate_limiter import rate_limiter; print('Rate limiter OK')" — Must succeed
- [ ] Verify nginx/ssl/ directory exists (even if empty)
- [ ] Run: docker compose --profile local-api config | grep telegram-bot-api — Should appear with profile

STOP HERE. Do not proceed to Phase 3. Report all checklist results and wait for my next command.
```

---
---
---

## PHASE 3 COMMAND: Performance Optimization (Estimated: 2-3 hours)

```
TASK: Implement Phase 3 (Performance Optimization) from PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md

Read PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root first. You are implementing Phase 3: Performance Optimization (items 3.1 through 3.5).

IMPORTANT: Phases 1 and 2 must already be completed. Verify by running: .venv/bin/pytest tests/ -v (all tests must pass before you start).

=== TASK 3.1: Add httpx to requirements.txt ===

PROBLEM: helpers/artwork.py imports httpx but it's not in requirements.txt. It only works because another package pulls it in as a transitive dependency, which could break at any time.

WHAT TO DO:
1. Add httpx>=0.27.0 to requirements.txt
2. Keep the existing entries and their version constraints
3. Sort alphabetically if the existing list is sorted, otherwise add at the end

FILES TO MODIFY: requirements.txt

=== TASK 3.2: Namespace Download Directories Per Worker Process ===

PROBLEM: All bot and worker containers share the same downloads_data volume and write to the same filenames (%(id)s.%(ext)s). Two workers downloading the same video simultaneously will corrupt each other's files.

WHAT TO DO:
1. In downloader.py, in the download_audio() method, after resolving target_dir, append a process-specific subdirectory:
   import os
   target_dir = target_dir / f"worker_{os.getpid()}"
   target_dir.mkdir(parents=True, exist_ok=True)
2. This ensures each process (bot or worker) writes to its own isolated subdirectory
3. Make sure the cleanup_files() method still works (it uses absolute paths, so it should be fine)
4. Update the cleanup_orphaned_downloads() in helpers/cleanup.py to also scan subdirectories of the download dir (iterate recursively or scan one level of child dirs)

FILES TO MODIFY: downloader.py, helpers/cleanup.py

=== TASK 3.3: Convert FFmpeg Artwork Calls to Async Subprocess ===

PROBLEM: subprocess.run() in helpers/artwork.py blocks threads. At scale, this starves the asyncio thread pool.

WHAT TO DO:
1. In helpers/artwork.py, create a new async function: crop_to_square_jpeg_async(source_input: str, output_path: Path) -> Optional[str]
2. Use asyncio.create_subprocess_exec() instead of subprocess.run() for the FFmpeg call
3. The existing synchronous crop_to_square_jpeg() must remain unchanged (it's called from synchronous contexts inside download_audio which runs in asyncio.to_thread)
4. The async version is for future use when we refactor the download pipeline — for now, just create it alongside the sync version
5. Add proper error handling and logging matching the existing patterns

FILES TO MODIFY: helpers/artwork.py

=== TASK 3.4: Reduce concurrent_fragment_downloads ===

PROBLEM: concurrent_fragment_downloads=4 in downloader.py means at 50 concurrent downloads, there are 200 simultaneous HTTP connections to YouTube, increasing ban risk.

WHAT TO DO:
1. In downloader.py, in the download_audio() method, change concurrent_fragment_downloads from 4 to 2
2. Add a comment explaining why: # Reduced from 4 to prevent YouTube rate-limiting under high concurrency

FILES TO MODIFY: downloader.py

=== TASK 3.5: Proper Docker Healthcheck for Bot Container ===

PROBLEM: The Dockerfile healthcheck just runs python3 -c "import sys; sys.exit(0)" which only verifies Python is installed, not that the bot is actually running and connected to Redis.

WHAT TO DO:
1. In the Dockerfile, replace the existing HEALTHCHECK with one that actually checks Redis connectivity:
   HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
       CMD python3 -c "import redis; r = redis.from_url('redis://redis:6379/0'); r.ping()" || exit 1
2. This verifies both Python AND Redis connectivity

FILES TO MODIFY: Dockerfile

=== VERIFICATION CHECKLIST (Phase 3) ===

After completing all tasks above, verify EACH of these items and report the results:

- [ ] Run: .venv/bin/pytest tests/ -v — All 67+ tests must pass
- [ ] Run: grep "httpx" requirements.txt — Should show httpx>=0.27.0
- [ ] Run: grep "concurrent_fragment_downloads" downloader.py — Should show value of 2, not 4
- [ ] Run: grep "worker_" downloader.py — Should show worker_{os.getpid()} namespace logic
- [ ] Run: grep "crop_to_square_jpeg_async" helpers/artwork.py — Should find the new async function
- [ ] Run: grep "HEALTHCHECK" Dockerfile — Should show redis.from_url health check, NOT "import sys"
- [ ] Run: docker compose config — Must exit with code 0
- [ ] Run: docker compose up -d --build — Should build and start successfully
- [ ] Run: docker compose ps — All default services should be Up/Healthy
- [ ] Run: python -c "from helpers.artwork import crop_to_square_jpeg_async; print('OK')" — Must succeed
- [ ] Verify helpers/cleanup.py handles nested worker subdirectories

STOP HERE. Do not proceed to Phase 4. Report all checklist results and wait for my next command.
```

---
---
---

## PHASE 4 COMMAND: Observability & Monitoring (Estimated: 3-4 hours)

```
TASK: Implement Phase 4 (Observability & Monitoring) from PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md

Read PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root first. You are implementing Phase 4: Observability & Monitoring (items 4.1 through 4.3).

IMPORTANT: Phases 1-3 must already be completed. Verify by running: .venv/bin/pytest tests/ -v (all tests must pass before you start).

=== TASK 4.1: Add Structured JSON Logging for Production ===

PROBLEM: The current logger uses colorized console output (helpers/logger.py). This is great for development but unusable for production log aggregation (ELK, CloudWatch, Datadog, etc.).

WHAT TO DO:
1. In helpers/logger.py, add a new JSONFormatter class that outputs logs as JSON:
   {
     "timestamp": "2026-09-17T21:00:00",
     "level": "INFO",
     "logger": "bot",
     "message": "Successfully downloaded track..."
   }
2. If the record has exc_info, include an "exception" key with the formatted traceback
3. Add a new environment variable: LOG_FORMAT (values: "json" or "colored", default: "colored")
4. In config.py, add log_format: str = "colored" to AppConfig and read LOG_FORMAT from env
5. In setup_logger(), select JSONFormatter when config says "json", otherwise use existing ColoredFormatter
6. Keep the existing ColoredFormatter unchanged — it's the default for development
7. Add log_format to the .env.example file with a comment

FILES TO MODIFY: helpers/logger.py, config.py
FILES TO UPDATE: .env.example

=== TASK 4.2: Add Application Metrics Tracking ===

PROBLEM: Zero metrics or observability. At 100k+ users, we need to track cache hits, download durations, error rates, queue depth, etc.

WHAT TO DO:
1. Create a new file: helpers/metrics.py
2. Implement a simple MetricsCollector class (no external dependencies required — use Python stdlib):
   - counters: dict of string -> int (e.g., "downloads_total", "downloads_cached", "downloads_failed", "cache_hits", "cache_misses", "telegram_api_errors", "rate_limit_hits")
   - gauges: dict of string -> float (e.g., "active_downloads", "queue_depth")
   - increment(counter_name: str, value: int = 1)
   - set_gauge(gauge_name: str, value: float)
   - get_all() -> dict — Returns all metrics as a dictionary
   - get_summary() -> str — Returns a human-readable summary string
3. Create a global singleton: metrics = MetricsCollector()
4. Instrument the codebase — add metrics.increment() calls at key points:
   - bot.py handle_single_track(): increment "downloads_total" on start, "downloads_cached" on cache hit, "downloads_failed" on error
   - helpers/cache.py get(): increment "cache_hits" or "cache_misses"
   - helpers/rate_limiter.py: increment "rate_limit_hits" when a user is rate-limited
   - helpers/queue_manager.py: set gauges for "active_downloads" and "queue_depth" on acquire/release
5. Add metrics to the /status command in bot.py — append a "📊 Metrics" section showing key counters
6. DO NOT add prometheus-client or any external dependency — keep it pure Python

FILES TO CREATE: helpers/metrics.py
FILES TO MODIFY: bot.py, helpers/cache.py, helpers/rate_limiter.py (if it exists from Phase 2), helpers/queue_manager.py

=== TASK 4.3: Add ARQ Dead Letter Tracking for Failed Jobs ===

PROBLEM: When ARQ worker jobs fail, they silently disappear. We need to track failures.

WHAT TO DO:
1. In worker.py, add an on_job_error callback to WorkerSettings that logs failed jobs
2. The callback should:
   - Log the job function name, arguments, and exception at ERROR level
   - Increment metrics counter "worker_jobs_failed"
3. Increase job_timeout from 600 to 900 (15 minutes) to handle slow proxied downloads
4. Add retry_jobs=True and max_tries=2 to WorkerSettings so failed downloads get one automatic retry

FILES TO MODIFY: worker.py

=== TASK 4.4: Add Docker Compose Monitoring Stack (Optional Profile) ===

WHAT TO DO:
1. Create a monitoring directory: monitoring/
2. Create monitoring/prometheus.yml with a basic scrape config targeting the bot on port 9090
3. In docker-compose.yml, add prometheus and grafana services under profiles: ["monitoring"]
4. Prometheus: image prom/prometheus:latest, port 9090:9090, mount monitoring/prometheus.yml
5. Grafana: image grafana/grafana:latest, port 3000:3000
6. Both services should only start with: docker compose --profile monitoring up -d

FILES TO CREATE: monitoring/prometheus.yml
FILES TO MODIFY: docker-compose.yml
DIRECTORIES TO CREATE: monitoring/

=== VERIFICATION CHECKLIST (Phase 4) ===

After completing all tasks above, verify EACH of these items and report the results:

- [ ] Run: .venv/bin/pytest tests/ -v — All 67+ tests must pass
- [ ] Run: python -c "from helpers.metrics import metrics; metrics.increment('test'); print(metrics.get_all())" — Should work
- [ ] Run: python -c "from helpers.logger import setup_logger; print('OK')" — Must succeed
- [ ] Read helpers/logger.py — Confirm JSONFormatter class exists
- [ ] Read config.py — Confirm log_format field exists in AppConfig
- [ ] Read helpers/metrics.py — Confirm MetricsCollector class with increment(), set_gauge(), get_all(), get_summary()
- [ ] Read bot.py /status command — Confirm metrics section is included in the output
- [ ] Read bot.py handle_single_track — Confirm metrics.increment() calls exist for downloads
- [ ] Read helpers/cache.py get() — Confirm cache_hits / cache_misses counters
- [ ] Read worker.py — Confirm job_timeout=900, max_tries=2
- [ ] Run: docker compose config — Must exit with code 0
- [ ] Verify monitoring/prometheus.yml exists with valid YAML
- [ ] Run: docker compose --profile monitoring config | grep prometheus — Should appear
- [ ] Run: docker compose --profile monitoring config | grep grafana — Should appear

STOP HERE. Do not proceed to Phase 5. Report all checklist results and wait for my next command.
```

---
---
---

## PHASE 5 COMMAND: Documentation & Configuration Completeness (Estimated: 1-2 hours)

```
TASK: Implement Phase 5 (Documentation & Configuration Completeness) from PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md

Read PERFORMANCE_AUDIT_AND_IMPROVEMENTS.md in the project root first. You are implementing Phase 5: Complete .env.example & Documentation.

IMPORTANT: Phases 1-4 must already be completed. Verify by running: .venv/bin/pytest tests/ -v (all tests must pass before you start).

=== TASK 5.1: Update .env.example with ALL Configuration Variables ===

PROBLEM: The .env.example file only has the basic config vars. All the scaling variables (TELEGRAM_API_SERVER_URL, TELEGRAM_LOCAL_MODE, WEBHOOK_MODE, WORKER_MODE, etc.) are defined in config.py but missing from .env.example. Developers don't know they exist.

WHAT TO DO:
1. Open config.py and identify EVERY environment variable that is read via os.getenv()
2. Open .env.example and add ALL missing variables with:
   - A descriptive comment explaining what each one does
   - The default value shown
   - Example values in comments where helpful
3. Group variables by category with section headers:
   - Core Settings (token, users, dirs)
   - Audio Settings (bitrate, format, max size)
   - Cache Settings (Redis, TTL)
   - Scaling & Distributed Architecture (webhook, worker, local API)
   - Proxy Protection (YouTube proxy, pool)
   - Rate Limiting (user rate limit, window)
   - Logging (level, format)
   - Pre-warming (storage chat ID)
4. Add every variable that appears in config.py load_config() function
5. Include the new variables added in Phases 1-4 (LOG_FORMAT, USER_RATE_LIMIT, USER_RATE_WINDOW)

FILES TO MODIFY: .env.example

=== TASK 5.2: Update README.md ===

WHAT TO DO:
1. Update README.md to reflect the current state of the project including all improvements made in Phases 1-4
2. Add a "Scaling for Production" section explaining:
   - How to enable webhook mode
   - How to enable distributed workers (docker compose up -d --scale worker=10)
   - How to enable the local Telegram Bot API server (docker compose --profile local-api up -d)
   - How to enable Nginx (docker compose --profile production up -d)
   - How to enable monitoring (docker compose --profile monitoring up -d)
   - How to configure rotating proxies
3. Add a "Configuration Reference" section that lists all env vars with descriptions
4. Add a "Monitoring & Observability" section
5. Keep any existing README content that's still accurate

FILES TO MODIFY: README.md

=== TASK 5.3: Add Graceful Shutdown Handler ===

PROBLEM: No SIGTERM/SIGINT handling. Docker sends SIGTERM on stop, but mid-upload operations get killed.

WHAT TO DO:
1. In bot.py, the python-telegram-bot library's Application.run_polling() and Application.run_webhook() already handle SIGINT/SIGTERM gracefully by default
2. Add a post_shutdown callback to the Application that:
   - Closes the cache_manager connection (await cache_manager.close())
   - Closes the ARQ pool if it exists (await pool.close())
   - Logs "Bot shutdown complete."
3. Register it via .post_shutdown(on_shutdown) in create_bot_app()

FILES TO MODIFY: bot.py

=== TASK 5.4: Write New Tests for Phase 1-4 Features ===

WHAT TO DO:
1. Create or update test files to cover the new functionality:
   - tests/test_rate_limiter.py: Test is_allowed() returns True for first N requests, False after limit, and resets after window
   - tests/test_metrics.py: Test increment(), set_gauge(), get_all(), get_summary()
   - tests/test_telegram_retry.py: Test that the retry wrapper retries on RetryAfter and gives up after max_retries
2. Each test file should have at least 3 test cases
3. Use pytest-asyncio for async tests
4. Follow the existing test patterns in the tests/ directory (mocking, fixtures, etc.)

FILES TO CREATE: tests/test_rate_limiter.py, tests/test_metrics.py, tests/test_telegram_retry.py

=== VERIFICATION CHECKLIST (Phase 5 — FINAL) ===

After completing all tasks above, verify EACH of these items and report the results:

- [ ] Run: .venv/bin/pytest tests/ -v — ALL tests must pass (should be 75+ tests now with the new test files)
- [ ] Run: docker compose config — Must exit with code 0
- [ ] Run: docker compose up -d --build — Must build and start successfully
- [ ] Run: docker compose ps — redis, bot, worker should be Up/Healthy
- [ ] Read .env.example — Confirm it has EVERY env var from config.py load_config()
- [ ] Read .env.example — Confirm section headers and grouping exist
- [ ] Read README.md — Confirm "Scaling for Production" section exists
- [ ] Read README.md — Confirm "Configuration Reference" section exists
- [ ] Read bot.py — Confirm post_shutdown callback closes cache and ARQ pool
- [ ] Verify tests/test_rate_limiter.py exists with 3+ test cases
- [ ] Verify tests/test_metrics.py exists with 3+ test cases
- [ ] Verify tests/test_telegram_retry.py exists with 3+ test cases
- [ ] Count total tests: .venv/bin/pytest tests/ --co -q | tail -1 — Should be 75+

STOP HERE. Report all checklist results. All 5 phases are complete!
```

---
---
---

## FINAL VERIFICATION COMMAND (Run After All 5 Phases)

```
TASK: Run the Final Full-Stack Verification for TubeTapper after all 5 improvement phases.

Execute each of these commands and report the results:

1. Run full test suite with coverage:
   .venv/bin/pytest tests/ -v --tb=short

2. Validate Docker stack:
   docker compose config

3. Rebuild and start all default services:
   docker compose down
   docker compose up -d --build

4. Check all container statuses:
   docker compose ps

5. Check Redis memory policy:
   docker compose exec redis redis-cli CONFIG GET maxmemory-policy

6. Check Redis connection count:
   docker compose exec redis redis-cli INFO clients | grep connected_clients

7. Verify bot token is NOT in git:
   git log --all --oneline -- .env

8. Count total lines of test code:
   wc -l tests/*.py

9. List all files modified across all phases:
   git diff --name-only HEAD

10. Show git status:
    git status

Report all results. If everything passes, the upgrade is complete and ready for commit.
Ask the human: "All 5 phases are verified. Shall I commit and push with message: 'feat: 5-phase production hardening for 1M+ users — stampede protection, retry logic, rate limiting, observability, infrastructure'?"
```

---

## Quick Reference: Phase Summary

| Phase | Focus | Key Deliverables | Est. Time |
|-------|-------|-----------------|-----------|
| 1 | Critical Fixes | ARQ pool fix, stampede lock, retry logic, WAL mode, token security | 3-5h |
| 2 | Infrastructure | Nginx in compose, bot-api profiles, rate limiter, Redis tuning | 3-4h |
| 3 | Performance | httpx dep, worker namespacing, async FFmpeg, fragment tuning, healthcheck | 2-3h |
| 4 | Observability | JSON logging, metrics collector, dead letter tracking, monitoring stack | 3-4h |
| 5 | Documentation | .env.example, README, graceful shutdown, new tests | 1-2h |
| **Total** | | | **12-18h** |
