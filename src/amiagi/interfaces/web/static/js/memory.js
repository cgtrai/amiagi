/**
 * Memory Browser — client-side logic.
 * Handles tab switching, agent filtering, search, knowledge base queries.
 */
(function () {
  "use strict";

  let currentAgent = "";
  let currentItems = [];

  const TYPE_MAP = {
    note: { cls: 'badge-idle', icon: '\uD83D\uDCDD', label: 'note' },
    context: { cls: 'badge-working', icon: '\uD83D\uDCCE', label: 'context' },
    fact: { cls: 'badge-success', icon: '\u2705', label: 'fact' },
    insight: { cls: 'badge-muted', icon: '\uD83D\uDCA1', label: 'insight' },
  };

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
      currentItems = data.items || [];
      updateSelectionSummary(currentItems.length, currentAgent);

      if (!data.items || data.items.length === 0) {
        el.innerHTML = '<p class="empty-state">No memory items found.</p>';
        return;
      }

      el.innerHTML = data.items.map((item) => renderMemoryItem(item)).join("");

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

  function updateSelectionSummary(count, agentId) {
    const summary = document.getElementById('memory-selection-summary');
    if (!summary) return;
    summary.textContent = agentId
      ? `Showing ${count} entries for ${agentId}.`
      : `Showing all ${count} memory entries.`;
  }

  function renderMemoryItem(item) {
    const date = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : "\u2014";
    const tags = (item.tags || []).map((t) => `<span class="glass-pill tag">${t}</span>`).join(" ");
    const itemType = item.item_type || (item.metadata && item.metadata.type) || (item.tags && item.tags[0]) || 'note';
    const badge = TYPE_MAP[itemType] || TYPE_MAP.note;
    const typeBadge = `<span class="glass-badge ${badge.cls} memory-type-badge" data-memory-type="${escapeHtml(itemType)}">${badge.icon} ${itemType}</span>`;
    const scopeBadge = `<span class="glass-pill memory-meta-badge">${item.memory_scope || 'local'}</span>`;
    const links = item.links || {};
    let crossRefs = '';
    if (links.task) crossRefs += `<a href="${escapeHtml(links.task)}" class="glass-btn glass-btn--xs glass-btn--ghost" data-memory-link="task">\uD83D\uDCCB Task</a>`;
    if (links.agent) crossRefs += `<a href="${escapeHtml(links.agent)}" class="glass-btn glass-btn--xs glass-btn--ghost" data-memory-link="agent">\uD83E\uDD16 Agent</a>`;
    return `<div class="glass-card memory-item">
      <div class="memory-item-header">
        ${typeBadge}
        ${scopeBadge}
        <span class="memory-agent">\uD83E\uDD16 ${item.agent_id}</span>
        <span class="memory-task">${item.task_id ? `Task ${escapeHtml(item.task_id)}` : ""}</span>
        <span class="memory-time">${date}</span>
      </div>
      <div class="memory-item-body">${escapeHtml(item.key_findings)}</div>
      <div class="memory-item-footer">
        <div class="memory-tags">${tags}</div>
        <div class="memory-links">
          ${crossRefs}
          <button class="btn btn-sm" data-action="edit" data-index="${item.index}" title="Edit">\u270F\uFE0F</button>
          <button class="btn btn-sm btn-danger" data-action="delete" data-index="${item.index}">\uD83D\uDDD1</button>
        </div>
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
      el.innerHTML = data.items.map((item) => renderMemoryItem(item)).join("");
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
      currentItems = data.items || [];
      updateSelectionSummary(currentItems.length, currentAgent);
      el.innerHTML = data.items.map((item) => renderMemoryItem(item)).join("");
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
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
  }

  function csvTags(value) {
    return String(value || '').split(',').map((t) => t.trim()).filter(Boolean);
  }

  function openMemoryEditor(item) {
    if (typeof openDetailDrawer !== 'function' || !item) return;
    const itemType = item.item_type || (item.metadata && item.metadata.type) || 'note';
    openDetailDrawer('Edit Memory', `<form id="edit-memory-form" style="display:grid;gap:var(--space-3)">
      <div><label>Agent</label><div class="glass-input" style="padding:var(--space-2)">${escapeHtml(item.agent_id || '')}</div></div>
      <div><label>Task ID</label><input class="glass-input" id="em-task-id" value="${escapeHtml(item.task_id || '')}"></div>
      <div><label>Type</label><select class="glass-input" id="em-type"><option value="note">Note</option><option value="fact">Fact</option><option value="context">Context</option><option value="insight">Insight</option></select></div>
      <div><label>Key Findings</label><textarea class="glass-input" id="em-findings" rows="6">${escapeHtml(item.key_findings || '')}</textarea></div>
      <div><label>Tags (comma-separated)</label><input class="glass-input" id="em-tags" value="${escapeHtml((item.tags||[]).join(', '))}"></div>
      <button type="submit" class="glass-btn glass-btn--primary">Save</button>
    </form>`);
    setTimeout(() => {
      const typeSelect = document.getElementById('em-type');
      if (typeSelect) typeSelect.value = itemType;
      const form = document.getElementById('edit-memory-form');
      if (form) form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        await fetch('/api/memory/' + item.index, {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            task_id: document.getElementById('em-task-id').value,
            key_findings: document.getElementById('em-findings').value,
            tags: csvTags(document.getElementById('em-tags').value),
            metadata: Object.assign({}, item.metadata || {}, { type: document.getElementById('em-type').value })
          })
        });
        if (typeof closeDetailDrawer === 'function') closeDetailDrawer();
        loadMemoryItems();
      });
    }, 100);
  }

  function openMemoryCreator(agentOptionsHtml) {
    if (typeof openDetailDrawer !== 'function') return;
    openDetailDrawer('Add Memory', `<form id="add-memory-form" style="display:grid;gap:var(--space-3)">
      <div><label>Agent</label><select class="glass-input" id="new-mem-agent">${agentOptionsHtml}</select></div>
      <div><label>Task ID</label><input class="glass-input" id="new-mem-task" placeholder="task-123"></div>
      <div><label>Type</label><select class="glass-input" id="new-mem-type"><option value="note">Note</option><option value="fact">Fact</option><option value="context">Context</option><option value="insight">Insight</option></select></div>
      <div><label>Key Findings</label><textarea class="glass-input" id="new-mem-findings" rows="6" placeholder="Enter findings..."></textarea></div>
      <div><label>Tags (comma-separated)</label><input class="glass-input" id="new-mem-tags" placeholder="research, important"></div>
      <button type="submit" class="glass-btn glass-btn--primary">Create</button>
    </form>`);
    setTimeout(() => {
      const agentSelect = document.getElementById('new-mem-agent');
      if (agentSelect && currentAgent) agentSelect.value = currentAgent;
      const form = document.getElementById('add-memory-form');
      if (form) form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const response = await fetch('/api/memory', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            agent_id: document.getElementById('new-mem-agent').value,
            task_id: document.getElementById('new-mem-task').value,
            key_findings: document.getElementById('new-mem-findings').value,
            tags: csvTags(document.getElementById('new-mem-tags').value),
            metadata: { type: document.getElementById('new-mem-type').value }
          })
        });
        let payload = {};
        try { payload = await response.json(); } catch (_) {}
        if (!response.ok || !payload.ok) {
          if (typeof showToast === 'function') showToast((payload && payload.error) || 'Failed to add memory', 'error');
          return;
        }
        if (typeof closeDetailDrawer === 'function') closeDetailDrawer();
        if (typeof showToast === 'function') showToast('Memory added', 'success');
        loadAgents();
        loadMemoryItems();
      });
    }, 100);
  }

  // ── Init ─────────────────────────────────────────────────
  // ME2: Edit memory item handler
  document.getElementById("memory-items")?.addEventListener("click", (e) => {
    const editBtn = e.target.closest('[data-action="edit"]');
    if (!editBtn) return;
    const idx = parseInt(editBtn.dataset.index, 10);
    const item = currentItems.find((entry) => Number(entry.index) === idx);
    openMemoryEditor(item);
  });

  // ME3: Add memory button handler
  document.getElementById("btn-add-memory")?.addEventListener("click", () => {
    fetch('/api/agents').then(r => r.json()).then(data => {
      const agents = data.agents || data || [];
      const options = agents.map(a => `<option value="${a.agent_id}">${a.name || a.agent_id}</option>`).join('');
      openMemoryCreator(options);
    }).catch(() => {});
  });

  document.getElementById('btn-refresh-memory')?.addEventListener('click', () => {
    loadAgents();
    loadMemoryItems();
  });

  loadAgents();
  loadMemoryItems();
})();
