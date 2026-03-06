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
          await fetch(`/api/knowledge/bases/${btn.dataset.baseId}/reindex`, { method: "POST" });
          btn.textContent = "⏳ Reindexing...";
          setTimeout(() => loadBases(), 2000);
        });
      });
      el.querySelectorAll("[data-action='delete-base']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("Delete this knowledge base?")) return;
          await fetch(`/api/knowledge/bases/${btn.dataset.baseId}`, { method: "DELETE" });
          loadBases();
        });
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderBaseCard(base) {
    const icon = base.id === "global" ? "📘" : "📗";
    const sources_count = (base.sources || []).length;
    return `<div class="glass-card base-card">
      <div class="base-header">
        <strong>${icon} ${base.name}</strong>
        <span class="glass-pill">${base.engine || "tfidf"}</span>
      </div>
      <div class="base-meta">
        <span>Chunks: ${base.chunks_count || 0}</span>
        <span>Sources: ${sources_count}</span>
      </div>
      <p class="base-desc">${base.description || ""}</p>
      <div class="base-actions">
        <button class="btn btn-sm btn-outline" data-action="reindex" data-base-id="${base.id}">🔄 Reindex</button>
        ${base.id !== "global" ? `<button class="btn btn-sm btn-danger" data-action="delete-base" data-base-id="${base.id}">🗑</button>` : ""}
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
          const typeIcon = src.type === "dir" ? "📁" : src.type === "url" ? "🌐" : "📄";
          const statusIcon = src.status === "indexed" ? "✅" : src.status === "error" ? "❌" : "⏳";
          const indexed = src.indexed_at ? new Date(src.indexed_at * 1000).toLocaleString() : "—";
          rows += `<tr>
            <td>${typeIcon} ${src.type}</td>
            <td>${escapeHtml(src.path)}</td>
            <td>${src.chunks_count || 0}</td>
            <td>${statusIcon} ${src.status}</td>
            <td>${indexed}</td>
            <td><button class="btn btn-sm btn-danger" data-action="remove-source"
                  data-base-id="${base.id}" data-source-id="${src.id}">🗑</button></td>
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
          ${r.metadata && r.metadata.source ? `<div class="result-source">📄 ${escapeHtml(r.metadata.source)}</div>` : ""}
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
        const icon = data.status === "idle" ? "🟢" : data.status === "indexing" ? "🔄" : "⚠️";
        status.textContent = `${icon} ${data.status} | Active jobs: ${data.active_jobs || 0}`;
      }
    } catch (err) {
      console.error("Pipeline status failed:", err);
    }
  }

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
        loadSources();
      } else {
        const err = await res.json();
        alert(err.error || "Failed to add source");
      }
    } catch (err) {
      alert("Error: " + err.message);
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
        loadBases();
      } else {
        const err = await res.json();
        alert(err.error || "Failed to create base");
      }
    } catch (err) {
      alert("Error: " + err.message);
    }
  });

  // ── Helpers ──────────────────────────────────────────────
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
