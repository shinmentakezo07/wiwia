---
name: ui-design-standards
description: Binding UI/UX and design standards for this repo — mandatory before any change to web/, an admin page, a marketing page, a playground, docs styling, an error page, or any HTML/CSS/JSX/TSX. Covers mobile+desktop compatibility, accessibility, library discipline, and anti-generic design.
---

# UI/UX standards (binding)

## Universal compatibility rule

Every UI or UX change — in `web/` or in any admin-facing HTML/template surface — MUST be verified as usable on both **desktop (mouse/keyboard)** and **mobile (touch, narrow viewport)** before being marked done. Specifically:

1. **Layout must not break below ~375 px viewport width.** Test at 375×812 (iPhone SE class) and at a wide desktop viewport. Sidebars, tables, and cards that assume a minimum width must reflow, stack, or collapse — not clip or overflow.
2. **All interactive controls must be operable by touch.** Tap targets must be at least 44×44 px (Apple HIG) or 48×48 dp (Material). Controls that only respond to hover (CSS `:hover`-only reveals, hover-dependent dropdowns) must also respond to focus and touch. No information or action may be hover-only.
3. **Keyboard and focus navigation must work.** Every focusable element must be reachable via Tab/Shift-Tab in a sensible order. Focus styles must be visible (do not suppress `outline` without providing an equivalent). Modal/dialog focus trapping and Escape-to-close must work on desktop.
4. **Text must be legible and not rely on fixed sizes.** Use relative units (`rem`, `em`, `%`, `vw`) over fixed `px` for typography and spacing where appropriate. Text in containers must not truncate silently in a way that hides information on narrow screens.
5. **Responsive is not optional for admin pages.** Admin pages in `web/src/pages/` are used on desktop but may be opened on a phone (e.g., a quick key rotation or budget check). Assume a mobile viewport is possible for every page; do not gate responsiveness behind a "this is an admin page" assumption.
6. **Verify before claiming done.** For any UI/UX change, open the page in a mobile-sized viewport (browser devtools device mode or a real device) and a desktop viewport, and confirm the change works in both. Screenshots or a short description of the verification belong in the commit message or PR description.

## UI design standards (binding for ALL UI/design work, in any codebase)

**Scope: every UI/UX/design task, in this repo or any other** — admin panels, marketing sites, apps, playgrounds, docs, error pages, emails, any HTML/CSS/JSX/TSX anywhere. Nothing UI-related is exempt.

Senior-frontend-architect standard. When a task touches anything visual, apply these rules in addition to the compatibility rule above:

**Mode of operation**

1. **Execute first.** Build the requested UI immediately; no philosophical lectures, no unsolicited advice, no wandering. Code and visual solutions over prose.
2. **Reason deeply before writing.** For non-trivial UI work, think through the design along four axes — psychological (user sentiment, cognitive load), technical (rendering performance, repaint/reflow cost, state complexity), accessibility (aim WCAG AA at minimum, AAA where feasible), and scalability (maintenance, modularity). If the reasoning feels easy, it is surface-level: dig until the logic is irrefutable. Surface the reasoning chain briefly in the report when it shaped the design.
3. **Cover edge cases.** Empty states, loading states, overflow, long strings, zero data, reduced motion — name them and handle them.

**Design philosophy — intentional minimalism**

4. **Anti-generic.** Reject bootstrapped/template-looking layouts — on every surface. If a page looks like a stock template, it is wrong. Strive for bespoke composition: asymmetry where it carries meaning, distinctive but restrained typography, purposeful whitespace.
5. **Every element must earn its place.** Before adding any element, state its purpose; if it has none, delete it. Reduction is the ultimate sophistication — remove until nothing left can be removed without loss.
6. **No neon/glow/generic "AI dashboard" flourishes by default — anywhere.** Effects stay within the design system of the surface being edited. Stronger effects only when the user explicitly asks for them.

**Coding standards**

7. **Library discipline (critical).** If the project uses a UI library or component kit (Shadcn, Radix, MUI, or the project's own shared components), you MUST use it. Before building any primitive (modal, dropdown, button, table, badge, tooltip, chart), search the codebase for an existing one; never rebuild what already exists — wrap or style the shared/library primitive instead, to keep stability and accessibility. Do not add redundant one-off CSS when a token, utility class, or library component exists.
8. **Follow the design system of the surface you are editing.** Discover the surrounding pages' tokens, spacing, typography, and component vocabulary first, and match them — never invent a parallel look or import one surface's style into another.
9. **Micro-interactions and invisible UX.** Where motion is used, keep it subtle, fast (150–600 ms), and gated on the user's reduced-motion preference.
10. **Semantic HTML5.** Landmarks, headings in order, `<button>` for actions, `<a>` for navigation — matching the markup conventions of neighboring pages.

**Never ship generic output**

11. If a produced UI — any surface — looks interchangeable with any template, iterate before presenting it. "Fine" is not the bar; coherent, intentional, and specific to that product's design language is the bar — unless the user explicitly asks for a plain/generic treatment.
