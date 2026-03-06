/**
 * <approval-card> Web Component
 *
 * Renders a single inbox item as an interactive card with approve/reject
 * buttons.  For "ask_human" items, includes a reply textarea.
 *
 * Attributes: item-id, title, body, item-type, status, source-type,
 *             source-id, node-id, agent-id, created-at
 *
 * Events:
 *   approval-action  — { detail: { itemId, action, message? } }
 */
class ApprovalCard extends HTMLElement {
  static get observedAttributes() {
    return [
      "item-id", "title", "body", "item-type", "status",
      "source-type", "source-id", "node-id", "agent-id", "created-at",
    ];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._renderPending = false;
  }

  connectedCallback() {
    this._scheduleRender();
  }

  attributeChangedCallback() {
    this._scheduleRender();
  }

  _scheduleRender() {
    if (this._renderPending) return;
    this._renderPending = true;
    queueMicrotask(() => {
      this._renderPending = false;
      this.render();
    });
  }

  /* ── Attribute accessors ─────────────────────────────── */
  get itemId() { return this.getAttribute("item-id") || ""; }
  get cardTitle() { return this.getAttribute("title") || "Unnamed"; }
  get body() { return this.getAttribute("body") || ""; }
  get itemType() { return this.getAttribute("item-type") || "gate_approval"; }
  get status() { return this.getAttribute("status") || "pending"; }
  get sourceType() { return this.getAttribute("source-type") || ""; }
  get sourceId() { return this.getAttribute("source-id") || ""; }
  get nodeId() { return this.getAttribute("node-id") || ""; }
  get agentId() { return this.getAttribute("agent-id") || ""; }
  get createdAt() { return this.getAttribute("created-at") || ""; }

  /* ── Helpers ─────────────────────────────────────────── */
  _timeAgo(iso) {
    if (!iso) return "";
    const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 60) return secs + "s ago";
    if (secs < 3600) return Math.floor(secs / 60) + "m ago";
    if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
    return Math.floor(secs / 86400) + "d ago";
  }

  _iconSvg() {
    if (this.itemType === "gate_approval") {
      return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/></svg>';
    }
    return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  }

  /* ── Render ──────────────────────────────────────────── */
  render() {
    const isPending = this.status === "pending";
    const isQuestion = this.itemType === "ask_human";
    const resolved = !isPending;

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          margin-bottom: 0.5rem;
        }
        .card {
          display: flex;
          align-items: flex-start;
          gap: 0.75rem;
          padding: 0.75rem 1rem;
          border-radius: 10px;
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.06);
          transition: background 0.15s;
          cursor: pointer;
        }
        .card:hover { background: rgba(255,255,255,0.08); }
        .card.resolved { opacity: 0.55; }
        .icon { flex-shrink: 0; color: var(--color-info, #60a5fa); }
        .icon.gate { color: var(--color-warning, #facc15); }
        .body { flex: 1; min-width: 0; }
        .title {
          font-weight: 600;
          font-size: 0.85rem;
          color: var(--text-primary, #e2e8f0);
          margin-bottom: 2px;
        }
        .excerpt {
          font-size: 0.75rem;
          color: var(--text-muted, #94a3b8);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .meta {
          font-size: 0.7rem;
          color: var(--text-muted, #94a3b8);
          margin-top: 2px;
        }
        .status {
          font-size: 0.65rem;
          font-weight: 700;
          text-transform: uppercase;
          padding: 2px 6px;
          border-radius: 4px;
          align-self: center;
        }
        .status--pending { background: rgba(250,204,21,0.15); color: #facc15; }
        .status--approved { background: rgba(74,222,128,0.15); color: #4ade80; }
        .status--rejected { background: rgba(239,68,68,0.15); color: #ef4444; }
        .actions {
          display: flex;
          gap: 0.3rem;
          align-self: center;
          flex-shrink: 0;
        }
        .btn {
          border: none;
          border-radius: 6px;
          padding: 4px 10px;
          font-size: 0.75rem;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.15s;
        }
        .btn--approve { background: rgba(74,222,128,0.18); color: #4ade80; }
        .btn--approve:hover { background: rgba(74,222,128,0.3); }
        .btn--reject { background: rgba(239,68,68,0.18); color: #ef4444; }
        .btn--reject:hover { background: rgba(239,68,68,0.3); }
        .reply-area {
          margin-top: 0.5rem;
          display: ${isPending && isQuestion ? "flex" : "none"};
          gap: 0.3rem;
        }
        .reply-area textarea {
          flex: 1;
          resize: vertical;
          min-height: 2.5rem;
          padding: 0.4rem;
          font-size: 0.78rem;
          border-radius: 6px;
          border: 1px solid rgba(255,255,255,0.1);
          background: rgba(0,0,0,0.2);
          color: var(--text-primary, #e2e8f0);
          font-family: inherit;
        }
        .reply-area .btn { align-self: flex-end; }
        .time { font-size: 0.7rem; color: var(--text-muted, #94a3b8); flex-shrink: 0; }
      </style>
      <div class="card ${resolved ? "resolved" : ""}">
        <div class="icon ${this.itemType === "gate_approval" ? "gate" : ""}">
          ${this._iconSvg()}
        </div>
        <div class="body">
          <div class="title">${this._esc(this.cardTitle)}</div>
          <div class="excerpt">${this._esc(this.body.substring(0, 120))}</div>
          <div class="meta">${this._esc(this.sourceType)}${this.agentId ? " · " + this._esc(this.agentId) : ""}</div>
        </div>
        <span class="time">${this._timeAgo(this.createdAt)}</span>
        <span class="status status--${this.status}">${this.status}</span>
        ${isPending ? `
          <div class="actions">
            <button class="btn btn--approve" id="btn-approve">✓</button>
            <button class="btn btn--reject" id="btn-reject">✗</button>
          </div>` : ""}
      </div>
      <div class="reply-area">
        <textarea id="reply-text" placeholder="Type your reply…" rows="2"></textarea>
        <button class="btn btn--approve" id="btn-reply">Send</button>
      </div>
    `;

    this._bindEvents();
  }

  _esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  _bindEvents() {
    const approveBtn = this.shadowRoot.getElementById("btn-approve");
    const rejectBtn = this.shadowRoot.getElementById("btn-reject");
    const replyBtn = this.shadowRoot.getElementById("btn-reply");

    if (approveBtn) {
      approveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._dispatch("approve");
      });
    }
    if (rejectBtn) {
      rejectBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._dispatch("reject");
      });
    }
    if (replyBtn) {
      replyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const ta = this.shadowRoot.getElementById("reply-text");
        const msg = ta ? ta.value.trim() : "";
        if (msg) {
          this._dispatch("reply", msg);
        }
      });
    }
  }

  _dispatch(action, message) {
    this.dispatchEvent(new CustomEvent("approval-action", {
      bubbles: true,
      composed: true,
      detail: { itemId: this.itemId, action, message },
    }));
  }
}

customElements.define("approval-card", ApprovalCard);
