# sync-skills.ps1 — Copy skills from the single source of truth
# (src/agentops/templates/skills/) to the VS Code extension
# (plugins/agentops/skills/).
#
# Run this after editing any SKILL.md in src/agentops/templates/skills/.
# CI will fail if the two directories diverge.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SrcDir = Join-Path $RepoRoot "src" "agentops" "templates" "skills"
$DestDir = Join-Path $RepoRoot "plugins" "agentops" "skills"

if (-not (Test-Path $SrcDir)) {
    Write-Error "Source directory not found: $SrcDir"
    exit 1
}

$synced = 0
foreach ($skillDir in Get-ChildItem -Path $SrcDir -Directory) {
    $srcFile = Join-Path $skillDir.FullName "SKILL.md"
    if (-not (Test-Path $srcFile)) {
        continue
    }

    $destSkillDir = Join-Path $DestDir $skillDir.Name
    if (-not (Test-Path $destSkillDir)) {
        New-Item -ItemType Directory -Path $destSkillDir -Force | Out-Null
    }

    $destFile = Join-Path $destSkillDir "SKILL.md"
    Copy-Item -Path $srcFile -Destination $destFile -Force
    $synced++
    Write-Host "  OK $($skillDir.Name)/SKILL.md"
}

Write-Host ""
Write-Host "Synced $synced skill(s) from src/agentops/templates/skills/ -> plugins/agentops/skills/"
