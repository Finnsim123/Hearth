/** Sign-in screen — shown whenever /api/auth/me returns 401 after setup.
 *  Also hosts the password-recovery form (no mail server: the operator mints a
 *  token from a shell, then redeems it here). Reachable at /reset too. */
import { useState } from "react";

const Logo = () => (
  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" stroke="currentColor"
         strokeWidth="2.5" strokeLinejoin="round" aria-hidden>
      <path d="M16 4 L28 14 V26 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 V14 Z" />
      <circle cx="16" cy="20" r="3.5" fill="var(--accent)" stroke="none" />
    </svg>
    <span style={{ fontWeight: 600, fontSize: 18, letterSpacing: "-0.02em" }}>hearth</span>
  </div>
);

const linkBtn: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer",
  color: "var(--text-dim)", fontSize: 13, textDecoration: "underline", alignSelf: "center",
};

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [mode, setMode] = useState<"login" | "reset">(
    () => (typeof window !== "undefined" && window.location.pathname === "/reset") ? "reset" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [newPw, setNewPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const wrap = (form: React.ReactNode) => (
    <div style={{ minHeight: "70vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <form onSubmit={(e) => e.preventDefault()} className="card"
            style={{ width: 380, display: "flex", flexDirection: "column", gap: 14, padding: 28 }}>
        <Logo />
        {form}
      </form>
    </div>
  );

  const signIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    const r = await fetch("/api/auth/login", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }) });
    setBusy(false);
    if (r.ok) onSuccess();
    else setError((await r.json()).detail ?? "Wrong email or password");
  };

  const reset = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null); setOk(null);
    const r = await fetch("/api/auth/reset", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token.trim(), new: newPw }) });
    setBusy(false);
    if (r.ok) {
      setOk("Password reset — sign in with your new one.");
      setMode("login"); setToken(""); setNewPw(""); setPassword("");
    } else {
      setError((await r.json()).detail ?? "Invalid or expired recovery token");
    }
  };

  if (mode === "reset") {
    return wrap(<>
      <p style={{ margin: 0, fontSize: 13.5, color: "var(--text-dim)" }}>
        Locked out? On the machine running Hearth, get a one-time token:
      </p>
      <code style={{ fontSize: 12, background: "var(--surface-2)", border: "1px solid var(--border)",
                     borderRadius: 8, padding: "8px 10px", overflowWrap: "anywhere" }}>
        docker compose exec hearth python -m hearth.recover you@example.com
      </code>
      <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
        Recovery token
        <input value={token} autoFocus placeholder="hrt_reset_…"
               onChange={(e) => setToken(e.target.value)} />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
        New password
        <input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
      </label>
      {error && <span style={{ color: "var(--danger)", fontSize: 13.5 }}>{error}</span>}
      <button className="btn btn-primary" disabled={busy || !token || newPw.length < 10} onClick={reset}>
        {busy ? "Resetting…" : "Set new password"}
      </button>
      <button style={linkBtn} onClick={() => { setMode("login"); setError(null); }}>Back to sign in</button>
    </>);
  }

  return wrap(<>
    <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
      Email
      <input type="email" value={email} autoFocus onChange={(e) => setEmail(e.target.value)} />
    </label>
    <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
      Password
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
    </label>
    {ok && <span style={{ color: "var(--ok, #34D399)", fontSize: 13.5 }}>{ok}</span>}
    {error && <span style={{ color: "var(--danger)", fontSize: 13.5 }}>{error}</span>}
    <button className="btn btn-primary" disabled={busy || !email || !password} onClick={signIn} type="submit">
      {busy ? "Signing in…" : "Sign in"}
    </button>
    <button style={linkBtn} onClick={() => { setMode("reset"); setError(null); }}>Forgot password?</button>
  </>);
}
