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
    this._entries = [];
    this._filter = { channel: 'all', level: 'all' };
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
        .entry-body {
          display: flex;
          flex-direction: column;
          gap: 0.18rem;
          min-width: 0;
          flex: 1;
        }
        .entry-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 0.3rem;
        }
        .entry-chip {
          display: inline-flex;
          align-items: center;
          padding: 0.08rem 0.38rem;
          border-radius: 999px;
          font-size: 0.66rem;
          line-height: 1.2;
          background: rgba(148, 163, 184, 0.14);
          color: var(--text-muted, #cbd5e1);
          border: 1px solid rgba(148, 163, 184, 0.18);
        }
        .entry-chip--source {
          background: rgba(96, 165, 250, 0.12);
          color: #bfdbfe;
        }
        .entry-chip--target {
          background: rgba(52, 211, 153, 0.12);
          color: #bbf7d0;
        }
        .entry-chip--type {
          background: rgba(167, 139, 250, 0.12);
          color: #ddd6fe;
        }
        .entry--info .entry-text { color: var(--color-info, #60a5fa); }
        .entry--warn .entry-text { color: var(--color-warning, #facc15); }
        .entry--error .entry-text { color: var(--color-error, #ef4444); }
        .stream-source-executor { border-left: 3px solid var(--glass-accent-blue, #60a5fa); }
        .stream-source-supervisor { border-left: 3px solid var(--glass-accent-purple, #a78bfa); }
        .stream-source-system { border-left: 3px solid var(--glass-accent-gray, #64748b); }
        .stream-source-user { border-left: 3px solid var(--glass-accent-green, #34d399); }
        .entry-save {
          opacity: 0; transition: opacity 0.15s; cursor: pointer; background: none;
          border: none; font-size: 0.75rem; padding: 0 2px; flex-shrink: 0;
        }
        .entry:hover .entry-save { opacity: 0.7; }
        .entry-save:hover { opacity: 1 !important; }
      </style>
      <div id="entries"></div>
    `;
  }

  _formatMessage(msg) {
    const type = String((msg && msg.type) || "event");
    const message = String((msg && msg.message) || "").trim();
    const summary = String((msg && msg.summary) || "").trim();
    const agentId = String((msg && msg.agent_id) || "").trim();
    const targetAgent = String((msg && msg.target_agent) || "").trim();

    if (summary) {
      return summary;
    }
    if (type === "log" && message) {
      return agentId ? `[${agentId}] ${message}` : message;
    }
    if (type === "error" && message) {
      return message;
    }
    if (type === "actor_state") {
      const actor = String((msg && msg.actor) || agentId || "actor").trim();
      const state = String((msg && msg.state) || "").trim();
      const event = String((msg && msg.event) || "").trim();
      return [actor, state, event].filter(Boolean).join(" · ");
    }
    if (type === "supervisor_message") {
      const reasonCode = String((msg && msg.reason_code) || "").trim();
      const notes = String((msg && msg.notes) || "").trim();
      const answer = String((msg && msg.answer) || "").trim();
      return notes || answer || reasonCode || "Supervisor update";
    }
    if (type === "operator.input.accepted") {
      return targetAgent
        ? `Operator → ${targetAgent}: ${message}`
        : `Operator → all: ${message}`;
    }
    if (type === "agent.lifecycle") {
      const action = String((msg && msg.action) || "").trim();
      return ["Agent", agentId, action].filter(Boolean).join(" ");
    }
    if (type.startsWith("system.current_task.")) {
      const tail = type.split(".").slice(-1)[0] || "updated";
      return agentId ? `Current task ${tail} [${agentId}]` : `Current task ${tail}`;
    }
    if (type === "agent.spawned") {
      return agentId ? `Agent spawned: ${agentId}` : "Agent spawned";
    }
    return agentId ? `${type} [${agentId}]` : type;
  }

  _eventLevel(msg) {
    const type = String((msg && msg.type) || "");
    if (type === "error" || type.endsWith(".failed")) return "error";
    if (type.endsWith(".paused") || type.endsWith(".stopped") || type.endsWith(".retry") || type.endsWith(".retried")) return "warn";
    if (type === "operator.input.accepted" || type === "agent.spawned") return "info";
    return undefined;
  }

  _buildMetaChips(meta) {
    const chips = [];
    const sourceLabel = String((meta && meta.source_label) || '').trim();
    const targetAgent = String((meta && meta.target_agent) || '').trim();
    const targetScope = String((meta && meta.target_scope) || '').trim();
    const type = String((meta && meta.type) || '').trim();
    const agentId = String((meta && meta.agent_id) || '').trim();

    if (sourceLabel) {
      chips.push({ text: sourceLabel, className: 'entry-chip entry-chip--source' });
    }
    if (targetAgent) {
      chips.push({ text: 'to ' + targetAgent, className: 'entry-chip entry-chip--target' });
    } else if (targetScope === 'broadcast') {
      chips.push({ text: 'to all', className: 'entry-chip entry-chip--target' });
    }
    if (type) {
      chips.push({ text: type, className: 'entry-chip entry-chip--type' });
    }
    if (agentId && !sourceLabel) {
      chips.push({ text: agentId, className: 'entry-chip' });
    }
    return chips;
  }

  setFilter(filter) {
    this._filter = Object.assign({}, this._filter, filter || {});
    this._renderEntries();
  }

  clearEntries() {
    this._entries = [];
    this._renderEntries();
  }

  _matchesFilter(entry) {
    const channelFilter = String((this._filter && this._filter.channel) || 'all');
    const levelFilter = String((this._filter && this._filter.level) || 'all');
    if (channelFilter !== 'all' && entry.channel !== channelFilter) {
      return false;
    }
    if (levelFilter !== 'all' && String(entry.level || '') !== levelFilter) {
      return false;
    }
    return true;
  }

  _renderEntries() {
    const container = this.shadowRoot.getElementById("entries");
    if (!container) return;
    container.innerHTML = '';
    this._entries.filter((entry) => this._matchesFilter(entry)).forEach((entry) => {
      container.appendChild(this._renderEntryElement(entry));
    });
    this.scrollTop = this.scrollHeight;
  }

  _renderEntryElement(entry) {
    const sourceClass = {
      executor: 'stream-source-executor',
      supervisor: 'stream-source-supervisor',
      system: 'stream-source-system',
      user: 'stream-source-user',
    }[entry.channel] || '';

    const el = document.createElement("div");
    el.className = "entry" + (entry.level ? " entry--" + entry.level : "") + (sourceClass ? " " + sourceClass : "");

    const ts = document.createElement("span");
    ts.className = "entry-ts";
    ts.textContent = entry.ts;

    const tx = document.createElement("span");
    tx.className = "entry-text";
    tx.textContent = entry.text;

    const body = document.createElement("div");
    body.className = "entry-body";

    const chips = this._buildMetaChips(entry.meta);
    if (chips.length) {
      const metaRow = document.createElement('div');
      metaRow.className = 'entry-meta';
      chips.forEach((chip) => {
        const chipEl = document.createElement('span');
        chipEl.className = chip.className;
        chipEl.textContent = chip.text;
        metaRow.appendChild(chipEl);
      });
      body.appendChild(metaRow);
    }
    body.appendChild(tx);

    const saveBtn = document.createElement("button");
    saveBtn.className = "entry-save";
    saveBtn.textContent = "\uD83D\uDCCC";
    saveBtn.title = "Save as snippet";
    saveBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      fetch("/api/snippets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: entry.text, title: entry.text.slice(0, 50), source: "stream", tags: ["auto-save"] }),
      }).then((response) => {
        if (typeof showToast === "function") {
          showToast(response.ok ? "Snippet saved" : "Failed to save snippet", response.ok ? "success" : "error");
        }
      }).catch(() => {});
    });

    el.appendChild(ts);
    el.appendChild(body);
    el.appendChild(saveBtn);
    return el;
  }

  append(text, level, channel, meta) {
    this._entries.push({
      text: text,
      level: level,
      channel: channel || 'system',
      meta: meta || {},
      ts: new Date().toLocaleTimeString(),
    });
    while (this._entries.length > this.maxEntries) {
      this._entries.shift();
    }
    this._renderEntries();
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
        const channel = msg.channel || "system";
        this.append(this._formatMessage(msg), this._eventLevel(msg), channel, msg);

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
