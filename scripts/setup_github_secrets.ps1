<#
.SYNOPSIS
    Carga los secrets de .env a GitHub Actions para TradingBot.

.REQUIRES
    GitHub CLI (gh) autenticado, o variable GH_TOKEN con un PAT válido.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/setup_github_secrets.ps1
#>

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectDir ".env"

if (-not (Test-Path $EnvFile)) {
    Write-Error ".env no encontrado en $ProjectDir. Copia .env.example primero."
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Error "Instala GitHub CLI: winget install GitHub.cli"
}

Set-Location $ProjectDir
$repo = gh repo view --json nameWithOwner -q .nameWithOwner
Write-Host "Configurando secrets en: $repo" -ForegroundColor Cyan

$keys = @(
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "POLYGON_API_KEY",
    "GROK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID"
)

$set = 0
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([^#=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $val = $matches[2].Trim()
        if ($key -in $keys -and $val -and $val -notmatch '^(tu_|PENDIENTE)') {
            Write-Host "  -> $key"
            $val | gh secret set $key --repo $repo
            $set++
        }
    }
}

if ($set -eq 0) {
    Write-Host ""
    Write-Host "AVISO: No se configuró ningún secret (faltan valores reales en .env)." -ForegroundColor Yellow
    Write-Host "Edita .env con ALPACA_API_KEY y ALPACA_SECRET_KEY de https://app.alpaca.markets/paper"
    exit 1
}

Write-Host ""
Write-Host "OK: $set secret(s) configurados en GitHub." -ForegroundColor Green
Write-Host "Siguiente: gh workflow run trading-bot.yml --repo $repo"