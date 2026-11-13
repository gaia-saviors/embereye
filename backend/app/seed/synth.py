"""Deterministic synthetic active-fire archive generator.

Simulates TRUE fire events, then samples each satellite's detection process
against them. Fixed-seed RNG (crc32 of seed_version + aoi_id) makes the
archive byte-stable across runs, hosts and compose rebuilds - the "auto
seeding" that lets the platform run fully offline with realistic,
internally-consistent sensor records.

Physics encoded:
  * overpass schedule: Terra 10:30/22:30, Aqua 13:30/01:30, S-NPP & NOAA-20
    13:30/01:30 local solar (NOAA-20 +50 min offset), NOAA-21 from 2024
  * MODIS 1 km vs VIIRS 375 m sensitivity gap -> published count ratios
  * MODIS equatorial swath gaps (2,330 km swath) vs VIIRS full coverage
  * cloud/dust suppression with seasonal cycle
  * VIIRS I4 saturation (>=367 K) on intense pixels; MODIS band-22
    saturation (>=331 K) -> band-21 fallback flag
  * confidence: MODIS numeric 0-100, VIIRS categorical l/n/h
  * gas-flare / industrial persistent cells -> static-source mask exercise
"""
from __future__ import annotations

import math
import zlib
from datetime import date, datetime, timedelta

import numpy as np

from ..ingest.canonical import (
    MODIS_BAND22_SATURATION_K,
    SENSOR_MODIS,
    SENSOR_VIIRS,
    VIIRS_I4_SATURATION_K,
    CanonicalRecord,
    local_calendar_day,
    pixel_area_km2,
)
from .profiles import RegionProfile

OVERPASSES = {
    # satellite: (list of local-solar-pass hours, day_fraction_hours window)
    "Terra": (10.5, 22.5),
    "Aqua": (13.5, 1.5),
    "SNPP": (13.5, 1.5),
    "N20": (14.33, 2.33),
    "N21": (13.5, 1.5),
}
VIIRS_SATELLITES = ("SNPP", "N20", "N21")
# Physical fire-event footprint radius (km) by size class: small residue
# fires ~0.3 km, medium fires ~0.8 km, large wildfires ~2.0 km. Pixel
# positions jitter within this radius so consecutive-day detections of the
# same event are spatially chainable (episode clustering).
EVENT_RADIUS_KM = (0.3, 0.8, 2.0)
N20_OFFSET_MIN = 50
N21_START = date(2023, 3, 1)   # NOAA-21 operational data start
MODIS_SATELLITES = ("Terra", "Aqua")


def rng_for(seed_version: str, aoi_id: str) -> np.random.Generator:
    seed = zlib.crc32(f"{seed_version}|{aoi_id}".encode("utf-8"))
    return np.random.default_rng(seed)


def _doy(d: date) -> int:
    return d.timetuple().tm_yday


def _seasonality(profile: RegionProfile, doy: int) -> float:
    total = 0.0
    for peak, sigma, weight in profile.seasonality:
        d = min(abs(doy - peak), 366 - abs(doy - peak))
        total += weight * math.exp(-0.5 * (d / sigma) ** 2)
    return total


def _cloud_factor(profile: RegionProfile, doy: int, rng: np.random.Generator) -> float:
    base = 0.0
    for peak, sigma, weight in profile.cloud_seasonality:
        d = min(abs(doy - peak), 366 - abs(doy - peak))
        base += weight * math.exp(-0.5 * (d / sigma) ** 2)
    cloud = float(np.clip(base + rng.uniform(0.0, 0.35), 0.0, 0.85))
    return 1.0 - cloud  # clear-sky fraction


def _event_count(profile: RegionProfile, day: date, rng: np.random.Generator, scale: float) -> int:
    years_in = (day.year - 2012) + (day.timetuple().tm_yday / 366.0)
    trend = 1.0 + profile.trend_per_year * years_in
    lam = (
        profile.base_daily_events
        * _seasonality(profile, _doy(day))
        * profile.year_factors.get(day.year, 1.0)
        * trend
        * scale
    )
    return int(rng.poisson(lam))


def _lon_to_utc_hours(local_hour: float, lon: float) -> float:
    return (local_hour - lon / 15.0) % 24.0


def _overpass_datetime(day: date, local_hour: float, lon: float, jitter_min: int) -> datetime:
    utc_h = _lon_to_utc_hours(local_hour, lon)
    base = datetime(day.year, day.month, day.day) + timedelta(hours=utc_h)
    return base + timedelta(minutes=jitter_min)


def _is_day(local_hour: float) -> bool:
    return 7.0 <= local_hour < 19.0


def _confidence_modis(rng: np.random.Generator, size_class: int) -> float:
    """MODIS numeric confidence, skewed by fire size (Giglio 2016 behaviour)."""
    if size_class == 2:
        return float(np.clip(rng.beta(8, 1.4) * 100, 35, 100))
    if size_class == 1:
        return float(np.clip(rng.beta(4.5, 3.2) * 100, 5, 100))
    return float(np.clip(rng.beta(2.2, 5.5) * 100, 0, 90))


def _confidence_viirs(rng: np.random.Generator, size_class: int) -> str:
    """VIIRS categorical confidence mixture (l/n/h) by detection SNR proxy."""
    if size_class == 2:
        roll = rng.random()
        return "LOW" if roll < 0.05 else ("NOMINAL" if roll < 0.45 else "HIGH")
    if size_class == 1:
        roll = rng.random()
        return "LOW" if roll < 0.25 else ("NOMINAL" if roll < 0.85 else "HIGH")
    roll = rng.random()
    return "LOW" if roll < 0.55 else ("NOMINAL" if roll < 0.95 else "HIGH")


def _jitter_km(rng: np.random.Generator, spread_km: float) -> tuple[float, float]:
    dlat = rng.normal(0.0, spread_km / 111.195)
    dlon = rng.normal(0.0, spread_km / (111.195 * 0.85))
    return dlat, dlon


def generate_aoi_records(
    profile: RegionProfile,
    aoi_id: str,
    start: date,
    end: date,
    scale: float,
    seed_version: str,
) -> list[CanonicalRecord]:
    """Generate the canonical record stream for one AOI. Fully deterministic."""
    rng = rng_for(seed_version, aoi_id)
    west, south, east, north = profile.bbox
    if not profile.clusters:
        # No ignition clusters means no fire events can be placed; return the
        # (empty) stream instead of letting numpy choke on choice(a=0).
        return []
    weights = np.array([c.weight for c in profile.clusters], dtype=np.float64)
    weights /= weights.sum()
    records: list[CanonicalRecord] = []

    day = start
    while day <= end:
        n_events = _event_count(profile, day, rng, scale)
        if n_events == 0:
            day += timedelta(days=1)
            continue
        clear = _cloud_factor(profile, _doy(day), rng)
        modis_gap = rng.random() < profile.equatorial_gap_factor

        for _ in range(n_events):
            ci = int(rng.choice(len(profile.clusters), p=weights))
            cluster = profile.clusters[ci]
            dlat, dlon = _jitter_km(rng, cluster.spread_km)
            ev_lat = float(np.clip(cluster.lat + dlat, south, north))
            ev_lon = float(np.clip(cluster.lon + dlon, west, east))
            size_class = int(rng.choice(3, p=profile.size_probs))
            duration = int(np.clip(rng.lognormal(0.4, 0.6), 1, 12 if size_class < 2 else 25))
            mu, sigma = profile.frp_lognorm
            size_mult = (0.4, 1.0, 4.5)[size_class]
            end_day = min(end, day + timedelta(days=duration - 1))

            for det_day in _drange(day, end_day):
                if det_day > end:
                    break
                decay = 1.0 - 0.55 * (det_day - day).days / max(1, duration)
                # ---- VIIRS detections (SNPP 2012+, N20 2018+, N21 2024+) ----
                # Two passes per satellite per day (13:30 / 01:30 local solar),
                # mirroring the MODIS Terra/Aqua day+night schedule.
                for sat in VIIRS_SATELLITES:
                    if sat == "SNPP" and (det_day < date(2012, 1, 20) or det_day >= date(2026, 11, 1)):
                        continue  # S-NPP record: 2012-01-20 to 2026-10-31
                    if sat == "N20" and det_day < date(2018, 4, 1):
                        continue
                    if sat == "N21" and det_day < N21_START:
                        continue
                    for local_hour in OVERPASSES[sat]:
                        night = not _is_day(local_hour)
                        p = profile.det_prob_viirs[size_class] * clear
                        if night:
                            p *= 1.05  # peat/night smouldering slightly favoured
                        if rng.random() > p:
                            continue
                        n_px = {0: (1, 4), 1: (3, 14), 2: (12, 60)}[size_class]
                        count = int(rng.integers(n_px[0], n_px[1] + 1))
                        if count <= 0:
                            continue
                        records.extend(_viirs_pixels(
                            rng, profile, aoi_id, det_day, sat, local_hour,
                            ev_lat, ev_lon, mu, sigma, size_mult, decay, count,
                            EVENT_RADIUS_KM[size_class], size_class,
                        ))
                # ---- MODIS detections (Terra + Aqua) ----
                if modis_gap:
                    continue  # swath gap on this day for the AOI latitude
                for sat in MODIS_SATELLITES:
                    if sat == "Terra" and det_day < date(2000, 11, 2):
                        continue
                    if sat == "Aqua" and det_day < date(2002, 7, 4):
                        continue
                    for hour in OVERPASSES[sat]:
                        night = not _is_day(hour)
                        p = profile.det_prob_modis[size_class] * clear * (0.6 if night else 1.0)
                        if rng.random() > p:
                            continue
                        n_px = {0: (0, 1), 1: (1, 4), 2: (4, 16)}[size_class]
                        count = int(rng.integers(n_px[0], n_px[1] + 1))
                        if count <= 0:
                            continue
                        records.extend(_modis_pixels(
                            rng, profile, aoi_id, det_day, sat, hour, ev_lat, ev_lon,
                            mu, sigma, size_mult, decay, count,
                            max(EVENT_RADIUS_KM[size_class] * 2.0, 1.2),
                            size_class,
                        ))
        day += timedelta(days=1)

    records.extend(_static_sources(profile, aoi_id, start, end, seed_version))
    records.sort(key=lambda r: (r.acq_dt_utc, r.lat, r.lon))
    return records


def _drange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _viirs_pixels(
    rng, profile, aoi_id, det_day, sat, local_hour, ev_lat, ev_lon,
    mu, sigma, size_mult, decay, count, spread_km, size_class,
) -> list[CanonicalRecord]:
    out: list[CanonicalRecord] = []
    jitter_min = int(rng.integers(-25, 26))
    if sat == "N20":
        jitter_min += N20_OFFSET_MIN
    dt = _overpass_datetime(det_day, local_hour, ev_lon, jitter_min)
    for _ in range(count):
        lat_j, lon_j = _jitter_km(rng, spread_km / 2.0)
        frp = float(rng.lognormal(mu + math.log(max(size_mult * decay, 0.05)), sigma))
        saturated = rng.random() < (profile.saturation_prob_viirs if size_class == 2 else 0.0)
        t4 = 340.0 + rng.gamma(2.0, 12.0) + (60.0 if saturated else 0.0)
        if saturated:
            t4 = max(t4, VIIRS_I4_SATURATION_K + rng.uniform(0, 8))
        conf = _confidence_viirs(rng, size_class)
        out.append(CanonicalRecord(
            aoi_id=aoi_id, sensor_family=SENSOR_VIIRS, satellite=sat,
            instrument="VIIRS", resolution_m=375,
            acq_dt_utc=dt, acq_date=det_day,
            local_date=local_calendar_day(dt, ev_lon + lon_j),
            lat=float(np.clip(ev_lat + lat_j, -89.9, 89.9)),
            lon=float((ev_lon + lon_j + 180.0) % 360.0 - 180.0),
            brightness_t4_k=round(float(t4), 1),
            brightness_t11_k=round(float(300.0 + rng.gamma(3.0, 4.0)), 1),
            frp_mw=round(frp, 2),
            confidence_class=conf, confidence_raw=None,
            daynight="D" if _is_day(local_hour) else "N",
            scan_km=0.5, track_km=0.5, pixel_area_km2=round(pixel_area_km2(0.5, 0.5), 4),
            saturated=saturated, band21_fallback=False,
            source_version="SYNTH-2.1",
        ))
    return out


def _modis_pixels(
    rng, profile, aoi_id, det_day, sat, hour, ev_lat, ev_lon,
    mu, sigma, size_mult, decay, count, spread_km, size_class,
) -> list[CanonicalRecord]:
    out: list[CanonicalRecord] = []
    jitter_min = int(rng.integers(-15, 16))
    dt = _overpass_datetime(det_day, hour, ev_lon, jitter_min)
    for _ in range(count):
        lat_j, lon_j = _jitter_km(rng, spread_km)
        frp = float(rng.lognormal(mu + math.log(max(size_mult * decay, 0.05)) - 0.55, sigma))
        vza_growth = float(rng.uniform(1.0, 3.4))  # scan-edge pixel growth 1->9.6 km^2
        scan = round(1.0 * vza_growth, 2)
        track = round(1.0 * (1.0 + 0.55 * (vza_growth - 1.0)), 2)
        t4 = 320.0 + rng.gamma(2.2, 18.0)
        b21 = t4 >= MODIS_BAND22_SATURATION_K
        if b21:
            t4 = min(t4 + rng.uniform(0, 60), 495.0)
        out.append(CanonicalRecord(
            aoi_id=aoi_id, sensor_family=SENSOR_MODIS, satellite=sat,
            instrument="MODIS", resolution_m=1000,
            acq_dt_utc=dt, acq_date=det_day,
            local_date=local_calendar_day(dt, ev_lon + lon_j),
            lat=float(np.clip(ev_lat + lat_j, -89.9, 89.9)),
            lon=float((ev_lon + lon_j + 180.0) % 360.0 - 180.0),
            brightness_t4_k=round(float(t4), 1),
            brightness_t11_k=round(float(298.0 + rng.gamma(3.0, 4.0)), 1),
            frp_mw=round(frp, 2),
            confidence_class="NOMINAL",
            confidence_raw=round(_confidence_modis(rng, size_class), 0),
            daynight="D" if _is_day(hour) else "N",
            scan_km=scan, track_km=track,
            pixel_area_km2=round(pixel_area_km2(scan, track), 4),
            saturated=False, band21_fallback=b21,
            source_version="SYNTH-2.1",
        ))
    return out


def _static_sources(
    profile: RegionProfile, aoi_id: str, start: date, end: date, seed_version: str,
) -> list[CanonicalRecord]:
    """Persistent gas-flare / industrial cells: detected on both sensors on
    ~every sampled pass, day and night, with thermally stable signatures ->
    the static-source mask flags them; strict counting excludes them."""
    rng = rng_for(seed_version + "|static", aoi_id)
    out: list[CanonicalRecord] = []
    span_days = max(1, (end - start).days)
    step = max(2, span_days // 1400)
    d = start
    while d <= end:
        if rng.random() < 0.9:
            for lat, lon in profile.static_source_cells:
                # --- VIIRS pass ---
                if d >= date(2012, 1, 20):
                    if d >= date(2026, 11, 1):
                        sat = "N20"  # S-NPP NRT ceased (FIRMS notice)
                    elif d >= date(2018, 4, 1):
                        sat = "SNPP" if rng.random() < 0.5 else "N20"
                    else:
                        sat = "SNPP"
                    local_hour = 1.5 if rng.random() < 0.55 else 13.5
                    dt = _overpass_datetime(d, local_hour, lon, int(rng.integers(-20, 21)))
                    out.append(CanonicalRecord(
                        aoi_id=aoi_id, sensor_family=SENSOR_VIIRS, satellite=sat,
                        instrument="VIIRS", resolution_m=375,
                        acq_dt_utc=dt, acq_date=d, local_date=local_calendar_day(dt, lon),
                        lat=round(lat + rng.normal(0, 0.004), 5),
                        lon=round(lon + rng.normal(0, 0.004), 5),
                        brightness_t4_k=round(352.0 + rng.normal(0, 1.5), 1),
                        brightness_t11_k=round(303.0 + rng.normal(0, 1.0), 1),
                        frp_mw=round(float(abs(rng.normal(18, 3))), 2),
                        confidence_class="HIGH", confidence_raw=None,
                        daynight="D" if _is_day(local_hour) else "N",
                        scan_km=0.5, track_km=0.5, pixel_area_km2=0.25,
                        saturated=False, band21_fallback=False, source_version="SYNTH-2.1",
                        firms_type=2, static_source=True,
                    ))
                # --- MODIS pass ---
                if d >= date(2000, 11, 2):
                    msat = "Terra" if rng.random() < 0.5 or d < date(2002, 7, 4) else "Aqua"
                    m_local_hour = 22.5 if rng.random() < 0.55 else 10.5
                    mdt = _overpass_datetime(d, m_local_hour, lon, int(rng.integers(-15, 16)))
                    out.append(CanonicalRecord(
                        aoi_id=aoi_id, sensor_family=SENSOR_MODIS, satellite=msat,
                        instrument="MODIS", resolution_m=1000,
                        acq_dt_utc=mdt, acq_date=d, local_date=local_calendar_day(mdt, lon),
                        lat=round(lat + rng.normal(0, 0.01), 5),
                        lon=round(lon + rng.normal(0, 0.01), 5),
                        brightness_t4_k=round(336.0 + rng.normal(0, 2.0), 1),
                        brightness_t11_k=round(304.0 + rng.normal(0, 1.0), 1),
                        frp_mw=round(float(abs(rng.normal(14, 3))), 2),
                        confidence_class="HIGH", confidence_raw=92.0,
                        daynight="D" if _is_day(m_local_hour) else "N",
                        scan_km=1.0, track_km=1.0, pixel_area_km2=1.0,
                        saturated=False, band21_fallback=True, source_version="SYNTH-2.1",
                        firms_type=2, static_source=True,
                    ))
        d += timedelta(days=step)
    return out
