# Hearth — Security Model

One page that answers: where does every secret live, and which code touches it.

## The one rule

**All cryptographic operations live in `backend/hearth/security.py`.** No
other module hashes a password, encrypts a token, mints an API token, or signs
a session. Adapters and API routes call that module's five functions; domain
code never sees a secret at all. Code review heuristic: `import hashlib`,
`import secrets`, `argon2`, or `Fernet` anywhere outside `security.py` is a
defect.

## Secrets inventory

| Secret | Created | Stored | Form at rest | Touched by |
|---|---|---|---|---|
| `HEARTH_SECRET` | user, in `.env` | docker env only | plaintext env (root key) | `security.py` only |
| Account passwords | first-boot wizard / Settings → Users | SQLite `users.password_hash` | **argon2id** (no plaintext, ever) | `security.py` |
| Browser sessions | login | HTTP-only cookie + SQLite `sessions` | random 256-bit id, **SHA-256 hash** in DB; cookie `Secure` (when TLS), `HttpOnly`, `SameSite=Lax` | `security.py` + auth middleware |
| Hearth API tokens (HA integration etc.) | Settings → API tokens | SQLite `api_tokens.token_hash` | **SHA-256 hash**, shown once at mint, scoped, revocable | `security.py` |
| Third-party tokens (HA, MQTT, InfluxDB, LLM) | wizard / Settings → Connections | SQLite `connections.token_encrypted` | **Fernet** (AES-128-CBC+HMAC), key derived from `HEARTH_SECRET` via HKDF | `security.py`; decrypted only inside the adapter that needs it, at call time |
| Model artifacts | training | `/data/models` volume | not secret, but volume-scoped | ModelStore |

Never stored anywhere: plaintext passwords, plaintext API tokens after mint,
raw `HEARTH_SECRET` derivatives. Never logged: any of the above (`security.py`
values have `__repr__` redaction; tokens are masked `hrt_****…` in UI and logs).

## Accounts

- First boot: zero users → every route except `/api/auth/setup` and static
  assets redirects to the create-admin screen. Setup is disabled forever after
  the first user exists.
- Passwords: argon2id with library defaults (memory-hard), per-hash salt,
  transparent rehash-on-login when parameters improve. Minimum length 10;
  no composition rules (NIST 800-63B).
- Roles: `admin` (everything) and `member` (dashboard, inbox, own settings).
  Household members can get their own login for inbox labeling — labels then
  record *who* confirmed.
- Sessions: server-side rows (revocable in Settings → Users), 30-day idle
  expiry, rotation on login. Logout = row deletion, not just cookie clearing.
- Login throttling: per-account exponential backoff stored on the user row
  (homelab-appropriate; no captchas).

## API authentication

- SPA: session cookie.
- HA integration / external consumers: `Authorization: Bearer hrt_<token>`
  checked against `api_tokens` by hash; scope `integration` grants
  read-predictions + write-overrides + WS subscribe, nothing else. `readonly`
  grants GETs only. Tokens carry no expiry by default but show last-used and
  are one-click revocable.
- The feedback webhook (`/api/feedback/action`) requires a scoped token too —
  shipped HA blueprint includes it in the rest_command.

## Transport & deployment

- Hearth binds `0.0.0.0:8420` inside the LAN; TLS is the reverse-proxy's job
  (docs show Caddy/Traefik snippets in Phase 5). Cookies upgrade to `Secure`
  automatically when the request arrives over HTTPS.
- CORS: same-origin only (SPA is served by the backend); the HA integration
  uses bearer tokens, not cookies, so CORS never needs widening.
- CSRF: `SameSite=Lax` + custom-header check on mutating routes.
- Backups: `/data` volume contains hashes and Fernet ciphertexts — safe to
  back up as-is; restoring on a new host requires the same `HEARTH_SECRET`
  (documented prominently — lose the secret, re-enter third-party tokens).

## What we deliberately don't do (v1)

OAuth/OIDC SSO, TOTP 2FA, per-entity ACLs — homelab scope, tracked as
post-v1 ideas. The architecture doesn't preclude them: auth is middleware +
`security.py`, swappable without touching domain code.
