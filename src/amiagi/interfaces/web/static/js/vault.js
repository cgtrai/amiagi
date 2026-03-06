(function () {
  "use strict";

  /* ── DOM refs ──────────────────────────────────────────────── */
  const addBtn       = document.getElementById("btn-add-secret");
  const refreshBtn   = document.getElementById("btn-refresh-vault");
  const cancelBtn    = document.getElementById("btn-cancel-secret");
  const overlay      = document.getElementById("vault-modal-overlay");
  const modalTitle   = document.getElementById("vault-modal-title");
  const form         = document.getElementById("vault-secret-form");
  const agentSelect  = document.getElementById("vault-agent-id");
  const keyInput     = document.getElementById("vault-key");
  const valueInput   = document.getElementById("vault-value");
  const formStatus   = document.getElementById("vault-form-status");
  const listEl       = document.getElementById("vault-secret-list");
  const emptyEl      = document.getElementById("vault-empty");
  const logEl        = document.getElementById("vault-access-log");

  /* ── Helpers ───────────────────────────────────────────────── */
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

  /* ── Modal ─────────────────────────────────────────────────── */
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
    formStatus.textContent = "";
  }

  /* ── Data fetching ─────────────────────────────────────────── */
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

  /* ── Render ────────────────────────────────────────────────── */
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

      let header = '<div class="vault-agent-header">' +
        '<span class="vault-agent-name">' + esc(a.agent_id) + "</span>" +
        '<span class="vault-agent-count">' + (a.keys ? a.keys.length : 0) + " keys</span>" +
        "</div>";
      group.innerHTML = header;

      const keys = a.keys || [];
      for (const k of keys) {
        const row = document.createElement("div");
        row.className = "vault-secret-row";
        row.innerHTML =
          '<span class="vault-secret-key">' + esc(k) + "</span>" +
          '<span class="vault-secret-value"><secret-field value="••••••••"></secret-field></span>' +
          '<span class="vault-secret-actions">' +
            '<button class="glass-btn glass-btn--xs js-assign" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(k) + '" title="Assignments">👥</button>' +
            '<button class="glass-btn glass-btn--xs js-rotate" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(k) + '" title="Rotate">↻</button>' +
            '<button class="glass-btn glass-btn--xs glass-btn--danger js-del" data-agent="' + esc(a.agent_id) + '" data-key="' + esc(k) + '" title="Delete">✕</button>' +
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

  /* ── Actions ───────────────────────────────────────────────── */
  async function saveSecret(e) {
    e.preventDefault();
    const agentId = agentSelect.value.trim();
    const key     = keyInput.value.trim();
    const value   = valueInput.value;

    if (!agentId || !key || !value) {
      formStatus.textContent = "All fields are required";
      formStatus.className = "vault-form-status --err";
      return;
    }
    formStatus.textContent = "Saving…";
    formStatus.className = "vault-form-status";
    try {
      const r = await fetch("/api/vault/" + encodeURIComponent(agentId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key, value: value }),
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

  /* ── Events ────────────────────────────────────────────────── */
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
    const key   = btn.dataset.key;
    if (!agent || !key) return;
    if (btn.classList.contains("js-del")) deleteSecret(agent, key);
    else if (btn.classList.contains("js-rotate")) rotateSecret(agent, key);
    else if (btn.classList.contains("js-assign")) showAssignments(agent, key);
  });

  /* ── Load ──────────────────────────────────────────────────── */
  async function load() {
    const [vault, log] = await Promise.all([fetchVault(), fetchLog()]);
    renderList(vault);
    renderLog(log);
  }

  populateAgentSelect();
  load();
  setInterval(load, 20000);
})();
