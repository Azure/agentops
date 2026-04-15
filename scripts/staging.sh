#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AgentOps — Local Staging
# Replicates .github/workflows/staging.yml + _build.yml
#
# What it does:
#   1. Lint (ruff)
#   2. Run tests (pytest)
#   3. Build package (sdist + wheel)
#   4. Publish to TestPyPI
#   5. Verify install from TestPyPI + smoke test
#   6. Build VSIX pre-release
#   7. Publish VSIX pre-release to Marketplace
#
# Usage:  ./scripts/staging.sh
# Prereqs:
#   - uv installed
#   - twine: pip install twine (for TestPyPI upload)
#   - npm + vsce: npm install -g @vscode/vsce
#   - TESTPYPI_TOKEN env var (API token from test.pypi.org)
#   - VSCE_PAT env var (VS Code Marketplace PAT)
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

skip_testpypi=false
skip_vsix=false

# ── Step 1: Lint ────────────────────────────────────────────────────
echo -e "\n>>> [1/7] Linting with ruff..."
uv run ruff check src/ tests/
echo ">>> Lint passed"

# ── Step 2: Test ────────────────────────────────────────────────────
echo -e "\n>>> [2/7] Running tests..."
uv run pytest tests/ -v --tb=short
echo ">>> Tests passed"

# ── Step 3: Build ───────────────────────────────────────────────────
echo -e "\n>>> [3/7] Building package..."
rm -rf dist/
uv build
echo ">>> Build artifacts:"
ls -lh dist/

# ── Step 4: Publish to TestPyPI ─────────────────────────────────────
echo -e "\n>>> [4/7] Publishing to TestPyPI..."
if [ -z "${TESTPYPI_TOKEN:-}" ]; then
    echo ">>> TESTPYPI_TOKEN not set — skipping TestPyPI publish"
    echo '    Set it with: export TESTPYPI_TOKEN="pypi-..."'
    skip_testpypi=true
else
    uv run twine upload --repository testpypi --skip-existing dist/*
    echo ">>> Published to TestPyPI"
fi

# ── Step 5: Verify TestPyPI install ─────────────────────────────────
echo -e "\n>>> [5/7] Verifying TestPyPI install..."
if $skip_testpypi; then
    echo ">>> Skipped (no TestPyPI publish)"
else
    scm_version=$(uv run python -m setuptools_scm 2>/dev/null)
    echo "    Expected version: $scm_version"
    for i in 1 2 3 4 5; do
        echo "    Attempt $i..."
        if pip install "agentops-toolkit==$scm_version" \
            --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/; then
            break
        fi
        if [ "$i" -lt 5 ]; then
            echo "    Not available yet, waiting 30s..."
            sleep 30
        fi
    done
    agentops --version
    agentops --help >/dev/null
    tmp_dir=$(mktemp -d)
    pushd "$tmp_dir" >/dev/null
    agentops init
    test -f .agentops/config.yaml || { echo "ERROR: Smoke test failed: config.yaml missing" >&2; exit 1; }
    test -f .agentops/run.yaml || { echo "ERROR: Smoke test failed: run.yaml missing" >&2; exit 1; }
    popd >/dev/null
    rm -rf "$tmp_dir"
    echo ">>> TestPyPI verification passed"
fi

# ── Step 6: Build VSIX ──────────────────────────────────────────────
echo -e "\n>>> [6/7] Building VSIX pre-release..."
if ! command -v vsce &>/dev/null; then
    echo ">>> vsce not found — skipping VSIX build"
    echo "    Install with: npm install -g @vscode/vsce"
    skip_vsix=true
else
    # Sync version from latest git tag
    last_tag=$(git tag -l 'v*' --sort=-v:refname | head -1)
    last_tag=${last_tag:-v0.0.0}
    last_version=${last_tag#v}
    IFS='.' read -r major minor patch <<< "$last_version"
    if git describe --tags --exact-match HEAD >/dev/null 2>&1; then
        base_version="$last_version"
    else
        base_version="$major.$minor.$((patch + 1))"
    fi

    pkg_path="plugins/agentops/package.json"
    # Save original package.json so staging doesn't pollute the working tree
    pkg_original=$(cat "$pkg_path")
    jq --arg v "$base_version" '.version = $v' "$pkg_path" > "${pkg_path}.tmp"
    mv "${pkg_path}.tmp" "$pkg_path"

    cp CHANGELOG.md plugins/agentops/CHANGELOG.md
    cp icon.png plugins/agentops/icon.png 2>/dev/null || true

    pushd plugins/agentops >/dev/null
    vsce package --pre-release -o agentops-skills.vsix
    echo ">>> VSIX built: agentops-skills.vsix (v$base_version)"
    popd >/dev/null

    # Restore original package.json to prevent version drift
    echo "$pkg_original" > "$pkg_path"
    echo ">>> package.json restored to committed version"
fi

# ── Step 7: Publish VSIX pre-release ────────────────────────────────
echo -e "\n>>> [7/7] Publishing VSIX pre-release..."
if $skip_vsix; then
    echo ">>> Skipped (vsce not available)"
elif [ -z "${VSCE_PAT:-}" ]; then
    echo ">>> VSCE_PAT not set — skipping Marketplace publish"
    echo '    Set it with: export VSCE_PAT="your-pat"'
else
    pushd plugins/agentops >/dev/null
    echo "    VSIX will publish from packagePath (version in VSIX: $base_version)"
    vsce publish --pre-release --packagePath agentops-skills.vsix -p "$VSCE_PAT"
    popd >/dev/null
    echo ">>> VSIX pre-release published to Marketplace"
fi

# ── Summary ─────────────────────────────────────────────────────────
echo -e "\n✅ Staging complete!"
echo "  Lint:     passed"
echo "  Tests:    passed"
echo "  Build:    dist/"
$skip_testpypi || echo "  TestPyPI: published + verified"
$skip_vsix || echo "  VSIX:     plugins/agentops/agentops-skills.vsix"
echo -e "\nNext: merge the PR, then run ./scripts/release.sh"
