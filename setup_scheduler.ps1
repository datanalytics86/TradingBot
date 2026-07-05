# Registra una tarea programada de Windows para ejecutar el bot automáticamente
# cada día hábil después del cierre de NYSE.
#
# Uso (PowerShell como usuario normal):
#   .\setup_scheduler.ps1
#   .\setup_scheduler.ps1 -Hour 18 -Minute 45   # hora local Chile (aprox. post-cierre NYSE)

param(
    [int]$Hour = 18,
    [int]$Minute = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $Python = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    throw "No se encontró python en PATH. Instala Python 3.11+ primero."
}

$TaskName = "TradingBot-DailyCycle"
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m tradingbot.main" `
    -WorkingDirectory $ProjectRoot

# Lunes a viernes a la hora indicada (hora local del sistema)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At (Get-Date -Hour $Hour -Minute $Minute -Second 0)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Ejecuta el ciclo diario del TradingBot (Alpaca paper/live)" `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' registrada." -ForegroundColor Green
Write-Host "  Python:  $Python"
Write-Host "  Proyecto: $ProjectRoot"
Write-Host "  Horario:  L-V a las $($Hour.ToString('00')):$($Minute.ToString('00')) (hora local)"
Write-Host ""
Write-Host "Asegúrate de tener .env con ALPACA_API_KEY y ALPACA_SECRET_KEY en:" -ForegroundColor Yellow
Write-Host "  $ProjectRoot\.env"
Write-Host ""
Write-Host "Para probar ahora: python -m tradingbot.main" -ForegroundColor Cyan
Write-Host "Para ver estado:   python -m tradingbot.report" -ForegroundColor Cyan