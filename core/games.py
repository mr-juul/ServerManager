from __future__ import annotations

from dataclasses import dataclass


WEB_CAPABILITY_KEYS: tuple[str, ...] = (
    "create_server",
    "edit_settings",
    "players",
    "mods",
    "mod_profiles",
    "backups",
    "logs",
    "world_selection",
    "world_import",
)


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
    web_create_supported: bool = False
    web_edit_supported: bool = False
    web_players_supported: bool = False
    web_world_import_supported: bool = False

    def web_capabilities(self) -> dict[str, bool]:
        return {
            "create_server": bool(self.web_create_supported),
            "edit_settings": bool(self.web_edit_supported),
            "players": bool(self.web_players_supported),
            "mods": bool(self.mod_support_enabled),
            "mod_profiles": bool(self.mod_support_enabled),
            "backups": True,
            "logs": True,
            "world_selection": bool(self.has_world),
            "world_import": bool(self.web_world_import_supported),
        }

    def web_editable_fields(self) -> list[str]:
        if not self.web_edit_supported:
            return []
        if self.id == "valheim":
            return ["name", "password", "public", "crossplay"]
        return ["name"]

    def web_editable_schema(self) -> list[dict[str, object]]:
        fields = self.web_editable_fields()
        if not fields:
            return []

        labels = {
            "name": "Server name",
            "password": "Password",
            "public": "Public",
            "crossplay": "Crossplay",
        }
        types = {
            "name": "text",
            "password": "password",
            "public": "checkbox",
            "crossplay": "checkbox",
        }
        placeholders = {
            "name": "Enter server name",
            "password": "Leave blank to keep current password",
        }
        help_text = {
            "name": "Shown in server browsers and management UI.",
            "password": "Only provide a value when you want to change it.",
            "public": "Allow players to discover this server publicly.",
            "crossplay": "Enable cross-platform matchmaking where supported.",
        }
        return [
            {
                "id": field,
                "label": labels.get(field, field.replace("_", " ").title()),
                "type": types.get(field, "text"),
                "placeholder": placeholders.get(field, ""),
                "help": help_text.get(field, ""),
            }
            for field in fields
        ]

    def web_create_schema(self) -> dict[str, object]:
        if not self.web_create_supported:
            return {
                "supported": False,
                "message": "Create this server in Server Manager.",
                "fields": [],
            }

        fields: list[dict[str, object]] = [
            {
                "id": "name",
                "type": "text",
                "label": "Server name",
                "required": True,
            },
            {
                "id": "password",
                "type": "password",
                "label": "Password",
                "required": False,
            },
        ]
        if self.has_world:
            fields.extend(
                [
                    {
                        "id": "world_mode",
                        "type": "select",
                        "label": "World",
                        "required": True,
                        "options": [
                            {"id": "new", "label": "Create new world"},
                            {"id": "existing", "label": "Use existing world"},
                        ],
                        "default": "new",
                    },
                    {
                        "id": "world",
                        "type": "world-selector",
                        "label": "World",
                        "required": False,
                    },
                ]
            )

        if self.id == "minecraft-java":
            fields.extend(
                [
                    {
                        "id": "minecraft_version",
                        "type": "select",
                        "label": "Version",
                        "required": False,
                        "options": [{"id": "latest", "label": "Latest stable"}],
                        "default": "latest",
                    },
                    {
                        "id": "minecraft_server_type",
                        "type": "select",
                        "label": "Server type",
                        "required": False,
                        "options": [
                            {"id": "vanilla", "label": "Vanilla"},
                            {"id": "fabric", "label": "Fabric"},
                            {"id": "forge", "label": "Forge"},
                            {"id": "neoforge", "label": "NeoForge"},
                        ],
                        "default": "vanilla",
                    },
                ]
            )

        return {
            "supported": True,
            "fields": fields,
            "editable_fields": self.web_editable_fields(),
        }


CATALOG: tuple[GameDefinition, ...] = (
    GameDefinition("valheim", "Valheim", "Dedicated server for Valheim.", "🎮", 896660, server_search_names=("valheim_server.exe",), has_world=True, supported=True, detection_capability="FULL", installation_capability="FULL", configuration_capability="FULL", mod_support_enabled=True, mod_adapter="valheim", mod_loader="BepInEx", web_create_supported=True, web_edit_supported=True, web_players_supported=True, web_world_import_supported=True),
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


def game_public_payload(definition: GameDefinition) -> dict[str, object]:
    return {
        "id": definition.id,
        "display_name": definition.display_name,
        "description": definition.description,
        "icon": definition.icon,
        "capabilities": definition.web_capabilities(),
    }
