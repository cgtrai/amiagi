/**
 * Audit Log page — client-side JS.
 */
(function () {
  const pageRoot = document.getElementById("audit-page");
  const tbody = document.getElementById("audit-tbody");
  const paginationEl = document.getElementById("audit-pagination");
  const filterUserEl = document.getElementById("filter-user");
  const filterSearchEl = document.getElementById("filter-search");
  const filterActionEl = document.getElementById("filter-action");
  const filterSinceEl = document.getElementById("filter-since");
  const filterUntilEl = document.getElementById("filter-until");
  const retentionSelectEl = document.getElementById("audit-retention-select");
  const retentionNoteEl = document.getElementById("audit-retention-note");
  const url = new URL(window.location.href);

  const state = {
    page: Number(url.searchParams.get("page") || "1") || 1,
    perPage: Number(url.searchParams.get("per_page") || url.searchParams.get("limit") || "50") || 50,
    total: 0,
    serverLogs: [],
    quick: "all",
  };

  const ACTION_COLORS = {
    create: "badge-success",
    update: "badge-working",
    delete: "badge-danger",
    login: "badge-idle",
    auth: "badge-idle",
    error: "badge-error",
    system: "badge-muted",
    prompt: "badge-working",
    workflow: "badge-working",
    task: "badge-success",
    settings: "badge-muted",
    vault: "badge-danger",
    agent: "badge-idle",
  };

  function esc(value) {
    const node = document.createElement("div");
    node.textContent = value == null ? "" : String(value);
    return node.innerHTML;
  }

  function encodeLog(log) {
    return encodeURIComponent(JSON.stringify(log || {}));
  }

  function decodeLog(value) {
    return JSON.parse(decodeURIComponent(value));
  }

  function detailText(detail) {
    if (detail == null || detail === "") return "–";
    if (typeof detail === "object") return JSON.stringify(detail);
    return String(detail);
  }

  function deriveActionGroup(action) {
    const text = String(action || "").toLowerCase();
    if (!text) return "system";
    if (text.includes("login") || text.includes("logout") || text.startsWith("user.")) return "login";
    if (text.includes("error") || text.includes("fail") || text.includes("exception")) return "error";
    if (text.includes("workflow")) return "workflow";
    if (text.includes("prompt")) return "prompt";
    if (text.includes("task")) return "task";
    if (text.includes("agent")) return "agent";
    if (text.includes("vault")) return "vault";
    if (text.includes("setting")) return "settings";
    if (text.includes("permission") || text.includes("role")) return "permission";
    if (text.includes("create")) return "create";
    if (text.includes("update") || text.includes("edit")) return "update";
    if (text.includes("delete") || text.includes("remove")) return "delete";
    return text.split(".")[0] || "system";
  }

  function syncControlsFromDataset() {
    if (!pageRoot) return;
    filterUserEl.value = pageRoot.dataset.initialUser || url.searchParams.get("user") || "";
    filterActionEl.value = pageRoot.dataset.initialAction || url.searchParams.get("action") || "";
    filterSinceEl.value = pageRoot.dataset.initialSince || url.searchParams.get("since") || "";
    filterUntilEl.value = pageRoot.dataset.initialUntil || url.searchParams.get("until") || "";
    filterSearchEl.value = pageRoot.dataset.initialSearch || url.searchParams.get("q") || "";
    const retention = pageRoot.dataset.retentionDays || "90";
    retentionSelectEl.value = retention === "forever" ? "forever" : String(retention);
    retentionNoteEl.textContent = retention === "forever"
      ? "Current retention: forever"
      : `Current retention: ${retention}d`;
    if ((pageRoot.dataset.initialErrorOnly || url.searchParams.get("error_only") || "") !== "") {
      state.quick = "errors";
      document.querySelectorAll("[data-quick]").forEach((b) => b.classList.toggle("active", b.dataset.quick === "errors"));
    }
  }

  function currentFilterParams(includePaging) {
    const params = new URLSearchParams();
    if (includePaging !== false) {
      params.set("page", String(state.page));
      params.set("per_page", String(state.perPage));
    }
    const user = filterUserEl.value.trim();
    const action = filterActionEl.value.trim();
    const since = filterSinceEl.value;
    const until = filterUntilEl.value;
    const search = filterSearchEl.value.trim();
    if (user) params.set("user", user);
    if (action) params.set("action", action);
    if (since) params.set("since", since);
    if (until) params.set("until", until);
    if (search) params.set("q", search);
    if (state.quick === "errors") params.set("error_only", "1");
    return params;
  }

  function updateBrowserUrl() {
    const params = currentFilterParams(true);
    const next = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState({}, "", next);
  }

  function applyClientFilters(logs) {
    const localSearch = filterSearchEl.value.trim().toLowerCase();
    if (!localSearch) return logs;
    return logs.filter((log) => {
      const haystack = [
        log.email,
        log.user_id,
        log.session_id,
        log.action,
        log.ip_address,
        detailText(log.detail),
      ].join(" ").toLowerCase();
      return haystack.includes(localSearch);
    });
  }

  async function loadLogs() {
    const params = currentFilterParams(true);
    updateBrowserUrl();
    try {
      const r = await fetch(`/admin/audit?${params.toString()}`, {
        headers: { Accept: "application/json" },
      });
      const data = await r.json();
      state.serverLogs = data.logs || data.items || [];
      state.total = data.total || state.serverLogs.length;
      renderLogs(applyClientFilters(state.serverLogs));
      renderPagination(state.total);
      if (retentionNoteEl && data.retention_days !== undefined) {
        retentionNoteEl.textContent = data.retention_days == null
          ? "Current retention: forever"
          : `Current retention: ${data.retention_days}d`;
      }
    } catch (_e) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">' + window.t("audit.error.load_failed", "Failed to load logs") + "</td></tr>";
    }
  }

  function renderUserCell(log) {
    const label = esc(log.email || log.user_id || "system");
    if (!log.user_id && !log.email) return label;
    return `<a href="#" class="audit-user-link" onclick="auditOpenUser(event, '${esc(log.user_id || "")}', '${esc(log.email || "")}')">${label}</a>`;
  }

  function renderSessionCell(log) {
    if (!log.session_id) return "";
    return ` <a href="/sessions?session_id=${encodeURIComponent(log.session_id)}" class="audit-session-link" onclick="event.stopPropagation()">Replay</a>`;
  }

  function renderLogs(logs) {
    if (!logs.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">' + window.t("audit.empty", "No logs found") + "</td></tr>";
      return;
    }
    tbody.innerHTML = logs.map((log) => {
      const actionGroup = deriveActionGroup(log.action);
      const colorCls = ACTION_COLORS[actionGroup] || "badge-muted";
      const preview = detailText(log.detail).slice(0, 120);
      return `<tr class="audit-row" data-log="${encodeLog(log)}" onclick="openAuditDetail(this)">
        <td>${log.created_at ? new Date(log.created_at).toLocaleString() : "–"}</td>
        <td>${renderUserCell(log)}${renderSessionCell(log)}</td>
        <td><span class="glass-badge ${colorCls}">${esc(log.action || "–")}</span></td>
        <td class="audit-detail-cell">${esc(preview)}</td>
        <td>${esc(log.ip_address || "–")}</td>
      </tr>`;
    }).join("");
  }

  function renderPagination(total) {
    const totalPages = Math.ceil(total / state.perPage);
    if (totalPages <= 1) {
      paginationEl.innerHTML = "";
      return;
    }
    let html = "";
    if (state.page > 1) html += `<button class="glass-btn glass-btn--sm" onclick="auditPage(${state.page - 1})">← Prev</button>`;
    html += `<span style="padding:0 8px;color:var(--text-muted)">Page ${state.page} / ${totalPages}</span>`;
    if (state.page < totalPages) html += `<button class="glass-btn glass-btn--sm" onclick="auditPage(${state.page + 1})">Next →</button>`;
    paginationEl.innerHTML = html;
  }

  window.auditPage = function (p) {
    state.page = p;
    loadLogs();
  };

  window.auditOpenUser = async function (event, userId, email) {
    if (event) event.stopPropagation();
    let html = `<div style="display:grid;gap:var(--space-3)">`;
    html += `<div><label>User</label><div class="drawer-code">${esc(email || userId || "system")}</div></div>`;
    if (userId) {
      html += `<div><label>User ID</label><div class="drawer-code">${esc(userId)}</div></div>`;
      html += `<div><a class="glass-btn glass-btn--sm glass-btn--ghost" href="/admin/audit?user=${encodeURIComponent(userId)}">Filtered audit log</a></div>`;
    }
    try {
      if (userId) {
        const r = await fetch(`/admin/users/${encodeURIComponent(userId)}`, { headers: { Accept: "application/json" } });
        if (r.ok) {
          const user = await r.json();
          html = `<div style="display:grid;gap:var(--space-3)">
            <div><label>Email</label><div class="drawer-code">${esc(user.email || email || "–")}</div></div>
            <div><label>Name</label><div class="drawer-code">${esc(user.display_name || "–")}</div></div>
            <div><label>Status</label><div class="drawer-code">${user.is_blocked ? "Blocked" : (user.is_active ? "Active" : "Inactive")}</div></div>
            <div><label>User ID</label><div class="drawer-code">${esc(user.id || userId)}</div></div>
            <div><a class="glass-btn glass-btn--sm glass-btn--ghost" href="/admin/audit?user=${encodeURIComponent(user.id || userId)}">Filtered audit log</a></div>
          </div>`;
        }
      }
    } catch (_e) {
      /* fallback drawer is enough */
    }
    if (typeof openDetailDrawer === "function") openDetailDrawer("User activity", html);
  };

  window.openAuditDetail = function (tr) {
    try {
      const log = decodeLog(tr.dataset.log);
      const replayLink = log.session_id
        ? `<a class="glass-btn glass-btn--sm glass-btn--ghost" href="/sessions?session_id=${encodeURIComponent(log.session_id)}">Open session replay</a>`
        : "";
      const html = `<div style="display:grid;gap:var(--space-3)">
        <div><label>Date</label><div class="drawer-code">${log.created_at ? new Date(log.created_at).toLocaleString() : "–"}</div></div>
        <div><label>User</label><div class="drawer-code">${esc(log.email || log.user_id || "system")}</div></div>
        <div><label>Action</label><div class="drawer-code">${esc(log.action || "")}</div></div>
        <div><label>Session</label><div class="drawer-code">${esc(log.session_id || "–")}</div></div>
        <div><label>IP Address</label><div class="drawer-code">${esc(log.ip_address || "–")}</div></div>
        <div><label>Details</label><pre class="drawer-code" style="white-space:pre-wrap;max-height:400px;overflow:auto">${esc(typeof log.detail === "object" ? JSON.stringify(log.detail, null, 2) : String(log.detail || "–"))}</pre></div>
        ${replayLink ? `<div>${replayLink}</div>` : ""}
      </div>`;
      if (typeof openDetailDrawer === "function") openDetailDrawer("Audit Entry", html);
    } catch (_e) {
      /* ignore */
    }
  };

  document.querySelectorAll("[data-quick]").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-quick]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.quick = btn.dataset.quick || "all";
      filterSinceEl.value = "";
      filterUntilEl.value = "";
      if (state.quick === "today") {
        filterSinceEl.value = new Date().toISOString().slice(0, 10);
      } else if (state.quick === "7d") {
        const d = new Date();
        d.setDate(d.getDate() - 7);
        filterSinceEl.value = d.toISOString().slice(0, 10);
      }
      state.page = 1;
      loadLogs();
    })
  );

  document.getElementById("btn-filter-apply").addEventListener("click", () => {
    state.page = 1;
    loadLogs();
  });

  [filterUserEl, filterSearchEl, filterActionEl, filterSinceEl, filterUntilEl].forEach((el) => {
    if (!el) return;
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        state.page = 1;
        loadLogs();
      }
    });
  });

  filterSearchEl.addEventListener("input", () => {
    renderLogs(applyClientFilters(state.serverLogs));
  });

  document.getElementById("btn-export-csv").addEventListener("click", () => {
    window.location = `/admin/audit/export?format=csv&${currentFilterParams(false).toString()}`;
  });
  document.getElementById("btn-export-json").addEventListener("click", () => {
    window.location = `/admin/audit/export?format=json&${currentFilterParams(false).toString()}`;
  });

  document.getElementById("btn-retention-save").addEventListener("click", async () => {
    try {
      const retentionValue = retentionSelectEl.value;
      const r = await fetch("/admin/audit/retention", {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ retention_days: retentionValue }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "save_failed");
      retentionNoteEl.textContent = data.retention_days == null
        ? "Current retention: forever"
        : `Current retention: ${data.retention_days}d`;
      if (typeof showToast === "function") showToast("Audit retention updated", "success");
    } catch (_e) {
      if (typeof showToast === "function") showToast("Failed to update audit retention", "error");
    }
  });

  let liveTailWs = null;
  let liveTailActive = false;
  let reconnectAttempts = 0;
  const MAX_RECONNECT = 8;
  const BASE_DELAY_MS = 1000;

  document.getElementById("audit-live-tail").addEventListener("change", function () {
    if (this.checked) startLiveTail();
    else stopLiveTail();
  });

  function prependLiveLog(log) {
    state.serverLogs.unshift(log);
    renderLogs(applyClientFilters(state.serverLogs));
  }

  function startLiveTail() {
    liveTailActive = true;
    connectLiveTail();
  }

  function connectLiveTail() {
    if (!liveTailActive) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    liveTailWs = new WebSocket(`${proto}//${location.host}/ws/events`);

    liveTailWs.onopen = function () {
      reconnectAttempts = 0;
    };

    liveTailWs.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "audit" || msg.type === "activity") {
          prependLiveLog(msg.data || msg);
        }
      } catch (_err) {
        /* ignore */
      }
    };

    liveTailWs.onclose = function () {
      liveTailWs = null;
      if (!liveTailActive) return;
      scheduleReconnect();
    };

    liveTailWs.onerror = function () {
      if (liveTailWs) {
        try { liveTailWs.close(); } catch (_) {}
      }
    };
  }

  function scheduleReconnect() {
    if (reconnectAttempts >= MAX_RECONNECT) {
      const toggle = document.getElementById("audit-live-tail");
      if (toggle) toggle.checked = false;
      liveTailActive = false;
      if (typeof showToast === "function") {
        showToast(window.t("audit.ws.reconnecting", "Reconnecting live tail…") + " — max retries reached", "error");
      }
      return;
    }
    const delay = BASE_DELAY_MS * Math.pow(2, reconnectAttempts) + Math.random() * 500;
    reconnectAttempts += 1;
    setTimeout(() => {
      if (liveTailActive) connectLiveTail();
    }, delay);
  }

  function stopLiveTail() {
    liveTailActive = false;
    reconnectAttempts = 0;
    if (liveTailWs) {
      liveTailWs.close();
      liveTailWs = null;
    }
  }

  syncControlsFromDataset();
  loadLogs();
})();
