---
version: alpha
name: "TraceRAG Evidence Studio"
description: "A forensic investigation console with a daylight case-file reading surface for source-grounded causal code analysis."
colors:
  primary: "#156f6c"
  primary-bright: "#83e1d6"
  background: "#f1f4f2"
  surface: "#ffffff"
  dark-shell: "#0b1823"
  dark-field: "#0d1c29"
  text: "#163039"
  muted: "#4c646d"
  border: "#d6dfdf"
  caution: "#a5682b"
  danger: "#a84235"
  focus: "#5ce0d3"
typography:
  display:
    fontFamily: "Bahnschrift, Arial Narrow, Segoe UI, sans-serif"
  body:
    fontFamily: "Aptos, Segoe UI, system-ui, sans-serif"
  data:
    fontFamily: "Consolas, Liberation Mono, monospace"
rounded:
  DEFAULT: "9px"
  card: "16px"
  control: "9px"
  chip: "5px"
spacing:
  sidebar-width: "390px"
  topbar-height: "76px"
  reading-max: "1120px"
components:
  topbar: {}
  investigation-rail: {}
  button: {}
  evidence-card: {}
  citation-pill: {}
  status-panel: {}
---

# TraceRAG Evidence Studio

## Overview

**Creative North Star:** a forensic analyst's split desk: a dark instrument console controls the investigation; a daylight-white case file holds claims and evidence. The visual signature is the *proof chain*: a restrained vertical line joining numbered, source-backed claims to clickable citation specimens. This is an application used by developers who must distinguish source facts from hypotheses, not a marketing page.

The established brand is TraceRAG with teal source emphasis. This redesign makes the reading surface much lighter and the type much more legible than the previous uniformly dark UI without replacing underlying behaviors or API contracts. English UI. No assumptions about user location, regulatory jurisdiction, or locale from appearance.

**Anti-references:** marketing gradient hero dashboards, tiny console-only typography, decorative "AI magic" motifs, fake analytics, and labels that imply runtime proof.

**Token ownership:** this file documents normative taste and exact semantic hex values. `frontend/src/styles.css` `:root` CSS variables are the sole runtime implementation; downstream components reference those variables. No Tailwind or other duplicate theme mapping.

## Colors

The dark rail uses `dark-shell` and `dark-field` for setup actions, while the high-reading-time canvas uses `background`, `surface`, and `text`. Teal is *source-backed* and interactive, while amber marks uncertainty or warnings. Never use green to imply a causal conclusion is proven. Preserve contrast for labels, inputs, code snippets, and focus rings.

## Typography

**Display** uses Bahnschrift / Arial Narrow in short headings for an investigative editorial character. **Body** uses Aptos / Segoe UI / system sans at 12–15px for legibility. **Data** uses Consolas with tabular numbers and short caps for genuine identifiers, counts, and code. Long source, error, and answer text wraps or scrolls rather than clips; never force evidence into a single line except in the code scroller.

## Layout

At desktop widths the sidebar is a 390px dark rail and the document surface uses remaining width with a 1120px reading maximum. At narrow widths the rail stacks above the content. The whole document remains scrollable; neither tab switching nor the source code viewer can trap form controls. The process rail uses actual stages (retrieve, reason, cite) rather than decorative numbers. Keep visual geometry stable while loading. Prefer native selects because an OS-controlled popup is acceptable here and retains keyboard support.

## Elevation & Depth

Use tonal separation and thin boundaries. Only the empty case-file and result summary receive quiet shadows. Avoid floating/shadowed stacks and glassmorphism on evidence text.

## Shapes

Small rounded controls are functional; paper cards are larger (roughly 11–16px). Citation chips are square-ish specimen tags, not decorative pills. The source chain's circle markers identify ordered claims, not rank or probability.

## Components

### Foundational visual states

All clickable controls have visible hover, focus-visible and disabled states. Busy operations maintain button width and provide live feedback. Results and errors are inline; errors avoid private contents. The global scrollbar baseline stays visible. Reduced motion disables transitions and smooth scroll.

### Buttons and actions

The teal solid primary action is only **Analyze question**. Connect is a secondary action. Privacy approval is a separate checkbox with consequence text; it is never silently checked. Token is masked by default, optionally revealed without persistence. The existing API and browser-memory-only token behavior remain unchanged.

### Navigation and data display

Source citations `S*`, revision-bound graph `G*`, historical `H*` and runtime `R*` preserve their server-provided identities and trust categories. The proof-chain layout belongs to claims only. Answer, evidence, and hypotheses remain distinct keyboard-operable tabs; citing an item navigates into Evidence and focuses the item. No fabricated source lines or synthetic scores.

### Forms and overlays

Use native labels, selects and checkboxes. Question editor does not permit uncontrolled resize, preserving the rail's layout. Display UTF-8 byte count. No browser `alert`, custom unsafe HTML, exposed credentials, or arbitrary repository paths.

### Iconography

Typographic specimen IDs, small arrows and a hand-built proof-chain motif, not a generic icon pack. Icons never replace action labels.

### Motion

Only hover and focus states transition (under 180ms). No idle animations. Honor `prefers-reduced-motion`.

### Content and data visualization

Tone is precise: "evidence-backed claims", "unverified", and "not runtime proof". Empty state offers real question shortcuts that fill the existing question field. Avoid promises of guaranteed causal truth.

## Do's and Don'ts

- **Do:** show server-provided evidence and limitations alongside model summaries.
- **Do:** keep the split instrument-to-paper layout at desktop and a single reachable document flow on mobile.
- **Don't:** add decorative numbers, invented metrics, or false source-line locations.
- **Don't:** brighten the rail so much that privacy controls blend into evidence; trust boundaries must remain visually distinct.
