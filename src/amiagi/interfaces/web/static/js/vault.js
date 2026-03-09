(function () {
  "use strict";

  const addBtn = document.getElementById("btn-add-secret");
  const refreshBtn = document.getElementById("btn-refresh-vault");
  const cancelBtn = document.getElementById("btn-cancel-secret");
  const overlay = document.getElementById("vault-modal-overlay");
  const modalTitle = document.getElementById("vault-modal-title");
  const form = document.getElementById("vault-secret-form");
  const agentSelect = document.getElementById("vault-agent-id");
  const keyInput = document.getElementById("vault-key");
  const valueInput = document.getElementById("vault-value");
  const typeInput    = document.getElementById("vault-type");
  const expiresInput = document.getElementById("vault-expires-at");
  const formStatus = document.getElementById("vault-form-status");
  const listEl = document.getElementById("vault-secret-list");
  const emptyEl = document.getElementById("vault-empty");
  const logEl = document.getElementById("vault-access-log");
  const rotationForm = document.getElementById("vault-rotation-form");
  const rotationPanel = document.getElementById("vault-rotation-panel");
  const rotationEmpty = document.getElementById("vault-rotation-empty");
  const rotationStatus = document.getElementById("vault-rotation-status");
  const rotationLabel = document.getElementById("vault-rotation-secret-label");
  const rotationList = document.getElementById("vault-rotation-list");
  const rotationCron = document.getElementById("vault-rotation-cron");

  let currentRotationSecretId = "";
  let currentRotationSecretLabel = "";

  function t(key, fallback) {
    try {
      return window.t ? window.t(key) : fallback;
    } catch {
      return fallback;
    }
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function fmtDate(iso) {
    if (!iso) return t("vault.never", "Never");
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  }

  function setRotationStatus(message, kind) {
    if (!rotationStatus) return;
    rotationStatus.textContent = message || "";
    rotationStatus.className = kind ? "vault-form-status --" + kind : "vault-form-status";
  }

  function openModal(title, agentId, key) {
    modalTitle.textContent = title || "Add Secret";
    if (agentId) {
      agentSelect.value = agentId;
      agentSelect.disabled = true;
    } else {
      agentSelect.disabled = false;
    }
    if (key) {
      keyInput.value = key;
      keyInput.readOnly = true;
    } else {
      keyInput.value = "";
      keyInput.readOnly = false;
    }
    valueInput.value = "";
    typeInput.value = "api_key";
    expiresInput.value = "";
    formStatus.textContent = "";
    overlay.removeAttribute("hidden");
    overlay.style.display = "flex";
    overlay.classList.add("--visible");
  }

  function closeModal() {
    overlay.classList.remove("--visible");
    overlay.style.display = "none";
    overlay.setAttribute("hidden", "");
    form.reset();
    agentSelect.disabled = false;
    keyInput.readOnly = false;
    typeInput.value = "api_key";
    expiresInput.value = "";
    formStatus.textContent = "";
  }

  async function fetchVault() {
    const r = await fetch("/api/vault");
    return r.ok ? r.json() : null;
  }

  async function fetchLog() {
    try {
      const r = await fetch("/api/vault/access-log");
      return r.ok ? r.json() : null;
    } catch { return null; }
  }

  async function fetchAgents() {
    try {
      const r = await fetch("/api/agents");
      return r.ok ? r.json() : null;
    } catch { return null; }
  }

  function renderList(data) {
    listEl.innerHTML = "";
    const agents = data && data.agents ? data.agents : [];
    if (agents.length === 0) {
      emptyEl.style.display = "";
      return;
    }
    emptyEl.style.display = "none";

    for (const a of agents) {
      const group = document.createElement("div");
      group.className = "vault-agent-group";

      group.innerHTML = '<div class="vault-agent-header">' +
        '<span class="vault-agent-name">' + esc(a.agent_id) + "</span>" +
        '<span class="vault-agent-count">' + (a.keys ? a.keys.length : 0) + " keys</span>" +
        "</div>";

      const keys = a.keys || [];
      for (const k of keys) {
        const row = document.createElement("div");
        row.className = "vault-secret-row";
        const keyName = typeof k === "string" ? k : (k.key || k.name || "");
        const secretType = typeof k === "object" ? (k.type || "api_key") : "api_key";
        const expiresAt = typeof k === "object" ? k.expires_at : null;
        const lastAccess = typeof k === "object" ? k.last_access : null;
        const secretStatus = typeof k === "object" ? (k.status || "active") : "active";
        const secretId = typeof k === "object" ? (k.id || (a.agent_id + ":" + keyName)) : (a.agent_id + ":" + keyName);
        const statusCls = secretStatus === "active" ? "badge-success" : secretStatus === "expiring" ? "badge-danger" : "badge-muted";
        row.innerHTML =
          '<span class="vault-secret-key">' + esc(keyName) + "</span>" +
          '<span class="glass-badge ' + statusCls + '">' + esc(secretStatus) + "</span>" +
          '<span class="meta-tag">' + esc(secretType) + "</span>" +
          '<span class="meta-tag">' + esc(t("vault.expires_short", "Exp")) + ': ' + esc(fmtDate(expiresAt)) + "</span>" +
          '<span class="meta-tag">' + esc(t("vault.last_access_short", "Last")) + ': ' + esc(fmtDate(lastAccess)) + "</span>" +
          '<span class="vault-secret-value"><secret-field value="••••••••"></secret-field></span>' +
          '<span class="vault-secret-actions">' +
            '<button class="glass-btn glass-btn--xs js-edit" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '" data-secret-id="' + esc(secretId) + '" data-type="' + esc(secretType) + '" data-expires-at="' + esc(expiresAt || "") + '">✏️</button>' +
            '<button class="glass-btn glass-btn--xs js-log" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '" data-secret-id="' + esc(secretId) + '">🕘</button>' +
            '<button class="glass-btn glass-btn--xs js-rotation" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '" data-secret-id="' + esc(secretId) + '">⏰</button>' +
            '<button class="glass-btn glass-btn--xs js-assign" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '">👥</button>' +
            '<button class="glass-btn glass-btn--xs js-rotate" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '">↻</button>' +
            '<button class="glass-btn glass-btn--xs glass-btn--danger js-del" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(keyName) + '">✕</button>' +
          "</span>";
        group.appendChild(row);
      }
      listEl.appendChild(group);
    }
  }

  function renderLog(data) {
    if (!logEl) return;
    logEl.innerHTML = "";
    const entries = data && data.entries ? data.entries : (Array.isArray(data) ? data : []);
    if (entries.length === 0) {
      logEl.innerHTML = '<p class="vault-empty">No access logs yet.</p>';
      return;
    }
    for (const e of entries) {
      const row = document.createElement("div");
      row.className = "vault-log-entry";
      row.innerHTML =
        '<span class="vault-log-time">' + esc(fmtTime(e.timestamp || e.ts)) + "</span>" +
        '<span class="vault-log-action --' + esc(e.action || "") + '">' + esc(e.action || "—") + "</span>" +
        '<span class="vault-log-target">' + esc(e.agent_id || "") + (e.key ? " / " + esc(e.key) : "") + "</span>";
      logEl.appendChild(row);
    }
  }

  function renderRotationJobs(data) {
    if (!rotationList) return;
    const jobs = data && data.jobs ? data.jobs : [];
    if (jobs.length === 0) {
      rotationList.innerHTML = '<div class="vault-empty">' + esc(t("vault.no_rotation_jobs", "No rotation jobs yet")) + '</div>';
      return;
    }
    rotationList.innerHTML = jobs.map(function (job) {
      return '<div class="vault-log-entry">'
        + '<span class="vault-log-target">' + esc(job.human_readable || job.cron_expr || "") + '</span>'
        + '<span class="meta-tag">' + esc(job.cron_expr || "") + '</span>'
        + '<span class="meta-tag">' + esc(t("vault.next_run", "Next")) + ': ' + esc(fmtDate(job.next_run)) + '</span>'
        + '<span class="glass-badge ' + (job.enabled ? 'badge-success">enabled' : 'badge-muted">disabled') + '</span>'
        + '<button class="glass-btn glass-btn--xs glass-btn--danger js-rotation-delete" data-job-id="' + esc(job.id) + '">' + esc(t("vault.rotation_delete", "Delete")) + '</button>'
        + '</div>';
    }).join("");
  }

  async function loadRotationSchedules(secretId) {
    if (!secretId || !rotationList) return;
    setRotationStatus(t("vault.rotation_loading", "Loading rotation workflow…"), "");
    try {
      const r = await fetch("/api/vault/" + encodeURIComponent(secretId) + "/rotation-schedule");
      const data = await r.json();
      if (!r.ok) {
        renderRotationJobs({ jobs: [] });
        setRotationStatus(data.error || t("vault.rotation_load_failed", "Could not load rotation workflow"), "err");
        return;
      }
      renderRotationJobs(data);
      setRotationStatus(currentRotationSecretLabel || secretId, "ok");
    } catch (err) {
      renderRotationJobs({ jobs: [] });
      setRotationStatus(err.message || t("vault.rotation_load_failed", "Could not load rotation workflow"), "err");
    }
  }

  function openRotationPanel(agentId, key, secretId) {
    currentRotationSecretId = secretId || (agentId + ":" + key);
    currentRotationSecretLabel = agentId + "/" + key;
    if (rotationEmpty) rotationEmpty.hidden = true;
    if (rotationPanel) rotationPanel.hidden = false;
    if (rotationLabel) rotationLabel.textContent = currentRotationSecretLabel;
    if (rotationCron) rotationCron.value = "";
    loadRotationSchedules(currentRotationSecretId);
  }

  async function saveRotationSchedule(e) {
    e.preventDefault();
    if (!currentRotationSecretId) return;
    const cronExpr = rotationCron && rotationCron.value ? rotationCron.value.trim() : "";
    if (!cronExpr) {
      setRotationStatus(t("vault.rotation_cron_required", "Cron expression is required"), "err");
      return;
    }
    setRotationStatus(t("vault.rotation_saving", "Saving rotation workflow…"), "");
    try {
      const r = await fetch("/api/vault/" + encodeURIComponent(currentRotationSecretId) + "/rotation-schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cron_expr: cronExpr }),
      });
      const data = await r.json();
      if (!r.ok) {
        setRotationStatus(data.error || t("vault.rotation_save_failed", "Could not save rotation workflow"), "err");
        return;
      }
      if (rotationCron) rotationCron.value = "";
      setRotationStatus(t("vault.rotation_saved", "Rotation workflow saved"), "ok");
      loadRotationSchedules(currentRotationSecretId);
    } catch (err) {
      setRotationStatus(err.message || t("vault.rotation_save_failed", "Could not save rotation workflow"), "err");
    }
  }

  async function deleteRotationSchedule(jobId) {
    if (!currentRotationSecretId || !jobId) return;
    try {
      const r = await fetch("/api/vault/" + encodeURIComponent(currentRotationSecretId) + "/rotation-schedule/" + encodeURIComponent(jobId), {
        method: "DELETE",
      });
      if (!r.ok) {
        const data = await r.json();
        setRotationStatus(data.error || t("vault.rotation_delete_failed", "Could not delete rotation workflow"), "err");
        return;
      }
      setRotationStatus(t("vault.rotation_deleted", "Rotation workflow deleted"), "ok");
      loadRotationSchedules(currentRotationSecretId);
    } catch (err) {
      setRotationStatus(err.message || t("vault.rotation_delete_failed", "Could not delete rotation workflow"), "err");
    }
  }

  async function populateAgentSelect() {
    const data = await fetchAgents();
    if (!data) return;
    const agents = data.agents || (Array.isArray(data) ? data : []);
    agentSelect.innerHTML = '<option value="">— Select agent —</option>';
    for (const a of agents) {
      const id = a.agent_id || a.id || a;
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      agentSelect.appendChild(opt);
    }
  }

  async function saveSecret(e) {
    e.preventDefault();
    const agentId = agentSelect.value.trim();
    const key = keyInput.value.trim();
    const value = valueInput.value;
    const secretType = typeInput.value || "api_key";
    const expiresAt = expiresInput.value ? new Date(expiresInput.value).toISOString() : null;

    if (!agentId || !key || !value) {
      formStatus.textContent = "All fields are required";
      formStatus.className = "vault-form-status --err";
      return;
    }
    formStatus.textContent = "Saving…";
    formStatus.className = "vault-form-status";
    try {
      const isEdit = keyInput.readOnly;
      const targetUrl = isEdit
        ? "/api/vault/" + encodeURIComponent(agentId + ":" + key)
        : "/api/vault";
      const method = isEdit ? "PUT" : "POST";
      const r = await fetch(targetUrl, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agentId, key: key, value: value, type: secretType, expires_at: expiresAt }),
      });
      if (r.ok) {
        formStatus.textContent = "Saved";
        formStatus.className = "vault-form-status --ok";
        setTimeout(function () { closeModal(); load(); }, 600);
      } else {
        const d = await r.json();
        formStatus.textContent = d.error || "Save failed";
        formStatus.className = "vault-form-status --err";
      }
    } catch (err) {
      formStatus.textContent = "Error: " + err.message;
      formStatus.className = "vault-form-status --err";
    }
  }

  async function showSecretLog(agentId, key, secretId) {
    try {
      const r = await fetch("/api/vault/" + encodeURIComponent(secretId || (agentId + ":" + key)) + "/access-log");
      if (!r.ok) { alert(t("vault.secret_log_failed", "Could not load secret access log")); return; }
      const data = await r.json();
      const entries = data.entries || [];
      if (entries.length === 0) {
        alert(t("vault.no_logs", "No access logs yet"));
        return;
      }
      const lines = entries.map(function (entry) {
        return fmtDate(entry.timestamp) + " · " + (entry.action || "—") + " · " + (entry.user || "—");
      });
      alert(agentId + "/" + key + "\n\n" + lines.join("\n"));
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  async function deleteSecret(agentId, key) {
    if (!confirm("Delete " + agentId + "/" + key + "?")) return;
    try {
      const r = await fetch(
        "/api/vault/" + encodeURIComponent(agentId) + "/" + encodeURIComponent(key),
        { method: "DELETE" }
      );
      if (r.ok) load();
      else alert("Delete failed");
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  async function rotateSecret(agentId, key) {
    const newVal = prompt("New value for " + agentId + "/" + key + ":");
    if (!newVal) return;
    try {
      const r = await fetch(
        "/api/vault/" + encodeURIComponent(agentId) + "/" + encodeURIComponent(key) + "/rotate",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: newVal }),
        }
      );
      if (r.ok) load();
      else alert("Rotate failed");
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  async function showAssignments(agentId, key) {
    try {
      const r = await fetch(
        "/api/vault/" + encodeURIComponent(agentId) + "/" + encodeURIComponent(key) + "/assignments"
      );
      if (!r.ok) { alert("Could not load assignments"); return; }
      const data = await r.json();
      const current = (data.assignments || []).map(function (a) {
        return a.entity_type + ":" + a.entity_id;
      });
      var input = prompt(
        "Assignments for " + agentId + "/" + key + "\n" +
        "Current: " + (current.length > 0 ? current.join(", ") : "(none)") + "\n\n" +
        "Enter comma-separated list (e.g. agent:kastor, skill:code_gen):",
        current.join(", ")
      );
      if (input === null) return;
      var entries = input.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      var assignments = [];
      for (var i = 0; i < entries.length; i++) {
        var parts = entries[i].split(":");
        if (parts.length === 2) {
          assignments.push({ entity_type: parts[0].trim(), entity_id: parts[1].trim() });
        }
      }
      var r2 = await fetch(
        "/api/vault/" + encodeURIComponent(agentId) + "/" + encodeURIComponent(key) + "/assignments",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assignments: assignments }),
        }
      );
      if (r2.ok) load();
      else alert("Save assignments failed");
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  if (addBtn) addBtn.addEventListener("click", function () {
    openModal("Add Secret");
  });
  if (cancelBtn) cancelBtn.addEventListener("click", function (e) {
    e.preventDefault();
    e.stopPropagation();
    closeModal();
  });
  if (overlay) overlay.addEventListener("click", function (e) {
    if (e.target === overlay) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && overlay.classList.contains("--visible")) {
      closeModal();
    }
  });
  if (form) form.addEventListener("submit", saveSecret);
  if (refreshBtn) refreshBtn.addEventListener("click", load);

  listEl.addEventListener("click", function (e) {
    const btn = e.target.closest("button");
    if (!btn) return;
    const agent = btn.dataset.agent;
    const key = btn.dataset.key;
    const secretId = btn.dataset.secretId;
    if (!agent || !key) return;
    if (btn.classList.contains("js-del")) deleteSecret(agent, key);
    else if (btn.classList.contains("js-rotate")) rotateSecret(agent, key);
    else if (btn.classList.contains("js-rotation")) openRotationPanel(agent, key, secretId);
    else if (btn.classList.contains("js-assign")) showAssignments(agent, key);
    else if (btn.classList.contains("js-log")) showSecretLog(agent, key, secretId);
    else if (btn.classList.contains("js-edit")) {
      openModal("Edit Secret", agent, key);
      typeInput.value = btn.dataset.type || "api_key";
      expiresInput.value = btn.dataset.expiresAt ? new Date(btn.dataset.expiresAt).toISOString().slice(0, 16) : "";
    }
  });

  if (rotationList) rotationList.addEventListener("click", function (e) {
    const btn = e.target.closest("button");
    if (!btn || !btn.classList.contains("js-rotation-delete")) return;
    deleteRotationSchedule(btn.dataset.jobId);
  });

  if (rotationForm) rotationForm.addEventListener("submit", saveRotationSchedule);

  async function load() {
    const [vault, log] = await Promise.all([fetchVault(), fetchLog()]);
    renderList(vault);
    renderLog(log);
  }

  populateAgentSelect();
  load();
  setInterval(load, 20000);
})();
