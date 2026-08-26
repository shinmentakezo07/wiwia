// Dark/light theme toggle. The console is dark-only, so this renders a
// single Moon icon button that does nothing — it exists only so pages ported
// from the reference (which place a ModeToggle in the top bar) still render.
// If light mode is ever supported, this is where the toggle would live.

import { MoonIcon } from "lucide-react";

export function ModeToggle({ className }: { className?: string }) {
  return (
    <button
      type="button"
      aria-label="Toggle theme"
      className={`inline-flex size-9 items-center justify-center rounded-md text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] ${className ?? ""}`}
    >
      <MoonIcon className="size-5" />
      <span className="sr-only">Toggle theme</span>
    </button>
  );
}
