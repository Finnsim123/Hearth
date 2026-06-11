import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
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

/** True when the viewport is at or below `maxWidth` (phone / narrow tablet). */
export function useIsMobile(maxWidth = 720): boolean {
  return useMediaQuery(`(max-width: ${maxWidth}px)`);
}

/** True when the user asked the OS to minimise motion — disable the dots. */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
