import { useEffect, useMemo, useRef } from "react";

import { useStore } from "../store";
import { addDays, daysBetween, prettyDate } from "../lib/format";

export default function TimelineScrubber() {
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const setRange = useStore((s) => s.setRange);
  const recordStart = useStore((s) => s.recordStart);
  const recordEnd = useStore((s) => s.recordEnd);
  const cursorDate = useStore((s) => s.cursorDate);
  const setCursorDate = useStore((s) => s.setCursorDate);
  const playing = useStore((s) => s.playing);
  const setPlaying = useStore((s) => s.setPlaying);
  const speed = useStore((s) => s.playSpeedDaysPerTick);
  const setSpeed = useStore((s) => s.setPlaySpeed);
  const setView = useStore((s) => s.setView);

  const totalDays = daysBetween(rangeStart, rangeEnd);
  const cursorOffset = cursorDate ? daysBetween(rangeStart, cursorDate) : totalDays;

  const raf = useRef<number | null>(null);
  const lastTick = useRef<number>(0);

  useEffect(() => {
    if (!playing) return;
    const step = (ts: number) => {
      if (ts - lastTick.current > 220) {
        lastTick.current = ts;
        const s = useStore.getState();
        const next = addDays(s.cursorDate ?? s.rangeEnd, s.playSpeedDaysPerTick);
        if (next >= s.rangeEnd) {
          s.setCursorDate(s.rangeEnd);
          s.setPlaying(false);
        } else {
          s.setCursorDate(next);
        }
      }
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [playing]);

  const ticks = useMemo(() => {
    const out: number[] = [];
    if (totalDays <= 0) return out;
    const stepDays = Math.max(1, Math.round(totalDays / 8));
    for (let d = 0; d <= totalDays; d += stepDays) out.push(d);
    return out;
  }, [totalDays]);

  return (
    <div className="z-30 flex h-[64px] shrink-0 items-center gap-4 border-t border-basalt-300 bg-basalt-100 px-5 shadow-lg">
      {/* Play/Pause Button */}
      <button
        onClick={() => {
          setView("map");
          if (!cursorDate) setCursorDate(rangeEnd);
          setPlaying(!playing);
        }}
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-sm font-bold shadow-sm transition-all ${
          playing
            ? "border-terra bg-terra text-alabaster shadow-terra/30"
            : "border-basalt-300 bg-basalt-200 text-alabaster hover:border-terra/60 hover:text-terra"
        }`}
        aria-label={playing ? "pause playback" : "play temporal sequence"}
      >
        {playing ? "❚❚" : "▶"}
      </button>

      {/* Speed Selector */}
      <div className="flex items-center gap-1 text-[10px] text-muted font-display">
        <span className="mr-1 tracking-wider uppercase font-semibold">Speed</span>
        {[1, 3, 7].map((n) => (
          <button
            key={n}
            onClick={() => setSpeed(n)}
            className={`rounded-lg px-2 py-1 font-mono font-semibold transition ${
              speed === n
                ? "bg-terra text-alabaster shadow-sm"
                : "bg-basalt-200 text-pewter hover:text-alabaster"
            }`}
          >
            {n}d
          </button>
        ))}
      </div>

      {/* Timeline Slider Track */}
      <div className="relative min-w-0 flex-1">
        <input
          type="range"
          min={0}
          max={Math.max(1, totalDays)}
          value={cursorOffset}
          onChange={(e) => {
            setPlaying(false);
            setCursorDate(addDays(rangeStart, Number(e.target.value)));
          }}
          className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-basalt-300 accent-terra"
          aria-label="timeline cursor"
        />
        <div className="pointer-events-none mt-1 flex justify-between text-[9px] font-mono text-muted">
          {ticks.map((d) => (
            <span key={d}>{addDays(rangeStart, d).slice(0, 7)}</span>
          ))}
        </div>
      </div>

      {/* Active Date & Clamped Range Picker */}
      <div className="shrink-0 text-right">
        <div className="font-mono text-xs font-bold text-terra">
          {prettyDate(cursorDate ?? rangeEnd)}
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted font-mono">
          <input
            type="date"
            value={rangeStart}
            min={recordStart ?? undefined}
            max={rangeEnd}
            onChange={(e) => setRange(e.target.value, rangeEnd)}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-1.5 py-0.5 text-[10px] text-alabaster outline-none focus:border-terra"
            aria-label="range start"
          />
          <span>→</span>
          <input
            type="date"
            value={rangeEnd}
            min={rangeStart}
            max={recordEnd ?? undefined}
            onChange={(e) => {
              setRange(rangeStart, e.target.value);
              setCursorDate(e.target.value);
            }}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-1.5 py-0.5 text-[10px] text-alabaster outline-none focus:border-terra"
            aria-label="range end"
          />
        </div>
      </div>
    </div>
  );
}
