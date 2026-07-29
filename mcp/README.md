# Hearth × Claude — the MCP server

Connect **Claude** to your Hearth instance and talk to your home in plain
language. This folder is a small [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes Hearth's data and a few safe actions as tools Claude can call —
so instead of opening the dashboard, you just ask.

```
You:    What's everyone doing right now?
Claude: Alexander is cooking (82% confident); Nora is away.

You:    How's Alex's model doing?
Claude: 88.9% real-world accuracy, validated, 795 training windows. It's solid on
        cooking but still mixes up away/home — adding a motion sensor near the
        entrance would help most.

You:    Which patterns need naming?
Claude: One: pattern 12 (40 windows) — signature "sofa ↑, media playing ↑".
You:    That's movie night. Name it that.
Claude: Done — pattern 12 named "movie", and it labelled ~40 windows of history.
```

## Why it's useful

- **Ask instead of navigate.** The dashboard is great, but a question is faster.
  "Anything I should know?" beats hunting through pages of advisories.
- **Tune the model from the couch.** Claude can walk you through the honest
  capability read-out ("what can't it do, and why"), then name a pattern, answer a
  pending question, or kick off a retrain — the things that actually improve
  accuracy — conversationally.
- **Cross-reference your other tools.** If you also have the Home Assistant MCP
  connected, Claude can reason across both: *"compare what Hearth thinks Alex is
  doing with what the HA sensors show."*
- **A natural-language window into a glass box.** Hearth is built to be honest
  about what it knows; MCP lets you interrogate that honesty directly — accuracy,
  evidence, drift, coverage gaps — and get it explained back in plain words.

Typical things to ask:

| You want to… | Ask something like |
|---|---|
| A quick check-in | "What's everyone doing right now?" |
| Model health | "Is Alex's model validated? What's it bad at?" |
| Improve accuracy | "What would help the model most?" then act on it |
| Clear the backlog | "What questions is Hearth waiting on?" → answer them |
| Name discoveries | "Which patterns need naming?" → "call 7 'reading'" |
| Understand routines | "How regular is my rhythm? How's the home footprint?" |
| Home wiring | "Which sensors reliably fire in sequence?" |
| Stay informed | "Any advisories?" · "Sensor coverage gaps?" |

## How it works

The server is a **thin wrapper over Hearth's REST API** authenticated with an API
token — it changes nothing about the running Hearth service. Transport is
**stdio**: Claude Desktop launches it as a local process, so nothing is exposed to
the network and the token lives only in your local Claude config. It's
**read-first** — every tool is a read except `answer_question`, `name_pattern`, and
`train_model`. Destructive operations (forget/delete a person) are deliberately
**not** exposed; do those in the Hearth UI behind its confirmations.

## Setup (≈5 minutes)

**1. Mint an API token** in Hearth: Settings → tokens → create an
**integration-scoped** token (the three action tools need write access; a
`readonly` token allows only the read tools). Copy it.

**2. Install the deps.** Requires **Python 3.10+** (the `mcp` SDK won't install on
3.9 — macOS's built-in Python). If `python3 --version` is < 3.10, install a newer
one (`brew install python@3.12`, or [uv](https://docs.astral.sh/uv/)).

```bash
cd hearth/mcp
python3 -m venv .venv          # use python3.12 explicitly if python3 is 3.9
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

**3. Sanity-check** it can import and start (it'll sit silently waiting for a
client on stdio — that's success; Ctrl-C to stop):

```bash
HEARTH_URL=http://localhost:8420 HEARTH_TOKEN=<your-token> .venv/bin/python hearth_mcp.py
```

**4. Add it to Claude Desktop.** Config file (macOS):
`~/Library/Application Support/Claude/claude_desktop_config.json`. Add a `hearth`
entry under `mcpServers` (keep any existing servers — merge, don't overwrite):

```json
{
  "mcpServers": {
    "hearth": {
      "command": "/absolute/path/to/hearth/mcp/.venv/bin/python",
      "args": ["/absolute/path/to/hearth/mcp/hearth_mcp.py"],
      "env": {
        "HEARTH_URL": "http://localhost:8420",
        "HEARTH_TOKEN": "your-integration-token"
      }
    }
  }
}
```

Use `http://localhost:8420` if Claude Desktop runs on the same machine as Hearth;
otherwise the host's LAN IP (e.g. `http://192.168.1.50:8420`). To merge safely
into an existing config from the terminal:

```bash
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
cp "$CFG" "$CFG.bak" 2>/dev/null
python3 - "$CFG" <<'EOF'
import json, os, sys
p = sys.argv[1]
cfg = json.load(open(p)) if os.path.exists(p) else {}
cfg.setdefault("mcpServers", {})["hearth"] = {
    "command": "/absolute/path/to/hearth/mcp/.venv/bin/python",
    "args": ["/absolute/path/to/hearth/mcp/hearth_mcp.py"],
    "env": {"HEARTH_URL": "http://localhost:8420", "HEARTH_TOKEN": "your-token"},
}
json.dump(cfg, open(p, "w"), indent=2)
print("servers now:", list(cfg["mcpServers"]))
EOF
```

**5. Fully quit and reopen Claude Desktop** (⌘Q, not just close the window), then
ask *"list the people in Hearth."* Alexander and Nora coming back means you're wired
up.

## Tools

| Tool | Reads / Acts | What it does |
|---|---|---|
| `list_people` | read | household members (id + name) |
| `current_activity` | read | latest predicted activity + confidence |
| `model_status` | read | accuracy / validated / train windows |
| `capability` | read | what each activity can/can't do + remedy |
| `behaviour_summary` | read | footprint, daily rhythm, sleep/away |
| `sensor_health` | read | live/constant/no-data + presence gaps |
| `home_wiring` | read | lead/lag sensor sequences |
| `patterns` | read | unnamed clusters awaiting a name |
| `pending_questions` | read | open questions to answer |
| `advisories` | read | active advisories |
| `answer_question` | **act** | confirm a label for a question |
| `name_pattern` | **act** | name a pattern (labels weeks of history) |
| `train_model` | **act** | trigger a training run |

## Security & notes

- **stdio = no network exposure.** The token sits only in your local Claude config.
  Treat it like a password; revoke + re-mint in Settings if it leaks.
- **Token scope:** read tools work with a `readonly` token; the three actions need
  `integration` scope.
- **Actions stay honest:** `train_model` still respects the promotion gate;
  `answer_question` writes a confirmed (gold, if it was an explore ask) label —
  exactly as if you'd tapped it in the Inbox.
- **Reaching it from elsewhere (v2).** stdio is local-only. To use Hearth from
  cloud claude.ai or your phone, the next step is a token-authed `/mcp` endpoint
  mounted inside Hearth behind a Tailscale/Cloudflare tunnel — not built here yet.

*Troubleshooting: if Claude reports a connection error, Hearth usually isn't
reachable at `HEARTH_URL` from where Claude runs (wrong host/port, or Hearth not
up). Check `docker compose ps` and try the sanity-check command from step 3.*
