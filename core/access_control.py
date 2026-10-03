from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException

_HASHER = PasswordHasher()


ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_MEMBER = "MEMBER"

PERMISSION_VIEW_SERVERS = "view_servers"
PERMISSION_CONTROL_SERVERS = "control_servers"
PERMISSION_CREATE_SERVERS = "create_servers"
PERMISSION_DELETE_SERVERS = "delete_servers"
PERMISSION_MANAGE_MODS = "manage_mods"
PERMISSION_MANAGE_BACKUPS = "manage_backups"
PERMISSION_VIEW_USERS = "view_users"
PERMISSION_MANAGE_USERS = "manage_users"


ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_OWNER: {
        PERMISSION_VIEW_SERVERS,
        PERMISSION_CONTROL_SERVERS,
        PERMISSION_CREATE_SERVERS,
        PERMISSION_DELETE_SERVERS,
        PERMISSION_MANAGE_MODS,
        PERMISSION_MANAGE_BACKUPS,
        PERMISSION_VIEW_USERS,
        PERMISSION_MANAGE_USERS,
    },
    ROLE_ADMIN: {
        PERMISSION_VIEW_SERVERS,
        PERMISSION_CONTROL_SERVERS,
        PERMISSION_CREATE_SERVERS,
        PERMISSION_DELETE_SERVERS,
        PERMISSION_MANAGE_MODS,
        PERMISSION_MANAGE_BACKUPS,
        PERMISSION_VIEW_USERS,
    },
    ROLE_MEMBER: {
        PERMISSION_VIEW_SERVERS,
        PERMISSION_CONTROL_SERVERS,
    },
}


@dataclass
class UserAccount:
    user_id: str
    display_name: str
    role: str
    permissions: set[str] = field(default_factory=set)
    active: bool = True
    password_hash: str = field(default="", repr=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "name": self.display_name,
            "role": self.role,
            "permissions": sorted(self.permissions),
            "active": self.active,
        }


class UserStoreError(RuntimeError):
    pass


class UserManager:
    """Simple in-memory user manager for web API authorization."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path
        self._persisted_user_ids: set[str] = set()
        self._users: dict[str, UserAccount] = {
            "owner-local": UserAccount(
                user_id="owner-local",
                display_name="Owner",
                role=ROLE_OWNER,
                permissions=set(ROLE_PERMISSIONS[ROLE_OWNER]),
            ),
            "admin-local": UserAccount(
                user_id="admin-local",
                display_name="Admin",
                role=ROLE_ADMIN,
                permissions=set(ROLE_PERMISSIONS[ROLE_ADMIN]),
            ),
            "member-local": UserAccount(
                user_id="member-local",
                display_name="Member",
                role=ROLE_MEMBER,
                permissions=set(ROLE_PERMISSIONS[ROLE_MEMBER]),
            ),
        }
        self._load_persisted_users()

    def _load_persisted_users(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UserStoreError("user_store_unavailable") from exc
        if not isinstance(data, dict) or not isinstance(data.get("users"), list):
            raise UserStoreError("user_store_invalid")

        for saved_user in data["users"]:
            if not isinstance(saved_user, dict):
                raise UserStoreError("user_store_invalid")
            user_id = saved_user.get("id")
            display_name = saved_user.get("name")
            role = saved_user.get("role")
            password_hash = saved_user.get("password_hash")
            active = saved_user.get("active", True)
            existing = self._users.get(user_id) if isinstance(user_id, str) else None
            if (
                not isinstance(user_id, str)
                or not user_id.strip()
                or len(user_id) > 64
                or any(char.isspace() for char in user_id)
                or not isinstance(display_name, str)
                or not display_name.strip()
                or len(display_name) > 100
                or not isinstance(role, str)
                or role not in {ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER}
                or (role == ROLE_OWNER and (existing is None or existing.role != ROLE_OWNER))
                or (existing is not None and existing.role != role)
                or not isinstance(password_hash, str)
                or not password_hash
                or not isinstance(active, bool)
            ):
                raise UserStoreError("user_store_invalid")
            try:
                _HASHER.check_needs_rehash(password_hash)
            except InvalidHashError as exc:
                raise UserStoreError("user_store_invalid") from exc
            self._users[user_id] = UserAccount(
                user_id=user_id,
                display_name=display_name,
                role=role,
                permissions=set(ROLE_PERMISSIONS[role]),
                active=active,
                password_hash=password_hash,
            )
            self._persisted_user_ids.add(user_id)

    def _save_persisted_users(self) -> None:
        if self._storage_path is None:
            return
        users = []
        for user_id in sorted(self._persisted_user_ids):
            user = self._users[user_id]
            users.append({
                "id": user.user_id,
                "name": user.display_name,
                "role": user.role,
                "active": user.active,
                "password_hash": user.password_hash,
            })
        temporary_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")
            temporary_path.replace(self._storage_path)
        except OSError as exc:
            raise UserStoreError("user_store_unavailable") from exc

    def resolve(self, user_id: str | None) -> UserAccount:
        if user_id:
            user = self._users.get(user_id)
            if user and user.active:
                return user
        # Preserve backward compatibility for existing session/api-key clients.
        return self._users["owner-local"]

    def set_password(self, user_id: str, password: str) -> None:
        user = self._users[user_id]
        user.password_hash = _HASHER.hash(password)

    def upsert_user(
        self,
        user_id: str,
        password: str,
        display_name: str | None = None,
        role: str | None = None,
    ) -> UserAccount:
        normalized_id = user_id.strip() if isinstance(user_id, str) else ""
        if not normalized_id or len(normalized_id) > 64 or any(char.isspace() for char in normalized_id):
            raise ValueError("invalid_user_id")
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("password_too_short")
        if len(password) > 1024:
            raise ValueError("password_too_long")

        normalized_role = role.strip().upper() if isinstance(role, str) else None
        if role is not None and normalized_role not in {ROLE_MEMBER, ROLE_ADMIN}:
            raise ValueError("invalid_role")

        normalized_name = None
        if display_name is not None:
            if not isinstance(display_name, str):
                raise ValueError("invalid_name")
            normalized_name = display_name.strip()
            if not normalized_name or len(normalized_name) > 100:
                raise ValueError("invalid_name")

        existing = self._users.get(normalized_id)
        if existing and existing.role == ROLE_OWNER and normalized_role is not None:
            raise ValueError("owner_role_protected")

        account_role = normalized_role or (existing.role if existing else ROLE_MEMBER)
        password_hash = _HASHER.hash(password)
        was_persisted = normalized_id in self._persisted_user_ids
        if existing:
            original = (existing.display_name, existing.role, existing.permissions, existing.active, existing.password_hash)
            existing.display_name = normalized_name or existing.display_name
            existing.role = account_role
            existing.permissions = set(ROLE_PERMISSIONS[account_role])
            existing.active = True
            existing.password_hash = password_hash
            user = existing
        else:
            user = UserAccount(
                user_id=normalized_id,
                display_name=normalized_name or normalized_id,
                role=account_role,
                permissions=set(ROLE_PERMISSIONS[account_role]),
                password_hash=password_hash,
            )
            self._users[normalized_id] = user
        self._persisted_user_ids.add(normalized_id)
        try:
            self._save_persisted_users()
        except UserStoreError:
            if existing:
                existing.display_name, existing.role, existing.permissions, existing.active, existing.password_hash = original
            else:
                self._users.pop(normalized_id, None)
            if not was_persisted:
                self._persisted_user_ids.discard(normalized_id)
            raise
        return user

    def verify_password(self, user_id: str, password: str) -> bool:
        user = self.get(user_id)
        if not user or not user.password_hash:
            return False
        try:
            return bool(_HASHER.verify(user.password_hash, password))
        except (VerifyMismatchError, InvalidHashError):
            return False

    def get(self, user_id: str) -> UserAccount | None:
        user = self._users.get(user_id)
        return user if user and user.active else None

    def list_users(self) -> list[UserAccount]:
        return [self._users[key] for key in sorted(self._users.keys())]

    def set_active(self, user_id: str, active: bool) -> UserAccount:
        user = self._users.get(user_id)
        if user is None:
            raise ValueError("user_not_found")
        if user.role == ROLE_OWNER and not active:
            raise ValueError("owner_user_protected")

        was_active = user.active
        user.active = bool(active)
        try:
            self._save_persisted_users()
        except UserStoreError:
            user.active = was_active
            raise
        return user

    def is_server_owner(self, user: UserAccount, config: Any) -> bool:
        if user.role == ROLE_OWNER:
            return True
        return str(getattr(config, "owner_id", "") or "owner-local") == user.user_id

    def can_access_server(self, user: UserAccount, config: Any) -> bool:
        if self.is_server_owner(user, config):
            return True
        return user.user_id in (getattr(config, "shared_with", None) or [])


class PermissionManager:
    def require(self, user: UserAccount, permission: str) -> None:
        if permission in user.permissions:
            return
        raise HTTPException(status_code=403, detail="forbidden")
