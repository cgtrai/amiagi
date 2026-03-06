/**
 * <cloud-model-card> Web Component
 *
 * Displays a cloud model entry with provider, model name, API key,
 * and actions (test / delete).
 *
 * Attributes:
 *   provider      – "openai" | "anthropic" | "custom"
 *   model         – model identifier (e.g. "gpt-5-mini")
 *   display-name  – human-readable name
 *   base-url      – API base URL
 *   api-key       – masked or full API key
 *   enabled       – "true" | "false"
 *
 * Events emitted (bubble):
 *   cloud-model-delete  – detail: { provider, model }
 *   cloud-model-test    – detail: { provider, model, base_url, api_key }
 */
class CloudModelCard extends HTMLElement {
  static get observedAttributes() {
    return ["provider", "model", "display-name", "base-url", "api-key", "enabled"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._renderPending = false;
  }

  /* ── Attribute getters ─────────────────────────────────────── */
  get provider()    { return this.getAttribute("provider") || "custom"; }
  get model()       { return this.getAttribute("model") || ""; }
  get displayName() { return this.getAttribute("display-name") || this.model; }
  get baseUrl()     { return this.getAttribute("base-url") || ""; }
  get apiKey()      { return this.getAttribute("api-key") || ""; }
  get enabled()     { return this.getAttribute("enabled") !== "false"; }

  attributeChangedCallback() {
    this._scheduleRender();
  }

  connectedCallback() {
    this._render();
    this._bindEvents();
  }

  _scheduleRender() {
    if (this._renderPending) return;
    this._renderPending = true;
    queueMicrotask(() => {
      this._renderPending = false;
      this._render();
      this._bindEvents();
    });
  }

  _esc(s) {
    var d = document.createElement("div");
    d.textContent = String(s || "");
    return d.innerHTML;
  }

  _providerLabel(p) {
    var labels = { openai: "OpenAI", anthropic: "Anthropic", custom: "Custom" };
    return labels[p] || p;
  }

  _providerColor(p) {
    var colors = { openai: "#10a37f", anthropic: "#d97706", custom: "#60a5fa" };
    return colors[p] || "#94a3b8";
  }

  _render() {
    var prov = this.provider;
    var color = this._providerColor(prov);
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        .card {
          display: flex;
          flex-direction: column;
          gap: 0.45rem;
          padding: 0.75rem 1rem;
          border-radius: 10px;
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.06);
          transition: background 0.15s;
        }
        .card:hover {
          background: rgba(255, 255, 255, 0.08);
        }
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 0.5rem;
        }
        .name {
          font-weight: 600;
          font-size: 0.85rem;
          color: var(--text-primary, #e2e8f0);
        }
        .badge {
          display: inline-flex;
          align-items: center;
          gap: 3px;
          padding: 2px 8px;
          border-radius: 12px;
          font-size: 0.68rem;
          font-weight: 600;
          letter-spacing: 0.02em;
          background: ${color}22;
          color: ${color};
          border: 1px solid ${color}44;
        }
        .badge-dot {
          width: 6px; height: 6px;
          border-radius: 50%;
          background: ${color};
        }
        .meta {
          display: flex;
          flex-direction: column;
          gap: 2px;
          font-size: 0.72rem;
          color: var(--text-muted, #94a3b8);
        }
        .meta-row {
          display: flex;
          align-items: center;
          gap: 0.35rem;
        }
        .meta-label {
          opacity: 0.7;
          min-width: 50px;
        }
        .meta-value {
          font-family: var(--font-mono, monospace);
          word-break: break-all;
        }
        .actions {
          display: flex;
          gap: 0.4rem;
          margin-top: 0.25rem;
        }
        button {
          padding: 3px 8px;
          font-size: 0.72rem;
          border-radius: 6px;
          cursor: pointer;
          background: rgba(255,255,255,0.06);
          border: 1px solid rgba(255,255,255,0.1);
          color: var(--text-primary, #e2e8f0);
          transition: background 0.15s;
        }
        button:hover {
          background: rgba(255,255,255,0.12);
        }
        .btn-danger {
          color: var(--color-danger, #ef4444);
          border-color: rgba(239,68,68,0.3);
        }
        .btn-danger:hover {
          background: rgba(239,68,68,0.15);
        }
      </style>
      <div class="card">
        <div class="header">
          <span class="name">${this._esc(this.displayName)}</span>
          <span class="badge"><span class="badge-dot"></span>${this._esc(this._providerLabel(prov))}</span>
        </div>
        <div class="meta">
          <div class="meta-row">
            <span class="meta-label">Model:</span>
            <span class="meta-value">${this._esc(this.model)}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">URL:</span>
            <span class="meta-value">${this._esc(this.baseUrl || "default")}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Key:</span>
            <span class="meta-value">${this._esc(this.apiKey)}</span>
          </div>
        </div>
        <div class="actions">
          <button class="js-test">⚡ Test</button>
          <button class="js-delete btn-danger">✕ Delete</button>
        </div>
      </div>
    `;
  }

  _bindEvents() {
    var self = this;
    var testBtn = this.shadowRoot.querySelector(".js-test");
    var delBtn  = this.shadowRoot.querySelector(".js-delete");
    if (testBtn) {
      testBtn.addEventListener("click", function () {
        self.dispatchEvent(new CustomEvent("cloud-model-test", {
          bubbles: true,
          detail: {
            provider: self.provider,
            model: self.model,
            base_url: self.baseUrl,
            api_key: self.apiKey,
          },
        }));
      });
    }
    if (delBtn) {
      delBtn.addEventListener("click", function () {
        self.dispatchEvent(new CustomEvent("cloud-model-delete", {
          bubbles: true,
          detail: {
            provider: self.provider,
            model: self.model,
          },
        }));
      });
    }
  }
}

customElements.define("cloud-model-card", CloudModelCard);
