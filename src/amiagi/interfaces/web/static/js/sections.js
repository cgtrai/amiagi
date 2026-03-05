/**
 * sections.js — Section tabs (Monitoring / Teams / Models)
 *
 * Handles switching between dashboard sections and data loading
 * for Teams and Model Configuration views.
 */
(function () {
  "use strict";

  // ── Section switching ─────────────────────────────────────────

  window.switchSection = function switchSection(sectionId) {
    document.querySelectorAll(".section-tab").forEach(function (tab) {
      tab.classList.toggle("active", tab.dataset.section === sectionId);
    });
    document.querySelectorAll(".section-content").forEach(function (sec) {
      sec.classList.toggle("active", sec.id === "section-" + sectionId);
    });

    // Lazy-load data on first visit
    if (sectionId === "teams" && !window._teamsLoaded) {
      loadTeams();
      window._teamsLoaded = true;
    }
    if (sectionId === "prompts" && !window._promptsLoaded) {
      loadPrompts();
      window._promptsLoaded = true;
    }
    if (sectionId === "models" && !window._modelsLoaded) {
      loadModelConfig();
      loadOllamaStatus();
      window._modelsLoaded = true;
    }
    if (sectionId === "memory" && !window._memoryLoaded) {
      loadMemoryItems();
      window._memoryLoaded = true;
    }
    if (sectionId === "scheduled" && !window._scheduledLoaded) {
      loadCronJobs();
      window._scheduledLoaded = true;
    }
  };

  // ── Teams ─────────────────────────────────────────────────────

  async function loadTeams() {
    var grid = document.getElementById("teams-grid");
    if (!grid) return;

    try {
      var resp = await fetch("/api/teams");
      var data = await resp.json();
      var teams = data.teams || [];

      if (teams.length === 0) {
        grid.innerHTML = '<div class="sidebar-empty">No teams configured.</div>';
        return;
      }

      grid.innerHTML = teams.map(function (t) {
        var memberCount = (t.members || []).length;
        return (
          '<div class="team-card">' +
            '<h4>' + escHtml(t.name || t.team_id) + '</h4>' +
            '<div class="team-meta">' +
              '<div>ID: ' + escHtml(t.team_id) + '</div>' +
              '<div>Members: ' + memberCount + '</div>' +
              (t.workflow ? '<div>Workflow: ' + escHtml(t.workflow) + '</div>' : '') +
            '</div>' +
          '</div>'
        );
      }).join("");
    } catch (err) {
      grid.innerHTML = '<div class="sidebar-empty">Failed to load teams.</div>';
    }
  }

  // ── Model config ──────────────────────────────────────────────

  async function loadModelConfig() {
    try {
      var resp = await fetch("/models/config");
      if (!resp.ok) return;
      var cfg = await resp.json();

      setVal("cfg-polluks-model", cfg.polluks_model || "");
      setVal("cfg-polluks-source", cfg.polluks_source || "ollama");
      setVal("cfg-kastor-model", cfg.kastor_model || "");
      setVal("cfg-kastor-source", cfg.kastor_source || "ollama");
    } catch (_) {
      // ignore
    }
  }

  window.saveModelConfig = async function saveModelConfig() {
    var body = {
      polluks_model: getVal("cfg-polluks-model"),
      polluks_source: getVal("cfg-polluks-source"),
      kastor_model: getVal("cfg-kastor-model"),
      kastor_source: getVal("cfg-kastor-source"),
    };
    var statusEl = document.getElementById("model-save-status");

    try {
      var resp = await fetch("/models/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        if (statusEl) {
          statusEl.textContent = "Saved ✓";
          setTimeout(function () { statusEl.textContent = ""; }, 3000);
        }
      } else {
        if (statusEl) statusEl.textContent = "Save failed";
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = "Network error";
    }
  };

  // ── Ollama status ─────────────────────────────────────────────

  async function loadOllamaStatus() {
    var dot = document.getElementById("ollama-dot");
    var text = document.getElementById("ollama-status-text");
    var list = document.getElementById("ollama-models-list");

    try {
      var resp = await fetch("/models/ollama/status");
      var data = await resp.json();

      if (data.available) {
        if (dot) dot.classList.add("online");
        if (text) text.textContent = "Ollama online — " + (data.models || []).length + " models";
        if (list) {
          var models = data.models || [];
          list.innerHTML = models.length
            ? models.map(function (m) { return "<div>• " + escHtml(m) + "</div>"; }).join("")
            : "No models pulled.";
        }
      } else {
        if (text) text.textContent = "Ollama offline" + (data.error ? ": " + data.error : "");
        if (list) list.textContent = "Ollama not available";
      }
    } catch (err) {
      if (text) text.textContent = "Cannot reach Ollama";
      if (list) list.textContent = "—";
    }
  }

  // ── Prompts ────────────────────────────────────────────────────

  var _allPrompts = [];

  async function loadPrompts() {
    var list = document.getElementById("prompts-list");
    if (!list) return;

    try {
      var resp = await fetch("/prompts");
      _allPrompts = await resp.json();
      renderPromptList(_allPrompts);
    } catch (err) {
      list.innerHTML = '<div class="sidebar-empty">Failed to load prompts.</div>';
    }
  }

  function renderPromptList(prompts) {
    var list = document.getElementById("prompts-list");
    if (!list) return;

    if (prompts.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">No prompts found.</div>';
      return;
    }

    list.innerHTML = prompts.map(function (p) {
      var paramBadge = p.parameters && p.parameters.length
        ? ' <span class="glass-badge glass-badge--sm">' + p.parameters.length + ' params</span>'
        : '';
      var tags = (p.tags || []).map(function (t) {
        return '<span class="glass-badge glass-badge--sm">' + escHtml(t) + '</span>';
      }).join(' ');
      return (
        '<div class="prompt-item" data-id="' + p.id + '" onclick="selectPrompt(\'' + p.id + '\')">' +
          '<div class="prompt-item-title">' + escHtml(p.title) + paramBadge + '</div>' +
          (tags ? '<div class="prompt-item-tags">' + tags + '</div>' : '') +
        '</div>'
      );
    }).join("");
  }

  window.filterPrompts = function () {
    var q = (document.getElementById("prompt-search").value || "").toLowerCase();
    var filtered = _allPrompts.filter(function (p) {
      return (p.title || "").toLowerCase().indexOf(q) !== -1 ||
        (p.tags || []).some(function (t) { return t.toLowerCase().indexOf(q) !== -1; });
    });
    renderPromptList(filtered);
  };

  window.selectPrompt = function (id) {
    var p = _allPrompts.find(function (x) { return x.id === id; });
    var detail = document.getElementById("prompt-detail");
    if (!p || !detail) return;

    // Highlight selected item
    document.querySelectorAll(".prompt-item").forEach(function (el) {
      el.classList.toggle("prompt-item--active", el.dataset.id === id);
    });

    var params = p.parameters || [];
    var hasParams = params.length > 0;

    var html =
      '<h3>' + escHtml(p.title) + '</h3>' +
      '<pre class="prompt-template-preview">' + escHtml(p.template) + '</pre>';

    if (hasParams) {
      html += '<div class="prompt-params-form">' +
        '<h4>Parameters</h4>' +
        params.map(function (name) {
          return '<div class="field">' +
            '<label for="pp-' + escHtml(name) + '">' + escHtml(name) + '</label>' +
            '<input id="pp-' + escHtml(name) + '" data-param="' + escHtml(name) +
            '" placeholder="Enter ' + escHtml(name) + '…" oninput="previewPromptRender(\'' + id + '\')"/>' +
          '</div>';
        }).join('') +
      '</div>' +
      '<div class="prompt-rendered-preview">' +
        '<h4>Preview</h4>' +
        '<pre id="prompt-rendered-text" class="prompt-template-preview prompt-template-preview--rendered">' +
          escHtml(p.template) +
        '</pre>' +
      '</div>';
    }

    html += '<div class="prompt-actions">' +
      '<button class="glass-btn glass-btn--primary" onclick="usePrompt(\'' + id + '\')">' +
        (hasParams ? '✓ Use Rendered Prompt' : '✓ Use Prompt') +
      '</button>' +
    '</div>';

    detail.innerHTML = html;
  };

  window.previewPromptRender = function (id) {
    var p = _allPrompts.find(function (x) { return x.id === id; });
    if (!p) return;
    var rendered = p.template;
    document.querySelectorAll("[data-param]").forEach(function (el) {
      var name = el.dataset.param;
      var val = el.value || '{' + name + '}';
      rendered = rendered.split('{' + name + '}').join(val);
    });
    var pre = document.getElementById("prompt-rendered-text");
    if (pre) pre.textContent = rendered;
  };

  window.usePrompt = function (id) {
    var p = _allPrompts.find(function (x) { return x.id === id; });
    if (!p) return;

    // Gather parameter values
    var values = {};
    document.querySelectorAll("[data-param]").forEach(function (el) {
      values[el.dataset.param] = el.value;
    });

    // Call the use endpoint (increments count + renders server-side)
    fetch("/prompts/" + id + "/use", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: values }),
    }).then(function (r) { return r.json(); })
    .then(function (data) {
      var rendered = data.rendered || p.template;
      // Insert into mobile chat input or dispatch event
      var chatInput = document.getElementById("mobile-chat-input");
      if (chatInput) {
        chatInput.value = rendered;
        chatInput.focus();
      }
      // Also dispatch event for chat-stream integration
      document.dispatchEvent(new CustomEvent("prompt.use", {
        detail: { prompt_id: id, rendered: rendered },
        bubbles: true,
      }));
    }).catch(function () {});
  };

  // ── Memory browser ──────────────────────────────────────────────

  var _allMemoryItems = [];

  async function loadMemoryItems() {
    var list = document.getElementById("memory-list");
    if (!list) return;

    try {
      var resp = await fetch("/api/memory?limit=500");
      var data = await resp.json();
      _allMemoryItems = data.items || [];
      renderMemoryList(_allMemoryItems);
      var totalEl = document.getElementById("memory-total");
      if (totalEl) totalEl.textContent = "Total items: " + (data.total || 0);
    } catch (err) {
      list.innerHTML = '<div class="sidebar-empty">Failed to load memory.</div>';
    }
  }

  function renderMemoryList(items) {
    var list = document.getElementById("memory-list");
    if (!list) return;

    if (items.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">No memory items.</div>';
      return;
    }

    list.innerHTML = items.map(function (m, idx) {
      var tags = (m.tags || []).map(function (t) {
        return '<span class="glass-badge glass-badge--sm">' + escHtml(t) + '</span>';
      }).join(' ');
      var ts = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString() : '';
      return (
        '<div class="memory-item">' +
          '<div class="memory-item-header">' +
            '<strong>' + escHtml(m.agent_id) + '</strong>' +
            ' <span class="memory-task-id">task: ' + escHtml(m.task_id) + '</span>' +
            '<span class="memory-ts">' + ts + '</span>' +
            '<button class="glass-btn glass-btn--ghost glass-btn--sm" onclick="deleteMemoryItem(' + idx + ')" title="Delete">×</button>' +
          '</div>' +
          '<div class="memory-item-findings">' + escHtml(m.key_findings) + '</div>' +
          (tags ? '<div class="memory-item-tags">' + tags + '</div>' : '') +
        '</div>'
      );
    }).join("");
  }

  window.filterMemory = function () {
    var q = (document.getElementById("memory-filter").value || "").toLowerCase();
    var filtered = _allMemoryItems.filter(function (m) {
      return (m.agent_id || "").toLowerCase().indexOf(q) !== -1 ||
        (m.task_id || "").toLowerCase().indexOf(q) !== -1 ||
        (m.key_findings || "").toLowerCase().indexOf(q) !== -1 ||
        (m.tags || []).some(function (t) { return t.toLowerCase().indexOf(q) !== -1; });
    });
    renderMemoryList(filtered);
  };

  window.deleteMemoryItem = async function (idx) {
    try {
      await fetch("/api/memory/" + idx, { method: "DELETE" });
      _allMemoryItems.splice(idx, 1);
      renderMemoryList(_allMemoryItems);
    } catch (_) {}
  };

  window.clearAllMemory = async function () {
    if (!confirm("Clear all cross-agent memory?")) return;
    try {
      await fetch("/api/memory", { method: "DELETE" });
      _allMemoryItems = [];
      renderMemoryList([]);
    } catch (_) {}
  };

  // ── Cron / Scheduled tasks ─────────────────────────────────────

  async function loadCronJobs() {
    var list = document.getElementById("cron-jobs-list");
    if (!list) return;

    try {
      var resp = await fetch("/api/cron");
      var jobs = await resp.json();

      if (!jobs.length) {
        list.innerHTML = '<div class="sidebar-empty">No scheduled jobs.</div>';
        return;
      }

      list.innerHTML = jobs.map(function (j) {
        var statusBadge = j.enabled
          ? '<span class="glass-badge glass-badge--sm glass-badge--ok">enabled</span>'
          : '<span class="glass-badge glass-badge--sm">disabled</span>';
        var lastRun = j.last_run ? new Date(j.last_run).toLocaleString() : 'never';
        return (
          '<div class="cron-job-card">' +
            '<div class="cron-job-header">' +
              '<strong>' + escHtml(j.name || j.id) + '</strong> ' + statusBadge +
              '<button class="glass-btn glass-btn--ghost glass-btn--sm" onclick="deleteCronJob(\'' + j.id + '\')" title="Delete">×</button>' +
            '</div>' +
            '<div class="cron-job-meta">' +
              '<code>' + escHtml(j.cron_expr) + '</code>' +
              ' — last run: ' + lastRun +
            '</div>' +
            '<div class="cron-job-task">' + escHtml(j.task_title || '') + '</div>' +
          '</div>'
        );
      }).join("");
    } catch (err) {
      list.innerHTML = '<div class="sidebar-empty">Failed to load scheduled jobs.</div>';
    }
  }

  window.showCronForm = function () {
    var panel = document.getElementById("cron-form-panel");
    if (panel) panel.classList.remove("cron-form-panel--hidden");
  };
  window.hideCronForm = function () {
    var panel = document.getElementById("cron-form-panel");
    if (panel) panel.classList.add("cron-form-panel--hidden");
  };

  window.saveCronJob = async function () {
    var body = {
      name: getVal("cron-name"),
      cron_expr: getVal("cron-expr"),
      task_title: getVal("cron-task-title"),
      task_description: getVal("cron-task-desc"),
    };
    try {
      var resp = await fetch("/api/cron", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        hideCronForm();
        window._scheduledLoaded = false;
        loadCronJobs();
      }
    } catch (_) {}
  };

  window.deleteCronJob = async function (id) {
    if (!confirm("Delete this scheduled job?")) return;
    try {
      await fetch("/api/cron/" + id, { method: "DELETE" });
      window._scheduledLoaded = false;
      loadCronJobs();
    } catch (_) {}
  };

  // ── Helpers ───────────────────────────────────────────────────

  function escHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function getVal(id) {
    var el = document.getElementById(id);
    return el ? el.value : "";
  }

  function setVal(id, val) {
    var el = document.getElementById(id);
    if (el) el.value = val;
  }
})();
