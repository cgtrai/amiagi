/**
 * <event-ticker> Web Component
 *
 * Scrolling event log that displays real-time EventBus messages.
 * New events appear at the top with a fade-in animation.
 *
 * Attributes: max-items (default 100)
 */
class EventTicker extends HTMLElement {
  static get observedAttributes() {
    return ["max-items"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
  }

  connectedCallback() {
    this._render();
    this._list = this.shadowRoot.querySelector(".event-list");
  }

  get maxItems() {
    return parseInt(this.getAttribute("max-items") || "100", 10);
  }

  /**
   * Add an event to the ticker.
   * @param {{type: string, message?: string, panel?: string, actor?: string, state?: string, timestamp?: string}} evt
   */
  addEvent(evt) {
    this._events.unshift(evt);
    if (this._events.length > this.maxItems) {
      this._events.pop();
    }
    this._prependRow(evt);
    // Trim DOM
    if (this._list && this._list.children.length > this.maxItems) {
      this._list.removeChild(this._list.lastChild);
    }
  }

  clear() {
    this._events = [];
    if (this._list) this._list.innerHTML = "";
  }

  _typeColor(type) {
    const map = {
      log: "#60a5fa",
      actor_state: "#a78bfa",
      cycle_finished: "#4ade80",
      supervisor_message: "#facc15",
      error: "#ef4444",
    };
    return map[type] || "#94a3b8";
  }

  _typeIcon(type) {
    const map = {
      log: "📝", actor_state: "🔄", cycle_finished: "✅",
      supervisor_message: "👁️", error: "❌",
    };
    return map[type] || "📌";
  }

  _prependRow(evt) {
    if (!this._list) return;
    const row = document.createElement("div");
    row.className = "event-row";
    const ts = evt.timestamp
      ? new Date(evt.timestamp).toLocaleTimeString()
      : new Date().toLocaleTimeString();
    const text = evt.message || evt.state || evt.event || JSON.stringify(evt);
    row.innerHTML = `
      <span class="evt-icon">${this._typeIcon(evt.type)}</span>
      <span class="evt-type" style="color:${this._typeColor(evt.type)}">${evt.type}</span>
      <span class="evt-text">${this._esc(String(text).slice(0, 200))}</span>
      <span class="evt-time">${ts}</span>
    `;
    this._list.prepend(row);
  }

  _esc(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; overflow-y: auto; max-height: 400px; }
        .event-list {
          display: flex;
          flex-direction: column;
          gap: 2px;
          padding: var(--space-2, 0.5rem);
        }
        .event-row {
          display: flex;
          align-items: center;
          gap: var(--space-2, 0.5rem);
          padding: 4px var(--space-2, 0.5rem);
          border-radius: var(--radius-sm, 8px);
          font-size: 0.8rem;
          animation: fadeIn 0.3s ease;
          background: var(--glass-bg, rgba(255,255,255,0.03));
        }
        .event-row:hover {
          background: rgba(255,255,255,0.06);
        }
        .evt-icon { flex-shrink: 0; font-size: 0.85rem; }
        .evt-type {
          flex-shrink: 0;
          font-weight: 600;
          font-size: 0.7rem;
          text-transform: uppercase;
          min-width: 90px;
        }
        .evt-text {
          flex: 1;
          color: var(--text-secondary, #cbd5e1);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .evt-time {
          flex-shrink: 0;
          color: var(--text-muted, #94a3b8);
          font-size: 0.7rem;
          font-variant-numeric: tabular-nums;
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      </style>
      <div class="event-list"></div>
    `;
  }
}

customElements.define("event-ticker", EventTicker);
