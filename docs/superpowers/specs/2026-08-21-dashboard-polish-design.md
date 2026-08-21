# Dashboard polish pass — design spec

**Date:** 2026-08-21
**Scope:** `web/src/pages/Dashboard.tsx` (+ small shared additions in `web/src/components/ui.tsx`, `web/src/styles.css`)
**Out of scope:** backend changes, other admin pages, light theme, layout restructure

## Goal

The wiwi admin Dashboard works but reads flat: at zero traffic it is a wall of
zeros and empty chart grids, chart fills are saturated blocks, two series colors
sit outside the validated dark-mode lightness band, and tiles carry no trend
context. This spec upgrades the page in place — same layout, same data sources,
client-side only.

## 1. Palette (validated)

Keep wiwi's hues; snap to legal steps for the `#0a0a0a` card surface. Validated
with the dataviz six-checks script (`validate_palette.js --mode dark --surface
"#0a0a0a"`): worst adjacent CVD ΔE 16.6 (tritan), normal-vision floor 19.8, all
slots inside the dark lightness band L 0.48–0.67, chroma ≥ 0.1, contrast ≥ 3:1.
All checks pass with no warnings.

| Series | Old | New | Why |
|---|---|---|---|
| tokens in | `#3b82f6` | `#3b82f6` | already legal |
| out | `#fbbf24` | `#c98500` | old L=0.84 above dark band |
| cached | `#34d399` | `#199e70` | old L=0.77 above dark band |
| reasoning | `#a855f7` | `#a855f7` | already legal |
| requests | `#3b82f6` | `#3b82f6` | unchanged |
| errors | `#f87171` | `#e66767` | old L=0.71 slightly above band |

Stack order changes from in→cached→reasoning→out to **in→out→cached→reasoning**
so no weak adjacent pair touches (violet↔aqua tritan ΔE was 4.7 when adjacent).

Legend and tooltip rows follow the new stack order.

## 2. Stat tiles

Featured tiles (req/min, spend, error rate, p95 ttft) each gain:

- **12-point hourly sparkline** of that tile's metric over the last hour,
  stroke in de-emphasis gray with the current period accented in the tile tone.
  Client-side from existing `/admin/stats/timeseries` + `/admin/logs/requests`
  polls — no new endpoints.
- **Delta chip** vs previous hour: signed percent colored by direction ×
  goodness (spend up = red, errors up = red, req/min up = green). Previous
  window empty → muted "—" chip, never ∞%.
- **Zero-traffic empty state**: when the window holds zero requests, featured
  tiles show a subtle pulsing "waiting for first request" state instead of flat
  zeros.

Sparkline + delta live in an extended `StatCard` (new optional props) so other
pages can adopt them later without touching this page again.

## 3. Charts

Tokens/min stacked area:

- New stack order and palette per §1.
- Fills drop to ~10–12% washes (dataviz mark spec), keeping a faint vertical
  gradient for depth (10% → 2%).
- Strokes 2px round join/cap.
- Gridlines become solid hairlines one step off surface (replace dashed).
- Soft glow on newest bucket end-dot only; no per-point dots.

Requests & errors/min line chart:

- Same grid treatment; 2px lines; errors keep status-red semantics.
- No dots except the active hover dot.

Tooltips (both charts): value leads in strong ink, series name secondary,
short line-key strokes instead of swatch boxes; crosshair retained.

Refetch behavior: hold previous render at reduced opacity on the 10s poll —
no skeleton flash, no layout jump.

## 4. Live sparkline card

Keep the custom SVG implementation. Restyle strokes to the new request/error
colors, solid hairline baseline, legend keys updated. SSE pulse-dot behavior
unchanged.

## Testing

- `cd web && bun run build` passes (tsc + vite).
- Manual check against a running gateway: zero traffic → pulse placeholders;
  seeded traffic (bench.py or curl loop) populates tiles/deltas/sparklines/
  charts with the new palette.
- Visual eyeball pass per dataviz step 7: label collisions, tooltip order,
  stack segment legibility.

## Non-goals / future

- Backend `prev_window` rollup for server-side deltas.
- Light-theme token set (admin UI is dark-only today).
- Rolling the extended StatCard out to Usage/Analytics pages.
