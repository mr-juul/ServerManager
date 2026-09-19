from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameDefinition:
    id: str
    display_name: str
    description: str
    icon: str
    steam_app_id: int | None = None
    requires_java: bool = False
    server_search_names: tuple[str, ...] = ()
    has_world: bool = False
    supported: bool = True
    detection_capability: str = "FULL"
    installation_capability: str = "MANUAL"
    configuration_capability: str = "PARTIAL"
    mod_support_enabled: bool = False
    mod_adapter: str = "generic"
    mod_loader: str = ""


CATALOG: tuple[GameDefinition, ...] = (
    GameDefinition("valheim", "Valheim", "Dedicated server for Valheim.", "🎮", 896660, server_search_names=("valheim_server.exe",), has_world=True, supported=True, detection_capability="FULL", installation_capability="FULL", configuration_capability="FULL", mod_support_enabled=True, mod_adapter="valheim", mod_loader="BepInEx"),
    GameDefinition("minecraft-java", "Minecraft Java", "Java server instance.", "⛏", requires_java=True, server_search_names=("server.jar",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("terraria", "Terraria", "Terraria dedicated server.", "🧱", 105600, server_search_names=("TerrariaServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("project-zomboid", "Project Zomboid", "Project Zomboid dedicated server.", "🧟", server_search_names=("StartServer64.bat",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("palworld", "Palworld", "Palworld dedicated server.", "🌴", 2394010, server_search_names=("PalServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("7-days-to-die", "7 Days to Die", "7 Days to Die dedicated server.", "🧟", 294420, server_search_names=("7DaysToDieServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("ark-survival-evolved", "ARK: Survival Evolved", "ARK dedicated server.", "🦖", 376030, server_search_names=("ShooterGameServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("ark-survival-ascended", "ARK: Survival Ascended", "ARK: Survival Ascended dedicated server.", "🦖", 2430930, server_search_names=("ArkAscendedServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("rust", "Rust", "Rust dedicated server.", "🔧", 258550, server_search_names=("RustDedicated.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("factorio", "Factorio", "Factorio dedicated server.", "⚙", 427520, server_search_names=("factorio.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("satisfactory", "Satisfactory", "Satisfactory dedicated server.", "🏭", 1690800, server_search_names=("FactoryServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("v-rising", "V Rising", "V Rising dedicated server.", "🧛", 1604030, server_search_names=("VRisingServer.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("enshrouded", "Enshrouded", "Enshrouded dedicated server.", "🌫", 2278520, server_search_names=("enshrouded_server.exe",), supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("dont-starve-together", "Don't Starve Together", "Don't Starve Together server.", "🔥", 343050, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("counter-strike-2", "Counter-Strike 2", "Counter-Strike 2 dedicated server.", "🎯", 730, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("garrys-mod", "Garry's Mod", "Garry's Mod dedicated server.", "🧰", 4020, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("left-4-dead-2", "Left 4 Dead 2", "Left 4 Dead 2 dedicated server.", "🧟", 222860, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("unturned", "Unturned", "Unturned dedicated server.", "🧱", 1110390, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("starbound", "Starbound", "Starbound dedicated server.", "🚀", 211820, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("eco", "Eco", "Eco dedicated server.", "🌱", 382310, supported=True, detection_capability="FULL", installation_capability="MANUAL", configuration_capability="PARTIAL"),
    GameDefinition("generic", "Generic / Custom", "Any local server started by a BAT file.", "🖥", supported=True, detection_capability="FULL", installation_capability="FULL", configuration_capability="FULL"),
)

DEFINITIONS = {definition.id: definition for definition in CATALOG}


def game_definition(game_id: str) -> GameDefinition:
    return DEFINITIONS.get(game_id, DEFINITIONS["generic"])


def game_choices() -> list[GameDefinition]:
    return list(CATALOG)
