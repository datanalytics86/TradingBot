<#
.SYNOPSIS
    Ejecuta el setup completo de Opción A + B una vez que .env tiene keys reales.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/complete_setup.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== TradingBot: setup completo A + B ===" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creado .env desde plantilla." -ForegroundColor Yellow
}

$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch 'ALPACA_API_KEY=PK' -and $envContent -notmatch 'ALPACA_API_KEY=[A-Z0-9]{10,}') {
    Write-Host "Faltan keys de Alpaca en .env. Abriendo Alpaca Paper..." -ForegroundColor Yellow
    Start-Process "https://app.alpaca.markets/paper/dashboard/overview"
    Write-Host @"

Pega en .env:
  ALPACA_API_KEY=tu_key_id
  ALPACA_SECRET_KEY=tu_secret

Luego vuelve a ejecutar este script.
"@
    exit 1
}

Write-Host "[A] Instalando dependencias..." -ForegroundColor Green
python -m pip install -r requirements.txt -q

Write-Host "[A] Registrando tarea programada Windows..." -ForegroundColor Green
powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1

Write-Host "[A] Probando ciclo local (paper)..." -ForegroundColor Green
python -m tradingbot.main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Ciclo local terminó con código $LASTEXITCODE (revisa logs/bot.log)" -ForegroundColor Yellow
}

Write-Host "[B] Configurando GitHub Actions (secrets + workflow)..." -ForegroundColor Green
python scripts/github_actions_setup.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "=== Setup completo ===" -ForegroundColor Green
Write-Host "Local:  tarea TradingBot-DailyCycle (L-V 18:45)"
Write-Host "Nube:   https://github.com/datanalytics86/TradingBot/actions"
Write-Host "Estado: python -m tradingbot.report"