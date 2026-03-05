/**
 * <chat-stream> Web Component
 *
 * Scrollable chat log with auto-scroll. Accepts messages via
 * .addMessage({role, text, timestamp}) and renders them as bubbles.
 *
 * Attributes: agent-id
 */
class ChatStream extends HTMLElement {
  static get observedAttributes() {
    return ["agent-id"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._messages = [];
    this._autoScroll = true;
  }

  connectedCallback() {
    this._render();
    this._container = this.shadowRoot.querySelector(".messages");
    this._container.addEventListener("scroll", () => this._onScroll());
  }

  get agentId() { return this.getAttribute("agent-id") || ""; }

  /**
   * Append a message to the stream.
   * @param {{role: string, text: string, timestamp?: string}} msg
   */
  addMessage(msg) {
    this._messages.push(msg);
    this._appendBubble(msg);
    if (this._autoScroll) {
      this._scrollToBottom();
    }
  }

  /** Clear all messages. */
  clear() {
    this._messages = [];
    if (this._container) {
      this._container.innerHTML = "";
    }
  }

  _scrollToBottom() {
    requestAnimationFrame(() => {
      if (this._container) {
        this._container.scrollTop = this._container.scrollHeight;
      }
    });
  }

  _onScroll() {
    if (!this._container) return;
    const { scrollTop, scrollHeight, clientHeight } = this._container;
    // Re-enable auto-scroll when user scrolls near bottom (within 60px)
    this._autoScroll = scrollHeight - scrollTop - clientHeight < 60;
  }

  _appendBubble(msg) {
    if (!this._container) return;
    const el = document.createElement("div");
    const isUser = msg.role === "user";
    el.className = `bubble ${isUser ? "bubble--user" : "bubble--agent"}`;

    const actionsHtml = isUser ? "" : `
      <div class="bubble-actions">
        <button class="btn-copy" title="Copy to clipboard">📋 Copy</button>
        <button class="btn-snippet" title="Save to snippets">💾 Snippet</button>
      </div>`;

    el.innerHTML = `
      <div class="bubble-text">${this._escapeHtml(msg.text)}</div>
      <div class="bubble-meta">${msg.timestamp || new Date().toLocaleTimeString()}</div>
      ${actionsHtml}
    `;

    if (!isUser) {
      const rawText = msg.text;
      el.querySelector(".btn-copy").addEventListener("click", () => this._copyText(rawText));
      el.querySelector(".btn-snippet").addEventListener("click", () => this._saveSnippet(rawText, msg));
    }

    this._container.appendChild(el);
  }

  _copyText(text) {
    navigator.clipboard.writeText(text).then(() => {
      this._toast("Copied to clipboard");
    }).catch(() => {
      // Fallback for non-HTTPS contexts
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      this._toast("Copied to clipboard");
    });
  }

  _saveSnippet(text, msg) {
    fetch("/snippets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text,
        tags: ["chat"],
        source_agent: this.agentId || null,
      }),
    }).then(r => {
      if (r.ok) {
        this._toast("Saved to snippets ✓");
      } else {
        this._toast("Failed to save snippet");
      }
    }).catch(() => {
      this._toast("Failed to save snippet");
    });
  }

  _toast(message) {
    // Simple shadow-DOM toast
    let toast = this.shadowRoot.querySelector(".mini-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "mini-toast";
      this.shadowRoot.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("mini-toast--show");
    setTimeout(() => toast.classList.remove("mini-toast--show"), 2000);
  }

  _escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: flex;
          flex-direction: column;
          height: 100%;
          min-height: 200px;
        }
        .messages {
          flex: 1;
          overflow-y: auto;
          padding: var(--space-3, 0.75rem);
          display: flex;
          flex-direction: column;
          gap: var(--space-2, 0.5rem);
          scroll-behavior: smooth;
        }
        .messages::-webkit-scrollbar {
          width: 6px;
        }
        .messages::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.15);
          border-radius: 3px;
        }
        .bubble {
          max-width: 80%;
          padding: var(--space-2, 0.5rem) var(--space-3, 0.75rem);
          border-radius: var(--radius-md, 12px);
          font-size: 0.875rem;
          line-height: 1.5;
          word-wrap: break-word;
        }
        .bubble--user {
          align-self: flex-end;
          background: var(--accent-primary, #6366f1);
          color: #fff;
          border-bottom-right-radius: 4px;
        }
        .bubble--agent {
          align-self: flex-start;
          background: var(--glass-bg, rgba(255,255,255,0.06));
          color: var(--text-primary, #f1f5f9);
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-bottom-left-radius: 4px;
        }
        .bubble-meta {
          font-size: 0.7rem;
          color: var(--text-muted, #94a3b8);
          margin-top: 2px;
          opacity: 0.7;
        }
        .bubble--user .bubble-meta {
          color: rgba(255,255,255,0.6);
        }
        .empty {
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100%;
          color: var(--text-muted, #94a3b8);
          font-size: 0.85rem;
        }
        .bubble-actions {
          display: flex;
          gap: 4px;
          margin-top: 4px;
          opacity: 0;
          transition: opacity 0.15s;
        }
        .bubble:hover .bubble-actions {
          opacity: 1;
        }
        .bubble-actions button {
          background: transparent;
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-radius: 4px;
          color: var(--text-muted, #94a3b8);
          font-size: 0.7rem;
          padding: 2px 6px;
          cursor: pointer;
          transition: background 0.15s, color 0.15s;
        }
        .bubble-actions button:hover {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          color: var(--text-primary, #f1f5f9);
        }
        .mini-toast {
          position: fixed;
          bottom: var(--space-3, 12px);
          left: 50%;
          transform: translateX(-50%) translateY(60px);
          background: var(--glass-bg, rgba(30,41,59,0.9));
          color: var(--text-primary, #f1f5f9);
          padding: 6px 16px;
          border-radius: 6px;
          font-size: 0.8rem;
          pointer-events: none;
          opacity: 0;
          transition: opacity 0.2s, transform 0.2s;
          z-index: 9999;
        }
        .mini-toast--show {
          opacity: 1;
          transform: translateX(-50%) translateY(0);
        }
      </style>
      <div class="messages"></div>
    `;
  }
}

customElements.define("chat-stream", ChatStream);
