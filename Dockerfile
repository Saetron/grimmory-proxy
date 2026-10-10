FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build arguments and environment variables for versioning
ARG APP_VERSION="0.3"
ENV APP_VERSION=${APP_VERSION}

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy version file and application source code
COPY VERSION* ./
COPY app/ ./app/

# Create persistent data and thumbnails directories
RUN mkdir -p /app/data /app/data/thumbnails

# Environment defaults
ENV GRIMMORY_URL=http://localhost:8080 \
    SYNC_USERNAME="" \
    SYNC_PASSWORD="" \
    SYNC_INTERVAL_MINUTES=30 \
    SYNC_ON_STARTUP=true \
    SYNC_CONCURRENCY=6 \
    DATABASE_PATH=/app/data/bridge.db \
    THUMBNAILS_DIR=/app/data/thumbnails \
    PORT=8080 \
    HOST=0.0.0.0 \
    CACHE_TTL=300 \
    LOG_LEVEL=info \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/actuator/info || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
