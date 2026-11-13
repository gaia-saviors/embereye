# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | ✅ |
| < 1.0 | ❌ |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/3lyly0/embereye-spaceapps/security/advisories/new)
for this repository, or contact the maintainer ([@3lyly0](https://github.com/3lyly0))
directly.

Include:

- Affected component and version/commit.
- Reproduction steps or a proof of concept.
- Impact assessment (data exposure, remote code execution, denial of service).
- Any suggested remediation.

You can expect an acknowledgement within 72 hours and a remediation plan
within 7 days. Please allow a coordinated disclosure window before publishing
details.

## Scope

In scope: the FastAPI service, React frontend, container images, CI workflow,
and ingestion/backfill tooling in this repository.

Out of scope: vulnerabilities in upstream dependencies (report them to the
upstream project), and issues that require an already-compromised host.

## Handling secrets and data

- `FIRMS_MAP_KEY` and database credentials belong in `.env` (gitignored) or
  your deployment secret store — never in commits, issues, or screenshots.
- Do not attach raw FIRMS archives, `embereye_dev.db`, or `data/` contents to
  reports; describe the shape of the data instead.
- If a key is exposed, rotate it at
  https://firms.modaps.eosdis.nasa.gov/api/map_key/ immediately.
