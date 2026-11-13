"""Harmonization subpackage: the MODIS<->VIIRS bridge.

Method (traceable to the research base):
  1. Duplicate correction per sensor          (Zheng et al. 2026, adapted)
  2. Near-synchronous footprint pairing       (Aqua MODIS <-> S-NPP VIIRS,
                                               |dt| <= 5 min, footprint containment)
  3. Aggregate transfer functions             (Vadrevu et al. 2018 style,
                                               Theil-Sen on log1p monthly sums,
                                               chronological out-of-sample split)
  4. Seam-weighted fused daily series         (Volume 5: smooth 2012-2014 ramp,
                                               per-day provenance)
  5. DOY climatology + anomalies              (fixed baseline, +/-15 d kernel)
  6. Deterministic fire-episode clustering    (Su & Yu 2026 style chaining)
"""
