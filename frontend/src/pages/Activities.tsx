/**
 * Activities — the household's taxonomy. Rename, set the notification phrase,
 * mark activities silent (never push — e.g. sleeping), add new ones, and see
 * the bootstrap rules behind each activity in plain language.
 * Spec: docs/UI_SPEC.md §Activities.
 */
import { useEffect, useState } from "react";
import { Icon } from "../icons";

type Activity = {
  id: number | null; slug: string; name: string; phrase: string | null;
  icon: string; color: string; parent_id: number | null;
  enabled: boolean; silent: boolean;
};
type Rule = {
  id: number; activity_slug: string; person_id: string | null;
  predicate: Record<string, unknown>; priority: number;
  origin: string; enabled: boolean;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Render the JSON predicate AST as readable text: kitchen_presence_frac > 0.3 AND … */
function predicateText(p: Record<string, unknown>): string {
  if (!p || typeof p !== "object") return "?";
  if (Array.isArray((p as { all?: unknown[] }).all))
    return ((p as { all: Record<string, unknown>[] }).all).map(predicateText).join("  AND  ");
  if (Array.isArray((p as { any?: unknown[] }).any))
    return "(" + ((p as { any: Record<string, unknown>[] }).any).map(predicateText).join("  OR  ") + ")";
  const { feat, op, value } = p as { feat?: string; op?: string; value?: unknown };
  if (feat) return `${feat} ${op} ${String(value)}`;
  return JSON.stringify(p);
}

function ActivityCard({ a: initial, rules, persons, parents, onSaved }: {
  a: Activity; rules: Rule[]; persons: Record<string, string>;
  parents: Activity[]; onSaved: () => void;
}) {
  const [a, setA] = useState(initial);
  const [expanded, setExpanded] = useState(false);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "ok" | "fail">("idle");
  const u = (patch: Partial<Activity>) => { setA({ ...a, ...patch }); setState("idle"); };
  const save = async () => {
    setState("saving");
    try { await post("/api/activities", a).then(j); setState("ok"); onSaved(); }
    catch { setState("fail"); }
  };
  const mine = rules.filter((r) => r.activity_slug === a.slug);
  const parentName = a.parent_id ? parents.find((p2) => p2.id === a.parent_id)?.name : null;
  const summary = [
    parentName ? `in ${parentName}` : "top-level state",
    a.enabled ? null : "disabled",
    `${mine.length} rule${mine.length === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden",
                  opacity: a.enabled ? 1 : 0.6 }}>
      <button onClick={() => setExpanded(!expanded)}
        style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", border: "none",
                 background: expanded ? "var(--surface-2)" : "transparent", cursor: "pointer",
                 color: "var(--text)", padding: "12px 14px", textAlign: "left" }}>
        <span style={{ width: 11, height: 11, borderRadius: "50%", background: a.color, flexShrink: 0 }} />
        <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
          <strong style={{ fontSize: 14.5 }}>{a.name}</strong>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            <code>{a.slug}</code> · {summary}
          </span>
        </div>
        {a.silent && (
          <span title="Never pushed as a notification — questions go to the Inbox"
                style={{ marginLeft: 8, fontSize: 11.5, padding: "2px 8px", borderRadius: 99,
                         border: "1px solid var(--border)", color: "var(--text-dim)",
                         display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <Icon name="bell-off" size={11} /> silent
          </span>
        )}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden
             style={{ marginLeft: "auto", flexShrink: 0, color: "var(--text-dim)",
                      transition: "transform .18s", transform: expanded ? "none" : "rotate(-90deg)" }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {expanded && (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12,
                    borderTop: "1px solid var(--border)" }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 5, maxWidth: 240 }}>
        <span style={{ fontSize: 13.5, fontWeight: 500 }}>Name</span>
        <input value={a.name} onChange={(e) => u({ name: e.target.value })}
               style={{ fontWeight: 600, fontSize: 15 }} />
      </label>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span style={{ fontSize: 13.5, fontWeight: 500 }}>Notification phrase</span>
          <input placeholder="watching a movie" value={a.phrase ?? ""}
                 onChange={(e) => u({ phrase: e.target.value || null })} />
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            Used in questions: “Are you {a.phrase || a.name.toLowerCase()}?”
          </span>
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "center" }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5, cursor: "pointer" }}>
            <input type="checkbox" checked={a.silent}
                   onChange={(e) => u({ silent: e.target.checked })}
                   style={{ width: 15, height: 15 }} />
            Silent — never push about this activity
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5, cursor: "pointer" }}>
            <input type="checkbox" checked={a.enabled}
                   onChange={(e) => u({ enabled: e.target.checked })}
                   style={{ width: 15, height: 15 }} />
            Enabled — model predicts this activity
          </label>
          {parents.length > 0 && a.parent_id !== undefined && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}
                   title="Fine activities live INSIDE a state: 'home and eating' are simultaneously true. The state model stays accurate; a smaller model picks among the activities within it.">
              Within
              <select value={a.parent_id ?? ""} disabled={parents.some((p2) => p2.parent_id === a.id)}
                      onChange={(e) => u({ parent_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">— top-level state —</option>
                {parents.filter((p2) => p2.id !== a.id && p2.parent_id === null).map((p2) => (
                  <option key={p2.id} value={p2.id!}>{p2.name}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      <button className="btn btn-ghost" onClick={() => setOpen(!open)}
              style={{ alignSelf: "flex-start", minHeight: 30, padding: "3px 10px", fontSize: 12.5 }}>
        {open ? "Hide rules" : `Show rules (${mine.length})`}
      </button>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {mine.length === 0 && (
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim)" }}>
              No bootstrap rules — this activity is learned purely from your confirmed labels.
            </p>
          )}
          {mine.map((r) => (
            <div key={r.id} style={{ padding: "8px 12px", background: "var(--surface-2)",
                                     borderRadius: 8, fontSize: 12.5, display: "flex",
                                     alignItems: "center", gap: 8,
                                     opacity: r.enabled ? 1 : 0.55 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <code style={{ overflowWrap: "anywhere" }}>{predicateText(r.predicate)}</code>
                <span style={{ color: "var(--text-dim)", marginLeft: 8 }}>
                  {r.person_id ? `· ${persons[r.person_id] ?? r.person_id}` : "· everyone"}
                  {" "}· priority {r.priority} · {r.origin}
                  {!r.enabled && r.origin === "discovered" && " · drafted from a pattern — review, then enable"}
                </span>
              </div>
              <button className="btn btn-ghost"
                      style={{ minHeight: 26, padding: "2px 10px", fontSize: 12, flexShrink: 0 }}
                      onClick={async () => {
                        await post("/api/rules", { ...r, enabled: !r.enabled });
                        onSaved();
                      }}>
                {r.enabled ? "Disable" : "Enable"}
              </button>
            </div>
          ))}
          {mine.length > 0 && (
            <p style={{ margin: 0, fontSize: 12, color: "var(--text-dim)" }}>
              Rules only create <em>starter</em> labels — your confirmed answers always outrank them.
            </p>
          )}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn btn-primary" disabled={state === "saving"} onClick={save}>
          {state === "saving" ? "Saving…" : "Save"}
        </button>
        {state === "ok" && <span style={{ color: "var(--ok, #34D399)", fontSize: 13 }}>Saved ✓</span>}
        {state === "fail" && <span style={{ color: "var(--danger)", fontSize: 13 }}>Couldn't save</span>}
      </div>
      </div>
      )}
    </div>
  );
}

export default function Activities() {
  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [persons, setPersons] = useState<Record<string, string>>({});
  const [newName, setNewName] = useState("");
  const [regenMsg, setRegenMsg] = useState("");
  const load = () => {
    fetch("/api/activities").then(j).then(setActivities).catch(() => setActivities([]));
    fetch("/api/rules").then(j).then(setRules).catch(() => {});
  };
  useEffect(() => {
    load();
    fetch("/api/persons").then(j)
      .then((ps: { id: string; name: string }[]) =>
        setPersons(Object.fromEntries(ps.map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, []);
  const add = async () => {
    const name = newName.trim();
    if (!name) return;
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    await post("/api/activities", {
      slug, name, phrase: null, icon: "mdi:star", color: "#818CF8",
      parent_id: null, enabled: true, silent: false,
    }).then(j).catch(() => {});
    setNewName("");
    load();
  };
  const regen = async () => {
    if (!window.confirm("Regenerate starter rules from the current sensor bindings? Auto-generated rules are replaced; your confirmed labels are never touched.")) return;
    const r = await post("/api/rules/regenerate", {}).then(j);
    setRegenMsg(`Generated ${r.generated} rules.`);
    load();
  };
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="activities" size={22} />
        <h2 style={{ margin: 0 }}>Activities</h2>
        <button className="btn btn-secondary" style={{ marginLeft: "auto" }} onClick={regen}>
          Regenerate rules
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        What Hearth can say about your home. Each activity has a notification phrase, an optional
        <em> silent</em> flag (sleeping is silent by default — nobody answers “are you asleep?”),
        and the starter rules that bootstrap its training labels.
      </p>
      {regenMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{regenMsg}</p>}
      {activities === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {activities && [...activities]
        .sort((x, y) => Number(x.parent_id !== null) - Number(y.parent_id !== null))
        .map((a) => (
          <div key={a.slug} style={a.parent_id !== null ? { marginLeft: 24 } : undefined}>
            <ActivityCard a={a} rules={rules} persons={persons}
                          parents={activities} onSaved={load} />
          </div>
        ))}
      <div style={{ display: "flex", gap: 10 }}>
        <input placeholder="New activity, e.g. Gaming" value={newName}
               onChange={(e) => setNewName(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && add()} style={{ maxWidth: 260 }} />
        <button className="btn btn-secondary" disabled={!newName.trim()} onClick={add}>
          <Icon name="plus" size={15} /> Add activity
        </button>
      </div>
    </section>
  );
}
