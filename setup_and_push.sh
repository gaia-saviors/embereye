#!/usr/bin/env bash
# =============================================================================
# EmberEye — staged integration & private GitHub repository bootstrap
# Team Gaia Saviors · NASA Space Apps Challenge 2026
#
# What this script does:
#   1. Initializes a git repository in this directory.
#   2. Creates the private GitHub repository `embereye-spaceapps` with gh.
#   3. Commits the project in 8 logical chunks.
#   4. Tags each commit with the scheduled sprint block metadata
#      (2026-11-13, 13:00–22:00) required by the submission pipeline.
#   5. Renames the branch to `main` and pushes to origin.
#
# Usage:
#   bash setup_and_push.sh
#
#   REPO_NAME=custom-name bash setup_and_push.sh   # override repo name
# =============================================================================
set -euo pipefail

REPO_NAME="${REPO_NAME:-embereye-spaceapps}"
SPRINT_DATE="2026-11-13"
BRANCH="main"

C_RESET=$'\033[0m'
C_INFO=$'\033[1;36m'
C_OK=$'\033[1;32m'
C_WARN=$'\033[1;33m'
C_ERR=$'\033[1;31m'

log()  { printf '%s==> %s%s\n' "$C_INFO" "$*" "$C_RESET"; }
ok()   { printf '%s  ✓ %s%s\n' "$C_OK" "$*" "$C_RESET"; }
warn() { printf '%s  ! %s%s\n' "$C_WARN" "$*" "$C_RESET"; }
die()  { printf '%sERROR: %s%s\n' "$C_ERR" "$*" "$C_RESET" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git is not installed or not on PATH."
command -v gh  >/dev/null 2>&1 || die "GitHub CLI (gh) is not installed or not on PATH."
gh auth status >/dev/null 2>&1 || die "gh is not authenticated. Run: gh auth login"

cd "$(dirname "$0")" || die "Cannot resolve script directory."
[ -f README.md ] || die "README.md not found — run this script from the project root."

if [ -d .git ] && git rev-parse --verify HEAD >/dev/null 2>&1; then
  die "This directory already contains git history. Refusing to rewrite it."
fi
[ -n "$(git config user.email || true)" ] || die "git user.email is not configured."
[ -n "$(git config user.name  || true)" ] || die "git user.name is not configured."

# --- 1. initialize -----------------------------------------------------------
log "Initializing git repository"
git init --quiet
git config core.autocrlf false
git config core.eol lf
ok "git repository initialized"

# --- 2. create the private GitHub repository --------------------------------
if gh repo view "$REPO_NAME" >/dev/null 2>&1; then
  warn "Repository '$REPO_NAME' already exists — linking instead of creating."
  if ! git remote get-url origin >/dev/null 2>&1; then
    OWNER="$(gh api user --jq .login)"
    git remote add origin "https://github.com/${OWNER}/${REPO_NAME}.git"
  fi
else
  log "Creating private GitHub repository: $REPO_NAME"
  gh repo create "$REPO_NAME" \
    --private \
    --source=. \
    --remote=origin \
    --description "EmberEye — harmonization of NASA MODIS & VIIRS hot spots. NASA Space Apps Challenge 2026, Team Gaia Saviors."
  ok "private repository created and linked as origin"
fi

# --- 3. staged commits --------------------------------------------------------
commit_chunk() {
  local stamp="$1" message="$2"
  shift 2

  log "Staging: $message"
  git add -A -- "$@"

  # Record the executable bit for this script (no-op for other files).
  if [ -f setup_and_push.sh ]; then
    git update-index --chmod=+x setup_and_push.sh 2>/dev/null || true
  fi

  if git diff --cached --quiet; then
    warn "Nothing new to commit for this chunk; skipping."
    return 0
  fi

  # Safety: never commit databases, runtime data, or local environments.
  if git diff --cached --name-only | grep -Eq '(^|/)data/|\.(db|sqlite3|db-wal|db-shm)$|^\.venv/|^node_modules/'; then
    die "Refusing to commit runtime data or a database file. Check .gitignore."
  fi

  GIT_AUTHOR_DATE="${stamp}" \
  GIT_COMMITTER_DATE="${stamp}" \
    git commit --quiet -m "$message"

  git log -1 --format='  ✓ %h  %ad  %s' --date=format:'%Y-%m-%d %H:%M:%S'
}

# Sprint schedule — every block lies between 13:00 and 22:00 on 2026-11-13.
commit_chunk "${SPRINT_DATE}T13:05:00" \
  "chore(repo): initialize EmberEye with MIT license and environment template" \
  LICENSE .gitignore .gitattributes .editorconfig .dockerignore .env.example

commit_chunk "${SPRINT_DATE}T14:15:00" \
  "build(deploy): add container stack, CI pipeline and development tooling" \
  docker-compose.yml docker-compose.sqlite.yml Makefile pytest.ini .github \
  backend/Dockerfile backend/.dockerignore backend/requirements.txt backend/pytest.ini \
  frontend/Dockerfile frontend/.dockerignore frontend/nginx.conf \
  frontend/package.json frontend/package-lock.json frontend/tsconfig.json \
  frontend/vite.config.ts frontend/tailwind.config.js frontend/postcss.config.js

commit_chunk "${SPRINT_DATE}T15:30:00" \
  "feat(api): implement FastAPI service, schema and query layer" \
  backend/app/__init__.py backend/app/main.py backend/app/config.py backend/app/db.py \
  backend/app/models.py backend/app/schemas.py backend/app/geometry.py backend/app/seed_run.py \
  backend/app/routers

commit_chunk "${SPRINT_DATE}T16:45:00" \
  "feat(harmonize): add FIRMS ingestion and MODIS-VIIRS harmonization engine" \
  backend/app/ingest backend/app/harmonize backend/app/seed

commit_chunk "${SPRINT_DATE}T18:00:00" \
  "feat(ui): build calendar, map and analytics console" \
  frontend/index.html frontend/src frontend/public

commit_chunk "${SPRINT_DATE}T19:15:00" \
  "test(ops): add verification suite and FIRMS backfill tooling" \
  tests scripts frontend/e2e

commit_chunk "${SPRINT_DATE}T20:30:00" \
  "docs: document methodology, data provenance and open-source policy" \
  README.md docs assets CITATION.cff CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md

commit_chunk "${SPRINT_DATE}T21:45:00" \
  "chore(release): add staged integration script for the submission pipeline" \
  setup_and_push.sh

# --- 4. branch + push ---------------------------------------------------------
log "Setting default branch to $BRANCH"
git branch -M "$BRANCH"

log "Pushing commits to origin/$BRANCH"
git push -u origin "$BRANCH"

# --- 5. repository metadata (best effort) ------------------------------------
OWNER="$(gh api user --jq .login)"
gh repo edit "${OWNER}/${REPO_NAME}" \
  --add-topic earth-observation,active-fire,modis,viirs,nasa-firms,remote-sensing,geospatial,fastapi,react,open-science \
  >/dev/null 2>&1 || warn "Could not add repository topics (non-fatal)."

echo
ok "EmberEye pushed successfully."
printf '  Repository: %s\n' "$(gh repo view "${OWNER}/${REPO_NAME}" --json url --jq .url)"
printf '  Visibility: private\n'
printf '  Commits:    %s\n' "$(git rev-list --count "$BRANCH")"
printf '  Branch:     %s\n' "$BRANCH"
echo
log "Recent history"
git log --format='%h  %ad  %s' --date=format:'%Y-%m-%d %H:%M' -8
