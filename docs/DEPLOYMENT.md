# Public deployment

This guide covers an anonymous, view-only EmberEye demo on one Linux host. It is not a managed hosting recipe or an operational wildfire warning service. Use Docker Compose v2.24 or later because the public overrides use Compose merge tags.

## Before opening the host to visitors

GitHub repository visibility and GHCR package visibility are separate. When you are ready to publish, make the repository public and set both `embereye-backend` and `embereye-frontend` packages to **Public** in GitHub Packages. A public package can be pulled anonymously; a private package needs a classic GitHub PAT with `read:packages`. [1] [2]

The app has no user accounts. The public profile forces `PUBLIC_READ_ONLY=true`, rejects API writes, disables write controls in the UI, does not publish the FastAPI port, and binds the frontend only to loopback for Caddy. Nginx limits `/api/` to 20 requests per second per client with a burst of 40. The documented Caddy proxy sanitizes `X-Forwarded-For`, which Nginx uses for that limit. Nginx also sends `nosniff`, frame, referrer, and permissions headers. Content Security Policy is currently **report-only**, not enforced, until MapLibre and ECharts violations are reviewed after deployment. Read-only does not prevent resource exhaustion from expensive GET requests; use a host firewall and, for a large audience, an edge WAF or CDN. Do not expose Docker, PostgreSQL, or the backend port to the internet. [5]

The v1.0.8 offline archive is mixed: 171,855 real NASA FIRMS detections for the Nile Delta and synthetic demo rows for the other AOIs. The MODIS-only period before VIIRS is model-dependent. FIRMS NRT observations are provisional. EmberEye is not a forecast or authoritative incident-alert system; preserve the in-app provenance and uncertainty labels.

## PostgreSQL seeded demo

Point a DNS name, for example `fire.example.org`, to the Linux host. Install Docker Engine with the Compose plugin and Caddy using its [official instructions](https://caddyserver.com/docs/install). Allow inbound TCP ports 80 and 443 only. [3] [4]

Once the selected GHCR release has been published, clone the repository and prepare the public environment file:

```bash
git clone https://github.com/3lyly0/embereye-spaceapps.git
cd embereye-spaceapps
cp .env.public.example .env
openssl rand -hex 32
```

Edit `.env`: set `POSTGRES_PASSWORD` to the generated secret, set `PUBLIC_CORS_ORIGINS` to the exact HTTPS origin (for example `https://fire.example.org`, with no trailing slash), keep `DATA_MODE=SEED`, and pin `EMBEREYE_IMAGE_TAG` to the release you actually published. Do not copy the development `.env.example` values to a public host, and never commit `.env`.

Validate the merged configuration, then pull and start the images:

```bash
docker compose -f docker-compose.yml -f docker-compose.public.yml config
docker compose -f docker-compose.yml -f docker-compose.public.yml pull
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --wait
docker compose -f docker-compose.yml -f docker-compose.public.yml ps
curl -fsS http://127.0.0.1:8080/api/health
```

The one-shot seeder must finish successfully before the backend starts. The API health check must pass before the frontend starts. If the seed job fails, inspect it rather than bypassing the dependency:

```bash
docker compose -f docker-compose.yml -f docker-compose.public.yml logs --tail=150 seeder backend
```

Configure the host's active Caddyfile to proxy only to the loopback-bound frontend port:

```caddyfile
fire.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Reload Caddy with `sudo systemctl reload caddy`. Caddy obtains and renews HTTPS certificates when DNS points to this host and ports 80/443 are reachable. The public site is `https://fire.example.org`; `/api/` is proxied internally by the frontend Nginx. Do not add a separate public route to FastAPI or PostgreSQL.

To update later, change `EMBEREYE_IMAGE_TAG` in `.env`, then rerun `pull` and `up -d --wait` with the same two Compose files. Keep the PostgreSQL volume backed up; `docker compose down -v` deletes it.

## Read-only SQLite archive

This option serves the downloadable mixed v1.0.8 archive instead of creating a new synthetic seed. Download it after the repository/release is public:

```bash
mkdir -p data
curl -fL -o data/embereye_dev.db \
  https://github.com/3lyly0/embereye-spaceapps/releases/download/v1.0.8-offline-data/embereye_dev.db
sha256sum data/embereye_dev.db
```

If the repository is still private, download the same asset through an authenticated GitHub session; do not put a PAT in the URL or in `.env`. Confirm the checksum against the GitHub Release asset digest before serving the file.

Copy `.env.sqlite.public.example` to `.env`, set `PUBLIC_CORS_ORIGINS` to the exact HTTPS site origin, and confirm `EMBEREBEYE_SQLITE_HOST_PATH` points to the downloaded database. The public SQLite profile mounts the file `:ro`, runs the backend as the unprivileged `embereye` user with a read-only container filesystem, and opens SQLite in URI read-only mode. Public read-only startup deliberately skips schema creation/migrations, so the archive must already be a complete, initialized EmberEye database. Do not use the separate `docker-compose.sqlite.yml` read-write development profile on a public host.

```bash
# Check that the resolved profile uses a read-only SQLite URL and mount.
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml \
  -f docker-compose.sqlite.public.yml config

# Pull and start; only the frontend is bound to 127.0.0.1:8080.
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml \
  -f docker-compose.sqlite.public.yml pull
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml \
  -f docker-compose.sqlite.public.yml up -d --wait
curl -fsS http://127.0.0.1:8080/api/health
```

Caddy uses the same `reverse_proxy 127.0.0.1:8080` block described above. Keep a separate backup of the SQLite file and replace it only while the stack is stopped.

## References

[1]: https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility "GitHub Docs: Configuring package access control and visibility"
[2]: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry "GitHub Docs: Working with the container registry"
[3]: https://docs.docker.com/compose/how-tos/production/ "Docker Docs: Use Compose in production"
[4]: https://caddyserver.com/docs/quick-starts/reverse-proxy "Caddy Docs: Reverse proxy quick-start"
[5]: https://caddyserver.com/docs/caddyfile/directives/reverse_proxy "Caddy Docs: Reverse proxy headers"
