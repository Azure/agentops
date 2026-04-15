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
# Usage:  .\.local\staging.ps1
# Prereqs:
#   - uv installed
#   - twine: pip install twine (for TestPyPI upload)
#   - npm + vsce: npm install -g @vscode/vsce
#   - TESTPYPI_TOKEN env var (API token from test.pypi.org)
#   - VSCE_PAT env var (VS Code Marketplace PAT)
# ─────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$skipTestPyPI = $false
$skipVSIX = $false

# ── Step 1: Lint ────────────────────────────────────────────────────
Write-Host "`n>>> [1/7] Linting with ruff..." -ForegroundColor Yellow
uv run ruff check src/ tests/
Write-Host ">>> Lint passed" -ForegroundColor Green

# ── Step 2: Test ────────────────────────────────────────────────────
Write-Host "`n>>> [2/7] Running tests..." -ForegroundColor Yellow
uv run pytest tests/ -v --tb=short
Write-Host ">>> Tests passed" -ForegroundColor Green

# ── Step 3: Build ───────────────────────────────────────────────────
Write-Host "`n>>> [3/7] Building package..." -ForegroundColor Yellow
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
uv build
Write-Host ">>> Build artifacts:" -ForegroundColor Green
Get-ChildItem dist/ | ForEach-Object { Write-Host "    $_" }

# ── Step 4: Publish to TestPyPI ─────────────────────────────────────
Write-Host "`n>>> [4/7] Publishing to TestPyPI..." -ForegroundColor Yellow
if (-not $env:TESTPYPI_TOKEN) {
    Write-Host ">>> TESTPYPI_TOKEN not set — skipping TestPyPI publish" -ForegroundColor DarkYellow
    Write-Host "    Set it with: `$env:TESTPYPI_TOKEN = 'pypi-...'" -ForegroundColor DarkGray
    $skipTestPyPI = $true
} else {
    uv run twine upload --repository testpypi --skip-existing dist/*
    Write-Host ">>> Published to TestPyPI" -ForegroundColor Green
}

# ── Step 5: Verify TestPyPI install ─────────────────────────────────
Write-Host "`n>>> [5/7] Verifying TestPyPI install..." -ForegroundColor Yellow
if ($skipTestPyPI) {
    Write-Host ">>> Skipped (no TestPyPI publish)" -ForegroundColor DarkYellow
} else {
    $scmVersion = uv run python -m setuptools_scm 2>$null
    Write-Host "    Expected version: $scmVersion"
    $maxAttempts = 5
    for ($i = 1; $i -le $maxAttempts; $i++) {
        Write-Host "    Attempt ${i}..."
        $result = pip install "agentops-toolkit==$scmVersion" `
            --index-url https://test.pypi.org/simple/ `
            --extra-index-url https://pypi.org/simple/ 2>&1
        if ($LASTEXITCODE -eq 0) {
            break
        }
        if ($i -lt $maxAttempts) {
            Write-Host "    Not available yet, waiting 30s..."
            Start-Sleep -Seconds 30
        }
    }
    agentops --version
    agentops --help | Out-Null
    $tmpDir = New-TemporaryFile | ForEach-Object { Remove-Item $_; New-Item -ItemType Directory -Path $_ }
    Push-Location $tmpDir
    agentops init
    if (-not (Test-Path .agentops/config.yaml)) { Write-Error "Smoke test failed: config.yaml missing" }
    if (-not (Test-Path .agentops/run.yaml)) { Write-Error "Smoke test failed: run.yaml missing" }
    Pop-Location
    Remove-Item $tmpDir -Recurse -Force
    Write-Host ">>> TestPyPI verification passed" -ForegroundColor Green
}

# ── Step 6: Build VSIX ──────────────────────────────────────────────
Write-Host "`n>>> [6/7] Building VSIX pre-release..." -ForegroundColor Yellow
$vsceAvailable = Get-Command vsce -ErrorAction SilentlyContinue
if (-not $vsceAvailable) {
    Write-Host ">>> vsce not found — skipping VSIX build" -ForegroundColor DarkYellow
    Write-Host "    Install with: npm install -g @vscode/vsce" -ForegroundColor DarkGray
    $skipVSIX = $true
} else {
    # Sync version from latest git tag
    $lastTag = git tag -l 'v*' --sort=-v:refname | Select-Object -First 1
    if (-not $lastTag) { $lastTag = "v0.0.0" }
    $lastVersion = $lastTag -replace '^v', ''
    $parts = $lastVersion -split '\.'
    $exactTag = git describe --tags --exact-match HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $exactTag) {
        $baseVersion = $lastVersion
    } else {
        $baseVersion = "$($parts[0]).$($parts[1]).$([int]$parts[2] + 1)"
    }

    $pkgPath = "plugins/agentops/package.json"
    # Save original package.json so staging doesn't pollute the working tree
    $pkgOriginal = Get-Content $pkgPath -Raw
    $pkg = $pkgOriginal | ConvertFrom-Json
    $pkg.version = $baseVersion
    $pkg | ConvertTo-Json -Depth 10 | Set-Content $pkgPath -NoNewline

    Copy-Item CHANGELOG.md plugins/agentops/CHANGELOG.md -Force
    Copy-Item icon.png plugins/agentops/icon.png -Force -ErrorAction SilentlyContinue

    Push-Location plugins/agentops
    vsce package --pre-release -o agentops-skills.vsix
    Write-Host ">>> VSIX built: agentops-skills.vsix (v$baseVersion)" -ForegroundColor Green
    Pop-Location

    # Restore original package.json to prevent version drift
    Set-Content $pkgPath -Value $pkgOriginal -NoNewline
    Write-Host ">>> package.json restored to committed version" -ForegroundColor DarkGray
}

# ── Step 7: Publish VSIX pre-release ────────────────────────────────
Write-Host "`n>>> [7/7] Publishing VSIX pre-release..." -ForegroundColor Yellow
if ($skipVSIX) {
    Write-Host ">>> Skipped (vsce not available)" -ForegroundColor DarkYellow
} elseif (-not $env:VSCE_PAT) {
    Write-Host ">>> VSCE_PAT not set — skipping Marketplace publish" -ForegroundColor DarkYellow
    Write-Host "    Set it with: `$env:VSCE_PAT = 'your-pat'" -ForegroundColor DarkGray
} else {
    Push-Location plugins/agentops
    # Verify the VSIX contains the expected version before publishing
    $vsixPkg = Get-Content package.json -Raw | ConvertFrom-Json
    Write-Host "    VSIX will publish from packagePath (version in VSIX: $baseVersion)" -ForegroundColor DarkGray
    vsce publish --pre-release --packagePath agentops-skills.vsix -p $env:VSCE_PAT
    Pop-Location
    Write-Host ">>> VSIX pre-release published to Marketplace" -ForegroundColor Green
}

# ── Summary ─────────────────────────────────────────────────────────
Write-Host "`n✅ Staging complete!" -ForegroundColor Green
Write-Host "  Lint:     passed" -ForegroundColor Cyan
Write-Host "  Tests:    passed" -ForegroundColor Cyan
Write-Host "  Build:    dist/" -ForegroundColor Cyan
if (-not $skipTestPyPI) { Write-Host "  TestPyPI: published + verified" -ForegroundColor Cyan }
if (-not $skipVSIX) { Write-Host "  VSIX:     plugins/agentops/agentops-skills.vsix" -ForegroundColor Cyan }
Write-Host "`nNext: merge the PR, then run .\.local\release.ps1" -ForegroundColor DarkYellow
