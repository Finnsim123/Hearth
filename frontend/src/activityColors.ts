/**
 * Activity colour system — one source of truth for the whole UI.
 * Every activity's colour is user-set on the Activities page and stored on the
 * backend (Activity.color). This hook fetches the taxonomy once (shared via the
 * react-query ["activities"] cache — no extra requests) and hands back a
 * colorOf(slug) that every page uses so an activity looks identical everywhere.
 *
 * Keep PALETTE in sync with backend domain/labeling/palette.py.
 */
import { useQuery } from "@tanstack/react-query";

export const PALETTE = [
  "#34d399", "#818cf8", "#f59e0b", "#f472b6", "#60a5fa", "#fb923c", "#22d3ee",
  "#a78bfa", "#2dd4bf", "#facc15", "#fb7185", "#c084fc", "#4ade80", "#94a3b8",
];

// "unknown" is intentionally neutral (unclassified time), not a hue.
const SPECIAL: Record<string, string> = { unknown: "var(--surface-2)" };
// Fact slugs that don't have their own taxonomy row borrow a sibling's colour.
const ALIAS: Record<string, string> = { asleep: "sleeping", media: "movie" };

type Act = { slug: string; name: string; color?: string; enabled?: boolean };

/** Stable palette slot for a slug with no stored colour — deterministic so it
 *  never flickers between renders. */
const hashColor = (slug: string) =>
  PALETTE[[...slug].reduce((n, c) => n + c.charCodeAt(0), 0) % PALETTE.length];

export function activityColorOf(activities: Act[]) {
  const m = new Map(activities.map((a) => [a.slug, a.color]));
  return (slug: string): string => {
    if (SPECIAL[slug]) return SPECIAL[slug];
    return m.get(slug) || (ALIAS[slug] && m.get(ALIAS[slug])) || hashColor(slug);
  };
}

export function activityNameOf(activities: Act[]) {
  const m = new Map(activities.map((a) => [a.slug, a.name]));
  return (slug: string) => m.get(slug) ?? slug.replace(/_/g, " ");
}

/** Shared hook: activities + colorOf/nameOf. Uses the same query key as the
 *  rest of the app, so it's a cache hit wherever activities are already loaded. */
export function useActivityColors() {
  const q = useQuery<Act[]>({
    queryKey: ["activities"],
    queryFn: () => fetch("/api/activities").then((r) => r.json()),
    staleTime: 60_000,
  });
  const activities = q.data ?? [];
  return { activities, colorOf: activityColorOf(activities), nameOf: activityNameOf(activities) };
}
