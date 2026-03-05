/**
 * <agent-card> Web Component
 *
 * Displays an agent's name, role, state badge, and model info.
 * Updates live via attribute changes.
 *
 * Attributes: agent-id, name, role, state, model
 */
class AgentCard extends HTMLElement {
  static get observedAttributes() {
    return ["agent-id", "name", "role", "state", "model"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback() {
    this.render();
  }

  get agentId() { return this.getAttribute("agent-id") || ""; }
  get agentName() { return this.getAttribute("name") || "Unknown"; }
  get role() { return this.getAttribute("role") || "executor"; }
  get state() { return this.getAttribute("state") || "idle"; }
  get model() { return this.getAttribute("model") || "—"; }

  _stateColor(state) {
    const map = {
      idle: "var(--color-success, #4ade80)",
      working: "var(--color-warning, #facc15)",
      paused: "var(--color-info, #60a5fa)",
      error: "var(--color-error, #ef4444)",
      terminated: "var(--text-muted, #94a3b8)",
    };
    return map[state.toLowerCase()] || map.idle;
  }

  _roleIcon(role) {
    const map = { executor: "⚙️", supervisor: "👁️", specialist: "🔬" };
    return map[role.toLowerCase()] || "⚙️";
  }

  render() {
    const s = this.state.toLowerCase();
    const color = this._stateColor(s);
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          cursor: pointer;
        }
        .card {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          backdrop-filter: blur(var(--glass-blur, 12px));
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-radius: var(--radius-lg, 16px);
          padding: var(--space-4, 1rem);
          transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .card:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }
        .header {
          display: flex;
          align-items: center;
          gap: var(--space-2, 0.5rem);
          margin-bottom: var(--space-2, 0.5rem);
        }
        .icon { font-size: 1.4rem; }
        .name {
          font-weight: 600;
          color: var(--text-primary, #f1f5f9);
          font-size: 0.95rem;
          flex: 1;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .badge {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 0.7rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          padding: 2px 8px;
          border-radius: 9999px;
          background: ${color}22;
          color: ${color};
        }
        .badge::before {
          content: "";
          width: 6px; height: 6px;
          border-radius: 50%;
          background: ${color};
          ${s === "working" ? "animation: pulse 1.5s infinite;" : ""}
        }
        .meta {
          font-size: 0.8rem;
          color: var(--text-muted, #94a3b8);
          margin-top: var(--space-1, 0.25rem);
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      </style>
      <div class="card" part="card">
        <div class="header">
          <span class="icon">${this._roleIcon(this.role)}</span>
          <span class="name">${this.agentName}</span>
          <span class="badge">${this.state}</span>
        </div>
        <div class="meta">${this.model} · ${this.role}</div>
      </div>
    `;
  }
}

customElements.define("agent-card", AgentCard);
