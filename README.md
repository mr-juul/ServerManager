# Server Manager

A local Windows 10/11 desktop application for managing game and dedicated servers launched by `.bat` files. The core separates game definitions, installation detection, server instances, and live process state.

The UI has four sections: `SERVERS` for running configured instances, `GAMES` for dependency and installation detection, `MODS` for library and profile management, and `SETTINGS`. Use `+ NY SERVER` to create an instance without editing JSON. The JSON files remain persistence/storage, not the primary workflow.

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

The desktop application is local-first. Network access is only used if you explicitly enable Web Control.

## Web Control (Remote)

Server Manager now includes an optional built-in Web Control service for simple remote operations only:

- View servers
- View server status
- Start / Stop / Restart
- View read-only logs

It does **not** allow creating/deleting servers, changing configuration, running arbitrary commands, or editing paths.

Enable it from desktop `Indstillinger`:

- `Web Control` -> `Enabled`
- Set `Port` (default `8080`)
- Set `Bind address` (default `0.0.0.0` for LAN access)
- Set a Web Control password (`Skift web-password`)

When enabled, open the URL shown in Settings, for example:

```text
http://192.168.0.104:8080
```

Security notes:

- Passwords are stored as Argon2 hashes, never plaintext
- Session cookie is HttpOnly with SameSite=lax
- API write actions use CSRF token checks
- Login attempts are throttled
- Logs are sanitized before being sent to browser
- Use HTTPS before exposing beyond trusted LAN

## External Web Control API (v1)

Server Manager now provides a narrow versioned API for external remote-control websites.

Available endpoints:

- GET /api/v1/status
- GET /api/v1/games
- GET /api/v1/games/{game_id}
- GET /api/v1/games/{game_id}/create-schema
- GET /api/v1/games/{game_id}/worlds
- GET /api/v1/servers
- GET /api/v1/servers/{server_id}
- POST /api/v1/servers
- GET /api/v1/jobs/{job_id}
- POST /api/v1/servers/{server_id}/start
- POST /api/v1/servers/{server_id}/stop
- POST /api/v1/servers/{server_id}/restart
- GET /api/v1/servers/{server_id}/logs
- GET /api/v1/servers/{server_id}/players
- GET /api/v1/servers/{server_id}/settings
- PATCH /api/v1/servers/{server_id}/settings
- GET /api/v1/mods
- GET /api/v1/mods/{mod_id}
- GET /api/v1/servers/{server_id}/mods
- POST /api/v1/servers/{server_id}/mods/{mod_id}/enable
- POST /api/v1/servers/{server_id}/mods/{mod_id}/disable
- POST /api/v1/servers/{server_id}/mods/{mod_id}/install
- POST /api/v1/servers/{server_id}/mods/{mod_id}/uninstall
- GET /api/v1/mod-profiles
- POST /api/v1/servers/{server_id}/mod-profile
- GET /api/v1/servers/{server_id}/backups
- POST /api/v1/servers/{server_id}/backups
- POST /api/v1/servers/{server_id}/backups/{backup_id}/restore

Response details:

- `GET /api/v1/servers/{id}/settings` also returns `field_schema` so website labels/types can be game-specific.
- `GET /api/v1/servers/{id}/backups` also returns `restore_history` for recent restore events.
  - restore history items include `status`, `error`, and optional `safety_backup_id`.

Website UI notes:

- Navigation includes `Servers`, `Players`, `Mods`, and `Backups`.
- `Players` view is server-selectable and only shows safe player information.

Security behavior:

- No executable paths, BAT paths, directories, or command-lines are returned.
- Actions are limited to start/stop/restart only.
- Concurrent start/stop/restart calls per server are locked.
- v1 accepts either authenticated web session or API key header `X-API-Key`.

### External website components

This repository includes separate components:

- Website frontend: [website/index.html](website/index.html)
- Website backend: [website_backend/app.py](website_backend/app.py)
- Server Manager API client for backend: [website_backend/server_manager_client.py](website_backend/server_manager_client.py)

Desktop one-click option:

- In `Indstillinger`, under `EKSTERN REMOTE SIDE`, use:
  - `START REMOTE SIDE`
  - `ÅBN REMOTE SIDE`
  - `STOP REMOTE SIDE`

Server Manager will generate/store an API key automatically and wire backend environment values for you.

### Run external website backend

Set these environment variables before launch:

- `SERVER_MANAGER_API_KEY`: used by Server Manager v1 API (desktop app process env)
- `SM_API_KEY`: same key used by website backend client
- `SM_API_BASE_URL`: usually `http://127.0.0.1:8080`

Start backend:

```powershell
c:/Users/Mr-Ju/ServerManager/.venv/Scripts/python.exe -m uvicorn website_backend.app:app --host 0.0.0.0 --port 8090
```

Then open:

```text
http://<server-ip>:8090
```

The website stays a thin remote control: login, list servers, start, stop, restart, logs, and create server for games where `create_server` capability is true.

## Public internet access (simple)

If you want access from anywhere without Cloudflare, the simplest setup is direct HTTP with DNS + router port forward.

1. Enable Web Control in the app and set a strong password.
2. Run this script as Administrator:

```powershell
.\scripts\setup_public_webcontrol.ps1 -Domain sm.e-bold.dk -Port 8080
```

3. In your router, forward TCP `8080` to your server PC LAN IP on port `8080`.
4. In DNS for your domain, create an A-record:
  - `sm.e-bold.dk` -> your public IP

Then open:

```text
http://sm.e-bold.dk:8080
```

Important:

- This is easy but not encrypted (HTTP).
- Keep a strong web password and only expose the port you need.
- For better security later, place HTTPS reverse proxy in front (for example Caddy on port 443).

## Mod Management (Phase 1/2)

The `MODS` section now provides a local mod library and server-specific mod assignment:

- Import local `.zip` and `.dll` mods into the central library
- Keep per-game mod entries with installed versions and latest version metadata
- Open `MODS` on each server card to enable, disable, or uninstall mods for that server
- Save and apply per-game mod profiles to toggle sets of mods quickly

Current behavior notes:

- `Disable` keeps the mod in library and marks it inactive on that server
- `Uninstall` removes the mod from that server's managed mod folder
- If a server is running, the UI prompts to stop it before making mod changes

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

## Release automation (GitHub Actions)

This repository includes three release-related workflows:

- `.github/workflows/release-windows.yml`
  - Stable releases for tags like `v1.5.0`
  - Builds `ServerManager.exe`, `ServerManagerWatchdog.exe`, and `ServerManagerUpdater.exe`
  - Packages `dist/ServerManager.zip`
  - Publishes a normal GitHub Release
- `.github/workflows/release-windows-beta.yml`
  - Prereleases for tags like `v1.6.0-beta.1` or `v1.6.0-rc.1`
  - Publishes a GitHub prerelease with `dist/ServerManager.zip`
- `.github/workflows/sync-version-from-tag.yml`
  - Syncs `core/version.py` (`APP_VERSION`) to the pushed tag version and commits it to `main`

Optional code signing for release workflows:

- Add repository secret `WINDOWS_CERTIFICATE_BASE64` with your PFX encoded as base64
- Add repository secret `WINDOWS_CERTIFICATE_PASSWORD` with the PFX password

If both secrets are present, the workflows sign:

- `dist/ServerManager/ServerManager.exe`
- `dist/ServerManager/ServerManagerWatchdog.exe`
- `dist/ServerManager/ServerManagerUpdater.exe`

## Release checklist

Stable release:

```powershell
git checkout main
git pull
git tag v1.0.0
git push origin v1.0.0
```

Beta release:

```powershell
git checkout main
git pull
git tag v1.1.0-beta.1
git push origin v1.1.0-beta.1
```

After tag push, GitHub Actions builds and uploads `ServerManager.zip` to the corresponding GitHub Release.

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
