/**
 * Hearth icon set — single source of truth for every icon in the UI.
 * Rules (docs/DESIGN.md §7): 24px grid, 2px stroke, round caps/joins,
 * outline-only, currentColor. Color comes from the PARENT (text color or an
 * activity token) — icons never hardcode color, with one exception: the
 * brand ember in <Logo/>.
 *
 * Usage:  <Icon name="inbox" />  ·  <Icon name="cooking" size={20} />
 */
import type { JSX } from "react";

export type IconName =
  // navigation
  | "dashboard" | "inbox" | "activities" | "patterns" | "models" | "sensors" | "settings"
  // actions
  | "plus" | "x" | "check" | "edit" | "trash" | "download" | "upload" | "refresh"
  | "search" | "filter" | "copy" | "external" | "more" | "play" | "rollback" | "promote"
  | "chevron-down" | "chevron-right" | "logout"
  // status & meta
  | "warning" | "info" | "question" | "lock" | "key" | "user" | "household"
  | "bell" | "bell-off" | "sun" | "moon" | "monitor" | "eye" | "drift"
  // domain roles
  | "presence" | "power" | "light" | "env" | "door" | "alarm" | "focus"
  // activities
  | "sleeping" | "away" | "home" | "cooking" | "eating" | "movie" | "working";

const I: Record<IconName, JSX.Element> = {
  dashboard: (<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>),
  inbox: (<><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 13h4l2 3h4l2-3h4"/></>),
  activities: (<><rect x="4" y="4" width="6" height="6" rx="1"/><circle cx="17" cy="7" r="3"/><path d="M7 14l3 6H4z"/></>),
  patterns: (<><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><circle cx="19" cy="19" r="1.6"/></>),
  models: (<><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/></>),
  sensors: (<><circle cx="12" cy="18" r="1.8"/><path d="M8.5 14.5a5 5 0 0 1 7 0M5.6 11.6a9 9 0 0 1 12.8 0"/></>),
  settings: (<><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/></>),
  plus: <path d="M12 5v14M5 12h14"/>,
  x: <path d="M6 6l12 12M18 6L6 18"/>,
  check: <path d="M5 13l4 4L19 7"/>,
  edit: <path d="M14 5l5 5M4 20l1.3-4.6L15.5 5.1l3.4 3.4L8.6 18.7z"/>,
  trash: <path d="M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13"/>,
  download: <path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>,
  upload: <path d="M12 20V9M7 13l5-5 5 5M5 4h14"/>,
  refresh: <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/>,
  search: (<><circle cx="11" cy="11" r="6"/><path d="M16 16l5 5"/></>),
  filter: <path d="M4 5h16l-6 7v6l-4 2v-8z"/>,
  copy: (<><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V7a2 2 0 0 1 2-2h8"/></>),
  external: <path d="M14 5h5v5M19 5l-9 9M10 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"/>,
  more: (<><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></>),
  play: <path d="M8 5v14l11-7z"/>,
  rollback: <path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 0 10h-4"/>,
  promote: <path d="M12 19V5M5 12l7-7 7 7"/>,
  "chevron-down": <path d="M6 9l6 6 6-6"/>,
  "chevron-right": <path d="M9 6l6 6-6 6"/>,
  logout: <path d="M9 12h11M16 8l4 4-4 4M14 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8"/>,
  warning: <path d="M12 4L2 20h20zM12 10v4M12 17v.01"/>,
  info: (<><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/></>),
  question: (<><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1.9-1.1 1.8M12 17v.01"/></>),
  lock: (<><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></>),
  key: (<><circle cx="8" cy="15" r="4.5"/><path d="M11.5 11.5L20 3M16 7l2.5 2.5"/></>),
  user: (<><circle cx="12" cy="8" r="4"/><path d="M5 20a7 7 0 0 1 14 0"/></>),
  household: (<><path d="M12 3l9 7.5V19a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19v-8.5z"/><circle cx="9.5" cy="15" r="1.4"/><circle cx="14.5" cy="15" r="1.4"/></>),
  bell: <path d="M18 17H6l-1 2h14zM6 17v-6a6 6 0 0 1 12 0v6M10.5 21a1.5 1.5 0 0 0 3 0"/>,
  "bell-off": <path d="M18 17H8M6 17v-6a6 6 0 0 1 9-5.2M10.5 21a1.5 1.5 0 0 0 3 0M4 4l16 16"/>,
  sun: (<><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></>),
  moon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>,
  monitor: (<><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M9 21h6M12 17v4"/></>),
  eye: (<><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></>),
  drift: <path d="M3 17l6-6 4 4 8-8M16 7h5v5"/>,
  presence: (<><circle cx="12" cy="12" r="1.8"/><path d="M6 6a8.5 8.5 0 0 0 0 12M18 6a8.5 8.5 0 0 1 0 12"/></>),
  power: <path d="M13 2L5 13h6l-1 9 8-11h-6z"/>,
  light: <path d="M12 3a6 6 0 0 1 3.5 10.9c-.6.5-1 1.2-1 2.1h-5c0-.9-.4-1.6-1-2.1A6 6 0 0 1 12 3zM9.5 19h5M10.5 21.5h3"/>,
  env: <path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/>,
  door: (<><rect x="6" y="3" width="12" height="18" rx="1"/><circle cx="15" cy="12" r="1"/></>),
  alarm: (<><circle cx="12" cy="13" r="7"/><path d="M12 9v4l3 2M5 4L3 6M19 4l2 2"/></>),
  focus: (<><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r=".5"/></>),
  sleeping: <path d="M3 7v11M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5M5.5 11.5a1.8 1.8 0 1 0 3 0 1.8 1.8 0 0 0-3 0"/>,
  away: (<><path d="M5 4h9v16H5z"/><path d="M14 12h7M18 9l3 3-3 3"/></>),
  home: <path d="M12 3l9 7.5V19a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19v-8.5zM12 16v.01"/>,
  cooking: <path d="M3 15a6.5 6.5 0 0 0 13 0zM16 15h5M8 11V9M11 11V8"/>,
  eating: <path d="M8 4v16M5 4v4a3 3 0 0 0 6 0V4M17 4v16M17 4a4 6.5 0 0 1 0 9"/>,
  movie: (<><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.5v5l4.5-2.5z"/></>),
  working: (<><rect x="5" y="5" width="14" height="9" rx="1.5"/><path d="M2 18h20"/></>),
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {I[name]}
    </svg>
  );
}

export const ICON_NAMES = Object.keys(I) as IconName[];
