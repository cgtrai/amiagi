/**
 * <secret-field> Web Component
 *
 * Displays a masked secret value with reveal/copy controls.
 *
 * Attributes:
 *   value   – the secret text (default: "••••••••")
 *   masked  – boolean, whether to mask the value (default: true)
 *
 * Usage:
 *   <secret-field value="my-secret"></secret-field>
 */
class SecretField extends HTMLElement {
  static get observedAttributes() {
    return ["value", "masked"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._masked = true;
    this._renderPending = false;
  }

  /* ── Attributes ────────────────────────────────────────────── */
  get value()  { return this.getAttribute("value") || "••••••••"; }
  set value(v) { this.setAttribute("value", v); }

  get masked() { return this.getAttribute("masked") !== "false"; }
  set masked(v) { this.setAttribute("masked", v ? "true" : "false"); }

  attributeChangedCallback() {
    this._scheduleRender();
  }

  connectedCallback() {
    this._masked = this.masked;
    this._render();
    this._bindEvents();
  }

  /* ── Render ────────────────────────────────────────────────── */
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
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  _render() {
    const display = this._masked
      ? "••••••••"
      : this._esc(this.value);

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-family: var(--font-mono, monospace);
          font-size: 0.8rem;
        }
        .val {
          color: var(--text-primary, #e2e8f0);
          user-select: all;
          min-width: 60px;
        }
        .val.--masked {
          color: var(--text-muted, #94a3b8);
          user-select: none;
          letter-spacing: 2px;
        }
        button {
          background: none;
          border: none;
          cursor: pointer;
          font-size: 0.72rem;
          padding: 2px 4px;
          border-radius: 4px;
          color: var(--text-muted, #94a3b8);
          transition: color 0.15s, background 0.15s;
        }
        button:hover {
          color: var(--text-primary, #e2e8f0);
          background: rgba(255,255,255,0.06);
        }
        .copied {
          color: var(--color-success, #4ade80);
        }
      </style>
      <span class="val${this._masked ? ' --masked' : ''}">${display}</span>
      <button class="js-toggle" title="${this._masked ? 'Reveal' : 'Hide'}">${this._masked ? '👁' : '🙈'}</button>
      <button class="js-copy" title="Copy">📋</button>
    `;
  }

  _bindEvents() {
    const toggle = this.shadowRoot.querySelector(".js-toggle");
    const copy   = this.shadowRoot.querySelector(".js-copy");

    if (toggle) {
      toggle.addEventListener("click", () => {
        this._masked = !this._masked;
        this._render();
        this._bindEvents();
      });
    }

    if (copy) {
      copy.addEventListener("click", () => {
        navigator.clipboard.writeText(this.value).then(() => {
          copy.classList.add("copied");
          copy.textContent = "✓";
          setTimeout(() => {
            copy.classList.remove("copied");
            copy.textContent = "📋";
          }, 1500);
        });
      });
    }
  }
}

customElements.define("secret-field", SecretField);
