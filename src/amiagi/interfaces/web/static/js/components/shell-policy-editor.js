/**
 * <shell-policy-editor> Web Component
 *
 * Visual editor for the shell allowlist policy with JSON fallback.
 *
 * Attributes:
 *   mode – "editor" (default) or "json"
 *
 * Loads from GET /api/shell-policy, saves via PUT /api/shell-policy.
 */
class ShellPolicyEditor extends HTMLElement {
  static get observedAttributes() {
    return ["mode"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._policy = null;
    this._mode = "editor";
    this._dirty = false;
  }

  /* ── Lifecycle ─────────────────────────────────────────────── */

  connectedCallback() {
    this._mode = this.getAttribute("mode") || "editor";
    this._renderShell();
    this._load();
  }

  attributeChangedCallback(name, _old, val) {
    if (name === "mode" && val) {
      this._mode = val;
      this._render();
    }
  }

  /* ── Data ──────────────────────────────────────────────────── */

  async _load() {
    try {
      const r = await fetch("/api/shell-policy");
      if (r.ok) this._policy = await r.json();
    } catch { /* ignore */ }
    this._render();
  }

  async _save() {
    if (!this._policy) return;
    try {
      const r = await fetch("/api/shell-policy", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this._policy),
      });
      if (r.ok) {
        this._dirty = false;
        this._render();
        if (typeof showToast === "function") showToast("Shell policy saved.", "success");
      } else {
        if (typeof showToast === "function") showToast("Save failed", "error");
      }
    } catch {
      if (typeof showToast === "function") showToast("Save failed", "error");
    }
  }

  /* ── Render ────────────────────────────────────────────────── */

  _esc(s) {
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `<style>${this._css()}</style><div class="root"><p class="muted">Loading…</p></div>`;
  }

  _render() {
    const root = this.shadowRoot.querySelector(".root");
    if (!root) return;
    if (!this._policy) {
      root.innerHTML = '<p class="muted">No policy data.</p>';
      return;
    }
    if (this._mode === "json") {
      this._renderJSON(root);
    } else {
      this._renderEditor(root);
    }
  }

  /* ── Editor mode ───────────────────────────────────────────── */

  _renderEditor(root) {
    const p = this._policy;
    let html = "";

    /* Allowed bare commands */
    const bare = p.allowed_commands || [];
    html += '<section class="section">';
    html += '<h4 class="section-title">✅ Allowed commands (bare)</h4>';
    html += '<div class="tag-list" data-key="allowed_commands">';
    html += bare.map(c => '<span class="tag">' + this._esc(c) + '<button class="tag-remove" data-key="allowed_commands" data-val="' + this._esc(c) + '">×</button></span>').join("");
    html += '</div>';
    html += '<div class="add-row"><input class="add-input" data-key="allowed_commands" placeholder="Add command…"/><button class="add-btn" data-key="allowed_commands">+</button></div>';
    html += '</section>';

    /* Allowed with sub-arguments */
    const sub = p.allowed_with_args || {};
    html += '<section class="section">';
    html += '<h4 class="section-title">✅ Allowed with sub-arguments</h4>';
    for (const [cmd, args] of Object.entries(sub)) {
      html += '<div class="sub-group">';
      html += '<span class="sub-cmd">' + this._esc(cmd) + '</span> → ';
      html += '<span class="tag-list-inline">';
      html += (args || []).map(a => '<span class="tag tag--small">' + this._esc(a) + '<button class="tag-remove" data-key="allowed_with_args" data-cmd="' + this._esc(cmd) + '" data-val="' + this._esc(a) + '">×</button></span>').join("");
      html += '</span>';
      html += '<input class="add-input add-input--inline" data-key="allowed_with_args" data-cmd="' + this._esc(cmd) + '" placeholder="arg…"/>';
      html += '<button class="add-btn add-btn--inline" data-key="allowed_with_args" data-cmd="' + this._esc(cmd) + '">+</button>';
      html += '</div>';
    }
    html += '<div class="add-row"><input class="add-input" data-key="new_allowed_with_args" placeholder="New command…"/><button class="add-btn" data-key="new_allowed_with_args">+</button></div>';
    html += '</section>';

    /* Exact commands */
    const exact = p.exact_commands || [];
    html += '<section class="section">';
    html += '<h4 class="section-title">✅ Exact commands</h4>';
    html += '<div class="tag-list" data-key="exact_commands">';
    html += exact.map(c => '<span class="tag tag--mono">' + this._esc(c) + '<button class="tag-remove" data-key="exact_commands" data-val="' + this._esc(c) + '">×</button></span>').join("");
    html += '</div>';
    html += '<div class="add-row"><input class="add-input" data-key="exact_commands" placeholder="Exact command…"/><button class="add-btn" data-key="exact_commands">+</button></div>';
    html += '</section>';

    /* Blocked patterns */
    const blocked = p.blocked_patterns || [];
    html += '<section class="section section--danger">';
    html += '<h4 class="section-title">🚫 Blocked patterns</h4>';
    html += '<div class="tag-list" data-key="blocked_patterns">';
    html += blocked.map(c => '<span class="tag tag--danger">' + this._esc(c) + '<button class="tag-remove" data-key="blocked_patterns" data-val="' + this._esc(c) + '">×</button></span>').join("");
    html += '</div>';
    html += '<div class="add-row"><input class="add-input" data-key="blocked_patterns" placeholder="Block pattern…"/><button class="add-btn" data-key="blocked_patterns">+</button></div>';
    html += '</section>';

    /* Save button */
    html += '<div class="save-row"><button class="save-btn"' + (this._dirty ? '' : ' disabled') + '>Save Policy</button></div>';

    root.innerHTML = html;
    this._bindEditorEvents(root);
  }

  _bindEditorEvents(root) {
    /* Remove tags */
    root.querySelectorAll(".tag-remove").forEach(btn => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.key;
        const val = btn.dataset.val;
        const cmd = btn.dataset.cmd;
        if (key === "allowed_with_args" && cmd) {
          const arr = this._policy.allowed_with_args[cmd];
          if (arr) {
            const idx = arr.indexOf(val);
            if (idx >= 0) arr.splice(idx, 1);
          }
        } else if (this._policy[key]) {
          const idx = this._policy[key].indexOf(val);
          if (idx >= 0) this._policy[key].splice(idx, 1);
        }
        this._dirty = true;
        this._render();
      });
    });

    /* Add buttons */
    root.querySelectorAll(".add-btn").forEach(btn => {
      btn.addEventListener("click", () => this._handleAdd(root, btn));
    });

    /* Enter key in inputs */
    root.querySelectorAll(".add-input").forEach(input => {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const btn = input.parentElement.querySelector(".add-btn");
          if (btn) this._handleAdd(root, btn);
        }
      });
    });

    /* Save */
    const saveBtn = root.querySelector(".save-btn");
    if (saveBtn) saveBtn.addEventListener("click", () => this._save());
  }

  _handleAdd(root, btn) {
    const key = btn.dataset.key;
    const cmd = btn.dataset.cmd;
    let input;

    if (cmd) {
      input = root.querySelector('.add-input[data-key="' + key + '"][data-cmd="' + cmd + '"]');
    } else {
      input = root.querySelector('.add-input[data-key="' + key + '"]');
    }
    if (!input) return;
    const val = input.value.trim();
    if (!val) return;

    if (key === "new_allowed_with_args") {
      if (!this._policy.allowed_with_args) this._policy.allowed_with_args = {};
      if (!this._policy.allowed_with_args[val]) this._policy.allowed_with_args[val] = [];
    } else if (key === "allowed_with_args" && cmd) {
      if (!this._policy.allowed_with_args[cmd]) this._policy.allowed_with_args[cmd] = [];
      if (!this._policy.allowed_with_args[cmd].includes(val)) {
        this._policy.allowed_with_args[cmd].push(val);
      }
    } else {
      if (!this._policy[key]) this._policy[key] = [];
      if (!this._policy[key].includes(val)) {
        this._policy[key].push(val);
      }
    }
    this._dirty = true;
    this._render();
  }

  /* ── JSON mode ─────────────────────────────────────────────── */

  _renderJSON(root) {
    const json = JSON.stringify(this._policy, null, 2);
    root.innerHTML =
      '<textarea class="json-editor" spellcheck="false">' + this._esc(json) + '</textarea>' +
      '<div class="save-row"><button class="save-btn">Save Policy</button></div>';

    const ta = root.querySelector(".json-editor");
    const saveBtn = root.querySelector(".save-btn");

    ta.addEventListener("input", () => {
      this._dirty = true;
      saveBtn.removeAttribute("disabled");
    });

    saveBtn.addEventListener("click", () => {
      try {
        this._policy = JSON.parse(ta.value);
        this._save();
      } catch (e) {
        if (typeof showToast === "function") showToast("Invalid JSON: " + e.message, "error");
      }
    });
  }

  /* ── Styles ────────────────────────────────────────────────── */

  _css() {
    return `
      :host { display: block; }
      .root { font-family: var(--font-primary, system-ui); font-size: 0.875rem; color: var(--text-primary, #e6edf3); }
      .muted { color: var(--text-muted, #6b7280); }
      .section { margin-bottom: 1rem; padding: 0.75rem; background: rgba(255,255,255,0.03); border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }
      .section--danger { border-color: rgba(244,63,94,0.25); }
      .section-title { margin: 0 0 0.5rem; font-size: 0.8rem; font-weight: 600; }
      .tag-list, .tag-list-inline { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-bottom: 0.5rem; }
      .tag-list-inline { display: inline-flex; }
      .tag { display: inline-flex; align-items: center; gap: 0.2rem; padding: 0.15rem 0.5rem; background: rgba(78,161,255,0.12); border: 1px solid rgba(78,161,255,0.25); border-radius: 9999px; font-size: 0.75rem; color: var(--accent-blue, #4ea1ff); }
      .tag--small { font-size: 0.7rem; padding: 0.1rem 0.4rem; }
      .tag--mono { font-family: var(--font-mono, monospace); }
      .tag--danger { background: rgba(244,63,94,0.12); border-color: rgba(244,63,94,0.25); color: var(--accent-rose, #f43f5e); }
      .tag-remove { background: none; border: none; color: inherit; cursor: pointer; font-size: 0.85rem; padding: 0 0.15rem; opacity: 0.6; transition: opacity 150ms; }
      .tag-remove:hover { opacity: 1; }
      .sub-group { margin-bottom: 0.4rem; display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
      .sub-cmd { font-weight: 600; font-family: var(--font-mono, monospace); font-size: 0.8rem; color: var(--text-primary, #e6edf3); }
      .add-row { display: flex; gap: 0.3rem; align-items: center; }
      .add-input { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; padding: 0.3rem 0.5rem; color: var(--text-primary, #e6edf3); font-size: 0.8rem; outline: none; transition: border-color 150ms; flex: 1; max-width: 250px; }
      .add-input:focus { border-color: var(--accent-blue, #4ea1ff); }
      .add-input--inline { max-width: 100px; font-size: 0.75rem; }
      .add-btn { background: rgba(78,161,255,0.15); border: 1px solid rgba(78,161,255,0.3); border-radius: 6px; padding: 0.25rem 0.5rem; color: var(--accent-blue, #4ea1ff); cursor: pointer; font-size: 0.8rem; transition: background 150ms; }
      .add-btn:hover { background: rgba(78,161,255,0.25); }
      .add-btn--inline { font-size: 0.7rem; padding: 0.2rem 0.4rem; }
      .save-row { margin-top: 1rem; display: flex; justify-content: flex-end; }
      .save-btn { background: rgba(78,161,255,0.2); border: 1px solid rgba(78,161,255,0.4); border-radius: 8px; padding: 0.4rem 1.2rem; color: var(--accent-blue, #4ea1ff); font-weight: 600; cursor: pointer; font-size: 0.85rem; transition: background 150ms, opacity 150ms; }
      .save-btn:hover { background: rgba(78,161,255,0.35); }
      .save-btn[disabled] { opacity: 0.4; cursor: default; }
      .json-editor { width: 100%; min-height: 300px; background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 0.75rem; color: var(--text-primary, #e6edf3); font-family: var(--font-mono, monospace); font-size: 0.8rem; line-height: 1.5; resize: vertical; outline: none; tab-size: 2; }
      .json-editor:focus { border-color: var(--accent-blue, #4ea1ff); }
    `;
  }
}

customElements.define("shell-policy-editor", ShellPolicyEditor);
