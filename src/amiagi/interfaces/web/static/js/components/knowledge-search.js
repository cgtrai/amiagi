/**
 * <knowledge-search> — Inline knowledge base search with result highlighting.
 *
 * Attributes:
 *   base-id  — knowledge base ID to search (default: "global")
 *   top      — max results to return (default: 5)
 *   placeholder — input placeholder text
 *
 * Events:
 *   result-click — { detail: { entry_id, text, score } }
 *
 * Usage:
 *   <knowledge-search base-id="global" top="5"></knowledge-search>
 */
class KnowledgeSearch extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._debounce = null;
  }

  static get observedAttributes() {
    return ["base-id", "top", "placeholder"];
  }

  get baseId() { return this.getAttribute("base-id") || "global"; }
  get top() { return parseInt(this.getAttribute("top") || "5", 10); }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; }
        .search-box {
          display: flex; gap: .5rem; margin-bottom: .75rem;
        }
        input {
          flex: 1; padding: .5rem .75rem; border-radius: 8px;
          background: rgba(15,23,42,.6); border: 1px solid rgba(148,163,184,.15);
          color: #e2e8f0; font-size: .85rem; outline: none;
        }
        input:focus { border-color: #0ea5e9; }
        button {
          padding: .5rem 1rem; border-radius: 8px;
          background: rgba(14,165,233,.15); border: 1px solid rgba(14,165,233,.3);
          color: #38bdf8; cursor: pointer; font-size: .85rem; font-weight: 600;
        }
        button:hover { background: rgba(14,165,233,.25); }
        .results { display: flex; flex-direction: column; gap: .5rem; }
        .result-item {
          padding: .75rem; border-radius: 8px; cursor: pointer;
          background: rgba(15,23,42,.5); border: 1px solid rgba(148,163,184,.08);
          transition: border-color .15s;
        }
        .result-item:hover { border-color: rgba(14,165,233,.3); }
        .result-score {
          float: right; font-size: .75rem; font-weight: 700; color: #38bdf8;
          font-variant-numeric: tabular-nums;
        }
        .result-text {
          font-size: .85rem; color: #cbd5e1; line-height: 1.5;
          white-space: pre-wrap; word-break: break-word;
        }
        .result-text mark { background: rgba(250,204,21,.2); color: #fde68a; }
        .result-source { font-size: .75rem; color: #64748b; margin-top: .25rem; }
        .empty-state { color: #64748b; font-size: .85rem; text-align: center; padding: 1rem; }
        .spinner { color: #94a3b8; font-size: .85rem; padding: .5rem; }
      </style>
      <div class="search-box">
        <input id="input" type="text" placeholder="${this.getAttribute("placeholder") || "Search knowledge base…"}" />
        <button id="btn">Search</button>
      </div>
      <div class="results" id="results"></div>
    `;

    const input = this.shadowRoot.getElementById("input");
    const btn = this.shadowRoot.getElementById("btn");

    btn.addEventListener("click", () => this._doSearch());
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._doSearch();
    });
    // Live search with debounce
    input.addEventListener("input", () => {
      clearTimeout(this._debounce);
      this._debounce = setTimeout(() => {
        if (input.value.trim().length >= 3) this._doSearch();
      }, 400);
    });
  }

  disconnectedCallback() {
    clearTimeout(this._debounce);
  }

  async _doSearch() {
    const input = this.shadowRoot.getElementById("input");
    const query = input.value.trim();
    if (!query) return;

    const results = this.shadowRoot.getElementById("results");
    results.innerHTML = '<div class="spinner">Searching…</div>';

    try {
      const res = await fetch(
        `/api/knowledge/bases/${this.baseId}/search?q=${encodeURIComponent(query)}&top=${this.top}`
      );
      const data = await res.json();
      const items = data.results || [];

      if (items.length === 0) {
        results.innerHTML = '<div class="empty-state">No results found.</div>';
        return;
      }

      results.innerHTML = items.map((r) => `
        <div class="result-item" data-entry-id="${r.entry_id}">
          <span class="result-score">${r.score.toFixed(3)}</span>
          <div class="result-text">${this._highlight(r.text, query)}</div>
          ${r.metadata?.source ? `<div class="result-source">📄 ${this._esc(r.metadata.source)}</div>` : ""}
        </div>
      `).join("");

      results.querySelectorAll(".result-item").forEach((el) => {
        el.addEventListener("click", () => {
          this.dispatchEvent(new CustomEvent("result-click", {
            detail: { entry_id: el.dataset.entryId, text: el.querySelector(".result-text")?.textContent },
          }));
        });
      });
    } catch (err) {
      results.innerHTML = `<div class="empty-state">Error: ${this._esc(err.message)}</div>`;
    }
  }

  _highlight(text, query) {
    if (!text || !query) return this._esc(text);
    const escaped = this._esc(text);
    const words = query.split(/\s+/).filter(Boolean).map((w) =>
      w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    );
    if (words.length === 0) return escaped;
    const regex = new RegExp(`(${words.join("|")})`, "gi");
    return escaped.replace(regex, "<mark>$1</mark>");
  }

  _esc(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}

customElements.define("knowledge-search", KnowledgeSearch);
