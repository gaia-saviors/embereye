# Space Agency Data Sources & Attribution

EmberEye is built exclusively on **open NASA Earth-observation data**. This
document records every upstream product, its provider, the access route used
by the code, and the citation that must accompany downstream use.

> **Scope note.** The current release uses **NASA FIRMS** data only. No ESA
> (Sentinel/CCI), JAXA, or other agency product is ingested, derived from, or
> redistributed. If that changes, this file is the single place to update.

---

## 1. Primary source — NASA FIRMS

| Field | Value |
|---|---|
| Provider | NASA Earth Science Data and Information System (ESDIS) / LANCE |
| Service | Fire Information for Resource Management System (FIRMS) |
| Base URL | https://firms.modaps.eosdis.nasa.gov |
| API area endpoint | `/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}/{START_DATE}[/{END_DATE}]` |
| Access key | Free self-service: https://firms.modaps.eosdis.nasa.gov/api/map_key/ |
| Code entry point | `backend/app/ingest/firms_client.py` |
| Backfill tool | `scripts/backfill_firms.py` |
| Terms of use | https://www.earthdata.nasa.gov/about/esdis/eosdis/firms (see also the FIRMS "Data Use" page) |

### Products ingested

| Product code | Sensor family | Platforms | Resolution | Reference |
|---|---|---|---|---|
| `MODIS_SP` / `MODIS_NRT` | MODIS Collection 6.1 active fire | Terra, Aqua | 1 km | Giglio, Schroeder & Justice (2016) |
| `VIIRS_SNPP_SP` / `_NRT` | VIIRS 375 m active fire (I-band) | Suomi-NPP | 375 m | Schroeder et al. (2014) |
| `VIIRS_NOAA20_SP` / `_NRT` | VIIRS 375 m active fire | NOAA-20 | 375 m | Schroeder et al. (2014) |
| `VIIRS_NOAA21_SP` / `_NRT` | VIIRS 375 m active fire | NOAA-21 | 375 m | Schroeder et al. (2014) |

Standard (`SP`) and near-real-time (`NRT`) products share the canonical schema
in `backend/app/ingest/canonical.py`; every row keeps its `firms_product`
provenance tag so derived statistics can distinguish archived science data
from provisional NRT tails.

---

## 2. Required citations

Any publication, demo, or redistribution that uses EmberEye outputs should
cite the upstream products **and** this repository:

1. **Giglio, L., Schroeder, W., & Justice, C. O. (2016).** The Collection 6
   MODIS active fire detection algorithm and fire products. *Remote Sensing of
   Environment*, 178, 31–43. https://doi.org/10.1016/j.rse.2016.02.054
2. **Schroeder, W., Oliva, P., Giglio, L., & Csiszar, I. A. (2014).** The New
   VIIRS 375 m Active Fire Detection Data Product: Algorithm Description and
   Initial Assessment. *Remote Sensing of Environment*, 143, 85–96.
   https://doi.org/10.1016/j.rse.2013.12.008
3. **NASA FIRMS.** Fire Information for Resource Management System.
   NASA ESDIS. https://firms.modaps.eosdis.nasa.gov (accessed 2026).
4. **Team Gaia Saviors (2026).** *EmberEye — Harmonization of MODIS & VIIRS
   Hot Spots.* NASA Space Apps Challenge 2026, Cairo.
   https://github.com/3lyly0/embereye-spaceapps

The in-app citation registry is served by `GET /api/analytics/methods` and by
`GET /api/meta` (`citations`), sourced from `CITATIONS` in
`backend/app/harmonize/pipeline.py`.

Additional methodological anchors (FRP retrieval, inter-sensor comparison,
episode tracking) are listed in `README.md` and `docs/METHODOLOGY.md`.

---

## 3. Provenance and honesty rules

- **Live vs. seed is always labelled.** `GET /api/meta` reports
  `FIRMS_ARCHIVE`, `SEED`, or `MIXED`, and the UI surfaces the same badge.
  Synthetic rows carry an empty `firms_product`; real rows carry `SP`/`NRT`.
- **No raw agency data is committed** to this repository. The SQLite archive
  (`embereye_dev.db`) and `data/` are runtime artifacts and are gitignored
  (see `docs/DATA.md`). Rebuild with `scripts/backfill_firms.py` (requires a
  free `FIRMS_MAP_KEY`) or use the deterministic offline seeder.
- **No rebadging.** Derivative series are labelled *harmonized /
  VIIRS-equivalent*, never presented as an official NASA product.
- **Attribution travels with exports.** CSV/GeoJSON exports include the
  `firms_product` and `satellite` columns; the API metadata endpoint exposes
  the citation list for downstream consumers.

---

## 4. Licensing of upstream data

NASA Earth-observation data are generally free and open, subject to NASA's
data-use guidance and the FIRMS terms of use. EmberEye's MIT license
(`LICENSE`) covers **the software**, not the upstream observations. When
redistributing derived data, retain the citations above and comply with the
provider's current terms.
