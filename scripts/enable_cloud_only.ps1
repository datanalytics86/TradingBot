<#
.SYNOPSIS
    Deja el bot 100% en GitHub Actions (sin depender de este PC encendido).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\enable_cloud_only.ps1
#>
$ErrorActionPreference = "Stop"
$TaskName = "TradingBot-DailyCycle"

Write-Host "=== Modo solo nube (GitHub Actions) ===" -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea local '$TaskName' eliminada." -ForegroundColor Green
} else {
    Write-Host "No habia tarea local '$TaskName' (ok)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "El bot correra automaticamente en GitHub Actions:" -ForegroundColor Green
Write-Host "  Horario: L-V 21:35 UTC (despues del cierre NYSE)"
Write-Host "  URL:     https://github.com/datanalytics86/TradingBot/actions"
Write-Host ""
Write-Host "Para disparar manualmente: Actions -> Ciclo diario -> Run workflow"
Write-Host "Para notificaciones: scripts\setup_telegram.ps1"