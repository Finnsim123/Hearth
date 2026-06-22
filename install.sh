#!/usr/bin/env bash
# Hearth installer — clone, run this, open the URL it prints. That's the product.
#
#   bash install.sh          Hearth + a bundled InfluxDB. In the wizard you choose
#                            whether to use the bundled InfluxDB or your own.
#   bash install.sh --reset  FACTORY RESET: wipe accounts, household, bindings,
#                            connections (Hearth's SQLite volume) and start fresh
#                            at the wizard. InfluxDB data is NOT touched.
set -euo pipefail
cd "$(dirname "$0")"
source deploy/lib.sh          # hearth_os, with_lock, hearth_iso, hearth_lan_ip

EMBER='\033[38;5;214m'; OK='\033[0;32m'; DIM='\033[2m'; NC='\033[0m'

# ── self-update registration (per-OS) ───────────────────────────────────────
# Linux: /etc/cron.d drop-in (runs as root). macOS: a launchd LaunchAgent (runs
# as the logged-in user, so it has Docker Desktop's context — a root LaunchDaemon
# wouldn't reach the user's Docker socket). Else: a per-user crontab line.
install_launchd_agent() {
  local script="$1" label="com.hearth.updater"
  local dir="$HOME/Library/LaunchAgents" plist
  plist="$dir/$label.plist"; mkdir -p "$dir"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>${script}</string></array>
  <key>WorkingDirectory</key><string>$(pwd)</string>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$(pwd)/.hearth-shared/updater.launchd.log</string>
  <key>StandardErrorPath</key><string>$(pwd)/.hearth-shared/updater.launchd.log</string>
</dict>
</plist>
PLIST
  launchctl unload "$plist" 2>/dev/null || true
  if launchctl load -w "$plist" 2>/dev/null; then
    echo -e "${OK}✓${NC} in-app updates enabled (launchd agent, every 60s)"
  else
    echo -e "${DIM}note: couldn't load the launchd agent — in-app updates off (use: git pull && docker compose up -d --build).${NC}"
  fi
}

install_updater() {
  mkdir -p .hearth-shared
  local os script; os="$(hearth_os)"; script="$(pwd)/deploy/hearth-updater.sh"
  if [[ "$os" == "linux" && -d /etc/cron.d ]] && command -v crontab >/dev/null; then
    echo "* * * * * root bash $script" > /etc/cron.d/hearth-updater
    chmod 644 /etc/cron.d/hearth-updater
    echo -e "${OK}✓${NC} in-app updates enabled (host updater cron installed)"
  elif [[ "$os" == "macos" ]]; then
    install_launchd_agent "$script"
  elif command -v crontab >/dev/null; then
    ( crontab -l 2>/dev/null | grep -v 'hearth-updater.sh'; echo "* * * * * bash $script" ) \
      | crontab - && echo -e "${OK}✓${NC} in-app updates enabled (user crontab)"
  else
    echo -e "${DIM}note: no cron or launchd found — in-app updates off (use: git pull && docker compose up -d --build).${NC}"
  fi
}

do_deploy() { docker compose up -d --build --remove-orphans; }

if [[ "${1:-}" == "--reset" ]]; then
  echo "Factory reset: removing Hearth's app data (accounts, household,"
  echo "bindings, connections). Time-series data in InfluxDB is kept."
  read -r -p "Type 'reset' to confirm: " CONFIRM
  [[ "$CONFIRM" == "reset" ]] || { echo "aborted"; exit 1; }
  docker compose down
  docker volume rm "$(basename "$PWD")_hearth-data" 2>/dev/null     || docker volume rm hearth_hearth-data 2>/dev/null || true
fi

command -v docker >/dev/null || { echo "Docker is required: curl -fsSL https://get.docker.com | sh"; exit 1; }

# ── .env: create with a generated secret if missing ─────────────────────────
if [[ ! -f .env ]]; then
  cp .env.example .env
  SECRET="$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 40)"
  TOKEN="$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 40)"
  PASS="$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 20)"
  # Always provision the bundled InfluxDB's admin token + password; the wizard
  # decides whether Hearth actually USES it or an external instance.
  sed -i.bak -e "s|^HEARTH_SECRET=.*|HEARTH_SECRET=${SECRET}|" \
             -e "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=${TOKEN}|" \
             -e "s|^INFLUX_PASSWORD=.*|INFLUX_PASSWORD=${PASS}|" .env && rm -f .env.bak
  echo -e "${OK}✓${NC} .env created (secrets generated)"
fi

# ── in-app updates: register the host updater (per-OS) ──────────────────────
install_updater

# ── host IP for the wizard ───────────────────────────────────────────────────
# Detect the host's LAN IP and hand it to the container (via .env → compose), so
# the wizard's step 10 can tell the user which address Home Assistant should use
# to reach Hearth. Refreshed every run (DHCP leases change).
HOST_IP="$(hearth_lan_ip)"
if grep -q '^HEARTH_HOST_IP=' .env 2>/dev/null; then
  sed -i.bak -e "s|^HEARTH_HOST_IP=.*|HEARTH_HOST_IP=${HOST_IP}|" .env && rm -f .env.bak
else
  echo "HEARTH_HOST_IP=${HOST_IP}" >> .env
fi
echo -e "${OK}✓${NC} host address: http://${HOST_IP}:8420 (given to the integration step)"

# ── build + start ────────────────────────────────────────────────────────────
# Same lock the updater uses — a manual install must never race it. with_lock
# uses flock on Linux, an mkdir lock on macOS (no util-linux needed).
echo -e "${DIM}Building and starting the stack (first build takes a few minutes)…${NC}"
export GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
if ! with_lock .hearth-shared/deploy.lock 600 do_deploy; then
  echo "Deploy failed or another deploy is running — check: docker compose logs hearth"
  exit 1
fi

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
# Genuine LAN address across Linux (ip route / hostname -I) and macOS
# (ipconfig getifaddr enN) — see hearth_lan_ip in deploy/lib.sh.
IP="$(hearth_lan_ip)"
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
