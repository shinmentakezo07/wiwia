// MarqueeContainer — dual-track infinite scroll marquee. Pauses on hover.
// Uses the wiwi-marquee keyframe defined in styles.css and CSS mask for fade
// edges. No framer-motion required.

import type { ReactNode } from "react";

interface MarqueeContainerProps {
  children: ReactNode;
  reverse?: boolean;
  className?: string;
}

export function MarqueeContainer({ children, reverse = false, className }: MarqueeContainerProps) {
  const trackClass = `flex shrink-0 gap-6 animate-marquee ${
    reverse ? "[animation-direction:reverse]" : ""
  } group-hover:[animation-play-state:paused]`;

  return (
    <div
      className={`group flex gap-6 overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_10%,black_90%,transparent)] ${className ?? ""}`}
    >
      <div
        className={trackClass}
        style={{ ["--duration" as string]: "40s", ["--gap" as string]: "24px" } as React.CSSProperties}
      >
        {children}
      </div>
      <div
        className={trackClass}
        aria-hidden="true"
        style={{ ["--duration" as string]: "40s", ["--gap" as string]: "24px" } as React.CSSProperties}
      >
        {children}
      </div>
    </div>
  );
}
