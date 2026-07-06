<#
.SYNOPSIS
    Guía interactiva para configurar notificaciones de Telegram (local + GitHub).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_telegram.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== Configurar Telegram ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Abre @BotFather en Telegram y crea un bot con /newbot" -ForegroundColor Yellow
Write-Host "2. Envia /start a tu bot recien creado" -ForegroundColor Yellow
Write-Host ""

Start-Process "https://t.me/BotFather"

$token = Read-Host "Pega TELEGRAM_BOT_TOKEN (formato 123456:ABC...)"
$chatId = Read-Host "Pega TELEGRAM_CHAT_ID (Enter para detectarlo automaticamente)"

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}

$args = @("scripts\setup_telegram.py", $token)
if ($chatId) { $args += $chatId }

& $py @args
exit $LASTEXITCODE