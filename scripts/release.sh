#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AgentOps — Local Release (Production)
# Replicates .github/workflows/release.yml
#
# What it does:
#   1. Prompts for version confirmation
#   2. Creates and pushes git tag
#   3. Builds package
#   4. Publishes to TestPyPI + verifies
#   5. Publishes to PyPI (production) — with confirmation
#   6. Builds and publishes VSIX stable
#   7. Creates GitHub Release with artifacts
#   8. Syncs develop from main
#
# Usage:  ./scripts/release.sh
# Prereqs:
#   - PR merged to main
#   - gh auth login
#   - twine: pip install twine
#   - TESTPYPI_TOKEN env var
#   - PYPI_TOKEN env var (API token from pypi.org)
#   - VSCE_PAT env var (VS Code Marketplace PAT)
#   - npm + vsce: npm install -g @vscode/vsce
#   - jq installed
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

skip_vsix=false

# ── Step 1: Prompt for version ──────────────────────────────────────
read -rp "Enter release version to publish (e.g. 0.1.6) — no 'v' prefix: " version
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Version must be in semver format (e.g. 0.1.6), got: $version" >&2
    exit 1
fi

# Verify we're on main
branch=$(git branch --show-current)
if [ "$branch" != "main" ]; then
    echo ">>> Not on main (current: $branch). Switching..."
    git checkout main
    git pull origin main
fi

echo -e "\n>>> Release v$version from main"

# ── Step 2: Create and push tag ─────────────────────────────────────
echo -e "\n>>> [1/8] Creating tag v$version..."
if git tag -l "v$version" | grep -q "v$version"; then
    echo ">>> Tag v$version already exists — skipping creation"
else
    git tag "v$version"
    echo ">>> Tag v$version created"
fi
git push origin "v$version"
echo ">>> Tag pushed to remote"

# ── Step 3: Build ───────────────────────────────────────────────────
echo -e "\n>>> [2/8] Building package..."
rm -rf dist/
uv build
echo ">>> Build artifacts:"
ls -lh dist/

# ── Step 4: Publish to TestPyPI ─────────────────────────────────────
echo -e "\n>>> [3/8] Publishing to TestPyPI (final verification)..."
if [ -z "${TESTPYPI_TOKEN:-}" ]; then
    echo ">>> TESTPYPI_TOKEN not set — skipping TestPyPI"
else
    uv run twine upload --repository testpypi --skip-existing dist/*
    echo ">>> Published to TestPyPI"
fi

# ── Step 5: Verify TestPyPI ─────────────────────────────────────────
echo -e "\n>>> [4/8] Verifying TestPyPI install..."
if [ -z "${TESTPYPI_TOKEN:-}" ]; then
    echo ">>> Skipped (no TestPyPI publish)"
else
    for i in 1 2 3 4 5; do
        echo "    Attempt $i: installing agentops-toolkit==$version"
        if pip install "agentops-toolkit==$version" \
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
    echo ">>> TestPyPI verification passed"
fi

# ── Step 6: Publish to PyPI (production) ────────────────────────────
echo -e "\n>>> [5/8] Publishing to PyPI (PRODUCTION)..."
if [ -z "${PYPI_TOKEN:-}" ]; then
    echo ">>> PYPI_TOKEN not set — skipping PyPI publish"
    echo '    Set it with: export PYPI_TOKEN="pypi-..."'
else
    read -rp "Publish v$version to PyPI (PRODUCTION)? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo ">>> Aborted PyPI publish"
        exit 1
    fi
    uv run twine upload --skip-existing dist/*
    echo ">>> Published to PyPI"
fi

# ── Step 7: Build and publish VSIX stable ───────────────────────────
echo -e "\n>>> [6/8] Building VSIX stable..."
if ! command -v vsce &>/dev/null; then
    echo ">>> vsce not found — skipping VSIX"
    skip_vsix=true
else
    pkg_path="plugins/agentops/package.json"
    jq --arg v "$version" '.version = $v' "$pkg_path" > "${pkg_path}.tmp"
    mv "${pkg_path}.tmp" "$pkg_path"

    cp CHANGELOG.md plugins/agentops/CHANGELOG.md
    cp icon.png plugins/agentops/icon.png 2>/dev/null || true

    pushd plugins/agentops >/dev/null
    vsce package -o agentops-skills.vsix
    echo ">>> VSIX built: agentops-skills.vsix (v$version)"

    if [ -z "${VSCE_PAT:-}" ]; then
        echo ">>> VSCE_PAT not set — skipping Marketplace publish"
    else
        # Verify the VSIX package.json matches the release version
        vsix_version=$(jq -r '.version' package.json)
        if [ "$vsix_version" != "$version" ]; then
            echo "ERROR: VSIX version mismatch! package.json=$vsix_version, expected=$version. Aborting publish." >&2
            popd >/dev/null
            exit 1
        fi
        vsce publish --packagePath agentops-skills.vsix -p "$VSCE_PAT"
        echo ">>> VSIX stable published to Marketplace (v$version)"
    fi
    popd >/dev/null
fi

# ── Step 8: Create GitHub Release ───────────────────────────────────
echo -e "\n>>> [7/8] Creating GitHub Release..."
release_assets=(dist/*)
if ! $skip_vsix && [ -f "plugins/agentops/agentops-skills.vsix" ]; then
    release_assets+=("plugins/agentops/agentops-skills.vsix")
fi
gh release create "v$version" "${release_assets[@]}" --title "v$version" --generate-notes
echo ">>> GitHub Release v$version created"

# ── Step 9: Sync develop ────────────────────────────────────────────
echo -e "\n>>> [8/8] Syncing develop from main..."
read -rp "Sync develop from main? (yes/no): " sync_confirm
if [ "$sync_confirm" = "yes" ]; then
    git checkout develop
    git merge main
    git push origin develop
    echo ">>> develop synced from main"
else
    echo ">>> Skipped develop sync"
    echo "    Run manually: git checkout develop && git merge main && git push origin develop"
fi

# ── Summary ─────────────────────────────────────────────────────────
echo -e "\n✅ Release v$version complete!"
echo "  Tag:      v$version"
echo "  PyPI:     https://pypi.org/project/agentops-toolkit/$version/"
echo "  GitHub:   https://github.com/Azure/agentops/releases/tag/v$version"
$skip_vsix || echo "  VSIX:     https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-toolkit"
