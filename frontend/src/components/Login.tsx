/** Sign-in screen — shown whenever /api/auth/me returns 401 after setup. */
import { useState } from "react";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await fetch("/api/auth/login", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }) });
    setBusy(false);
    if (r.ok) onSuccess();
    else setError((await r.json()).detail ?? "Wrong email or password");
  };
  return (
    <div style={{ minHeight: "70vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <form onSubmit={submit} className="card"
            style={{ width: 360, display: "flex", flexDirection: "column", gap: 14, padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <svg width="26" height="26" viewBox="0 0 32 32" fill="none" stroke="currentColor"
               strokeWidth="2.5" strokeLinejoin="round" aria-hidden>
            <path d="M16 4 L28 14 V26 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 V14 Z" />
            <circle cx="16" cy="20" r="3.5" fill="var(--accent)" stroke="none" />
          </svg>
          <span style={{ fontWeight: 600, fontSize: 18, letterSpacing: "-0.02em" }}>hearth</span>
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
          Email
          <input type="email" value={email} autoFocus onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 500 }}>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <span style={{ color: "var(--danger)", fontSize: 13.5 }}>{error}</span>}
        <button className="btn btn-primary" disabled={busy || !email || !password} type="submit">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
