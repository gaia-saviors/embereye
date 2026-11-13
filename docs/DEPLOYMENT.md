# Public deployment

This guide is a minimal single-Linux-host deployment for the GHCR images. It is not a cloud-provider-specific recipe or a substitute for application security review.

## Before making the project public

The repository and the two GHCR packages have separate visibility settings. Making the repository public does **not** automatically make the container packages public. Before submission, change the visibility of both `embereye-backend` and `embereye-frontend` to **Public** in their GitHub Packages settings. Public GHCR container packages can then be pulled anonymously. While a package remains private, use a classic GitHub PAT with `read:packages` to pull it. [1] [2]

The project currently has no user authentication or API rate limiting. Its API includes unauthenticated AOI creation, deletion, rebuild, and live-fetch routes. Binding the backend and database ports to loopback protects those ports from direct internet access, but the frontend still proxies `/api`; it does **not** make those write routes read-only. Before advertising an unrestricted public demo, add an application read-only/authentication control and rate limiting for write routes. Do not treat this Compose guide as security hardening.

## Deploy the seeded demo on one Linux host

Use a Linux host with Docker Engine and the Compose plugin. Point a DNS name such as `fire.example.org` to the host, install Caddy using its [official instructions](https://caddyserver.com/docs/install), and allow inbound TCP ports 80 and 443 for HTTPS. [3] [4]

After the repository and GHCR packages are public, clone the repository and prepare the local environment file:

```bash
git clone https://github.com/3lyly0/embereye-spaceapps.git
cd embereye-spaceapps
cp .env.example .env
openssl rand -hex 32
```

Edit `.env` and set a unique `POSTGRES_PASSWORD` to the generated value. Also set these production values:

```dotenv
EMBEREYE_IMAGE_TAG=v1.0.9
DATA_MODE=SEED
BACKEND_PORT=127.0.0.1:8000
FRONTEND_PORT=127.0.0.1:8080
POSTGRES_PORT=127.0.0.1:5432
```

Keep `.env` on the host; never commit it. The loopback bindings leave only the reverse proxy internet-facing. `DATA_MODE=SEED` starts the deterministic demo archive. Without `FIRMS_MAP_KEY`, those seeded records are synthetic and are labelled as such in the application; they are not NASA observations.

Pull and start the pinned images:

```bash
docker compose pull
docker compose up -d --no-build
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
```

Configure Caddy to proxy the public hostname to the frontend container's host port. Replace the example hostname with the real DNS name:

```caddyfile
fire.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Place that site block in Caddy's active `Caddyfile`, then reload its service:

```bash
sudo systemctl reload caddy
```

Caddy can obtain and renew a publicly trusted certificate when the DNS name points to this host and ports 80 and 443 are reachable. [4] The public entry point is `https://fire.example.org`; keep the API docs on the loopback-only backend port.

To update to a later image release, change `EMBEREYE_IMAGE_TAG` in `.env`, then run:

```bash
docker compose pull
docker compose up -d --no-build
docker compose logs --tail=100 backend seeder
```

`docker compose down` stops the stack while preserving the named database volume. Avoid `docker compose down -v` unless you intend to delete that database.

## Serve the Nile Delta offline archive

If the public demo should show the real-data snapshot rather than the synthetic seed, download the release asset after the repository is public:

```bash
mkdir -p data
curl -fL -o data/embereye_dev.db \
  https://github.com/3lyly0/embereye-spaceapps/releases/download/v1.0.8-offline-data/embereye_dev.db
```

Set the archive path in `.env`:

```dotenv
EMBEREYE_SQLITE_HOST_PATH=./data/embereye_dev.db
```

Then use the SQLite override for both pull and startup:

```bash
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml pull
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up -d --no-build
```

The override disables the seeder and mounts the SQLite file read-write. Back up the file before deployment and do not replace it while the service is using it. This snapshot contains real NASA FIRMS records for Nile Delta and synthetic demo data for the other preset AOIs; it is not an all-real-data archive.

## References

[1]: https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility "GitHub Docs: Configuring package access control and visibility"
[2]: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry "GitHub Docs: Working with the container registry"
[3]: https://docs.docker.com/compose/how-tos/production/ "Docker Docs: Use Compose in production"
[4]: https://caddyserver.com/docs/quick-starts/reverse-proxy "Caddy Docs: Reverse proxy quick-start"
