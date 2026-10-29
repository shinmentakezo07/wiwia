// Canonical provider badges from `web/public/logos/` — the same tiles the
// README and the docs page embed. One entry per type in
// `wiwi/config.py:PROVIDER_TYPES`; anything missing falls back to the
// caller's own glyph (a letter tile, a server icon, …).
//
// These SVGs are self-contained 120×120 tiles: each carries its own rounded
// background and brand colours, so a caller only has to size the box.

export const PROVIDER_LOGO: Record<string, string> = {
  openai: "/logos/openai.svg",
  anthropic: "/logos/anthropic.svg",
  gemini: "/logos/gemini.svg",
  openrouter: "/logos/openrouter.svg",
  "openai-compatible": "/logos/openai-compatible.svg",
  gmicloud: "/logos/gmicloud.svg",
  bai: "/logos/bai.svg",
  cline: "/logos/cline.svg",
  workbuddy: "/logos/workbuddy.svg",
  "nvidia-nim": "/logos/nvidia-nim.svg",
  opencode: "/logos/opencode.svg",
  hermes: "/logos/hermes.svg",
};

/** Badge URL for a provider type, or null when the type has no badge. */
export function providerLogo(type: string | null | undefined): string | null {
  return type ? (PROVIDER_LOGO[type] ?? null) : null;
}
