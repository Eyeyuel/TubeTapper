# Multi-stage / optimized production Dockerfile for TubeTapper Bot
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/app/downloads

# Install FFmpeg and required system libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . /app/

# Create ephemeral downloads directory and assign ownership to non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/downloads && \
    chown -R appuser:appuser /app

USER appuser

# Health check to ensure python process is operational
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD python3 -c "import redis; r = redis.from_url('redis://redis:6379/0'); r.ping()" || exit 1

# Start the bot
CMD ["python", "bot.py"]
