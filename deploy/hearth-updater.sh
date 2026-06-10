#!/usr/bin/env bash
# Hearth host-side updater — installed as a cron job by install.sh.
# Every minute: report how far behind origin/main we are, and if the UI
# requested an update (flag file), pull + rebuild + swap the container.
set -uo pipefail
cd "$(dirname "$0")/.."
SHARED=".hearth-shared"
mkdir -p "$SHARED"
LOG="$SHARED/updater.log"

# ── 1. update requested from the UI? ────────────────────────────────────────
if [[ -f "$SHARED/update_requested" ]]; then
  rm -f "$SHARED/update_requested"
  {
    echo "[$(date -Is)] update requested — pulling"
    git pull --ff-only
    export GIT_SHA="$(git rev-parse --short HEAD)"
    docker compose up -d --build
    echo "[$(date -Is)] updated to $GIT_SHA"
  } >>"$LOG" 2>&1
fi

# ── 2. report status for the UI ─────────────────────────────────────────────
git fetch -q origin 2>>"$LOG" || true
LOCAL="$(git rev-parse --short HEAD 2>/dev/null)"
REMOTE="$(git rev-parse --short origin/main 2>/dev/null)"
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
SUBJECT="$(git log -1 --format=%s origin/main 2>/dev/null | head -c 100)"
cat > "$SHARED/update_status.json" <<JSON
{"local": "${LOCAL}", "remote": "${REMOTE}", "behind": ${BEHIND:-0},
 "latest_subject": $(printf '%s' "$SUBJECT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""'),
 "checked_at": "$(date -Is)"}
JSON
