#!/usr/bin/env bash
# Hearth host-side updater — installed as a cron job by install.sh.
# Every minute: report how far behind origin/main we are, and if the UI
# requested an update (flag file), pull + rebuild + swap the container.
set -uo pipefail
cd "$(dirname "$0")/.."
SHARED=".hearth-shared"
mkdir -p "$SHARED"
LOG="$SHARED/updater.log"
LOCK="$SHARED/deploy.lock"

# ── 1. update requested from the UI? ────────────────────────────────────────
# flock: never rebuild while install.sh (or a previous cron run) is mid-deploy
# — concurrent `compose up` calls collide on the container name.
if [[ -f "$SHARED/update_requested" ]]; then
  rm -f "$SHARED/update_requested"
  {
    flock -w 600 9 || { echo "[$(date -Is)] another deploy holds the lock — skipped"; exit 0; }
    echo "[$(date -Is)] update requested — pulling"
    git pull --ff-only
    export GIT_SHA="$(git rev-parse --short HEAD)"
    docker compose up -d --build --remove-orphans
    echo "[$(date -Is)] updated to $GIT_SHA"
  } 9>"$LOCK" >>"$LOG" 2>&1
fi

# ── 2. report status for the UI ─────────────────────────────────────────────
git fetch -q origin 2>>"$LOG" || true
LOCAL="$(git rev-parse --short HEAD 2>/dev/null)"
REMOTE="$(git rev-parse --short origin/main 2>/dev/null)"
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
SUBJECT="$(git log -1 --format=%s origin/main 2>/dev/null | head -c 100)"
# the DEPLOYED commit (current HEAD = what the running container was built from):
# its message is what the buddy announces as "what's new" after an update.
HEAD_SUBJECT="$(git log -1 --format=%s HEAD 2>/dev/null | head -c 200)"
HEAD_BODY="$(git log -1 --format=%b HEAD 2>/dev/null | head -c 600)"
# JSON-escape arbitrary text safely (commit messages contain quotes/newlines).
jesc() { printf '%s' "${1:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""'; }
cat > "$SHARED/update_status.json" <<JSON
{"local": "${LOCAL}", "remote": "${REMOTE}", "behind": ${BEHIND:-0},
 "latest_subject": $(jesc "$SUBJECT"),
 "deployed_sha": "${LOCAL}",
 "deployed_subject": $(jesc "$HEAD_SUBJECT"),
 "deployed_body": $(jesc "$HEAD_BODY"),
 "checked_at": "$(date -Is)"}
JSON
