/**
 * Knowledge Management — client-side logic.
 * Manages knowledge bases, sources, search, pipeline, stats.
 */
(function () {
  "use strict";

  // ── Tab switching ────────────────────────────────────────
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".knowledge-tab").forEach((t) => (t.hidden = true));
      document.getElementById(`tab-${tab}`).hidden = false;
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadTab(tab);
    });
  });

  function loadTab(tab) {
    switch (tab) {
      case "overview": loadBases(); break;
      case "sources": loadSources(); break;
      case "explore": break; // search on demand
      case "pipeline": loadPipeline(); break;
      case "stats": loadStats(); break;
    }
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

  function badge(label, variant) {
    return `<span class="glass-badge ${variant || 'badge-muted'}">${label}</span>`;
  }

  function sourceTypeLabel(type) {
    if (type === "dir") return badge("Directory", "badge-muted");
    if (type === "url") return badge("URL", "badge-working");
    return badge("File", "badge-idle");
  }

  function sourceStatusLabel(status) {
    const normalized = String(status || "pending").toLowerCase();
    if (normalized === "indexed") return badge("Indexed", "badge-success");
    if (normalized === "error") return badge("Error", "badge-error");
    if (normalized === "partial") return badge("Partial", "badge-working");
    return badge("Pending", "badge-muted");
  }

  // ── Load bases ───────────────────────────────────────────
  async function loadBases() {
    const el = document.getElementById("bases-list");
    try {
      const res = await fetch("/api/knowledge/bases");
      const data = await res.json();
      const bases = data.bases || [];

      if (bases.length === 0) {
        el.innerHTML = '<p class="empty-state">No knowledge bases configured.</p>';
        return;
      }

      el.innerHTML = bases.map(renderBaseCard).join("");

      // Wire action buttons
      el.querySelectorAll("[data-action='reindex']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const res = await fetch(`/api/knowledge/bases/${btn.dataset.baseId}/reindex`, { method: "POST" });
          if (res.ok) {
            btn.textContent = "Reindexing...";
            notify("Reindex started", "success");
            setTimeout(() => loadBases(), 2000);
            return;
          }
          notify(await responseErrorMessage(res, "Failed to start reindex"), "error");
        });
      });
      el.querySelectorAll("[data-action='delete-base']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("Delete this knowledge base?")) return;
          const res = await fetch(`/api/knowledge/bases/${btn.dataset.baseId}`, { method: "DELETE" });
          if (res.ok) {
            notify("Knowledge base deleted", "success");
            loadBases();
          } else {
            notify(await responseErrorMessage(res, "Delete failed"), "error");
          }
        });
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderBaseCard(base) {
    const sources_count = (base.sources || []).length;
    const agentsUsing = (base.agents_using || []).length;
    const totalSize = formatBytes(base.total_size_bytes || 0);
    const lastUpdated = base.last_updated ? new Date(base.last_updated).toLocaleDateString() : "Never";
    return `<div class="glass-card base-card">
      <div class="base-header">
        <strong>${base.name}</strong>
        ${badge(base.is_global ? "Global" : "Custom", base.is_global ? "badge-working" : "badge-muted")}
      </div>
      <div class="base-meta">
        <span class="meta-tag">${base.document_count || base.chunks_count || 0} docs</span>
        <span class="meta-tag">${totalSize}</span>
        <span class="meta-tag">Engine: ${base.engine || "tfidf"}</span>
        <span class="meta-tag">Agents: ${agentsUsing}</span>
        <span class="meta-tag">Updated: ${lastUpdated}</span>
        <span class="meta-tag">Sources: ${sources_count}</span>
      </div>
      <p class="base-desc">${base.description || ""}</p>
      <div class="base-actions">
        ${base.supports_reindex ? `<button class="btn btn-sm btn-outline" data-action="reindex" data-base-id="${base.id}">Reindex</button>` : `<span class="meta-tag">Config only</span>`}
        ${!base.is_global ? `<button class="btn btn-sm btn-danger" data-action="delete-base" data-base-id="${base.id}">Delete</button>` : ""}
      </div>
    </div>`;
  }

  // ── Load sources ─────────────────────────────────────────
  async function loadSources() {
    const tbody = document.getElementById("sources-body");
    try {
      const res = await fetch("/api/knowledge/bases");
      const data = await res.json();
      const bases = data.bases || [];

      let rows = "";
      for (const base of bases) {
        for (const src of base.sources || []) {
          const indexed = src.indexed_at ? new Date(src.indexed_at * 1000).toLocaleString() : "—";
          rows += `<tr>
            <td>${sourceTypeLabel(src.type)}</td>
            <td>${escapeHtml(src.path)}</td>
            <td>${src.chunks_count || 0}</td>
            <td>${sourceStatusLabel(src.status)}</td>
            <td>${indexed}</td>
            <td><button class="btn btn-sm btn-danger" data-action="remove-source"
                  data-base-id="${base.id}" data-source-id="${src.id}">Remove</button></td>
          </tr>`;
        }
      }

      tbody.innerHTML = rows || '<tr><td colspan="6" class="empty-state">No sources added yet.</td></tr>';

      // Wire remove buttons
      tbody.querySelectorAll("[data-action='remove-source']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await fetch(`/api/knowledge/bases/${btn.dataset.baseId}/sources/${btn.dataset.sourceId}`, { method: "DELETE" });
          loadSources();
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="error-state">Error: ${err.message}</td></tr>`;
    }
  }

  // ── Explore (search) ─────────────────────────────────────
  document.getElementById("btn-explore-search")?.addEventListener("click", doExploreSearch);
  document.getElementById("explore-query")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doExploreSearch();
  });

  async function doExploreSearch() {
    const query = document.getElementById("explore-query")?.value.trim();
    if (!query) return;
    const baseId = document.getElementById("explore-base-select")?.value || "global";
    const top = document.getElementById("explore-top")?.value || "5";
    const el = document.getElementById("explore-results");

    try {
      const res = await fetch(`/api/knowledge/bases/${baseId}/search?q=${encodeURIComponent(query)}&top=${top}`);
      if (!res.ok) {
        el.innerHTML = `<p class="error-state">${escapeHtml(await responseErrorMessage(res, "Search failed"))}</p>`;
        return;
      }
      const data = await res.json();
      const results = data.results || [];

      if (results.length === 0) {
        el.innerHTML = '<p class="empty-state">No results found.</p>';
        return;
      }

      el.innerHTML = results.map((r) =>
        `<div class="glass-card explore-result">
          <div class="result-header">
            <span class="glass-pill">Score: ${r.score.toFixed(3)}</span>
            <span>#${r.entry_id}</span>
          </div>
          <div class="result-text">${escapeHtml(r.text)}</div>
          ${r.metadata && r.metadata.source ? `<div class="result-source">Source: ${escapeHtml(r.metadata.source)}</div>` : ""}
        </div>`
      ).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  // ── Pipeline status ──────────────────────────────────────
  async function loadPipeline() {
    try {
      const res = await fetch("/api/knowledge/pipeline/status");
      const data = await res.json();
      const status = document.getElementById("pipeline-status");
      if (status) {
        const runtimeLabel = data.runtime_available ? "runtime ready" : "config only";
        status.textContent = `${data.status} | Active jobs: ${data.active_jobs || 0} | ${runtimeLabel}`;
      }
      // K1 — refresh schedule metadata
      const freqSel = document.getElementById("kb-refresh-freq");
      if (freqSel && data.refresh_frequency) freqSel.value = data.refresh_frequency;
      const config = data.config || {};
      const chunking = document.getElementById("cfg-chunking");
      if (chunking && config.chunking) chunking.value = config.chunking;
      const chunkSize = document.getElementById("cfg-chunk-size");
      if (chunkSize && config.chunk_size != null) chunkSize.value = String(config.chunk_size);
      const overlap = document.getElementById("cfg-overlap");
      if (overlap && config.overlap != null) overlap.value = String(config.overlap);
      const embedding = document.getElementById("cfg-embedding");
      if (embedding && config.embedding_model) embedding.value = config.embedding_model;
      const lastEl = document.getElementById("kb-last-refresh");
      if (lastEl) lastEl.textContent = data.last_refresh ? new Date(data.last_refresh).toLocaleString() : "—";
      const nextEl = document.getElementById("kb-next-refresh");
      if (nextEl) nextEl.textContent = data.next_refresh ? new Date(data.next_refresh).toLocaleString() : "—";
    } catch (err) {
      console.error("Pipeline status failed:", err);
    }
  }

  // K1 — Save refresh schedule
  document.getElementById("btn-save-schedule")?.addEventListener("click", async () => {
    const freq = document.getElementById("kb-refresh-freq")?.value || "manual";
    const chunking = document.getElementById("cfg-chunking")?.value || "paragraph";
    const chunkSize = Number(document.getElementById("cfg-chunk-size")?.value || 512);
    const overlap = Number(document.getElementById("cfg-overlap")?.value || 64);
    const embeddingModel = document.getElementById("cfg-embedding")?.value || "tfidf";
    try {
      const res = await fetch("/api/knowledge/pipeline/schedule", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          frequency: freq,
          chunking,
          chunk_size: chunkSize,
          overlap,
          embedding_model: embeddingModel,
        }),
      });
      if (res.ok) {
        if (typeof showToast === "function") showToast("Pipeline settings saved", "success");
      } else {
        notify(await responseErrorMessage(res, "Failed to save pipeline settings"), "error");
      }
      loadPipeline();
    } catch (err) {
      notify("Failed to save pipeline settings", "error");
    }
  });

  // K1 — Refresh now
  document.getElementById("btn-refresh-now")?.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/knowledge/pipeline/refresh", { method: "POST" });
      if (res.ok) {
        if (typeof showToast === "function") showToast("Refresh started", "success");
        setTimeout(loadPipeline, 2000);
      } else {
        notify(await responseErrorMessage(res, "Refresh failed"), "error");
      }
    } catch (err) {
      notify("Refresh failed", "error");
    }
  });

  // ── Stats ────────────────────────────────────────────────
  async function loadStats() {
    const el = document.getElementById("kb-stats-grid");
    try {
      const res = await fetch("/api/knowledge/bases");
      const data = await res.json();
      const bases = data.bases || [];

      if (bases.length === 0) {
        el.innerHTML = '<p class="empty-state">No knowledge bases.</p>';
        return;
      }

      el.innerHTML = bases.map((base) =>
        `<div class="glass-card stat-card">
          <h4>${base.name}</h4>
          <div class="stat-row"><span>Engine:</span> <strong>${base.engine || "tfidf"}</strong></div>
          <div class="stat-row"><span>Chunks:</span> <strong>${base.chunks_count || 0}</strong></div>
          <div class="stat-row"><span>Sources:</span> <strong>${(base.sources || []).length}</strong></div>
        </div>`
      ).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  // ── Add Source dialog ────────────────────────────────────
  document.getElementById("btn-add-source")?.addEventListener("click", async () => {
    await populateBaseSelect("source-base-select");
    document.getElementById("add-source-dialog")?.showModal();
  });

  document.getElementById("add-source-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      type: form.elements.type.value,
      path: form.elements.path.value,
    };
    const baseId = form.elements.base_id.value;
    try {
      const res = await fetch(`/api/knowledge/bases/${baseId}/sources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        document.getElementById("add-source-dialog").close();
        form.reset();
        notify("Source added", "success");
        loadSources();
      } else {
        notify(await responseErrorMessage(res, "Failed to add source"), "error");
      }
    } catch (err) {
      notify("Failed to add source", "error");
    }
  });

  // ── New Base dialog ──────────────────────────────────────
  document.getElementById("btn-new-base")?.addEventListener("click", () => {
    document.getElementById("new-base-dialog")?.showModal();
  });

  document.getElementById("new-base-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      name: form.elements.name.value,
      description: form.elements.description.value,
      engine: form.elements.engine.value,
    };
    try {
      const res = await fetch("/api/knowledge/bases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        document.getElementById("new-base-dialog").close();
        form.reset();
        notify("Knowledge base created", "success");
        loadBases();
      } else {
        const err = await res.json();
        notify(err.error || "Failed to create base", "error");
      }
    } catch (err) {
      notify("Failed to create base", "error");
    }
  });

  // ── Helpers ──────────────────────────────────────────────
  function formatBytes(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }

  async function populateBaseSelect(selectId) {
    try {
      const res = await fetch("/api/knowledge/bases");
      const data = await res.json();
      const options = (data.bases || []).map((b) => `<option value="${b.id}">${b.name}</option>`).join("");
      const sel = document.getElementById(selectId);
      if (sel) sel.innerHTML = options;
    } catch (err) {
      console.error("Failed to populate base select:", err);
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ── Init ─────────────────────────────────────────────────
  loadBases();
})();
