@echo off
REM TradingBot — lanzador rápido para Windows
REM Uso: doble clic o desde cmd: run_bot.bat [comando]
REM Comandos: cycle | scanner | report | backtest | dashboard | optimize

setlocal
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

if "%~1"=="" goto cycle
if /i "%~1"=="cycle" goto cycle
if /i "%~1"=="scanner" goto scanner
if /i "%~1"=="report" goto report
if /i "%~1"=="backtest" goto backtest
if /i "%~1"=="dashboard" goto dashboard
if /i "%~1"=="optimize" goto optimize
if /i "%~1"=="live" goto live

echo Comandos: cycle, scanner, report, backtest, dashboard, optimize, live
exit /b 1

:cycle
echo === Ciclo diario (paper) ===
"%PYTHON%" -m tradingbot.main
exit /b %ERRORLEVEL%

:scanner
echo === Test scanner ===
"%PYTHON%" -m tradingbot.main --test-scanner
exit /b %ERRORLEVEL%

:report
echo === Reporte ===
"%PYTHON%" -m tradingbot.report
exit /b %ERRORLEVEL%

:backtest
echo === Backtest avanzado ===
"%PYTHON%" -m tradingbot.backtest --start 2018-01-01 --advanced
exit /b %ERRORLEVEL%

:dashboard
echo === Generar dashboard ===
"%PYTHON%" -m tradingbot.main --build-dashboard
exit /b %ERRORLEVEL%

:optimize
echo === Optimizar parametros ===
"%PYTHON%" scripts\optimize_params.py --start 2018-01-01 --quick
exit /b %ERRORLEVEL%

:live
echo === LIVE — requiere confirmacion ===
set TRADINGBOT_LIVE_CONFIRM=YES
"%PYTHON%" -m tradingbot.main --confirm-live
exit /b %ERRORLEVEL%