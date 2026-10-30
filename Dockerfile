# Pinned by digest: python:3.12-slim is a moving tag, and the same line builds a different image on a different day.
# To refresh it:  docker buildx imagetools inspect python:3.12-slim --format '{{.Manifest.Digest}}'
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
LABEL authors="Jeremi"

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
