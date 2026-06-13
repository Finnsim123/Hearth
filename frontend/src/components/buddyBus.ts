/**
 * A tiny client-side bus so user actions can make the buddy react *immediately*
 * — instead of waiting for the next /api/buddy poll. A page calls cheerBuddy()
 * after a successful action (answering a question, approving sensors, naming a
 * pattern…); the Buddy shows a short, warm acknowledgement and then re-polls so
 * the underlying state (open questions, etc.) is fresh by the time it fades.
 */
export type BuddyCheer = { title: string; detail?: string };

type Listener = (cheer: BuddyCheer) => void;
const listeners = new Set<Listener>();

export function cheerBuddy(cheer: BuddyCheer): void {
  listeners.forEach((l) => l(cheer));
}

export function onBuddyCheer(l: Listener): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}
