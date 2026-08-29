// AnimatedBeam — adapted from llmgateway's animated-beam.tsx. Pure SVG +
// CSS animation (no framer-motion dependency). Renders a curved path between
// two DOM elements with an animated gradient sweep traveling along the path.

import { useEffect, useId, useRef, useState } from "react";

export interface BeamProps {
  containerRef: React.RefObject<HTMLDivElement | null>;
  fromRef: React.RefObject<HTMLDivElement | null>;
  toRef: React.RefObject<HTMLDivElement | null>;
  curvature?: number;
  duration?: number; // seconds
  delay?: number; // seconds
  pathColor?: string;
  pathWidth?: number;
  pathOpacity?: number;
  gradientStart?: string;
  gradientStop?: string;
}

export function AnimatedBeam({
  containerRef,
  fromRef,
  toRef,
  curvature = 0,
  duration = 5,
  delay = 0,
  pathColor = "rgba(255,255,255,0.15)",
  pathWidth = 2,
  pathOpacity = 0.2,
  gradientStart = "#ffaa40",
  gradientStop = "#9c40ff",
}: BeamProps) {
  const id = useId();
  const [pathD, setPathD] = useState("");
  const [dims, setDims] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const updatePath = () => {
      if (!containerRef.current || !fromRef.current || !toRef.current) return;
      const containerRect = containerRef.current.getBoundingClientRect();
      const rectA = fromRef.current.getBoundingClientRect();
      const rectB = toRef.current.getBoundingClientRect();

      const svgWidth = containerRect.width;
      const svgHeight = containerRect.height;
      setDims({ width: svgWidth, height: svgHeight });

      const startX = rectA.left - containerRect.left + rectA.width / 2;
      const startY = rectA.top - containerRect.top + rectA.height / 2;
      const endX = rectB.left - containerRect.left + rectB.width / 2;
      const endY = rectB.top - containerRect.top + rectB.height / 2;

      const controlY = startY - curvature;
      const d = `M ${startX},${startY} Q ${(startX + endX) / 2},${controlY} ${endX},${endY}`;
      setPathD(d);
    };

    const resizeObserver = new ResizeObserver(() => updatePath());
    if (containerRef.current) resizeObserver.observe(containerRef.current);
    updatePath();

    return () => resizeObserver.disconnect();
  }, [containerRef, fromRef, toRef, curvature]);

  // Sanitize the useId for use in SVG gradient IDs (remove colons)
  const safeId = id.replace(/[:]/g, "");

  return (
    <svg
      fill="none"
      width={dims.width}
      height={dims.height}
      xmlns="http://www.w3.org/2000/svg"
      className="pointer-events-none absolute left-0 top-0 overflow-hidden stroke-2"
      viewBox={`0 0 ${dims.width} ${dims.height}`}
    >
      {/* Static faint path */}
      <path
        d={pathD}
        stroke={pathColor}
        strokeWidth={pathWidth}
        strokeOpacity={pathOpacity}
        strokeLinecap="round"
      />
      {/* Animated gradient path */}
      <path
        d={pathD}
        strokeWidth={pathWidth}
        stroke={`url(#beam-${safeId})`}
        strokeOpacity={1}
        strokeLinecap="round"
      />
      <defs>
        <linearGradient
          id={`beam-${safeId}`}
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor={gradientStart} stopOpacity="0">
            <animate
              attributeName="offset"
              values="-0.3;1"
              dur={`${duration}s`}
              begin={`${delay}s`}
              repeatCount="indefinite"
            />
          </stop>
          <stop stopColor={gradientStart}>
            <animate
              attributeName="offset"
              values="-0.1;1.1"
              dur={`${duration}s`}
              begin={`${delay}s`}
              repeatCount="indefinite"
            />
          </stop>
          <stop offset="0.3" stopColor={gradientStop}>
            <animate
              attributeName="offset"
              values="0;1.3"
              dur={`${duration}s`}
              begin={`${delay}s`}
              repeatCount="indefinite"
            />
          </stop>
          <stop offset="1" stopColor={gradientStop} stopOpacity="0">
            <animate
              attributeName="offset"
              values="0.3;1.6"
              dur={`${duration}s`}
              begin={`${delay}s`}
              repeatCount="indefinite"
            />
          </stop>
        </linearGradient>
      </defs>
    </svg>
  );
}

// ── Graph section ───────────────────────────────────────────────────────────

// Provider icons — real SVG logos adapted from llmgateway's provider-icons.tsx
export function OpenAIIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 512 509.639" className={className} xmlns="http://www.w3.org/2000/svg" fill="#fff" fillRule="evenodd" clipRule="evenodd">
      <path d="M412.037 221.764a90.834 90.834 0 0 0 4.648-28.67 90.79 90.79 0 0 0-12.443-45.87c-16.37-28.496-46.738-46.089-79.605-46.089-6.466 0-12.943.683-19.264 2.04a90.765 90.765 0 0 0-67.881-30.515h-.576c-39.807 0-75.108 25.686-87.346 63.554-25.626 5.239-47.748 21.31-60.682 44.03a91.873 91.873 0 0 0-12.407 46.077 91.833 91.833 0 0 0 23.694 61.553 90.802 90.802 0 0 0-4.649 28.67 90.804 90.804 0 0 0 12.442 45.87c16.369 28.504 46.74 46.087 79.61 46.087a91.81 91.81 0 0 0 19.253-2.04 90.783 90.783 0 0 0 67.887 30.516h.81c39.829 0 75.119-25.686 87.357-63.588 25.626-5.242 47.748-21.312 60.682-44.033a91.718 91.718 0 0 0 12.383-46.035 91.83 91.83 0 0 0-23.693-61.553zm-136.935 191.397h-.094a68.146 68.146 0 0 1-43.611-15.8 56.936 56.936 0 0 0 2.155-1.221l72.54-41.901a11.799 11.799 0 0 0 5.962-10.251V241.651l30.661 17.704c.326.163.55.479.596.84v84.693c-.042 37.653-30.554 68.198-68.21 68.273zm-146.689-62.649a68.128 68.128 0 0 1-9.152-34.085c0-3.904.341-7.817 1.005-11.663.539.323 1.48.897 2.155 1.285l72.54 41.901a11.832 11.832 0 0 0 11.918-.002l88.563-51.137v35.408a1.1 1.1 0 0 1-.438.94l-73.33 42.339a68.43 68.43 0 0 1-34.11 9.12 68.359 68.359 0 0 1-59.15-34.11zm-19.083-158.36a68.044 68.044 0 0 1 35.538-29.934c0 .625-.036 1.731-.036 2.5v83.801a11.79 11.79 0 0 0 5.954 10.242l88.564 51.13-30.661 17.704a1.096 1.096 0 0 1-1.034.093l-73.337-42.375a68.36 68.36 0 0 1-34.095-59.143 68.412 68.412 0 0 1 9.112-34.085zm251.907 58.621-88.563-51.137 30.661-17.697a1.097 1.097 0 0 1 1.034-.094l73.337 42.339c21.109 12.195 34.132 34.746 34.132 59.132 0 28.604-17.849 54.199-44.686 64.078v-86.308c0-4.219-2.261-8.119-5.919-10.217zm30.518-45.93c-.539-.331-1.48-.898-2.155-1.286l-72.54-41.901a11.842 11.842 0 0 0-5.958-1.611c-2.092 0-4.15.558-5.957 1.611l-88.564 51.137v-35.408a1.1 1.1 0 0 1 .44-.88l73.33-42.303a68.301 68.301 0 0 1 34.108-9.129c37.704 0 68.281 30.577 68.281 68.281a68.69 68.69 0 0 1-.984 11.545zm-191.843 63.109-30.668-17.704a1.09 1.09 0 0 1-.596-.84v-84.692c.016-37.685 30.593-68.236 68.281-68.236a68.332 68.332 0 0 1 43.689 15.804 63.09 63.09 0 0 0-2.155 1.222l-72.54 41.9a11.794 11.794 0 0 0-5.961 10.248v.068l-.05 102.23zm16.655-35.91 39.445-22.782 39.444 22.767v45.55l-39.444 22.767-39.445-22.767v-45.535z" />
    </svg>
  );
}

export function AnthropicIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 512 509.64" className={className} xmlns="http://www.w3.org/2000/svg" fillRule="evenodd" clipRule="evenodd">
      <path fill="#D77655" d="M115.612 0h280.775C459.974 0 512 52.026 512 115.612v278.415c0 63.587-52.026 115.612-115.613 115.612H115.612C52.026 509.639 0 457.614 0 394.027V115.612C0 52.026 52.026 0 115.612 0z" />
      <path fill="#FCF2EE" fillRule="nonzero" d="m142.27 316.619 73.655-41.326 1.238-3.589-1.238-1.996-3.589-.001-12.31-.759-42.084-1.138-36.498-1.516-35.361-1.896-8.897-1.895-8.34-10.995.859-5.484 7.482-5.03 10.717.935 23.683 1.617 35.537 2.452 25.782 1.517 38.193 3.968h6.064l.86-2.451-2.073-1.517-1.618-1.517-36.776-24.922-39.81-26.338-20.852-15.166-11.273-7.683-5.687-7.204-2.451-15.721 10.237-11.273 13.75.935 3.513.936 13.928 10.716 29.749 23.027 38.848 28.612 5.687 4.727 2.275-1.617.278-1.138-2.553-4.271-21.13-38.193-22.546-38.848-10.035-16.101-2.654-9.655c-.935-3.968-1.617-7.304-1.617-11.374l11.652-15.823 6.445-2.073 15.545 2.073 6.547 5.687 9.655 22.092 15.646 34.78 24.265 47.291 7.103 14.028 3.791 12.992 1.416 3.968 2.449-.001v-2.275l1.997-26.641 3.69-32.707 3.589-42.084 1.239-11.854 5.863-14.206 11.652-7.683 9.099 4.348 7.482 10.716-1.036 6.926-4.449 28.915-8.72 45.294-5.687 30.331h3.313l3.792-3.791 15.342-20.372 25.782-32.227 11.374-12.789 13.27-14.129 8.517-6.724 16.1-.001 11.854 17.617-5.307 18.199-16.581 21.029-13.75 17.819-19.716 26.54-12.309 21.231 1.138 1.694 2.932-.278 44.536-9.479 24.062-4.347 28.714-4.928 12.992 6.066 1.416 6.167-5.106 12.613-30.71 7.583-36.018 7.204-53.636 12.689-.657.48.758.935 24.164 2.275 10.337.556h25.301l47.114 3.514 12.309 8.139 7.381 9.959-1.238 7.583-18.957 9.655-25.579-6.066-59.702-14.205-20.474-5.106-2.83-.001v1.694l17.061 16.682 31.266 28.233 39.152 36.397 1.997 8.999-5.03 7.102-5.307-.758-34.401-25.883-13.27-11.651-30.053-25.302-1.996-.001v2.654l6.926 10.136 36.574 54.975 1.895 16.859-2.653 5.485-9.479 3.311-10.414-1.895-21.408-30.054-22.092-33.844-17.819-30.331-2.173 1.238-10.515 113.261-4.929 5.788-11.374 4.348-9.478-7.204-5.03-11.652 5.03-23.027 6.066-30.052 4.928-23.886 4.449-29.674 2.654-9.858-.177-.657-2.173.278-22.37 30.71-34.021 45.977-26.919 28.815-6.445 2.553-11.173-5.789 1.037-10.337 6.243-9.2 37.257-47.392 22.47-29.371 14.508-16.961-.101-2.451h-.859l-98.954 64.251-17.618 2.275-7.583-7.103.936-11.652 3.589-3.791 29.749-20.474.101.102.024.101z" />
    </svg>
  );
}

export function GeminiIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 192 192" className={className} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="gemini-top" x1="21" y1="60" x2="171" y2="60" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#22a05a" />
          <stop offset="45%" stopColor="#1e88e5" />
          <stop offset="100%" stopColor="#2f7df5" />
        </linearGradient>
        <linearGradient id="gemini-bot" x1="21" y1="132" x2="171" y2="132" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#2f7df5" />
          <stop offset="55%" stopColor="#5d8df0" />
          <stop offset="80%" stopColor="#f4a72b" />
          <stop offset="100%" stopColor="#ea3a2d" />
        </linearGradient>
      </defs>
      <path fill="url(#gemini-top)" d="M161.23 55.76 79.92 98.11C53.06 112.1 21.01 92.5 21.01 62.08c0-22.42 18.08-40.59 40.37-40.58l91.57.04c18.88 0 25.04 25.5 8.28 34.22" />
      <path fill="url(#gemini-bot)" d="m30.77 136.24 81.31-42.35c26.86-13.99 58.91 5.61 58.91 36.03 0 22.42-18.08 40.59-40.37 40.58l-91.57-.04c-18.88 0-25.04-25.5-8.28-34.22" />
    </svg>
  );
}

export function XAIIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 759 290.2" className={className} xmlns="http://www.w3.org/2000/svg" fill="#fff">
      <g transform="translate(-674.5,-388.5)">
        <path d="M1433.19 388.934C1345.69 396.068 1001.73 438.71 763.235 678.467H674.732L684.62 668.612C734.53 620.395 955.373 416.045 1433.19 388.668V388.934Z" />
        <path d="M1133.66 678.467H1064.29L927.618 578.979C940.213 571.058 952.888 563.515 965.603 556.33L1133.66 678.467Z" />
        <path d="M1022.8 678.468H953.463L932.43 663.169H814.998C823.265 655.828 831.635 648.702 840.092 641.784H902.996L870.444 618.106C882.079 609.448 893.843 601.155 905.701 593.211L1022.8 678.468Z" />
        <path d="M770.433 495.02 823.901 533.876C810.594 541.712 798.139 549.468 786.514 557.055L701.158 494.986 770.433 495.02Z" />
      </g>
    </svg>
  );
}

export function DeepSeekIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 57.86 43.38" className={className} xmlns="http://www.w3.org/2000/svg" fill="#4d6bfe">
      <path d="M57.26 3.65c-.61-.31-.88.27-1.24.57-.12.09-.22.22-.33.33-.89.98-1.94 1.62-3.31 1.54-1.99-.11-3.7.53-5.21 2.08-.32-1.92-1.39-3.06-3.01-3.8-.85-.38-1.7-.76-2.3-1.59-.41-.6-.53-1.26-.73-1.91-.14-.39-.27-.79-.71-.86-.48-.07-.67.34-.86.68-.75 1.41-1.05 2.96-1.02 4.52.07 3.53 1.53 6.34 4.43 8.34.33.22.42.45.31.79-.19.69-.43 1.35-.64 2.04-.13.44-.33.54-.79.35-1.59-.68-2.97-1.68-4.19-2.9-2.06-2.03-3.93-4.28-6.26-6.04-.54-.41-1.09-.79-1.66-1.15-2.37-2.35.32-4.28.94-4.51.65-.24.22-1.06-1.88-1.06-2.1.01-4.02.73-6.48 1.69-.35.14-.73.25-1.12.33-2.22-.43-4.53-.52-6.95-.25-4.54.52-8.17 2.71-10.84 6.44C.2 13.77-.55 18.88.37 24.2c.97 5.61 3.78 10.25 8.1 13.88 4.48 3.77 9.64 5.61 15.52 5.26 3.58-.21 7.56-.7 12.04-4.57 1.14.57 2.32.8 4.29.98 1.52.14 2.98-.08 4.12-.32 1.77-.38 1.65-2.05 1-2.36-5.19-2.46-4.05-1.46-5.09-2.27 2.64-3.19 6.62-6.49 8.18-17.2.12-.85.02-1.39 0-2.08-.01-.42.08-.58.55-.63 1.31-.15 2.57-.51 3.73-1.16 3.36-1.88 4.72-4.95 5.04-8.64.05-.57 0-1.15-.59-1.44z" />
    </svg>
  );
}

export function MoonshotIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} xmlns="http://www.w3.org/2000/svg" fill="currentColor">
      <path fillRule="evenodd" d="m1.052 16.916 9.539 2.552a21.007 21.007 0 0 0 .06 2.033l5.956 1.593a11.997 11.997 0 0 1-5.586.865l-.18-.016-.044-.004-.084-.009-.094-.01a11.605 11.605 0 0 1-.157-.02l-.107-.014-.11-.016a11.962 11.962 0 0 1-.32-.051l-.042-.008-.075-.013-.107-.02-.07-.015-.093-.019-.075-.016-.095-.02-.097-.023-.094-.022-.068-.017-.088-.022-.09-.024-.095-.025-.082-.023-.109-.03-.062-.02-.084-.025-.093-.028-.105-.034-.058-.019-.08-.026-.09-.031-.066-.024a6.293 6.293 0 0 1-.044-.015l-.068-.025-.101-.037-.057-.022-.08-.03-.087-.035-.088-.035-.079-.032-.095-.04-.063-.028-.063-.027a5.655 5.655 0 0 1-.041-.018l-.066-.03-.103-.047-.052-.024-.096-.046-.062-.03-.084-.04-.086-.044-.093-.047-.052-.027-.103-.055-.057-.03-.058-.032a6.49 6.49 0 0 1-.046-.026l-.094-.053-.06-.034-.051-.03-.072-.041-.082-.05-.093-.056-.052-.032-.084-.053-.061-.039-.079-.05-.07-.047-.053-.035a7.785 7.785 0 0 1-.054-.036l-.044-.03-.044-.03a6.066 6.066 0 0 1-.04-.028l-.057-.04-.076-.054-.069-.05-.074-.054-.056-.042-.076-.057-.076-.059-.086-.067-.045-.035-.064-.052-.074-.06-.089-.073-.046-.039-.046-.039a7.516 7.516 0 0 1-.043-.037l-.045-.04-.061-.053-.07-.062-.068-.06-.062-.058-.067-.062-.053-.05-.088-.084a13.28 13.28 0 0 1-.099-.097l-.029-.028-.041-.042-.069-.07-.05-.051-.05-.053a6.457 6.457 0 0 1-.168-.179l-.08-.088-.062-.07-.071-.08-.042-.049-.053-.062-.058-.068-.046-.056a7.175 7.175 0 0 1-.027-.033l-.045-.055-.066-.082-.041-.052-.05-.064-.02-.025a11.99 11.99 0 0 1-1.44-2.402zm-1.02-5.794 11.353 3.037a20.468 20.468 0 0 0-.469 2.011l10.817 2.894a12.076 12.076 0 0 1-1.845 2.005L.657 15.923l-.016-.046-.035-.104a11.965 11.965 0 0 1-.05-.153l-.007-.023a11.896 11.896 0 0 1-.207-.741l-.03-.126-.018-.08-.021-.097-.018-.081-.018-.09-.017-.084-.018-.094c-.026-.141-.05-.283-.071-.426l-.017-.118-.011-.083-.013-.102a12.01 12.01 0 0 1-.019-.161l-.005-.047a12.12 12.12 0 0 1-.034-2.145zm1.593-5.15 11.948 3.196c-.368.605-.705 1.231-1.01 1.875l11.295 3.022c-.142.82-.368 1.612-.668 2.365l-11.55-3.09L.124 10.26l.015-.1.008-.049.01-.067.015-.087.018-.098c.026-.148.056-.295.088-.442l.028-.124.02-.085.024-.097c.022-.09.045-.18.07-.268l.028-.102.023-.083.03-.1.025-.082.03-.096.026-.082.031-.095a11.896 11.896 0 0 1 1.01-2.232zm4.442-4.4L17.352 4.59a20.77 20.77 0 0 0-1.688 1.721l7.823 2.093c.267.852.442 1.744.513 2.665L2.106 5.213l.045-.065.027-.04.04-.055.046-.065.055-.076.054-.072.064-.086.05-.065.057-.073.055-.07.06-.074.055-.069.065-.077.054-.066.066-.077.053-.06.072-.082.053-.06.067-.074.054-.058.073-.078.058-.06.063-.067.168-.17.1-.098.059-.056.076-.071a12.084 12.084 0 0 1 2.272-1.677zM12.017 0h.097l.082.001.069.001.054.002.068.002.046.001.076.003.047.002.06.003.054.002.087.005.105.007.144.011.088.007.044.004.077.008.082.008.047.005.102.012.05.006.108.014.081.01.042.006.065.01.207.032.07.012.065.011.14.026.092.018.11.022.046.01.075.016.041.01L14.7.3l.042.01.065.015.049.012.071.017.096.024.112.03.113.03.113.032.05.015.07.02.078.024.073.023.05.016.05.016.076.025.099.033.102.036.048.017.064.023.093.034.11.041.116.045.1.04.047.02.06.024.041.018.063.026.04.018.057.025.11.048.1.046.074.035.075.036.06.028.092.046.091.045.102.052.053.028.049.026.046.024.06.033.041.022.052.029.088.05.106.06.087.051.057.034.053.032.096.059.088.055.098.062.036.024.064.041.084.056.04.027.062.042.062.043.023.017c.054.037.108.075.161.114l.083.06.065.048.056.043.086.065.082.064.04.03.05.041.086.069.079.065.085.071c.712.6 1.353 1.283 1.909 2.031L7.222.994l.062-.027.065-.028.081-.034.086-.035c.113-.045.227-.09.341-.131l.096-.035.093-.033.084-.03.096-.031c.087-.03.176-.058.264-.085l.091-.027.086-.025.102-.03.085-.023.1-.026L9.04.37l.09-.023.091-.022.095-.022.09-.02.098-.021.091-.02.095-.018.092-.018.1-.018.091-.016.098-.017.092-.014.097-.015.092-.013.102-.013.091-.012.105-.012.09-.01.105-.01c.093-.01.186-.018.28-.024l.106-.008.09-.005.11-.006.093-.004.1-.004.097-.002.099-.002.197-.002z" />
    </svg>
  );
}

export function OpenRouterIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 381 294" className={className} xmlns="http://www.w3.org/2000/svg">
      <path fill="currentColor" d="M303.9475,17.19926c42.79734,0,77.48933,34.69327,77.48933,77.48933s-34.69199,77.48933-77.48933,77.48933l76.86166,76.86244c9.76367,9.76313,2.84903,26.45667-10.95697,26.45667h-220.88335c-71.32686,0-129.14889-57.82202-129.14889-129.14889S77.64197,17.19926,148.96884,17.19926h154.97866ZM148.96884,68.85881c-42.79607,0-77.48933,34.69327-77.48933,77.48933s34.69327,77.48933,77.48933,77.48933,77.48933-34.69327,77.48933-77.48933-34.69327-77.48933-77.48933-77.48933Z" />
    </svg>
  );
}

// Provider node data with real icon components
const PROVIDER_NODES = [
  { label: "OpenAI", Icon: OpenAIIcon },
  { label: "Anthropic", Icon: AnthropicIcon },
  { label: "Gemini", Icon: GeminiIcon },
  { label: "xAI", Icon: XAIIcon },
  { label: "DeepSeek", Icon: DeepSeekIcon },
  { label: "Moonshot", Icon: MoonshotIcon },
];

const DIALECTS = [
  { name: "chat", note: "OpenAI Chat" },
  { name: "responses", note: "Codex CLI" },
  { name: "messages", note: "Claude Code" },
];

const STATS = [
  { value: "3", label: "Inbound dialects" },
  { value: "4+", label: "Providers" },
  { value: "1", label: "Binary" },
];

function Circle({
  ref,
  children,
  className = "",
}: {
  ref: React.RefObject<HTMLDivElement | null>;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      ref={ref}
      className={`group relative z-10 flex h-16 w-16 items-center justify-center rounded-full border-2 border-[var(--admin-border)] bg-[var(--admin-surface)] p-3 shadow-lg shadow-black/30 backdrop-blur-sm transition-all duration-200 hover:scale-110 hover:border-white/[0.18] hover:shadow-brand-500/30 ${className}`}
    >
      {children}
    </div>
  );
}

export function GraphSection() {
  const containerRef = useRef<HTMLDivElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const centerRef = useRef<HTMLDivElement>(null);
  const rightRefs = [
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
  ];

  return (
    <section className="relative w-full overflow-hidden py-28 md:py-40">
      {/* Rich multi-color radial glow behind center */}
      <div className="absolute left-1/2 top-1/2 h-[32rem] w-[32rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(139,92,246,0.12)_0%,rgba(59,130,246,0.08)_40%,transparent_70%)] blur-2xl" aria-hidden />
      <div className="absolute left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-pink-500/5 blur-3xl" aria-hidden />

      <div className="relative">
        {/* Header */}
        <div className="mx-auto max-w-4xl px-4">
          <div className="mb-8 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h2 className="text-4xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-5xl">
                One request. Any model.
              </h2>
              <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
                Your app sends one request. wiwi routes it to OpenAI, Anthropic, Gemini,
                or any OpenAI-compatible URL — automatically decoding and re-encoding across dialects.
              </p>
            </div>
            <div className="flex gap-6 lg:gap-10">
              {STATS.map((s) => (
                <div key={s.label}>
                  <div className="bg-gradient-to-b from-[var(--admin-text)] to-[var(--admin-text-muted)] bg-clip-text text-3xl font-bold tabular-nums text-transparent sm:text-4xl">
                    {s.value}
                  </div>
                  <div className="text-[12px] text-[var(--admin-text-dim)]">{s.label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Diagram */}
        <div className="relative mx-auto max-w-4xl">
          <div
            className="relative flex h-[560px] items-center justify-center p-10"
            ref={containerRef}
          >
            {/* Left: client node */}
            <div className="absolute left-10 top-1/2 -translate-y-1/2 z-10">
              <Circle ref={leftRef}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[var(--admin-text)]">
                  <rect width="20" height="14" x="2" y="3" rx="2" />
                  <line x1="8" x2="16" y1="21" y2="21" />
                  <line x1="12" x2="12" y1="17" y2="21" />
                </svg>
              </Circle>
              <div className="mt-3 text-center">
                <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--admin-text-dim)]">Your app</span>
              </div>
            </div>

            {/* Center: wiwi gateway */}
            <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
              <Circle ref={centerRef} className="!h-20 !w-20 wiwi-gateway-node">
                <img src="/wiwi-logo.png" alt="wiwi" className="h-10 w-10 rounded-full object-cover" />
              </Circle>
              <div className="mt-3 text-center">
                <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--admin-text-dim)]">wiwi gateway</span>
              </div>
            </div>

            {/* Right: provider nodes */}
            <div className="absolute right-10 top-1/2 flex -translate-y-1/2 flex-col items-center justify-center gap-5 z-10">
              {PROVIDER_NODES.map((node, i) => (
                <div key={node.label} className="flex flex-col items-center">
                  <Circle ref={rightRefs[i]} className="hover:!border-[var(--admin-border-hover)]">
                    <node.Icon className="h-7 w-7 object-contain" />
                  </Circle>
                  <span className="mt-2 text-[10px] font-medium text-[var(--admin-text-dim)]">{node.label}</span>
                </div>
              ))}
            </div>

            {/* Animated beams */}
            <AnimatedBeam
              containerRef={containerRef}
              fromRef={leftRef}
              toRef={centerRef}
              pathWidth={3}
              gradientStart="#3b82f6"
              gradientStop="#8b5cf6"
              duration={3.5}
            />
            {rightRefs.map((ref, i) => {
              const hues = [
                ["#8b5cf6", "#ec4899"], // OpenAI  → violet→pink
                ["#f59e0b", "#ef4444"], // Anthropic → amber→red
                ["#22c55e", "#3b82f6"], // Gemini  → green→blue
                ["#06b6d4", "#8b5cf6"], // xAI     → cyan→violet
                ["#4d6bfe", "#22c55e"], // DeepSeek → blue→green
                ["#ec4899", "#f59e0b"], // Moonshot → pink→amber
              ];
              const [g0, g1] = hues[i] ?? ["#8b5cf6", "#ec4899"];
              return (
                <AnimatedBeam
                  key={i}
                  containerRef={containerRef}
                  fromRef={centerRef}
                  toRef={ref}
                  curvature={(i - 2) * 24}
                  delay={i * 0.4}
                  pathWidth={3}
                  gradientStart={g0}
                  gradientStop={g1}
                  duration={3.5 + i * 0.4}
                />
              );
            })}
          </div>
        </div>

        {/* Inbound dialect labels below the diagram */}
        <div className="mx-auto mt-4 max-w-4xl px-4">
          <div className="flex flex-wrap items-center justify-center gap-3">
            {DIALECTS.map((d) => (
              <div
                key={d.name}
                className="flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-2.5 transition-all duration-200 hover:border-brand-400/40 hover:bg-brand-500/[0.06]"
              >
                <span className="font-mono text-[13px] text-blue-300">{d.name}</span>
                <span className="text-[11px] text-[var(--admin-text-dim)]">{d.note}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
