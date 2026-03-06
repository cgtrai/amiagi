/**
 * Workflow Studio — client-side logic.
 * Manages tabs, CRUD for definitions and runs, DAG rendering.
 */
(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────
  let activeTab = "runs";

  // ── Tab switching ────────────────────────────────────────
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeTab = btn.dataset.tab;
      document.querySelectorAll(".workflow-tab").forEach((t) => (t.hidden = true));
      const target = document.getElementById(`tab-${activeTab}`);
      if (target) target.hidden = false;
      document.querySelectorAll("[data-tab]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      if (activeTab === "runs") loadRuns();
      if (activeTab === "definitions") loadDefinitions();
    });
  });

  // ── Load runs ────────────────────────────────────────────
  async function loadRuns() {
    const el = document.getElementById("runs-list");
    try {
      const res = await fetch("/api/workflow-runs");
      const data = await res.json();
      if (!data.runs || data.runs.length === 0) {
        el.innerHTML = '<p class="empty-state">No active workflow runs.</p>';
        return;
      }
      el.innerHTML = data.runs.map(renderRunCard).join("");
      el.querySelectorAll("[data-run-id]").forEach((card) => {
        card.addEventListener("click", () => openRunDetail(card.dataset.runId));
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error loading runs: ${err.message}</p>`;
    }
  }

  function renderRunCard(run) {
    const statusClass = run.status === "running" ? "status-running" :
                        run.status === "paused" ? "status-paused" :
                        run.status === "completed" ? "status-completed" : "status-failed";
    const nodesHtml = run.nodes.map((n) => {
      const icon = n.status === "completed" ? "✅" :
                   n.status === "running" ? "▶" :
                   n.status === "waiting_approval" ? "🛑" :
                   n.status === "failed" ? "❌" : "○";
      return `<span class="dag-inline-node node-${n.status}" title="${n.label}">${icon} ${n.label}</span>`;
    }).join(" → ");

    return `<div class="glass-card run-card" data-run-id="${run.run_id}">
      <div class="run-card-header">
        <strong>${run.workflow_name}</strong>
        <span class="glass-pill ${statusClass}">${run.status}</span>
      </div>
      <div class="dag-inline">${nodesHtml}</div>
    </div>`;
  }

  // ── Load definitions ─────────────────────────────────────
  async function loadDefinitions() {
    const el = document.getElementById("definitions-list");
    try {
      const res = await fetch("/api/workflows");
      const data = await res.json();
      if (!data.definitions || data.definitions.length === 0) {
        el.innerHTML = '<p class="empty-state">No workflow definitions.</p>';
        return;
      }
      el.innerHTML = data.definitions.map(renderDefinitionCard).join("");
      // Wire buttons
      el.querySelectorAll("[data-action='start']").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          startRun(btn.dataset.defId);
        });
      });
      el.querySelectorAll("[data-action='delete']").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          deleteDefinition(btn.dataset.defId);
        });
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderDefinitionCard(def) {
    return `<div class="glass-card definition-card">
      <div class="def-header"><strong>${def.name}</strong></div>
      <p class="def-desc">${def.description || ""}</p>
      <div class="def-meta">${def.nodes.length} nodes</div>
      <div class="def-actions">
        <button class="btn btn-sm btn-primary" data-action="start" data-def-id="${def.id}">🚀 Start</button>
        <button class="btn btn-sm btn-danger" data-action="delete" data-def-id="${def.id}">🗑</button>
      </div>
    </div>`;
  }

  // ── Run detail ───────────────────────────────────────────
  async function openRunDetail(runId) {
    const panel = document.getElementById("workflow-run-detail");
    try {
      const res = await fetch(`/api/workflow-runs/${runId}`);
      const data = await res.json();
      if (!data.run) return;

      const run = data.run;
      document.getElementById("run-detail-title").textContent = run.workflow_name;
      document.getElementById("run-detail-status").textContent = run.status;
      document.getElementById("run-detail-status").className = `glass-pill status-${run.status}`;
      document.getElementById("run-detail-started").textContent = new Date(run.started_at * 1000).toLocaleString();

      // DAG component
      const dag = document.getElementById("run-dag");
      if (dag && dag.setData) {
        dag.setData(run.nodes);
      }

      // Controls
      const controls = document.getElementById("run-controls");
      controls.querySelectorAll("[data-action]").forEach((btn) => {
        btn.onclick = () => runAction(runId, btn.dataset.action);
      });

      panel.hidden = false;
    } catch (err) {
      console.error("Failed to load run detail:", err);
    }
  }

  document.getElementById("btn-close-run-detail")?.addEventListener("click", () => {
    document.getElementById("workflow-run-detail").hidden = true;
  });

  async function runAction(runId, action) {
    try {
      await fetch(`/api/workflow-runs/${runId}/${action}`, { method: "POST" });
      loadRuns();
      openRunDetail(runId);
    } catch (err) {
      console.error("Run action failed:", err);
    }
  }

  // ── Start run from definition ────────────────────────────
  async function startRun(defId) {
    try {
      const res = await fetch("/api/workflow-runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ definition_id: defId }),
      });
      if (res.ok) {
        activeTab = "runs";
        document.querySelectorAll("[data-tab]").forEach((b) => b.classList.remove("active"));
        document.querySelector('[data-tab="runs"]')?.classList.add("active");
        document.querySelectorAll(".workflow-tab").forEach((t) => (t.hidden = true));
        document.getElementById("tab-runs").hidden = false;
        loadRuns();
      }
    } catch (err) {
      console.error("Start run failed:", err);
    }
  }

  // ── Delete definition ────────────────────────────────────
  async function deleteDefinition(defId) {
    if (!confirm("Delete this workflow definition?")) return;
    try {
      await fetch(`/api/workflows/${defId}`, { method: "DELETE" });
      loadDefinitions();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  }

  // ── Create workflow ──────────────────────────────────────
  const createBtn = document.getElementById("btn-create-workflow");
  const createDialog = document.getElementById("create-workflow-dialog");
  const cancelCreate = document.getElementById("btn-cancel-create");

  createBtn?.addEventListener("click", () => createDialog?.showModal());
  cancelCreate?.addEventListener("click", () => createDialog?.close());

  document.getElementById("create-workflow-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const name = form.elements.name.value.trim();
    const description = form.elements.description.value.trim();
    const yamlBody = form.elements.yaml_body.value.trim();

    let body;
    try {
      // Try parsing as JSON first, then YAML-like
      body = JSON.parse(yamlBody);
    } catch {
      // Simple YAML-like → just send as nodes array placeholder
      body = { name, description, nodes: [] };
    }
    body.name = name;
    body.description = description;

    try {
      const res = await fetch("/api/workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        createDialog.close();
        form.reset();
        loadDefinitions();
      } else {
        const err = await res.json();
        alert(err.error || "Creation failed");
      }
    } catch (err) {
      alert("Error: " + err.message);
    }
  });

  // ── Refresh button ───────────────────────────────────────
  document.getElementById("btn-refresh-runs")?.addEventListener("click", loadRuns);

  // ── Initial load ─────────────────────────────────────────
  loadRuns();
})();
