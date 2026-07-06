# Wrapper para la tarea programada: carga .env, ejecuta el ciclo y guarda log.
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("scheduled_{0:yyyy-MM-dd_HH-mm}.log" -f (Get-Date))

$Python = $null
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $Python = $venvPy
}
if (-not $Python) {
    foreach ($cmd in @("python", "python3")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found -and $found.Source -notmatch '\\WindowsApps\\') {
            $Python = $found.Source
            break
        }
    }
}
if (-not $Python) {
    $searchRoots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:LOCALAPPDATA\Python"
    )
    $localPy = $searchRoots |
        ForEach-Object { Get-ChildItem $_ -Recurse -Filter "python.exe" -ErrorAction SilentlyContinue } |
        Where-Object { $_.FullName -notmatch '\\WindowsApps\\' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if ($localPy) { $Python = $localPy }
}
if (-not $Python) {
    "ERROR: Python no encontrado" | Out-File $LogFile -Encoding utf8
    exit 1
}

"=== TradingBot ciclo $(Get-Date -Format o) ===" | Out-File $LogFile -Encoding utf8
"& $Python -m tradingbot.main" | Out-File $LogFile -Append -Encoding utf8

& $Python -m tradingbot.main *>> $LogFile
exit $LASTEXITCODE