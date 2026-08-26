// TimeRangePicker — segmented control for picking a relative time range
// (1h, 4h, 24h, 7d, and a gated 30d). Ported from the Next.js reference's
// time-range-picker.tsx. The reference gated 30d behind an enterprise flag
// via a config hook; this port accepts an `isGated` prop (default false) so
// the parent can decide. Self-contained: no tooltip primitive, no config hook.

import { Lock } from "lucide-react";

const FREE_RANGES = [
  { value: "1h", label: "1h" },
  { value: "4h", label: "4h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
] as const;

const PRO_RANGES = [{ value: "30d", label: "30d" }] as const;

export type TimeRangeValue =
  | (typeof FREE_RANGES)[number]["value"]
  | (typeof PRO_RANGES)[number]["value"];

interface TimeRangePickerProps {
  value: TimeRangeValue;
  onChange: (value: TimeRangeValue) => void;
  allowedValues?: readonly TimeRangeValue[];
  /** When true, the 30d range is shown locked (enterprise/self-hosted gate). */
  isGated?: boolean;
}

export function TimeRangePicker({ value, onChange, allowedValues, isGated = false }: TimeRangePickerProps) {
  const freeRanges = allowedValues
    ? FREE_RANGES.filter((r) => allowedValues.includes(r.value))
    : FREE_RANGES;
  const proRanges = allowedValues
    ? PRO_RANGES.filter((r) => allowedValues.includes(r.value))
    : PRO_RANGES;

  return (
    <div className="inline-flex w-full items-center rounded-md border border-[var(--admin-border)] bg-white/[0.02] p-0.5 sm:w-auto">
      {freeRanges.map((range) => (
        <button
          key={range.value}
          type="button"
          onClick={() => onChange(range.value)}
          className={`flex-1 rounded-sm px-3 py-1 text-sm font-medium transition-colors sm:flex-none ${
            value === range.value
              ? "bg-[var(--admin-bg)] text-[var(--admin-text)] shadow-sm"
              : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
          }`}
        >
          {range.label}
        </button>
      ))}
      {proRanges.map((range) =>
        isGated ? (
          <button
            key={range.value}
            type="button"
            disabled
            aria-disabled="true"
            title="Extended analytics — available on Enterprise or self-hosted"
            className="inline-flex flex-1 cursor-not-allowed items-center justify-center gap-1 rounded-sm px-3 py-1 text-sm font-medium text-[var(--admin-text-dim)] sm:flex-none"
          >
            {range.label}
            <Lock className="h-3 w-3" />
          </button>
        ) : (
          <button
            key={range.value}
            type="button"
            onClick={() => onChange(range.value)}
            className={`flex-1 rounded-sm px-3 py-1 text-sm font-medium transition-colors sm:flex-none ${
              value === range.value
                ? "bg-[var(--admin-bg)] text-[var(--admin-text)] shadow-sm"
                : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
            }`}
          >
            {range.label}
          </button>
        ),
      )}
    </div>
  );
}
