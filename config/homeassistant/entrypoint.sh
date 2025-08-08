#!/usr/bin/env bash
set -euo pipefail
RUNTIME_DIR=/runtime/homeassistant
mkdir -p "$RUNTIME_DIR"
# One-way sync (do not delete extras in runtime)
cp -n /config-src/*.yaml "$RUNTIME_DIR" 2>/dev/null || true
# Substitute password token
if [ -n "${DB_PASSWORD:-}" ]; then
  sed -i "s|__DB_PASSWORD__|${DB_PASSWORD//|/\|}|g" "$RUNTIME_DIR/configuration.yaml"
fi
exec /init --config "$RUNTIME_DIR"
