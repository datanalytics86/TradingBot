<#
.SYNOPSIS
    Pide las API keys de Alpaca, actualiza .env y ejecuta setup completo A+B.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/enter_keys_and_finish.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== TradingBot: ingreso de keys y setup A+B ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Obtén tus keys en: https://app.alpaca.markets/paper/dashboard/overview" -ForegroundColor Yellow
Write-Host "(API Keys -> Generate new key)" -ForegroundColor Yellow
Write-Host ""

$apiKey = Read-Host "ALPACA_API_KEY (empieza con PK...)"
$secret = Read-Host "ALPACA_SECRET_KEY" -AsSecureString
$secretPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
)

if (-not $apiKey -or $apiKey -match 'tu_') {
    Write-Host "API key inválida." -ForegroundColor Red
    exit 1
}
if (-not $secretPlain -or $secretPlain -match 'tu_') {
    Write-Host "Secret inválido." -ForegroundColor Red
    exit 1
}

$envPath = Join-Path $Root ".env"
$lines = if (Test-Path $envPath) { Get-Content $envPath } else { Get-Content ".env.example" }
$out = @()
$seenKey = $false
$seenSecret = $false
foreach ($line in $lines) {
    if ($line -match '^ALPACA_API_KEY=') {
        $out += "ALPACA_API_KEY=$apiKey"
        $seenKey = $true
    }
    elseif ($line -match '^ALPACA_SECRET_KEY=') {
        $out += "ALPACA_SECRET_KEY=$secretPlain"
        $seenSecret = $true
    }
    else {
        $out += $line
    }
}
if (-not $seenKey) { $out += "ALPACA_API_KEY=$apiKey" }
if (-not $seenSecret) { $out += "ALPACA_SECRET_KEY=$secretPlain" }
$out | Set-Content $envPath -Encoding utf8

Write-Host ""
Write-Host ".env actualizado. Ejecutando setup completo..." -ForegroundColor Green
powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "complete_setup.ps1")