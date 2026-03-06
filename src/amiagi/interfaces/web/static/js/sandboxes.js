/**
 * Sandboxes admin page controller.
 * CRUD sandbox cards, execution log list, shell policy mode toggle.
 */
(function () {
  "use strict";

  /* ── Helpers ──────────────────────────────────────────── */

  async function fetchJSON(url, opts) {
    try {
      const r = await fetch(url, opts);
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  }

  function fmtBytes(bytes) {
    if (!bytes && bytes !== 0) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  }

  function fmtDuration(ms) {
    if (!ms && ms !== 0) return "—";
    if (ms < 1000) return ms + "ms";
    return (ms / 1000).toFixed(1) + "s";
  }

  function toast(msg, type) {
    if (typeof showToast === "function") showToast(msg, type || "info");
  }

  /* ── Sandbox list ──────────────────────────────────────── */

  async function loadSandboxes() {
    const list = document.getElementById("sandboxes-list");
    if (!list) return;
    const data = await fetchJSON("/api/sandboxes");
    if (!data) {
      list.innerHTML = '<p class="sandboxes-muted">Failed to load sandboxes.</p>';
      return;
    }
    const items = data.sandboxes || data || [];
    if (items.length === 0) {
      list.innerHTML = '<p class="sandboxes-muted">No active sandboxes.</p>';
      return;
    }
    list.innerHTML = items.map(sandboxCardHTML).join("");

    /* Attach card button listeners */
    list.querySelectorAll("[data-action]").forEach(function (btn) {
      btn.addEventListener("click", handleSandboxAction);
    });
  }

  function sandboxCardHTML(s) {
    const utilPct = s.utilization_pct || 0;
    const warn =
      utilPct >= 50
        ? '<div class="sandbox-card-warning">⚠️ ' +
          Math.round(utilPct) +
          "% of limit used</div>"
        : "";
    return (
      '<div class="sandbox-card" data-agent="' + (s.agent_id || "") + '">' +
      '<div class="sandbox-card-header">' +
      '<span class="sandbox-card-name">📦 ' + (s.agent_id || "unknown") + "</span>" +
      "</div>" +
      '<div class="sandbox-card-meta">' +
      "<span>Agent: " + (s.agent_id || "—") + "</span>" +
      "<span>Size: " + fmtBytes(s.size_bytes) + "</span>" +
      "<span>Files: " + (s.file_count || 0) + "</span>" +
      "</div>" +
      '<div class="sandbox-card-path">' + (s.path || "") + "</div>" +
      warn +
      '<div class="sandbox-card-actions">' +
      '<button class="glass-btn glass-btn--ghost" data-action="reset" data-agent="' +
      s.agent_id + '">🔄 Reset</button>' +
      '<button class="glass-btn glass-btn--ghost" data-action="cleanup" data-agent="' +
      s.agent_id + '">🧹 Cleanup</button>' +
      '<button class="glass-btn glass-btn--ghost" data-action="destroy" data-agent="' +
      s.agent_id + '">💀 Destroy</button>' +
      "</div>" +
      "</div>"
    );
  }

  async function handleSandboxAction(e) {
    var btn = e.currentTarget;
    var action = btn.dataset.action;
    var agent = btn.dataset.agent;
    if (!agent) return;

    if (action === "destroy") {
      if (!confirm("Destroy sandbox for " + agent + "? This cannot be undone.")) return;
      await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent), { method: "DELETE" });
      toast("Sandbox destroyed", "success");
    } else if (action === "reset") {
      if (!confirm("Reset sandbox for " + agent + "?")) return;
      await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent) + "/reset", {
        method: "POST",
      });
      toast("Sandbox reset", "success");
    } else if (action === "cleanup") {
      await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent) + "/cleanup", {
        method: "POST",
      });
      toast("Sandbox cleaned", "success");
    }
    loadSandboxes();
  }

  /* ── New sandbox ───────────────────────────────────────── */

  async function createSandbox() {
    var agentId = prompt("Agent ID for new sandbox:");
    if (!agentId) return;
    await fetchJSON("/api/sandboxes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agentId }),
    });
    toast("Sandbox created", "success");
    loadSandboxes();
  }

  /* ── Execution log ─────────────────────────────────────── */

  async function loadExecLog(blockedOnly) {
    var list = document.getElementById("exec-log-list");
    if (!list) return;
    var url = "/api/shell-executions";
    if (blockedOnly) url += "?blocked_only=true";
    var data = await fetchJSON(url);
    if (!data) {
      list.innerHTML = '<p class="sandboxes-muted">Failed to load execution log.</p>';
      return;
    }
    var items = data.executions || data || [];
    if (items.length === 0) {
      list.innerHTML = '<p class="sandboxes-muted">No shell executions recorded.</p>';
      return;
    }
    list.innerHTML = items.map(execRowHTML).join("");
  }

  function execRowHTML(ex) {
    var blocked = ex.blocked;
    var statusCls = blocked ? "exec-log-status--blocked" : "exec-log-status--ok";
    var statusTxt = blocked ? "❌ BLOCKED" : "✅ " + (ex.exit_code || 0);
    return (
      '<div class="exec-log-row">' +
      '<span class="exec-log-time">' + fmtTime(ex.created_at) + "</span>" +
      '<span class="exec-log-agent">' + (ex.agent_id || "—") + "</span>" +
      '<span class="exec-log-cmd" title="' + (ex.command || "") + '">' +
      (ex.command || "—") + "</span>" +
      '<span class="exec-log-status ' + statusCls + '">' + statusTxt + "</span>" +
      '<span class="exec-log-duration">' + fmtDuration(ex.duration_ms) + "</span>" +
      "</div>"
    );
  }

  /* ── Shell policy mode toggle ──────────────────────────── */

  function initModeToggle() {
    var btnEditor = document.getElementById("btn-mode-editor");
    var btnJson = document.getElementById("btn-mode-json");
    var editor = document.getElementById("shell-policy-editor");
    if (!btnEditor || !btnJson || !editor) return;

    btnEditor.addEventListener("click", function () {
      btnEditor.classList.add("active");
      btnJson.classList.remove("active");
      editor.setAttribute("mode", "editor");
    });
    btnJson.addEventListener("click", function () {
      btnJson.classList.add("active");
      btnEditor.classList.remove("active");
      editor.setAttribute("mode", "json");
    });
  }

  /* ── Init ──────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function () {
    loadSandboxes();
    loadExecLog(false);
    initModeToggle();

    var btnNew = document.getElementById("btn-new-sandbox");
    if (btnNew) btnNew.addEventListener("click", createSandbox);

    var filterBlocked = document.getElementById("filter-blocked-only");
    if (filterBlocked) {
      filterBlocked.addEventListener("change", function () {
        loadExecLog(filterBlocked.checked);
      });
    }
  });
})();
