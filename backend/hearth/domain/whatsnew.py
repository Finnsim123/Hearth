"""What's-new — after an in-app update lands, the buddy announces the change.

The container has no git, so the HOST updater (deploy/hearth-updater.sh) writes
the deployed commit's sha + subject + body into the shared update_status.json.
This module maps that to the RUNNING build (HEARTH_BUILD_SHA, baked at build) and
decides whether it's a build the user hasn't acknowledged yet. The buddy surfaces
it once (tone 'news'); dismissing marks this build seen. First install is marked
seen at setup so a fresh box never shows a spurious 'what's new'.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

SEEN_KEY = "update.seen_sha"


def _build_sha() -> str:
    return os.getenv("HEARTH_BUILD_SHA", "dev")


def read_update_status() -> dict:
    """The host updater's status file, or {} when there is no updater."""
    shared = Path(os.getenv("HEARTH_SHARED_DIR", "/shared"))
    f = shared / "update_status.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clean_subject(subject: str) -> str:
    """Make a conventional-commit subject read like a sentence for end users:
    drop a leading 'feat:'/'fix(scope):' prefix and capitalise."""
    s = re.sub(r"^[a-z]+(\([^)]*\))?!?:\s*", "", subject.strip(), flags=re.I)
    return (s[:1].upper() + s[1:]) if s else subject.strip()


def mark_seen(repo) -> None:
    """Acknowledge the running build — the buddy stops announcing it."""
    try:
        repo.set_setting(SEEN_KEY, _build_sha())
    except Exception:
        pass


def pending_news(repo) -> dict | None:
    """The unseen update to announce, or None. Returns {sha, subject, body}."""
    build = _build_sha()
    if build in ("", "dev"):
        return None                              # local/dev build — nothing to announce
    st = read_update_status()
    sha = st.get("deployed_sha") or st.get("local")
    subject = str(st.get("deployed_subject") or "").strip()
    if not subject:
        return None
    # only announce when the shared file actually describes THIS running build
    if sha and sha != build:
        return None
    try:
        if repo.get_setting(SEEN_KEY) == build:
            return None
    except Exception:
        return None
    return {"sha": build, "subject": _clean_subject(subject),
            "body": str(st.get("deployed_body") or "").strip()}
