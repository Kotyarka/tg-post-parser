param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 or newer was not found in PATH."
}

if (-not (Test-Path -LiteralPath "config.yml")) {
    Copy-Item -LiteralPath "config.example.yml" -Destination "config.yml"
    Write-Host "Created config.yml. Fill in the settings in the WEB panel." -ForegroundColor Yellow
}

python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing WEB dependencies..." -ForegroundColor Cyan
    python -m pip install -e ".[web]"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install WEB dependencies."
    }
}

$arguments = @("-m", "tg_post_parser.web", "--config", "config.yml", "--port", $Port)
if (-not $NoBrowser) {
    $arguments += "--open-browser"
}

Write-Host "WEB panel: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the panel." -ForegroundColor DarkGray
& python @arguments
