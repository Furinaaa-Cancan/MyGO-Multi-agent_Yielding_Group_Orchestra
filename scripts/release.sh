#!/usr/bin/env bash
# ── MyGO Release Script ──────────────────────────────────
#
# Automates: test → lint → version bump → changelog → commit → tag → push
#
# Usage:
#   ./scripts/release.sh patch    # 0.9.0 → 0.9.1
#   ./scripts/release.sh minor    # 0.9.0 → 0.10.0
#   ./scripts/release.sh major    # 0.9.0 → 1.0.0
#   ./scripts/release.sh 1.2.3    # explicit version
#
# Prerequisites:
#   - Clean git working tree (no uncommitted changes)
#   - All tests passing
#   - PYTHONPATH=src set or editable install

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Args ─────────────────────────────────────────────────

BUMP="${1:-}"
if [[ -z "$BUMP" ]]; then
    echo "Usage: $0 <patch|minor|major|X.Y.Z>"
    exit 1
fi

# ── Resolve project root ─────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# ── Step 1: Check clean working tree ─────────────────────

info "Checking git status..."
if ! git diff --quiet HEAD 2>/dev/null; then
    fail "Working tree has uncommitted changes. Commit or stash first."
fi
ok "Working tree clean"

# ── Step 2: Read current version ─────────────────────────

CURRENT=$(grep '__version__' src/multi_agent/__init__.py | sed 's/.*"\(.*\)".*/\1/')
info "Current version: $CURRENT"

# ── Step 3: Calculate new version ────────────────────────

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

case "$BUMP" in
    patch) NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))" ;;
    minor) NEW_VERSION="$MAJOR.$((MINOR + 1)).0" ;;
    major) NEW_VERSION="$((MAJOR + 1)).0.0" ;;
    *)
        if [[ "$BUMP" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            NEW_VERSION="$BUMP"
        else
            fail "Invalid version bump: $BUMP (use patch/minor/major or X.Y.Z)"
        fi
        ;;
esac

info "New version: $NEW_VERSION"
echo ""

# ── Step 4: Run tests ────────────────────────────────────

info "Running tests..."
if PYTHONPATH=src .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -3; then
    ok "Tests passed"
else
    fail "Tests failed — aborting release"
fi

# ── Step 5: Run linter ───────────────────────────────────

info "Running ruff..."
if PYTHONPATH=src .venv/bin/python -m ruff check src/multi_agent/ 2>&1; then
    ok "Ruff clean"
else
    fail "Ruff errors — fix before release"
fi

echo ""

# ── Step 6: Bump version in all files ────────────────────

info "Bumping version $CURRENT → $NEW_VERSION..."

# __init__.py
sed -i '' "s/__version__ = \"$CURRENT\"/__version__ = \"$NEW_VERSION\"/" src/multi_agent/__init__.py

# pyproject.toml
sed -i '' "s/version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" pyproject.toml

# README.md
sed -i '' "s/v$CURRENT/v$NEW_VERSION/g" README.md

# server.py
sed -i '' "s/version=\"$CURRENT\"/version=\"$NEW_VERSION\"/" src/multi_agent/web/server.py

# index.html footer
sed -i '' "s/Orchestra v$CURRENT/Orchestra v$NEW_VERSION/" src/multi_agent/web/static/index.html

ok "Version bumped in 5 files"

# ── Step 7: Update CHANGELOG ─────────────────────────────

info "Updating CHANGELOG.md..."
TODAY=$(date +%Y-%m-%d)
CHANGELOG_ENTRY="## [$NEW_VERSION] - $TODAY

### Changed
- Version bump from $CURRENT to $NEW_VERSION

"

# Insert after the header line
sed -i '' "/^## \[$CURRENT\]/i\\
$CHANGELOG_ENTRY" CHANGELOG.md 2>/dev/null || {
    # Fallback: prepend after format line
    warn "Could not insert changelog entry automatically — please update CHANGELOG.md manually"
}

ok "CHANGELOG updated"

# ── Step 8: Commit ───────────────────────────────────────

info "Committing..."
git add -A
git commit -m "release: v$NEW_VERSION"
ok "Committed"

# ── Step 9: Tag ──────────────────────────────────────────

info "Tagging v$NEW_VERSION..."
git tag -a "v$NEW_VERSION" -m "v$NEW_VERSION"
ok "Tagged"

# ── Step 10: Push ────────────────────────────────────────

info "Pushing to origin..."
git push && git push --tags
ok "Pushed"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}  🎉 Released v$NEW_VERSION successfully!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
