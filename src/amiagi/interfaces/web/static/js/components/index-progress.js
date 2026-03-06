/**
 * <index-progress> — Progress bar for knowledge base reindex operations.
 *
 * Attributes:
 *   base-id  — knowledge base being indexed
 *   status   — current status: "idle" | "indexing" | "done" | "error"
 *   progress — 0-100 percentage
 *   label    — optional text overlay
 *
 * Usage:
 *   <index-progress base-id="global" status="indexing" progress="42"></index-progress>
 */
class IndexProgress extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  static get observedAttributes() {
    return ["base-id", "status", "progress", "label"];
  }

  get status() { return this.getAttribute("status") || "idle"; }
  get progress() { return parseFloat(this.getAttribute("progress") || "0"); }
  get label() { return this.getAttribute("label") || ""; }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; width: 100%; }
        .wrapper {
          border-radius: 8px; overflow: hidden;
          background: rgba(15,23,42,.6); border: 1px solid rgba(148,163,184,.1);
          padding: .5rem .75rem;
        }
        .header {
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: .4rem; font-size: .8rem;
        }
        .status-text { color: #94a3b8; }
        .pct-text { color: #e2e8f0; font-weight: 700; font-variant-numeric: tabular-nums; }
        .track {
          height: 8px; border-radius: 4px;
          background: rgba(71,85,105,.35); overflow: hidden;
        }
        .bar {
          height: 100%; border-radius: 4px;
          transition: width .35s ease;
        }
        .bar.idle     { background: #475569; }
        .bar.indexing { background: linear-gradient(90deg, #0ea5e9, #38bdf8); }
        .bar.done     { background: #22c55e; }
        .bar.error    { background: #ef4444; }
        .label-text { font-size: .75rem; color: #64748b; margin-top: .3rem; }
        @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .5 } }
        .bar.indexing { animation: pulse 1.5s ease-in-out infinite; }
      </style>
      <div class="wrapper">
        <div class="header">
          <span class="status-text" id="status-text"></span>
          <span class="pct-text" id="pct-text"></span>
        </div>
        <div class="track"><div class="bar" id="bar"></div></div>
        <div class="label-text" id="label-text"></div>
      </div>
    `;
    this._update();
  }

  disconnectedCallback() {}

  attributeChangedCallback() {
    this._update();
  }

  _update() {
    if (!this.shadowRoot) return;
    const bar = this.shadowRoot.getElementById("bar");
    const statusEl = this.shadowRoot.getElementById("status-text");
    const pctEl = this.shadowRoot.getElementById("pct-text");
    const labelEl = this.shadowRoot.getElementById("label-text");
    if (!bar) return;

    const s = this.status;
    const p = Math.max(0, Math.min(100, this.progress));

    bar.className = `bar ${s}`;
    bar.style.width = s === "idle" ? "0%" : `${p}%`;

    const icons = { idle: "⏸", indexing: "🔄", done: "✅", error: "❌" };
    statusEl.textContent = `${icons[s] || ""} ${s.charAt(0).toUpperCase() + s.slice(1)}`;
    pctEl.textContent = s === "idle" ? "" : `${p.toFixed(0)}%`;
    labelEl.textContent = this.label;
    labelEl.hidden = !this.label;
  }

  /**
   * Helper to start polling for progress.
   * @param {string} baseId
   * @param {number} intervalMs
   */
  startPolling(baseId, intervalMs = 2000) {
    this.setAttribute("status", "indexing");
    this._pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/knowledge/pipeline/status`);
        const data = await res.json();
        const pct = data.progress ?? 0;
        this.setAttribute("progress", String(pct));
        if (data.status === "idle" || data.status === "done") {
          this.setAttribute("status", "done");
          this.setAttribute("progress", "100");
          clearInterval(this._pollTimer);
        } else if (data.status === "error") {
          this.setAttribute("status", "error");
          clearInterval(this._pollTimer);
        }
      } catch {
        this.setAttribute("status", "error");
        clearInterval(this._pollTimer);
      }
    }, intervalMs);
  }

  stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }
}

customElements.define("index-progress", IndexProgress);
