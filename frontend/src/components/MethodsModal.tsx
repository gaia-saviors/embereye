import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { MethodsResponse } from "../api/types";
import { useStore } from "../store";

export default function MethodsModal() {
  const open = useStore((s) => s.methodsOpen);
  const setOpen = useStore((s) => s.setMethodsOpen);
  const aoiId = useStore((s) => s.aoiId);
  const meta = useStore((s) => s.meta);
  const [methods, setMethods] = useState<MethodsResponse | null>(null);

  useEffect(() => {
    if (open && aoiId) {
      api.methods(aoiId).then(setMethods).catch(() => setMethods(null));
    }
  }, [open, aoiId]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-basalt-300 bg-basalt-100 p-6 shadow-2xl text-alabaster"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-start justify-between border-b border-basalt-300 pb-3">
          <div>
            <h2 className="font-display text-lg font-bold text-alabaster tracking-wide">
              Harmonization Methodology &amp; Provenance
            </h2>
            <p className="font-mono text-xs text-terra">
              {meta?.methodology.harmonization_tier ?? "Standard Multi-Decadal Pipeline"}
            </p>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-3 py-1 text-xs font-semibold text-pewter hover:border-basalt-400 hover:text-alabaster transition-colors"
          >
            ✕ CLOSE
          </button>
        </div>

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
                        <th className="py-1.5 pr-3">95% PI</th>
                        <th className="py-1.5 pr-3">Agreement ρ</th>
                        <th className="py-1.5">Window</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono text-[11px]">
                      {methods.calibrations.map((c) => {
                        const p = c.params as Record<string, unknown>;
                        const isMoy = c.method === "moy_factor_v1";
                        const isCont = c.method === "continuity_ratio";
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
                                ? `[${(Number(p.factor_lo) || 1).toFixed(3)}, ${(Number(p.factor_hi) || 1).toFixed(3)}]`
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
