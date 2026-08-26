// DateRangeSelect — simpler relative date-range dropdown (Last 1m, Last 5m,
// Last 1h, Today, Last 30 days, etc.). Ported from the Next.js reference's
// date-range-select.tsx. Self-contained: inline date math, no date-fns.

import { ChevronDownIcon } from "lucide-react";
import { useMemo, useState } from "react";

export interface DateRange {
  start: Date;
  end: Date;
}

interface RelativeTimeOption {
  label: string;
  value: string;
  getRange: () => DateRange;
}

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
function subMinutes(n: number) {
  return { start: new Date(Date.now() - n * 60_000), end: new Date() };
}
function subHours(n: number) {
  return { start: new Date(Date.now() - n * 3_600_000), end: new Date() };
}
function subDays(n: number) {
  const start = new Date();
  start.setDate(start.getDate() - n);
  return { start, end: new Date() };
}

const RELATIVE_TIME_OPTIONS: RelativeTimeOption[] = [
  { label: "Today", value: "today", getRange: () => ({ start: startOfDay(new Date()), end: new Date() }) },
  { label: "Last 1 minute", value: "1m", getRange: () => subMinutes(1) },
  { label: "Last 5 minutes", value: "5m", getRange: () => subMinutes(5) },
  { label: "Last 30 minutes", value: "30m", getRange: () => subMinutes(30) },
  { label: "Last 1 hour", value: "1h", getRange: () => subHours(1) },
  { label: "Last 2 hours", value: "2h", getRange: () => subHours(2) },
  { label: "Last 4 hours", value: "4h", getRange: () => subHours(4) },
  { label: "Last 12 hours", value: "12h", getRange: () => subHours(12) },
  { label: "Last 24 hours", value: "24h", getRange: () => subHours(24) },
  { label: "Last 3 days", value: "3days", getRange: () => subDays(3) },
  { label: "Last 7 days", value: "7days", getRange: () => subDays(7) },
  { label: "Last 14 days", value: "14days", getRange: () => subDays(14) },
  { label: "Last 30 days", value: "30days", getRange: () => subDays(30) },
];

interface DateRangeSelectProps {
  value?: string;
  onChange: (value: string, range: DateRange) => void;
  className?: string;
}

export function DateRangeSelect({ value, onChange, className }: DateRangeSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(value);

  const selectedOption = RELATIVE_TIME_OPTIONS.find((option) => option.value === selected);

  const filteredOptions = useMemo(
    () => (search.trim() ? RELATIVE_TIME_OPTIONS.filter((o) => o.label.toLowerCase().includes(search.toLowerCase())) : RELATIVE_TIME_OPTIONS),
    [search],
  );

  const handleSelect = (option: RelativeTimeOption) => {
    setSelected(option.value);
    onChange(option.value, option.getRange());
    setOpen(false);
  };

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex h-9 w-52 items-center justify-between gap-2 whitespace-nowrap rounded-md border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)] ${
          !selectedOption ? "text-[var(--admin-text-muted)]" : ""
        } ${className ?? ""}`}
      >
        <span className="truncate">{selectedOption?.label ?? "Select time range"}</span>
        <ChevronDownIcon className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => { setOpen(false); setSearch(""); }} />
          <div className="absolute left-0 z-40 mt-1 w-52 overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-xl">
            <div className="px-3 pb-2 pt-3">
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search…"
                className="admin-input h-8 border-b-2 border-blue-500/40 bg-transparent px-0 shadow-none"
              />
            </div>
            <div className="admin-scroll max-h-72 overflow-y-auto pb-1">
              {filteredOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => handleSelect(option)}
                  className={`w-full px-3 py-2 text-left text-sm text-[var(--admin-text)] transition-colors hover:bg-white/[0.03] ${
                    selected === option.value ? "bg-white/[0.02]" : ""
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
