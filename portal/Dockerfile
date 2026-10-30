# The image of hive-portal, the BFF of Hive's PWA (HIG-93): ghcr.io/jeremialcala/hive-portal. Built from the root of
# the monorepo, where `shared/` is:
#
#   docker build -f portal/Dockerfile .
#
# What goes into the context is said by portal/Dockerfile.dockerignore, which BuildKit uses instead of the root's.
# The base goes by digest, like the engine's: a tag moves, and the same line would give another image another day.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
LABEL authors="Jeremi"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Only what runs, from the lock and with hashes, and only wheels: nothing is resolved again, nor compiled.
COPY portal/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

WORKDIR /usr/src/app

COPY portal/src/ ./src/
COPY shared/ ./shared/

# Without privileges, with the code read-only (root's). It writes nothing: the sessions are in Bifröst.
RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]

# One worker: the portal's DPoP key lives in its memory, and so would a second one's (ADR-0033). Without uvicorn's
# access log, as the gateway: the line of /auth/callback would carry the code of the login in its query.
CMD ["uvicorn", "--factory", "src.app:create_app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
