// Stable per-provider hue, shared by every console surface that names a
// provider. A provider account keeps the same colour in the Models ledger, in
// the Combos composition editor, and anywhere else that draws its identity —
// so the colour is a recognition cue rather than decoration.

// Twelve entries so two providers rarely collide, and every one of them holds
// contrast on the near-black admin surface.
const HUES = [
  "#7c9cff",
  "#c084fc",
  "#4ade9b",
  "#fbbf24",
  "#f472b6",
  "#22d3ee",
  "#fb923c",
  "#a3e635",
  "#e879f9",
  "#2dd4bf",
  "#94a3b8",
  "#facc15",
];

/** Stable per-provider hue, so an account looks the same on every page. */
export function providerHue(provider: string): string {
  let h = 0;
  for (let i = 0; i < provider.length; i++) h = (h * 31 + provider.charCodeAt(i)) >>> 0;
  return HUES[h % HUES.length];
}
