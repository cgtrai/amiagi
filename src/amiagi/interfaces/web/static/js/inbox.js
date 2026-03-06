/**
 * inbox.js — Human-in-the-Loop inbox client logic.
 *
 * Fetches inbox items from /api/inbox, renders approval cards,
 * manages tabs and the detail modal.
 */
(function () {
  'use strict';

  /* ── DOM refs ───────────────────────────────────────────── */
  const listEl       = document.getElementById('inbox-list');
  const emptyEl      = document.getElementById('inbox-empty');
  const tabBar       = document.getElementById('inbox-tabs');
  const badge        = document.getElementById('inbox-count-badge');
  const overlay      = document.getElementById('inbox-modal-overlay');
  const modalTitle   = document.getElementById('inbox-modal-title');
  const modalBody    = document.getElementById('inbox-modal-body');
  const modalMeta    = document.getElementById('inbox-modal-meta');
  const modalActions = document.getElementById('inbox-modal-actions');
  const modalReply   = document.getElementById('inbox-modal-reply');
  const btnApprove   = document.getElementById('inbox-modal-approve');
  const btnReject    = document.getElementById('inbox-modal-reject');
  const btnSendReply = document.getElementById('inbox-modal-send-reply');
  const btnClose     = document.getElementById('inbox-modal-close');
  const replyText    = document.getElementById('inbox-reply-text');

  let currentStatus = 'pending';
  let currentItem = null;

  /* ── Helpers ────────────────────────────────────────────── */
  function timeAgo(iso) {
    if (!iso) return '';
    const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 60) return secs + 's ago';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
    return Math.floor(secs / 86400) + 'd ago';
  }

  /* ── Fetch ──────────────────────────────────────────────── */
  async function fetchItems(status) {
    const qs = status ? '?status=' + status : '';
    try {
      const r = await fetch('/api/inbox' + qs);
      return (await r.json()).items || [];
    } catch {
      return [];
    }
  }

  async function fetchCounts() {
    try {
      const r = await fetch('/api/inbox/count');
      return await r.json();
    } catch {
      return { pending: 0, approved: 0, rejected: 0, total: 0 };
    }
  }

  /* ── Render list ────────────────────────────────────────── */
  function renderItems(items) {
    // Remove previous cards (both legacy divs and <approval-card> elements)
    listEl.querySelectorAll('.inbox-card, approval-card').forEach(function (c) { c.remove(); });

    if (!items.length) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;

    items.forEach(function (item) {
      var card = document.createElement('approval-card');
      card.setAttribute('item-id', item.id || '');
      card.setAttribute('title', item.title || '');
      card.setAttribute('body', item.body || '');
      card.setAttribute('item-type', item.item_type || '');
      card.setAttribute('status', item.status || 'pending');
      card.setAttribute('source-type', item.source_type || '');
      card.setAttribute('source-id', item.source_id || '');
      card.setAttribute('node-id', item.node_id || '');
      card.setAttribute('agent-id', item.agent_id || '');
      card.setAttribute('created-at', item.created_at || '');
      listEl.appendChild(card);
    });
  }

  async function updateCounts() {
    var c = await fetchCounts();
    document.getElementById('tab-count-pending').textContent = c.pending;
    document.getElementById('tab-count-approved').textContent = c.approved;
    document.getElementById('tab-count-rejected').textContent = c.rejected;
    badge.textContent = c.pending > 0 ? c.pending + ' pending' : '';
  }

  async function load() {
    var items = await fetchItems(currentStatus);
    renderItems(items);
    await updateCounts();
  }

  /* ── Tabs ───────────────────────────────────────────────── */
  tabBar.addEventListener('click', function (e) {
    var tab = e.target.closest('.inbox-tab');
    if (!tab) return;
    tabBar.querySelectorAll('.inbox-tab').forEach(function (t) { t.classList.remove('active'); });
    tab.classList.add('active');
    currentStatus = tab.dataset.status || '';
    load();
  });

  /* ── Inline approve / reject / reply via <approval-card> ── */
  listEl.addEventListener('approval-action', function (e) {
    var d = e.detail;
    if (!d || !d.itemId || !d.action) return;
    if (d.action === 'reply') {
      resolveItem(d.itemId, 'reply', { message: d.message });
    } else {
      resolveItem(d.itemId, d.action);
    }
  });

  /* Click on <approval-card> itself -> open modal */
  listEl.addEventListener('click', function (e) {
    var card = e.target.closest('approval-card');
    if (card) {
      openModal(card.getAttribute('item-id'));
    }
  });

  /* ── Resolve item ───────────────────────────────────────── */
  async function resolveItem(id, action, body) {
    try {
      var r = await fetch('/api/inbox/' + id + '/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      var d = await r.json();
      if (d.ok) {
        load();
        closeModal();
      }
    } catch { /* */ }
  }

  /* ── Modal ──────────────────────────────────────────────── */
  async function openModal(id) {
    try {
      var r = await fetch('/api/inbox/' + id);
      var d = await r.json();
      currentItem = d.item;
    } catch {
      return;
    }

    modalTitle.textContent = currentItem.title || '';
    modalBody.textContent = currentItem.body || '';
    modalMeta.textContent = [
      'Type: ' + currentItem.item_type,
      'Source: ' + currentItem.source_type,
      currentItem.agent_id ? 'Agent: ' + currentItem.agent_id : '',
      'Created: ' + timeAgo(currentItem.created_at),
    ].filter(Boolean).join('  ·  ');

    // Show/hide actions based on status
    var isPending = currentItem.status === 'pending';
    modalActions.hidden = !isPending;

    // Show reply area for ask_human type
    var isQuestion = currentItem.item_type === 'ask_human';
    modalReply.hidden = !(isPending && isQuestion);

    overlay.hidden = false;
  }

  function closeModal() {
    overlay.hidden = true;
    currentItem = null;
    if (replyText) replyText.value = '';
  }

  btnClose.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeModal();
  });

  btnApprove.addEventListener('click', function () {
    if (currentItem) resolveItem(currentItem.id, 'approve');
  });
  btnReject.addEventListener('click', function () {
    if (currentItem) resolveItem(currentItem.id, 'reject');
  });
  btnSendReply.addEventListener('click', function () {
    if (currentItem && replyText.value.trim()) {
      resolveItem(currentItem.id, 'reply', { message: replyText.value.trim() });
    }
  });

  /* ── WebSocket for real-time updates ────────────────────── */
  function connectWS() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var ws = new WebSocket(proto + '//' + location.host + '/ws/events');
    ws.onmessage = function (e) {
      try {
        var msg = JSON.parse(e.data);
        if (msg.type === 'inbox.new' || msg.type === 'inbox.resolved') {
          load();
        }
      } catch { /* ignore */ }
    };
    ws.onclose = function () {
      setTimeout(connectWS, 5000);
    };
  }

  /* ── Init ───────────────────────────────────────────────── */
  load();
  connectWS();
  setInterval(load, 30000);
})();
