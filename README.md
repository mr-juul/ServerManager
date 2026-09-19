# Server Manager

A local Windows 10/11 desktop application for managing game and dedicated servers launched by `.bat` files. The core separates game definitions, installation detection, server instances, and live process state.

The UI has three sections: `SERVERS` for running configured instances, `GAMES` for dependency and installation detection, and `SETTINGS`. Use `+ NY SERVER` to create an instance without editing JSON. The JSON files remain persistence/storage, not the primary workflow.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer
- The server's existing `.bat` file and working directory

## Install and run

Open PowerShell in this directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

If PowerShell blocks activation, run the interpreter directly:

```powershell
.\.venv\Scripts\python.exe main.py
```

The application is local-only and opens no network port.

## Configure the existing Valheim BAT

Edit `config/servers.json`. Replace the example `script` and `working_directory` values with the real paths. Do not recreate the Valheim command line in Server Manager; it executes your existing BAT unchanged.

```json
{
  "id": "valheim-kirken",
  "name": "Valheim - Kirken",
  "script": "C:\\Servers\\Valheim\\start.bat",
  "working_directory": "C:\\Servers\\Valheim",
  "auto_start": true,
  "auto_restart": true,
  "restart_delay": 10,
  "max_restarts": 5,
  "restart_window_minutes": 15,
  "backup": {
    "enabled": false,
    "source": "C:\\Servers\\Valheim\\worlds",
    "destination": "D:\\ServerBackups\\Valheim",
    "retention_days": 30
  }
}
```

JSON paths use escaped backslashes. The BAT must be readable and the working directory must exist. Add more objects to `servers` for other servers. Each `id` must be unique.

## Features

The application starts and stops the BAT process tree, monitors unexpected termination, keeps a 2,000-line rolling console buffer, writes daily server logs under `logs/<server-id>/`, and records application events in `logs/application.log`. Auto-restart is bounded by `max_restarts` within `restart_window_minutes`; once exceeded, the server is marked `CRASH LOOP`.

When creating a server, choose `Opret start-script og arbejdsmappe automatisk` and select the real server executable. Server Manager then creates `servers/<server-id>/start.bat` and its working directory inside the portable application folder. For Valheim, the generated script includes name, world, password, port, public, crossplay, and extra arguments. Existing BAT files remain unchanged when the automatic option is not selected.

The `GAMES` section contains definitions for Valheim, Minecraft Java, Terraria, Project Zomboid, Palworld, 7 Days to Die, ARK, Rust, Factorio, Satisfactory, V Rising, Enshrouded, and other common dedicated-server games, plus Generic/Custom. Detection checks practical local signals such as Java on `PATH`, Steam roots and library folders, configured server folders, and known server executables. A game is not reported `READY` merely because it is listed in the catalog.

Valheim instances additionally support world, password, port, public, crossplay, and extra arguments. Passwords are not shown on server cards or written to application/process logs. Selecting a BAT through the new-server dialog never modifies that BAT; it is executed as-is.

For Valheim, configure the folder containing the world/save files in `World-mappe` in the server editor. The server card then provides `ÅBN WORLDMAPPE` and `BACKUP .TAR.ZST`. A manual backup creates a timestamped archive in that world folder, preserves the source files, and removes only old Server Manager archives after the configured retention period.

The tray menu keeps the manager alive when the window is closed. Choose `Exit` from the tray or File menu to stop managed servers and terminate the manager. The main window also includes system CPU, RAM, disk, and uptime metrics, all-server controls, folder access, and manual backup for enabled entries.

## Windows startup

Create a shortcut in the Startup folder (`Win+R`, enter `shell:startup`) whose target is:

```text
C:\Path\To\Python\pythonw.exe C:\Path\To\server_manager\main.py
```

Alternatively point the shortcut at a PyInstaller executable. Keep `Start Manager` separate from each server's `auto_start` setting.

## Run without VS Code

Build a portable Windows folder from PowerShell in `server_manager`:

```powershell
.\build_windows.ps1
```

Copy the complete `dist\ServerManager` folder to the target PC. Double-click:

```text
dist\ServerManager\ServerManager.exe
```

Keep the `config` folder beside the EXE. The application writes its editable configuration and logs there, so it does not need Python, VS Code, or the source project on the target PC. Update the BAT path through `+ NY SERVER` after copying the folder.

To create a desktop shortcut, right-click `ServerManager.exe`, choose `Show more options` and `Create shortcut`, then move the shortcut to the desktop. For Windows startup, place a shortcut in `Win+R` → `shell:startup`.

## Build an EXE

From the project directory:

```powershell
python -m PyInstaller --noconfirm --clean --onedir --windowed --name ServerManager main.py
```

The supplied `build_windows.ps1` runs this command and copies both configuration files beside the executable. The complete `dist\ServerManager` directory is the portable application; do not copy only the EXE because PySide6 runtime files are included in the neighboring folders.

## Test

```powershell
python -m pytest -q
```

The tests validate configuration, backups, retention cleanup, and start/stop behavior without attempting to run Valheim itself.

## Troubleshooting

- `Script not found`: verify the absolute BAT path and JSON escaping.
- `Working directory not found`: verify the directory exists and is accessible by the Windows account running the manager.
- A server immediately becomes `CRASHED`: run the BAT manually in its working directory and inspect `logs/<id>/` plus `logs/application.log`.
- A child executable survives stop: ensure the BAT does not deliberately detach the server from its process tree; the manager terminates the tracked Windows process tree only.
- Invalid JSON: validate commas, quotes, and doubled backslashes. The application reports configuration errors instead of starting with partial settings.
