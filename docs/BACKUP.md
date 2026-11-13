# BACKUP.md — PowerShell-safe backup & restore for the real data

The real archive currently lives in `embereye_dev.db` (SQLite, repo root,
~75.6 MB, 171,853 hotspots rows). After the option-(b) migration it lives in
the compose Postgres volume. Both backup paths are below; do one NOW and one
before any risky operation (`--fresh`, seeder overrides, re-runs).

## 1. SQLite (`embereye_dev.db`)

```powershell
cd embereye
# make sure no backfill/audit run and no sqlite-mode stack is writing the file
New-Item -ItemType Directory -Force -Path backup | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

# safest: a consistent snapshot even if a writer is active (pure sqlite3 semantics)
python -c "import sqlite3; sqlite3.connect('backup/embereye_' + '$stamp' + '.db').execute(\"VACUUM INTO 'backup/embereye_$stamp.db'\")"

# simple alternative when NOTHING is writing the file:
Copy-Item .\embereye_dev.db "backup\embereye_dev_$stamp.db"
# if WAL sidecars exist, copy them too (or just use the VACUUM INTO path above):
# Copy-Item .\embereye_dev.db-wal, .\embereye_dev.db-shm "backup\" -ErrorAction SilentlyContinue

# verify the copy opens and has the expected rows:
python -c "import sqlite3; print(sqlite3.connect('backup/embereye_$stamp.db').execute('SELECT COUNT(*) FROM hotspots').fetchone())"
```

Restore: stop whatever uses the live file, then
`Copy-Item backup\embereye_dev_<stamp>.db .\embereye_dev.db -Force`
(and delete stray `-wal`/`-shm` siblings of the live file first).

## 2. Postgres (compose volume `embereye_pgdata`)

PowerShell note: **never** use `>` or `Out-File` redirects for dumps — they
produce UTF-16 and corrupt the file. Use `pg_dump -f` inside the container and
`docker compose cp` to get it out.

```powershell
cd embereye
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path backup | Out-Null

# custom-format dump (compressed, restore with pg_restore)
docker compose exec db pg_dump -U embereye -d embereye -Fc -f /tmp/embereye_$stamp.dump
docker compose cp "db:/tmp/embereye_$stamp.dump" "backup/embereye_$stamp.dump"
docker compose exec db rm /tmp/embereye_$stamp.dump

# plain SQL alternative (readable, larger):
docker compose exec db pg_dump -U embereye -d embereye -f /tmp/embereye_$stamp.sql
docker compose cp "db:/tmp/embereye_$stamp.sql" "backup/embereye_$stamp.sql"

# verify:
docker compose exec db pg_restore --list /tmp/embereye_$stamp.dump | Select-Object -First 5
```

Restore into a fresh/empty volume:

```powershell
docker compose down            # keep the volume unless you really drop it
# drop only if required: docker volume rm embereye_pgdata
docker compose up -d db
docker compose cp "backup/embereye_$stamp.dump" "db:/tmp/restore.dump"
docker compose exec db pg_restore -U embereye -d embereye --clean --if-exists /tmp/restore.dump
docker compose exec db rm /tmp/restore.dump
docker compose up -d
```

After any restore, verify with the status command (works on both engines):

```powershell
$env:EMBEREBEYE_DATABASE_URL = "postgresql+psycopg://embereye:embereye_dev_pw@localhost:5432/embereye"
python scripts\backfill_firms.py --status
# expect: Terra 6,752 · Aqua 6,870 · SNPP 93,372 · N20 46,137 · N21 18,722
```

## 3. Also worth backing up

- `data\firms_backfill\state.json` — the resumable-state file (loses ~hours if lost).
- `data\firms_backfill\raw\` — verbatim chunk CSVs (only for chunks fetched
  after v1.0.4; see docs/DATA.md §4).
- `data\firms_backfill\snapshots\` — row-level CSV exports
  (`python scripts\backfill_firms.py --build-snapshot`).
- `data\audit_real\<stamp>\` — audit reports you want to keep.
