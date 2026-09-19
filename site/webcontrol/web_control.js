let csrfToken = "";
let serversTimer = null;
let logsTimer = null;
let logServerId = "";
let logCursor = 0;

function showMessage(text, isError = false) {
  const box = document.getElementById("message");
  box.textContent = text;
  box.classList.remove("hidden");
  box.style.borderColor = isError ? "#7a3f3f" : "#2d4055";
  box.style.color = isError ? "#ffb8b8" : "#cde1ff";
}

function hideMessage() {
  document.getElementById("message").classList.add("hidden");
}

async function call(path, options = {}) {
  const headers = options.headers || {};
  if (options.method && options.method !== "GET") {
    headers["X-CSRF-Token"] = csrfToken;
  }
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { credentials: "include", ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.error || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data;
}

function statusClass(state) {
  if (["RUNNING", "STARTING"].includes(state)) return "ok";
  if (["STOPPING", "UNKNOWN"].includes(state)) return "warn";
  if (["CRASHED"].includes(state)) return "bad";
  return "";
}

function actionButtons(server) {
  if (server.state === "STOPPED") {
    return `<button data-action="start" data-id="${server.id}">Start</button><button data-action="logs" data-id="${server.id}">Logs</button>`;
  }
  if (server.state === "UNKNOWN") {
    return `<button data-action="logs" data-id="${server.id}">Logs</button>`;
  }
  return `<button class="warn" data-action="stop" data-id="${server.id}">Stop</button><button class="bad" data-action="restart" data-id="${server.id}">Restart</button><button data-action="logs" data-id="${server.id}">Logs</button>`;
}

async function loadServers() {
  const data = await call("/api/servers");
  const host = document.getElementById("servers");
  host.innerHTML = data.servers.map((server) => `
    <div class="card server">
      <strong>${server.name}</strong>
      <div class="muted">${server.game}</div>
      <div class="status ${statusClass(server.state)}">${server.state}</div>
      ${server.players ? `<div>Players: ${server.players}</div>` : ""}
      <div class="row">${actionButtons(server)}</div>
    </div>
  `).join("");

  host.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const id = btn.dataset.id;
      try {
        if (action === "logs") {
          openLogs(id);
          return;
        }
        if (action === "stop" && !confirm(`Stop ${id}?`)) return;
        if (action === "restart" && !confirm(`Restart ${id}?`)) return;
        hideMessage();
        await call(`/api/servers/${id}/${action}`, { method: "POST", body: "{}" });
        await loadServers();
      } catch (err) {
        showMessage(String(err.message || err), true);
      }
    });
  });
}

async function openLogs(serverId) {
  logServerId = serverId;
  logCursor = 0;
  document.getElementById("logsTitle").textContent = `Logs: ${serverId}`;
  document.getElementById("logsBox").classList.remove("hidden");
  document.getElementById("logs").textContent = "";
  await refreshLogs(true);
  if (logsTimer) clearInterval(logsTimer);
  logsTimer = setInterval(() => refreshLogs(false), 1500);
}

async function refreshLogs(initial) {
  if (!logServerId) return;
  const data = await call(`/api/servers/${logServerId}/logs?cursor=${logCursor}&limit=${initial ? 300 : 200}`);
  logCursor = data.cursor;
  const logsEl = document.getElementById("logs");
  if (initial) {
    logsEl.textContent = data.lines.join("\n");
  } else if (data.lines.length) {
    logsEl.textContent += (logsEl.textContent ? "\n" : "") + data.lines.join("\n");
  }
  logsEl.scrollTop = logsEl.scrollHeight;
}

async function bootstrap() {
  try {
    const status = await call("/api/auth/status");
    csrfToken = status.csrf_token || "";

    document.getElementById("setupBox").classList.add("hidden");
    document.getElementById("loginBox").classList.add("hidden");
    document.getElementById("appBox").classList.add("hidden");
    document.getElementById("logoutBtn").classList.add("hidden");

    if (!status.configured) {
      document.getElementById("setupBox").classList.remove("hidden");
      return;
    }

    if (!status.authenticated) {
      document.getElementById("loginBox").classList.remove("hidden");
      return;
    }

    document.getElementById("appBox").classList.remove("hidden");
    document.getElementById("logoutBtn").classList.remove("hidden");
    await loadServers();
    if (serversTimer) clearInterval(serversTimer);
    serversTimer = setInterval(loadServers, 2000);
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
}

document.getElementById("setupBtn").addEventListener("click", async () => {
  const password = document.getElementById("setupPassword").value;
  try {
    hideMessage();
    await call("/api/auth/setup", { method: "POST", body: JSON.stringify({ password }) });
    showMessage("Password configured. Please log in.");
    await bootstrap();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("loginBtn").addEventListener("click", async () => {
  const password = document.getElementById("loginPassword").value;
  try {
    hideMessage();
    const data = await call("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
    csrfToken = data.csrf_token || "";
    await bootstrap();
  } catch (err) {
    showMessage(String(err.message || err), true);
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    await call("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {
    // noop
  }
  if (serversTimer) clearInterval(serversTimer);
  if (logsTimer) clearInterval(logsTimer);
  logServerId = "";
  await bootstrap();
});

document.getElementById("logsCloseBtn").addEventListener("click", () => {
  document.getElementById("logsBox").classList.add("hidden");
  if (logsTimer) clearInterval(logsTimer);
  logServerId = "";
});

document.getElementById("logsRefreshBtn").addEventListener("click", () => refreshLogs(false));

bootstrap();
