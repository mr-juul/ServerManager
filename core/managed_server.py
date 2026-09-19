from __future__ import annotations

import shlex
from pathlib import Path

from .config import ServerConfig
from .games import game_definition


def _bat_quote(value: str) -> str:
    text = str(value)
    return '"' + text.replace('"', '""') + '"'


def resolve_server_executable(config: ServerConfig, installation: str) -> Path:
    location = Path(installation)
    if location.is_file():
        return location
    names = game_definition(config.game).server_search_names
    for name in names:
        direct = location / name
        if direct.is_file():
            return direct
    for name in names:
        matches = list(location.rglob(name))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"Fandt ingen kendt server-executable i {location}")


def generate_managed_server(config: ServerConfig, root: Path, installation: str) -> ServerConfig:
    """Create a managed server folder and launcher without touching external files."""
    if not installation.strip():
        raise ValueError("Vælg serverens installationsmappe")
    executable = resolve_server_executable(config, installation)
    server_dir = root / "servers" / config.id
    server_dir.mkdir(parents=True, exist_ok=True)
    script = server_dir / "start.bat"
    lines = ["@echo off", "setlocal", f"cd /d {_bat_quote(str(server_dir))}", f"set SERVER_EXE={_bat_quote(executable)}"]
    if config.game == "valheim":
        args = [
            "-name", config.name,
            "-port", str(config.port),
            "-world", config.world or config.name,
            "-password", config.password,
            "-public", "1" if config.public else "0",
        ]
        if config.crossplay:
            args.append("-crossplay")
        args.extend(shlex.split(config.additional_arguments, posix=False))
        command = " ".join(["%SERVER_EXE%", *(_bat_quote(arg) if " " in arg else arg for arg in args)])
    else:
        command = " ".join(["%SERVER_EXE%", config.additional_arguments]).strip()
    lines.extend([command, "set EXIT_CODE=%ERRORLEVEL%", "exit /b %EXIT_CODE%", ""])
    script.write_text("\n".join(lines), encoding="utf-8")
    config.script = str(script)
    config.working_directory = str(server_dir)
    return config
