#!/bin/bash
# Replace the engine (paths listed in the template's .fdp-engine) with the template's current version.
# Your content (fdp.config.json, fdp/, assets/, README.md, PERSISTENCE.md, ...) is never touched.
#
#   scripts/update-engine.sh                 # update everything, including .github/workflows/
#   scripts/update-engine.sh --no-workflows  # skip workflow files (GitHub's built-in Actions token cannot change them)
#
# Review with `git diff`, then commit.
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_WORKFLOWS=false
[ "${1:-}" = "--no-workflows" ] && SKIP_WORKFLOWS=true

TEMPLATE=$(python3 scripts/fdpconfig.py get template.repository)
BRANCH=$(python3 scripts/fdpconfig.py get template.branch 2>/dev/null || echo main)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone -q --depth 1 --branch "$BRANCH" "https://github.com/$TEMPLATE.git" "$TMP"
echo "Engine from $TEMPLATE@$(git -C "$TMP" rev-parse --short HEAD)"

grep -vE '^\s*(#|$)' "$TMP/.fdp-engine" | while read -r path; do
  if $SKIP_WORKFLOWS && [[ "$path" == .github/workflows* ]]; then
    echo "  skipped  $path (workflow files)"
    continue
  fi
  if [[ "$path" == */ ]]; then
    mkdir -p "$path"
    rsync -a --delete --exclude '__pycache__' "$TMP/$path" "$path"
  else
    mkdir -p "$(dirname "$path")"
    cp "$TMP/$path" "$path"
  fi
  echo "  updated  $path"
done

if $SKIP_WORKFLOWS && ! diff -rq "$TMP/.github/workflows" .github/workflows >/dev/null 2>&1; then
  echo "WORKFLOWS_DIFFER" > .engine-workflows-differ
  diff -ru .github/workflows "$TMP/.github/workflows" > .engine-workflows.diff || true
fi
git status --short
