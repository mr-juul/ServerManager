let serversTimer = null;
let logTimer = null;
let logServerId = "";
let logCursor = 0;

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
  host.innerHTML = servers.map((item) => {
    const status = String(item.status || "unknown").toLowerCase();
    return `
      <article class="server">
        <div><strong>${item.name}</strong></div>
        <div class="muted">${String(item.game || "").toUpperCase()}</div>
        <div class="status ${statusClass(status)}">${statusText(status)}</div>
        <div class="row">
          ${status === "stopped" ? `<button data-action="start" data-id="${item.id}">START</button>` : ""}
          ${status === "running" || status === "starting" ? `<button data-action="stop" data-id="${item.id}">STOP</button>` : ""}
          ${status === "running" || status === "starting" || status === "stopping" ? `<button data-action="restart" data-id="${item.id}">RESTART</button>` : ""}
          <button data-action="logs" data-id="${item.id}">LOGS</button>
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
        await call(`/api/servers/${serverId}/${action}`, { method: "POST", body: "{}" });
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
  document.getElementById("serversCard").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  await loadServers();
  if (serversTimer) clearInterval(serversTimer);
  serversTimer = setInterval(loadServers, 5000);
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
  document.getElementById("serversCard").classList.add("hidden");
  document.getElementById("logsCard").classList.add("hidden");
  document.getElementById("loginCard").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.add("hidden");
});

document.getElementById("closeLogsBtn").addEventListener("click", () => {
  document.getElementById("logsCard").classList.add("hidden");
  if (logTimer) clearInterval(logTimer);
  logServerId = "";
});

(async () => {
  try {
    await call("/api/status");
    await bootstrapLoggedIn();
  } catch (_) {
    document.getElementById("loginCard").classList.remove("hidden");
  }
})();
