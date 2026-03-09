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
    this._draggedTaskId = null;
    this._draggedFromStatus = null;
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

  _findTask(taskId) {
    return this._tasks.find((item) => String(item.task_id) === String(taskId)) || null;
  }

  _applyTaskMove(taskId, toStatus) {
    const task = this._findTask(taskId);
    if (!task) return null;
    const fromStatus = String(task.status || "pending").toLowerCase();
    if (fromStatus === toStatus) {
      return { task, fromStatus, toStatus, changed: false };
    }
    task.status = toStatus;
    if (toStatus === "assigned" && !task.assigned_agent_id) {
      task.assigned_agent_id = task.assigned_agent_id || null;
    }
    return { task, fromStatus, toStatus, changed: true };
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
        <button class="task-card ${this._priorityClass(t.priority)}" data-task-id="${this._esc(t.task_id || "")}" data-task-status="${this._esc(col.key)}" type="button" draggable="true">
          <div class="task-title">${this._esc(t.title || t.task_id)}</div>
          ${t.assigned_agent_id ? `<div class="task-agent">🤖 ${this._esc(t.assigned_agent_id)}</div>` : ""}
        </button>
      `).join("");
      return `
        <div class="column" data-column-status="${this._esc(col.key)}">
          <div class="column-header">
            <span class="dot" style="background:${col.color}"></span>
            <span class="col-label">${col.label}</span>
            <span class="col-count">${items.length}</span>
          </div>
          <div class="column-body" data-drop-status="${this._esc(col.key)}">${cardsHtml || '<div class="empty-col">—</div>'}</div>
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
          min-height: 120px;
          padding: 2px;
          border-radius: var(--radius-md, 12px);
          transition: background 0.15s ease, box-shadow 0.15s ease;
        }
        .column-body.drop-target {
          background: rgba(96, 165, 250, 0.08);
          box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.22);
        }
        .task-card {
          background: var(--glass-bg, rgba(255,255,255,0.06));
          border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
          border-radius: var(--radius-md, 12px);
          padding: var(--space-2, 0.5rem) var(--space-3, 0.75rem);
          font-size: 0.85rem;
          color: var(--text-primary, #f1f5f9);
          border-left: 3px solid transparent;
          width: 100%;
          text-align: left;
          cursor: pointer;
          transition: border-color 0.15s ease, transform 0.15s ease, opacity 0.15s ease;
        }
        .task-card:hover { border-color: rgba(255,255,255,0.18); transform: translateY(-1px); }
        .task-card.dragging { opacity: 0.45; transform: scale(0.985); }
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

    this.shadowRoot.querySelectorAll(".task-card[data-task-id]").forEach((button) => {
      button.addEventListener("dragstart", (event) => {
        const taskId = button.getAttribute("data-task-id") || "";
        const status = button.getAttribute("data-task-status") || "pending";
        this._draggedTaskId = taskId;
        this._draggedFromStatus = status;
        button.classList.add("dragging");
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", taskId);
          event.dataTransfer.setData("application/x-amiagi-task-status", status);
        }
      });
      button.addEventListener("dragend", () => {
        this._draggedTaskId = null;
        this._draggedFromStatus = null;
        button.classList.remove("dragging");
        this.shadowRoot.querySelectorAll(".column-body.drop-target").forEach((col) => col.classList.remove("drop-target"));
      });
      button.addEventListener("click", () => {
        const taskId = button.getAttribute("data-task-id") || "";
        const task = this._tasks.find((item) => String(item.task_id) === taskId) || null;
        const event = new CustomEvent("task:selected", {
          detail: { taskId, task },
          bubbles: true,
          composed: true,
        });
        this.dispatchEvent(event);
        if (typeof window.openTaskDetailDrawer === "function") {
          window.openTaskDetailDrawer(taskId, task);
        }
      });
    });

    this.shadowRoot.querySelectorAll(".column-body[data-drop-status]").forEach((columnBody) => {
      columnBody.addEventListener("dragover", (event) => {
        if (!this._draggedTaskId) return;
        event.preventDefault();
        columnBody.classList.add("drop-target");
      });
      columnBody.addEventListener("dragleave", (event) => {
        if (event.relatedTarget && columnBody.contains(event.relatedTarget)) return;
        columnBody.classList.remove("drop-target");
      });
      columnBody.addEventListener("drop", (event) => {
        if (!this._draggedTaskId) return;
        event.preventDefault();
        const toStatus = columnBody.getAttribute("data-drop-status") || "pending";
        const taskId = this._draggedTaskId || event.dataTransfer?.getData("text/plain") || "";
        const move = this._applyTaskMove(taskId, toStatus);
        this._draggedTaskId = null;
        this._draggedFromStatus = null;
        this.shadowRoot.querySelectorAll(".column-body.drop-target").forEach((col) => col.classList.remove("drop-target"));
        if (!move || !move.changed) {
          this.render();
          return;
        }
        this.render();
        const moveEvent = new CustomEvent("task:moved", {
          detail: {
            taskId,
            task: move.task,
            fromStatus: move.fromStatus,
            toStatus,
          },
          bubbles: true,
          composed: true,
        });
        this.dispatchEvent(moveEvent);
      });
    });
  }

  _esc(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }
}

customElements.define("task-board", TaskBoard);
