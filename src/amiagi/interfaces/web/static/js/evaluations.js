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
    return `<tr>
      <td>${date}</td>
      <td>${run.agent_id || "—"}</td>
      <td>${run.suite || "—"}</td>
      <td><strong>${score}</strong></td>
      <td>${pf}</td>
      <td>—</td>
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
    } catch (err) {
      console.error("History load failed:", err);
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

  document.getElementById("new-eval-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      agent_id: form.elements.agent_id.value,
      suite: form.elements.suite.value,
      scorer: form.elements.scorer.value,
      label: form.elements.label.value,
    };
    try {
      const res = await fetch("/api/evaluations/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        newEvalDialog.close();
        loadDashboard();
      } else {
        const err = await res.json();
        alert(err.error || "Failed to start evaluation");
      }
    } catch (err) {
      alert("Error: " + err.message);
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
        loadABTests();
      }
    } catch (err) {
      alert("Error: " + err.message);
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
