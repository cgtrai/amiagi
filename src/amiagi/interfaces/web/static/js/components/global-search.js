/**
 * <global-search> Web Component
 *
 * Provides a drop-in search overlay that queries GET /api/search.
 * Can be triggered programmatically via .open() / .close() or by the
 * keyboard shortcut wiring in keybindings.js.
 *
 * Features:
 *   - Per-type icons (agents, tasks, files, skills, prompts, etc.)
 *   - Recent searches stored in localStorage
 *   - Type filter tabs
 *   - Keyboard navigation (arrows, Enter, Escape)
 *
 * Attributes:
 *   placeholder  – input placeholder text (default: "Search agents, tasks, files…")
 *   api-url      – search endpoint (default: "/api/search")
 *   limit        – max results (default: 10)
 */
class GlobalSearch extends HTMLElement {
  static get observedAttributes() {
    return ["placeholder", "api-url", "limit"];
  }

  /** SVG icons per entity type. */
  static TYPE_ICONS = {
    agent: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/></svg>',
    task: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 12l2 2 4-4"/></svg>',
    file: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    skill: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
    prompt: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    session: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    workflow: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
    template: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>',
    knowledge: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>'
  };

  /** Color per entity type for the badge. */
  static TYPE_COLORS = {
    agent: "rgba(99,102,241,0.15)",
    task: "rgba(34,197,94,0.15)",
    file: "rgba(251,191,36,0.15)",
    skill: "rgba(244,114,182,0.15)",
    prompt: "rgba(147,51,234,0.15)",
    session: "rgba(56,189,248,0.15)",
    workflow: "rgba(251,146,60,0.15)",
    template: "rgba(45,212,191,0.15)",
    knowledge: "rgba(168,85,247,0.15)"
  };

  static RECENT_KEY = "gs_recent_searches";
  static MAX_RECENT = 5;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._visible = false;
    this._debounceTimer = null;
    this._activeFilter = null;
  }

  connectedCallback() {
    this._render();
    this._overlay = this.shadowRoot.querySelector(".gs-overlay");
    this._input = this.shadowRoot.querySelector(".gs-input");
    this._list = this.shadowRoot.querySelector(".gs-list");
    this._filters = this.shadowRoot.querySelector(".gs-filters");
    this._recentSection = this.shadowRoot.querySelector(".gs-recent");

    var self = this;

    // Close on overlay background click
    this._overlay.addEventListener("click", function (e) {
      if (e.target === self._overlay) self.close();
    });

    // Input handling
    this._input.addEventListener("input", function () {
      clearTimeout(self._debounceTimer);
      var q = self._input.value.trim();
      self._debounceTimer = setTimeout(function () {
        self._search(q);
      }, 200);
      // Show/hide recent section
      self._recentSection.style.display = q.length > 0 ? "none" : "";
    });

    // Keyboard nav
    this._input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        self.close();
        return;
      }
      if (e.key === "Enter") {
        var active = self._list.querySelector(".gs-item.active");
        if (active) active.click();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        self._navigate(e.key === "ArrowDown" ? 1 : -1);
      }
    });

    // Filter tab clicks
    this._filters.addEventListener("click", function (e) {
      var btn = e.target.closest(".gs-filter");
      if (!btn) return;
      var type = btn.dataset.type || null;
      self._setFilter(type);
      var q = self._input.value.trim();
      if (q.length >= 2) self._search(q);
    });
  }

  disconnectedCallback() {
    clearTimeout(this._debounceTimer);
  }

  get apiUrl() {
    return this.getAttribute("api-url") || "/api/search";
  }
  get maxResults() {
    return parseInt(this.getAttribute("limit") || "10", 10);
  }
  get placeholderText() {
    return this.getAttribute("placeholder") || "Search agents, tasks, files\u2026";
  }

  /** Open the search overlay. */
  open() {
    this._visible = true;
    this._overlay.style.display = "flex";
    this._input.value = "";
    this._list.innerHTML = "";
    this._setFilter(null);
    this._showRecent();
    this._recentSection.style.display = "";
    var inp = this._input;
    setTimeout(function () { inp.focus(); }, 50);
  }

  /** Close the search overlay. */
  close() {
    this._visible = false;
    this._overlay.style.display = "none";
  }

  get isOpen() {
    return this._visible;
  }

  /* ---------- Recent Searches ---------- */

  _getRecent() {
    try {
      return JSON.parse(localStorage.getItem(GlobalSearch.RECENT_KEY) || "[]");
    } catch (_) { return []; }
  }

  _addRecent(query) {
    if (!query || query.length < 2) return;
    var recent = this._getRecent().filter(function (q) { return q !== query; });
    recent.unshift(query);
    if (recent.length > GlobalSearch.MAX_RECENT) recent = recent.slice(0, GlobalSearch.MAX_RECENT);
    try { localStorage.setItem(GlobalSearch.RECENT_KEY, JSON.stringify(recent)); } catch (_) {}
  }

  _showRecent() {
    var recent = this._getRecent();
    this._recentSection.innerHTML = "";
    if (!recent.length) return;

    var header = document.createElement("div");
    header.className = "gs-recent-header";
    header.innerHTML = '<span>Recent</span><button class="gs-recent-clear">Clear</button>';
    this._recentSection.appendChild(header);

    var self = this;
    header.querySelector(".gs-recent-clear").addEventListener("click", function () {
      try { localStorage.removeItem(GlobalSearch.RECENT_KEY); } catch (_) {}
      self._recentSection.innerHTML = "";
    });

    recent.forEach(function (q) {
      var item = document.createElement("div");
      item.className = "gs-recent-item";
      item.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>' +
        '<span>' + self._esc(q) + '</span>';
      item.addEventListener("click", function () {
        self._input.value = q;
        self._recentSection.style.display = "none";
        self._search(q);
      });
      self._recentSection.appendChild(item);
    });
  }

  /* ---------- Filter tabs ---------- */

  _setFilter(type) {
    this._activeFilter = type || null;
    this._filters.querySelectorAll(".gs-filter").forEach(function (btn) {
      btn.classList.toggle("active", (btn.dataset.type || null) === (type || null));
    });
  }

  /* ---------- Search ---------- */

  async _search(query) {
    if (query.length < 2) {
      this._list.innerHTML = "";
      return;
    }
    var url = this.apiUrl + "?q=" + encodeURIComponent(query) + "&limit=" + this.maxResults;
    if (this._activeFilter) url += "&type=" + encodeURIComponent(this._activeFilter);
    try {
      var resp = await fetch(url);
      var results = await resp.json();
      var arr = Array.isArray(results) ? results : results.results || [];
      this._renderResults(arr);
      if (arr.length > 0) this._addRecent(query);
    } catch (_) {
      this._list.innerHTML =
        '<li class="gs-item gs-muted">Search error</li>';
    }
  }

  _renderResults(results) {
    this._list.innerHTML = "";
    if (results.length === 0) {
      this._list.innerHTML = '<li class="gs-item gs-muted">No results</li>';
      return;
    }
    var self = this;
    results.forEach(function (r, i) {
      var type = (r.entity_type || r.type || "item").toLowerCase();
      var icon = GlobalSearch.TYPE_ICONS[type] || "";
      var color = GlobalSearch.TYPE_COLORS[type] || "rgba(99,102,241,0.1)";
      var li = document.createElement("li");
      li.className = "gs-item" + (i === 0 ? " active" : "");
      li.innerHTML =
        '<span class="gs-type" style="background:' + color + '">' +
          (icon ? '<span class="gs-icon">' + icon + '</span>' : "") +
          self._esc(type) +
        "</span>" +
        '<span class="gs-title">' + self._esc(r.title || r.name || "") + "</span>";
      li.addEventListener("click", function () {
        self.close();
        self.dispatchEvent(
          new CustomEvent("result-select", { detail: r, bubbles: true })
        );
        if (r.url) window.location.href = r.url;
      });
      self._list.appendChild(li);
    });
  }

  _navigate(dir) {
    var items = this._list.querySelectorAll(".gs-item:not(.gs-muted)");
    if (!items.length) return;
    var idx = -1;
    items.forEach(function (el, i) {
      if (el.classList.contains("active")) idx = i;
    });
    items.forEach(function (el) { el.classList.remove("active"); });
    var next = (idx + dir + items.length) % items.length;
    items[next].classList.add("active");
    items[next].scrollIntoView({ block: "nearest" });
  }

  _esc(str) {
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: contents; }
        .gs-overlay {
          display: none;
          position: fixed;
          inset: 0;
          z-index: 9999;
          background: rgba(0, 0, 0, 0.5);
          backdrop-filter: blur(4px);
          justify-content: center;
          align-items: flex-start;
          padding-top: 15vh;
        }
        .gs-panel {
          width: 90%;
          max-width: 560px;
          background: var(--glass-bg, rgba(30,41,59,0.95));
          border: 1px solid var(--border-secondary, #475569);
          border-radius: var(--radius-lg, 12px);
          box-shadow: 0 12px 40px rgba(0,0,0,0.5);
          overflow: hidden;
        }
        .gs-input {
          width: 100%;
          box-sizing: border-box;
          padding: 0.75rem 1rem;
          font-size: 1rem;
          background: transparent;
          color: var(--text-primary, #f1f5f9);
          border: none;
          border-bottom: 1px solid var(--border-secondary, #334155);
          outline: none;
        }
        .gs-input::placeholder {
          color: var(--text-muted, #64748b);
        }
        /* ── Filter tabs ── */
        .gs-filters {
          display: flex;
          gap: 4px;
          padding: 6px 10px;
          border-bottom: 1px solid var(--border-secondary, #334155);
          overflow-x: auto;
          scrollbar-width: none;
        }
        .gs-filters::-webkit-scrollbar { display: none; }
        .gs-filter {
          flex-shrink: 0;
          padding: 3px 8px;
          font-size: 0.72rem;
          font-weight: 600;
          text-transform: uppercase;
          border: 1px solid transparent;
          border-radius: 6px;
          background: transparent;
          color: var(--text-muted, #64748b);
          cursor: pointer;
          transition: all 0.15s;
        }
        .gs-filter:hover { color: var(--text-primary, #f1f5f9); }
        .gs-filter.active {
          background: rgba(99,102,241,0.12);
          color: var(--accent-primary, #818cf8);
          border-color: rgba(99,102,241,0.25);
        }
        /* ── Recent searches ── */
        .gs-recent { padding: 0; }
        .gs-recent-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 6px 12px;
          font-size: 0.72rem;
          font-weight: 600;
          text-transform: uppercase;
          color: var(--text-muted, #64748b);
        }
        .gs-recent-clear {
          background: none;
          border: none;
          color: var(--text-muted, #64748b);
          cursor: pointer;
          font-size: 0.72rem;
          padding: 2px 6px;
          border-radius: 4px;
        }
        .gs-recent-clear:hover { color: var(--text-primary, #f1f5f9); }
        .gs-recent-item {
          display: flex;
          gap: 8px;
          align-items: center;
          padding: 6px 12px;
          font-size: 0.85rem;
          color: var(--text-secondary, #94a3b8);
          cursor: pointer;
        }
        .gs-recent-item:hover {
          background: rgba(99, 102, 241, 0.08);
          color: var(--text-primary, #f1f5f9);
        }
        .gs-recent-item svg { flex-shrink: 0; color: var(--text-muted, #64748b); }
        /* ── Results list ── */
        .gs-list {
          list-style: none;
          margin: 0;
          padding: 0;
          max-height: 340px;
          overflow-y: auto;
        }
        .gs-item {
          display: flex;
          gap: 0.5rem;
          align-items: center;
          padding: 0.5rem 1rem;
          font-size: 0.88rem;
          cursor: pointer;
          color: var(--text-primary, #f1f5f9);
          border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .gs-item:hover, .gs-item.active {
          background: rgba(99, 102, 241, 0.12);
        }
        .gs-item.gs-muted {
          color: var(--text-muted, #64748b);
          cursor: default;
        }
        .gs-type {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          flex-shrink: 0;
          font-size: 0.7rem;
          font-weight: 600;
          text-transform: uppercase;
          color: var(--accent-primary, #818cf8);
          background: rgba(99,102,241,0.1);
          padding: 2px 6px;
          border-radius: 4px;
        }
        .gs-icon { display: inline-flex; align-items: center; }
        .gs-title {
          flex: 1;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
      </style>
      <div class="gs-overlay">
        <div class="gs-panel">
          <input class="gs-input" placeholder="${this.placeholderText}" autocomplete="off"/>
          <div class="gs-filters">
            <button class="gs-filter active" data-type="">All</button>
            <button class="gs-filter" data-type="agent">Agents</button>
            <button class="gs-filter" data-type="task">Tasks</button>
            <button class="gs-filter" data-type="file">Files</button>
            <button class="gs-filter" data-type="skill">Skills</button>
            <button class="gs-filter" data-type="prompt">Prompts</button>
            <button class="gs-filter" data-type="workflow">Workflows</button>
          </div>
          <div class="gs-recent"></div>
          <ul class="gs-list"></ul>
        </div>
      </div>
    `;
  }
}

customElements.define("global-search", GlobalSearch);
