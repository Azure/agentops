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
# Usage:  .\.local\release.ps1
# Prereqs:
#   - PR merged to main
#   - gh auth login
#   - twine: pip install twine
#   - TESTPYPI_TOKEN env var
#   - PYPI_TOKEN env var (API token from pypi.org)
#   - VSCE_PAT env var (VS Code Marketplace PAT)
#   - npm + vsce: npm install -g @vscode/vsce
# ─────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$skipVSIX = $false

# ── Step 1: Prompt for version ──────────────────────────────────────
$version = Read-Host "Enter release version to publish (e.g. 0.1.6) — no 'v' prefix"
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Error "Version must be in semver format (e.g. 0.1.6), got: $version"
    exit 1
}

# Verify we're on main
$branch = git branch --show-current
if ($branch -ne "main") {
    Write-Host ">>> Not on main (current: $branch). Switching..." -ForegroundColor Yellow
    git checkout main
    git pull origin main
}

Write-Host "`n>>> Release v$version from main" -ForegroundColor Cyan

# ── Step 2: Create and push tag ─────────────────────────────────────
Write-Host "`n>>> [1/8] Creating tag v$version..." -ForegroundColor Yellow
$tagExists = git tag -l "v$version"
if ($tagExists) {
    Write-Host ">>> Tag v$version already exists — skipping creation" -ForegroundColor DarkYellow
} else {
    git tag "v$version"
    Write-Host ">>> Tag v$version created" -ForegroundColor Green
}
git push origin "v$version"
Write-Host ">>> Tag pushed to remote" -ForegroundColor Green

# ── Step 3: Build ───────────────────────────────────────────────────
Write-Host "`n>>> [2/8] Building package..." -ForegroundColor Yellow
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
uv build
Write-Host ">>> Build artifacts:" -ForegroundColor Green
Get-ChildItem dist/ | ForEach-Object { Write-Host "    $_" }

# ── Step 4: Publish to TestPyPI ─────────────────────────────────────
Write-Host "`n>>> [3/8] Publishing to TestPyPI (final verification)..." -ForegroundColor Yellow
if (-not $env:TESTPYPI_TOKEN) {
    Write-Host ">>> TESTPYPI_TOKEN not set — skipping TestPyPI" -ForegroundColor DarkYellow
} else {
    uv run twine upload --repository testpypi --skip-existing dist/*
    Write-Host ">>> Published to TestPyPI" -ForegroundColor Green
}

# ── Step 5: Verify TestPyPI ─────────────────────────────────────────
Write-Host "`n>>> [4/8] Verifying TestPyPI install..." -ForegroundColor Yellow
if (-not $env:TESTPYPI_TOKEN) {
    Write-Host ">>> Skipped (no TestPyPI publish)" -ForegroundColor DarkYellow
} else {
    $maxAttempts = 5
    for ($i = 1; $i -le $maxAttempts; $i++) {
        Write-Host "    Attempt ${i}: installing agentops-toolkit==$version"
        $result = pip install "agentops-toolkit==$version" `
            --index-url https://test.pypi.org/simple/ `
            --extra-index-url https://pypi.org/simple/ 2>&1
        if ($LASTEXITCODE -eq 0) { break }
        if ($i -lt $maxAttempts) {
            Write-Host "    Not available yet, waiting 30s..."
            Start-Sleep -Seconds 30
        }
    }
    agentops --version
    Write-Host ">>> TestPyPI verification passed" -ForegroundColor Green
}

# ── Step 6: Publish to PyPI (production) ────────────────────────────
Write-Host "`n>>> [5/8] Publishing to PyPI (PRODUCTION)..." -ForegroundColor Yellow
if (-not $env:PYPI_TOKEN) {
    Write-Host ">>> PYPI_TOKEN not set — skipping PyPI publish" -ForegroundColor DarkYellow
    Write-Host "    Set it with: `$env:PYPI_TOKEN = 'pypi-...'" -ForegroundColor DarkGray
} else {
    $confirm = Read-Host "Publish v$version to PyPI (PRODUCTION)? (yes/no)"
    if ($confirm -ne "yes") {
        Write-Host ">>> Aborted PyPI publish" -ForegroundColor Red
        exit 1
    }
    uv run twine upload dist/*
    Write-Host ">>> Published to PyPI" -ForegroundColor Green
}

# ── Step 7: Build and publish VSIX stable ───────────────────────────
Write-Host "`n>>> [6/8] Building VSIX stable..." -ForegroundColor Yellow
$vsceAvailable = Get-Command vsce -ErrorAction SilentlyContinue
if (-not $vsceAvailable) {
    Write-Host ">>> vsce not found — skipping VSIX" -ForegroundColor DarkYellow
    $skipVSIX = $true
} else {
    $pkgPath = "plugins/agentops/package.json"
    $pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
    $pkg.version = $version
    $pkg | ConvertTo-Json -Depth 10 | Set-Content $pkgPath -NoNewline

    Copy-Item CHANGELOG.md plugins/agentops/CHANGELOG.md -Force
    Copy-Item icon.png plugins/agentops/icon.png -Force -ErrorAction SilentlyContinue

    Push-Location plugins/agentops
    vsce package -o agentops-skills.vsix
    Write-Host ">>> VSIX built: agentops-skills.vsix (v$version)" -ForegroundColor Green

    if (-not $env:VSCE_PAT) {
        Write-Host ">>> VSCE_PAT not set — skipping Marketplace publish" -ForegroundColor DarkYellow
    } else {
        # Verify the VSIX package.json matches the release version
        $vsixPkg = Get-Content package.json -Raw | ConvertFrom-Json
        if ($vsixPkg.version -ne $version) {
            Write-Error "VSIX version mismatch! package.json=$($vsixPkg.version), expected=$version. Aborting publish."
            Pop-Location
            exit 1
        }
        vsce publish --packagePath agentops-skills.vsix -p $env:VSCE_PAT
        Write-Host ">>> VSIX stable published to Marketplace (v$version)" -ForegroundColor Green
    }
    Pop-Location
}

# ── Step 8: Create GitHub Release ───────────────────────────────────
Write-Host "`n>>> [7/8] Creating GitHub Release..." -ForegroundColor Yellow
$releaseAssets = @("dist/*")
if (-not $skipVSIX -and (Test-Path "plugins/agentops/agentops-skills.vsix")) {
    $releaseAssets += "plugins/agentops/agentops-skills.vsix"
}
$assetArgs = $releaseAssets -join " "
Invoke-Expression "gh release create v$version $assetArgs --title `"v$version`" --generate-notes"
Write-Host ">>> GitHub Release v$version created" -ForegroundColor Green

# ── Step 9: Sync develop ────────────────────────────────────────────
Write-Host "`n>>> [8/8] Syncing develop from main..." -ForegroundColor Yellow
$syncConfirm = Read-Host "Sync develop from main? (yes/no)"
if ($syncConfirm -eq "yes") {
    git checkout develop
    git merge main
    git push origin develop
    Write-Host ">>> develop synced from main" -ForegroundColor Green
} else {
    Write-Host ">>> Skipped develop sync" -ForegroundColor DarkYellow
    Write-Host "    Run manually: git checkout develop; git merge main; git push origin develop" -ForegroundColor DarkGray
}

# ── Summary ─────────────────────────────────────────────────────────
Write-Host "`n✅ Release v$version complete!" -ForegroundColor Green
Write-Host "  Tag:      v$version" -ForegroundColor Cyan
Write-Host "  PyPI:     https://pypi.org/project/agentops-toolkit/$version/" -ForegroundColor Cyan
Write-Host "  GitHub:   https://github.com/Azure/agentops/releases/tag/v$version" -ForegroundColor Cyan
if (-not $skipVSIX) {
    Write-Host "  VSIX:     https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-toolkit" -ForegroundColor Cyan
}
