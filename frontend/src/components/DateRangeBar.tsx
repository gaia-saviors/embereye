import { useStore } from "../store";
import { prettyDate } from "../lib/format";

export default function DateRangeBar() {
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const setRange = useStore((s) => s.setRange);
  const recordStart = useStore((s) => s.recordStart);
  const recordEnd = useStore((s) => s.recordEnd);

  return (
    <div className="z-30 flex h-[50px] shrink-0 items-center justify-end gap-3 border-t border-basalt-300 bg-basalt-100 px-5 text-xs shadow-md">
      <span className="font-mono text-[11px] text-muted">
        {recordStart && recordEnd
          ? `Record Coverage: ${prettyDate(recordStart)} → ${prettyDate(recordEnd)}`
          : "Archive loading…"}
      </span>
      <span className="text-basalt-300" aria-hidden>|</span>
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
      <span className="text-muted">→</span>
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
    </div>
  );
}
