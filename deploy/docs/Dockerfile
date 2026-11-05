# The static page of the management API (ADR-0026), served as https://hive.higerotech.com/docs.
# Built from the root of the repository, which is where the contract is:
#
#   docker build -f deploy/docs/Dockerfile -t ghcr.io/jeremialcala/hive-docs:<tag> .
#
# Its own Dockerfile.dockerignore lets in only the contract and this directory.

FROM node:22.23.3-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS page
WORKDIR /build
COPY deploy/docs/package.json deploy/docs/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY docs/02-design/contracts/openapi.yaml deploy/docs/build.mjs ./
RUN node build.mjs

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
# The image lags behind Alpine: on 2026-11-05 it still had brotli, curl and expat with fixes already published
# (CVE-2026-111497, CRITICAL; HIG-157). What Alpine has fixed comes in at build time, and the server goes on as user
# 101, as the image runs it.
USER root
RUN apk upgrade --no-cache
USER 101
COPY deploy/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=page /build/csp.conf /etc/nginx/csp.conf
COPY --from=page /build/out/ /usr/share/nginx/html/docs/
COPY assets/logos/hive-favicon.svg /usr/share/nginx/html/docs/favicon.svg
