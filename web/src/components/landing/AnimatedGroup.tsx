// AnimatedGroup — CSS-only wrapper replacing the framer-motion based
// animated-group. Instead of staggered spring animations, uses a CSS-based
// staggered fade+slide-up entrance via inline animation-delay on children.
// Children stay regular elements (no motion.div wrappers needed).

import { Children, type ReactNode } from "react";

type PresetType =
  | "fade"
  | "slide"
  | "scale"
  | "blur"
  | "blur-slide"
  | "zoom"
  | "flip"
  | "bounce"
  | "rotate"
  | "swing";

interface AnimatedGroupProps {
  children: ReactNode;
  className?: string;
  preset?: PresetType;
}

const PRESET_CLASS: Record<PresetType, string> = {
  fade: "animate-[wiwi-ag-fade_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  slide: "animate-[wiwi-ag-slide_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  scale: "animate-[wiwi-ag-scale_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  blur: "animate-[wiwi-ag-blur_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  "blur-slide": "animate-[wiwi-ag-blur-slide_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  zoom: "animate-[wiwi-ag-scale_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  flip: "animate-[wiwi-ag-slide_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  bounce: "animate-[wiwi-ag-slide_0.6s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  rotate: "animate-[wiwi-ag-slide_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
  swing: "animate-[wiwi-ag-slide_0.5s_cubic-bezier(0.25,0.4,0.25,1)_both]",
};

export function AnimatedGroup({ children, className, preset = "fade" }: AnimatedGroupProps) {
  const animClass = PRESET_CLASS[preset];
  return (
    <div className={className}>
      {Children.map(children, (child, index) => (
        <div
          className={animClass}
          style={{ animationDelay: `${index * 0.1}s` }}
        >
          {child}
        </div>
      ))}
    </div>
  );
}
