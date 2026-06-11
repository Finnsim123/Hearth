import { useEffect, useState } from "react";

/** True when the viewport is at or below `maxWidth` (phone / narrow tablet).
 *  Drives the responsive shell and per-page mobile layouts. */
export function useIsMobile(maxWidth = 720): boolean {
  const query = `(max-width: ${maxWidth}px)`;
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}
