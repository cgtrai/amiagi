/**
 * Dashboard controller — orchestrates panels, WebSocket connection,
 * agent tabs, and debug grid mode.
 */
(function () {
  "use strict";

  // -------------------------------------------------------------------
  // Panel configuration
  // -------------------------------------------------------------------
  const PANELS = [
    { id: "agents-overview", label: "Agents Overview", default: true },
    { id: "task-board",      label: "Task Board",      default: true },
    { id: "metrics",         label: "Metrics",          default: true },
    { id: "event-log",       label: "Event Log",        default: true },
    { id: "costs",           label: "Costs",            default: false },
    { id: "system-health",   label: "System Health",    default: true },
  ];

  const STORAGE_KEY = "amiagi_dashboard_panels";

  // -------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------
  let ws = null;
  let agentTabs = {};       // agent_id → { ws, tabEl, panelEl }
  let debugMode = false;

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
      indicator.textContent = "Reconnecting…";
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
      const resp = await fetch("/api/agents");
      const data = await resp.json();
      if (!data.agents || data.agents.length === 0) {
        list.innerHTML = '<div class="sidebar-empty">No agents registered</div>';
        return;
      }
      list.innerHTML = data.agents.map(a => `
        <div class="agent-sidebar-item" data-agent-id="${a.agent_id}" role="button" tabindex="0">
          <agent-card name="${esc(a.name)}"
                      role="${esc(a.role)}"
                      state="${esc(a.state)}"
                      model="${esc(a.model_name)}"
                      agent-id="${esc(a.agent_id)}">
          </agent-card>
        </div>
      `).join("");

      // Click → open agent tab
      list.querySelectorAll(".agent-sidebar-item").forEach(el => {
        el.addEventListener("click", () => openAgentTab(el.dataset.agentId));
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") openAgentTab(el.dataset.agentId);
        });
      });
    } catch (err) {
      list.innerHTML = '<div class="sidebar-empty">Failed to load agents</div>';
      console.error("[dashboard] agent list error", err);
    }
  }

  // -------------------------------------------------------------------
  // Agent tabs
  // -------------------------------------------------------------------
  function openAgentTab(agentId) {
    if (agentTabs[agentId]) {
      activateTab(agentId);
      return;
    }

    const tabBar = document.getElementById("agent-tab-bar");
    const tabContent = document.getElementById("agent-tab-content");
    if (!tabBar || !tabContent) return;

    // Create tab button
    const tabBtn = document.createElement("button");
    tabBtn.className = "glass-tab agent-tab";
    tabBtn.dataset.agentId = agentId;
    tabBtn.innerHTML = `<span>${esc(agentId)}</span><span class="tab-close" title="Close">&times;</span>`;
    tabBtn.querySelector(".tab-close").addEventListener("click", (e) => {
      e.stopPropagation();
      closeAgentTab(agentId);
    });
    tabBtn.addEventListener("click", () => activateTab(agentId));
    tabBar.appendChild(tabBtn);

    // Create tab panel
    const panel = document.createElement("div");
    panel.className = "agent-tab-panel";
    panel.dataset.agentId = agentId;
    panel.innerHTML = `
      <div class="agent-detail-split">
        <div class="agent-chat-area">
          <chat-stream agent-id="${esc(agentId)}"></chat-stream>
          <div class="chat-input-row">
            <input type="text" class="glass-input agent-prompt-input"
                   placeholder="Send a message to ${esc(agentId)}…"
                   data-agent-id="${esc(agentId)}">
            <button class="glass-btn glass-btn--primary send-btn">Send</button>
          </div>
        </div>
      </div>
    `;
    tabContent.appendChild(panel);

    // Wire input
    const input = panel.querySelector(".agent-prompt-input");
    const sendBtn = panel.querySelector(".send-btn");
    const chatStream = panel.querySelector("chat-stream");

    const sendPrompt = () => {
      const text = input.value.trim();
      if (!text) return;
      chatStream.addMessage({ role: "user", text });
      const agentWS = agentTabs[agentId]?.ws;
      if (agentWS && agentWS.readyState === WebSocket.OPEN) {
        agentWS.send(JSON.stringify({ type: "user_prompt", message: text }));
      }
      input.value = "";
    };
    sendBtn.addEventListener("click", sendPrompt);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
    });

    // Connect per-agent WebSocket
    const agentWS = connectAgentWS(agentId, chatStream);

    agentTabs[agentId] = { ws: agentWS, tabEl: tabBtn, panelEl: panel };
    activateTab(agentId);
  }

  function activateTab(agentId) {
    Object.values(agentTabs).forEach(t => {
      t.tabEl.classList.remove("active");
      t.panelEl.style.display = "none";
    });
    const tab = agentTabs[agentId];
    if (tab) {
      tab.tabEl.classList.add("active");
      tab.panelEl.style.display = "";
    }
  }

  function closeAgentTab(agentId) {
    const tab = agentTabs[agentId];
    if (!tab) return;
    if (tab.ws) tab.ws.close();
    tab.tabEl.remove();
    tab.panelEl.remove();
    delete agentTabs[agentId];
    // Activate another tab if available
    const remaining = Object.keys(agentTabs);
    if (remaining.length > 0) activateTab(remaining[0]);
  }

  function connectAgentWS(agentId, chatStream) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const token = getWSToken();
    const url = `${proto}//${location.host}/ws/agent/${encodeURIComponent(agentId)}?token=${encodeURIComponent(token)}`;
    const sock = new WebSocket(url);

    sock.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "ping") {
          sock.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (msg.type === "log") {
          chatStream.addMessage({ role: "agent", text: msg.message, timestamp: msg.timestamp });
        } else if (msg.type === "error") {
          chatStream.addMessage({ role: "agent", text: `⚠️ ${msg.message}` });
        }
      } catch (_) { /* ignore */ }
    };
    sock.onclose = (e) => {
      console.log(`[agent-ws] disconnected: ${agentId}`, e.code);
      // Auto-reconnect per-agent WS with backoff
      if (agentTabs[agentId]) {
        const delay = Math.min(2000, _MAX_RECONNECT_DELAY);
        setTimeout(() => {
          if (agentTabs[agentId]) {
            agentTabs[agentId].ws = connectAgentWS(agentId, chatStream);
          }
        }, delay);
      }
    };
    sock.onerror = (e) => console.error(`[agent-ws] error: ${agentId}`, e);
    return sock;
  }

  // -------------------------------------------------------------------
  // Debug grid mode
  // -------------------------------------------------------------------
  function toggleDebugMode() {
    debugMode = !debugMode;
    const content = document.getElementById("agent-tab-content");
    const btn = document.getElementById("btn-debug-grid");
    if (content) {
      content.classList.toggle("debug-grid", debugMode);
    }
    if (btn) {
      btn.textContent = debugMode ? "Exit Debug Grid" : "Open in New Pane";
      btn.classList.toggle("active", debugMode);
    }
    // In debug mode, show all tab panels simultaneously
    if (debugMode) {
      Object.values(agentTabs).forEach(t => { t.panelEl.style.display = ""; });
    } else {
      // Restore single-tab view
      const active = Object.entries(agentTabs).find(([_, t]) => t.tabEl.classList.contains("active"));
      if (active) activateTab(active[0]);
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
        container.innerHTML = '<metric-card label="Status" value="OK" color="#4ade80"></metric-card>';
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
        <metric-card label="Session Spent" value="${(session.spent_usd || 0).toFixed(4)}" unit="USD"
                     color="var(--color-warning, #facc15)"></metric-card>
        <metric-card label="Session Tokens" value="${session.tokens_used || 0}"
                     color="var(--accent-primary, #6366f1)"></metric-card>
        <metric-card label="Session Requests" value="${session.requests_count || 0}"
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
        container.innerHTML = '<div class="sidebar-empty">No per-task cost data yet.</div>';
        return;
      }
      let rows = entries.map(function ([tid, t]) {
        return '<tr><td>' + esc(tid) + '</td><td>' + (t.tokens_used || 0) +
          '</td><td>' + (t.requests_count || 0) +
          '</td><td>$' + (t.spent_usd || 0).toFixed(4) + '</td></tr>';
      }).join("");
      container.innerHTML = '<table class="glass-table glass-table--compact">' +
        '<thead><tr><th>Task</th><th>Tokens</th><th>Requests</th><th>Cost</th></tr></thead>' +
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
        cards += '<metric-card label="Uptime" value="' + display + '" color="var(--color-success, #4ade80)"></metric-card>';
      }
      // RAM
      if (d.ram_rss_mb != null) {
        cards += '<metric-card label="RAM RSS" value="' + d.ram_rss_mb + '" unit="MB" color="var(--accent-primary, #6366f1)"></metric-card>';
      }
      // CPU
      if (d.cpu_percent != null) {
        cards += '<metric-card label="CPU" value="' + d.cpu_percent + '" unit="%" color="var(--color-info, #60a5fa)"></metric-card>';
      }
      // DB Pool
      if (d.db_pool) {
        cards += '<metric-card label="DB Pool" value="' + d.db_pool.free + '/' + d.db_pool.size + '" unit="free" color="var(--color-success, #4ade80)"></metric-card>';
      }
      // Ollama
      if (d.ollama) {
        const olColor = d.ollama.available ? 'var(--color-success, #4ade80)' : 'var(--color-danger, #f87171)';
        cards += '<metric-card label="Ollama" value="' + (d.ollama.available ? 'online' : 'offline') + '" color="' + olColor + '"></metric-card>';
      }
      // Disk
      if (d.disk) {
        cards += '<metric-card label="Disk Used" value="' + d.disk.used_pct + '" unit="%" color="var(--color-warning, #facc15)"></metric-card>';
      }
      // Agents
      if (d.agents) {
        cards += '<metric-card label="Agents" value="' + d.agents.total + '" color="var(--accent-primary, #6366f1)"></metric-card>';
      }
      container.innerHTML = cards || '<div class="sidebar-empty">No health data.</div>';
    } catch (err) {
      container.innerHTML = '<div class="sidebar-empty">Health check unavailable.</div>';
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
    loadTasks();
    loadMetrics();
    loadBudget();
    loadTaskCosts();
    loadHealthDiagnostics();

    // Debug grid toggle
    const debugBtn = document.getElementById("btn-debug-grid");
    if (debugBtn) debugBtn.addEventListener("click", toggleDebugMode);

    // Periodic refresh (30s)
    setInterval(() => {
      loadAgentSidebar();
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
