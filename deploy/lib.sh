#!/usr/bin/env bash
# Shared host-script helpers — portable across Linux and macOS (incl. bash 3.2,
# which ships on macOS). No bash-4-only syntax here.
#
# Source it:  source "$(dirname "$0")/lib.sh"   (from deploy/)

hearth_os() {
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux)  echo linux ;;
    *)      echo other ;;
  esac
}

# ISO-8601 UTC timestamp. GNU `date -Is` isn't available on BSD/macOS, so build it
# explicitly — works identically everywhere.
hearth_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Run a command while holding an exclusive lock, so install.sh and the updater cron
# never `docker compose up` concurrently (they collide on the container name).
#   with_lock <lockfile> <timeout_seconds> <command> [args...]
# Returns the command's exit code, or 1 if the lock couldn't be taken in time.
# Uses flock when present (Linux); falls back to an atomic mkdir lock with
# stale-steal (macOS, where flock needs `brew install util-linux`).
with_lock() {
  lock="$1"; timeout="$2"; shift 2
  if command -v flock >/dev/null 2>&1; then
    # fd 9, subshell so the lock releases on exit; works on bash 3.2+
    ( flock -w "$timeout" 9 || exit 99; "$@" ) 9>"$lock"
    rc=$?
    [ "$rc" -eq 99 ] && return 1
    return "$rc"
  fi
  # ── portable fallback: atomic mkdir lock ──
  dir="${lock}.d"; waited=0
  while ! mkdir "$dir" 2>/dev/null; do
    # steal a stale lock whose holder process is gone
    if [ -f "$dir/pid" ]; then
      p="$(cat "$dir/pid" 2>/dev/null || echo)"
      if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$dir"; continue; fi
    fi
    sleep 1; waited=$((waited + 1))
    if [ "$waited" -ge "$timeout" ]; then return 1; fi
  done
  echo $$ > "$dir/pid" 2>/dev/null || true
  "$@"; rc=$?
  rm -rf "$dir"
  return "$rc"
}

# Best-effort LAN IP across platforms (for the "open this URL" banner).
hearth_lan_ip() {
  ip="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
  if [ -z "$ip" ] && [ "$(hearth_os)" = macos ]; then
    for nic in en0 en1 en2; do
      ip="$(ipconfig getifaddr "$nic" 2>/dev/null || true)"
      [ -n "$ip" ] && break
    done
  fi
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -z "$ip" ] && ip="$(hostname -i 2>/dev/null | awk '{print $1}')"
  [ -z "$ip" ] && ip="localhost"
  printf '%s' "$ip"
}
