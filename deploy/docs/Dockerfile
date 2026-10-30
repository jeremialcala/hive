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

FROM nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86
COPY deploy/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=page /build/csp.conf /etc/nginx/csp.conf
COPY --from=page /build/out/ /usr/share/nginx/html/docs/
COPY assets/logos/hive-favicon.svg /usr/share/nginx/html/docs/favicon.svg
