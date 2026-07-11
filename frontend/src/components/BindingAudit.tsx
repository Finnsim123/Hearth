/**
 * BindingAudit — the one-tap hand on the self-awareness loop.
 *
 * Backend detects models leaning on a device's AMBIENT entity (coffee-machine
 * temperature) while its DIRECT sibling (the power switch) sits unused, and
 * stores findings. This card says it in plain words and offers a single tap:
 * bind the right sensor, stop training on the wrong one, retrain — the
 * promotion gate only keeps the result if it actually helped.
 *
 * Backend: GET /api/audit/bindings · POST /api/audit/bindings/apply.
 * The advisory CTA links to /sensors#audit — keep the anchor id.
 */
import { useEffect, useState } from "react";
import Card from "./Card";

type Finding = {
  kind: "bind_sibling" | "exclude_ambient";
  binding_id: number; binding_name: string; entity_id: string;
  reliance: number; device_id: string | null; device: string | null;
  candidates: string[]; why: string;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

export default function BindingAudit() {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [done, setDone] = useState<Record<number, string>>({});

  useEffect(() => {
    fetch("/api/audit/bindings").then(j)
      .then((d) => setFindings(d.findings ?? []))
      .catch(() => setFindings([]));
  }, []);

  const apply = async (f: Finding) => {
    setBusy(f.binding_id);
    try {
      const r = await fetch("/api/audit/bindings/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ binding_id: f.binding_id, kind: f.kind,
          candidate: choice[f.binding_id] ?? f.candidates[0] ?? null }),
      }).then(j);
      setDone((d) => ({ ...d, [f.binding_id]:
        r.bound ? `Bound ${r.bound.entity_id} · stopped training on ${r.excluded}`
                : `Stopped training on ${r.excluded}`
        + (r.retraining ? " · retraining…" : "") }));
    } catch {
      setDone((d) => ({ ...d, [f.binding_id]: "Couldn't apply — check the Sensors list." }));
    }
    setBusy(null);
  };

  if (!findings || findings.length === 0) return null;

  return (
    <div id="audit">
      <Card icon="warning" title="The model is leaning on the wrong sensors"
        sub="Hearth checked what its predictions actually rest on. These sensors carry weight they shouldn't — each fix is one tap: bind the right signal, stop training on the wrong one, retrain. The change only sticks if it measurably helps.">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {findings.map((f) => (
            <div key={`${f.binding_id}:${f.kind}`}
              style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px",
                       display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 13.5 }}>
                <strong>{f.device || f.binding_name}</strong>
                <span style={{ color: "var(--text-dim)" }}> — {f.why}</span>
              </div>
              {done[f.binding_id] ? (
                <span style={{ fontSize: 12.5, color: "var(--ok, #34D399)" }}>✓ {done[f.binding_id]}</span>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  {f.kind === "bind_sibling" && f.candidates.length > 1 && (
                    <select value={choice[f.binding_id] ?? f.candidates[0]}
                            onChange={(e) => setChoice((c) => ({ ...c, [f.binding_id]: e.target.value }))}
                            style={{ fontSize: 12.5 }}>
                      {f.candidates.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  )}
                  {f.kind === "bind_sibling" && f.candidates.length === 1 && (
                    <code style={{ fontSize: 12, color: "var(--text-dim)" }}>{f.candidates[0]}</code>
                  )}
                  <button className="btn btn-primary" disabled={busy === f.binding_id}
                          onClick={() => apply(f)}>
                    {busy === f.binding_id ? "Fixing…"
                      : f.kind === "bind_sibling" ? "Fix it — bind this instead" : "Stop training on it"}
                  </button>
                  <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
                    keeps the sensor visible to discovery — only the model stops using it
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
