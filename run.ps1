<#
  mtgstack launcher for Windows (PowerShell). The first run sets everything up.

    .\run.ps1           start the website at http://localhost:8765
    .\run.ps1 -Dev      same, and restart automatically when you edit the code
    .\run.ps1 -Tailscale  open it on your iPhone from anywhere via Tailscale (real HTTPS, no warnings)
    .\run.ps1 -Phone    also reachable from your phone on the same Wi-Fi, over HTTPS so the live camera works
    .\run.ps1 -Test     run the test suite
#>
param([switch]$Dev, [switch]$Phone, [switch]$Tailscale, [switch]$Test, [int]$Port = 8765)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- Python -------------------------------------------------------------------------------------
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "First run: creating .venv ..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
    else {
        Write-Host "Python isn't installed. Run:  winget install Python.Python.3.12" -ForegroundColor Yellow
        Write-Host "then close and reopen this terminal and run .\run.ps1 again."
        exit 1
    }
    if ($LASTEXITCODE -ne 0) { throw "could not create the virtual environment" }
}

# (Re)install when pyproject.toml is newer than the last install.
$stamp = Join-Path $PSScriptRoot ".venv\.installed"
if (-not (Test-Path $stamp) -or (Get-Item pyproject.toml).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    Write-Host "Installing dependencies (a minute or two the first time) ..." -ForegroundColor Cyan
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -e ".[dev]" --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install failed - see the messages above" }
    New-Item $stamp -ItemType File -Force | Out-Null
}

# --- Tesseract (only the scanner needs it) ----------------------------------------------------------
$hasTess = (Get-Command tesseract -ErrorAction SilentlyContinue) -or
           (Test-Path "$env:ProgramFiles\Tesseract-OCR\tesseract.exe") -or
           (Test-Path "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")
if (-not $hasTess) {
    Write-Warning "Tesseract OCR not found: everything works except card scanning. Install it with:  winget install UB-Mannheim.TesseractOCR"
}

if ($Test) { & $venvPy -m pytest -q; exit $LASTEXITCODE }

if (-not $env:MTGSTACK_SCANS) { $env:MTGSTACK_SCANS = Join-Path $PSScriptRoot "scan-data" }

# --- Start ---------------------------------------------------------------------------------------
$serveArgs = @("-m", "mtgstack.cli", "serve", "--port", "$Port")
if ($Phone) {
    $serveArgs += @("--host", "0.0.0.0", "--https")
    Write-Host "If Windows Firewall asks, allow Python on PRIVATE networks so your phone can connect." -ForegroundColor Yellow
}
if ($Tailscale) { $serveArgs += "--tailscale" }
if ($Dev) { $serveArgs += "--reload" }
$url = if ($Phone) { "https://localhost:$Port" } else { "http://localhost:$Port" }
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList $url | Out-Null
& $venvPy @serveArgs
