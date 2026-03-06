/**
 * <inbox-badge> Web Component
 *
 * Displays a pending-item count badge.  Polls /api/inbox/count and
 * listens on the parent <live-stream> 'stream-event' for real-time
 * updates.
 *
 * Attributes: poll-interval (ms, default 30000)
 */
class InboxBadge extends HTMLElement {
  static get observedAttributes() {
    return ["poll-interval"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timer = null;
    this._count = 0;
    this._onStreamEvent = this._onStreamEvent.bind(this);
  }

  connectedCallback() {
    this.render();
    this._fetch();
    this._startPoll();
    // Listen for live-stream events bubbling up
    document.addEventListener("stream-event", this._onStreamEvent);
  }

  disconnectedCallback() {
    this._stopPoll();
    document.removeEventListener("stream-event", this._onStreamEvent);
  }

  attributeChangedCallback() {
    this._stopPoll();
    this._startPoll();
  }

  get pollInterval() {
    return parseInt(this.getAttribute("poll-interval") || "30000", 10);
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 1.3em;
          height: 1.3em;
          padding: 0 0.4em;
          border-radius: 999px;
          font-size: 0.7rem;
          font-weight: 700;
          font-family: var(--font-sans, system-ui, sans-serif);
          background: var(--color-error, #ef4444);
          color: #fff;
          transition: opacity 0.2s, transform 0.2s;
        }
        .badge[data-count="0"] {
          opacity: 0;
          transform: scale(0.8);
        }
      </style>
      <span class="badge" id="badge" data-count="0">0</span>
    `;
  }

  async _fetch() {
    try {
      const r = await fetch("/api/inbox/count");
      const d = await r.json();
      this._count = d.pending || 0;
      this._update();
    } catch { /* */ }
  }

  _update() {
    const el = this.shadowRoot.getElementById("badge");
    if (!el) return;
    el.textContent = this._count;
    el.dataset.count = String(this._count);
  }

  _onStreamEvent(e) {
    const type = e.detail && e.detail.type;
    if (type === "inbox.new" || type === "inbox.resolved") {
      this._fetch();
    }
  }

  _startPoll() {
    this._timer = setInterval(() => this._fetch(), this.pollInterval);
  }

  _stopPoll() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }
}

customElements.define("inbox-badge", InboxBadge);
