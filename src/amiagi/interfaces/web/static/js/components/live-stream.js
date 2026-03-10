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
    return ["max-entries", "ws-path", "thread-owner"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._ws = null;
    this._reconnectTimer = null;
    this._entries = [];
    this._nextEntryId = 1;
    this._filter = { channel: 'all', level: 'all' };
    this._entriesContainer = null;
    this._scrollFrame = null;
    this._reconnectAttempts = 0;
    this._maxReconnectDelay = 30000;
    this._lastEventId = 0;
    this._seenServerEventIds = new Set();
    this._lastRenderedSignature = null;
    this._lastRenderedAtMs = 0;
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

  get threadOwner() {
    return String(this.getAttribute("thread-owner") || "").trim();
  }

  _currentUserLabel() {
    const root = this.closest('.supervisor-main');
    if (!root || !root.dataset) {
      return 'Operator';
    }
    return String(root.dataset.currentUserLabel || '').trim() || 'Operator';
  }

  _resolveDisplayLabel(label) {
    const normalized = String(label || '').trim();
    if (!normalized) {
      return '';
    }
    if (normalized === 'Sponsor' || normalized === 'Operator') {
      return this._currentUserLabel();
    }
    return normalized;
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100%;
          min-height: var(--supervisor-screen-height, calc(var(--stream-row-height, 1.35rem) * 18));
          height: var(--supervisor-screen-height, calc(var(--stream-row-height, 1.35rem) * 18));
          box-sizing: border-box;
          font-family: var(--font-mono, 'JetBrains Mono', monospace);
          font-size: 0.78rem;
          overflow: hidden;
          padding: 0.5rem;
          background: rgba(0,0,0,0.15);
          border-radius: 8px;
        }
        .entries {
          height: 100%;
          overflow-y: auto;
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
        .entry-chip--status {
          background: rgba(250, 204, 21, 0.12);
          color: #fde68a;
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
      <div id="entries" class="entries"></div>
    `;
    this._entriesContainer = this.shadowRoot.getElementById("entries");
  }

  _formatMessage(msg) {
    const type = String((msg && (msg.message_type || msg.type)) || "event");
    const message = String((msg && msg.message) || "").trim();
    const summary = String((msg && msg.summary) || "").trim();
    const agentId = String((msg && msg.agent_id) || "").trim();
    const targetAgent = String((msg && (msg.to || msg.target_agent)) || "").trim();
    const sourceLabel = this._resolveDisplayLabel((msg && (msg.from || msg.source_label)) || 'Operator');
    const targetLabel = this._resolveDisplayLabel(targetAgent);

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
      return targetLabel
        ? `${sourceLabel} → ${targetLabel}: ${message}`
        : `${sourceLabel} → all: ${message}`;
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
    const sourceLabel = this._resolveDisplayLabel((meta && (meta.from || meta.source_label)) || '');
    const targetAgent = this._resolveDisplayLabel((meta && (meta.to || meta.target_agent)) || '');
    const targetScope = String((meta && meta.target_scope) || '').trim();
    const type = String((meta && (meta.message_type || meta.type)) || '').trim();
    const status = String((meta && meta.status) || '').trim();
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
    if (status) {
      chips.push({ text: status, className: 'entry-chip entry-chip--status' });
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
    this._seenServerEventIds.clear();
    this._lastRenderedSignature = null;
    this._lastRenderedAtMs = 0;
    this._renderEntries();
  }

  _buildDedupSignature(text, level, channel, meta) {
    const normalizedMeta = {
      type: String((meta && (meta.message_type || meta.type)) || '').trim(),
      source: String((meta && (meta.from || meta.source_label)) || '').trim(),
      target: String((meta && (meta.to || meta.target_agent || meta.target_scope)) || '').trim(),
      status: String((meta && meta.status) || '').trim(),
      threadOwners: Array.isArray(meta && meta.thread_owners)
        ? meta.thread_owners.map((value) => String(value || '').trim()).filter(Boolean)
        : [],
    };
    return JSON.stringify({
      text: String(text || '').trim(),
      level: String(level || '').trim(),
      channel: String(channel || '').trim(),
      meta: normalizedMeta,
    });
  }

  _isConsecutiveDuplicate(text, level, channel, meta, timestampText) {
    const signature = this._buildDedupSignature(text, level, channel, meta);
    const parsedTimestamp = timestampText ? new Date(timestampText) : null;
    const entryTimestampMs = parsedTimestamp && !Number.isNaN(parsedTimestamp.getTime())
      ? parsedTimestamp.getTime()
      : Date.now();
    if (
      this._lastRenderedSignature === signature
      && entryTimestampMs - this._lastRenderedAtMs <= 5000
    ) {
      return true;
    }
    this._lastRenderedSignature = signature;
    this._lastRenderedAtMs = entryTimestampMs;
    return false;
  }

  _normalizeEventId(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      return 0;
    }
    return Math.trunc(numeric);
  }

  _rememberServerEvent(meta) {
    const eventId = this._normalizeEventId(meta && meta.event_id);
    if (!eventId) {
      return 0;
    }
    this._seenServerEventIds.add(eventId);
    if (eventId > this._lastEventId) {
      this._lastEventId = eventId;
    }
    return eventId;
  }

  _hasSeenServerEvent(meta) {
    const eventId = this._normalizeEventId(meta && meta.event_id);
    if (!eventId) {
      return false;
    }
    return this._seenServerEventIds.has(eventId);
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

  _matchesThreadOwner(msg) {
    const owner = this.threadOwner;
    if (!owner) {
      return true;
    }
    const owners = [];
    if (msg && Array.isArray(msg.thread_owners)) {
      msg.thread_owners.forEach((value) => {
        const normalized = String(value || '').trim();
        if (normalized) {
          owners.push(normalized);
        }
      });
    }
    if (!owners.length && msg && msg.thread_owner) {
      const legacyOwner = String(msg.thread_owner || '').trim();
      if (legacyOwner) {
        owners.push(legacyOwner);
      }
    }
    if (!owners.length) {
      return false;
    }
    return owners.includes(owner);
  }

  _renderEntries() {
    const container = this._entriesContainer || this.shadowRoot.getElementById("entries");
    if (!container) return;
    container.innerHTML = '';
    this._entries.filter((entry) => this._matchesFilter(entry)).forEach((entry) => {
      container.appendChild(this._renderEntryElement(entry));
    });
    this._scheduleScrollToBottom();
  }

  _renderEntryElement(entry) {
    const sourceClass = {
      executor: 'stream-source-executor',
      supervisor: 'stream-source-supervisor',
      system: 'stream-source-system',
      user: 'stream-source-user',
    }[entry.channel] || '';

    const el = document.createElement("div");
    el.dataset.entryId = String(entry.id);
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

  _scheduleScrollToBottom() {
    if (this._scrollFrame != null) {
      cancelAnimationFrame(this._scrollFrame);
    }
    this._scrollFrame = requestAnimationFrame(() => {
      this._scrollFrame = null;
      if (this._entriesContainer) {
        this._entriesContainer.scrollTop = this._entriesContainer.scrollHeight;
      }
    });
  }

  _appendRenderedEntry(entry) {
    const container = this._entriesContainer || this.shadowRoot.getElementById("entries");
    if (!container || !this._matchesFilter(entry)) {
      return;
    }
    container.appendChild(this._renderEntryElement(entry));
    this._scheduleScrollToBottom();
  }

  _removeRenderedEntry(entry) {
    const container = this._entriesContainer || this.shadowRoot.getElementById("entries");
    if (!container || !entry) {
      return;
    }
    const node = container.querySelector('[data-entry-id="' + String(entry.id) + '"]');
    if (node) {
      node.remove();
    }
  }

  append(text, level, channel, meta, timestampText) {
    this._rememberServerEvent(meta);
    if (this._isConsecutiveDuplicate(text, level, channel, meta, timestampText)) {
      return;
    }
    const parsedTimestamp = timestampText ? new Date(timestampText) : null;
    const entry = {
      id: this._nextEntryId++,
      text: text,
      level: level,
      channel: channel || 'system',
      meta: meta || {},
      ts: parsedTimestamp && !Number.isNaN(parsedTimestamp.getTime())
        ? parsedTimestamp.toLocaleTimeString()
        : new Date().toLocaleTimeString(),
    };
    this._entries.push(entry);
    this._appendRenderedEntry(entry);
    while (this._entries.length > this.maxEntries) {
      const removed = this._entries.shift();
      this._removeRenderedEntry(removed);
    }
  }

  _connectWS() {
    this._closeWS();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = new URL(proto + "//" + location.host + this.wsPath);
    if (this._lastEventId > 0) {
      url.searchParams.set("since_id", String(this._lastEventId));
    }
    this._ws = new WebSocket(url.toString());

    this._ws.onopen = () => {
      this._reconnectAttempts = 0;
      this._notifyConnectionStatus("connected", { attempt: 0, delayMs: 0 });
      this.append("Connected", "info");
    };

    this._ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "heartbeat") return;
        if (msg.type === "stream.config") {
          this.dispatchEvent(new CustomEvent("stream-config", {
            bubbles: true,
            detail: msg,
          }));
          return;
        }
        if (msg.type === "stream.history") {
          const historyEvents = Array.isArray(msg.events) ? msg.events : [];
          const truncated = !!msg.truncated;
          if (truncated) {
            this.append("Reconnect gap exceeded retention; replaying the latest available events", "warn", "system", {
              type: "stream.history.truncated",
            });
          }
          historyEvents.forEach((event) => {
            if (!event || this._hasSeenServerEvent(event)) {
              return;
            }
            if (this._matchesThreadOwner(event)) {
              const channel = event.channel || "system";
              this.append(
                this._formatMessage(event),
                this._eventLevel(event),
                channel,
                event,
                event.timestamp || event.ts || event.created_at || event.time || null,
              );
            } else {
              this._rememberServerEvent(event);
            }
            this.dispatchEvent(new CustomEvent("stream-event", {
              bubbles: true,
              detail: Object.assign({ replayed: true }, event),
            }));
          });
          return;
        }
        if (msg.type === "ping") {
          this._ws.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (msg.type === "pong") return;
        if (this._hasSeenServerEvent(msg)) return;
        const channel = msg.channel || "system";
        if (this._matchesThreadOwner(msg)) {
          this.append(this._formatMessage(msg), this._eventLevel(msg), channel, msg);
        }

        // Dispatch custom event so parent JS can react
        this.dispatchEvent(new CustomEvent("stream-event", {
          bubbles: true,
          detail: msg,
        }));
      } catch { /* non-JSON */ }
    };

    this._ws.onclose = () => {
      const attempt = this._reconnectAttempts + 1;
      const delayMs = Math.min(1000 * Math.pow(2, this._reconnectAttempts), this._maxReconnectDelay);
      this._reconnectAttempts = attempt;
      this.append("Disconnected — retrying", "warn");
      this._notifyConnectionStatus("reconnecting", { attempt: attempt, delayMs: delayMs });
      this._reconnectTimer = setTimeout(() => this._connectWS(), delayMs);
    };
  }

  _notifyConnectionStatus(status, extra) {
    this.dispatchEvent(new CustomEvent("stream-connection", {
      bubbles: true,
      detail: Object.assign({ status: status }, extra || {}),
    }));
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
