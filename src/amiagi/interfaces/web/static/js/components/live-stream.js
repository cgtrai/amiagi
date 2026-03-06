/**
 * <live-stream> Web Component
 *
 * Real-time event stream panel that connects to /ws/events and displays
 * timestamped entries.  Used in the Supervisor page.
 *
 * Attributes: max-entries (default 500), ws-path (default /ws/events)
 */
class LiveStream extends HTMLElement {
  static get observedAttributes() {
    return ["max-entries", "ws-path"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._ws = null;
    this._reconnectTimer = null;
  }

  connectedCallback() {
    this.render();
    this._connectWS();
  }

  disconnectedCallback() {
    this._closeWS();
  }

  attributeChangedCallback() {
    // max-entries change takes effect on next append
  }

  get maxEntries() {
    return parseInt(this.getAttribute("max-entries") || "500", 10);
  }

  get wsPath() {
    return this.getAttribute("ws-path") || "/ws/events";
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: var(--font-mono, 'JetBrains Mono', monospace);
          font-size: 0.78rem;
          overflow-y: auto;
          max-height: 400px;
          padding: 0.5rem;
          background: rgba(0,0,0,0.15);
          border-radius: 8px;
        }
        .entry {
          padding: 2px 0;
          border-bottom: 1px solid rgba(255,255,255,0.04);
          display: flex;
          gap: 0.5em;
        }
        .entry-ts {
          color: var(--text-muted, #94a3b8);
          flex-shrink: 0;
          min-width: 5.5em;
        }
        .entry-text {
          color: var(--text-primary, #e2e8f0);
          word-break: break-word;
        }
        .entry--info .entry-text { color: var(--color-info, #60a5fa); }
        .entry--warn .entry-text { color: var(--color-warning, #facc15); }
        .entry--error .entry-text { color: var(--color-error, #ef4444); }
      </style>
      <div id="entries"></div>
    `;
  }

  append(text, level) {
    const container = this.shadowRoot.getElementById("entries");
    if (!container) return;

    const el = document.createElement("div");
    el.className = "entry" + (level ? " entry--" + level : "");

    const ts = document.createElement("span");
    ts.className = "entry-ts";
    ts.textContent = new Date().toLocaleTimeString();

    const tx = document.createElement("span");
    tx.className = "entry-text";
    tx.textContent = text;

    el.appendChild(ts);
    el.appendChild(tx);
    container.appendChild(el);

    // Trim excess
    while (container.children.length > this.maxEntries) {
      container.removeChild(container.firstChild);
    }

    // Auto-scroll
    this.scrollTop = this.scrollHeight;
  }

  _connectWS() {
    this._closeWS();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = proto + "//" + location.host + this.wsPath;
    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
      this.append("Connected", "info");
    };

    this._ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "heartbeat") return;
        const label = msg.type || "event";
        const detail = msg.agent_id ? " [" + msg.agent_id + "]" : "";
        this.append(label + detail);

        // Dispatch custom event so parent JS can react
        this.dispatchEvent(new CustomEvent("stream-event", {
          bubbles: true,
          detail: msg,
        }));
      } catch { /* non-JSON */ }
    };

    this._ws.onclose = () => {
      this.append("Disconnected — retrying in 5s", "warn");
      this._reconnectTimer = setTimeout(() => this._connectWS(), 5000);
    };
  }

  _closeWS() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._ws) {
      this._ws.onclose = null;
      this._ws.close();
      this._ws = null;
    }
  }
}

customElements.define("live-stream", LiveStream);
