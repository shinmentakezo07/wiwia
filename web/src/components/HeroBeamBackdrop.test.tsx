import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { useReducedMotion } from "@/components/animated";
import { HERO_BEAMS_FULL, HeroBeamBackdrop } from "./HeroBeamBackdrop";

vi.mock("@/components/animated", () => ({
  useReducedMotion: vi.fn(() => false),
}));

function renderBeam(reducedMotion = false) {
  vi.mocked(useReducedMotion).mockReturnValue(reducedMotion);
  return renderToStaticMarkup(<HeroBeamBackdrop beams={[HERO_BEAMS_FULL[0]]} />);
}

describe("HeroBeamBackdrop", () => {
  it("moves the complete gradient without collapsing its stops", () => {
    const html = renderBeam();
    const animations = [...html.matchAll(/<animateTransform [^>]+>/g)].map((match) => match[0]);
    expect(animations).toHaveLength(1);
    expect(animations[0]).toContain('attributeName="gradientTransform"');
    expect(animations[0]).toContain('type="translate"');
    expect(animations[0]).toContain('values="0 0;1680 0"');
    expect(html).not.toContain('attributeName="offset"');
    expect(html).toContain('x1="0"');
    expect(html).toContain('y1="0"');
    expect(html).toContain('x2="1200"');
    expect(html).toContain('y2="0"');
  });

  it("keeps the fourth and fifth beams brighter throughout their loop", () => {
    const html = renderToStaticMarkup(<HeroBeamBackdrop />);
    expect(html).toContain('stroke-width="2"');
    expect(HERO_BEAMS_FULL[3]!.w).toBe(2);
    expect(HERO_BEAMS_FULL[4]!.w).toBe(2);
  });

  it("keeps a static gradient and has no animated stops for reduced motion", () => {
    const html = renderBeam(true);
    expect(html).not.toContain("<animate");
    expect(html).toContain('stroke-dasharray="110 2000"');
  });
});
