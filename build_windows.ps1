$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$python = $null
$usePyLauncher = $false

$localVenv = Join-Path $project ".venv\Scripts\python.exe"
if (Test-Path $localVenv) {
	$python = $localVenv
}

if (-not $python) {
	$parentVenv = Join-Path $project "..\.venv\Scripts\python.exe"
	if (Test-Path $parentVenv) {
		$python = $parentVenv
	}
}

if (-not $python) {
	try {
		& py -3.11 --version *> $null
		$python = "py"
		$usePyLauncher = $true
	}
	catch {
	}
}

if (-not $python) {
	try {
		& python --version *> $null
		$python = "python"
	}
	catch {
	}
}

if (-not $python) {
	throw "No Python interpreter found. Install Python 3.11+ or create .venv in the project folder."
}

if ($usePyLauncher) {
	& $python -3.11 -m PyInstaller --noconfirm --clean --onedir --windowed --name ServerManager main.py
	& $python -3.11 -m PyInstaller --noconfirm --clean --onefile --console --name ServerManagerWatchdog watchdog_main.py
	& $python -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name ServerManagerUpdater updater_main.py
}
else {
	& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name ServerManager main.py
	& $python -m PyInstaller --noconfirm --clean --onefile --console --name ServerManagerWatchdog watchdog_main.py
	& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name ServerManagerUpdater updater_main.py
}

$target = Join-Path $project "dist\ServerManager"
New-Item -ItemType Directory -Force -Path (Join-Path $target "config") | Out-Null
Copy-Item -Force .\config\servers.json (Join-Path $target "config\servers.json")
Copy-Item -Force .\config\games.json (Join-Path $target "config\games.json")
New-Item -ItemType Directory -Force -Path (Join-Path $target "site\webcontrol") | Out-Null
Copy-Item -Force .\site\webcontrol\* (Join-Path $target "site\webcontrol")
New-Item -ItemType Directory -Force -Path (Join-Path $target "logs") | Out-Null
Copy-Item -Force .\dist\ServerManagerWatchdog.exe (Join-Path $target "ServerManagerWatchdog.exe")
Copy-Item -Force .\dist\ServerManagerUpdater.exe (Join-Path $target "ServerManagerUpdater.exe")

Write-Host "Built: $target\ServerManager.exe"
Write-Host "Built: $target\ServerManagerWatchdog.exe"
Write-Host "Built: $target\ServerManagerUpdater.exe"
Write-Host "Copy the complete dist\ServerManager folder to the Windows PC."
