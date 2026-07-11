# Hearth MCP server

Connect **Claude** to your Hearth instance. This is a small [Model Context
Protocol](https://modelcontextprotocol.io) server that wraps Hearth's REST API as
tools, so you can ask Claude things like:

- *"What's everyone doing right now?"*
- *"How's Alex's model doing — is it validated yet?"*
- *"Which patterns need naming?"* → *"Name pattern 12 'reading'."*
- *"What questions is Hearth waiting on?"* → *"Answer #48 with cooking."*
- *"Anything I should know?"* (advisories) · *"How regular is my rhythm?"*

It's **read-first**: everything above is a read except answering a question,
naming a pattern, and triggering a train. Destructive ops (forget/delete a person)
are intentionally **not** exposed — do those in the Hearth UI.

The server talks to Hearth over HTTP with an API token, so it changes nothing
about the running Hearth service. Transport is **stdio** — Claude Desktop launches
it as a local process.

## Setup

**1. Mint an API token** in Hearth: Settings → (tokens/integration) → create an
**integration-scoped** token (the action tools need write access; a `readonly`
token only allows the read tools). Copy it.

**2. Install the deps** (a venv keeps it isolated):

```bash
cd Hearth/mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**3. Sanity-check** it can reach Hearth:

```bash
HEARTH_URL=http://localhost:8420 HEARTH_TOKEN=<your-token> .venv/bin/python hearth_mcp.py
```
(It'll sit waiting for an MCP client on stdio — Ctrl-C to stop. No error = good.)

**4. Add it to Claude Desktop.** Edit the config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "hearth": {
      "command": "/absolute/path/to/Hearth/mcp/.venv/bin/python",
      "args": ["/absolute/path/to/Hearth/mcp/hearth_mcp.py"],
      "env": {
        "HEARTH_URL": "http://localhost:8420",
        "HEARTH_TOKEN": "your-integration-token"
      }
    }
  }
}
```

Use `http://localhost:8420` if Claude Desktop runs on the same machine as Hearth;
otherwise the Mac mini's LAN IP (e.g. `http://192.168.1.241:8420`).

**5. Restart Claude Desktop.** You should see a 🔌 / tools indicator; ask
*"list the people in Hearth"* to confirm.

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

## Notes

- **Token scope:** read tools work with a `readonly` token; `answer_question` /
  `name_pattern` / `train_model` need an `integration` token.
- **Security:** stdio means nothing is exposed to the network — the token lives
  only in your local Claude config. If you later want to reach Hearth from
  cloud claude.ai or your phone, the next step is a `/mcp` endpoint mounted inside
  Hearth behind a Tailscale/Cloudflare tunnel (a v2, not built here).
- **Actions are honest:** `train_model` still respects the promotion gate;
  `answer_question` writes a confirmed (gold, if it was an explore ask) label.
