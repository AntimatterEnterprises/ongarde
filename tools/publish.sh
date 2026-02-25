#!/bin/bash
# OnGarde Package Publisher
# Usage: NPM_TOKEN=xxx PYPI_TOKEN=xxx ./tools/publish.sh
# Run from repo root.

set -e

echo "=== OnGarde Package Publisher ==="
echo ""

# ── Validate tokens ──────────────────────────────────────────
if [ -z "$NPM_TOKEN" ]; then
  echo "ERROR: NPM_TOKEN not set"
  echo "  Get a token: https://www.npmjs.com/settings/~/tokens"
  exit 1
fi

if [ -z "$PYPI_TOKEN" ]; then
  echo "ERROR: PYPI_TOKEN not set"
  echo "  Get a token: https://pypi.org/manage/account/token/"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "── Python package (PyPI) ──────────────────────────────"
cd "$REPO_ROOT"
pip install build twine --break-system-packages -q
python3 -m build --wheel --sdist
TWINE_PASSWORD="$PYPI_TOKEN" TWINE_USERNAME="__token__" twine upload dist/* --non-interactive
echo "✓ ongarde published to PyPI"
echo ""

echo "── npm package (npm registry) ─────────────────────────"
cd "$REPO_ROOT/packages/openclaw"
echo "//registry.npmjs.org/:_authToken=${NPM_TOKEN}" > .npmrc
npm publish --access public
rm -f .npmrc  # Don't leave token in .npmrc
echo "✓ @ongarde/openclaw published to npm"
echo ""

echo "=== Done ==="
echo "PyPI: https://pypi.org/project/ongarde/"
echo "npm:  https://www.npmjs.com/package/@ongarde/openclaw"
