# Hearth — Security Model

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

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
- Login throttling: after 5 consecutive failures, per-account exponential
  backoff (capped 15 min) stored on the user row — refused *before* argon2 runs,
  so it also blunts the unauth CPU-flood lever. A success clears the counters.
- Account recovery (no mail server): the operator mints a one-time token from a
  shell — `docker compose exec hearth python -m hearth.recover <email>` — and
  redeems it at `/reset` ("Forgot password?" on the sign-in screen). Tokens are
  SHA-256-hashed in `password_resets`, single-use, expire in 1 hour, and revoke
  every session on redemption. Shell access to the box is the recovery bar.

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
- CSRF: `SameSite=Lax` session cookie + a JSON-only API (no cookie-authenticated
  HTML form posts). No separate CSRF token; SameSite=Lax is the control.
- Backups: `/data` volume contains hashes and Fernet ciphertexts — safe to
  back up as-is; restoring on a new host requires the same `HEARTH_SECRET`
  (documented prominently — lose the secret, re-enter third-party tokens).

## SSRF / outbound requests

- User-supplied connection URLs (HA, InfluxDB, LLM) and the setup-wizard probes
  (`/api/ha/test`, `/api/ha/inventory`, `/api/influx/inspect`) are validated by
  `urlguard.url_block_reason`: the host is resolved and **link-local
  (incl. 169.254.169.254 cloud-metadata), unspecified, multicast and reserved**
  addresses are refused. Loopback and RFC1918 private ranges are **allowed** —
  they are legitimate homelab targets. (Python's `is_private` includes 169.254/16,
  so link-local is checked explicitly rather than via an is_private allow-list.)
- Outbound responses are size-capped (chunked reads via `adapters/_httpcap`):
  LLM 8 MB, HA `/api/config` 4 MB, HA `/api/states` 32 MB — a hostile upstream
  can't OOM the box. All outbound clients also carry timeouts.
- Remote InfluxDB bucket names are escaped (`_flux_tag`) before Flux interpolation.

## Operator hardening notes (security audit, June 2026)

Residual items that are deployment/ops concerns, not code holes:

- **Run setup on a trusted network.** While no admin exists, the wizard probe +
  `/api/tokens` endpoints are reachable without a session (by design — there's no
  credential to check yet). They close the moment the first admin is created.
- **No app-level rate limiting** on expensive authenticated endpoints
  (`/models/train`, `/discovery/run`, `/import/history`). Heavy jobs are already
  gated by the resource governor; for internet-exposed deploys add a reverse-proxy
  rate limit. Single-admin homelab risk is self-inflicted load only.
- **Model artifacts are loaded with `joblib`/pickle.** Only load model files Hearth
  itself wrote to `/data/models`; never import a `.joblib` from an untrusted source
  (pickle deserialization can execute code). The stored path is server-generated.
- **`/data` permissions** follow the container umask; keep the volume off shared
  hosts — it holds the sqlite DB (hashes + Fernet ciphertexts) and model files.

## What we deliberately don't do (v1)

OAuth/OIDC SSO, TOTP 2FA, per-entity ACLs — homelab scope, tracked as
post-v1 ideas. The architecture doesn't preclude them: auth is middleware +
`security.py`, swappable without touching domain code.
