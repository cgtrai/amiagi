(function () {
  "use strict";

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

  async function loadSandboxes() {
    const list = document.getElementById("sandboxes-list");
    if (!list) return;
    const data = await fetchJSON("/api/sandboxes");
    if (!data) {
      list.innerHTML = '<p class="sandboxes-muted">' + window.t("sandboxes.list.load_failed", "Failed to load sandboxes.") + '</p>';
      return;
    }
    const items = data.items || [];
    if (items.length === 0) {
      list.innerHTML = '<p class="sandboxes-muted">' + window.t("sandboxes.list.empty", "No active sandboxes.") + '</p>';
      return;
    }
    list.innerHTML = items.map(sandboxCardHTML).join("");
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
          window.t("sandboxes.card.limit_warning", "% of limit used") + '</div>'
        : "";
    const createdAt = s.created_at ? '<span>' + window.t("sandboxes.card.created", "Created: ") + new Date(s.created_at).toLocaleDateString() + '</span>' : '';
    const lastWrite = s.last_write_at ? '<span>' + window.t("sandboxes.card.last_write", "Last write: ") + new Date(s.last_write_at).toLocaleString() + '</span>' : '<span>' + window.t("sandboxes.card.last_write_never", "Last write: Never") + '</span>';
    return '<div class="sandbox-card" data-agent="' + (s.agent_id || "") + '">'
      + '<div class="sandbox-card-header"><span class="sandbox-card-name">📦 ' + (s.agent_id || "unknown") + '</span></div>'
      + '<div class="sandbox-card-meta">'
      + '<span>' + window.t("sandboxes.card.agent_label", "Agent: ") + (s.agent_id || "—") + '</span>'
      + '<span>' + window.t("sandboxes.card.size_label", "Size: ") + fmtBytes(s.size_bytes) + '</span>'
      + '<span>' + window.t("sandboxes.card.files_label", "Files: ") + (s.file_count || 0) + '</span>'
      + createdAt + lastWrite + '</div>'
      + '<div class="sandbox-card-path">' + (s.path || "") + '</div>' + warn
      + '<div class="sandbox-card-actions">'
      + '<button class="glass-btn glass-btn--ghost" data-action="browse" data-sandbox="' + (s.sandbox_id || s.agent_id) + '">' + window.t("sandboxes.action.browse", "📁 Browse") + '</button>'
      + '<button class="glass-btn glass-btn--ghost" data-action="log" data-sandbox="' + (s.sandbox_id || s.agent_id) + '">' + window.t("sandboxes.action.log", "📋 Log") + '</button>'
      + '<button class="glass-btn glass-btn--ghost" data-action="reset" data-agent="' + s.agent_id + '">' + window.t("sandboxes.action.reset", "🔄 Reset") + '</button>'
      + '<button class="glass-btn glass-btn--ghost" data-action="cleanup" data-agent="' + s.agent_id + '">' + window.t("sandboxes.action.cleanup", "🧹 Cleanup") + '</button>'
      + '<button class="glass-btn glass-btn--ghost" data-action="destroy" data-agent="' + s.agent_id + '">' + window.t("sandboxes.action.destroy", "💀 Destroy") + '</button>'
      + '</div></div>';
  }

  async function handleSandboxAction(e) {
    var btn = e.currentTarget;
    var action = btn.dataset.action;
    var agent = btn.dataset.agent;
    var sandboxId = btn.dataset.sandbox;
    if (!agent && action !== 'browse' && action !== 'log') return;

    if (action === "destroy") {
      if (!confirm(window.t("sandboxes.confirm.destroy", "Destroy this sandbox? This cannot be undone."))) return;
      var destroyed = await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent), { method: "DELETE" });
      if (!destroyed || destroyed.error) { toast((destroyed && destroyed.error) || 'Destroy failed', 'error'); return; }
      toast(window.t("sandboxes.toast.destroyed", "Sandbox destroyed"), "success");
    } else if (action === "reset") {
      if (!confirm(window.t("sandboxes.confirm.reset", "Reset this sandbox?"))) return;
      var reset = await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent) + "/reset", {
        method: "POST",
      });
      if (!reset || reset.error) { toast((reset && reset.error) || 'Reset failed', 'error'); return; }
      toast(window.t("sandboxes.toast.reset", "Sandbox reset"), "success");
    } else if (action === "cleanup") {
      var cleanup = await fetchJSON("/api/sandboxes/" + encodeURIComponent(agent) + "/cleanup", {
        method: "POST",
      });
      if (!cleanup || cleanup.error) { toast((cleanup && cleanup.error) || 'Cleanup failed', 'error'); return; }
      toast(window.t("sandboxes.toast.cleaned", "Sandbox cleaned"), "success");
    } else if (action === "browse") {
      var sbId = sandboxId;
      var data = await fetchJSON("/api/sandboxes/" + encodeURIComponent(sbId) + "/files");
      var files = (data && data.files) ? data.files : [];
      var html = files.map(function(f) {
        return '<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--glass-border)">' +
          '<span>' + (f.is_dir ? '📁' : '📄') + '</span>' +
          '<span style="flex:1">' + (f.name || '') + '</span>' +
          '<span class="meta-tag">' + (f.size ? fmtBytes(f.size) : '') + '</span>' +
          '</div>';
      }).join('');
      if (typeof openDetailDrawer === 'function') openDetailDrawer(window.t("sandboxes.drawer.files_title", "Sandbox Files") + ': ' + sbId, html || '<p>' + window.t("sandboxes.drawer.empty", "Empty sandbox") + '</p>');
      return;
    } else if (action === "log") {
      var sbId2 = sandboxId;
      var logData = await fetchJSON("/api/sandboxes/" + encodeURIComponent(sbId2) + "/log");
      var entries = (logData && logData.entries) ? logData.entries : (logData && logData.log) ? logData.log : [];
      var logHtml = '<pre style="max-height:400px;overflow:auto;font-size:0.82em;white-space:pre-wrap">' +
        (entries.length ? entries.map(function(e) { return typeof e === 'string' ? e : JSON.stringify(e); }).join('\n') : window.t("sandboxes.drawer.no_log", "No log entries")) +
        '</pre>';
      if (typeof openDetailDrawer === 'function') openDetailDrawer(window.t("sandboxes.drawer.log_title", "Execution Log") + ': ' + sbId2, logHtml);
      return;
    }
    loadSandboxes();
  }

  async function createSandbox() {
    var agentId = prompt(window.t("sandboxes.prompt.new_agent_id", "Agent ID for new sandbox:"));
    if (!agentId) return;
    await fetchJSON("/api/sandboxes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agentId }),
    });
    toast(window.t("sandboxes.toast.created", "Sandbox created"), "success");
    loadSandboxes();
  }

  async function loadExecLog(blockedOnly) {
    var list = document.getElementById("exec-log-list");
    if (!list) return;
    var url = "/api/shell-executions";
    if (blockedOnly) url += "?blocked_only=true";
    var data = await fetchJSON(url);
    if (!data) {
      list.innerHTML = '<p class="sandboxes-muted">' + window.t("sandboxes.exec_log.load_failed", "Failed to load execution log.") + '</p>';
      return;
    }
    var items = data.items || [];
    if (items.length === 0) {
      list.innerHTML = '<p class="sandboxes-muted">' + window.t("sandboxes.exec_log.empty", "No shell executions recorded.") + '</p>';
      return;
    }
    list.innerHTML = items.map(execRowHTML).join("");
  }

  function execRowHTML(ex) {
    var blocked = ex.blocked;
    var statusCls = blocked ? "exec-log-status--blocked" : "exec-log-status--ok";
    var statusTxt = blocked ? window.t("sandboxes.exec_log.status_blocked", "❌ BLOCKED") : "✅ " + (ex.exit_code || 0);
    return '<div class="exec-log-row">'
      + '<span class="exec-log-time">' + fmtTime(ex.created_at) + '</span>'
      + '<span class="exec-log-agent">' + (ex.agent_id || "—") + '</span>'
      + '<span class="exec-log-cmd" title="' + (ex.command || "") + '">' + (ex.command || "—") + '</span>'
      + '<span class="exec-log-status ' + statusCls + '">' + statusTxt + '</span>'
      + '<span class="exec-log-duration">' + fmtDuration(ex.duration_ms) + '</span>'
      + '</div>';
  }

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
