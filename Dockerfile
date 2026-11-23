# Pinned by digest: python:3.12-slim is a moving tag, and the same line builds a different image on a different day.
# To refresh it:  docker buildx imagetools inspect python:3.12-slim --format '{{.Manifest.Digest}}'
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
LABEL authors="Jeremi"

# The image lags behind Debian: on 2026-11-16 even its newest build still had zlib1g with two HIGH already fixed in
# trixie-security (CVE-2026-76844, CVE-2026-85091). What Debian has fixed comes in at build time.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /usr/src/app

# Runtime dependencies only, from the lock and with hash verification: nothing is re-resolved at build time.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Only the application code. config.env and certs/ live outside src/, and .dockerignore keeps them out of the
# build context as a second barrier.
COPY src/ ./
# And what it shares with the response engine (ADR-0023), importable as `shared`.
COPY shared/ ./shared/

# The app runs unprivileged and can only write the per-event keys under certs/.
RUN useradd --system --uid 10001 --no-create-home app \
 && mkdir certs \
 && chown app certs
USER app

EXPOSE 5002
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5002/health', timeout=3)"]

# The number of workers comes from WEB_CONCURRENCY (one by default). No access log: it writes the query string of
# every request, and the application already logs each one without it.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5002", "--no-access-log"]
