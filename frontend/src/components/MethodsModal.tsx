import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { MethodsResponse } from "../api/types";
import { useStore } from "../store";
import { EMBEREYE_LOGO_SRC } from "../assets/logo";

export default function MethodsModal() {
  const open = useStore((s) => s.methodsOpen);
  const setOpen = useStore((s) => s.setMethodsOpen);
  const aoiId = useStore((s) => s.aoiId);
  const meta = useStore((s) => s.meta);
  const [methods, setMethods] = useState<MethodsResponse | null>(null);
  const [methodsLoading, setMethodsLoading] = useState(false);
  const [methodsError, setMethodsError] = useState<string | null>(null);
  const [methodsRetry, setMethodsRetry] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusables = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? []);
      if (!focusables.length) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [open, setOpen]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setMethods(null);
    setMethodsError(null);
    setMethodsLoading(Boolean(aoiId));
    if (!aoiId) return () => { alive = false; };
    api.methods(aoiId)
      .then((data) => { if (alive) setMethods(data); })
      .catch((error) => { if (alive) setMethodsError((error as Error).message); })
      .finally(() => { if (alive) setMethodsLoading(false); });
    return () => { alive = false; };
  }, [open, aoiId, methodsRetry]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-2 backdrop-blur-sm sm:p-4"
      onClick={() => setOpen(false)}
    >
      <div
        className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-basalt-300 bg-basalt-100 p-4 text-alabaster shadow-2xl sm:max-h-[85vh] sm:p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="methods-modal-title"
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className="mb-5 flex items-center justify-between border-b border-basalt-300 pb-3">
          <div className="flex items-center gap-3.5">
            <img
              src={EMBEREYE_LOGO_SRC}
              alt="EmberEye Project Logo"
              className="h-11 w-11 rounded-xl object-contain ring-1 ring-white/10 shadow"
            />
            <div>
              <div className="flex items-center gap-2">
                <h2 id="methods-modal-title" className="font-display text-lg font-bold text-alabaster tracking-wide">
                  Methodology &amp; Data Sources
                </h2>
                <span className="rounded bg-terra/20 px-2 py-0.5 text-[10px] font-bold text-terra">
                  NASA Space Apps 2026
                </span>
              </div>
              <p className="font-mono text-xs text-muted">
                {meta?.methodology.harmonization_tier ?? "Harmonization Methodology & Provenance"}
              </p>
            </div>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close methodology dialog"
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-3 py-1 text-xs font-semibold text-pewter hover:border-basalt-400 hover:text-alabaster transition-colors"
          >
            ✕ CLOSE
          </button>
        </div>

        {!meta && <p role="status" className="mb-4 text-xs text-pewter">Loading application methodology…</p>}
        {methodsLoading && <p role="status" className="mb-4 text-xs text-pewter">Loading AOI calibration and limitations…</p>}
        {methodsError && (
          <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-coral/40 bg-coral/10 p-3 text-xs text-alabaster">
            <span>AOI methods could not be loaded: {methodsError}</span>
            <button type="button" onClick={() => setMethodsRetry((attempt) => attempt + 1)} className="rounded-lg border border-coral/60 px-3 py-1.5 font-semibold text-coral hover:bg-coral/10">Retry methods</button>
          </div>
        )}

        {meta && (
          <>
            <Section title="Pipeline Architecture">
              <ol className="list-decimal space-y-1.5 pl-5 text-xs leading-relaxed text-pewter">
                {meta.methodology.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              <p className="mt-2.5 font-mono text-xs text-amber">{meta.methodology.units}</p>
            </Section>

            <Section title="Anomaly Baseline Definition">
              <p className="text-xs leading-relaxed text-pewter">{meta.baseline.definition}</p>
            </Section>

            {meta.firms_seams?.length > 0 && (
              <Section title="Data Provenance — Standard (SP) &amp; NRT Seam">
                <ul className="space-y-2 text-xs leading-relaxed text-pewter">
                  {meta.firms_seams.map((s) => (
                    <li key={s.satellite} className="rounded-lg bg-basalt-200 p-2.5 border border-basalt-300">
                      <span className="font-bold text-alabaster font-display">{s.satellite}</span>
                      {" "}· Standard (SP) archive through{" "}
                      <span className="font-mono text-terra font-semibold">{s.sp_last_date}</span>,
                      NRT provisional from{" "}
                      <span className="font-mono text-amber font-semibold">{s.nrt_first_date}</span>
                      <div className="mt-1 text-[10px] text-muted">{s.note}</div>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {methods && (
              <Section title={`Calibration Factors — ${methods.aoi_id}`}>
                <div className="overflow-x-auto rounded-xl border border-basalt-300 bg-basalt-200/50 p-2">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted font-mono text-[10px] border-b border-basalt-300">
                      <tr>
                        <th className="py-1.5 pr-3">Variable</th>
                        <th className="py-1.5 pr-3">Method</th>
                        <th className="py-1.5 pr-3">MAPE / Bias · Factors</th>
                        <th className="py-1.5 pr-3">95% PI / Factor range</th>
                        <th className="py-1.5 pr-3">Agreement ρ</th>
                        <th className="py-1.5">Window</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono text-[11px]">
                      {methods.calibrations.map((c) => {
                        const p = c.params as Record<string, unknown>;
                        const isMoy = c.method === "moy_factor_v1";
                        const isCont = c.method === "continuity_ratio";
                        const dayLo = Number(p.factor_day_lo ?? p.factor_lo ?? p.factor_day ?? 1);
                        const dayHi = Number(p.factor_day_hi ?? p.factor_hi ?? p.factor_day ?? 1);
                        const nightLo = Number(p.factor_night_lo ?? p.factor_lo ?? p.factor_night ?? 1);
                        const nightHi = Number(p.factor_night_hi ?? p.factor_hi ?? p.factor_night ?? 1);
                        const hasSeparatedBounds = [
                          p.factor_day_lo, p.factor_day_hi,
                          p.factor_night_lo, p.factor_night_hi,
                        ].every((value) => value !== undefined && value !== null);
                        const legacyLo = Number(p.factor_lo ?? p.factor_day ?? 1);
                        const legacyHi = Number(p.factor_hi ?? p.factor_day ?? 1);
                        const factorInterval = hasSeparatedBounds
                          ? `Day [${dayLo.toFixed(3)}, ${dayHi.toFixed(3)}] · Night [${nightLo.toFixed(3)}, ${nightHi.toFixed(3)}]`
                          : `Combined [${legacyLo.toFixed(3)}, ${legacyHi.toFixed(3)}]`;
                        return (
                          <tr key={c.variable + c.direction + c.method} className="border-b border-basalt-300/50">
                            <td className="py-2 pr-3 font-semibold text-alabaster font-sans">
                              {c.variable}
                              <div className="text-[9px] font-normal text-muted">{c.direction}</div>
                            </td>
                            <td className="py-2 pr-3 text-[10px] text-amber">{c.method}</td>
                            <td className="py-2 pr-3 text-pewter">
                              {isMoy
                                ? `${((p.mape as number) * 100).toFixed(1)}% / ${((p.bias as number) * 100).toFixed(1)}%`
                                : isCont
                                ? `Day ${(Number(p.factor_day) || 1).toFixed(3)} · Night ${(Number(p.factor_night) || 1).toFixed(3)}`
                                : "—"}
                            </td>
                            <td className="py-2 pr-3 text-pewter">
                              {isMoy
                                ? `[${(Number(p.pi_low) + 1).toFixed(2)}x, ${(Number(p.pi_high) + 1).toFixed(2)}x]`
                                : isCont
                                ? factorInterval
                                : "—"}
                            </td>
                            <td className="py-2 pr-3 text-terra font-bold">{c.agreement_stat.toFixed(2)}</td>
                            <td className="py-2 text-[10px] text-muted">
                              {c.window_start} → {c.window_end}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {methods && methods.limitations?.length > 0 && (
              <Section title="Scientific Limitations &amp; Caveats">
                <ul className="space-y-1.5 text-xs leading-relaxed text-pewter">
                  {methods.limitations.map((l, i) => (
                    <li key={i}>• {l}</li>
                  ))}
                </ul>
              </Section>
            )}

            {methods && (
              <Section title="Key Academic &amp; Challenge References">
                <ul className="space-y-1 text-xs leading-relaxed text-pewter">
                  {methods.citations.map((c, i) => (
                    <li key={i}>• {c}</li>
                  ))}
                </ul>
              </Section>
            )}

            {meta.seed_disclaimer && (
              <p className="mt-4 rounded-xl border border-terra/30 bg-terra/10 p-3.5 text-xs leading-relaxed text-pewter">
                {meta.seed_disclaimer}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="mb-2 font-display text-[11px] font-bold tracking-widest text-terra uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}
