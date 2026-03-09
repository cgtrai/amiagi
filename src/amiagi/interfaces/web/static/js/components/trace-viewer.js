/**
 * <trace-viewer> — Web Component for visualising performance trace spans.
 *
 * Attributes:
 *   trace-id  — load this trace on connectedCallback
 *
 * API:  .loadTrace(traceId)
 * Events: trace-span-click  (detail: span object)
 */
class TraceViewer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._trace = null;
  }

  static get observedAttributes() {
    return ["trace-id"];
  }

  connectedCallback() {
    this._render();
    const tid = this.getAttribute("trace-id");
    if (tid) this.loadTrace(tid);
  }

  attributeChangedCallback(name, _old, val) {
    if (name === "trace-id" && val) this.loadTrace(val);
  }

  async loadTrace(traceId) {
    try {
      const res = await fetch(`/api/traces/${encodeURIComponent(traceId)}`);
      if (!res.ok) {
        this._trace = null;
        this._renderError("Trace not found");
        return;
      }
      this._trace = await res.json();
      this._renderTrace();
    } catch (e) {
      this._renderError(e.message);
    }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .tv-container { font-family: inherit; color: var(--text-primary, #e2e8f0); }
        .tv-empty { color: var(--text-muted, #94a3b8); font-size: 0.85rem; }
        .tv-span {
          position: relative;
          padding: 6px 10px 6px 24px;
          border-left: 2px solid var(--glass-border, #334155);
          margin-left: 12px;
          margin-bottom: 4px;
          cursor: pointer;
          border-radius: 4px;
          transition: background 0.15s;
        }
        .tv-span:hover { background: rgba(255,255,255,0.04); }
        .tv-span--success { border-left-color: var(--state-success, #34d399); }
        .tv-span--error   { border-left-color: var(--state-error, #f87171); }
        .tv-span--running { border-left-color: var(--accent-blue, #60a5fa); }
        .tv-span-name { font-weight: 600; font-size: 0.85rem; }
        .tv-span-meta { font-size: 0.75rem; color: var(--text-muted, #94a3b8); margin-top: 2px; }
        .tv-bar-outer {
          height: 6px; background: rgba(255,255,255,0.06);
          border-radius: 3px; margin-top: 4px; overflow: hidden;
        }
        .tv-bar-inner { height: 100%; border-radius: 3px; }
        .tv-bar-inner--ok     { background: var(--state-success, #34d399); }
        .tv-bar-inner--error  { background: var(--state-error, #f87171); }
        .tv-bar-inner--active { background: var(--accent-blue, #60a5fa); }
        .tv-header { font-weight: 700; margin-bottom: 8px; font-size: 0.9rem; }
      </style>
      <div class="tv-container">
        <p class="tv-empty">Select a trace to view spans.</p>
      </div>`;
  }

  _renderError(msg) {
    const c = this.shadowRoot.querySelector(".tv-container");
    if (c) c.innerHTML = `<p class="tv-empty">${this._esc(msg)}</p>`;
  }

  _renderTrace() {
    const t = this._trace;
    if (!t) return;

    const spans = t.spans || t.events || [];
    const totalMs = t.duration_ms || t.total_duration_ms || 1;

    const c = this.shadowRoot.querySelector(".tv-container");
    if (!c) return;

    let html = `<div class="tv-header">${this._esc(t.name || t.trace_id || t.id || "Trace")} — ${totalMs.toFixed(1)}ms</div>`;

    if (spans.length === 0) {
      // render single-level info
      const status = t.status || "unknown";
      html += this._renderSpanHtml(t, totalMs);
    } else {
      for (const span of spans) {
        html += this._renderSpanHtml(span, totalMs);
      }
    }
    c.innerHTML = html;

    // Attach click handlers
    c.querySelectorAll(".tv-span").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.idx, 10);
        const span = spans[idx] || t;
        this.dispatchEvent(new CustomEvent("trace-span-click", { detail: span, bubbles: true }));
      });
    });
  }

  _renderSpanHtml(span, totalMs) {
    const name = span.name || span.agent_role || span.type || "span";
    const dur = span.duration_ms || 0;
    const status = (span.status || span.result || "unknown").toLowerCase();
    const statusCls = status === "success" || status === "ok" ? "tv-span--success" : status === "error" ? "tv-span--error" : "tv-span--running";
    const barCls = status === "success" || status === "ok" ? "tv-bar-inner--ok" : status === "error" ? "tv-bar-inner--error" : "tv-bar-inner--active";
    const pct = totalMs > 0 ? Math.min((dur / totalMs) * 100, 100) : 0;

    return `<div class="tv-span ${statusCls}" data-idx="${span._idx || 0}">
      <div class="tv-span-name">${this._esc(name)}</div>
      <div class="tv-span-meta">${dur.toFixed(1)}ms · ${this._esc(status)} · ${this._esc(span.model || "")}</div>
      <div class="tv-bar-outer"><div class="tv-bar-inner ${barCls}" style="width:${pct.toFixed(1)}%"></div></div>
    </div>`;
  }

  _esc(s) {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }
}

customElements.define("trace-viewer", TraceViewer);
