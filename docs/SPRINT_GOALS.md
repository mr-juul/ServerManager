# Server Manager Web Portal - Sprint Goals

This roadmap converts the agreed implementation phases into sprint goals with concrete deliverables.

## Sprint 1 - Service Layer Refactor
Goal:
- Stabilize backend architecture around shared services without breaking desktop behavior.

Deliverables:
- Keep GameDefinition as source for game metadata and web capabilities.
- Introduce UserManager and PermissionManager service layer in API backend.
- Keep JobManager behavior centralized (existing job flow retained and extended through jobs/tasks endpoints).
- Ensure desktop GUI and existing Valheim flows still function.

Definition of done:
- Existing web-control tests pass.
- New user/permission endpoints exist in v1 API.
- No direct game-specific logic added in website backend.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- `UserManager` and `PermissionManager` are defined in [core/access_control.py](../core/access_control.py).
	- v1 access checks and job/task routes live in [core/web_control.py](../core/web_control.py); no separate shared `JobManager` is evident there.

## Sprint 2 - Game Catalog API
Goal:
- Deliver complete game catalog and metadata contracts for the website.

Deliverables:
- GET /api/v1/games
- GET /api/v1/games/{id}
- GET /api/v1/games/{id}/create-schema
- Expose capabilities and setup states via metadata only.

Definition of done:
- Website can render game list without hardcoded game conditionals.

Status:
- Implementation status: Implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 exposes game catalog, game detail, and create-schema routes in [core/web_control.py](../core/web_control.py).
	- Website filters games by `create_server` capability and builds create fields from the schema in [website/js/app.js](../website/js/app.js).

## Sprint 3 - Setup + Server Creation Jobs
Goal:
- Make long-running operations async and trackable.

Deliverables:
- POST /api/v1/games/{id}/setup (job)
- POST /api/v1/servers (job)
- GET /api/v1/jobs and /api/v1/jobs/{id}
- GET /api/v1/tasks and /api/v1/tasks/{id}
- World discovery endpoints integrated in creation flow.

Definition of done:
- UI can poll jobs/tasks for progress and final state.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- Setup and server creation return job IDs; v1 provides jobs/tasks list and detail routes in [core/web_control.py](../core/web_control.py).
	- Website create/delete and backup flows wait for jobs in [website/js/app.js](../website/js/app.js).

## Sprint 4 - Games + Create Server UI
Goal:
- Build web-first create flow driven by GameDefinition schema.

Deliverables:
- Games page with setup state and actions.
- Dynamic create form from create-schema endpoint.
- Creation progress UI based on jobs/tasks.

Definition of done:
- User can create supported servers from website without desktop app.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- The create form requests each game's schema and world list, then submits fields selected from that schema in [website/js/app.js](../website/js/app.js).
	- The v1 API accepts server creation asynchronously in [core/web_control.py](../core/web_control.py).

## Sprint 5 - Server Detail + Settings + Delete
Goal:
- Complete operational management on server detail pages.

Deliverables:
- Overview, Logs, Settings tabs.
- Start/Stop/Restart UX with confirmation where needed.
- Delete endpoint and UI with safety backup behavior.

Definition of done:
- Daily operations possible entirely from website for supported games.

Status:
- Implementation status: Implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 provides server control, logs, settings, and delete routes; delete stops the server and creates a safety backup in [core/web_control.py](../core/web_control.py).
	- Website exposes server settings and delete confirmation flows in [website/js/app.js](../website/js/app.js).

## Sprint 6 - Mods + Profiles
Goal:
- Full mod operations from website with safe orchestration.

Deliverables:
- Global mod library and profile operations.
- Server-specific mod enable/disable/install/uninstall.
- Apply profile preview and apply flow.

Definition of done:
- Mod lifecycle manageable from web for supported games.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 exposes mod listing, per-server enable/disable/install/uninstall, and profile routes in [core/web_control.py](../core/web_control.py).
	- Website loads server mods and profiles and offers mod actions in [website/js/app.js](../website/js/app.js).

## Sprint 7 - Backups + Restore
Goal:
- Safe backup lifecycle exposed in web portal.

Deliverables:
- Backup list, create backup, restore backup.
- Enforced safety backup before restore.
- Restore progress and history visibility.

Definition of done:
- Restore flow reliable and reversible where practical.

Status:
- Implementation status: Implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 supports backup listing/creation and restore, with a safety backup and restore history in [core/web_control.py](../core/web_control.py).
	- Website displays restore history and polls restore jobs in [website/js/app.js](../website/js/app.js).

## Sprint 8 - Players
Goal:
- Expose player insight where game supports it.

Deliverables:
- Players endpoints and tab visibility by capability.
- Online/max indicators where available.

Definition of done:
- Unsupported games hide players UX automatically.

Status:
- Implementation status: Implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 exposes the players endpoint in [core/web_control.py](../core/web_control.py).
	- Website lets users select a server and displays supported state, online/max counts, and player names in [website/js/app.js](../website/js/app.js).

## Sprint 9 - Multi-User + Sharing
Goal:
- Introduce real user access model for friends and shared servers.

Deliverables:
- Users, roles, invitations, membership management.
- Server ownership and access scoping.
- Permission-based create/control/mod/backup operations.

Definition of done:
- Users only see and control authorized servers.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- v1 scopes server visibility and supports owner grant/revoke through access routes in [core/web_control.py](../core/web_control.py).
	- Session identity, immediate revocation, and access scoping have focused tests in [tests/test_session_identity.py](../tests/test_session_identity.py) and [tests/test_access_scoping.py](../tests/test_access_scoping.py).

## Sprint 10 - Security Hardening
Goal:
- Harden web portal for higher trust deployments.

Deliverables:
- Audit log for privileged actions.
- Rate limiting and tighter validation.
- Session security, CSRF hardening, auth boundary checks.
- Final API authorization sweep.

Definition of done:
- Security checklist completed for exposed control surface.

Status:
- Implementation status: Delvist implementeret
- Verification status: Ikke verificeret
- Evidence:
	- [core/web_control.py](../core/web_control.py) enforces CSRF for session writes, login throttling, and security response headers.
	- The roadmap's privileged-action audit log and final authorization sweep are not evidenced as complete by the inspected implementation.
