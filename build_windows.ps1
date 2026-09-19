$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$python = Join-Path $project "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name ServerManager main.py

$target = Join-Path $project "dist\ServerManager"
New-Item -ItemType Directory -Force -Path (Join-Path $target "config") | Out-Null
Copy-Item -Force .\config\servers.json (Join-Path $target "config\servers.json")
Copy-Item -Force .\config\games.json (Join-Path $target "config\games.json")
New-Item -ItemType Directory -Force -Path (Join-Path $target "logs") | Out-Null

Write-Host "Built: $target\ServerManager.exe"
Write-Host "Copy the complete dist\ServerManager folder to the Windows PC."
