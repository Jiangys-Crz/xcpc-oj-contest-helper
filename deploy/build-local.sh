#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform="${VP_BUILD_PLATFORM:-linux/amd64}"
output="$root/outputs/cloud-release"
mkdir -p "$output/npm-cache"
# Compile platform-independent Next.js JS/CSS on the native developer machine.
# Server dependencies and Node are installed/packaged for Linux locally below.
NEXT_TELEMETRY_DISABLED=1 npm --prefix "$root/web" run build
runtime_container="$(docker create --platform "$platform" node:24-bookworm-slim)"
trap 'docker rm -f "$runtime_container" >/dev/null 2>&1 || true' EXIT
docker cp "$runtime_container:/usr/local/bin/node" "$output/node24-runtime"
docker rm "$runtime_container" >/dev/null
trap - EXIT
docker run --rm --platform "$platform" -v "$root/web:/source:ro" -v "$root/deploy:/deploy:ro" -v "$output:/output" -v "$output/npm-cache:/root/.npm" node:24-bookworm-slim bash -euc '
  mkdir -p /build /payload/release /payload/runtime/pm2 /payload/source
  cp /source/package.json /source/package-lock.json /build/
  cd /build
  npm ci --omit=dev --prefer-offline --no-audit --no-fund
  cp -a /source/.next/standalone/. /payload/release/
  rm -rf /payload/release/node_modules
  cp -a node_modules /payload/release/node_modules
  cp package.json package-lock.json /payload/release/
  mkdir -p /payload/release/.next
  cp -a /source/.next/static /payload/release/.next/
  if [ -d /source/public ]; then cp -a /source/public /payload/release/; fi
  cp /output/node24-runtime /payload/runtime/node
  npm install --prefix /payload/runtime/pm2 pm2@7.1.1 --omit=dev --prefer-offline --no-audit --no-fund
  cp -a /source/app /source/lib /source/components /source/package.json /source/package-lock.json /source/tsconfig.json /source/next.config.ts /payload/source/
  cp -a /deploy/. /payload/deploy/
  /payload/runtime/node --version > /payload/runtime/NODE_VERSION
  tar -czf /output/xcpc-vp-cloud-linux-x64.tar.gz -C /payload .
  sha256sum /output/xcpc-vp-cloud-linux-x64.tar.gz > /output/xcpc-vp-cloud-linux-x64.tar.gz.sha256
'
printf 'Built locally: %s/xcpc-vp-cloud-linux-x64.tar.gz\n' "$output"
