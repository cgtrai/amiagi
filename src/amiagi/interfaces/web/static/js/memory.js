/**
 * Memory Browser — client-side logic.
 * Handles tab switching, agent filtering, search, knowledge base queries.
 */
(function () {
  "use strict";

  let currentAgent = "";

  // ── Tab switching ────────────────────────────────────────
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".memory-tab").forEach((t) => (t.hidden = true));
      document.getElementById(`tab-${tab}`).hidden = false;
      document.querySelectorAll("[data-tab]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      if (tab === "shared") loadShared();
    });
  });

  // ── Load agents ──────────────────────────────────────────
  async function loadAgents() {
    try {
      const res = await fetch("/api/memory/agents");
      const data = await res.json();
      const list = document.getElementById("agent-list");
      const totalCount = document.getElementById("total-count");

      let total = 0;
      let agentButtons = `<button class="agent-btn active" data-agent="">All <span class="badge">0</span></button>`;
      for (const agent of data.agents || []) {
        total += agent.count;
        agentButtons += `<button class="agent-btn" data-agent="${agent.agent_id}">
          🤖 ${agent.agent_id} <span class="badge">${agent.count}</span>
        </button>`;
      }

      // Update total in the "All" button
      agentButtons = agentButtons.replace(
        '<span class="badge">0</span>',
        `<span class="badge">${total}</span>`
      );
      totalCount.textContent = total;
      list.innerHTML = agentButtons;

      // Wire click handlers
      list.querySelectorAll(".agent-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          list.querySelectorAll(".agent-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          currentAgent = btn.dataset.agent;
          loadMemoryItems();
        });
      });
    } catch (err) {
      console.error("Failed to load agents:", err);
    }
  }

  // ── Load memory items ────────────────────────────────────
  async function loadMemoryItems() {
    const el = document.getElementById("memory-items");
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (currentAgent) params.set("agent_id", currentAgent);
      const res = await fetch(`/api/memory?${params}`);
      const data = await res.json();

      if (!data.items || data.items.length === 0) {
        el.innerHTML = '<p class="empty-state">No memory items found.</p>';
        return;
      }

      el.innerHTML = data.items.map((item, idx) => renderMemoryItem(item, idx)).join("");

      // Wire delete buttons
      el.querySelectorAll("[data-action='delete']").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const idx = btn.dataset.index;
          await fetch(`/api/memory/${idx}`, { method: "DELETE" });
          loadMemoryItems();
          loadAgents();
        });
      });
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderMemoryItem(item, index) {
    const date = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : "—";
    const tags = (item.tags || []).map((t) => `<span class="glass-pill tag">${t}</span>`).join(" ");
    return `<div class="glass-card memory-item">
      <div class="memory-item-header">
        <span class="memory-agent">🤖 ${item.agent_id}</span>
        <span class="memory-task">${item.task_id || ""}</span>
        <span class="memory-time">${date}</span>
      </div>
      <div class="memory-item-body">${escapeHtml(item.key_findings)}</div>
      <div class="memory-item-footer">
        <div class="memory-tags">${tags}</div>
        <button class="btn btn-sm btn-danger" data-action="delete" data-index="${index}">🗑</button>
      </div>
    </div>`;
  }

  // ── Load shared ──────────────────────────────────────────
  async function loadShared() {
    const el = document.getElementById("shared-items");
    try {
      const res = await fetch("/api/memory/shared");
      const data = await res.json();
      if (!data.items || data.items.length === 0) {
        el.innerHTML = '<p class="empty-state">No shared memory items.</p>';
        return;
      }
      el.innerHTML = data.items.map((item, idx) => renderMemoryItem(item, idx)).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  // ── Search ───────────────────────────────────────────────
  document.getElementById("btn-search")?.addEventListener("click", doSearch);
  document.getElementById("memory-search")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });

  async function doSearch() {
    const query = document.getElementById("memory-search")?.value.trim();
    if (!query) { loadMemoryItems(); return; }

    const el = document.getElementById("memory-items");
    try {
      const res = await fetch(`/api/memory/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      if (!data.items || data.items.length === 0) {
        el.innerHTML = `<p class="empty-state">No results for "${escapeHtml(query)}"</p>`;
        return;
      }
      el.innerHTML = data.items.map((item, idx) => renderMemoryItem(item, idx)).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  // ── Knowledge base search ────────────────────────────────
  document.getElementById("btn-kb-search")?.addEventListener("click", doKBSearch);
  document.getElementById("kb-search-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doKBSearch();
  });

  async function doKBSearch() {
    const query = document.getElementById("kb-search-input")?.value.trim();
    if (!query) return;

    const el = document.getElementById("kb-results");
    try {
      const res = await fetch(`/api/knowledge/bases/global/search?q=${encodeURIComponent(query)}&top=10`);
      const data = await res.json();
      if (!data.results || data.results.length === 0) {
        el.innerHTML = `<p class="empty-state">No knowledge entries found.</p>`;
        return;
      }
      el.innerHTML = data.results.map(renderKBResult).join("");
    } catch (err) {
      el.innerHTML = `<p class="error-state">Error: ${err.message}</p>`;
    }
  }

  function renderKBResult(entry) {
    return `<div class="glass-card memory-item">
      <div class="memory-item-header">
        <span class="glass-pill">Score: ${entry.score.toFixed(3)}</span>
        <span>#${entry.entry_id}</span>
      </div>
      <div class="memory-item-body">${escapeHtml(entry.text)}</div>
    </div>`;
  }

  // ── Export ───────────────────────────────────────────────
  document.getElementById("btn-export")?.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/memory?limit=10000");
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data.items, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "memory_export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed:", err);
    }
  });

  // ── Helpers ──────────────────────────────────────────────
  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ── Init ─────────────────────────────────────────────────
  loadAgents();
  loadMemoryItems();
})();
