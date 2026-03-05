/**
 * <session-timeline> Web Component
 *
 * Renders a vertical timeline of events for a given session.
 * Fetches events from GET /api/sessions/{sessionId}/events.
 *
 * Attributes:
 *   session-id  – the session identifier (required)
 *   auto-poll   – polling interval in seconds (0 = disabled, default 10)
 */
class SessionTimeline extends HTMLElement {
  static get observedAttributes() {
    return ["session-id", "auto-poll"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
    this._pollTimer = null;
    this._playing = false;
    this._playIndex = 0;
    this._playTimer = null;
    this._autoScroll = true;
  }

  connectedCallback() {
    this._render();
    this._container = this.shadowRoot.querySelector(".tl-items");
    this._btnPlay = this.shadowRoot.querySelector(".tl-btn-play");
    this._scrubber = this.shadowRoot.querySelector(".tl-scrubber");
    this._scrubberLabel = this.shadowRoot.querySelector(".tl-scrubber-label");
    this._btnPlay?.addEventListener("click", () => this._togglePlay());
    this._scrubber?.addEventListener("input", (e) => this._onScrub(e));
    if (this.sessionId) this.load();
    this._startPoll();
  }

  disconnectedCallback() {
    this._stopPoll();
    this._stopPlay();
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === "session-id" && oldVal !== newVal && this._container) {
      this._events = [];
      this._container.innerHTML = "";
      if (newVal) this.load();
    }
    if (name === "auto-poll") {
      this._stopPoll();
      this._startPoll();
    }
  }

  get sessionId() {
    return this.getAttribute("session-id") || "";
  }

  get autoPoll() {
    return parseInt(this.getAttribute("auto-poll") || "10", 10);
  }

  /** Fetch events from the API and render them. */
  async load() {
    if (!this.sessionId) return;
    try {
      const resp = await fetch("/api/sessions/" + encodeURIComponent(this.sessionId) + "/events");
      if (!resp.ok) {
        this._showError("Failed to load events (" + resp.status + ")");
        return;
      }
      const data = await resp.json();
      const events = data.events || data || [];
      this._events = events;
      this._renderItems();
    } catch (err) {
      this._showError("Network error");
    }
  }

  /** Programmatically append a single event (real-time). */
  addEvent(evt) {
    this._events.push(evt);
    if (this._container) this._appendRow(evt);
  }

  clear() {
    this._events = [];
    if (this._container) this._container.innerHTML = "";
  }

  /* ---------- Play / Pause ---------- */

  _togglePlay() {
    if (this._playing) {
      this._stopPlay();
    } else {
      this._startPlay();
    }
  }

  _startPlay() {
    if (this._events.length === 0) return;
    this._playing = true;
    this._autoScroll = true;
    if (this._btnPlay) this._btnPlay.textContent = "⏸ Pause";
    this._container.innerHTML = "";
    this._playIndex = 0;
    this._updateScrubber();
    const speed = parseInt(this.getAttribute("play-speed") || "400", 10);
    this._playTimer = setInterval(() => {
      if (this._playIndex >= this._events.length) {
        this._stopPlay();
        return;
      }
      this._appendRow(this._events[this._playIndex]);
      this._scrollToBottom();
      this._playIndex++;
      this._updateScrubber();
    }, speed);
  }

  _stopPlay() {
    this._playing = false;
    if (this._btnPlay) this._btnPlay.textContent = "▶ Play";
    if (this._playTimer) {
      clearInterval(this._playTimer);
      this._playTimer = null;
    }
  }

  _scrollToBottom() {
    if (!this._autoScroll || !this._container) return;
    this._container.scrollTop = this._container.scrollHeight;
  }

  /* ---------- Rendering helpers ---------- */

  _typeColor(type) {
    const map = {
      created: "#60a5fa",
      started: "#a78bfa",
      completed: "#4ade80",
      failed: "#ef4444",
      message: "#facc15",
      supervisor: "#f97316",
      tool_call: "#22d3ee",
      cycle: "#818cf8",
    };
    return map[type] || "#94a3b8";
  }

  _typeIcon(type) {
    const map = {
      created: "🆕", started: "▶️", completed: "✅",
      failed: "❌", message: "💬", supervisor: "👁️",
      tool_call: "🔧", cycle: "🔄",
    };
    return map[type] || "📌";
  }

  _renderItems() {
    if (!this._container) return;
    this._container.innerHTML = "";
    this._updateScrubberMax();
    if (this._events.length === 0) {
      this._container.innerHTML = '<div class="tl-empty">No events yet</div>';
      return;
    }
    this._events.forEach(evt => this._appendRow(evt));
    this._updateScrubber();
  }

  /* ---------- Scrubber ---------- */

  _updateScrubberMax() {
    if (!this._scrubber) return;
    this._scrubber.max = Math.max(this._events.length - 1, 0);
  }

  _updateScrubber() {
    if (!this._scrubber) return;
    this._scrubber.max = Math.max(this._events.length - 1, 0);
    this._scrubber.value = Math.min(this._playIndex || this._events.length - 1, this._events.length - 1);
    if (this._scrubberLabel) {
      this._scrubberLabel.textContent = (parseInt(this._scrubber.value, 10) + 1) + " / " + this._events.length;
    }
  }

  _onScrub(e) {
    const idx = parseInt(e.target.value, 10);
    // Pause playback if active
    if (this._playing) this._stopPlay();
    // Re-render up to idx
    if (!this._container) return;
    this._container.innerHTML = "";
    for (let i = 0; i <= idx; i++) {
      this._appendRow(this._events[i]);
    }
    this._playIndex = idx + 1;
    this._scrollToBottom();
    if (this._scrubberLabel) {
      this._scrubberLabel.textContent = (idx + 1) + " / " + this._events.length;
    }
  }

  _appendRow(evt) {
    if (!this._container) return;
    const row = document.createElement("div");
    row.className = "tl-row";
    const ts = evt.timestamp
      ? new Date(evt.timestamp).toLocaleTimeString()
      : "";
    const label = evt.message || evt.description || evt.event || evt.type || "";
    const actor = evt.actor || evt.agent_id || "";
    row.innerHTML = `
      <div class="tl-dot" style="background:${this._typeColor(evt.type)}"></div>
      <div class="tl-content">
        <div class="tl-header">
          <span class="tl-icon">${this._typeIcon(evt.type)}</span>
          <span class="tl-type" style="color:${this._typeColor(evt.type)}">${this._esc(evt.type || "event")}</span>
          ${actor ? '<span class="tl-actor">' + this._esc(actor) + '</span>' : ''}
          <span class="tl-time">${ts}</span>
        </div>
        <div class="tl-body">${this._esc(String(label).slice(0, 500))}</div>
      </div>
    `;
    this._container.appendChild(row);
    if (this._autoScroll) this._scrollToBottom();
  }

  _showError(msg) {
    if (!this._container) return;
    this._container.innerHTML = '<div class="tl-error">' + this._esc(msg) + '</div>';
  }

  _esc(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  _startPoll() {
    if (this.autoPoll > 0) {
      this._pollTimer = setInterval(() => this.load(), this.autoPoll * 1000);
    }
  }

  _stopPoll() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          overflow-y: auto;
          max-height: 600px;
          padding: var(--space-2, 0.5rem);
        }
        .tl-items {
          position: relative;
          padding-left: 24px;
        }
        .tl-items::before {
          content: '';
          position: absolute;
          left: 7px;
          top: 0;
          bottom: 0;
          width: 2px;
          background: var(--border-secondary, #334155);
        }
        .tl-row {
          position: relative;
          display: flex;
          gap: var(--space-2, 0.5rem);
          margin-bottom: var(--space-3, 0.75rem);
          animation: fadeIn 0.3s ease;
        }
        .tl-dot {
          position: absolute;
          left: -20px;
          top: 4px;
          width: 12px;
          height: 12px;
          border-radius: 50%;
          flex-shrink: 0;
          border: 2px solid var(--surface-1, #0f172a);
        }
        .tl-content {
          flex: 1;
          background: var(--glass-bg, rgba(255,255,255,0.03));
          border-radius: var(--radius-sm, 8px);
          padding: var(--space-2, 0.5rem) var(--space-3, 0.75rem);
        }
        .tl-header {
          display: flex;
          align-items: center;
          gap: var(--space-2, 0.5rem);
          margin-bottom: 2px;
        }
        .tl-icon { font-size: 0.85rem; }
        .tl-type {
          font-weight: 600;
          font-size: 0.75rem;
          text-transform: uppercase;
        }
        .tl-actor {
          font-size: 0.7rem;
          color: var(--text-secondary, #94a3b8);
          background: rgba(255,255,255,0.06);
          padding: 1px 6px;
          border-radius: 4px;
        }
        .tl-time {
          margin-left: auto;
          font-size: 0.7rem;
          color: var(--text-muted, #64748b);
          font-variant-numeric: tabular-nums;
        }
        .tl-body {
          font-size: 0.82rem;
          color: var(--text-secondary, #cbd5e1);
          line-height: 1.4;
          word-break: break-word;
        }
        .tl-empty, .tl-error {
          text-align: center;
          padding: var(--space-4, 1rem);
          color: var(--text-muted, #64748b);
          font-size: 0.85rem;
        }
        .tl-error { color: var(--status-error, #ef4444); }
        .tl-controls {
          display: flex;
          gap: var(--space-2, 0.5rem);
          margin-bottom: var(--space-2, 0.5rem);
        }
        .tl-btn-play {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          color: var(--text-primary, #e2e8f0);
          border: 1px solid var(--border-secondary, #334155);
          border-radius: var(--radius-sm, 8px);
          padding: 4px 12px;
          cursor: pointer;
          font-size: 0.8rem;
        }
        .tl-btn-play:hover { background: rgba(255,255,255,0.1); }
        .tl-scrubber-wrap {
          display: flex;
          align-items: center;
          gap: var(--space-2, 0.5rem);
          flex: 1;
        }
        .tl-scrubber {
          flex: 1;
          -webkit-appearance: none;
          appearance: none;
          height: 4px;
          background: var(--border-secondary, #334155);
          border-radius: 2px;
          outline: none;
          cursor: pointer;
        }
        .tl-scrubber::-webkit-slider-thumb {
          -webkit-appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: var(--accent-primary, #6366f1);
          border: 2px solid var(--surface-1, #0f172a);
          cursor: pointer;
        }
        .tl-scrubber::-moz-range-thumb {
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: var(--accent-primary, #6366f1);
          border: 2px solid var(--surface-1, #0f172a);
          cursor: pointer;
        }
        .tl-scrubber-label {
          font-size: 0.7rem;
          color: var(--text-muted, #64748b);
          white-space: nowrap;
          min-width: 50px;
          text-align: right;
          font-variant-numeric: tabular-nums;
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      </style>
      <div class="tl-controls">
        <button class="tl-btn-play">▶ Play</button>
        <div class="tl-scrubber-wrap">
          <input type="range" class="tl-scrubber" min="0" max="0" value="0"/>
          <span class="tl-scrubber-label">0 / 0</span>
        </div>
      </div>
      <div class="tl-items"></div>
    `;
  }
}

customElements.define("session-timeline", SessionTimeline);
