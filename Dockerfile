FROM python:3.12-slim-bookworm AS build
ARG TARGETARCH
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.19
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
ARG LONGBRIDGE_VERSION=v0.28.7
RUN case "$TARGETARCH" in \
      arm64) checksum=e67468d6abf34b5b0c4b82921b2ac0ebf43d459f27c179320d217f725aec46b0 ;; \
      amd64) checksum=2e2a8bb2add75c77248d0311c83e02f5d5a06cbeacd7162887d25b7d96fc1e97 ;; \
      *) exit 1 ;; \
    esac && \
    curl --fail --location --retry 3 "https://assets.lbkrs.com/github/release/longbridge-terminal/${LONGBRIDGE_VERSION}/longbridge-terminal-linux-musl-${TARGETARCH}.tar.gz" -o /tmp/longbridge.tgz && \
    echo "$checksum  /tmp/longbridge.tgz" | sha256sum -c - && \
    tar -xzf /tmp/longbridge.tgz -C /usr/local/bin longbridge
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS frontend-build
ARG VITE_BASE_PATH=/
COPY web /build/web
COPY scripts/build_web.py /build/build_web.py
RUN python /build/build_web.py --base "${VITE_BASE_PATH}" /build/web /build/dist

FROM python:3.12-slim-bookworm
RUN groupadd -g 10001 agent && useradd -u 10001 -g agent -m agent && \
    mkdir -p /data /home/agent/.longbridge && \
    ln -s /tmp /home/agent/.local && \
    ln -s /tmp /home/agent/.cache && \
    chown -R agent:agent /data /home/agent
COPY --from=build /app/.venv /app/.venv
COPY --from=build /usr/local/bin/longbridge /usr/local/bin/longbridge
COPY --from=frontend-build /build/dist /app/web
COPY skills /app/skills
COPY seed /app/seed
COPY licenses /app/licenses
COPY src /app/src
COPY scripts/migrate_sqlite_to_mysql.py /app/scripts/migrate_sqlite_to_mysql.py
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" APP_ROOT=/app DATA_DIR=/data HOME=/home/agent \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    CREWAI_TELEMETRY_DISABLED=true OTEL_SDK_DISABLED=true CREWAI_TRACING_ENABLED=false \
    XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/config XDG_DATA_HOME=/tmp/share \
    CREWAI_STORAGE_DIR=/data/crewai COOKIE_SECURE=true
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; from crew_stock.config import BASE_PATH; urllib.request.urlopen('http://127.0.0.1:8000' + BASE_PATH + 'api/health', timeout=4)"
CMD ["uvicorn", "crew_stock.app:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
