#!/usr/bin/env bash
# Hearth installer — clone, run this, open the URL it prints. That's the product.
#
#   bash install.sh                    Hearth only (connect your own InfluxDB)
#   bash install.sh --with-influxdb    include the bundled InfluxDB
set -euo pipefail
cd "$(dirname "$0")"

EMBER='\033[38;5;214m'; OK='\033[0;32m'; DIM='\033[2m'; NC='\033[0m'

PROFILE_ARGS=()
if [[ "${1:-}" == "--with-influxdb" ]]; then
  PROFILE_ARGS=(--profile influxdb)
fi

command -v docker >/dev/null || { echo "Docker is required: curl -fsSL https://get.docker.com | sh"; exit 1; }

# ── .env: create with a generated secret if missing ─────────────────────────
if [[ ! -f .env ]]; then
  cp .env.example .env
  SECRET="$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 40)"
  sed -i.bak "s|^HEARTH_SECRET=.*|HEARTH_SECRET=${SECRET}|" .env && rm -f .env.bak
  if [[ "${1:-}" == "--with-influxdb" ]]; then
    TOKEN="$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 40)"
    PASS="$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 20)"
    sed -i.bak -e "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=${TOKEN}|" \
               -e "s|^INFLUX_PASSWORD=.*|INFLUX_PASSWORD=${PASS}|" .env && rm -f .env.bak
  else
    sed -i.bak -e "s|^INFLUX_TOKEN=|#INFLUX_TOKEN=|" \
               -e "s|^INFLUX_PASSWORD=|#INFLUX_PASSWORD=|" \
               -e "s|^HEARTH_INFLUX_URL=|#HEARTH_INFLUX_URL=|" .env && rm -f .env.bak
  fi
  echo -e "${OK}✓${NC} .env created (HEARTH_SECRET generated)"
fi

# ── build + start ────────────────────────────────────────────────────────────
echo -e "${DIM}Building and starting the stack (first build takes a few minutes)…${NC}"
docker compose "${PROFILE_ARGS[@]}" up -d --build

# ── wait for health ──────────────────────────────────────────────────────────
printf "Waiting for Hearth to come up"
HEALTH=""
for _ in $(seq 1 60); do
  HEALTH="$(curl -fsS http://localhost:8420/api/health 2>/dev/null || true)"
  [[ -n "$HEALTH" ]] && break
  printf "."
  sleep 2
done
echo ""
if [[ -z "$HEALTH" ]]; then
  echo "Hearth did not come up in time — check: docker compose logs hearth"
  exit 1
fi

# ── banner ───────────────────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$IP" ]] && IP="$(hostname -i 2>/dev/null | awk '{print $1}')"
[[ -z "$IP" ]] && IP="localhost"
URL="http://${IP}:8420"

if echo "$HEALTH" | grep -q '"needs_setup":true'; then
  ACTION="to set up your Hearth instance"
else
  ACTION="to open your Hearth dashboard"
fi

echo ""
echo -e "${EMBER}  ◠◠◠${NC}"
echo -e "${EMBER}  hearth${NC}"
echo ""
echo -e "  ${OK}Install is complete.${NC}"
echo ""
echo -e "  Go to:  ${EMBER}${URL}${NC}  ${ACTION}"
echo ""
echo -e "  ${DIM}logs:    docker compose logs -f hearth${NC}"
echo -e "  ${DIM}update:  git pull && docker compose up -d --build${NC}"
echo ""
