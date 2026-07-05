<#
.SYNOPSIS
    Abre Alpaca Paper Trading para generar API keys.
#>
Start-Process "https://app.alpaca.markets/paper/dashboard/overview"
Write-Host "1. Inicia sesión o crea cuenta paper (gratis)" -ForegroundColor Cyan
Write-Host "2. Ve a API Keys -> Generate new key" -ForegroundColor Cyan
Write-Host "3. Copia Key ID y Secret en .env:" -ForegroundColor Cyan
Write-Host "   ALPACA_API_KEY=..." -ForegroundColor Yellow
Write-Host "   ALPACA_SECRET_KEY=..." -ForegroundColor Yellow
Write-Host "4. Luego ejecuta:" -ForegroundColor Cyan
Write-Host "   powershell -File scripts/setup_github_secrets.ps1" -ForegroundColor Yellow
Write-Host "   powershell -File scripts/configure_github_actions.ps1" -ForegroundColor Yellow