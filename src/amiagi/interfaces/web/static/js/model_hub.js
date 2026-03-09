(function () {
  "use strict";

  /* ── DOM refs ──────────────────────────────────────────────── */
  // Tabs
  const tabs       = document.querySelectorAll(".model-hub-tab");
  const panels     = document.querySelectorAll(".model-hub-tab-panel");
  // Local
  const statusDot  = document.getElementById("ollama-status-dot");
  const statusText = document.getElementById("ollama-status-text");
  const baseUrlEl  = document.getElementById("ollama-base-url");
  const countEl    = document.getElementById("ollama-model-count");
  const localGrid  = document.getElementById("local-model-grid");
  const localEmpty = document.getElementById("local-model-empty");
  const pullForm   = document.getElementById("model-pull-form");
  const pullInput  = document.getElementById("pull-model-name");
  const pullBtn    = document.getElementById("btn-pull");
  const pullStatus = document.getElementById("pull-status");
  // VRAM panel
  const vramPanel    = document.getElementById("vram-panel");
  const vramTotal    = document.getElementById("vram-total");
  const vramBar      = document.getElementById("vram-bar");
  const vramModelList = document.getElementById("vram-model-list");
  // Cloud
  const cloudForm   = document.getElementById("cloud-model-form");
  const cloudGrid   = document.getElementById("cloud-model-grid");
  const cloudEmpty  = document.getElementById("cloud-model-empty");
  const cloudStatus = document.getElementById("cloud-form-status");
  const providerSel = document.getElementById("cloud-provider");
  const cloudModel  = document.getElementById("cloud-model-name");
  const cloudUrl    = document.getElementById("cloud-base-url");
  const cloudKey    = document.getElementById("cloud-api-key");
  const cloudName   = document.getElementById("cloud-display-name");
  const testBtn     = document.getElementById("btn-cloud-test");
  // Shared
  const assignEl   = document.getElementById("model-assignments");
  const refreshBtn = document.getElementById("btn-refresh-models");
  // Benchmark tab
  const benchForm    = document.getElementById("bench-form");
  const benchSelect  = document.getElementById("bench-model-select");
  const benchPrompt  = document.getElementById("bench-prompt");
  const benchStatus  = document.getElementById("bench-status");
  const benchTbody   = document.getElementById("bench-tbody");
  const benchEmpty   = document.getElementById("bench-empty");

  /* ── Helpers ───────────────────────────────────────────────── */
  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }
  function fmtSize(s) {
    if (!s) return "—";
    return String(s);
  }
  function fmtMeta(value, fallback) {
    return value == null || value === '' ? (fallback || 'n/a') : value;
  }

  function notifyModelHub(message, level) {
    if (typeof showToast === 'function') {
      showToast(message, level || 'info');
    }
  }

  async function responseErrorMessage(response, fallback) {
    var payload = await response.json().catch(function () { return {}; });
    return payload.error || fallback;
  }

  /* ── Tab switching ─────────────────────────────────────────── */
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) { t.classList.remove("active"); });
      panels.forEach(function (p) { p.classList.remove("active"); });
      tab.classList.add("active");
      var panel = document.getElementById("panel-" + tab.dataset.tab);
      if (panel) panel.classList.add("active");
    });
  });

  /* ══════════════════════════════════════════════════════════
   *  LOCAL MODELS
   * ══════════════════════════════════════════════════════════ */

  async function fetchOllamaStatus() {
    const r = await fetch("/models/ollama/status");
    return r.ok ? r.json() : null;
  }
  async function fetchLocalModels() {
    try { const r = await fetch("/api/models/local"); return r.ok ? r.json() : null; }
    catch { return null; }
  }
  async function fetchModelCatalog() {
    try { const r = await fetch("/models"); return r.ok ? r.json() : null; }
    catch { return null; }
  }
  async function fetchVram() {
    try { const r = await fetch("/api/models/vram"); return r.ok ? r.json() : null; }
    catch { return null; }
  }

  function renderOllamaStatus(data) {
    if (!data) {
      statusDot.className = "status-dot --offline";
      statusText.textContent = "Offline";
      baseUrlEl.textContent = "—";
      countEl.textContent = "0";
      return;
    }
    var ok = data.available !== false && data.status !== "error";
    statusDot.className = "status-dot " + (ok ? "--online" : "--offline");
    statusText.textContent = ok ? "Online" : "Offline";
    baseUrlEl.textContent = data.base_url || "—";
    countEl.textContent = (data.models || []).length;
  }

  /* ── VRAM Monitor panel ──────────────────────────────────── */
  function fmtBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    var v = bytes;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(i > 1 ? 1 : 0) + " " + units[i];
  }

  function renderVramPanel(vram) {
    if (!vramPanel) return;
    if (!vram || !vram.models || vram.models.length === 0) {
      vramPanel.style.display = "none";
      return;
    }
    vramPanel.style.display = "";

    var totalVram = 0;
    var totalCapacity = 0;
    var chips = "";

    for (var i = 0; i < vram.models.length; i++) {
      var m = vram.models[i];
      var sz = m.size_vram || m.size || 0;
      totalVram += sz;
      chips +=
        '<span class="model-hub-vram-chip">' +
          esc(m.name) +
          ' <span class="vram-chip-size">' + fmtBytes(sz) + '</span>' +
        '</span>';
    }

    // Use GPU total if backend provides it, else estimate 2x loaded models
    totalCapacity = vram.gpu_total || (totalVram > 0 ? totalVram * 2 : 0);
    var pct = totalCapacity > 0 ? Math.min((totalVram / totalCapacity) * 100, 100) : 0;

    if (vramTotal) {
      vramTotal.textContent = fmtBytes(totalVram) + " / " + fmtBytes(totalCapacity);
    }
    if (vramBar) {
      vramBar.style.width = pct.toFixed(1) + "%";
      vramBar.className = "model-hub-vram-bar-inner" +
        (pct >= 90 ? " vram-danger" : pct >= 70 ? " vram-warn" : "");
    }
    if (vramModelList) {
      vramModelList.innerHTML = chips;
    }
  }

  function renderLocalGrid(models, vram) {
    localGrid.innerHTML = "";
    if (!models || models.length === 0) {
      localEmpty.style.display = "";
      localGrid.appendChild(localEmpty);
      renderVramPanel(null);
      return;
    }
    localEmpty.style.display = "none";
    var running = {};
    if (vram && vram.models) {
      for (var i = 0; i < vram.models.length; i++) running[vram.models[i].name] = vram.models[i];
    }
    renderVramPanel(vram);
    for (var j = 0; j < models.length; j++) {
      var m = models[j];
      var name = m.name || "unknown";
      var run  = running[name];
      var card = document.createElement("div");
      card.className = "glass-card model-hub-card";
      card.innerHTML =
        '<div class="model-hub-card-header">' +
          '<span class="model-hub-card-name">' + esc(name) + '</span>' +
          (run ? '<span class="model-hub-badge --running">running</span>' : '') +
        '</div>' +
        '<div class="model-hub-card-meta">' +
          '<span>Size: ' + esc(fmtSize(m.size)) + '</span>' +
          '<span class="meta-tag">Ctx: ' + fmtMeta(m.context_length, 'n/a') + (m.context_length ? '' : '') + '</span>' +
          '<span class="meta-tag">VRAM: ' + fmtMeta(m.vram_mb, 'n/a') + (m.vram_mb != null ? ' MB' : '') + '</span>' +
          '<span class="meta-tag">Cost: ' + (m.cost_per_1k == null ? 'local' : ('$' + Number(m.cost_per_1k).toFixed(4) + '/1k')) + '</span>' +
          (run ? '<span class="meta-tag meta-tag--accent">Loaded: ' + fmtBytes(run.size_vram || run.size || 0) + '</span>' : '') +
        '</div>' +
        '<div class="model-hub-card-actions">' +
          '<button class="glass-btn glass-btn--sm js-bench" data-name="' + esc(name) + '">Benchmark</button>' +
          (run ? '<button class="glass-btn glass-btn--sm glass-btn--ghost js-unload" data-name="' + esc(name) + '">Unload</button>' : '') +
          '<button class="glass-btn glass-btn--sm glass-btn--danger js-del-local" data-name="' + esc(name) + '">Delete</button>' +
        '</div>';
      localGrid.appendChild(card);
    }
  }

  /* Local actions */
  async function pullModel(name) {
    pullBtn.disabled = true;
    pullStatus.textContent = "Pulling " + name + "…";
    pullStatus.className = "model-hub-pull-status";
    try {
      var r = await fetch("/api/models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: name }),
      });

      if (!r.ok) {
        var err = await r.json().catch(function () { return {}; });
        pullStatus.textContent = err.error || "Pull failed (HTTP " + r.status + ")";
        pullStatus.className = "model-hub-pull-status --err";
        pullBtn.disabled = false;
        return;
      }

      // Stream SSE progress
      var reader = r.body.getReader();
      var decoder = new TextDecoder();
      var buf = "";

      while (true) {
        var result = await reader.read();
        if (result.done) break;
        buf += decoder.decode(result.value, { stream: true });

        var lines = buf.split("\n");
        buf = lines.pop() || "";

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line.startsWith("data: ")) continue;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.done) {
              pullStatus.textContent = "Pull complete";
              pullStatus.className = "model-hub-pull-status --ok";
              pullInput.value = "";
              loadLocal();
            } else if (evt.error) {
              pullStatus.textContent = "Error: " + evt.error;
              pullStatus.className = "model-hub-pull-status --err";
            } else {
              var pct = evt.total > 0
                ? " (" + Math.round((evt.completed / evt.total) * 100) + "%)"
                : "";
              pullStatus.textContent = (evt.status || "downloading") + pct;
              pullStatus.className = "model-hub-pull-status";
            }
          } catch (e2) { /* ignore parse error */ }
        }
      }
    } catch (e) {
      pullStatus.textContent = "Error: " + e.message;
      pullStatus.className = "model-hub-pull-status --err";
    }
    pullBtn.disabled = false;
  }
  async function benchModel(name) {
    try {
      var r = await fetch("/api/models/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: name }),
      });
      var d = await r.json();
      if (r.ok && d.ok) {
        notifyModelHub("Benchmark ready: " + (d.tokens_per_second || 0).toFixed(1) + " tok/s", "success");
      } else {
        notifyModelHub("Benchmark failed: " + (d.error || "unknown"), "error");
      }
    } catch (e) {
      notifyModelHub("Benchmark failed", "error");
    }
  }
  async function deleteLocalModel(name) {
    if (!confirm("Delete local model " + name + "?")) return;
    try {
      var r = await fetch("/api/models/local/" + encodeURIComponent(name), { method: "DELETE" });
      if (r.ok) {
        notifyModelHub("Local model deleted", "success");
        loadLocal();
      } else {
        notifyModelHub(await responseErrorMessage(r, "Delete failed"), "error");
      }
    } catch (e) {
      notifyModelHub("Delete failed", "error");
    }
  }

  /* ── M5: Model Queue ──────────────────────────────────── */
  async function loadModelQueue() {
    var queueEl = document.getElementById('model-queue');
    if (!queueEl) return;
    try {
      var r = await fetch('/api/models/queue');
      if (!r.ok) { queueEl.innerHTML = ''; return; }
      var data = await r.json();
      var items = data.queue || data.items || [];
      if (items.length === 0) { queueEl.innerHTML = '<p class="model-hub-empty">No models in queue</p>'; return; }
      var html = '<h4>Model Queue</h4>';
      for (var i = 0; i < items.length; i++) {
        var q = items[i];
        html += '<div class="glass-card" style="padding:var(--space-2);margin-bottom:var(--space-1)">' +
          '<span class="model-hub-card-name">' + esc(q.model_name || '?') + '</span> ' +
          '<span class="meta-tag">' + esc(q.agent_id || '') + '</span> ' +
          '<span class="meta-tag">waiting ' + esc(q.waiting_since || '') + '</span>' +
          '</div>';
      }
      queueEl.innerHTML = html;
    } catch (e) { /* ignore */ }
  }

  /* ── M6: Unload model (free VRAM) ──────────────────────── */
  async function unloadModel(name) {
    if (!confirm('Unload ' + name + ' from VRAM?')) return;
    try {
      var r = await fetch('/api/models/' + encodeURIComponent(name) + '/unload', { method: 'POST' });
      if (r.ok) {
        notifyModelHub('Model unloaded', 'success');
        loadLocal();
      } else {
        notifyModelHub(await responseErrorMessage(r, 'Unload failed'), 'error');
      }
    } catch (e) {
      notifyModelHub('Unload failed', 'error');
    }
  }

  /* ── M4: JSON Config Editor ────────────────────────────── */
  async function loadModelConfig() {
    var textarea = document.getElementById('model-config-json');
    if (!textarea) return;
    try {
      var r = await fetch('/models/config');
      if (r.ok) {
        var data = await r.json();
        textarea.value = JSON.stringify(data, null, 2);
      }
    } catch (e) { /* ignore */ }
  }
  function initConfigEditor() {
    var saveBtn = document.getElementById('btn-save-config');
    if (!saveBtn) return;
    saveBtn.addEventListener('click', async function () {
      var textarea = document.getElementById('model-config-json');
      try {
        var parsed = JSON.parse(textarea.value);
        var r = await fetch('/models/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(parsed)
        });
        var d = await r.json();
        if (d.ok) {
          if (typeof showToast === 'function') showToast('Config saved', 'success');
        } else {
          if (typeof showToast === 'function') showToast(d.error || 'Save failed', 'error');
        }
      } catch (e) {
        if (typeof showToast === 'function') showToast('Invalid JSON: ' + e.message, 'error');
      }
    });
    loadModelConfig();
  }
  initConfigEditor();

  pullForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var name = pullInput.value.trim();
    if (name) pullModel(name);
  });
  localGrid.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    var name = btn.dataset.name;
    if (!name) return;
    if (btn.classList.contains("js-bench")) benchModel(name);
    else if (btn.classList.contains("js-del-local")) deleteLocalModel(name);
    else if (btn.classList.contains("js-unload")) unloadModel(name);
  });

  async function loadLocal() {
    var [status, local, vram] = await Promise.all([
      fetchOllamaStatus(), fetchLocalModels(), fetchVram(),
    ]);
    renderOllamaStatus(status);
    var models = (local && local.models) ? local.models : [];
    renderLocalGrid(models, vram);
  }

  /* ══════════════════════════════════════════════════════════
   *  CLOUD MODELS
   * ══════════════════════════════════════════════════════════ */

  var defaultUrls = {
    openai: "https://api.openai.com/v1",
    google: "https://generativelanguage.googleapis.com/v1beta",
    anthropic: "https://api.anthropic.com",
    custom: "",
  };

  providerSel.addEventListener("change", function () {
    var prov = providerSel.value;
    cloudUrl.placeholder = defaultUrls[prov] || "https://...";
    // auto-fill URL only if empty or still a default
    var cur = cloudUrl.value.trim();
    var isDefault = !cur || Object.values(defaultUrls).indexOf(cur) !== -1;
    if (isDefault && defaultUrls[prov]) cloudUrl.value = defaultUrls[prov];
  });

  async function fetchCloudModels() {
    try { var r = await fetch("/api/models/cloud"); return r.ok ? r.json() : null; }
    catch { return null; }
  }

  function renderCloudGrid(data) {
    cloudGrid.innerHTML = "";
    var models = data && data.models ? data.models : [];
    if (models.length === 0) {
      cloudEmpty.style.display = "";
      cloudGrid.appendChild(cloudEmpty);
      return;
    }
    cloudEmpty.style.display = "none";
    for (var i = 0; i < models.length; i++) {
      var m = models[i];
      var card = document.createElement("cloud-model-card");
      card.setAttribute("provider", m.provider || "");
      card.setAttribute("model", m.model || "");
      card.setAttribute("display-name", m.display_name || m.model || "");
      card.setAttribute("base-url", m.base_url || "");
      card.setAttribute("api-key", m.api_key || "");
      card.setAttribute("enabled", m.enabled !== false ? "true" : "false");
      cloudGrid.appendChild(card);
    }
  }

  /* Cloud actions */
  async function testCloudConnection() {
    var prov = providerSel.value;
    var key  = cloudKey.value.trim();
    var url  = cloudUrl.value.trim();
    if (!key) {
      cloudStatus.textContent = "API key is required";
      cloudStatus.className = "model-hub-cloud-status --err";
      return;
    }
    cloudStatus.textContent = "Testing connection…";
    cloudStatus.className = "model-hub-cloud-status";
    testBtn.disabled = true;
    try {
      var r = await fetch("/api/models/cloud/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: prov, api_key: key, base_url: url }),
      });
      var d = await r.json();
      if (d.ok) {
        cloudStatus.textContent = "Connected — " + d.latency_ms + "ms, " + (d.models || []).length + " models available";
        cloudStatus.className = "model-hub-cloud-status --ok";
      } else {
        cloudStatus.textContent = (d.error || "Connection failed") + (d.detail ? ": " + d.detail.slice(0, 120) : "");
        cloudStatus.className = "model-hub-cloud-status --err";
      }
    } catch (e) {
      cloudStatus.textContent = "Error: " + e.message;
      cloudStatus.className = "model-hub-cloud-status --err";
    }
    testBtn.disabled = false;
  }

  async function saveCloudModel(e) {
    e.preventDefault();
    var prov  = providerSel.value;
    var model = cloudModel.value.trim();
    var key   = cloudKey.value.trim();
    var url   = cloudUrl.value.trim();
    var dname = cloudName.value.trim() || (prov + "/" + model);
    if (!model || !key) {
      cloudStatus.textContent = "Model and API key are required";
      cloudStatus.className = "model-hub-cloud-status --err";
      return;
    }
    cloudStatus.textContent = "Saving…";
    cloudStatus.className = "model-hub-cloud-status";
    try {
      var r = await fetch("/api/models/cloud", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: prov, model: model, api_key: key,
          base_url: url, display_name: dname, enabled: true,
        }),
      });
      var d = await r.json();
      if (d.ok) {
        cloudStatus.textContent = "Saved";
        cloudStatus.className = "model-hub-cloud-status --ok";
        cloudModel.value = ""; cloudKey.value = ""; cloudName.value = "";
        loadCloud();
      } else {
        cloudStatus.textContent = d.error || "Save failed";
        cloudStatus.className = "model-hub-cloud-status --err";
      }
    } catch (e2) {
      cloudStatus.textContent = "Error: " + e2.message;
      cloudStatus.className = "model-hub-cloud-status --err";
    }
  }

  async function deleteCloudModel(provider, model) {
    if (!confirm("Remove cloud model " + provider + "/" + model + "?")) return;
    try {
      var r = await fetch(
        "/api/models/cloud/" + encodeURIComponent(provider) + "/" + encodeURIComponent(model),
        { method: "DELETE" }
      );
      if (r.ok) {
        notifyModelHub("Cloud model deleted", "success");
        loadCloud();
      } else {
        notifyModelHub(await responseErrorMessage(r, "Delete failed"), "error");
      }
    } catch (e) {
      notifyModelHub("Delete failed", "error");
    }
  }

  cloudForm.addEventListener("submit", saveCloudModel);
  testBtn.addEventListener("click", testCloudConnection);
  cloudGrid.addEventListener("cloud-model-delete", function (e) {
    deleteCloudModel(e.detail.provider, e.detail.model);
  });
  cloudGrid.addEventListener("cloud-model-test", function (e) {
    // Fill form and trigger test
    providerSel.value = e.detail.provider;
    cloudUrl.value = e.detail.base_url || "";
    cloudKey.value = e.detail.api_key || "";
    testCloudConnection();
  });

  async function loadCloud() {
    var data = await fetchCloudModels();
    renderCloudGrid(data);
  }

  /* ══════════════════════════════════════════════════════════
   *  BENCHMARK TAB
   * ══════════════════════════════════════════════════════════ */

  var benchResults = [];

  async function populateBenchModelSelect() {
    if (!benchSelect) return;
    var current = benchSelect.value;
    var opts = '<option value="">— select model —</option>';
    try {
      var d = await fetchModelCatalog();
      var models = (d && d.ollama_models) ? d.ollama_models : [];
      for (var i = 0; i < models.length; i++) {
        var name = models[i] || "";
        var sel = (name === current) ? " selected" : "";
        opts += '<option value="' + esc(name) + '"' + sel + '>' + esc(name) + '</option>';
      }
      if (current && models.indexOf(current) === -1) {
        opts += '<option value="' + esc(current) + '" selected>' + esc(current) + '</option>';
      }
    } catch (e) { /* ignore */ }
    benchSelect.innerHTML = opts;
  }

  function renderBenchResults() {
    if (!benchTbody) return;
    benchTbody.innerHTML = "";
    if (benchResults.length === 0) {
      if (benchEmpty) benchEmpty.style.display = "";
      return;
    }
    if (benchEmpty) benchEmpty.style.display = "none";
    for (var i = 0; i < benchResults.length; i++) {
      var r = benchResults[i];
      var row = document.createElement("tr");
      row.innerHTML =
        '<td>' + esc(r.model) + '</td>' +
        '<td class="bench-tps">' + (r.tokens_per_second || 0).toFixed(1) + '</td>' +
        '<td>' + (r.tokens || 0) + '</td>' +
        '<td>' + (r.elapsed_seconds || 0).toFixed(2) + 's</td>' +
        '<td class="bench-preview">' + esc((r.response_preview || "").slice(0, 80)) + '</td>';
      benchTbody.appendChild(row);
    }
  }

  async function runBenchmark(modelName, prompt) {
    if (!modelName) return;
    if (benchStatus) {
      benchStatus.textContent = "Running benchmark for " + modelName + "…";
      benchStatus.className = "model-hub-bench-status";
    }
    try {
      var r = await fetch("/api/models/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName, prompt: prompt || "Say hello in one sentence." }),
      });
      var d = await r.json();
      if (r.ok && d.ok) {
        benchResults.unshift(d);
        if (benchResults.length > 20) benchResults.pop();
        renderBenchResults();
        if (benchStatus) {
          benchStatus.textContent = d.tokens_per_second.toFixed(1) + " tok/s — " + d.elapsed_seconds + "s";
          benchStatus.className = "model-hub-bench-status --ok";
        }
      } else {
        if (benchStatus) {
          benchStatus.textContent = d.error || "Benchmark failed";
          benchStatus.className = "model-hub-bench-status --err";
        }
      }
    } catch (e) {
      if (benchStatus) {
        benchStatus.textContent = "Error: " + e.message;
        benchStatus.className = "model-hub-bench-status --err";
      }
    }
  }

  if (benchForm) {
    benchForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var model = benchSelect ? benchSelect.value : "";
      var prompt = benchPrompt ? benchPrompt.value.trim() : "";
      if (model) runBenchmark(model, prompt);
    });
  }

  /* ══════════════════════════════════════════════════════════
   *  ASSIGNMENTS (shared)
   * ══════════════════════════════════════════════════════════ */

  async function loadAssignments() {
    try {
      var data = await fetchModelCatalog();
      if (!data) return;
      if (!assignEl) return;
      var agents = data.agent_assignments || [];
      if (agents.length === 0) { assignEl.innerHTML = ""; return; }
      var html = "";
      for (var i = 0; i < agents.length; i++) {
        var a = agents[i];
        html +=
          '<div class="glass-card model-hub-assignment-card">' +
            '<span class="model-hub-assignment-agent">' + esc(a.name || a.agent_id || "—") + '</span>' +
            '<span class="model-hub-assignment-model">' + esc(a.model_name || "—") + '</span>' +
            '<span class="model-hub-assignment-source">' + esc(a.model_backend || a.source || "ollama") + '</span>' +
          '</div>';
      }
      assignEl.innerHTML = html;
    } catch (e) { /* ignore */ }
  }

  /* ── Load all ──────────────────────────────────────────────── */
  async function loadAll() {
    await Promise.all([loadLocal(), loadCloud(), loadAssignments(), populateBenchModelSelect(), loadModelQueue()]);
  }
  if (refreshBtn) refreshBtn.addEventListener("click", loadAll);
  loadAll();
  setInterval(loadAll, 30000);
})();
