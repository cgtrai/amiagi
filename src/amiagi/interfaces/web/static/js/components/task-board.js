/**
 * <task-board> Web Component
 *
 * Kanban-style task board with columns for each status.
 * Accepts tasks via .setTasks(array) method.
 */
class TaskBoard extends HTMLElement {
  static COLUMNS = [
    { key: "pending", label: "Pending", color: "#94a3b8" },
    { key: "assigned", label: "Assigned", color: "#60a5fa" },
    { key: "in_progress", label: "In Progress", color: "#facc15" },
    { key: "review", label: "Review", color: "#a78bfa" },
    { key: "done", label: "Done", color: "#4ade80" },
    { key: "failed", label: "Failed", color: "#ef4444" },
  ];

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tasks = [];
  }

  connectedCallback() {
    this.render();
    // Listen for prompt.submit events (e.g. from task_wizard.html)
    this._onPromptSubmit = (e) => this._handlePromptSubmit(e);
    document.addEventListener("prompt.submit", this._onPromptSubmit);
  }

  disconnectedCallback() {
    if (this._onPromptSubmit) {
      document.removeEventListener("prompt.submit", this._onPromptSubmit);
    }
  }

  /**
   * Handle a prompt.submit CustomEvent from the task wizard.
   * Adds the task optimistically as "pending" and refreshes.
   * @param {CustomEvent} e
   */
  _handlePromptSubmit(e) {
    const detail = e.detail || {};
    if (!detail.title) return;
    const task = {
      task_id: detail.task_id || "pending-" + Date.now(),
      title: detail.title,
      description: detail.description || "",
      status: "pending",
      priority: detail.priority || "medium",
      origin: detail.origin || "operator",
      assigned_agent_id: detail.assigned_agent_id || null,
    };
    this._tasks.push(task);
    this.render();
  }

  /**
   * Update the displayed tasks.
   * @param {Array<{task_id, title, status, priority, assigned_agent_id}>} tasks
   */
  setTasks(tasks) {
    this._tasks = tasks || [];
    this.render();
  }

  _groupByStatus() {
    const groups = {};
    for (const col of TaskBoard.COLUMNS) {
      groups[col.key] = [];
    }
    for (const t of this._tasks) {
      const key = (t.status || "pending").toLowerCase();
      if (groups[key]) groups[key].push(t);
      else if (groups.pending) groups.pending.push(t);
    }
    return groups;
  }

  _priorityClass(p) {
    const map = { critical: "pri-crit", high: "pri-high", normal: "pri-norm", low: "pri-low" };
    return map[(p || "normal").toLowerCase()] || "pri-norm";
  }

  render() {
    const groups = this._groupByStatus();
    const columnsHtml = TaskBoard.COLUMNS.map(col => {
      const items = groups[col.key] || [];
      const cardsHtml = items.map(t => `
        <div class="task-card ${this._priorityClass(t.priority)}">
          <div class="task-title">${this._esc(t.title || t.task_id)}</div>
          ${t.assigned_agent_id ? `<div class="task-agent">🤖 ${this._esc(t.assigned_agent_id)}</div>` : ""}
        </div>
      `).join("");
      return `
        <div class="column">
          <div class="column-header">
            <span class="dot" style="background:${col.color}"></span>
            <span class="col-label">${col.label}</span>
            <span class="col-count">${items.length}</span>
          </div>
          <div class="column-body">${cardsHtml || '<div class="empty-col">—</div>'}</div>
        </div>
      `;
    }).join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; overflow-x: auto; }
        .board {
          display: flex;
          gap: var(--space-3, 0.75rem);
          min-width: max-content;
          padding: var(--space-2, 0.5rem) 0;
        }
        .column {
          min-width: 180px;
          flex: 1;
          max-width: 260px;
        }
        .column-header {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: var(--space-2, 0.5rem);
          font-size: 0.8rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: var(--text-secondary, #cbd5e1);
        }
        .dot {
          width: 8px; height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .col-count {
          margin-left: auto;
          background: var(--glass-bg, rgba(255,255,255,0.06));
          padding: 1px 6px;
          border-radius: 9999px;
          font-size: 0.7rem;
        }
        .column-body {
          display: flex;
          flex-direction: column;
          gap: var(--space-2, 0.5rem);
        }
        .task-card {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-radius: var(--radius-md, 12px);
          padding: var(--space-2, 0.5rem) var(--space-3, 0.75rem);
          font-size: 0.85rem;
          color: var(--text-primary, #f1f5f9);
          border-left: 3px solid transparent;
        }
        .pri-crit { border-left-color: #ef4444; }
        .pri-high { border-left-color: #f97316; }
        .pri-norm { border-left-color: #60a5fa; }
        .pri-low  { border-left-color: #94a3b8; }
        .task-title {
          font-weight: 500;
          margin-bottom: 2px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .task-agent {
          font-size: 0.75rem;
          color: var(--text-muted, #94a3b8);
        }
        .empty-col {
          text-align: center;
          color: var(--text-muted, #94a3b8);
          font-size: 0.8rem;
          padding: var(--space-4, 1rem) 0;
        }
      </style>
      <div class="board">${columnsHtml}</div>
    `;
  }

  _esc(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }
}

customElements.define("task-board", TaskBoard);
