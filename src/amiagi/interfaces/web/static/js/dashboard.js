/**
 * Dashboard controller — orchestrates panels, WebSocket connection,
 * and agent sidebar.
 */
(function () {
  "use strict";

  // -------------------------------------------------------------------
  // Panel configuration
  // -------------------------------------------------------------------
  const PANELS = [
    { id: "agents-overview", label: window.t ? window.t("dashboard.panel.agents_overview", "Agents Overview") : "Agents Overview", default: true },
    { id: "task-board",      label: window.t ? window.t("dashboard.panel.task_board", "Task Board") : "Task Board",      default: true },
    { id: "metrics",         label: window.t ? window.t("dashboard.panel.metrics", "Metrics") : "Metrics",          default: true },
    { id: "event-log",       label: window.t ? window.t("dashboard.panel.event_log", "Event Log") : "Event Log",        default: true },
    { id: "costs",           label: window.t ? window.t("dashboard.panel.costs", "Costs") : "Costs",            default: false },
    { id: "system-health",   label: window.t ? window.t("dashboard.panel.system_health", "System Health") : "System Health",    default: true },
  ];

  const STORAGE_KEY = "amiagi_dashboard_panels";

  // -------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------
  let ws = null;

  // -------------------------------------------------------------------
  // Shared agent data cache (avoids duplicate /api/agents fetches)
  // -------------------------------------------------------------------
  let _agentDataCache = null;
  let _agentDataTs = 0;
  let _agentDataPromise = null; // de-dup in-flight requests
  const _AGENT_CACHE_TTL = 2000; // 2 seconds

  async function fetchAgents() {
    const now = Date.now();
    if (_agentDataCache && now - _agentDataTs < _AGENT_CACHE_TTL) return _agentDataCache;
    if (_agentDataPromise) return _agentDataPromise;
    _agentDataPromise = fetch("/api/agents").then(r => r.json()).then(data => {
      _agentDataCache = data;
      _agentDataTs = Date.now();
      _agentDataPromise = null;
      return data;
    }).catch(err => {
      _agentDataPromise = null;
      throw err;
    });
    return _agentDataPromise;
  }

  // -------------------------------------------------------------------
  // Panel preferences (localStorage)
  // -------------------------------------------------------------------
  function loadPanelPrefs() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) { /* ignore */ }
    const defaults = {};
    for (const p of PANELS) defaults[p.id] = p.default;
    return defaults;
  }

  function savePanelPrefs(prefs) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  }

  // -------------------------------------------------------------------
  // Panel visibility
  // -------------------------------------------------------------------
  function applyPanelVisibility() {
    const prefs = loadPanelPrefs();
    for (const p of PANELS) {
      const el = document.getElementById(`panel-${p.id}`);
      if (el) el.style.display = prefs[p.id] ? "" : "none";
      const cb = document.getElementById(`cb-${p.id}`);
      if (cb) cb.checked = !!prefs[p.id];
    }
  }

  function buildPanelSelector(container) {
    if (!container) return;
    const prefs = loadPanelPrefs();
    container.innerHTML = PANELS.map(p => `
      <label class="panel-checkbox">
        <input type="checkbox" id="cb-${p.id}" ${prefs[p.id] ? "checked" : ""}
               data-panel="${p.id}">
        <span>${p.label}</span>
      </label>
    `).join("");
    container.addEventListener("change", (e) => {
      const cb = e.target;
      if (!cb.dataset.panel) return;
      const prefs = loadPanelPrefs();
      prefs[cb.dataset.panel] = cb.checked;
      savePanelPrefs(prefs);
      applyPanelVisibility();
    });
  }

  // -------------------------------------------------------------------
  // JWT token for WebSocket auth
  // -------------------------------------------------------------------
  function getWSToken() {
    // Read from cookie
    const match = document.cookie.match(/(?:^|;\s*)amiagi_session=([^;]+)/);
    return match ? match[1] : "";
  }

  // -------------------------------------------------------------------
  // Global WebSocket with exponential backoff
  // -------------------------------------------------------------------
  let _globalReconnectAttempt = 0;
  const _MAX_RECONNECT_DELAY = 30000;

  function connectGlobalWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const token = getWSToken();
    const url = `${proto}//${location.host}/ws/events?token=${encodeURIComponent(token)}`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      console.log("[dashboard] Global WS connected");
      _globalReconnectAttempt = 0;
      setConnectionStatus(true);
    };
    ws.onclose = (e) => {
      console.log("[dashboard] Global WS disconnected", e.code, e.reason);
      setConnectionStatus(false);
      // Exponential backoff: 1s, 2s, 4s, 8s… max 30s
      const delay = Math.min(1000 * Math.pow(2, _globalReconnectAttempt), _MAX_RECONNECT_DELAY);
      _globalReconnectAttempt++;
      setTimeout(connectGlobalWS, delay);
    };
    ws.onerror = (e) => console.error("[dashboard] WS error", e);

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "ping") {
          // Reply to server heartbeat
          ws.send(JSON.stringify({ type: "pong" }));
          return;
        }
        handleGlobalEvent(msg);
      } catch (_) { /* ignore */ }
    };
  }

  // -------------------------------------------------------------------
  // Connection status indicator
  // -------------------------------------------------------------------
  function setConnectionStatus(connected) {
    let indicator = document.getElementById("ws-status");
    if (!indicator) {
      indicator = document.createElement("div");
      indicator.id = "ws-status";
      indicator.className = "ws-status-badge";
      document.body.appendChild(indicator);
    }
    if (connected) {
      indicator.textContent = "";
      indicator.style.display = "none";
    } else {
      indicator.textContent = window.t("dashboard.ws.reconnecting", "Reconnecting…");
      indicator.style.display = "flex";
    }
  }

  function handleGlobalEvent(msg) {
    // Update event ticker
    const ticker = document.querySelector("event-ticker");
    if (ticker) ticker.addEvent(msg);

    // Update agent sidebar badges
    if (msg.type === "actor_state") {
      updateSidebarBadge(msg.actor, msg.state);
    }
  }

  // -------------------------------------------------------------------
  // Agent sidebar
  // -------------------------------------------------------------------
  function updateSidebarBadge(actor, state) {
    const badge = document.querySelector(`.agent-badge[data-actor="${actor}"]`);
    if (badge) {
      badge.textContent = state;
      badge.className = `agent-badge glass-badge badge-${state.toLowerCase()}`;
    }
  }

  async function loadAgentSidebar() {
    const list = document.getElementById("agent-list");
    if (!list) return;

    try {
      const data = await fetchAgents();
      if (!data.agents || data.agents.length === 0) {
        list.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.agents.empty", "No agents registered") + '</div>';
        return;
      }

      const incoming = data.agents;
      const incomingIds = new Set(incoming.map(a => a.agent_id));

      // Build a map of existing cards for fast lookup
      const existingItems = list.querySelectorAll(".agent-sidebar-item");
      const existingMap = new Map();
      existingItems.forEach(el => existingMap.set(el.dataset.agentId, el));

      // Remove agents no longer present
      existingMap.forEach((el, id) => {
        if (!incomingIds.has(id)) el.remove();
      });

      // Update existing or create new cards
      for (const a of incoming) {
        const existing = existingMap.get(a.agent_id);
        if (existing) {
          // Update attributes on existing card (only changed ones)
          const card = existing.querySelector("agent-card");
          if (card) {
            if (card.getAttribute("state") !== a.state) card.setAttribute("state", a.state);
            if (card.getAttribute("name") !== a.name) card.setAttribute("name", a.name);
            if (card.getAttribute("role") !== a.role) card.setAttribute("role", a.role);
            if (card.getAttribute("model") !== a.model_name) card.setAttribute("model", a.model_name);
          }
        } else {
          // Create new card element
          const div = document.createElement("div");
          div.className = "agent-sidebar-item";
          div.dataset.agentId = a.agent_id;
          div.setAttribute("role", "button");
          div.tabIndex = 0;
          div.innerHTML = `
            <agent-card name="${esc(a.name)}"
                        role="${esc(a.role)}"
                        state="${esc(a.state)}"
                        model="${esc(a.model_name)}"
                        agent-id="${esc(a.agent_id)}">
            </agent-card>
          `;
          div.addEventListener("click", () => { location.href = "/agents/" + encodeURIComponent(a.agent_id); });
          div.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { location.href = "/agents/" + encodeURIComponent(a.agent_id); }
          });
          list.appendChild(div);
        }
      }
    } catch (err) {
      list.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.agents.load_failed", "Failed to load agents") + '</div>';
      console.error("[dashboard] agent list error", err);
    }
  }



  // -------------------------------------------------------------------
  // Agents Overview panel
  // -------------------------------------------------------------------
  async function loadAgentsOverview() {
    const container = document.getElementById("agents-overview-content");
    if (!container) return;
    try {
      const data = await fetchAgents();
      const agents = data.agents || [];
      if (!agents.length) {
        container.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.agents.empty", "No agents registered.") + '</div>';
        return;
      }
      container.innerHTML = agents.map(function (a) {
        const stateColor = {idle: "muted", working: "success", paused: "idle", error: "error", terminated: "danger"}[a.state] || "muted";
        return '<div class="agent-overview-row">' +
          '<span class="agent-overview-name">' + esc(a.name || a.agent_id) + '</span>' +
          '<span class="glass-badge glass-badge--' + stateColor + '">' + esc(a.state) + '</span>' +
          '<span class="agent-overview-model">' + esc(a.model_name || "—") + '</span>' +
          '</div>';
      }).join("");
    } catch (err) {
      container.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.agents.load_failed", "Failed to load agent data.") + '</div>';
    }
  }

  // -------------------------------------------------------------------
  // Data loaders
  // -------------------------------------------------------------------
  async function loadTasks() {
    const board = document.querySelector("task-board");
    if (!board) return;
    try {
      const resp = await fetch("/api/tasks");
      const data = await resp.json();
      board.setTasks(data.tasks || []);
    } catch (err) {
      console.error("[dashboard] tasks error", err);
    }
  }

  async function loadMetrics() {
    const container = document.getElementById("metrics-cards");
    if (!container) return;
    try {
      const resp = await fetch("/api/metrics");
      const data = await resp.json();
      const m = data.metrics || {};
      container.innerHTML = "";
      const entries = Object.entries(m);
      if (entries.length === 0) {
        container.innerHTML = '<metric-card label="' + window.t("dashboard.metrics.status_label", "Status") + '" value="' + window.t("dashboard.metrics.status_ok", "OK") + '" color="#4ade80"></metric-card>';
        return;
      }
      for (const [key, val] of entries.slice(0, 8)) {
        const mc = document.createElement("metric-card");
        mc.setAttribute("label", key.replace(/_/g, " "));
        mc.setAttribute("value", typeof val === "number" ? val.toFixed(2) : String(val));
        container.appendChild(mc);
      }
    } catch (err) {
      console.error("[dashboard] metrics error", err);
    }
  }

  async function loadBudget() {
    const container = document.getElementById("budget-cards");
    if (!container) return;
    try {
      const resp = await fetch("/api/budget");
      const data = await resp.json();
      const session = data.session || {};
      container.innerHTML = `
        <metric-card label="${window.t("dashboard.budget.session_spent", "Session Spent")}" value="${(session.spent_usd || 0).toFixed(4)}" unit="USD"
                     color="var(--color-warning, #facc15)"></metric-card>
        <metric-card label="${window.t("dashboard.budget.session_tokens", "Session Tokens")}" value="${session.tokens_used || 0}"
                     color="var(--accent-primary, #6366f1)"></metric-card>
        <metric-card label="${window.t("dashboard.budget.session_requests", "Session Requests")}" value="${session.requests_count || 0}"
                     color="var(--color-info, #60a5fa)"></metric-card>
      `;
    } catch (err) {
      console.error("[dashboard] budget error", err);
    }
  }

  // -------------------------------------------------------------------
  // Per-task cost breakdown (P12)
  // -------------------------------------------------------------------
  async function loadTaskCosts() {
    const container = document.getElementById("task-costs-breakdown");
    if (!container) return;
    try {
      const resp = await fetch("/api/budget/tasks");
      const data = await resp.json();
      const tasks = data.tasks || {};
      const entries = Object.entries(tasks);
      if (!entries.length) {
        container.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.costs.no_data", "No per-task cost data yet.") + '</div>';
        return;
      }
      let rows = entries.map(function ([tid, t]) {
        return '<tr><td>' + esc(tid) + '</td><td>' + (t.tokens_used || 0) +
          '</td><td>' + (t.requests_count || 0) +
          '</td><td>$' + (t.spent_usd || 0).toFixed(4) + '</td></tr>';
      }).join("");
      container.innerHTML = '<table class="glass-table glass-table--compact">' +
        '<thead><tr><th>' + window.t("dashboard.costs.th_task", "Task") + '</th><th>' + window.t("dashboard.costs.th_tokens", "Tokens") + '</th><th>' + window.t("dashboard.costs.th_requests", "Requests") + '</th><th>' + window.t("dashboard.costs.th_cost", "Cost") + '</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table>';
    } catch (_) {}
  }

  // -------------------------------------------------------------------
  // System Health panel (P14)
  // -------------------------------------------------------------------
  async function loadHealthDiagnostics() {
    const container = document.getElementById("health-cards");
    if (!container) return;
    try {
      const resp = await fetch("/health/detailed");
      const d = await resp.json();
      let cards = '';
      // Uptime
      if (d.uptime_seconds != null) {
        const mins = Math.floor(d.uptime_seconds / 60);
        const hrs = Math.floor(mins / 60);
        const display = hrs > 0 ? hrs + 'h ' + (mins % 60) + 'm' : mins + 'm';
        cards += '<metric-card label="' + window.t("dashboard.health.uptime", "Uptime") + '" value="' + display + '" color="var(--color-success, #4ade80)"></metric-card>';
      }
      // RAM
      if (d.ram_rss_mb != null) {
        cards += '<metric-card label="' + window.t("dashboard.health.ram_rss", "RAM RSS") + '" value="' + d.ram_rss_mb + '" unit="MB" color="var(--accent-primary, #6366f1)"></metric-card>';
      }
      // CPU
      if (d.cpu_percent != null) {
        cards += '<metric-card label="' + window.t("dashboard.health.cpu", "CPU") + '" value="' + d.cpu_percent + '" unit="%" color="var(--color-info, #60a5fa)"></metric-card>';
      }
      // DB Pool
      if (d.db_pool) {
        cards += '<metric-card label="' + window.t("dashboard.health.db_pool", "DB Pool") + '" value="' + d.db_pool.free + '/' + d.db_pool.size + '" unit="' + window.t("dashboard.health.unit_free", "free") + '" color="var(--color-success, #4ade80)"></metric-card>';
      }
      // Ollama
      if (d.ollama) {
        const olColor = d.ollama.available ? 'var(--color-success, #4ade80)' : 'var(--color-danger, #f87171)';
        cards += '<metric-card label="' + window.t("dashboard.health.ollama", "Ollama") + '" value="' + (d.ollama.available ? window.t("dashboard.health.online", "online") : window.t("dashboard.health.offline", "offline")) + '" color="' + olColor + '"></metric-card>';
      }
      // Disk
      if (d.disk) {
        cards += '<metric-card label="' + window.t("dashboard.health.disk_used", "Disk Used") + '" value="' + d.disk.used_pct + '" unit="%" color="var(--color-warning, #facc15)"></metric-card>';
      }
      // Agents
      if (d.agents) {
        cards += '<metric-card label="' + window.t("dashboard.health.agents", "Agents") + '" value="' + d.agents.total + '" color="var(--accent-primary, #6366f1)"></metric-card>';
      }
      container.innerHTML = cards || '<div class="sidebar-empty">' + window.t("dashboard.health.no_data", "No health data.") + '</div>';
    } catch (err) {
      container.innerHTML = '<div class="sidebar-empty">' + window.t("dashboard.health.unavailable", "Health check unavailable.") + '</div>';
    }
  }

  // -------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------
  function esc(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  // -------------------------------------------------------------------
  // Initialisation
  // -------------------------------------------------------------------
  function init() {
    buildPanelSelector(document.getElementById("panel-selector"));
    applyPanelVisibility();

    connectGlobalWS();
    loadAgentSidebar();
    loadAgentsOverview();
    loadTasks();
    loadMetrics();
    loadBudget();
    loadTaskCosts();
    loadHealthDiagnostics();

    // Periodic refresh (30s)
    setInterval(() => {
      loadAgentSidebar();
      loadAgentsOverview();
      loadTasks();
      loadMetrics();
      loadBudget();
      loadTaskCosts();
      loadHealthDiagnostics();
    }, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
