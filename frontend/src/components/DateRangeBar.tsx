import { useStore } from "../store";
import { prettyDate } from "../lib/format";

export default function DateRangeBar() {
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const setRange = useStore((s) => s.setRange);
  const recordStart = useStore((s) => s.recordStart);
  const recordEnd = useStore((s) => s.recordEnd);
  const view = useStore((s) => s.view);
  const isFullRange = rangeStart === recordStart && rangeEnd === recordEnd;

  return (
    <div className="z-30 flex min-h-[50px] shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-basalt-300 bg-basalt-100 px-3 py-2 text-xs shadow-md sm:px-5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-display text-[10px] font-bold uppercase tracking-wide text-terra">
          {view === "calendar" ? "Calendar range" : "Time-series range"}
        </span>
        <span className="font-mono text-[10px] text-muted" title={view === "analytics" ? "The selected range scopes the time-series chart; other analytics use the full archive." : "The selected range scopes the calendar matrix."}>
          {recordStart && recordEnd
            ? `Record: ${prettyDate(recordStart)} → ${prettyDate(recordEnd)}`
            : "Archive loading…"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <label className="flex items-center gap-1.5 text-pewter font-display">
          <span className="text-[11px] font-semibold">From</span>
          <input
            type="date"
            value={rangeStart}
            min={recordStart ?? undefined}
            max={rangeEnd}
            onChange={(e) => setRange(e.target.value, rangeEnd)}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-[11px] text-alabaster outline-none focus:border-terra"
            aria-label="range start"
          />
        </label>
        <span className="text-muted" aria-hidden>→</span>
        <label className="flex items-center gap-1.5 text-pewter font-display">
          <span className="text-[11px] font-semibold">To</span>
          <input
            type="date"
            value={rangeEnd}
            min={rangeStart}
            max={recordEnd ?? undefined}
            onChange={(e) => setRange(rangeStart, e.target.value)}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-[11px] text-alabaster outline-none focus:border-terra"
            aria-label="range end"
          />
        </label>
        {recordStart && recordEnd && (
          <button
            type="button"
            onClick={() => setRange(recordStart, recordEnd)}
            disabled={isFullRange}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-2.5 py-1 font-display text-[10px] font-semibold text-pewter transition hover:border-terra/60 hover:text-alabaster disabled:cursor-default disabled:opacity-40"
            title="Restore the full archive date range"
          >
            Full record
          </button>
        )}
      </div>
    </div>
  );
}
