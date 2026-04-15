#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AgentOps — Local Cut Release
# Replicates .github/workflows/cut-release.yml
#
# What it does:
#   1. Prompts for the version (semver)
#   2. Checks out develop (latest)
#   3. Verifies release branch doesn't exist
#   4. Creates release/v<version> branch
#   5. Updates CHANGELOG.md with versioned section
#   6. Syncs VSIX version in plugins/agentops/package.json
#   7. Commits and pushes the branch
#   8. Creates a PR to main via gh CLI
#
# Usage:  ./scripts/cut-release.sh
# Prereq: gh auth login, jq installed
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Step 1: Prompt for version ──────────────────────────────────────
read -rp "Enter release version (e.g. 0.1.6) — no 'v' prefix: " version
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Version must be in semver format (e.g. 0.1.6), got: $version" >&2
    exit 1
fi
echo -e "\n>>> Version: $version"

# ── Step 2: Checkout develop ────────────────────────────────────────
echo -e "\n>>> Checking out develop..."
git checkout develop
git pull origin develop

# ── Step 3: Check release branch does not exist ─────────────────────
if git ls-remote --exit-code origin "refs/heads/release/v$version" >/dev/null 2>&1; then
    echo "ERROR: Branch release/v$version already exists on remote. Delete it first or use a different version." >&2
    exit 1
fi
echo ">>> No existing release/v$version branch — OK"

# ── Step 4: Create release branch ──────────────────────────────────
echo -e "\n>>> Creating branch release/v$version..."
git checkout -b "release/v$version"

# ── Step 5: Update CHANGELOG ────────────────────────────────────────
echo -e "\n>>> Updating CHANGELOG.md..."
date_today=$(date +%Y-%m-%d)

if grep -q "## \[$version\]" CHANGELOG.md; then
    echo "CHANGELOG already has [$version] entry — skipping"
else
    sed -i.bak "s/adheres to \[Semantic Versioning\](https:\/\/semver.org\/)./&\n\n## [$version] - $date_today/" CHANGELOG.md
    rm -f CHANGELOG.md.bak
    echo "CHANGELOG updated with [$version] - $date_today"
fi

# ── Step 6: Sync VSIX version ──────────────────────────────────────
echo -e "\n>>> Syncing VSIX version in package.json..."
pkg_path="plugins/agentops/package.json"
jq --arg v "$version" '.version = $v' "$pkg_path" > "${pkg_path}.tmp"
mv "${pkg_path}.tmp" "$pkg_path"
echo "VSIX version set to $version"

# ── Step 7: Commit and push ────────────────────────────────────────
echo -e "\n>>> Committing and pushing..."
git add CHANGELOG.md plugins/agentops/package.json
git commit -m "chore: prepare release $version"
git push origin "release/v$version"

# ── Step 8: Create PR to main ──────────────────────────────────────
echo -e "\n>>> Creating PR to main..."
gh pr create \
    --base main \
    --head "release/v$version" \
    --title "Release v$version" \
    --body "## Release v$version

Automated release branch created from \`develop\`.

### What happened
- Branch \`release/v$version\` created from \`develop\`
- \`CHANGELOG.md\` updated: versioned section \`[$version]\` added
- \`plugins/agentops/package.json\` version synced to \`$version\`

### Next steps
1. Run staging locally: \`./scripts/staging.sh\`
2. Review and approve this PR
3. Merge to \`main\`
4. Run release locally: \`./scripts/release.sh\`
5. Sync develop: \`git checkout develop && git merge main && git push origin develop\`

### Checklist
- [ ] Staging passes (build + TestPyPI + verify)
- [ ] CHANGELOG entries reviewed
- [ ] PR approved and merged to main
- [ ] Tag \`v$version\` pushed
- [ ] PyPI publish done
- [ ] VSIX stable publish done
- [ ] develop synced from main"

echo -e "\n✅ Cut Release complete!"
echo "  Branch: release/v$version"
echo "  PR:     release/v$version → main"
echo -e "\nNext: run ./scripts/staging.sh to build + verify"
