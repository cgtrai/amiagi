/**
 * <global-search> Web Component
 *
 * Provides a drop-in search overlay that queries GET /api/search.
 * Can be triggered programmatically via .open() / .close() or by the
 * keyboard shortcut wiring in keybindings.js.
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

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._visible = false;
    this._debounceTimer = null;
  }

  connectedCallback() {
    this._render();
    this._overlay = this.shadowRoot.querySelector(".gs-overlay");
    this._input = this.shadowRoot.querySelector(".gs-input");
    this._list = this.shadowRoot.querySelector(".gs-list");

    const self = this;

    // Close on overlay background click
    this._overlay.addEventListener("click", function (e) {
      if (e.target === self._overlay) self.close();
    });

    // Input handling
    this._input.addEventListener("input", function () {
      clearTimeout(self._debounceTimer);
      self._debounceTimer = setTimeout(function () {
        self._search(self._input.value.trim());
      }, 200);
    });

    // Keyboard nav
    this._input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        self.close();
        return;
      }
      if (e.key === "Enter") {
        const active = self._list.querySelector(".gs-item.active");
        if (active) active.click();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        self._navigate(e.key === "ArrowDown" ? 1 : -1);
      }
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
    const inp = this._input;
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

  /* ---------- Internal ---------- */

  async _search(query) {
    if (query.length < 2) {
      this._list.innerHTML = "";
      return;
    }
    try {
      const resp = await fetch(
        this.apiUrl + "?q=" + encodeURIComponent(query) + "&limit=" + this.maxResults
      );
      const results = await resp.json();
      this._renderResults(Array.isArray(results) ? results : results.results || []);
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
    const self = this;
    results.forEach(function (r, i) {
      const li = document.createElement("li");
      li.className = "gs-item" + (i === 0 ? " active" : "");
      li.innerHTML =
        '<span class="gs-type">' + self._esc(r.entity_type || r.type || "item") + "</span>" +
        '<span class="gs-title">' + self._esc(r.title || r.name || "") + "</span>";
      li.addEventListener("click", function () {
        self.close();
        self.dispatchEvent(
          new CustomEvent("result-select", { detail: r, bubbles: true })
        );
        // Navigate if URL provided
        if (r.url) window.location.href = r.url;
      });
      self._list.appendChild(li);
    });
  }

  _navigate(dir) {
    const items = this._list.querySelectorAll(".gs-item:not(.gs-muted)");
    if (!items.length) return;
    let idx = -1;
    items.forEach(function (el, i) {
      if (el.classList.contains("active")) idx = i;
    });
    items.forEach(function (el) { el.classList.remove("active"); });
    const next = (idx + dir + items.length) % items.length;
    items[next].classList.add("active");
    items[next].scrollIntoView({ block: "nearest" });
  }

  _esc(str) {
    const d = document.createElement("div");
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
          max-width: 520px;
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
          flex-shrink: 0;
          font-size: 0.7rem;
          font-weight: 600;
          text-transform: uppercase;
          color: var(--accent-primary, #818cf8);
          background: rgba(99,102,241,0.1);
          padding: 2px 6px;
          border-radius: 4px;
        }
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
          <ul class="gs-list"></ul>
        </div>
      </div>
    `;
  }
}

customElements.define("global-search", GlobalSearch);
