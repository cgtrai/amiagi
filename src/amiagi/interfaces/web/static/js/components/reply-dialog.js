class ReplyDialog extends HTMLElement {
  static get observedAttributes() {
    return ["placeholder", "button-label", "rows", "disabled"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this._render();
  }

  attributeChangedCallback() {
    this._render();
  }

  get value() {
    const input = this.shadowRoot && this.shadowRoot.getElementById("reply-text");
    return input ? input.value : "";
  }

  set value(nextValue) {
    const input = this.shadowRoot && this.shadowRoot.getElementById("reply-text");
    if (input) input.value = nextValue || "";
  }

  clear() {
    this.value = "";
  }

  _render() {
    const placeholder = this.getAttribute("placeholder") || "Type your reply…";
    const buttonLabel = this.getAttribute("button-label") || "Send";
    const rows = this.getAttribute("rows") || "3";
    const disabled = this.getAttribute("disabled") === "true";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .reply-wrap { display: grid; gap: 0.75rem; }
        textarea {
          width: 100%;
          min-height: 84px;
          resize: vertical;
          padding: 0.8rem 0.9rem;
          border-radius: 12px;
          border: 1px solid rgba(255,255,255,0.1);
          background: rgba(0,0,0,0.16);
          color: var(--glass-text-primary, #fff);
          font: inherit;
          box-sizing: border-box;
        }
        button {
          justify-self: end;
          border: 1px solid rgba(255,255,255,0.12);
          background: rgba(255,255,255,0.08);
          color: inherit;
          border-radius: 10px;
          padding: 0.55rem 0.9rem;
          cursor: pointer;
          font: inherit;
          font-weight: 600;
        }
        button:disabled,
        textarea:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
      </style>
      <div class="reply-wrap">
        <textarea id="reply-text" rows="${rows}" placeholder="${this._esc(placeholder)}" ${disabled ? "disabled" : ""}></textarea>
        <button id="reply-send" ${disabled ? "disabled" : ""}>${this._esc(buttonLabel)}</button>
      </div>
    `;

    const button = this.shadowRoot.getElementById("reply-send");
    const textarea = this.shadowRoot.getElementById("reply-text");
    if (button && textarea) {
      button.addEventListener("click", () => {
        const message = textarea.value.trim();
        if (!message) return;
        this.dispatchEvent(new CustomEvent("reply-submit", {
          bubbles: true,
          composed: true,
          detail: { message: message },
        }));
      });
    }
  }

  _esc(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
}

customElements.define("reply-dialog", ReplyDialog);