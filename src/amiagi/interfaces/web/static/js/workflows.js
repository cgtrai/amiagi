/**
 * Workflow Studio — client-side logic.
 * Manages tabs, CRUD for definitions and runs, DAG rendering.
 */
(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────
  let activeTab = "runs";
  let editingDefinitionId = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function parseScalar(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const lowered = text.toLowerCase();
    if (lowered === "true") return true;
    if (lowered === "false") return false;
    if (lowered === "null" || lowered === "none") return null;
    if (/^-?\d+$/.test(text)) return Number(text);
    if (/^-?\d+\.\d+$/.test(text)) return Number(text);
    if ((text.startsWith("[") && text.endsWith("]")) || (text.startsWith("{") && text.endsWith("}"))) {
      try {
        return JSON.parse(text.replace(/'/g, '"'));
      } catch (_) {
        return text;
      }
    }
    return text.replace(/^['\"]|['\"]$/g, "");
  }

  function fallbackParseYamlDefinition(yamlBody) {
    const result = {};
    const nodes = [];
    let currentNode = null;

    String(yamlBody || "").split(/\r?\n/).forEach((rawLine) => {
      const line = rawLine.replace(/\s+$/, "");
      if (!line.trim() || line.trim().startsWith("#")) return;

      const stripped = line.trimStart();
      const indent = line.length - stripped.length;

      if (indent === 0) {
        if (stripped === "nodes:") {
          result.nodes = nodes;
          currentNode = null;
          return;
        }
        const idx = stripped.indexOf(":");
        if (idx === -1) return;
        const key = stripped.slice(0, idx).trim();
        const value = stripped.slice(idx + 1);
        result[key] = parseScalar(value);
        currentNode = null;
        return;
      }

      if (stripped.startsWith("-")) {
        currentNode = {};
        nodes.push(currentNode);
        const itemBody = stripped.slice(1).trim();
        if (itemBody && itemBody.includes(":")) {
          const idx = itemBody.indexOf(":");
          currentNode[itemBody.slice(0, idx).trim()] = parseScalar(itemBody.slice(idx + 1));
        }
        return;
      }

      if (currentNode && stripped.includes(":")) {
        const idx = stripped.indexOf(":");
        currentNode[stripped.slice(0, idx).trim()] = parseScalar(stripped.slice(idx + 1));
      }
    });

    if (nodes.length && !result.nodes) result.nodes = nodes;
    return result;
  }

  function tryParseDefinitionText(yamlBody) {
    const text = String(yamlBody || "").trim();
    if (!text) return { nodes: Array.from([]) };
    try {
      return JSON.parse(text);
    } catch (_) {
      return fallbackParseYamlDefinition(text);
    }
  }

  function validateDefinition(definition) {
    const errors = [];
    const nodes = Array.isArray(definition?.nodes) ? definition.nodes : [];
    const ids = new Set();
    const indegree = {};
    const adjacency = {};

    if (!nodes.length) {
      errors.push("Add at least one workflow node.");
      return errors;
    }

    nodes.forEach((node, index) => {
      const nodeId = String(node.node_id || node.id || "").trim();
      if (!nodeId) {
        errors.push(`Node #${index + 1} is missing node_id.`);
        return;
      }
      if (ids.has(nodeId)) {
        errors.push(`Node '${nodeId}' is duplicated.`);
      }
      ids.add(nodeId);
      indegree[nodeId] = 0;
      adjacency[nodeId] = [];
    });

    nodes.forEach((node) => {
      const nodeId = String(node.node_id || node.id || "").trim();
      const deps = Array.isArray(node.depends_on) ? node.depends_on : [];
      deps.forEach((depId) => {
        if (!ids.has(depId)) {
          errors.push(`Node '${nodeId}' depends on unknown '${depId}'.`);
          return;
        }
        indegree[nodeId] += 1;
        adjacency[depId].push(nodeId);
      });
    });

    const queue = Object.keys(indegree).filter((id) => indegree[id] === 0);
    if (!queue.length) {
      errors.push("Workflow has no root nodes.");
      return errors;
    }

    let visited = 0;
    while (queue.length) {
      const nodeId = queue.shift();
      visited += 1;
      (adjacency[nodeId] || []).forEach((child) => {
        indegree[child] -= 1;
        if (indegree[child] === 0) queue.push(child);
      });
    }
    if (visited !== Object.keys(indegree).length) {
      errors.push("Workflow DAG contains a cycle.");
    }

    return errors;
  }

  function normalizePreviewNodes(definition) {
    const nodes = Array.isArray(definition?.nodes) ? definition.nodes : [];
    return nodes.map((node) => ({
      id: node.node_id || node.id,
      label: node.label || node.name || node.node_id || node.id,
      status: node.status || "pending",
      depends_on: Array.isArray(node.depends_on) ? node.depends_on : [],
      type: node.node_type || node.type || "",
      progress: node.progress || (node.config && (node.config.progress || node.config.progress_label)) || "",
    })).filter((node) => node.id);
  }

  function definitionToYaml(definition) {
    const lines = ["nodes:"];
    (definition.nodes || []).forEach((node) => {
      lines.push(`  - node_id: ${node.node_id}`);
      lines.push(`    node_type: ${(node.node_type || "execute").toString()}`);
      lines.push(`    label: ${node.label || node.node_id}`);
      if (node.description) lines.push(`    description: ${node.description}`);
      if (node.agent_role) lines.push(`    agent_role: ${node.agent_role}`);
      if (Array.isArray(node.depends_on) && node.depends_on.length) {
        lines.push(`    depends_on: [${node.depends_on.join(", ")}]`);
      }
      if (node.progress) lines.push(`    progress: ${node.progress}`);
    });
    return lines.join("\n");
  }

  function updateWorkflowPreview() {
    const form = document.getElementById("create-workflow-form");
    const previewDag = document.getElementById("workflow-editor-preview");
    const previewSummary = document.getElementById("workflow-preview-summary");
    const previewErrors = document.getElementById("workflow-preview-errors");
    const previewStatus = document.getElementById("workflow-preview-status");
    if (!form || !previewDag || !previewErrors || !previewStatus || !previewSummary) return;

    const definition = tryParseDefinitionText(form.elements.yaml_body.value || "");
    if (!definition.name && form.elements.name.value.trim()) definition.name = form.elements.name.value.trim();
    if (!definition.description && form.elements.description.value.trim()) definition.description = form.elements.description.value.trim();

    const nodes = normalizePreviewNodes(definition);
    previewDag.setNodes(nodes);
    previewSummary.textContent = `${nodes.length} node${nodes.length === 1 ? "" : "s"}`;

    const errors = validateDefinition(definition);
    if (!errors.length) {
      previewStatus.textContent = "Valid DAG";
      previewStatus.className = "glass-pill workflow-preview-pill workflow-preview-pill-ok";
      previewErrors.innerHTML = `<li>Ready to save ${escapeHtml(definition.name || "workflow")}.</li>`;
      return;
    }

    previewStatus.textContent = `${errors.length} issue${errors.length === 1 ? "" : "s"}`;
    previewStatus.className = "glass-pill workflow-preview-pill workflow-preview-pill-warn";
    previewErrors.innerHTML = errors.map((error) => `<li>${escapeHtml(error)}</li>`).join("");
  }

  function resetWorkflowDialog() {
    editingDefinitionId = null;
    const form = document.getElementById("create-workflow-form");
    if (!form) return;
    form.reset();
    form.elements.definition_id.value = "";
    document.getElementById("workflow-dialog-title").textContent = "Create Workflow";
    document.getElementById("workflow-dialog-mode-badge").textContent = "Create";
    document.getElementById("workflow-submit-button").textContent = "Create";
    updateWorkflowPreview();
  }

  function openWorkflowDialog(definition) {
    const dialog = document.getElementById("create-workflow-dialog");
    const form = document.getElementById("create-workflow-form");
    if (!dialog || !form) return;

    if (definition) {
      editingDefinitionId = definition.id;
      form.elements.definition_id.value = definition.id || "";
      form.elements.name.value = definition.name || "";
      form.elements.description.value = definition.description || "";
      form.elements.yaml_body.value = definitionToYaml(definition);
      document.getElementById("workflow-dialog-title").textContent = "Edit Workflow";
      document.getElementById("workflow-dialog-mode-badge").textContent = "Edit";
      document.getElementById("workflow-submit-button").textContent = "Save changes";
    } else {
      resetWorkflowDialog();
    }
    updateWorkflowPreview();
    dialog.showModal();
  }

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

  function statusBadge(label, variant) {
    return `<span class="glass-badge ${variant || 'badge-muted'}">${label}</span>`;
  }

  function runNodeBadge(status) {
    const normalized = String(status || "pending").toLowerCase();
    if (normalized === "completed") return statusBadge("completed", "badge-success");
    if (normalized === "running") return statusBadge("running", "badge-working");
    if (normalized === "waiting_approval") return statusBadge("approval", "badge-paused");
    if (normalized === "failed") return statusBadge("failed", "badge-error");
    return statusBadge(normalized, "badge-muted");
  }

  function renderRunCard(run) {
    const statusClass = run.status === "running" ? "status-running" :
                        run.status === "paused" ? "status-paused" :
                        run.status === "completed" ? "status-completed" : "status-failed";
    const nodesHtml = run.nodes.map((n) => {
      return `<span class="dag-inline-node node-${n.status}" title="${n.label}">${n.label} ${runNodeBadge(n.status)}</span>`;
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
      el.querySelectorAll("[data-action='edit']").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          editDefinition(btn.dataset.defId);
        });
      });
      el.querySelectorAll("[data-action='clone']").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          cloneDefinition(btn.dataset.defId);
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
    const gateCount = (def.nodes || []).filter((node) => ["gate", "GATE"].includes(node.node_type || node.type)).length;
    return `<div class="glass-card definition-card">
      <div class="def-header"><strong>${def.name}</strong></div>
      <p class="def-desc">${def.description || ""}</p>
      <div class="def-meta">${def.nodes.length} nodes${gateCount ? ` • ${gateCount} gate${gateCount === 1 ? '' : 's'}` : ""}</div>
      <div class="def-actions">
        <button class="btn btn-sm btn-primary" data-action="start" data-def-id="${def.id}">Start</button>
        <button class="btn btn-sm btn-outline" data-action="edit" data-def-id="${def.id}">Edit</button>
        <button class="btn btn-sm btn-outline" data-action="clone" data-def-id="${def.id}">Clone</button>
        <button class="btn btn-sm btn-danger" data-action="delete" data-def-id="${def.id}">Delete</button>
      </div>
    </div>`;
  }

  async function responseErrorMessage(res, fallback) {
    try {
      const data = await res.json();
      if (Array.isArray(data.details) && data.details.length) {
        return data.details.join("; ");
      }
      return data.error || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function notify(message, type) {
    if (typeof showToast === "function") {
      showToast(message, type || "info");
    } else {
      alert(message);
    }
  }

  async function editDefinition(defId) {
    try {
      const res = await fetch(`/api/workflows/${defId}`);
      const data = await res.json();
      if (res.ok && data.definition) {
        openWorkflowDialog(data.definition);
      } else {
        notify(data.error || "Failed to load workflow definition", "error");
      }
    } catch (err) {
      notify("Failed to load workflow definition", "error");
    }
  }

  async function cloneDefinition(defId) {
    try {
      const res = await fetch(`/api/workflows/${defId}/clone`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      if (res.ok) {
        loadDefinitions();
        notify("Workflow cloned", "success");
      } else {
        notify(await responseErrorMessage(res, "Clone failed"), "error");
      }
    } catch (err) {
      notify("Clone failed", "error");
    }
  }

  // ── Run detail ───────────────────────────────────────────
  async function openRunDetail(runId) {
    const panel = document.getElementById("workflow-run-detail");
    try {
      const res = await fetch(`/api/workflow-runs/${runId}`);
      if (!res.ok) {
        notify(await responseErrorMessage(res, "Failed to load run detail"), "error");
        return;
      }
      const data = await res.json();
      if (!data.run) return;

      const run = data.run;
      document.getElementById("run-detail-title").textContent = run.workflow_name;
      document.getElementById("run-detail-status").textContent = run.status;
      document.getElementById("run-detail-status").className = `glass-pill status-${run.status}`;
      document.getElementById("run-detail-started").textContent = new Date(run.started_at * 1000).toLocaleString();

      // DAG component
      const dag = document.getElementById("run-dag");
      if (dag && dag.setNodes) {
        const nodes = (run.nodes || []).map(n => ({
          id: n.node_id || n.id,
          label: n.label || n.name || n.node_id || n.id,
          status: n.status || 'pending',
          depends_on: n.depends_on || [],
          type: n.node_type || n.type || '',
          progress: n.progress || ''
        }));
        dag.setNodes(nodes);
      }

      // Controls
      const controls = document.getElementById("run-controls");
      controls.querySelectorAll("[data-action]").forEach((btn) => {
        btn.onclick = () => runAction(runId, btn.dataset.action);
      });

      // W2 — GATE approve button if any gate node is waiting
      const gateWaiting = (run.nodes || []).find(n =>
        (n.node_type === 'gate' || n.node_type === 'GATE' || n.type === 'gate') &&
        (n.status === 'waiting' || n.status === 'pending' || n.status === 'waiting_approval')
      );
      // Remove previous gate approve button if any
      var oldGateBtn = controls.querySelector('[data-action="approve-gate"]');
      if (oldGateBtn) oldGateBtn.remove();
      if (gateWaiting) {
        var gateBtn = document.createElement('button');
        gateBtn.className = 'btn btn-sm btn-outline';
        gateBtn.style.cssText = 'color:var(--glass-success,#22c55e);border-color:var(--glass-success,#22c55e)';
        gateBtn.dataset.action = 'approve-gate';
        gateBtn.textContent = 'Approve gate';
        gateBtn.onclick = async function () {
          var nodeId = gateWaiting.node_id || gateWaiting.id;
          try {
            const response = await fetch('/api/workflow-runs/' + runId + '/approve/' + nodeId, { method: 'POST' });
            if (!response.ok) {
              notify(await responseErrorMessage(response, 'Gate approval failed'), 'error');
              return;
            }
            notify('Gate approved', 'success');
            openRunDetail(runId);
          } catch (err) {
            notify('Gate approval failed', 'error');
          }
        };
        controls.appendChild(gateBtn);
      }

      panel.hidden = false;
    } catch (err) {
      notify("Failed to load run detail", "error");
    }
  }

  document.getElementById("btn-close-run-detail")?.addEventListener("click", () => {
    document.getElementById("workflow-run-detail").hidden = true;
  });

  async function runAction(runId, action) {
    try {
      const res = await fetch(`/api/workflow-runs/${runId}/${action}`, { method: "POST" });
      if (!res.ok) {
        notify(await responseErrorMessage(res, `Run ${action} failed`), "error");
        return;
      }
      loadRuns();
      openRunDetail(runId);
      notify(`Run ${action} completed`, "success");
    } catch (err) {
      notify(`Run ${action} failed`, "error");
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
        notify("Workflow started", "success");
      } else {
        notify(await responseErrorMessage(res, "Start run failed"), "error");
      }
    } catch (err) {
      notify("Start run failed", "error");
    }
  }

  // ── Delete definition ────────────────────────────────────
  async function deleteDefinition(defId) {
    if (!confirm("Delete this workflow definition?")) return;
    try {
      const res = await fetch(`/api/workflows/${defId}`, { method: "DELETE" });
      if (!res.ok) {
        notify(await responseErrorMessage(res, "Delete failed"), "error");
        return;
      }
      loadDefinitions();
      notify("Workflow deleted", "success");
    } catch (err) {
      notify("Delete failed", "error");
    }
  }

  // ── Create workflow ──────────────────────────────────────
  const createBtn = document.getElementById("btn-create-workflow");
  const createDialog = document.getElementById("create-workflow-dialog");
  const cancelCreate = document.getElementById("btn-cancel-create");

  createBtn?.addEventListener("click", () => openWorkflowDialog());
  cancelCreate?.addEventListener("click", () => {
    createDialog?.close();
    resetWorkflowDialog();
  });

  ["name", "description", "yaml_body"].forEach((fieldName) => {
    document.querySelector(`#create-workflow-form [name='${fieldName}']`)?.addEventListener("input", updateWorkflowPreview);
  });

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
      body = { name, description, yaml_body: yamlBody };
    }
    body.name = name;
    body.description = description;

    try {
      const url = editingDefinitionId ? `/api/workflows/${editingDefinitionId}` : "/api/workflows";
      const method = editingDefinitionId ? "PUT" : "POST";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        createDialog.close();
        resetWorkflowDialog();
        loadDefinitions();
        notify(editingDefinitionId ? "Workflow updated" : "Workflow created", "success");
      } else {
        notify(await responseErrorMessage(res, editingDefinitionId ? "Update failed" : "Creation failed"), "error");
      }
    } catch (err) {
      notify("Error: " + err.message, "error");
    }
  });

  // ── Refresh button ───────────────────────────────────────
  document.getElementById("btn-refresh-runs")?.addEventListener("click", loadRuns);

  // ── Initial load ─────────────────────────────────────────
  updateWorkflowPreview();
  loadRuns();
})();
