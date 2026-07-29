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
| Account passwords | first-boot wizard / Settings (change password) | SQLite `users.password_hash` | **argon2id** (no plaintext, ever) | `security.py` |
| Browser sessions | login | HTTP-only cookie + SQLite `sessions` | random 256-bit id, **SHA-256 hash** in DB; cookie `Secure` (when TLS), `HttpOnly`, `SameSite=Lax` | `security.py` + auth middleware |
| Hearth API tokens (HA integration etc.) | Settings → API tokens | SQLite `api_tokens.token_hash` | **SHA-256 hash**, shown once at mint, scoped, revocable | `security.py` |
| Third-party tokens (HA, MQTT, InfluxDB, LLM) | wizard / Settings → Connections | SQLite `connections.token_encrypted` | **Fernet** (AES-128-CBC+HMAC), key derived from `HEARTH_SECRET` via HKDF | `security.py`; decrypted only inside the adapter that needs it, at call time |
| Model artifacts | training | `/data/models` volume | not secret, but volume-scoped | ModelStore |

Never stored anywhere: plaintext passwords, plaintext API tokens after mint,
raw `HEARTH_SECRET` derivatives. Never logged: any of the above (`security.py`
values have `__repr__` redaction; tokens are masked `hrt_****…` in UI and logs).

## Accounts

- First boot: zero users → the API is closed except health/login/reset and the
  wizard's setup surface (`/api/setup/complete` plus its probe endpoints —
  `/api/ha/test`, `/api/ha/inventory`, `/api/influx/inspect`, `/api/tokens`,
  triage/estimate previews). Those setup endpoints close the moment the first
  user exists.
- Passwords: argon2id with library defaults (memory-hard), per-hash salt,
  transparent rehash-on-login when parameters improve. Minimum length 10;
  no composition rules (NIST 800-63B).
- Roles: a `role` column (`admin`/`member`) exists on users and is returned by
  `/api/auth/me`, but **no endpoint enforces it yet** — any logged-in user can
  do anything. Role enforcement is post-v1 (see the last section); today the
  role's value is that labels record *who* confirmed them.
- Sessions: server-side rows with a fixed 30-day lifetime from creation (not a
  sliding idle window). Each login mints a fresh session id; logout deletes the
  row, not just the cookie. There is **no session-management UI** — revoking
  someone else's session means deleting their `sessions` row in SQLite (or
  changing the password via recovery, which revokes all sessions).
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
  checked against `api_tokens` by hash. `readonly` grants the read surface
  (predictions, models, health, patterns…); `integration` grants the same reads
  plus a narrow write set (feedback actions, train, inbox answers, cluster
  naming, person pause/resume). The allow-lists live in `api/scopes.py`;
  destructive endpoints (token mint, rollback, forget) are deliberately
  unreachable by bearer token — session only. Tokens carry no expiry by default
  but show last-used and are one-click revocable in Settings → API tokens.
- The feedback webhook (`/api/feedback/action`) requires a scoped token too —
  shipped HA blueprint includes it in the rest_command.
- Tokens minted during setup (the wizard's HA-integration step uses the open
  `/api/tokens` endpoint) **remain valid after setup completes** — that's how
  the integration keeps working. If you aborted a setup halfway, check
  Settings → API tokens and revoke anything you don't recognise.

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

## Two-factor authentication (TOTP, RFC 6238)

- Optional per-account. Secret generated server-side, stored **encrypted**
  (`HEARTH_SECRET`), and only marked enabled after the user proves a live code
  (`/auth/2fa/enable`) — a mistyped enrolment can't lock anyone out.
- Login is two-step: a correct password with 2FA on returns `twofa_required`
  and **no session**; the cookie is issued only after the TOTP or a recovery
  code verifies. TOTP compares constant-time (`hmac.compare_digest`, ±1 step
  for clock skew).
- **Brute-force**: a wrong second factor feeds the same exponential backoff as a
  wrong password (`note_2fa_failure`), and `verify_login` won't clear the
  counters while a factor is owed — so the 6-digit code can't be guessed once the
  password is known. Recovery codes are 48-bit, hashed (SHA-256), single-use, and
  matched constant-time.
- Disabling 2FA requires a password re-auth. Setup/enable/disable all sit behind
  the session middleware. Recovery codes are shown once at enable time.

## Outbound email (SMTP relay)

- Not a mail server — Hearth authenticates to the operator's existing SMTP
  (Gmail app-password, Fastmail, SES…) and hands off one message. Credentials
  are stored as the encrypted `smtp` connection (password via `HEARTH_SECRET`,
  same path as other tokens) and masked in API responses.
- TLS: STARTTLS or implicit SSL with `ssl.create_default_context()` (certificate
  verification on). A `none` (plaintext) option exists for a trusted-LAN relay —
  avoid it; it sends the relay password in clear.
- **Header-injection guard**: every recipient is validated by
  `security.valid_email` (rejects CR/LF/tab/NUL, commas, and non-address shapes)
  and the From name/address + Subject are CR/LF-stripped, so a crafted address
  can't smuggle a `Bcc:` or extra headers through the relay. Addresses are also
  validated at input (`POST /persons`, test-send).
- **`/auth/forgot`**: public (a locked-out user must reach it), but it always
  returns `ok` (no account enumeration), only mails a known address, and is
  rate-limited to one recovery mail per user per 15 min (mailbomb / quota guard).
  The reset link is built from the configured `hearth_base_url`, not the request
  Host header, so it can't be poisoned. Token is high-entropy, hashed, single-use,
  1-hour TTL. The `python -m hearth.recover` CLI remains the no-SMTP fallback.
- The newsletter renderer HTML-escapes all member/activity strings; the optional
  LLM intro is escaped before insertion.

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
- **InfluxDB port 8086 is published to the LAN** by the default compose file
  (so you can reach the Influx UI for backups/inspection). Anyone on the LAN
  with the admin token in `.env` has full DB access. If only Hearth uses it,
  delete the `ports:` mapping on the `influxdb` service — the containers talk
  over the compose network regardless.
- **The self-updater executes whatever `git pull` delivers.** The update path
  (cron/launchd → `hearth-updater.sh` → `docker compose up --build`) trusts the
  git remote; whoever can push to your configured remote/branch can run code on
  the box. Pin the remote to a repo you control and protect its main branch.

## What we deliberately don't do (v1)

OAuth/OIDC SSO, per-entity ACLs, **role enforcement** (the `admin`/`member`
distinction is stored but not checked), and a **session-management UI** — homelab
scope, tracked as post-v1 ideas. (TOTP 2FA *is* implemented — see the section
above.) The architecture doesn't preclude the rest: auth is middleware +
`security.py`, swappable without touching domain code.
