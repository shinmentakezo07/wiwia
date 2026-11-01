// ErrorBoundary — without one, a render error in any page blanks the whole SPA
// with no way back. This catches it, shows the message, and offers a reload.

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the component stack in the console; the UI stays minimal.
    console.error("Unhandled render error", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--admin-bg)] px-6">
          <div className="w-full max-w-lg rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
            <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10">
              <AlertTriangle size={22} className="text-red-400" />
            </span>
            <h1 className="mt-4 text-[20px] font-semibold text-[var(--admin-text)]">
              Something broke while rendering
            </h1>
            <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
              The console hit an unexpected error. Reloading usually clears it; if it
              persists, the message below is what to report.
            </p>
            <pre className="mt-4 max-h-40 overflow-auto rounded-lg border border-[var(--admin-border)] bg-zinc-950 p-3 text-left font-mono text-[11px] leading-relaxed text-zinc-300">
              {this.state.error.message || String(this.state.error)}
            </pre>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="admin-btn admin-btn-primary mt-5"
            >
              <RotateCcw size={14} className="mr-2" />
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
