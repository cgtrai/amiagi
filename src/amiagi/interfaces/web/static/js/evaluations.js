/**
 * Evaluations dashboard — client-side logic.
 * Manages evaluation runs, A/B tests, baselines, suites.
 */
(function () {
  "use strict";

  // ── Tab switching ────────────────────────────────────────
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".eval-tab").forEach((t) => (t.hidden = true));
      document.getElementById(`tab-${tab}`).hidden = false;
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadTab(tab);
    });
  });

  function loadTab(tab) {
    switch (tab) {
      case "dashboard": loadDashboard(); break;
      case "history": loadHistory(); break;
      case "ab-tests": loadABTests(); break;
      case "baselines": loadBaselines(); break;
      case "suites": loadSuites(); break;
    }
  }

  function esc(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatDateTime(ts) {
    if (!ts) return "—";
    const date = new Date(ts * 1000);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
  }

  async function responseErrorMessage(res, fallback) {
    try {
      const data = await res.json();
      return data.detail || data.error || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function notify(message, level) {
    if (typeof showToast === "function") {
      showToast(message, level || "info");
      return;
    }
    if (message && typeof window !== "undefined" && typeof window["alert"] === "function") {
      window["alert"](message);
    }
  }

  function renderScenarioDetail(scenario) {
    return `
      <div class="glass-card" style="margin-top:1rem;padding:1rem">
        <div style="font-weight:600;margin-bottom:.35rem">${esc(scenario.scenario_name || scenario.scenario_id || "—")}</div>
        <div class="text-muted" style="margin-bottom:.5rem">Score: ${Number(scenario.aggregate || 0).toFixed(1)}%</div>
        <pre style="margin:0;white-space:pre-wrap;overflow-wrap:anywhere"><code>${esc(JSON.stringify({
          scores: scenario.scores || {},
          notes: scenario.notes || [],
        }, null, 2))}</code></pre>
      </div>`;
  }

  // ── Dashboard ────────────────────────────────────────────
  async function loadDashboard() {
    try {
      const [evalRes, abRes, regRes] = await Promise.all([
        fetch("/api/evaluations?limit=5"),
        fetch("/api/evaluations/ab-tests"),
        fetch("/api/evaluations/regressions"),
      ]);
      const evalData = await evalRes.json();
      const abData = await abRes.json();
      const regData = await regRes.json();

      // Last eval card
      const runs = evalData.runs || [];
      if (runs.length > 0) {
        const last = runs[0];
        document.getElementById("last-eval-score").textContent = `${(last.aggregate_score || 0).toFixed(1)}%`;
        document.getElementById("last-eval-agent").textContent = last.agent_id || "—";
      }

      // Active A/B
      const activeCampaigns = (abData.campaigns || []).filter((c) => c.status === "running" || c.status === "pending");
      document.getElementById("active-ab-count").textContent = activeCampaigns.length;

      // Regressions
      const regressions = (regData.regressions || []).filter((r) => r.regressed);
      document.getElementById("regressions-count").textContent = regressions.length;
      document.getElementById("regressions-count").className =
        "eval-metric" + (regressions.length > 0 ? " text-danger" : "");

      // EV4 — extended summary tiles
      document.getElementById("total-runs-count").textContent = runs.length;
      if (runs.length > 0) {
        const totalPassed = runs.reduce((s, r) => s + (r.passed || 0), 0);
        const totalScenarios = runs.reduce((s, r) => s + (r.scenarios_count || 0), 0);
        const passRate = totalScenarios > 0 ? ((totalPassed / totalScenarios) * 100).toFixed(1) + "%" : "—";
        document.getElementById("eval-pass-rate").textContent = passRate;

        const avgScore = (runs.reduce((s, r) => s + (r.aggregate_score || 0), 0) / runs.length).toFixed(1) + "%";
        document.getElementById("eval-avg-score").textContent = avgScore;

        const lastDate = runs[0].started_at ? new Date(runs[0].started_at * 1000).toLocaleDateString() : "—";
        document.getElementById("eval-last-run-date").textContent = "Last: " + lastDate;
      }

      // Recent results table
      const tbody = document.getElementById("recent-results-body");
      if (runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No evaluation results yet.</td></tr>';
      } else {
        tbody.innerHTML = runs.map(renderResultRow).join("");
      }
    } catch (err) {
      console.error("Dashboard load failed:", err);
    }
  }

  function renderResultRow(run) {
    const date = run.started_at ? new Date(run.started_at * 1000).toLocaleTimeString() : "—";
    const score = (run.aggregate_score || 0).toFixed(1) + "%";
    const pf = `${run.passed || 0}/${run.scenarios_count || 0}`;
    const delta = run.baseline_score != null
      ? (run.aggregate_score || 0) - run.baseline_score
      : 0;
    const deltaHtml = run.baseline_score != null
      ? `<regression-badge delta="${delta.toFixed(1)}" threshold="5"></regression-badge>`
      : "—";
    return `<tr>
      <td>${date}</td>
      <td>${run.agent_id || "—"}</td>
      <td>${run.suite || "—"}</td>
      <td><strong>${score}</strong></td>
      <td>${pf}</td>
      <td>${deltaHtml}</td>
    </tr>`;
  }

  // ── History ──────────────────────────────────────────────
  async function loadHistory() {
    try {
      const res = await fetch("/api/evaluations?limit=100");
      const data = await res.json();
      const tbody = document.getElementById("history-body");
      const runs = data.runs || [];

      if (runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No history.</td></tr>';
        return;
      }

      tbody.innerHTML = runs.map((run) => {
        const date = run.started_at ? new Date(run.started_at * 1000).toLocaleString() : "—";
        const score = (run.aggregate_score || 0).toFixed(1) + "%";
        const pf = `${run.passed || 0}/${run.scenarios_count || 0}`;
        return `<tr>
          <td>${date}</td>
          <td>${run.agent_id || "—"}</td>
          <td>${run.suite || "—"}</td>
          <td><strong>${score}</strong></td>
          <td>${pf}</td>
          <td>${run.label || "—"}</td>
          <td><button class="btn btn-sm" data-run-id="${run.id}" data-action="detail">👁</button></td>
        </tr>`;
      }).join("");

      tbody.querySelectorAll("[data-action='detail']").forEach((btn) => {
        btn.addEventListener("click", () => openRunDetail(btn.dataset.runId));
      });
    } catch (err) {
      console.error("History load failed:", err);
    }
  }

  async function openRunDetail(runId) {
    const dialog = document.getElementById("eval-detail-dialog");
    const meta = document.getElementById("eval-detail-meta");
    const body = document.getElementById("eval-detail-body");
    const title = document.getElementById("eval-detail-title");
    if (!dialog || !meta || !body || !title) return;

    title.textContent = "{{ _('eval.run_details') }}";
    meta.textContent = "{{ _('common.loading') }}";
    body.innerHTML = "{{ _('common.loading') }}";
    dialog.showModal();

    try {
      const res = await fetch(`/api/evaluations/${encodeURIComponent(runId)}`);
      const data = await res.json();
      const run = data.run || {};
      const scenarios = run.results || [];
      title.textContent = `${run.label || run.suite || run.id || runId}`;
      meta.textContent = `${run.agent_id || '—'} · ${formatDateTime(run.started_at)} · ${(run.aggregate_score || 0).toFixed(1)}%`;

      if (!scenarios.length) {
        body.innerHTML = '<p class="empty-state">{{ _("eval.no_details") }}</p>';
        return;
      }

      body.innerHTML = `
        <eval-chart id="eval-detail-chart"></eval-chart>
        <div id="eval-scenario-drilldown"></div>
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ _('eval.scenarios') }}</th>
              <th>{{ _('eval.col_score') }}</th>
              <th>{{ _('eval.col_pass_fail') }}</th>
              <th>{{ _('admin.details') }}</th>
            </tr>
          </thead>
          <tbody>
            ${scenarios.map((scenario) => `
              <tr>
                <td>${esc(scenario.scenario_name || scenario.scenario_id || '—')}</td>
                <td>${Number(scenario.aggregate || 0).toFixed(1)}%</td>
                <td>${scenario.passed ? '✓' : '✗'}</td>
                <td>${esc((scenario.notes || []).join('; ') || JSON.stringify(scenario.scores || {}))}</td>
              </tr>`).join('')}
          </tbody>
        </table>
        ${run.config ? `<details style="margin-top:1rem"><summary style="cursor:pointer;font-weight:600">⚙️ Config</summary><pre class="glass-card" style="margin-top:.5rem;font-size:.8rem;overflow-x:auto"><code>${esc(JSON.stringify(run.config, null, 2))}</code></pre></details>` : ''}`;

      const chart = document.getElementById("eval-detail-chart");
      const drilldown = document.getElementById("eval-scenario-drilldown");
      if (chart && typeof chart.setData === "function") {
        chart.setData(scenarios.map((scenario) => ({
          scenario: scenario.scenario_name || scenario.scenario_id || "—",
          score: Number(scenario.aggregate || 0),
          max: 100,
          run_id: run.id || runId,
          item: scenario,
        })));
        chart.addEventListener("point-click", (event) => {
          const point = event.detail || {};
          const selectedScenario = scenarios[point.index] || point.item || scenarios[0];
          if (!drilldown || !selectedScenario) return;
          drilldown.innerHTML = renderScenarioDetail(selectedScenario);
        });
        if (drilldown && scenarios[0]) {
          drilldown.innerHTML = renderScenarioDetail(scenarios[0]);
        }
      }
    } catch (err) {
      body.innerHTML = `<p class="error-state">${esc(err.message)}</p>`;
      meta.textContent = "";
    }
  }

  // ── A/B Tests ────────────────────────────────────────────
  async function loadABTests() {
    const el = document.getElementById("ab-campaigns-list");
    try {
      const res = await fetch("/api/evaluations/ab-tests");
      const data = await res.json();
      const campaigns = data.campaigns || [];

      if (campaigns.length === 0) {
        el.innerHTML = '<p class="empty-state">No A/B campaigns yet.</p>';
        return;
      }

      el.innerHTML = campaigns.map(renderABCard).join("");

      // Wire action buttons
      el.querySelectorAll("[data-ab-action]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.campaignId;
          const action = btn.dataset.abAction;
          await fetch(`/api/evaluations/ab-tests/${id}/${action}`, { method: "PUT" });
          loadABTests();
        });
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderABCard(campaign) {
    const statusIcon = campaign.status === "running" ? "▶" :
                       campaign.status === "paused" ? "⏸" :
                       campaign.status === "completed" ? "✅" : "○";

    return `<div class="glass-card ab-card">
      <div class="ab-header">
        <strong>${campaign.label || "A/B Test"}</strong>
        <span class="glass-pill">${statusIcon} ${campaign.status}</span>
      </div>
      <ab-comparison
        agent-a="${campaign.agent_a_id}" agent-b="${campaign.agent_b_id}"
        score-a="${campaign.a_aggregate}" score-b="${campaign.b_aggregate}"
        wins-a="${campaign.a_wins}" wins-b="${campaign.b_wins}"
        ties="${campaign.ties}" delta="${campaign.score_delta}">
      </ab-comparison>
      <div class="ab-actions">
        ${campaign.status !== "completed" ? `
          <button class="btn btn-sm btn-outline" data-campaign-id="${campaign.id}" data-ab-action="pause">⏸ Pause</button>
          <button class="btn btn-sm btn-danger" data-campaign-id="${campaign.id}" data-ab-action="stop">⏹ Stop</button>
        ` : ""}
      </div>
    </div>`;
  }

  // ── Baselines ────────────────────────────────────────────
  async function loadBaselines() {
    try {
      const res = await fetch("/api/evaluations/baselines");
      const data = await res.json();
      const tbody = document.getElementById("baselines-body");
      const baselines = data.baselines || [];

      if (baselines.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No baselines saved.</td></tr>';
        return;
      }

      tbody.innerHTML = baselines.map((bl) => `<tr>
        <td>${bl.agent_id}</td>
        <td>${(bl.aggregate_score || 0).toFixed(1)}%</td>
        <td>${bl.scenarios_count || 0}</td>
        <td>${bl.passed || 0}/${bl.scenarios_count || 0}</td>
        <td><button class="btn btn-sm btn-danger" data-action="delete-bl" data-name="${bl.agent_id}">🗑</button></td>
      </tr>`).join("");

      tbody.querySelectorAll("[data-action='delete-bl']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await fetch(`/api/evaluations/baselines/${btn.dataset.name}`, { method: "DELETE" });
          loadBaselines();
        });
      });
    } catch (err) {
      console.error("Baselines load failed:", err);
    }
  }

  // ── Suites ───────────────────────────────────────────────
  async function loadSuites() {
    const el = document.getElementById("suites-list");
    try {
      const res = await fetch("/api/evaluations/suites");
      const data = await res.json();
      const suites = data.suites || [];

      if (suites.length === 0) {
        el.innerHTML = '<p class="empty-state">No benchmark suites found.</p>';
        return;
      }

      el.innerHTML = suites.map((s) =>
        `<div class="glass-card suite-card">
          <strong>${s.name}</strong>
          <div>${s.scenarios_count} scenarios</div>
        </div>`
      ).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  // ── New Eval dialog ──────────────────────────────────────
  const newEvalBtn = document.getElementById("btn-new-eval");
  const newEvalDialog = document.getElementById("new-eval-dialog");

  newEvalBtn?.addEventListener("click", async () => {
    await populateAgentSelects();
    await populateSuiteSelects();
    newEvalDialog?.showModal();
  });

  // EV1 — rubric dropdown: show/hide custom textarea
  document.getElementById("eval-rubric")?.addEventListener("change", (e) => {
    const wrap = document.getElementById("custom-rubric-wrap");
    if (wrap) wrap.style.display = e.target.value === "custom" ? "" : "none";
  });

  document.getElementById("new-eval-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const rubricVal = form.elements.rubric?.value || "default";
    const body = {
      agent_id: form.elements.agent_id.value,
      suite: form.elements.suite.value,
      scorer: form.elements.scorer.value,
      label: form.elements.label.value,
      rubric: rubricVal,
    };
    if (rubricVal === "custom") {
      body.custom_rubric = form.elements.custom_rubric?.value || "";
    }
    try {
      const res = await fetch("/api/evaluations/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        newEvalDialog.close();
        notify("Evaluation started", "success");
        loadDashboard();
      } else {
        notify(await responseErrorMessage(res, "Failed to start evaluation"), "error");
      }
    } catch (err) {
      notify("Failed to start evaluation", "error");
    }
  });

  // ── New A/B dialog ───────────────────────────────────────
  const newABBtn = document.getElementById("btn-new-ab");
  const newABDialog = document.getElementById("new-ab-dialog");

  newABBtn?.addEventListener("click", async () => {
    await populateAgentSelects();
    await populateSuiteSelects();
    newABDialog?.showModal();
  });

  document.getElementById("new-ab-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      agent_a_id: form.elements.agent_a_id.value,
      agent_b_id: form.elements.agent_b_id.value,
      suite: form.elements.suite.value,
      label: form.elements.label.value,
    };
    try {
      const res = await fetch("/api/evaluations/ab-tests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        newABDialog.close();
        notify("A/B test started", "success");
        loadABTests();
      } else {
        notify(await responseErrorMessage(res, "Failed to start A/B test"), "error");
      }
    } catch (err) {
      notify("Failed to start A/B test", "error");
    }
  });

  // ── Populate selects ─────────────────────────────────────
  async function populateAgentSelects() {
    try {
      const res = await fetch("/api/agents");
      const data = await res.json();
      const agents = data.agents || [];
      const options = agents.map((a) => `<option value="${a.id || a.name}">${a.name || a.id}</option>`).join("");
      document.querySelectorAll("#eval-agent-select, #ab-agent-a-select, #ab-agent-b-select").forEach((sel) => {
        sel.innerHTML = options || '<option value="">No agents available</option>';
      });
    } catch (err) {
      console.error("Failed to load agents:", err);
    }
  }

  async function populateSuiteSelects() {
    try {
      const res = await fetch("/api/evaluations/suites");
      const data = await res.json();
      const suites = data.suites || [];
      const options = suites.map((s) => `<option value="${s.name}">${s.name} (${s.scenarios_count})</option>`).join("");
      document.querySelectorAll("#eval-suite-select, #ab-suite-select").forEach((sel) => {
        sel.innerHTML = options || '<option value="">No suites available</option>';
      });
    } catch (err) {
      console.error("Failed to load suites:", err);
    }
  }

  // ── Init ─────────────────────────────────────────────────
  loadDashboard();
})();
