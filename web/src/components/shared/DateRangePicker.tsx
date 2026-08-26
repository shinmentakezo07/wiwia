// DateRangePicker — a popover with date-range presets (Today, This week,
// Last 30 days, etc.) and a custom month-range calendar. Ported from the
// Next.js reference's date-range-picker.tsx. The reference used date-fns and
// a timezone-aware day-key helper; this port inlines the date math so it has
// no date-fns dependency, and writes `from`/`to` URL params via react-router's
// navigate.

import { ChevronDownIcon, ChevronLeftIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

interface DatePreset {
  label: string;
  value: string;
  getRange: () => { from: Date; to: Date };
}

interface DateRangePickerProps {
  /** Called with the chosen range; if omitted, navigates with from/to params. */
  onRangeChange?: (from: Date, to: Date) => void;
  basePath?: string;
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ── inline date helpers (replacing date-fns) ──────────────────────────────

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
function addDays(d: Date, n: number) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}
function startOfWeek(d: Date) {
  // week starts Monday
  const day = d.getDay();
  const diff = (day === 0 ? 6 : day - 1);
  return addDays(d, -diff);
}
function endOfWeek(d: Date) {
  return addDays(startOfWeek(d), 6);
}
function startOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function endOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}
function startOfYear(d: Date) {
  return new Date(d.getFullYear(), 0, 1);
}
function endOfYear(d: Date) {
  return new Date(d.getFullYear(), 11, 31);
}
function addMonths(d: Date, n: number) {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
function addYears(d: Date, n: number) {
  return new Date(d.getFullYear() + n, d.getMonth(), d.getDate());
}
function addQuarters(d: Date, n: number) {
  return addMonths(d, n * 3);
}
function startOfQuarter(d: Date) {
  const m = d.getMonth();
  return new Date(d.getFullYear(), m - (m % 3), 1);
}
function endOfQuarter(d: Date) {
  return endOfMonth(addMonths(startOfQuarter(d), 2));
}
function getQuarter(d: Date) {
  return Math.floor(d.getMonth() / 3) + 1;
}
function fmtDate(d: Date) {
  return d.toISOString().slice(0, 10); // yyyy-MM-dd
}
function fmtLabel(d: Date) {
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function getQuarterLabel(date: Date) {
  return `Q${getQuarter(date)} ${date.getFullYear()}`;
}

function buildPresets(): DatePreset[] {
  const today = startOfDay(new Date());
  return [
    { label: "Custom", value: "custom", getRange: () => ({ from: addDays(today, -6), to: today }) },
    { label: "Today", value: "today", getRange: () => ({ from: today, to: today }) },
    { label: "This week", value: "this_week", getRange: () => ({ from: startOfWeek(today), to: today }) },
    { label: "This month", value: "this_month", getRange: () => ({ from: startOfMonth(today), to: today }) },
    { label: "This year", value: "this_year", getRange: () => ({ from: startOfYear(today), to: today }) },
    {
      label: "Last week",
      value: "last_week",
      getRange: () => {
        const lw = addDays(today, -7);
        return { from: startOfWeek(lw), to: endOfWeek(lw) };
      },
    },
    {
      label: "Last month",
      value: "last_month",
      getRange: () => {
        const lm = addMonths(today, -1);
        return { from: startOfMonth(lm), to: endOfMonth(lm) };
      },
    },
    {
      label: "Last year",
      value: "last_year",
      getRange: () => {
        const ly = addYears(today, -1);
        return { from: startOfYear(ly), to: endOfYear(ly) };
      },
    },
    { label: "Last 30 days", value: "last_30_days", getRange: () => ({ from: addDays(today, -29), to: today }) },
    { label: "Last 90 days", value: "last_90_days", getRange: () => ({ from: addDays(today, -89), to: today }) },
    { label: "Last 6 months", value: "last_6_months", getRange: () => ({ from: addMonths(today, -6), to: today }) },
    {
      label: `This quarter (${getQuarterLabel(today)})`,
      value: "this_quarter",
      getRange: () => ({ from: startOfQuarter(today), to: today }),
    },
    {
      label: `Last quarter (${getQuarterLabel(addQuarters(today, -1))})`,
      value: "last_quarter",
      getRange: () => {
        const lq = addQuarters(today, -1);
        return { from: startOfQuarter(lq), to: endOfQuarter(lq) };
      },
    },
    {
      label: `2 quarters ago (${getQuarterLabel(addQuarters(today, -2))})`,
      value: "2_quarters_ago",
      getRange: () => {
        const q = addQuarters(today, -2);
        return { from: startOfQuarter(q), to: endOfQuarter(q) };
      },
    },
    {
      label: `3 quarters ago (${getQuarterLabel(addQuarters(today, -3))})`,
      value: "3_quarters_ago",
      getRange: () => {
        const q = addQuarters(today, -3);
        return { from: startOfQuarter(q), to: endOfQuarter(q) };
      },
    },
    { label: "All time", value: "all_time", getRange: () => ({ from: new Date(2020, 0, 1), to: today }) },
  ];
}

function findMatchingPreset(from: Date, to: Date, presets: DatePreset[]): string {
  for (const preset of presets) {
    if (preset.value === "custom") continue;
    const range = preset.getRange();
    if (fmtDate(from) === fmtDate(range.from) && fmtDate(to) === fmtDate(range.to)) {
      return preset.value;
    }
  }
  return "custom";
}

function getDateRangeFromParams(searchParams: URLSearchParams) {
  const fromParam = searchParams.get("from");
  const toParam = searchParams.get("to");
  if (fromParam && toParam) {
    return { from: new Date(fromParam + "T00:00:00"), to: new Date(toParam + "T00:00:00") };
  }
  const today = startOfDay(new Date());
  return { from: addDays(today, -6), to: today };
}

function compareMonth(a: Date, b: Date) {
  return a.getFullYear() * 12 + a.getMonth() - (b.getFullYear() * 12 + b.getMonth());
}

// ── Month range calendar ──────────────────────────────────────────────────

function MonthRangePicker({ from, to, onSelect }: { from: Date; to: Date; onSelect: (from: Date, to: Date) => void }) {
  const today = startOfDay(new Date());
  const [leftYear, setLeftYear] = useState(() => today.getFullYear() - 1);
  const [pendingFrom, setPendingFrom] = useState<Date | null>(null);
  const [hoverMonth, setHoverMonth] = useState<Date | null>(null);
  const rightYear = leftYear + 1;

  const handleMonthClick = (year: number, monthIdx: number) => {
    if (year > today.getFullYear() || (year === today.getFullYear() && monthIdx > today.getMonth())) {
      return;
    }
    const clicked = new Date(year, monthIdx, 1);
    if (!pendingFrom) {
      setPendingFrom(clicked);
    } else {
      const [start, end] = clicked < pendingFrom ? [clicked, pendingFrom] : [pendingFrom, clicked];
      onSelect(startOfMonth(start), endOfMonth(end));
      setPendingFrom(null);
      setHoverMonth(null);
    }
  };

  const getEffectiveRange = (): { lo: Date; hi: Date } => {
    if (pendingFrom && hoverMonth) {
      return pendingFrom <= hoverMonth ? { lo: pendingFrom, hi: hoverMonth } : { lo: hoverMonth, hi: pendingFrom };
    }
    return { lo: from, hi: to };
  };

  const isFutureMonth = (year: number, monthIdx: number) =>
    year > today.getFullYear() || (year === today.getFullYear() && monthIdx > today.getMonth());

  const renderYearPanel = (year: number) => {
    const { lo, hi } = getEffectiveRange();
    return (
      <div className="flex-1 min-w-0">
        <div className="mb-2 text-center text-sm font-medium text-[var(--admin-text)]">{year}</div>
        <div className="grid grid-cols-3 gap-1">
          {MONTH_NAMES.map((name, idx) => {
            const d = new Date(year, idx, 1);
            const disabled = isFutureMonth(year, idx);
            const inRange = !disabled && compareMonth(d, lo) >= 0 && compareMonth(d, hi) <= 0;
            const isStart = !disabled && year === lo.getFullYear() && idx === lo.getMonth();
            const isEnd = !disabled && year === hi.getFullYear() && idx === hi.getMonth();
            return (
              <button
                key={name}
                type="button"
                disabled={disabled}
                onClick={() => handleMonthClick(year, idx)}
                onMouseEnter={() => { if (pendingFrom) setHoverMonth(new Date(year, idx, 1)); }}
                onMouseLeave={() => { if (pendingFrom) setHoverMonth(null); }}
                className={`rounded px-2 py-1.5 text-sm transition-colors ${
                  disabled
                    ? "cursor-not-allowed opacity-30"
                    : "cursor-pointer text-[var(--admin-text)] hover:bg-[var(--admin-accent-soft)]"
                } ${inRange ? "bg-[var(--admin-accent-soft)]" : ""} ${
                  (isStart || isEnd) ? "bg-blue-500/30 text-white hover:bg-blue-500/40" : ""
                }`}
              >
                {name}
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div>
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => setLeftYear((y) => y - 1)}
          className="mt-1 rounded p-1 text-[var(--admin-text-muted)] hover:bg-white/[0.04]"
        >
          <ChevronLeftIcon className="h-4 w-4" />
        </button>
        <div className="flex flex-1 gap-6">
          {renderYearPanel(leftYear)}
          {renderYearPanel(rightYear)}
        </div>
      </div>
      {pendingFrom && <p className="mt-2 text-center text-xs text-[var(--admin-text-muted)]">Select end month</p>}
    </div>
  );
}

export function DateRangePicker({ onRangeChange, basePath }: DateRangePickerProps) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [showCalendar, setShowCalendar] = useState(false);

  const { from, to } = getDateRangeFromParams(searchParams);
  const presets = useMemo(() => buildPresets(), []);
  const activePreset = useMemo(() => findMatchingPreset(from, to, presets), [from, to, presets]);

  const filteredPresets = useMemo(
    () => (search.trim() ? presets.filter((p) => p.label.toLowerCase().includes(search.toLowerCase())) : presets),
    [search, presets],
  );

  const updateDateRange = (newFrom: Date, newTo: Date) => {
    if (onRangeChange) {
      onRangeChange(newFrom, newTo);
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    params.delete("days");
    params.set("from", fmtDate(newFrom));
    params.set("to", fmtDate(newTo));
    const path = basePath ?? location.pathname;
    navigate(`${path}?${params.toString()}`);
  };

  const handlePresetSelect = (preset: DatePreset) => {
    if (preset.value === "custom") {
      setShowCalendar(true);
      return;
    }
    const range = preset.getRange();
    updateDateRange(range.from, range.to);
    setOpen(false);
  };

  const handleCustomSelect = (newFrom: Date, newTo: Date) => {
    updateDateRange(newFrom, newTo);
    setOpen(false);
    setShowCalendar(false);
  };

  const triggerLabel = useMemo(() => {
    const preset = presets.find((p) => p.value === activePreset);
    if (preset && preset.value !== "custom") return preset.label;
    return `${fmtLabel(from)} – ${fmtLabel(to)}`;
  }, [activePreset, from, to, presets]);

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 items-center gap-2 rounded-md border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]"
      >
        {triggerLabel}
        <ChevronDownIcon className="h-4 w-4 opacity-50" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => { setOpen(false); setSearch(""); setShowCalendar(false); }} />
          <div
            className={`absolute left-0 z-40 mt-1 overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-xl ${
              showCalendar ? "w-[500px]" : "w-72"
            }`}
          >
            {!showCalendar ? (
              <div>
                <div className="px-3 pb-2 pt-3">
                  <input
                    autoFocus
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search presets…"
                    className="admin-input h-8 border-b-2 border-blue-500/40 bg-transparent px-0 shadow-none"
                  />
                </div>
                <div className="admin-scroll max-h-72 overflow-y-auto pb-1">
                  {filteredPresets.map((preset) => (
                    <button
                      key={preset.value}
                      type="button"
                      onClick={() => handlePresetSelect(preset)}
                      className={`w-full px-3 py-2 text-left text-sm text-[var(--admin-text)] transition-colors hover:bg-white/[0.03] ${
                        activePreset === preset.value ? "bg-white/[0.02]" : ""
                      }`}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="p-4">
                <div className="mb-4 flex items-center gap-4">
                  <div>
                    <p className="text-xs text-[var(--admin-text-muted)]">From</p>
                    <p className="text-sm font-medium text-[var(--admin-text)]">{fmtLabel(from)}</p>
                  </div>
                  <span className="text-[var(--admin-text-muted)]">–</span>
                  <div>
                    <p className="text-xs text-[var(--admin-text-muted)]">To</p>
                    <p className="text-sm font-medium text-[var(--admin-text)]">{fmtLabel(to)}</p>
                  </div>
                </div>
                <MonthRangePicker from={from} to={to} onSelect={handleCustomSelect} />
                <button
                  type="button"
                  onClick={() => setShowCalendar(false)}
                  className="mt-3 text-xs text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                >
                  ← Back to presets
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export { getDateRangeFromParams };
