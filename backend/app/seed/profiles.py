"""Preset AOIs and their calibrated fire-regime profiles.

The synthetic archive generator is driven by these profiles. Every numeric
anchor is either a documented mission fact (overpass times, swath widths,
saturation points) or a published statistic (detection-count ratios, fire
season timing, extreme years). The generator simulates TRUE fire events
first, then samples each sensor's detection process against them - so
MODIS and VIIRS observe the same underlying fires with different
sensitivities, exactly the physics the harmonization bridge must recover.

References embedded per-field. `year_factors` encode well-known extreme
fire years per region (ENSO droughts, documented mega-fire seasons).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class Cluster:
    name: str
    lat: float
    lon: float
    weight: float
    spread_km: float
    landcover: str  # cropland | forest | peatland | savanna | mediterranean


@dataclass(slots=True, frozen=True)
class RegionProfile:
    aoi_id: str
    name: str
    description: str
    bbox: tuple[float, float, float, float]  # W, S, E, N
    clusters: tuple[Cluster, ...]
    base_daily_events: float                 # mean true fire events/day at scale 1.0
    seasonality: tuple[tuple[float, float, float], ...]  # (peak_doy, sigma_doy, weight)
    year_factors: dict[int, float]           # documented extreme years
    trend_per_year: float                    # linear trend on the mean
    # Sensor sensitivity: per-size-class detection probability per overpass.
    # Calibrated so count ratios land near published values (Vadrevu 2018:
    # VIIRS 4.8x MODIS in Punjab cropland; Coskuner 2022: ~4x in Turkey).
    det_prob_modis: tuple[float, float, float]    # small, medium, large
    det_prob_viirs: tuple[float, float, float]
    frp_lognorm: tuple[float, float]              # (mu, sigma) of per-pixel FRP MW
    saturation_prob_viirs: float                  # P(I4-saturated bright pixel | large fire)
    cloud_seasonality: tuple[tuple[float, float, float], ...]  # (peak_doy, sigma, weight)
    static_source_cells: tuple[tuple[float, float], ...]       # gas flares / industry
    equatorial_gap_factor: float                  # MODIS daily swath-gap probability
    # Fire size distribution (small, medium, large)
    size_probs: tuple[float, float, float]
    # Diurnal: fraction of events active at the 13:30 pass vs 01:30 pass
    day_fraction: float


@dataclass(slots=True, frozen=True)
class PresetAoi:
    profile: RegionProfile


def _p(**kw) -> RegionProfile:
    return RegionProfile(**kw)


PRESET_PROFILES: list[RegionProfile] = [
    _p(
        aoi_id="nile-delta-egypt",
        name="Nile Delta, Egypt",
        description=(
            "Agricultural residue burning across the delta's croplands. "
            "Dense small fires after the rice harvest (Sep-Nov) with a smaller "
            "spring shoulder; two persistent industrial heat sources exercise "
            "the static-source mask."
        ),
        bbox=(29.8, 30.2, 32.1, 31.7),
        clusters=(
            Cluster("central-delta", 30.85, 31.05, 0.34, 18.0, "cropland"),
            Cluster("east-delta", 30.85, 31.65, 0.26, 14.0, "cropland"),
            Cluster("west-delta", 30.95, 30.55, 0.22, 12.0, "cropland"),
            Cluster("middle-egypt-edge", 30.35, 30.85, 0.18, 20.0, "cropland"),
        ),
        base_daily_events=5.0,
        seasonality=((300, 16, 0.72), (125, 20, 0.28)),
        year_factors={2015: 1.3, 2020: 1.4, 2023: 1.2},
        trend_per_year=0.012,
        det_prob_modis=(0.03, 0.30, 0.95),
        det_prob_viirs=(0.42, 0.92, 1.0),
        frp_lognorm=(1.4, 0.9),
        saturation_prob_viirs=0.02,
        cloud_seasonality=((15, 25, 0.5), (205, 30, 0.3)),
        static_source_cells=((30.95, 29.95), (31.45, 30.35)),
        equatorial_gap_factor=0.18,
        size_probs=(0.86, 0.12, 0.02),
        day_fraction=0.55,
    ),
    _p(
        aoi_id="punjab-india",
        name="Punjab, India",
        description=(
            "Post-monsoon rice-residue burning (Oct-Nov), the canonical "
            "small-fire detection-bias region: VIIRS 375 m reports ~4.8x the "
            "detections of combined MODIS Terra+Aqua (Vadrevu et al. 2018)."
        ),
        bbox=(73.8, 29.5, 76.9, 32.6),
        clusters=(
            Cluster("amritsar", 31.5, 74.9, 0.3, 16.0, "cropland"),
            Cluster("ludhiana", 30.9, 75.8, 0.3, 15.0, "cropland"),
            Cluster("patiala", 30.4, 76.4, 0.22, 13.0, "cropland"),
            Cluster("bathinda", 30.2, 74.9, 0.18, 15.0, "cropland"),
        ),
        base_daily_events=60.0,
        seasonality=((302, 14, 0.85), (118, 14, 0.15)),
        year_factors={2016: 1.25, 2021: 1.35, 2023: 1.3},
        trend_per_year=-0.008,
        det_prob_modis=(0.03, 0.32, 0.95),
        det_prob_viirs=(0.45, 0.93, 1.0),
        frp_lognorm=(1.1, 0.8),
        saturation_prob_viirs=0.01,
        cloud_seasonality=((210, 30, 0.7),),
        static_source_cells=((30.75, 76.15),),
        equatorial_gap_factor=0.15,
        size_probs=(0.9, 0.09, 0.01),
        day_fraction=0.6,
    ),
    _p(
        aoi_id="california-sierra",
        name="Sierra Nevada & N. California, USA",
        description=(
            "Summer-autumn wildfire regime with documented mega-fire years "
            "(2018 Carr, 2020 August Complex, 2021 Dixie). Low event counts, "
            "high per-pixel FRP, large medium/large fire share."
        ),
        bbox=(-124.4, 36.9, -118.9, 41.9),
        clusters=(
            Cluster("klamath", 41.3, -123.2, 0.3, 22.0, "forest"),
            Cluster("sierra-central", 37.8, -119.6, 0.28, 25.0, "forest"),
            Cluster("mendocino", 39.3, -123.1, 0.22, 18.0, "forest"),
            Cluster("sacramento-valley", 39.1, -122.0, 0.2, 14.0, "cropland"),
        ),
        base_daily_events=1.4,
        seasonality=((255, 35, 0.9),),
        year_factors={2015: 1.5, 2018: 1.9, 2020: 2.3, 2021: 1.9, 2024: 1.4},
        trend_per_year=0.02,
        det_prob_modis=(0.05, 0.55, 1.0),
        det_prob_viirs=(0.5, 0.95, 1.0),
        frp_lognorm=(2.6, 1.1),
        saturation_prob_viirs=0.22,
        cloud_seasonality=((350, 40, 0.6),),
        static_source_cells=((40.5, -122.4),),
        equatorial_gap_factor=0.1,
        size_probs=(0.45, 0.35, 0.2),
        day_fraction=0.5,
    ),
    _p(
        aoi_id="borneo-peatlands",
        name="Central Kalimantan Peatlands, Indonesia",
        description=(
            "Peatland burning with strong ENSO teleconnection: extreme "
            "seasons in 2015 and 2023 (documented). Substantial night "
            "detections from smouldering peat; FRP moderate, duration long."
        ),
        bbox=(110.5, -3.6, 115.5, 0.5),
        clusters=(
            Cluster("pulangpisau", -2.4, 114.1, 0.34, 20.0, "peatland"),
            Cluster("palangkaraya-south", -2.6, 113.9, 0.26, 16.0, "peatland"),
            Cluster("kotawaringin", -2.5, 111.7, 0.24, 22.0, "peatland"),
            Cluster("kapuas", -2.9, 114.6, 0.16, 15.0, "peatland"),
        ),
        base_daily_events=4.0,
        seasonality=((255, 28, 0.92),),
        year_factors={2014: 1.4, 2015: 3.4, 2019: 2.6, 2023: 2.9},
        trend_per_year=0.0,
        det_prob_modis=(0.04, 0.4, 0.98),
        det_prob_viirs=(0.48, 0.94, 1.0),
        frp_lognorm=(1.8, 1.0),
        saturation_prob_viirs=0.06,
        cloud_seasonality=((20, 35, 0.75),),  # wet season suppresses detections
        static_source_cells=((-2.65, 114.42),),  # industrial site away from fire clusters
        equatorial_gap_factor=0.45,
        size_probs=(0.62, 0.3, 0.08),
        day_fraction=0.45,
    ),
    _p(
        aoi_id="pantanal-brazil",
        name="Pantanal, Brazil",
        description=(
            "Wetland-savanna burning concentrated Aug-Sep; 2020 is the "
            "documented record mega-fire season. Mixed medium fires with "
            "moderate FRP."
        ),
        bbox=(-59.2, -20.2, -54.9, -15.5),
        clusters=(
            Cluster("pocone", -16.3, -56.6, 0.3, 24.0, "savanna"),
            Cluster("corumba-north", -17.8, -57.4, 0.26, 26.0, "savanna"),
            Cluster("nabileque", -19.2, -57.1, 0.24, 22.0, "savanna"),
            Cluster("cacique", -18.5, -55.4, 0.2, 20.0, "savanna"),
        ),
        base_daily_events=3.2,
        seasonality=((235, 24, 0.94),),
        year_factors={2019: 1.5, 2020: 3.1, 2021: 1.7, 2024: 1.6},
        trend_per_year=0.015,
        det_prob_modis=(0.05, 0.5, 0.99),
        det_prob_viirs=(0.5, 0.95, 1.0),
        frp_lognorm=(2.0, 1.0),
        saturation_prob_viirs=0.1,
        cloud_seasonality=((330, 40, 0.7),),
        static_source_cells=((-16.95, -57.95),),  # industrial site away from fire clusters
        equatorial_gap_factor=0.3,
        size_probs=(0.55, 0.33, 0.12),
        day_fraction=0.5,
    ),
    _p(
        aoi_id="mediterranean-greece",
        name="Aegean & mainland Greece",
        description=(
            "Short, wind-driven Mediterranean fire season (Jul-Aug) with "
            "documented extreme years 2021 (Varibobi) and 2023 "
            "(Alexandroupoli). High peak FRP, fast episodes."
        ),
        bbox=(20.0, 34.8, 26.6, 41.2),
        clusters=(
            Cluster("attica", 38.1, 23.8, 0.26, 14.0, "mediterranean"),
            Cluster("peloponnese", 37.3, 22.2, 0.26, 18.0, "mediterranean"),
            Cluster("evros-north", 41.0, 25.6, 0.24, 18.0, "mediterranean"),
            Cluster("thessaly", 39.3, 22.4, 0.24, 16.0, "mediterranean"),
        ),
        base_daily_events=1.1,
        seasonality=((205, 18, 0.95),),
        year_factors={2021: 1.8, 2023: 2.4, 2024: 1.3},
        trend_per_year=0.01,
        det_prob_modis=(0.05, 0.5, 1.0),
        det_prob_viirs=(0.5, 0.95, 1.0),
        frp_lognorm=(2.4, 1.1),
        saturation_prob_viirs=0.18,
        cloud_seasonality=((20, 40, 0.6),),
        static_source_cells=((37.97, 23.7),),
        equatorial_gap_factor=0.12,
        size_probs=(0.5, 0.35, 0.15),
        day_fraction=0.55,
    ),
]

PROFILES_BY_ID = {p.aoi_id: p for p in PRESET_PROFILES}
