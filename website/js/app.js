let serversTimer = null;
let logTimer = null;
let logServerId = "";
let logCursor = 0;
let createSchema = null;
let createGameId = "";
let createGames = [];
let currentView = "servers";
let selectedModsServerId = "";
let selectedBackupsServerId = "";
let selectedSettingsServerId = "";
let selectedPlayersServerId = "";
let currentUser = null;
let jobsAutoRefreshTimer = null;
let playersAutoRefreshTimer = null;
let modsSearchText = "";
let modsStatusFilter = "all";
let backupsFilterText = "";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function formatDateTime(epochSeconds) {
  const value = Number(epochSeconds || 0);
  if (!value) {
    return "-";
  }
  const date = new Date(value * 1000);
  return date.toLocaleString();
}

function formatDuration(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  if (!total) {
    return "-";
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = Math.floor(total % 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${remaining}s`;
  }
  return `${remaining}s`;
}

function formatLineMetric(value) {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) {
    return "-";
  }
  return `#${value + 1}`;
}

async function waitForJob(jobId, startedMessage, failedMessage) {
  if (startedMessage) {
    showMessage(startedMessage);
  }
  for (let i = 0; i < 120; i += 1) {
    const jobPayload = await call(`/api/jobs/${jobId}`);
    const job = jobPayload.job || {};
    const status = String(job.status || "");
    if (status === "COMPLETED") {
      return job;
    }
    if (status === "FAILED") {
      throw new Error(String(job.error || failedMessage || "Operation failed."));
    }
    if (Array.isArray(job.progress) && job.progress.length) {
      showMessage(`${startedMessage || "Working..."} ${job.progress[job.progress.length - 1]}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Operation is taking too long. Check job status.");
}

function showMessage(text, isError = false) {
  const box = document.getElementById("message");
  const raw = String(text || "");
  if (isError) {
    const normalized = raw.toLowerCase();
    if (normalized === "setup_required") {
      box.textContent = "This game still needs setup. Open Games and run setup, then try again.";
    } else if (normalized === "create_not_supported") {
      box.textContent = "This game cannot be created from web yet.";
    } else {
      box.textContent = raw;
    }
  } else {
    box.textContent = raw;
  }
  box.classList.remove("hidden");
  box.style.borderColor = isError ? "#8a3232" : "#2f3f52";
}

function hideMessage() {
  document.getElementById("message").classList.add("hidden");
}

async function call(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    throw new Error(data.message || data.error || `HTTP ${response.status}`);
  }
  return data;
}

function statusClass(status) {
  const value = String(status || "unknown").toLowerCase();
  return value;
}

function statusText(status) {
  const map = {
    running: "ONLINE",
    stopped: "OFFLINE",
    starting: "STARTING",
    stopping: "STOPPING",
    crashed: "ERROR",
    unknown: "ERROR",
  };
  return map[String(status || "unknown").toLowerCase()] || String(status || "UNKNOWN").toUpperCase();
}

function gameStatusText(status) {
  const normalized = String(status || "UNKNOWN").toUpperCase();
  const labels = {
    READY: "Ready",
    SETUP_REQUIRED: "Setup required",
    INSTALLING: "Installing",
    ERROR: "Setup error",
    UNSUPPORTED: "Unsupported",
  };
  return labels[normalized] || normalized;
}

function gameStatusClass(status) {
  const normalized = String(status || "").toUpperCase();
  if (normalized === "READY") {
    return "running";
  }
  if (normalized === "INSTALLING") {
    return "starting";
  }
  if (normalized === "SETUP_REQUIRED") {
    return "stopping";
  }
  return "error";
}

async function loadServers() {
  const payload = await call("/api/servers");
  const servers = payload.servers || [];
  const host = document.getElementById("servers");
  const playersMap = {};
  await Promise.all(servers.map(async (item) => {
    try {
      const playersPayload = await call(`/api/servers/${item.id}/players`);
      playersMap[item.id] = playersPayload;
    } catch (_) {
      playersMap[item.id] = { supported: false, players: [], online: null, max: null };
    }
  }));

  host.innerHTML = servers.map((item) => {
    const status = String(item.status || "unknown").toLowerCase();
    const playersInfo = playersMap[item.id] || { supported: false };
    const playersText = playersInfo.supported
      ? `${playersInfo.online ?? 0}${playersInfo.max ? ` / ${playersInfo.max}` : ""} players`
      : "Players unavailable";
    const playersList = playersInfo.supported && Array.isArray(playersInfo.players) && playersInfo.players.length
      ? `<div class="muted">${playersInfo.players.slice(0, 5).join(", ")}</div>`
      : "";
    return `
      <article class="server">
        <div><strong>${item.name}</strong></div>
        <div class="muted">${String(item.game || "").toUpperCase()}</div>
        <div class="status ${statusClass(status)}">${statusText(status)}</div>
        <div class="muted">${playersText}</div>
        ${playersList}
        <div class="row">
          ${status === "stopped" ? `<button data-action="start" data-id="${item.id}">START</button>` : ""}
          ${status === "running" || status === "starting" ? `<button data-action="stop" data-id="${item.id}">STOP</button>` : ""}
          ${status === "running" || status === "starting" || status === "stopping" ? `<button data-action="restart" data-id="${item.id}">RESTART</button>` : ""}
          <button data-action="logs" data-id="${item.id}">LOGS</button>
        </div>
        <div class="row tight">
          <button data-action="details" data-id="${item.id}">DETAILS</button>
          <button data-action="go-players" data-id="${item.id}">PLAYERS</button>
          <button data-action="go-mods" data-id="${item.id}">MODS</button>
          <button data-action="go-backups" data-id="${item.id}">BACKUPS</button>
          <button data-action="settings" data-id="${item.id}">SETTINGS</button>
          ${["OWNER", "ADMIN"].includes(String(currentUser?.role || "").toUpperCase()) ? `<button data-action="access" data-id="${item.id}">ACCESS</button>` : ""}
          <button data-action="delete" data-id="${item.id}" data-name="${String(item.name || "").replace(/"/g, "&quot;")}">DELETE</button>
        </div>
      </article>
    `;
  }).join("");

  host.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      const serverId = button.dataset.id;
      try {
        hideMessage();
        if (action === "logs") {
          await openLogs(serverId);
          return;
        }
        if (action === "go-mods") {
          selectedModsServerId = serverId;
          setView("mods");
          await refreshCurrentView();
          return;
        }
        if (action === "go-players") {
          selectedPlayersServerId = serverId;
          setView("players");
          await refreshCurrentView();
          return;
        }
        if (action === "go-backups") {
          selectedBackupsServerId = serverId;
          setView("backups");
          await refreshCurrentView();
          return;
        }
        if (action === "settings") {
          await openSettings(serverId);
          return;
        }
        if (action === "details") {
          await openServerDetails(serverId);
          return;
        }
        if (action === "access") {
          await openAccess(serverId);
          return;
        }
        if (action === "delete") {
          const name = button.dataset.name || serverId;
          const typed = window.prompt(`Delete "${name}"? A safety backup is created first and the server is stopped if running. Type the server name to confirm:`);
          if (typed === null) {
            return;
          }
          if (typed.trim() !== name) {
            showMessage("Name did not match. Server was not deleted.", true);
            return;
          }
          const started = await call(`/api/servers/${serverId}`, { method: "DELETE", body: JSON.stringify({ confirm: serverId }) });
          await waitForJob(started.job_id, "Deleting server...", "Delete failed. The server was not removed.");
          showMessage("Server deleted. A safety backup was created.");
          await loadServers();
          return;
        }
        await call(`/api/servers/${serverId}/${action}`, { method: "POST", body: "{}" });
        await loadServers();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function openServerDetails(serverId) {
  const payload = await call(`/api/servers/${encodeURIComponent(serverId)}`);
  const server = payload.server || payload;
  const host = document.getElementById("detailsHost");
  document.getElementById("detailsTitle").textContent = `Server Details - ${serverId}`;
  host.innerHTML = `
    <div class="detail-grid">
      <div class="detail-tile">
        <div class="detail-label">Name</div>
        <div class="detail-value">${escapeHtml(server.name || "-")}</div>
      </div>
      <div class="detail-tile">
        <div class="detail-label">Game</div>
        <div class="detail-value">${escapeHtml(String(server.game || "").toUpperCase())}</div>
      </div>
      <div class="detail-tile">
        <div class="detail-label">Status</div>
        <div class="detail-value">${escapeHtml(statusText(server.status || server.state || "unknown"))}</div>
      </div>
      <div class="detail-tile">
        <div class="detail-label">Process ID</div>
        <div class="detail-value">${escapeHtml(server.pid || "-")}</div>
      </div>
      <div class="detail-tile">
        <div class="detail-label">Uptime</div>
        <div class="detail-value">${escapeHtml(formatDuration(server.uptime_seconds))}</div>
      </div>
      <div class="detail-tile">
        <div class="detail-label">Owner</div>
        <div class="detail-value">${escapeHtml(server.owner_id || "-")}</div>
      </div>
    </div>
    <div class="row" style="margin-top: 12px;">
      <button data-detail-action="logs" data-id="${escapeHtml(serverId)}">Open logs</button>
      <button data-detail-action="settings" data-id="${escapeHtml(serverId)}">Open settings</button>
    </div>
  `;
  host.querySelectorAll("button[data-detail-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = String(button.dataset.detailAction || "");
      const id = String(button.dataset.id || "");
      if (!id) {
        return;
      }
      if (action === "logs") {
        await openLogs(id);
      } else if (action === "settings") {
        await openSettings(id);
      }
    });
  });
  document.getElementById("detailsCard").classList.remove("hidden");
}

async function loadJobs() {
  const payload = await call("/api/jobs");
  const jobs = payload.jobs || [];
  const host = document.getElementById("jobs");
  if (!jobs.length) {
    host.innerHTML = '<div class="muted">No jobs yet.</div>';
    return;
  }

  host.innerHTML = jobs.map((job) => {
    const jobId = String(job.job_id || "");
    const status = String(job.status || "unknown").toLowerCase();
    const operation = String(job.operation || "operation");
    const updated = formatDateTime(job.updated_at);
    const progress = Array.isArray(job.progress) && job.progress.length
      ? escapeHtml(job.progress[job.progress.length - 1])
      : "No progress details yet.";
    return `
      <div class="job-row">
        <div><strong>${escapeHtml(operation)}</strong></div>
        <div class="status ${statusClass(status)}">${escapeHtml(String(job.status || "UNKNOWN"))}</div>
        <div class="job-meta">Job ID: ${escapeHtml(jobId)} | Updated: ${escapeHtml(updated)}</div>
        <div class="job-progress">${progress}</div>
      </div>
    `;
  }).join("");
}

async function loadUsersCenter() {
  const host = document.getElementById("usersCenter");
  const me = currentUser || {};
  const permissions = Array.isArray(me.permissions) ? me.permissions : [];
  const canViewUsers = permissions.includes("view_users");
  const canManageUsers = permissions.includes("manage_users");

  if (!canViewUsers) {
    host.innerHTML = '<div class="muted">You do not have permission to view users.</div>';
    return;
  }

  const [matrixPayload, auditPayload] = await Promise.all([
    call("/api/access-matrix"),
    call("/api/access-audit?limit=20").catch(() => ({ events: [] })),
  ]);
  const users = Array.isArray(matrixPayload.users) ? matrixPayload.users : [];
  const servers = Array.isArray(matrixPayload.servers) ? matrixPayload.servers : [];
  const events = Array.isArray(auditPayload.events) ? auditPayload.events : [];

  const rows = servers.map((server) => {
    const shared = Array.isArray(server.shared_with) ? server.shared_with : [];
    const ownerId = String(server.owner_id || "");
    const canManageAccess = Boolean(server.can_manage_access);
    const options = users
      .filter((item) => Boolean(item.active) && String(item.id || "") !== ownerId && !shared.includes(String(item.id || "")))
      .map((item) => `<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.name || item.id || "")}</option>`)
      .join("");
    return `
      <article class="server">
        <div><strong>${escapeHtml(server.name || server.id || "Server")}</strong></div>
        <div class="muted">Owner: ${escapeHtml(ownerId || "-")}</div>
        <div class="muted">Shared: ${shared.length}</div>
        <div class="chip-list">
          ${shared.length ? shared.map((userId) => {
            const u = users.find((x) => String(x.id || "") === String(userId));
            const label = u ? `${u.name || u.id} (${u.id})` : userId;
            return `<span class="chip">${escapeHtml(label)}</span>`;
          }).join("") : '<span class="muted">No shared users</span>'}
        </div>
        ${canManageAccess ? `
          <div class="row" style="margin-top: 10px;">
            <select data-grant-select="${escapeHtml(server.id || "")}">
              <option value="">Select user</option>
              ${options}
            </select>
            <button class="small" data-users-action="grant" data-server-id="${escapeHtml(server.id || "")}">Grant</button>
          </div>
          <div class="row" style="margin-top: 8px;">
            ${shared.map((userId) => `<button class="small" data-users-action="revoke" data-server-id="${escapeHtml(server.id || "")}" data-user-id="${escapeHtml(userId)}">Revoke ${escapeHtml(userId)}</button>`).join("") || ""}
          </div>
        ` : '<div class="muted" style="margin-top: 8px;">Read-only for this server.</div>'}
      </article>
    `;
  }).join("");

  host.innerHTML = `
    ${canManageUsers ? `
      <div class="invite-row">
        <h3>Create or reset user</h3>
        <div class="row">
          <label class="field" style="flex:1 1 220px;"><span>User ID</span><input id="usersNewId" type="text" maxlength="64" /></label>
          <label class="field" style="flex:1 1 220px;"><span>Name</span><input id="usersNewName" type="text" maxlength="100" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1 1 220px;"><span>Temporary password</span><input id="usersNewPassword" type="password" minlength="8" /></label>
          <label class="field" style="flex:1 1 220px;"><span>Role</span>
            <select id="usersNewRole">
              <option value="MEMBER">Member</option>
              <option value="ADMIN">Admin</option>
            </select>
          </label>
        </div>
        <button id="usersCreateBtn" class="small">Create / Reset</button>
      </div>
    ` : ""}

    <div class="invite-row">
      <h3>Users</h3>
      ${users.map((user) => `
        <div class="access-user">
          <span>
            ${escapeHtml(user.name || user.id || "-")}
            <span class="muted">(${escapeHtml(user.id || "-")}) ${escapeHtml(String(user.role || "MEMBER").toUpperCase())} ${user.active === false ? "- INACTIVE" : "- ACTIVE"}</span>
          </span>
          <span>
            ${canManageUsers && user.id !== me.id && user.role !== "OWNER" ? `<button class="small" data-users-action="${user.active === false ? "activate" : "deactivate"}" data-user-id="${escapeHtml(user.id || "")}">${user.active === false ? "Activate" : "Deactivate"}</button>` : ""}
            ${canManageUsers && user.role !== "OWNER" ? `<button class="small" data-users-action="reset-password" data-user-id="${escapeHtml(user.id || "")}" data-user-name="${escapeHtml(user.name || user.id || "")}">Reset password</button>` : ""}
          </span>
        </div>
      `).join("")}
    </div>

    <div class="invite-row">
      <h3>Server access matrix</h3>
      ${rows || '<div class="muted">No servers visible for access management.</div>'}
    </div>

    <div class="invite-row">
      <h3>Access audit</h3>
      ${(events.length ? events.map((event) => `
        <div class="event-row">
          <div><strong>${escapeHtml(String(event.action || "event"))}</strong> by ${escapeHtml(event.actor || "-")}</div>
          <div class="muted">${formatDateTime(event.at)} | ${escapeHtml(JSON.stringify(event.payload || {}))}</div>
        </div>
      `).join("") : '<div class="muted">No audit events yet.</div>')}
    </div>
  `;

  const createButton = document.getElementById("usersCreateBtn");
  if (createButton) {
    createButton.addEventListener("click", async () => {
      const userId = String(document.getElementById("usersNewId").value || "").trim();
      const name = String(document.getElementById("usersNewName").value || "").trim();
      const password = String(document.getElementById("usersNewPassword").value || "");
      const role = String(document.getElementById("usersNewRole").value || "MEMBER");
      if (!userId || !password) {
        showMessage("Enter user ID and temporary password.", true);
        return;
      }
      try {
        await call("/api/users", { method: "POST", body: JSON.stringify({ user_id: userId, name, password, role }) });
        showMessage("User created/reset successfully.");
        await loadUsersCenter();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  }

  host.querySelectorAll("button[data-users-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = String(button.dataset.usersAction || "");
      const userId = String(button.dataset.userId || "");
      const serverId = String(button.dataset.serverId || "");
      try {
        if (action === "activate" && userId) {
          await call(`/api/users/${encodeURIComponent(userId)}/activate`, { method: "POST", body: "{}" });
          showMessage("User activated.");
          await loadUsersCenter();
          return;
        }
        if (action === "deactivate" && userId) {
          await call(`/api/users/${encodeURIComponent(userId)}/deactivate`, { method: "POST", body: "{}" });
          showMessage("User deactivated.");
          await loadUsersCenter();
          return;
        }
        if (action === "reset-password" && userId) {
          const nextPassword = window.prompt(`Enter a temporary password for ${button.dataset.userName || userId}:`);
          if (nextPassword === null) {
            return;
          }
          if (String(nextPassword).length < 8) {
            showMessage("Password must be at least 8 characters.", true);
            return;
          }
          await call("/api/users", {
            method: "POST",
            body: JSON.stringify({ user_id: userId, password: String(nextPassword) }),
          });
          showMessage("Password reset completed.");
          await loadUsersCenter();
          return;
        }
        if (action === "grant" && serverId) {
          const select = host.querySelector(`select[data-grant-select="${serverId.replace(/"/g, "&quot;")}"]`);
          const targetUserId = String((select && select.value) || "").trim();
          if (!targetUserId) {
            showMessage("Select a user to grant.", true);
            return;
          }
          await call(`/api/servers/${encodeURIComponent(serverId)}/access`, {
            method: "POST",
            body: JSON.stringify({ user_id: targetUserId }),
          });
          showMessage("Access granted.");
          await loadUsersCenter();
          return;
        }
        if (action === "revoke" && serverId && userId) {
          await call(`/api/servers/${encodeURIComponent(serverId)}/access/${encodeURIComponent(userId)}`, { method: "DELETE" });
          showMessage("Access revoked.");
          await loadUsersCenter();
        }
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function loadInvitations() {
  const host = document.getElementById("invitations");
  const canManageUsers = Array.isArray(currentUser?.permissions)
    && currentUser.permissions.includes("manage_users");
  if (!canManageUsers) {
    host.innerHTML = '<div class="muted">You do not have permission to manage invitations.</div>';
    return;
  }

  const payload = await call("/api/invitations");
  const invitations = payload.invitations || [];
  host.innerHTML = `
    <div class="invite-row">
      <h3>Create invitation</h3>
      <label class="field"><span>Target</span><input id="inviteTarget" type="text" placeholder="email or username"></label>
      <label class="field"><span>Role</span>
        <select id="inviteRole">
          <option value="MEMBER">Member</option>
          <option value="ADMIN">Admin</option>
        </select>
      </label>
      <button id="createInvitationBtn" class="small">Create invitation</button>
    </div>
    <div class="invite-row">
      <h3>Pending invitations</h3>
      ${(invitations.length ? invitations.map((invitation) => `
        <div class="access-user">
          <span>
            ${escapeHtml(invitation.target || "-")}
            <span class="muted">(${escapeHtml(invitation.role || "MEMBER")}, ${escapeHtml(invitation.status || "pending")})</span>
          </span>
          <button class="small" data-delete-invitation="${escapeHtml(invitation.id || "")}">Delete</button>
        </div>
      `).join("") : '<div class="muted">No pending invitations.</div>')}
    </div>
  `;

  const createButton = document.getElementById("createInvitationBtn");
  if (createButton) {
    createButton.addEventListener("click", async () => {
      const target = String(document.getElementById("inviteTarget").value || "").trim();
      const role = String(document.getElementById("inviteRole").value || "MEMBER");
      if (!target) {
        showMessage("Enter a target for the invitation.", true);
        return;
      }
      try {
        await call("/api/invitations", { method: "POST", body: JSON.stringify({ target, role }) });
        showMessage("Invitation created.");
        await loadInvitations();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  }

  host.querySelectorAll("button[data-delete-invitation]").forEach((button) => {
    button.addEventListener("click", async () => {
      const invitationId = String(button.dataset.deleteInvitation || "");
      if (!invitationId) {
        return;
      }
      try {
        await call(`/api/invitations/${encodeURIComponent(invitationId)}`, { method: "DELETE" });
        showMessage("Invitation deleted.");
        await loadInvitations();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function openAccess(serverId) {
  const accessCard = document.getElementById("accessCard");
  const accessHost = document.getElementById("accessHost");
  document.getElementById("accessTitle").textContent = `Server Access - ${serverId}`;
  accessHost.textContent = "Loading access...";
  accessCard.classList.remove("hidden");

  const [access, usersPayload] = await Promise.all([
    call(`/api/servers/${encodeURIComponent(serverId)}/access`),
    call("/api/users"),
  ]);
  const users = usersPayload.users || [];
  const ownerId = String(access.owner_id || "");
  const sharedUsers = Array.isArray(access.shared_with) ? access.shared_with.map(String) : [];
  const owner = users.find((user) => String(user.id) === ownerId);
  const ownerName = owner ? String(owner.name || owner.id) : ownerId;
  const availableUsers = users.filter((user) => user.active !== false
    && String(user.id) !== ownerId
    && !sharedUsers.includes(String(user.id)));
  const canManageUsers = Array.isArray(currentUser?.permissions)
    && currentUser.permissions.includes("manage_users");

  accessHost.innerHTML = `
    <p class="muted">Owner: <strong>${escapeHtml(ownerName)}</strong> (${escapeHtml(ownerId)})</p>
    <h3>Shared with</h3>
    <div id="sharedUsers">
      ${sharedUsers.map((userId) => {
        const user = users.find((item) => String(item.id) === userId);
        const userName = user ? String(user.name || user.id) : userId;
        return `<div class="access-user"><span>${escapeHtml(userName)} <span class="muted">(${escapeHtml(userId)})</span></span><button class="small" data-revoke-user="${escapeHtml(userId)}">Remove</button></div>`;
      }).join("") || '<div class="muted">No users have shared access.</div>'}
    </div>
    <h3>Grant access</h3>
    ${availableUsers.length ? `
      <div class="row access-grant">
        <select id="accessUserSelect" aria-label="User to grant access">
          ${availableUsers.map((user) => `<option value="${escapeHtml(user.id)}">${escapeHtml(user.name || user.id)} (${escapeHtml(user.id)})</option>`).join("")}
        </select>
        <button id="grantAccessBtn" class="small">Grant access</button>
      </div>
    ` : '<div class="muted">There are no other users available to add.</div>'}
    ${canManageUsers ? `
      <h3>Invite a new user</h3>
      <div class="access-invite">
        <label class="field"><span>User ID</span><input id="inviteUserId" type="text" maxlength="64" autocomplete="username" required></label>
        <label class="field"><span>Name (optional)</span><input id="inviteUserName" type="text" maxlength="100" autocomplete="name"></label>
        <label class="field"><span>Temporary password</span><input id="inviteUserPassword" type="password" minlength="8" autocomplete="new-password" required></label>
        <button id="createAndGrantBtn" class="small">Create account and grant access</button>
        <p class="muted">Share the user ID and password with them privately.</p>
      </div>
    ` : ""}
  `;

  const grantButton = document.getElementById("grantAccessBtn");
  if (grantButton) {
    grantButton.addEventListener("click", async () => {
      const userId = String(document.getElementById("accessUserSelect").value || "");
      try {
        await call(`/api/servers/${encodeURIComponent(serverId)}/access`, {
          method: "POST",
          body: JSON.stringify({ user_id: userId }),
        });
        await openAccess(serverId);
        showMessage("Access granted.");
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  }

  const createAndGrantButton = document.getElementById("createAndGrantBtn");
  if (createAndGrantButton) {
    createAndGrantButton.addEventListener("click", async () => {
      const userId = String(document.getElementById("inviteUserId").value || "").trim();
      const name = String(document.getElementById("inviteUserName").value || "").trim();
      const password = String(document.getElementById("inviteUserPassword").value || "");
      if (!userId || !password) {
        showMessage("Enter a user ID and temporary password.", true);
        return;
      }
      let accountCreated = false;
      try {
        await call("/api/users", {
          method: "POST",
          body: JSON.stringify({ user_id: userId, name, password }),
        });
        accountCreated = true;
        await call(`/api/servers/${encodeURIComponent(serverId)}/access`, {
          method: "POST",
          body: JSON.stringify({ user_id: userId }),
        });
        await openAccess(serverId);
        showMessage("Account created and access granted. Share the credentials privately.");
      } catch (err) {
        const message = String(err.message || err);
        showMessage(accountCreated ? `Account created, but access could not be granted: ${message}` : message, true);
      }
    });
  }

  accessHost.querySelectorAll("button[data-revoke-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      const userId = String(button.dataset.revokeUser || "");
      try {
        await call(`/api/servers/${encodeURIComponent(serverId)}/access/${encodeURIComponent(userId)}`, { method: "DELETE" });
        await openAccess(serverId);
        showMessage("Access removed.");
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function loadMods() {
  const serversPayload = await call("/api/servers");
  const servers = serversPayload.servers || [];
  const host = document.getElementById("mods");

  if (!servers.length) {
    host.innerHTML = `<div class="muted">No servers available for mod management.</div>`;
    return;
  }

  const selectedServerId = selectedModsServerId || host.getAttribute("data-selected-server") || String(servers[0].id);
  selectedModsServerId = selectedServerId;
  host.setAttribute("data-selected-server", selectedServerId);

  const selectedServer = servers.find((item) => String(item.id) === String(selectedServerId)) || servers[0];
  selectedModsServerId = String(selectedServer.id);
  const [serverModsPayload, profilesPayload, catalogPayload] = await Promise.all([
    call(`/api/servers/${selectedServer.id}/mods`),
    call(`/api/mod-profiles?game=${encodeURIComponent(String(selectedServer.game || ""))}`),
    call(`/api/mods?game=${encodeURIComponent(String(selectedServer.game || ""))}`),
  ]);
  const serverMods = Array.isArray(serverModsPayload.mods) ? serverModsPayload.mods : [];
  const profiles = Array.isArray(profilesPayload.profiles) ? profilesPayload.profiles : [];
  const catalogMods = Array.isArray(catalogPayload.mods) ? catalogPayload.mods : [];

  const byId = new Map();
  catalogMods.forEach((item) => {
    byId.set(String(item.id || ""), {
      id: String(item.id || ""),
      name: String(item.name || item.id || "Unknown mod"),
      game: String(item.game || selectedServer.game || ""),
      version: String(item.version || "unknown"),
      status: String(item.status || "available"),
      enabled: false,
      installed: false,
    });
  });
  serverMods.forEach((item) => {
    const modId = String(item.id || "");
    if (!modId) {
      return;
    }
    const existing = byId.get(modId) || {
      id: modId,
      name: String(item.name || modId),
      game: String(item.game || selectedServer.game || ""),
      version: String(item.version || "unknown"),
      status: String(item.status || "available"),
      enabled: false,
      installed: false,
    };
    existing.name = String(item.name || existing.name || modId);
    existing.version = String(item.version || existing.version || "unknown");
    existing.status = String(item.status || existing.status || "available");
    existing.enabled = Boolean(item.enabled);
    existing.installed = Boolean(item.installed || item.enabled || String(item.status || "").toUpperCase() === "INSTALLED");
    byId.set(modId, existing);
  });

  const searchText = String(modsSearchText || "").trim().toLowerCase();
  const statusFilter = String(modsStatusFilter || "all").toLowerCase();
  const mergedMods = Array.from(byId.values()).sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const filteredMods = mergedMods.filter((item) => {
    const text = `${item.name} ${item.id}`.toLowerCase();
    if (searchText && !text.includes(searchText)) {
      return false;
    }
    if (statusFilter === "enabled") {
      return Boolean(item.enabled);
    }
    if (statusFilter === "installed") {
      return Boolean(item.installed);
    }
    if (statusFilter === "available") {
      return !item.installed;
    }
    return true;
  });

  const profileOptions = profiles.map((profile) => `<option value="${String(profile.name || "")}">${String(profile.name || "")}</option>`).join("");

  host.innerHTML = `
    <label class="field">
      <span>Server</span>
      <select id="modsServerSelect">
        ${servers.map((server) => `<option value="${server.id}"${server.id === selectedServer.id ? " selected" : ""}>${server.name}</option>`).join("")}
      </select>
    </label>
    <div class="row">
      <label class="field" style="flex: 2 1 220px; margin-top: 0;">
        <span>Search mods</span>
        <input id="modSearchInput" type="text" placeholder="Search by name or id" value="${escapeHtml(modsSearchText)}" />
      </label>
      <label class="field" style="flex: 1 1 160px; margin-top: 0;">
        <span>Filter</span>
        <select id="modStatusFilter">
          <option value="all"${modsStatusFilter === "all" ? " selected" : ""}>All</option>
          <option value="enabled"${modsStatusFilter === "enabled" ? " selected" : ""}>Enabled</option>
          <option value="installed"${modsStatusFilter === "installed" ? " selected" : ""}>Installed</option>
          <option value="available"${modsStatusFilter === "available" ? " selected" : ""}>Available</option>
        </select>
      </label>
    </div>
    <div class="row">
      <select id="modProfileSelect">
        <option value="">Select profile</option>
        ${profileOptions}
      </select>
      <button id="applyProfileBtn" class="small">Apply profile</button>
    </div>
    <div class="muted">Showing ${filteredMods.length} of ${mergedMods.length} mods for ${escapeHtml(String(selectedServer.game || "").toUpperCase())}.</div>
    ${filteredMods.map((mod) => `
      <article class="server">
        <div><strong>${escapeHtml(mod.name || mod.id)}</strong></div>
        <div class="muted">${escapeHtml(String(mod.game || "").toUpperCase())}</div>
        <div class="muted">Version: ${escapeHtml(mod.version || "unknown")}</div>
        <div class="status ${mod.enabled ? "running" : "stopped"}">${mod.enabled ? "ENABLED" : "DISABLED"}</div>
        <div class="muted">Status: ${escapeHtml(String(mod.status || "available").toUpperCase())} | Installed: ${mod.installed ? "Yes" : "No"}</div>
        <div class="row">
          <button data-mod-action="install" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}"${mod.installed ? " disabled" : ""}>Install</button>
          <button data-mod-action="enable" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}"${!mod.installed || mod.enabled ? " disabled" : ""}>Enable</button>
          <button data-mod-action="disable" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}"${!mod.enabled ? " disabled" : ""}>Disable</button>
          <button data-mod-action="uninstall" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}"${!mod.installed ? " disabled" : ""}>Uninstall</button>
        </div>
      </article>
    `).join("") || '<div class="muted">No mods match this filter.</div>'}
  `;

  document.getElementById("modsServerSelect").addEventListener("change", async (event) => {
    selectedModsServerId = String(event.target.value || "");
    host.setAttribute("data-selected-server", selectedModsServerId);
    await loadMods();
  });

  const searchInput = document.getElementById("modSearchInput");
  if (searchInput) {
    searchInput.addEventListener("input", async (event) => {
      modsSearchText = String(event.target.value || "");
      await loadMods();
    });
  }

  const statusFilterInput = document.getElementById("modStatusFilter");
  if (statusFilterInput) {
    statusFilterInput.addEventListener("change", async (event) => {
      modsStatusFilter = String(event.target.value || "all");
      await loadMods();
    });
  }

  const applyButton = document.getElementById("applyProfileBtn");
  if (applyButton) {
    applyButton.addEventListener("click", async () => {
      const profile = String((document.getElementById("modProfileSelect") || {}).value || "").trim();
      if (!profile) {
        showMessage("Select a profile first.", true);
        return;
      }
      try {
        hideMessage();
        await call(`/api/servers/${selectedServer.id}/mod-profile`, { method: "POST", body: JSON.stringify({ profile }) });
        showMessage("Profile applied.");
        await loadMods();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  }

  host.querySelectorAll("button[data-mod-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = String(button.dataset.modAction || "");
      const serverId = String(button.dataset.serverId || "");
      const modId = String(button.dataset.modId || "");
      if (!action || !serverId || !modId) {
        return;
      }
      try {
        hideMessage();
        await call(`/api/servers/${serverId}/mods/${modId}/${action}`, { method: "POST", body: "{}" });
        await loadMods();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function loadPlayers() {
  const serversPayload = await call("/api/servers");
  const servers = serversPayload.servers || [];
  const host = document.getElementById("players");
  if (!servers.length) {
    host.innerHTML = `<div class="muted">No servers found.</div>`;
    return;
  }

  const selectedServerId = selectedPlayersServerId || String(servers[0].id);
  const selectedServer = servers.find((item) => String(item.id) === String(selectedServerId)) || servers[0];
  selectedPlayersServerId = String(selectedServer.id);
  const playersPayload = await call(`/api/servers/${selectedServer.id}/players`);
  const players = playersPayload.players || [];
  const supported = Boolean(playersPayload.supported);
  const online = playersPayload.online ?? 0;
  const max = playersPayload.max;
  const sessions = Array.isArray(playersPayload.sessions) ? playersPayload.sessions : [];
  const recentEvents = Array.isArray(playersPayload.recent_events) ? playersPayload.recent_events.slice(-12).reverse() : [];
  const eventCount = Number(playersPayload.event_count || 0);

  host.innerHTML = `
    <label class="field">
      <span>Server</span>
      <select id="playersServerSelect">
        ${servers.map((server) => `<option value="${server.id}"${server.id === selectedServer.id ? " selected" : ""}>${server.name}</option>`).join("")}
      </select>
    </label>
    <div class="server">
      <div><strong>${selectedServer.name}</strong></div>
      <div class="muted">${String(selectedServer.game || "").toUpperCase()}</div>
      <div class="muted">Online: ${online}${max ? ` / ${max}` : ""}</div>
      <div class="muted">Detected events: ${eventCount}</div>
      ${!supported ? '<div class="muted">Players are not supported for this game yet.</div>' : ""}
      ${supported && players.length ? `<div class="chip-list">${players.map((name) => `<span class="chip">${escapeHtml(name)}</span>`).join("")}</div>` : ""}
      ${supported && !players.length ? '<div class="muted">No players online.</div>' : ""}
      ${supported ? `
        <div class="muted" style="margin-top:8px;">Active sessions</div>
        ${sessions.length ? sessions.map((session) => `
          <div class="event-row">
            <div><strong>${escapeHtml(session.player || "-")}</strong></div>
            <div class="muted">Joined at ${formatLineMetric(session.joined_line)} | Last seen ${formatLineMetric(session.last_seen_line)} | Activity span ${typeof session.line_span === "number" ? `${session.line_span} lines` : "-"}</div>
          </div>
        `).join("") : '<div class="muted">No active sessions detected.</div>'}
        <div class="muted" style="margin-top:8px;">Recent player events</div>
        ${recentEvents.length ? recentEvents.map((event) => `
          <div class="event-row">
            <div><strong>${escapeHtml(String(event.event || "event").toUpperCase())}</strong> ${escapeHtml(event.player || "-")}</div>
            <div class="muted">Log position ${formatLineMetric(event.line)}</div>
          </div>
        `).join("") : '<div class="muted">No recent join/leave events.</div>'}
      ` : ""}
    </div>
  `;

  const selector = document.getElementById("playersServerSelect");
  if (selector) {
    selector.addEventListener("change", async (event) => {
      selectedPlayersServerId = String(event.target.value || "");
      await loadPlayers();
    });
  }
}

async function loadGames() {
  const payload = await call("/api/games");
  const games = payload.games || [];
  const host = document.getElementById("games");
  if (!games.length) {
    host.innerHTML = '<div class="muted">No games available.</div>';
    return;
  }

  host.innerHTML = games.map((game) => {
    const gameId = String(game.id || "");
    const status = String(game.status || "UNKNOWN").toUpperCase();
    const setup = game.setup || {};
    const automaticSetup = Boolean(setup.automatic);
    const canCreate = Boolean(game.capabilities && game.capabilities.create_server);
    const requirements = Array.isArray(setup.requirements) ? setup.requirements.filter(Boolean) : [];
    return `
      <article class="game-card">
        <div><strong>${String(game.icon || "")} ${String(game.name || game.display_name || gameId)}</strong></div>
        <div class="muted">${gameId}</div>
        <div class="status ${gameStatusClass(status)} game-status">${gameStatusText(status)}</div>
        ${requirements.length ? `<div class="muted game-requirements">Requirements: ${requirements.join(", ")}</div>` : ""}
        <div class="row game-actions">
          ${canCreate ? `<button data-game-action="create" data-game-id="${gameId}">Create server</button>` : ""}
          ${automaticSetup && (status === "SETUP_REQUIRED" || status === "ERROR") ? `<button data-game-action="setup" data-game-id="${gameId}">Run setup</button>` : ""}
          ${status === "INSTALLING" ? `<button disabled>Setup in progress...</button>` : ""}
        </div>
      </article>
    `;
  }).join("");

  host.querySelectorAll("button[data-game-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = String(button.dataset.gameAction || "");
      const gameId = String(button.dataset.gameId || "");
      if (!action || !gameId) {
        return;
      }
      try {
        hideMessage();
        if (action === "create") {
          await openCreateServer(gameId);
          return;
        }
        const started = await call(`/api/games/${encodeURIComponent(gameId)}/setup`, { method: "POST", body: "{}" });
        const jobId = String(started.job_id || "");
        if (!jobId) {
          throw new Error("Missing job id.");
        }
        await waitForJob(jobId, "Running game setup...", "Game setup failed.");
        showMessage("Game setup completed. You can now create a server if setup is ready.");
        await loadGames();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function loadBackups() {
  const serversPayload = await call("/api/servers");
  const servers = serversPayload.servers || [];
  const host = document.getElementById("backups");
  if (!servers.length) {
    host.innerHTML = `<div class="muted">No servers found.</div>`;
    return;
  }

  const selectedServerId = selectedBackupsServerId || String(servers[0].id);
  const selectedServer = servers.find((item) => String(item.id) === String(selectedServerId)) || servers[0];
  selectedBackupsServerId = String(selectedServer.id);
  const payload = await call(`/api/servers/${selectedServer.id}/backups`);
  const backups = payload.backups || [];
  const restoreHistory = payload.restore_history || [];
  const filterText = String(backupsFilterText || "").trim().toLowerCase();
  const filteredBackups = backups.filter((backup) => {
    if (!filterText) {
      return true;
    }
    const content = `${backup.id || ""} ${backup.name || ""} ${backup.world || ""}`.toLowerCase();
    return content.includes(filterText);
  });

  host.innerHTML = `
    <label class="field">
      <span>Server</span>
      <select id="backupsServerSelect">
        ${servers.map((server) => `<option value="${server.id}"${server.id === selectedServer.id ? " selected" : ""}>${server.name}</option>`).join("")}
      </select>
    </label>
      <label class="field">
        <span>Filter backups</span>
        <input id="backupFilterInput" type="text" placeholder="Search by backup id, name, or world" value="${escapeHtml(backupsFilterText)}" />
      </label>
      <article class="server">
        <div><strong>${selectedServer.name}</strong></div>
        <div class="muted">${String(selectedServer.game || "").toUpperCase()}</div>
        <div class="muted">${filteredBackups.length} shown of ${backups.length} backups</div>
        <div class="row"><button data-action="create-backup" data-id="${selectedServer.id}">Create backup</button></div>
        ${(filteredBackups || []).map((backup) => `
          <div class="backup-row">
            <div>
              <div>${escapeHtml(backup.name || backup.id)}</div>
              <div class="muted">${formatDateTime(backup.created_at)}</div>
              <div class="muted">World: ${escapeHtml(backup.world || "-")} | Mods: ${backup.mods_active ?? "-"} | Version: ${escapeHtml(backup.server_version || "-")}</div>
              <div class="muted">ID: ${escapeHtml(backup.id || "-")}</div>
            </div>
            <button data-action="restore-backup" data-id="${selectedServer.id}" data-backup-id="${backup.id}">Restore</button>
          </div>
        `).join("") || '<div class="muted">No backups match this filter.</div>'}
        <div class="muted">Restore history</div>
        ${(restoreHistory || []).map((entry) => `
          <div class="event-row">
            <div><strong>${escapeHtml(String(entry.status || "unknown").toUpperCase())}</strong> ${escapeHtml(entry.backup_id || "-")}</div>
            <div class="muted">${formatDateTime(entry.restored_at)}${entry.safety_backup_id ? ` | Safety: ${escapeHtml(entry.safety_backup_id)}` : ""}${entry.error ? ` | ${escapeHtml(entry.error)}` : ""}</div>
          </div>
        `).join("") || '<div class="muted">No restores yet.</div>'}
      </article>
    `;

  const serverSelect = document.getElementById("backupsServerSelect");
  if (serverSelect) {
    serverSelect.addEventListener("change", async (event) => {
      selectedBackupsServerId = String(event.target.value || "");
      await loadBackups();
    });
  }

  const backupFilterInput = document.getElementById("backupFilterInput");
  if (backupFilterInput) {
    backupFilterInput.addEventListener("input", async (event) => {
      backupsFilterText = String(event.target.value || "");
      await loadBackups();
    });
  }
  host.querySelectorAll("button[data-action='create-backup']").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = button.dataset.id;
      try {
        hideMessage();
        const response = await call(`/api/servers/${serverId}/backups`, { method: "POST", body: "{}" });
        const jobId = String(response.job_id || "");
        if (!jobId) {
          throw new Error("Missing job id.");
        }
        await waitForJob(jobId, "Creating backup...", "Backup failed.");
        showMessage("Backup created.");
        await loadBackups();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });

  host.querySelectorAll("button[data-action='restore-backup']").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = String(button.dataset.id || "");
      const backupId = String(button.dataset.backupId || "");
      if (!serverId || !backupId) {
        return;
      }
      const typed = window.prompt(`Type the backup ID to confirm restore:\n${backupId}`);
      if (typed === null) {
        return;
      }
      if (typed.trim() !== backupId) {
        showMessage("Backup ID did not match. Restore was canceled.", true);
        return;
      }
      try {
        hideMessage();
        const response = await call(`/api/servers/${serverId}/backups/${encodeURIComponent(backupId)}/restore`, { method: "POST", body: "{}" });
        const jobId = String(response.job_id || "");
        if (!jobId) {
          throw new Error("Missing job id.");
        }
        await waitForJob(jobId, "Restoring backup...", "Restore failed.");
        showMessage("Backup restored.");
        await loadBackups();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

function setView(view) {
  currentView = view;

  if (jobsAutoRefreshTimer) {
    clearInterval(jobsAutoRefreshTimer);
    jobsAutoRefreshTimer = null;
  }
  if (playersAutoRefreshTimer) {
    clearInterval(playersAutoRefreshTimer);
    playersAutoRefreshTimer = null;
  }

  document.getElementById("serversCard").classList.toggle("hidden", view !== "servers");
  document.getElementById("gamesCard").classList.toggle("hidden", view !== "games");
  document.getElementById("jobsCard").classList.toggle("hidden", view !== "jobs");
  document.getElementById("playersCard").classList.toggle("hidden", view !== "players");
  document.getElementById("modsCard").classList.toggle("hidden", view !== "mods");
  document.getElementById("backupsCard").classList.toggle("hidden", view !== "backups");
  document.getElementById("usersCard").classList.toggle("hidden", view !== "users");
  document.getElementById("invitationsCard").classList.toggle("hidden", view !== "invitations");

  if (view === "jobs") {
    jobsAutoRefreshTimer = setInterval(() => {
      loadJobs().catch(() => {
        // no-op
      });
    }, 5000);
  }
  if (view === "players") {
    playersAutoRefreshTimer = setInterval(() => {
      loadPlayers().catch(() => {
        // no-op
      });
    }, 5000);
  }
}

async function refreshCurrentView() {
  if (currentView === "servers") {
    await loadServers();
    return;
  }
  if (currentView === "mods") {
    await loadMods();
    return;
  }
  if (currentView === "games") {
    await loadGames();
    return;
  }
  if (currentView === "jobs") {
    await loadJobs();
    return;
  }
  if (currentView === "players") {
    await loadPlayers();
    return;
  }
  if (currentView === "users") {
    await loadUsersCenter();
    return;
  }
  if (currentView === "invitations") {
    await loadInvitations();
    return;
  }
  await loadBackups();
}

async function openSettings(serverId) {
  selectedSettingsServerId = String(serverId || "");
  if (!selectedSettingsServerId) {
    return;
  }

  const payload = await call(`/api/servers/${selectedSettingsServerId}/settings`);
  const fields = payload.fields || [];
  const fieldSchema = payload.field_schema || [];
  const schemaById = Object.fromEntries(fieldSchema.map((field) => [String(field.id || ""), field]));
  const values = payload.values || {};
  const host = document.getElementById("settingsFormHost");
  const hint = document.getElementById("settingsHint");
  const title = document.getElementById("settingsTitle");
  title.textContent = `Server Settings - ${selectedSettingsServerId}`;

  if (!fields.length) {
    hint.textContent = "No safe editable fields are available for this game yet.";
    host.innerHTML = "";
  } else {
    hint.textContent = "Only game-approved safe settings are shown.";
    host.innerHTML = fields.map((field) => {
      const schema = schemaById[String(field)] || { id: field, label: field, type: "text", placeholder: "", help: "" };
      const label = String(schema.label || field);
      const type = String(schema.type || "text");
      const placeholder = String(schema.placeholder || "");
      const help = String(schema.help || "");
      if (type === "checkbox") {
        const checked = values[field] ? " checked" : "";
        return `<label class="field"><span>${label}</span><input type="checkbox" data-setting-id="${field}"${checked} />${help ? `<small class="hint">${help}</small>` : ""}</label>`;
      }
      const inputType = type === "password" ? "password" : "text";
      const value = inputType === "password" ? "" : String(values[field] || "");
      return `<label class="field"><span>${label}</span><input type="${inputType}" data-setting-id="${field}" value="${value}" placeholder="${placeholder}" />${help ? `<small class="hint">${help}</small>` : ""}</label>`;
    }).join("");
  }

  document.getElementById("settingsCard").classList.remove("hidden");
}

function _fieldRow(label, inputHtml) {
  return `<label class="field"><span>${label}</span>${inputHtml}</label>`;
}

function _collectCreatePayload() {
  const host = document.getElementById("createFormHost");
  const payload = {
    game: createGameId,
  };
  host.querySelectorAll("[data-field-id]").forEach((field) => {
    const key = field.getAttribute("data-field-id");
    payload[key] = field.value;
  });
  return payload;
}

async function _renderCreateForm() {
  const host = document.getElementById("createFormHost");
  const hint = document.getElementById("createHint");
  host.innerHTML = "";
  hint.textContent = "";
  document.getElementById("createSubmitBtn").disabled = true;

  if (!createGameId) {
    return;
  }

  const gameOptions = createGames
    .map((game) => {
      const gameId = String(game.id);
      const selected = gameId === createGameId ? " selected" : "";
      return `<option value="${gameId}"${selected}>${String(game.icon || "")} ${String(game.display_name || gameId)}</option>`;
    })
    .join("");

  const gameSelectorHtml = _fieldRow("Game", `<select id="createGameSelect">${gameOptions}</select>`);

  const schemaPayload = await call(`/api/games/${createGameId}/create-schema`);
  createSchema = schemaPayload.schema || {};
  if (!createSchema.supported) {
    host.innerHTML = gameSelectorHtml;
    const selector = document.getElementById("createGameSelect");
    if (selector) {
      selector.addEventListener("change", async (event) => {
        createGameId = String(event.target.value || "");
        await _renderCreateForm();
      });
    }
    hint.textContent = createSchema.message || "Create this server in Server Manager.";
    return;
  }

  const worldsPayload = await call(`/api/games/${createGameId}/worlds`);
  const worlds = worldsPayload.worlds || [];
  const fields = createSchema.fields || [];
  const html = [];
  for (const field of fields) {
    const id = String(field.id || "");
    const type = String(field.type || "text");
    const label = String(field.label || id);
    if (type === "password") {
      html.push(_fieldRow(label, `<input type="password" data-field-id="${id}" />`));
      continue;
    }
    if (type === "select") {
      const options = (field.options || []).map((opt) => {
        const selected = String(opt.id) === String(field.default || "") ? " selected" : "";
        return `<option value="${String(opt.id)}"${selected}>${String(opt.label || opt.id)}</option>`;
      }).join("");
      html.push(_fieldRow(label, `<select data-field-id="${id}">${options}</select>`));
      continue;
    }
    if (type === "world-selector") {
      const options = worlds.map((w) => `<option value="${String(w.id)}">${String(w.name)}</option>`).join("");
      html.push(_fieldRow(label, `<select data-field-id="${id}"><option value="">New world</option>${options}</select>`));
      continue;
    }
    html.push(_fieldRow(label, `<input type="text" data-field-id="${id}" />`));
  }
  host.innerHTML = gameSelectorHtml + html.join("");
  const selector = document.getElementById("createGameSelect");
  if (selector) {
    selector.addEventListener("change", async (event) => {
      createGameId = String(event.target.value || "");
      await _renderCreateForm();
    });
  }
  hint.textContent = "Only safe user choices are shown here.";
  document.getElementById("createSubmitBtn").disabled = false;
}

async function openCreateServer(initialGameId = "") {
  document.getElementById("createCard").classList.remove("hidden");
  document.getElementById("createFormHost").innerHTML = "Loading...";
  document.getElementById("createSubmitBtn").disabled = true;

  const gamesPayload = await call("/api/games");
  createGames = (gamesPayload.games || []).filter((g) => Boolean(g.capabilities && g.capabilities.create_server));
  if (!createGames.length) {
    document.getElementById("createFormHost").innerHTML = "No games are ready for web creation yet. Open Games and run setup where available.";
    return;
  }

  const preferred = String(initialGameId || "");
  const target = createGames.find((game) => String(game.id) === preferred) || createGames[0];
  createGameId = String(target.id);
  await _renderCreateForm();
}

async function openLogs(serverId) {
  logServerId = serverId;
  logCursor = 0;
  document.getElementById("logsTitle").textContent = `Logs - ${serverId}`;
  document.getElementById("logsCard").classList.remove("hidden");
  document.getElementById("logs").textContent = "";
  await refreshLogs(true);
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(() => refreshLogs(false), 2000);
}

async function refreshLogs(initial) {
  if (!logServerId) return;
  const payload = await call(`/api/servers/${logServerId}/logs?cursor=${logCursor}&limit=${initial ? 250 : 200}`);
  const lines = payload.lines || [];
  logCursor = payload.cursor || logCursor;
  const target = document.getElementById("logs");
  if (initial) {
    target.textContent = lines.join("\n");
  } else if (lines.length) {
    target.textContent += (target.textContent ? "\n" : "") + lines.join("\n");
  }
  target.scrollTop = target.scrollHeight;
}

async function bootstrapLoggedIn() {
  const mePayload = await call("/api/me");
  currentUser = mePayload.user || null;
  const identity = document.getElementById("currentUser");
  identity.textContent = currentUser
    ? `Signed in as ${currentUser.name || currentUser.id} (${currentUser.id})`
    : "";
  identity.classList.toggle("hidden", !currentUser);
  document.getElementById("loginCard").classList.add("hidden");
  document.getElementById("navCard").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  setView("servers");
  await refreshCurrentView();
  if (serversTimer) clearInterval(serversTimer);
  serversTimer = setInterval(refreshCurrentView, 5000);
}

document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const userId = document.getElementById("userId").value.trim();
  const password = document.getElementById("password").value;
  try {
    await call("/api/login", { method: "POST", body: JSON.stringify({ user_id: userId, password }) });
    hideMessage();
    await bootstrapLoggedIn();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    await call("/api/logout", { method: "POST", body: "{}" });
  } catch (_) {
    // no-op
  }
  if (serversTimer) clearInterval(serversTimer);
  if (logTimer) clearInterval(logTimer);
  if (jobsAutoRefreshTimer) clearInterval(jobsAutoRefreshTimer);
  if (playersAutoRefreshTimer) clearInterval(playersAutoRefreshTimer);
  document.getElementById("navCard").classList.add("hidden");
  document.getElementById("serversCard").classList.add("hidden");
  document.getElementById("gamesCard").classList.add("hidden");
  document.getElementById("jobsCard").classList.add("hidden");
  document.getElementById("playersCard").classList.add("hidden");
  document.getElementById("modsCard").classList.add("hidden");
  document.getElementById("backupsCard").classList.add("hidden");
  document.getElementById("usersCard").classList.add("hidden");
  document.getElementById("invitationsCard").classList.add("hidden");
  document.getElementById("settingsCard").classList.add("hidden");
  document.getElementById("accessCard").classList.add("hidden");
  document.getElementById("detailsCard").classList.add("hidden");
  document.getElementById("logsCard").classList.add("hidden");
  document.getElementById("loginCard").classList.remove("hidden");
  document.getElementById("currentUser").classList.add("hidden");
  currentUser = null;
  document.getElementById("logoutBtn").classList.add("hidden");
});

document.getElementById("closeAccessBtn").addEventListener("click", () => {
  document.getElementById("accessCard").classList.add("hidden");
});

document.getElementById("closeDetailsBtn").addEventListener("click", () => {
  document.getElementById("detailsCard").classList.add("hidden");
});

document.getElementById("closeSettingsBtn").addEventListener("click", () => {
  document.getElementById("settingsCard").classList.add("hidden");
});

document.getElementById("settingsSaveBtn").addEventListener("click", async () => {
  if (!selectedSettingsServerId) {
    return;
  }
  const payload = {};
  document.querySelectorAll("[data-setting-id]").forEach((field) => {
    const key = String(field.getAttribute("data-setting-id") || "");
    if (!key) {
      return;
    }
    if (field.type === "checkbox") {
      payload[key] = Boolean(field.checked);
      return;
    }
    payload[key] = String(field.value || "");
  });
  try {
    hideMessage();
    await call(`/api/servers/${selectedSettingsServerId}/settings`, { method: "PATCH", body: JSON.stringify(payload) });
    showMessage("Settings saved.");
    document.getElementById("settingsCard").classList.add("hidden");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navServersBtn").addEventListener("click", async () => {
  try {
    setView("servers");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navGamesBtn").addEventListener("click", async () => {
  try {
    setView("games");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navJobsBtn").addEventListener("click", async () => {
  try {
    setView("jobs");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navInvitationsBtn").addEventListener("click", async () => {
  try {
    setView("invitations");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshJobsBtn").addEventListener("click", async () => {
  try {
    await loadJobs();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshInvitationsBtn").addEventListener("click", async () => {
  try {
    await loadInvitations();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshPlayersBtn").addEventListener("click", async () => {
  try {
    await loadPlayers();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshModsBtn").addEventListener("click", async () => {
  try {
    await loadMods();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshBackupsBtn").addEventListener("click", async () => {
  try {
    await loadBackups();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navPlayersBtn").addEventListener("click", async () => {
  try {
    setView("players");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navModsBtn").addEventListener("click", async () => {
  try {
    setView("mods");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navBackupsBtn").addEventListener("click", async () => {
  try {
    setView("backups");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("navUsersBtn").addEventListener("click", async () => {
  try {
    setView("users");
    await refreshCurrentView();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("refreshUsersBtn").addEventListener("click", async () => {
  try {
    await loadUsersCenter();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("closeLogsBtn").addEventListener("click", () => {
  document.getElementById("logsCard").classList.add("hidden");
  if (logTimer) clearInterval(logTimer);
  logServerId = "";
});

document.getElementById("newServerBtn").addEventListener("click", async () => {
  try {
    hideMessage();
    await openCreateServer();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("closeCreateBtn").addEventListener("click", () => {
  document.getElementById("createCard").classList.add("hidden");
  createSchema = null;
  createGameId = "";
});

document.getElementById("createSubmitBtn").addEventListener("click", async () => {
  try {
    hideMessage();
    const payload = _collectCreatePayload();
    const createResponse = await call("/api/servers", { method: "POST", body: JSON.stringify(payload) });
    const jobId = String(createResponse.job_id || "");
    if (!jobId) {
      throw new Error("Missing job id.");
    }
    await waitForJob(jobId, "Creating server...", "Server creation failed.");
    document.getElementById("createCard").classList.add("hidden");
    await refreshCurrentView();
    showMessage("Server created successfully.");
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

(async () => {
  try {
    await call("/api/status");
    await bootstrapLoggedIn();
  } catch (_) {
    document.getElementById("loginCard").classList.remove("hidden");
  }
})();
