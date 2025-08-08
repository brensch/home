#!/usr/bin/env bash
# Disaster Recovery automated provisioning script
# 1. Decrypt secrets (.env and secrets.yaml) using sops (age/GPG)
# 2. Bootstrap directory structure
# 3. Start or recreate docker stack
set -euo pipefail

command -v sops >/dev/null || { echo "sops not installed"; exit 1; }
command -v docker >/dev/null || { echo "docker not installed"; exit 1; }

# Decrypt in-place (user must have age key exported: export SOPS_AGE_KEY_FILE=./age.key )
if [ -f .env ]; then
  if grep -q 'sops:' .env 2>/dev/null; then
    sops -d -i .env
  fi
fi

if [ -f configs/homeassistant/secrets.yaml ]; then
  if grep -q 'sops:' configs/homeassistant/secrets.yaml 2>/dev/null; then
    sops -d -i configs/homeassistant/secrets.yaml
  fi
fi

# Ensure config directories exist (minimal safety)
mkdir -p configs/homeassistant configs/mosquitto configs/mariadb

# Create default mosquitto.conf if missing
if [ ! -f configs/mosquitto/mosquitto.conf ]; then
cat > configs/mosquitto/mosquitto.conf <<'EOF'
persistence true
persistence_location /mosquitto/data/
log_timestamp true
listener 1883 0.0.0.0
allow_anonymous true
listener 9001
protocol websockets
log_type error
log_type warning
log_type notice
log_type information
EOF
fi

# Pull and start
docker compose pull
docker compose up -d

echo "Stack provisioned. Access Home Assistant at http://<host>:8123" 
