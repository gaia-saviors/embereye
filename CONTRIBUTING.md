# Contributing to EmberEye

Thanks for taking the time to improve EmberEye. This project is developed by
**Team Gaia Saviors** for the NASA Space Apps Challenge 2026, but the
contribution process is the same as any open-source repository.

## Code of conduct

By participating you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).
Be respectful, assume good faith, and keep technical disagreements technical.

## Getting set up

```bash
git clone https://github.com/3lyly0/embereye-spaceapps.git
cd embereye-spaceapps
cp .env.example .env

# Backend (Python 3.11)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt pytest httpx

# Frontend (Node 20)
cd frontend && npm ci && cd ..
```

Run the suite before opening a pull request:

```bash
python -m pytest -q                # backend + pipeline tests
cd frontend && npx tsc --noEmit && npm run build
```

The full multi-container stack is available with `docker compose up --build`
(see `README.md`).

## What to work on

- **Bugs and data-quality issues** — include the AOI, date range, sensor, and
  the exact API response or CSV row. Scientific claims need evidence.
- **Methodology changes** — open an issue first. Changes to transfer
  functions, deduplication, or climatology must update
  `docs/METHODOLOGY.md`, `docs/ADR.md`, and the `CITATIONS`/`LIMITATIONS`
  registries in `backend/app/harmonize/pipeline.py`.
- **UI changes** — keep the Warm Basalt design tokens, keyboard
  accessibility, and the existing responsive breakpoints.

## Pull request checklist

- [ ] Tests pass locally (`python -m pytest -q`).
- [ ] Frontend type-checks and builds (`npx tsc --noEmit`, `npm run build`).
- [ ] New behaviour is documented (README/docstring/changelog as appropriate).
- [ ] No secrets, API keys, database dumps, or raw agency data committed.
- [ ] Commits are focused and use conventional messages
      (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- [ ] Upstream data attribution remains intact (see `docs/DATA_SOURCES.md`).

## Data and citation rules

EmberEye uses NASA FIRMS data. Do not commit downloaded FIRMS archives, and
do not present harmonized outputs as official NASA products. Any new data
source must be added to `docs/DATA_SOURCES.md` with provider, product,
license, and citation before it is merged.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
