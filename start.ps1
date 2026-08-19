$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# npm global shims live here by default on Windows, but older terminals may
# not have picked up the PATH change yet.
$npmBin = Join-Path $env:APPDATA "npm"
if ((Test-Path -LiteralPath $npmBin) -and (($env:PATH -split ";") -notcontains $npmBin)) {
    $env:PATH = "$npmBin;$env:PATH"
}

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $python.Source }
    }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
        if ($LASTEXITCODE -eq 0) { return "$($launcher.Source) -3" }
    }

    throw "Python 3.10+ not found. Install Python first: https://www.python.org/downloads/windows/"
}

$pythonCommand = Find-Python
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating local Python environment (.venv)..."
    if ($pythonCommand.EndsWith(" -3")) {
        $launcherPath = $pythonCommand.Substring(0, $pythonCommand.Length - 3)
        & $launcherPath -3 -m venv .venv
    } else {
        & $pythonCommand -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }
}

& $venvPython -c "import fastapi, uvicorn, httpx, html2text, yfinance" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..."
    & $venvPython -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies" }
} else {
    Write-Host "Python dependencies are ready."
}

if (-not (Get-Command okx -ErrorAction SilentlyContinue)) {
    Write-Warning "okx CLI is not installed. Account and market data need: npm install -g @okx_ai/okx-trade-cli"
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "claude CLI is not installed. AI reports need: npm install -g @anthropic-ai/claude-code"
}

& $venvPython run_dashboard.py @args
