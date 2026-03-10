/**
 * Status Bar — live updater.
 *
 * Polls GET /api/status-bar every 10 s and patches the DOM elements
 * rendered by partials/status_bar.html.  Also listens on the global
 * WebSocket (/ws/events) for push updates so changes appear instantly.
 */
(function () {
  'use strict';

  var POLL_INTERVAL = 10000; // 10 s
  var _timer = null;

  /* ── DOM refs (cached once) ─────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function t(key, fallback) {
    return typeof window.t === 'function' ? window.t(key, fallback) : (fallback || key);
  }
  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function normalizeTaskList(data) {
    return (data && (data.items || data.tasks)) || [];
  }
  function bindStatusAction(id, handler) {
    var el = $(id);
    var section = el ? el.closest('.status-bar-section') : null;
    if (!section) return;
    section.classList.add('status-clickable');
    section.addEventListener('click', handler);
  }
  function renderTaskRows(tasks, emptyKey, emptyFallback) {
    if (!tasks.length) {
      return '<div class="drawer-item-empty">' + escapeHtml(t(emptyKey, emptyFallback)) + '</div>';
    }
    return tasks.map(function (task) {
      return '<div class="drawer-item"><strong>' + escapeHtml(task.title || task.task_id || '—') + '</strong> — ' + escapeHtml(task.status || '') + '</div>';
    }).join('');
  }

  /* ── Apply a status-bar payload to the DOM ─────────────────── */
  function applyStatus(d) {
    // Model status dot
    var dot = $('model-status-dot');
    if (dot) {
      dot.className = 'status-dot ' + (d.model_alive ? 'status-dot--green' : 'status-dot--red');
    }
    // Model name
    var mn = $('model-status-name');
    if (mn) mn.textContent = d.model_name || '—';

    // Budget bar fill
    var bf = $('budget-bar-fill');
    if (bf) {
      var pct = d.budget_pct || 0;
      bf.style.width = pct + '%';
      bf.className = 'progress-micro-fill '
        + (pct >= 90 ? 'progress-micro-fill--red'
           : pct >= 70 ? 'progress-micro-fill--yellow'
           : 'progress-micro-fill--green');
    }
    var bl = $('budget-label');
    if (bl) bl.textContent = (d.budget_used || '0.00') + '/' + (d.budget_limit || '∞');

    // Active tasks
    var at = $('active-tasks-count');
    if (at) at.dataset.total = d.active_tasks != null ? d.active_tasks : 0;
    var atr = $('active-tasks-running');
    if (atr) atr.textContent = d.running_tasks != null ? d.running_tasks : 0;
    var atp = $('active-tasks-pending');
    if (atp) atp.textContent = d.pending_tasks != null ? d.pending_tasks : (d.active_tasks != null ? d.active_tasks : 0);

    // Inbox pending
    var ip = $('inbox-pending-count');
    if (ip) ip.textContent = d.inbox_pending != null ? d.inbox_pending : 0;

    // Uptime
    var up = $('uptime-label');
    if (up) up.textContent = d.uptime || '0m';
  }

  /* ── REST polling ──────────────────────────────────────────── */
  function poll() {
    fetch('/api/status-bar')
      .then(function (r) { return r.json(); })
      .then(applyStatus)
      .catch(function () { /* silent — status bar stays stale */ });
  }

  /* ── WebSocket push (piggy-back on existing /ws/events) ────── */
  var _wsReconnectAttempts = 0;
  var _WS_MAX_RECONNECT = 10;
  var _WS_BASE_DELAY = 2000;

  function hookWebSocket() {
    // The global EventHub WS is created by event-ticker / dashboard.js.
    // We listen for a custom 'status-bar' event type.
    var origWs = window._amiagiEventWs;
    if (!origWs) {
      // WS not ready yet — create our own connection with reconnect
      _createStatusWs();
      return;
    }
    _wsReconnectAttempts = 0;
    var origOnMessage = origWs.onmessage;
    origWs.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'status-bar') {
          applyStatus(msg.data || msg);
        }
      } catch (e) { /* ignore parse errors */ }
      // Forward to original handler
      if (typeof origOnMessage === 'function') {
        origOnMessage.call(origWs, evt);
      }
    };
    // Watch for close → reconnect
    var origOnClose = origWs.onclose;
    origWs.onclose = function (evt) {
      if (typeof origOnClose === 'function') origOnClose.call(origWs, evt);
      _scheduleWsReconnect();
    };
  }

  function _createStatusWs() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var ws = new WebSocket(proto + '//' + location.host + '/ws/events');
    ws.onopen = function () { _wsReconnectAttempts = 0; };
    ws.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'status-bar') {
          applyStatus(msg.data || msg);
        }
      } catch (e) { /* ignore */ }
    };
    ws.onclose = function () { _scheduleWsReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (_) {} };
    window._amiagiEventWs = ws;
  }

  function _scheduleWsReconnect() {
    if (_wsReconnectAttempts >= _WS_MAX_RECONNECT) return; // give up
    var delay = _WS_BASE_DELAY * Math.pow(2, _wsReconnectAttempts) + Math.random() * 500;
    _wsReconnectAttempts++;
    setTimeout(function () {
      // Check if someone else already reconnected
      var existing = window._amiagiEventWs;
      if (existing && existing.readyState === WebSocket.OPEN) {
        hookWebSocket();
        return;
      }
      _createStatusWs();
    }, Math.min(delay, 30000));
  }

  /* ── Bootstrap ─────────────────────────────────────────────── */
  function init() {    // Set initial budget bar width from server-rendered data-pct
    var bf = $('budget-bar-fill');
    if (bf && bf.dataset.pct) {
      bf.style.width = bf.dataset.pct + '%';
    }    poll(); // first fetch immediately
    _timer = setInterval(poll, POLL_INTERVAL);
    // Try to hook WS after a short delay (WS may not be connected yet)
    setTimeout(hookWebSocket, 2000);

    // L4 — Status bar click handlers
    bindStatusAction('active-tasks-count', function () {
      fetch('/api/tasks').then(function (r) { return r.json(); }).then(function (data) {
        var tasks = normalizeTaskList(data);
        var running = tasks.filter(function (task) {
          var status = String(task.status || '').toLowerCase();
          return status === 'in_progress' || status === 'running';
        });
        var pending = tasks.filter(function (task) {
          var status = String(task.status || '').toLowerCase();
          return status === 'pending' || status === 'assigned';
        });
        var html = '<div style="display:grid;gap:var(--space-3)">'
          + '<div class="metric-grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">'
          + '<div class="glass-card" style="padding:var(--space-3)"><div class="text-secondary">' + escapeHtml(t('tasks.in_progress', 'In Progress')) + '</div><div style="font-size:1.25rem;font-weight:700">' + running.length + '</div></div>'
          + '<div class="glass-card" style="padding:var(--space-3)"><div class="text-secondary">' + escapeHtml(t('tasks.pending', 'Pending')) + '</div><div style="font-size:1.25rem;font-weight:700">' + pending.length + '</div></div>'
          + '</div>'
          + '<div><h4 style="margin:0 0 .5rem 0">' + escapeHtml(t('tasks.in_progress', 'In Progress')) + '</h4>' + renderTaskRows(running, 'dashboard.no_tasks', 'No tasks in queue') + '</div>'
          + '<div><h4 style="margin:0 0 .5rem 0">' + escapeHtml(t('tasks.pending', 'Pending')) + '</h4>' + renderTaskRows(pending, 'dashboard.no_tasks', 'No tasks in queue') + '</div>'
          + '</div>';
        if (typeof openDetailDrawer === 'function') openDetailDrawer(t('status.active_tasks', 'Active tasks'), html);
      });
    });

    bindStatusAction('inbox-pending-count', function () { window.location = '/inbox'; });

    bindStatusAction('model-status-dot', function () { window.location = '/model-hub'; });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
