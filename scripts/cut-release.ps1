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
# Usage:  .\scripts\cut-release.ps1
# Prereq: gh auth login
# ─────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Step 1: Prompt for version ──────────────────────────────────────
$version = Read-Host "Enter release version (e.g. 0.1.6) — no 'v' prefix"
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Error "Version must be in semver format (e.g. 0.1.6), got: $version"
    exit 1
}
Write-Host "`n>>> Version: $version" -ForegroundColor Cyan

# ── Step 2: Checkout develop ────────────────────────────────────────
Write-Host "`n>>> Checking out develop..." -ForegroundColor Yellow
git checkout develop
git pull origin develop

# ── Step 3: Check release branch does not exist ─────────────────────
$branchExists = git ls-remote --exit-code origin "refs/heads/release/v$version" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Error "Branch release/v$version already exists on remote. Delete it first or use a different version."
    exit 1
}
Write-Host ">>> No existing release/v$version branch — OK" -ForegroundColor Green

# ── Step 4: Create release branch ──────────────────────────────────
Write-Host "`n>>> Creating branch release/v$version..." -ForegroundColor Yellow
git checkout -b "release/v$version"

# ── Step 5: Update CHANGELOG ────────────────────────────────────────
Write-Host "`n>>> Updating CHANGELOG.md..." -ForegroundColor Yellow
$changelog = Get-Content CHANGELOG.md -Raw
$date = Get-Date -Format "yyyy-MM-dd"

if ($changelog -match [regex]::Escape("## [$version]")) {
    Write-Host "CHANGELOG already has [$version] entry — skipping" -ForegroundColor DarkYellow
} else {
    $anchor = "adheres to [Semantic Versioning]"
    $replacement = "$anchor(https://semver.org/).`n`n## [$version] - $date"
    $changelog = $changelog -replace [regex]::Escape("${anchor}(https://semver.org/)."), $replacement
    Set-Content CHANGELOG.md -Value $changelog -NoNewline
    Write-Host "CHANGELOG updated with [$version] - $date" -ForegroundColor Green
}

# ── Step 6: Sync plugin versions ────────────────────────────────────
Write-Host "`n>>> Syncing plugin versions..." -ForegroundColor Yellow

# 6a. package.json (VSIX)
$pkgPath = "plugins/agentops/package.json"
$pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
$pkg.version = $version
$pkg | ConvertTo-Json -Depth 10 | Set-Content $pkgPath -NoNewline
Write-Host "  package.json  → $version" -ForegroundColor Green

# 6b. plugin.json (Agent Plugins)
$pluginPath = "plugins/agentops/plugin.json"
$plugin = Get-Content $pluginPath -Raw | ConvertFrom-Json
$plugin.version = $version
$plugin | ConvertTo-Json -Depth 10 | Set-Content $pluginPath -NoNewline
Write-Host "  plugin.json   → $version" -ForegroundColor Green

# 6c. marketplace.json (GitHub + Claude Code)
foreach ($mpPath in @(".github/plugin/marketplace.json", ".claude-plugin/marketplace.json")) {
    $mp = Get-Content $mpPath -Raw | ConvertFrom-Json
    $mp.plugins[0].version = $version
    $mp | ConvertTo-Json -Depth 10 | Set-Content $mpPath -NoNewline
    Write-Host "  $mpPath → $version" -ForegroundColor Green
}

# ── Step 7: Configure git, commit and push ──────────────────────────
Write-Host "`n>>> Committing and pushing..." -ForegroundColor Yellow
git add CHANGELOG.md plugins/agentops/package.json plugins/agentops/plugin.json .github/plugin/marketplace.json .claude-plugin/marketplace.json
git commit -m "chore: prepare release $version"
git push origin "release/v$version"

# ── Step 8: Create PR to main ──────────────────────────────────────
Write-Host "`n>>> Creating PR to main..." -ForegroundColor Yellow
$body = @"
## Release v$version

Automated release branch created from ``develop``.

### What happened
- Branch ``release/v$version`` created from ``develop``
- ``CHANGELOG.md`` updated: versioned section ``[$version]`` added
- Plugin versions synced to ``$version`` (package.json, plugin.json, marketplace.json)

### Next steps
1. Run staging locally: ``.\scripts\staging.ps1``
2. Review and approve this PR
3. Merge to ``main``
4. Run release locally: ``.\scripts\release.ps1``
5. Sync develop: ``git checkout develop; git merge main; git push origin develop``

### Checklist
- [ ] Staging passes (build + TestPyPI + verify)
- [ ] CHANGELOG entries reviewed
- [ ] PR approved and merged to main
- [ ] Tag ``v$version`` pushed
- [ ] PyPI publish done
- [ ] VSIX stable publish done
- [ ] develop synced from main
"@

gh pr create `
    --base main `
    --head "release/v$version" `
    --title "Release v$version" `
    --body $body

Write-Host "`n✅ Cut Release complete!" -ForegroundColor Green
Write-Host "  Branch: release/v$version" -ForegroundColor Cyan
Write-Host "  PR:     release/v$version → main" -ForegroundColor Cyan
Write-Host "`nNext: run .\scripts\staging.ps1 to build + verify" -ForegroundColor DarkYellow
