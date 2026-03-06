(function () {
  "use strict";

  /* ── DOM refs ──────────────────────────────────────────────── */
  const refreshBtn     = document.getElementById("btn-refresh-budget");
  const spentEl        = document.getElementById("budget-total-spent");
  const barEl          = document.getElementById("budget-bar-session");
  const limitTextEl    = document.getElementById("budget-limit-text");
  const tokensEl       = document.getElementById("budget-total-tokens");
  const requestsEl     = document.getElementById("budget-total-requests");
  const utilEl         = document.getElementById("budget-utilization");
  const agentGrid      = document.getElementById("budget-agent-grid");
  const taskGrid       = document.getElementById("budget-task-grid");
  const taskEmpty      = document.getElementById("budget-task-empty");
  const quotasForm     = document.getElementById("budget-quotas-form");
  const qLimitInput    = document.getElementById("quota-session-limit");
  const qWarnInput     = document.getElementById("quota-warning-pct");
  const qBlockedInput  = document.getElementById("quota-blocked-pct");
  const quotaStatus    = document.getElementById("quota-status");
  const resetSessionBtn = document.getElementById("btn-reset-session");
  // Chart
  const chartCanvas    = document.getElementById("budget-history-chart");
  const chartEmpty     = document.getElementById("budget-chart-empty");

  /* ── Helpers ───────────────────────────────────────────────── */
  function esc(s) {
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }
  function fmtCost(v) { return "$" + Number(v || 0).toFixed(4); }
  function fmtNum(v)  { return Number(v || 0).toLocaleString(); }
  function pct(a, b)  { return b > 0 ? Math.min(100, (a / b) * 100) : 0; }

  /* ── Data fetching ─────────────────────────────────────────── */
  async function fetchBudget() {
    const r = await fetch("/api/budget");
    return r.ok ? r.json() : null;
  }

  async function fetchTasks() {
    const r = await fetch("/api/budget/tasks");
    return r.ok ? r.json() : null;
  }

  async function fetchHistory() {
    try {
      const r = await fetch("/api/budget/history");
      return r.ok ? r.json() : null;
    } catch { return null; }
  }

  /* ── Render ────────────────────────────────────────────────── */
  function renderOverview(data) {
    if (!data) return;
    const session = data.session || {};
    const spent   = session.total_cost || 0;
    const limit   = session.session_limit || session.limit || 50;
    const tokens  = session.total_tokens || 0;
    const reqs    = session.total_requests || 0;
    const usage   = pct(spent, limit);

    spentEl.textContent    = fmtCost(spent);
    limitTextEl.textContent = fmtCost(spent) + " / " + fmtCost(limit);
    tokensEl.textContent   = fmtNum(tokens);
    requestsEl.textContent = fmtNum(reqs);
    utilEl.textContent     = usage.toFixed(1) + "%";

    if (barEl) {
      barEl.style.width = Math.min(usage, 100) + "%";
      barEl.classList.toggle("--warning", usage >= 70 && usage < 90);
      barEl.classList.toggle("--danger", usage >= 90);
    }

    // Populate quotas form defaults
    if (qLimitInput && !qLimitInput.dataset.touched) qLimitInput.value = limit;
    if (qWarnInput && !qWarnInput.dataset.touched) qWarnInput.value = session.warning_pct || 70;
    if (qBlockedInput && !qBlockedInput.dataset.touched) qBlockedInput.value = session.blocked_pct || 100;
  }

  function renderAgents(data) {
    if (!agentGrid) return;
    agentGrid.innerHTML = "";
    const agents = data && data.agents ? data.agents : [];
    if (agents.length === 0) {
      agentGrid.innerHTML = '<p class="budget-empty">No agent costs yet.</p>';
      return;
    }
    for (const a of agents) {
      var agentId = a.agent_id || a.id || "unknown";
      var limitVal = a.limit_usd || a.limit || 5;
      var spentVal = a.total_cost || a.cost || 0;
      var usage = pct(spentVal, limitVal);
      const card = document.createElement("div");
      card.className = "glass-card budget-agent-card";
      card.innerHTML =
        '<div class="budget-agent-header">' +
          '<span class="budget-agent-name">' + esc(agentId) + '</span>' +
          '<button class="glass-btn glass-btn--sm glass-btn--ghost js-reset-agent" data-agent="' + esc(agentId) + '" title="Reset">&#x21bb;</button>' +
        '</div>' +
        '<div class="budget-agent-meta">' +
          "<span>" + fmtCost(spentVal) + "</span>" +
          "<span>" + fmtNum(a.total_tokens || a.tokens) + " tok</span>" +
          "<span>" + fmtNum(a.requests || 0) + " req</span>" +
        "</div>" +
        '<div class="budget-agent-bar-wrap"><div class="budget-agent-bar" style="width:' + Math.min(usage, 100) + '%"></div></div>' +
        '<div class="budget-agent-limit">' +
          '<label>Limit $</label>' +
          '<input type="number" step="0.1" min="0" class="budget-agent-limit-input js-agent-limit" ' +
            'data-agent="' + esc(agentId) + '" value="' + limitVal + '" />' +
        '</div>';
      agentGrid.appendChild(card);
    }
  }

  function renderTasks(tasks) {
    if (!taskGrid) return;
    taskGrid.innerHTML = "";
    const items = tasks && tasks.tasks ? tasks.tasks : (Array.isArray(tasks) ? tasks : []);
    if (items.length === 0) {
      if (taskEmpty) taskEmpty.style.display = "";
      return;
    }
    if (taskEmpty) taskEmpty.style.display = "none";
    for (const t of items) {
      const card = document.createElement("div");
      card.className = "glass-card budget-task-card";
      card.innerHTML =
        '<div class="budget-task-name">' + esc(t.task_id || t.id || "—") + "</div>" +
        '<div class="budget-task-meta">' +
          "<span>" + fmtCost(t.total_cost || t.cost) + "</span>" +
          "<span>" + fmtNum(t.total_tokens || t.tokens) + " tok</span>" +
        "</div>";
      taskGrid.appendChild(card);
    }
  }

  /* ── History Chart (lightweight Canvas) ──────────────────── */
  var historyData = [];

  function renderChart(data) {
    if (!chartCanvas) return;
    var ctx = chartCanvas.getContext("2d");
    if (!ctx) return;

    // Collect agent-level data for bar chart
    var agents = data && data.agents ? data.agents : [];
    if (agents.length === 0) {
      if (chartEmpty) chartEmpty.style.display = "";
      chartCanvas.style.display = "none";
      return;
    }
    if (chartEmpty) chartEmpty.style.display = "none";
    chartCanvas.style.display = "";

    var W = chartCanvas.width = chartCanvas.parentElement.clientWidth || 800;
    var H = chartCanvas.height = 200;
    var pad = { top: 20, right: 20, bottom: 40, left: 60 };
    var plotW = W - pad.left - pad.right;
    var plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    // Find max cost for scale
    var maxCost = 0;
    for (var i = 0; i < agents.length; i++) {
      var c = agents[i].spent_usd || 0;
      if (c > maxCost) maxCost = c;
    }
    if (maxCost === 0) maxCost = 1;

    var barW = Math.max(20, Math.min(60, Math.floor(plotW / agents.length) - 8));
    var gap = (plotW - barW * agents.length) / (agents.length + 1);

    // Grid lines
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++) {
      var y = pad.top + plotH - (plotH * g / 4);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();

      // Y-axis labels
      ctx.fillStyle = "rgba(148,163,184,0.7)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText("$" + (maxCost * g / 4).toFixed(2), pad.left - 6, y + 3);
    }

    // Bars
    var colors = ["#60a5fa", "#a78bfa", "#34d399", "#f97316", "#f472b6", "#facc15"];
    for (var j = 0; j < agents.length; j++) {
      var a = agents[j];
      var cost = a.spent_usd || 0;
      var barH = (cost / maxCost) * plotH;
      var x = pad.left + gap + j * (barW + gap);
      var bY = pad.top + plotH - barH;

      ctx.fillStyle = colors[j % colors.length];
      ctx.beginPath();
      ctx.roundRect(x, bY, barW, barH, 3);
      ctx.fill();

      // Agent label
      ctx.fillStyle = "rgba(148,163,184,0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      var label = (a.agent_id || "?").slice(0, 10);
      ctx.fillText(label, x + barW / 2, H - pad.bottom + 14);

      // Cost on top of bar
      ctx.fillStyle = "rgba(226,232,240,0.9)";
      ctx.font = "bold 10px system-ui, sans-serif";
      ctx.fillText("$" + cost.toFixed(3), x + barW / 2, bY - 4);
    }
  }

  /* ── Actions ───────────────────────────────────────────────── */
  async function saveQuotas(e) {
    e.preventDefault();
    quotaStatus.textContent = "Saving…";
    quotaStatus.className = "budget-quote-status";
    try {
      const body = {
        session_limit_usd: parseFloat(qLimitInput.value) || 50,
        warning_pct: parseInt(qWarnInput.value, 10) || 70,
        blocked_pct: parseInt(qBlockedInput ? qBlockedInput.value : "100", 10) || 100,
      };
      const r = await fetch("/api/budget/quotas", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.ok) {
        quotaStatus.textContent = "Saved";
        quotaStatus.className = "budget-quote-status --ok";
        load();
      } else {
        const d = await r.json();
        quotaStatus.textContent = d.error || "Save failed";
        quotaStatus.className = "budget-quote-status --err";
      }
    } catch (e2) {
      quotaStatus.textContent = "Error: " + e2.message;
      quotaStatus.className = "budget-quote-status --err";
    }
  }

  async function resetSession() {
    if (!confirm("Reset entire session budget counters?")) return;
    try {
      var r = await fetch("/api/budget/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "session" }),
      });
      if (r.ok) load();
      else alert("Reset failed");
    } catch (e) { alert("Error: " + e.message); }
  }

  async function resetAgent(agentId) {
    if (!confirm("Reset budget for " + agentId + "?")) return;
    try {
      var r = await fetch("/api/budget/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agentId }),
      });
      if (r.ok) load();
      else alert("Reset failed");
    } catch (e) { alert("Error: " + e.message); }
  }

  async function saveAgentLimit(agentId, limitVal) {
    try {
      var agents = {};
      agents[agentId] = { limit_usd: parseFloat(limitVal) || 5 };
      var r = await fetch("/api/budget/quotas", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agents: agents }),
      });
      if (!r.ok) alert("Save agent limit failed");
    } catch (e) { alert("Error: " + e.message); }
  }

  /* ── Events ────────────────────────────────────────────────── */
  if (refreshBtn) refreshBtn.addEventListener("click", load);
  if (quotasForm) quotasForm.addEventListener("submit", saveQuotas);
  if (resetSessionBtn) resetSessionBtn.addEventListener("click", resetSession);
  if (qLimitInput) qLimitInput.addEventListener("input", function () { this.dataset.touched = "1"; });
  if (qWarnInput)  qWarnInput.addEventListener("input", function () { this.dataset.touched = "1"; });
  if (qBlockedInput) qBlockedInput.addEventListener("input", function () { this.dataset.touched = "1"; });

  // Per-agent reset buttons (delegated on grid)
  if (agentGrid) {
    agentGrid.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-reset-agent");
      if (btn) resetAgent(btn.dataset.agent);
    });
    // Per-agent limit change (debounced save on blur)
    agentGrid.addEventListener("change", function (e) {
      var input = e.target.closest(".js-agent-limit");
      if (input) saveAgentLimit(input.dataset.agent, input.value);
    });
  }

  /* ── Load ──────────────────────────────────────────────────── */
  async function load() {
    const [budget, tasks, history] = await Promise.all([fetchBudget(), fetchTasks(), fetchHistory()]);
    renderOverview(budget);
    renderAgents(budget);
    renderTasks(tasks);
    renderChart(history || budget);
  }

  load();
  setInterval(load, 15000);
})();
