# Hearth — Design Language

One visual language across every user touchpoint: web UI, onboarding wizard,
docs site, README badges, HA integration branding, notification copy.
Reference aesthetic: budgero.app — calm, private, minimal SaaS. Hearth keeps
that restraint and swaps in its own identity: **warm ember on cool slate**
(a hearth: quiet, warm, always on).

## 1. Principles

1. **Calm by default.** A home-activity app must never feel like a trading
   terminal. One accent color, lots of air, no gradients-for-decoration, no
   glassmorphism. Information density comes from layout, not noise.
2. **Glass-box, not dashboard-soup.** Every number can explain itself
   (hover/tap → "how is this computed?"). Charts are monochrome slate with
   ember highlights — the accent always *means* something (current, selected,
   anomalous), never decoration.
3. **Quietly warm.** Microcopy is plain and human ("Was this right?" not
   "Validate inference output"). The ember accent carries the warmth; the rest
   stays neutral.
4. **Dark-first, light-complete.** Homelab screens live on TVs and dim desks;
   dark is the default theme, light is a first-class twin (token-swapped, no
   redesign).
5. **Touch-friendly.** The Inbox is used from phones; primary actions are
   44px+ targets, one-hand reachable.

## 2. Tokens (source of truth: `frontend/src/theme.css`)

### Color — dark (default)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0F1115` | app background |
| `--surface` | `#161A21` | cards, nav |
| `--surface-2` | `#1E242E` | nested surfaces, hover |
| `--border` | `#2A313D` | 1px hairlines everywhere, no shadows in dark |
| `--text` | `#E7EAF0` | primary text |
| `--text-dim` | `#9AA3B2` | secondary text, labels |
| `--accent` | `#F59E0B` | ember — actions, current activity, focus ring |
| `--accent-strong` | `#D97706` | hover/active states |
| `--ok` | `#34D399` | success, healthy, confirmed |
| `--warn` | `#FBBF24` | degraded, low confidence |
| `--danger` | `#F87171` | errors, drift alerts |

### Color — light

`--bg #F8FAFC · --surface #FFFFFF · --surface-2 #F1F5F9 · --border #E2E8F0 ·
--text #111827 · --text-dim #64748B` — accent/status colors unchanged
(ember reads beautifully on both). Cards get `--shadow-sm` in light mode only.

### Activity palette (categorical, color-blind-safe, consistent everywhere)

sleeping `#818CF8` · away `#94A3B8` · home `#34D399` · cooking `#F59E0B` ·
eating `#FB923C` · media `#F472B6` · working `#60A5FA` · custom slots cycle
through the same hue family. The same activity is ALWAYS the same color —
timeline, probabilities, confusion matrix, HA cards.

### Type

- Family: **Inter** (variable, self-hosted — no CDN, privacy), `tabular-nums`
  for all metrics.
- Scale (1.250): 12 label · 14 body-sm · 16 body · 20 h3 · 25 h2 · 31 h1.
- Weights: 400 body, 500 UI labels/buttons, 600 headings. Never 700+.
- Letter-spacing: −0.01em headings; +0.06em uppercase 12px section labels.

### Geometry & depth

- Spacing: 4px grid — 4/8/12/16/24/32/48/64.
- Radius: 8px controls · 12px cards · 16px modals · full for pills/avatars.
- Borders over shadows: dark mode uses 1px `--border` only; light mode may add
  `--shadow-sm: 0 1px 2px rgb(15 23 42 / 0.06)`.
- Max content width 1200px; wizard column 560px.

### Motion

150ms ease-out for hover/focus; 250ms for panels; spring only on the Inbox
card swipe. `prefers-reduced-motion` honored everywhere. Nothing pulses or
auto-animates except the live-prediction dot (2s gentle glow).

## 3. Components (rules, not pixel specs)

- **Card**: surface + border + 16/24 padding + 12 radius. Title row =
  h3 + optional 12px uppercase label + right-aligned action.
- **Buttons**: primary = ember fill, dark text `#1A1206`; secondary = surface-2
  + border; ghost = text-dim → text on hover. One primary per view.
- **Forms**: labels above fields, 14px; inputs surface-2, border, 8px radius,
  ember focus ring (2px outside). Inline validation, never toasts for errors.
- **Stat/metric**: 25px tabular number + 12px label underneath; delta chips in
  ok/danger at 12px.
- **Charts**: slate gridlines (`--border`), text-dim axes, series in activity
  palette or ember; no 3D, no legends when direct labeling fits.
- **Confidence**: always a horizontal micro-bar (not a gauge) — width = value,
  ember ≥ threshold, warn below.
- **Empty states**: one sentence + one action, optional 24px line-icon. The
  wizard and Patterns page lean on these heavily.
- **Activity chip**: pill, activity color at 15% fill + solid color text/dot.

## 4. Voice

Sentence case everywhere (including buttons: "Train now"). Plain verbs, no
jargon to users ("Hearth wasn't sure" not "low softmax confidence"). Numbers
honest: confidence intervals shown as "82% ± 6".

## 5. Logo & brand

**Official mark: Ember** (`brand/logo.svg`) — house outline in
`currentColor`, ember dot fixed `#F59E0B`. Rejected explorations kept in
`brand/explorations/`. Wordmark: "hearth" lowercase, Inter 600, −0.02em,
`--text`, mark to the left. Favicon (`frontend/public/favicon.svg`) adapts
its outline to the OS scheme; the ember never changes. Clear space = ½ mark
height; minimum 16px (mark only, no wordmark). Full rules: `brand/README.md`.

## 6. Iconography

One set, one source: `frontend/src/icons.tsx` (`<Icon name="…"/>`). Rules:

- 24px grid, 2px stroke, round caps/joins, **outline only** — visually one
  family with the Ember mark (same stroke language).
- Icons inherit `currentColor` from their parent; never hardcode color. The
  ember `#F59E0B` appears in exactly one icon-ish place: the logo.
- Sizes: 16px table rows · 18px default · 20px nav/buttons · 24px max
  (empty states). Never below 16px.
- Activity icons (sleeping, away, home, cooking, eating, movie, working)
  are tinted by the activity palette via the parent chip — the icon file
  stays color-agnostic.
- Adding an icon = add it to `icons.tsx` and this family's rules apply; no
  ad-hoc SVGs in pages, no third-party icon fonts.

## 7. Theming model

Three-way, user-facing everywhere a theme makes sense (web UI now; docs site
later): **System (default) · Light · Dark.** Implementation: dark tokens are
the CSS base; light overrides apply via `prefers-color-scheme` when no
`data-theme` attribute is set (= follow system), and `data-theme="light|dark"`
pins a mode (persisted in `localStorage`, switcher in the nav, also exposed in
Settings → Appearance). `color-scheme` is set per mode so native controls,
scrollbars and form widgets match. The activity palette, ember accent and
status colors are identical in both modes — only neutrals swap.
