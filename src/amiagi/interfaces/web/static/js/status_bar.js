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
    if (at) at.textContent = d.active_tasks != null ? d.active_tasks : 0;

    // Inbox pending
    var ip = $('inbox-pending-count');
    if (ip) ip.textContent = d.inbox_pending != null ? d.inbox_pending : 0;

    // Token counter
    var tc = $('token-counter');
    if (tc) tc.textContent = d.token_count != null ? d.token_count : 0;

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
  function hookWebSocket() {
    // The global EventHub WS is created by event-ticker / dashboard.js.
    // We listen for a custom 'status-bar' event type.
    var origWs = window._amiagiEventWs;
    if (!origWs) return;
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
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
