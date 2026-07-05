<#
.SYNOPSIS
    Habilita permisos de GitHub Actions y dispara el workflow manualmente.
#>

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Error "Instala GitHub CLI: winget install GitHub.cli"
}

$repo = gh repo view --json nameWithOwner -q .nameWithOwner
Write-Host "Configurando Actions en: $repo" -ForegroundColor Cyan

# Permisos del GITHUB_TOKEN: lectura/escritura para commitear state.json
gh api -X PUT "repos/$repo/actions/permissions/workflow" `
    -f default_workflow_permissions=write `
    -f can_approve_pull_request_reviews=false | Out-Null

Write-Host "  Permisos workflow: write" -ForegroundColor Green

# Disparar ciclo manual (workflow_dispatch)
gh workflow run "trading-bot.yml" --repo $repo
Write-Host "  Workflow disparado. Revisa: https://github.com/$repo/actions" -ForegroundColor Green