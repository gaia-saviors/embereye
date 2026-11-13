#!/usr/bin/env bash
# =============================================================================
# EmberEye — publish the offline SQLite archive as a GitHub Release
#
#   1. Annotated tag v1.0.0-offline-data @ 2026-11-13 22:30 (sprint metadata)
#   2. Private GitHub Release with embereye_dev.db attached
#   3. README download instructions committed @ 2026-11-13 22:45 and pushed
#
# Usage:
#   bash publish_data_release.sh
#
# Requires: git, gh (authenticated), the local embereye_dev.db, and network.
# Run from the repository root. On Windows, use Git Bash so that `gh` is on
# PATH (WSL bash does not inherit the Windows GitHub CLI path).
# =============================================================================
set -euo pipefail

TAG="v1.0.0-offline-data"
TAG_DATE="2026-11-13T22:30:00"
COMMIT_DATE="2026-11-13T22:45:00"
DB_FILE="embereye_dev.db"
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
command -v gh  >/dev/null 2>&1 || die "GitHub CLI (gh) is not on PATH. On Windows run this with Git Bash."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required."
gh auth status >/dev/null 2>&1 || die "gh is not authenticated. Run: gh auth login"

cd "$(dirname "$0")" || die "Cannot resolve script directory."
[ -f README.md ] || die "README.md not found — run from the repository root."
[ -f "$DB_FILE" ] || die "$DB_FILE not found in the repository root."
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not a git repository."

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$CURRENT_BRANCH" = "$BRANCH" ] || die "Expected branch '$BRANCH', found '$CURRENT_BRANCH'."

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" \
  || die "Cannot resolve the GitHub repository (is origin configured?)."
OWNER="${REPO%%/*}"
URL="https://github.com/${REPO}/releases/download/${TAG}/${DB_FILE}"

DB_BYTES="$(wc -c < "$DB_FILE" | tr -d ' ')"
DB_MIB="$(awk -v b="$DB_BYTES" 'BEGIN { printf "%.1f", b / 1048576 }')"
log "Repository: ${REPO}"
log "Database:   ${DB_FILE} (${DB_BYTES} bytes / ${DB_MIB} MiB)"

# --- 1. annotated tag ---------------------------------------------------------
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1; then
  TAG_SHA="$(git rev-list -n 1 "$TAG")"
  HEAD_SHA="$(git rev-parse HEAD)"
  [ "$TAG_SHA" = "$HEAD_SHA" ] \
    || die "Tag ${TAG} already exists but does not point at HEAD. Refusing to move it."
  warn "Tag ${TAG} already exists locally at HEAD — keeping it."
else
  log "Creating annotated tag ${TAG} at ${TAG_DATE}"
  GIT_COMMITTER_DATE="${TAG_DATE}" git tag -a "$TAG" -m "Release v1.0.0 offline database"
  ok "tag created"
fi

TAGGER_DATE="$(git for-each-ref "refs/tags/${TAG}" --format='%(taggerdate:iso-strict)')"
ok "tagger date: ${TAGGER_DATE}"

log "Pushing tag to origin"
git push origin "$TAG"
ok "tag pushed"

# --- 2. GitHub Release --------------------------------------------------------
SHA256="$(sha256sum "$DB_FILE" | awk '{print $1}')"
ok "sha256: ${SHA256}"

NOTES="Pre-processed 380MB SQLite database to run the platform offline without API keys. Required for Option B local setup.

Contains NASA FIRMS MODIS/VIIRS observations. See docs/DATA_SOURCES.md for attribution and terms.
SHA-256: ${SHA256}"

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  warn "Release ${TAG} already exists — verifying the attached asset."
  REMOTE_SIZE="$(gh release view "$TAG" --repo "$REPO" --json assets \
    --jq ".assets[] | select(.name == \"${DB_FILE}\") | .size" 2>/dev/null || true)"
  if [ "${REMOTE_SIZE:-0}" = "$DB_BYTES" ]; then
    ok "asset already uploaded (${REMOTE_SIZE} bytes)"
  else
    log "Uploading/repairing release asset"
    gh release upload "$TAG" "$DB_FILE" --repo "$REPO" --clobber
    ok "asset uploaded"
  fi
else
  log "Creating release ${TAG} and uploading ${DB_MIB} MiB (this can take a while)"
  gh release create "$TAG" "$DB_FILE" \
    --repo "$REPO" \
    --title "EmberEye Offline Database (Seed)" \
    --notes "$NOTES"
  ok "release created and asset uploaded"
fi

REMOTE_SIZE="$(gh release view "$TAG" --repo "$REPO" --json assets \
  --jq ".assets[] | select(.name == \"${DB_FILE}\") | .size")"
[ "$REMOTE_SIZE" = "$DB_BYTES" ] \
  || die "Asset size mismatch after upload (local ${DB_BYTES}, remote ${REMOTE_SIZE})."
ok "release asset verified (${REMOTE_SIZE} bytes)"
ok "download URL: ${URL}"

# --- 3. README instructions ---------------------------------------------------
log "Adding download instructions to README.md (idempotent)"
python - "$URL" "$REPO" "$TAG" "$DB_FILE" <<'PY'
import sys
from pathlib import Path

url, repo, tag, db_file = sys.argv[1:5]
path = Path("README.md")
with path.open("r", encoding="utf-8", newline="") as handle:
    text = handle.read()

marker = "### Option B — Serve an existing SQLite archive\n"
if url in text:
    print("  ✓ README already documents the download URL; no edit needed.")
    raise SystemExit(0)
if marker not in text:
    raise SystemExit("ERROR: could not find the Option B heading in README.md")

block = (
    "\n"
    "**Download the ready-to-run archive (no API key needed):**\n"
    "\n"
    "```bash\n"
    "# Download the pre-processed 380MB database to run locally without an API key\n"
    f'curl -L -o {db_file} "{url}"\n'
    "```\n"
    "\n"
    f"> Private-repository note: release assets require an authenticated GitHub\n"
    f"> session. If `curl` returns 404, run `gh auth login` and use\n"
    f"> `gh release download {tag} -R {repo}` instead.\n"
    "\n"
)

with path.open("w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(marker, marker + block, 1))
print("  ✓ README.md updated.")
PY

git add README.md
if git diff --cached --quiet; then
  warn "README.md has no changes to commit — skipping commit and push."
else
  log "Committing README update at ${COMMIT_DATE}"
  GIT_AUTHOR_DATE="${COMMIT_DATE}" \
  GIT_COMMITTER_DATE="${COMMIT_DATE}" \
    git commit -m "docs: add offline database download instructions to README"
  ok "commit created"

  log "Pushing ${BRANCH} to origin"
  git push origin "$BRANCH"
  ok "README commit pushed"
fi

# --- 4. final verification ----------------------------------------------------
git fetch --quiet origin "$BRANCH"
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/${BRANCH}")"
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || die "Remote ${BRANCH} is not at local HEAD."

if git show "origin/${BRANCH}:README.md" | grep -Fq "$URL"; then
  ok "remote README contains the release URL"
else
  die "remote README does not contain the release URL"
fi

echo
ok "Offline database release published."
printf '  Release:  https://github.com/%s/releases/tag/%s\n' "$REPO" "$TAG"
printf '  Asset:    %s (%s bytes, sha256 %s)\n' "$DB_FILE" "$DB_BYTES" "$SHA256"
printf '  Commit:   %s\n' "$(git log -1 --format='%h %ad %s' --date=format:'%Y-%m-%d %H:%M:%S')"
printf '  Branch:   %s @ %s\n' "$BRANCH" "$REMOTE_SHA"
