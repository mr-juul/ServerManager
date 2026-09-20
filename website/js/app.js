let serversTimer = null;
let logTimer = null;
let logServerId = "";
let logCursor = 0;
let createSchema = null;
let createGameId = "";
let currentView = "servers";
let selectedModsServerId = "";
let selectedBackupsServerId = "";
let selectedSettingsServerId = "";
let selectedPlayersServerId = "";

function formatDateTime(epochSeconds) {
  const value = Number(epochSeconds || 0);
  if (!value) {
    return "-";
  }
  const date = new Date(value * 1000);
  return date.toLocaleString();
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
  box.textContent = text;
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
          <button data-action="go-players" data-id="${item.id}">PLAYERS</button>
          <button data-action="go-mods" data-id="${item.id}">MODS</button>
          <button data-action="go-backups" data-id="${item.id}">BACKUPS</button>
          <button data-action="settings" data-id="${item.id}">SETTINGS</button>
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
        await call(`/api/servers/${serverId}/${action}`, { method: "POST", body: "{}" });
        await loadServers();
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
  const serverModsPayload = await call(`/api/servers/${selectedServer.id}/mods`);
  const serverMods = serverModsPayload.mods || [];
  const profilesPayload = await call(`/api/mod-profiles?game=${encodeURIComponent(String(selectedServer.game || ""))}`);
  const profiles = profilesPayload.profiles || [];

  const profileOptions = profiles.map((profile) => `<option value="${String(profile.name || "")}">${String(profile.name || "")}</option>`).join("");

  host.innerHTML = `
    <label class="field">
      <span>Server</span>
      <select id="modsServerSelect">
        ${servers.map((server) => `<option value="${server.id}"${server.id === selectedServer.id ? " selected" : ""}>${server.name}</option>`).join("")}
      </select>
    </label>
    <div class="row">
      <select id="modProfileSelect">
        <option value="">Select profile</option>
        ${profileOptions}
      </select>
      <button id="applyProfileBtn" class="small">Apply profile</button>
    </div>
    ${serverMods.map((mod) => `
      <article class="server">
        <div><strong>${mod.name || mod.id}</strong></div>
        <div class="muted">${String(mod.game || "").toUpperCase()}</div>
        <div class="muted">Version: ${mod.version || "unknown"}</div>
        <div class="status ${mod.enabled ? "running" : "stopped"}">${mod.enabled ? "ENABLED" : "DISABLED"}</div>
        <div class="row">
          <button data-mod-action="install" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}">Install</button>
          <button data-mod-action="enable" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}">Enable</button>
          <button data-mod-action="disable" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}">Disable</button>
          <button data-mod-action="uninstall" data-server-id="${selectedServer.id}" data-mod-id="${mod.id}">Uninstall</button>
        </div>
      </article>
    `).join("")}
  `;

  document.getElementById("modsServerSelect").addEventListener("change", async (event) => {
    selectedModsServerId = String(event.target.value || "");
    host.setAttribute("data-selected-server", selectedModsServerId);
    await loadMods();
  });

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
      ${!supported ? '<div class="muted">Players are not supported for this game yet.</div>' : ""}
      ${supported && players.length ? `<div class="muted">${players.join(", ")}</div>` : ""}
      ${supported && !players.length ? '<div class="muted">No players online.</div>' : ""}
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

  host.innerHTML = `
    <label class="field">
      <span>Server</span>
      <select id="backupsServerSelect">
        ${servers.map((server) => `<option value="${server.id}"${server.id === selectedServer.id ? " selected" : ""}>${server.name}</option>`).join("")}
      </select>
    </label>
      <article class="server">
        <div><strong>${selectedServer.name}</strong></div>
        <div class="muted">${String(selectedServer.game || "").toUpperCase()}</div>
        <div class="muted">${backups.length} backups</div>
        <div class="row"><button data-action="create-backup" data-id="${selectedServer.id}">Create backup</button></div>
        ${(backups || []).slice(0, 5).map((backup) => `
          <div class="backup-row">
            <div>
              <div>${backup.name || backup.id}</div>
              <div class="muted">${formatDateTime(backup.created_at)}</div>
              <div class="muted">World: ${backup.world || "-"} | Mods: ${backup.mods_active ?? "-"} | Version: ${backup.server_version || "-"}</div>
            </div>
            <button data-action="restore-backup" data-id="${selectedServer.id}" data-backup-id="${backup.id}">Restore</button>
          </div>
        `).join("")}
        <div class="muted">Restore history</div>
        ${(restoreHistory || []).slice(0, 5).map((entry) => `
          <div class="muted">${formatDateTime(entry.restored_at)} ${String(entry.status || "unknown").toUpperCase()} restore ${entry.backup_id}${entry.safety_backup_id ? ` | safety ${entry.safety_backup_id}` : ""}${entry.error ? ` | ${entry.error}` : ""}</div>
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
      const confirmed = window.confirm("Restore this backup? A safety backup will be created first.");
      if (!confirmed) {
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
  document.getElementById("serversCard").classList.toggle("hidden", view !== "servers");
  document.getElementById("playersCard").classList.toggle("hidden", view !== "players");
  document.getElementById("modsCard").classList.toggle("hidden", view !== "mods");
  document.getElementById("backupsCard").classList.toggle("hidden", view !== "backups");
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
  if (currentView === "players") {
    await loadPlayers();
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

  const schemaPayload = await call(`/api/games/${createGameId}/create-schema`);
  createSchema = schemaPayload.schema || {};
  if (!createSchema.supported) {
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
  host.innerHTML = html.join("");
  hint.textContent = "Only safe user choices are shown here.";
  document.getElementById("createSubmitBtn").disabled = false;
}

async function openCreateServer() {
  document.getElementById("createCard").classList.remove("hidden");
  document.getElementById("createFormHost").innerHTML = "Loading...";
  document.getElementById("createSubmitBtn").disabled = true;

  const gamesPayload = await call("/api/games");
  const games = (gamesPayload.games || []).filter((g) => Boolean(g.capabilities && g.capabilities.create_server));
  if (!games.length) {
    document.getElementById("createFormHost").innerHTML = "No games are available for web creation yet.";
    return;
  }

  createGameId = String(games[0].id);
  const options = games
    .map((game) => `<option value="${String(game.id)}">${String(game.icon || "")} ${String(game.display_name || game.id)}</option>`)
    .join("");
  document.getElementById("createFormHost").innerHTML = _fieldRow("Game", `<select id="createGameSelect">${options}</select>`);
  document.getElementById("createGameSelect").addEventListener("change", async (event) => {
    createGameId = String(event.target.value || "");
    await _renderCreateForm();
  });
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
  document.getElementById("loginCard").classList.add("hidden");
  document.getElementById("navCard").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  setView("servers");
  await refreshCurrentView();
  if (serversTimer) clearInterval(serversTimer);
  serversTimer = setInterval(refreshCurrentView, 5000);
}

document.getElementById("loginBtn").addEventListener("click", async () => {
  const password = document.getElementById("password").value;
  try {
    await call("/api/login", { method: "POST", body: JSON.stringify({ password }) });
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
  document.getElementById("navCard").classList.add("hidden");
  document.getElementById("serversCard").classList.add("hidden");
  document.getElementById("playersCard").classList.add("hidden");
  document.getElementById("modsCard").classList.add("hidden");
  document.getElementById("backupsCard").classList.add("hidden");
  document.getElementById("settingsCard").classList.add("hidden");
  document.getElementById("logsCard").classList.add("hidden");
  document.getElementById("loginCard").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.add("hidden");
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
