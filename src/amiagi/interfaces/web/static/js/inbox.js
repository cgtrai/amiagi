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
  const replyDialog  = document.getElementById('inbox-reply-dialog');
  const btnApprove   = document.getElementById('inbox-modal-approve');
  const btnReject    = document.getElementById('inbox-modal-reject');
  const btnGrant     = document.getElementById('inbox-modal-grant');
  const btnClose     = document.getElementById('inbox-modal-close');
  const btnBatchApprove = document.getElementById('btn-batch-approve');
  const btnBatchReject = document.getElementById('btn-batch-reject');
  const selectionCount = document.getElementById('inbox-selection-count');

  let currentStatus = 'pending';
  let currentItem = null;
  const selectedItems = new Set();

  /* ── Helpers ────────────────────────────────────────────── */
  function timeAgo(iso) {
    if (!iso) return '';
    const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 60) return secs + 's ago';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
    return Math.floor(secs / 86400) + 'd ago';
  }

  function tr(key, fallback) {
    return typeof window.t === 'function' ? window.t(key) : fallback;
  }

  function priorityBucket(priority) {
    if ((priority || 0) >= 8) return 'high';
    if ((priority || 0) >= 4) return 'medium';
    return 'low';
  }

  function priorityLabel(priority) {
    const bucket = priorityBucket(priority);
    if (bucket === 'high') return tr('inbox.priority_high', 'High priority');
    if (bucket === 'medium') return tr('inbox.priority_medium', 'Medium priority');
    return tr('inbox.priority_low', 'Low priority');
  }

  function timeoutState(item) {
    if (!item || !item.created_at || item.status !== 'pending') return null;
    const ageHours = (Date.now() - new Date(item.created_at).getTime()) / 3600000;
    if (ageHours >= 72) return { key: 'due', label: tr('inbox.auto_escalated', 'Auto-escalated') };
    if (ageHours >= 48) return { key: 'soon', label: tr('inbox.auto_escalation_soon', 'Auto-escalation soon') };
    if (ageHours >= 24) return { key: 'ok', label: tr('inbox.sla_active', 'SLA active') };
    return null;
  }

  function updateSelectionUi() {
    const count = selectedItems.size;
    if (selectionCount) {
      selectionCount.textContent = count
        ? tr('inbox.selected_count', '{count} selected').replace('{count}', String(count))
        : tr('inbox.none_selected', 'No items selected');
    }
    if (btnBatchApprove) btnBatchApprove.disabled = count === 0;
    if (btnBatchReject) btnBatchReject.disabled = count === 0;
  }

  function clearSelection() {
    selectedItems.clear();
    updateSelectionUi();
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
    listEl.innerHTML = '';

    if (!items.length) {
      emptyEl.hidden = false;
      clearSelection();
      return;
    }
    emptyEl.hidden = true;

    const groups = { high: [], medium: [], low: [] };
    const useGrouping = currentStatus === 'pending' || currentStatus === '';
    items.forEach(function (item) {
      if (!useGrouping) {
        groups.low.push(item);
        return;
      }
      groups[priorityBucket(item.priority)].push(item);
    });

    [['high', tr('inbox.priority_high', 'High priority')], ['medium', tr('inbox.priority_medium', 'Medium priority')], ['low', tr('inbox.priority_low', 'Low priority')]].forEach(function (entry) {
      var bucket = entry[0];
      var label = entry[1];
      var bucketItems = groups[bucket];
      if (!bucketItems.length) return;
      var section = document.createElement('section');
      section.className = 'inbox-group';
      if (useGrouping) {
        section.innerHTML = '<div class="inbox-group-header"><span>' + label + '</span><span class="inbox-group-count">' + bucketItems.length + '</span></div>';
      }
      bucketItems.forEach(function (item) {
        var row = document.createElement('div');
        row.className = 'inbox-row';
        var timeout = timeoutState(item);
        var badges = '<div class="inbox-row-badges">' +
          '<span class="inbox-priority-badge inbox-priority-badge--' + priorityBucket(item.priority) + '">' + priorityLabel(item.priority) + '</span>' +
          (timeout ? '<span class="inbox-timeout-badge inbox-timeout-badge--' + timeout.key + '">' + timeout.label + '</span>' : '') +
          '</div>';
        if (item.status === 'pending') {
          var checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.className = 'inbox-select';
          checkbox.dataset.itemId = item.id || '';
          checkbox.checked = selectedItems.has(item.id);
          row.appendChild(checkbox);
        }
        var main = document.createElement('div');
        main.className = 'inbox-row-main';
        main.innerHTML = badges;
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
        main.appendChild(card);
        row.appendChild(main);
        section.appendChild(row);
      });
      listEl.appendChild(section);
    });
    updateSelectionUi();
  }

  async function updateCounts() {
    var c = await fetchCounts();
    document.getElementById('tab-count-pending').textContent = c.pending;
    document.getElementById('tab-count-approved').textContent = c.approved;
    document.getElementById('tab-count-rejected').textContent = c.rejected;
    var expired = document.getElementById('tab-count-expired');
    if (expired) expired.textContent = c.expired || 0;
    if (badge) badge.textContent = c.pending > 0 ? c.pending + ' pending' : '';
  }

  async function load() {
    clearSelection();
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

  listEl.addEventListener('change', function (e) {
    var checkbox = e.target.closest('.inbox-select');
    if (!checkbox) return;
    if (checkbox.checked) selectedItems.add(checkbox.dataset.itemId);
    else selectedItems.delete(checkbox.dataset.itemId);
    updateSelectionUi();
  });

  /* Click on <approval-card> itself -> open modal */
  listEl.addEventListener('click', function (e) {
    // Skip if click originated from a button inside the shadow DOM
    var path = e.composedPath ? e.composedPath() : [];
    for (var i = 0; i < path.length && path[i] !== e.currentTarget; i++) {
      if (path[i].tagName === 'BUTTON' || path[i].tagName === 'TEXTAREA') return;
    }
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
      } else {
        alert(d.error || 'Action failed');
      }
    } catch (err) {
      alert('Network error: ' + err.message);
    }
  }

  async function batchResolve(action) {
    if (!selectedItems.size) return;
    try {
      var r = await fetch('/api/inbox/batch/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_ids: Array.from(selectedItems) }),
      });
      var d = await r.json();
      if (d.ok) {
        clearSelection();
        await load();
        if (typeof showToast === 'function') {
          showToast((d.resolved_count || 0) + ' ' + tr('inbox.batch_done', 'items updated'), 'success');
        }
      } else if (typeof showToast === 'function') {
        showToast(d.error || tr('inbox.batch_failed', 'Batch action failed'), 'error');
      }
    } catch (err) {
      if (typeof showToast === 'function') showToast(err.message, 'error');
    }
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
      'Priority: ' + (currentItem.priority || 0),
      currentItem.agent_id ? 'Agent: ' + currentItem.agent_id : '',
      'Created: ' + timeAgo(currentItem.created_at),
    ].filter(Boolean).join('  ·  ');
    // IN5: Context links
    var contextHtml = '';
    if (currentItem.source_type === 'workflow' && currentItem.source_id) {
      contextHtml += ' <a href="/workflows?run=' + currentItem.source_id + '" class="glass-btn glass-btn--xs glass-btn--ghost">\ud83d\udcca View Workflow</a>';
    }
    if (currentItem.metadata && currentItem.metadata.plan) {
      contextHtml += ' <button class="glass-btn glass-btn--xs glass-btn--ghost" onclick="if(typeof openDetailDrawer===\'function\')openDetailDrawer(\'Full Plan\',\'<pre>'+encodeURIComponent(currentItem.metadata.plan)+'</pre>\')">\ud83d\udccb Show Plan</button>';
    }
    if (contextHtml) {
      modalMeta.innerHTML = modalMeta.textContent + contextHtml;
    }
    // Show/hide actions based on status
    var isPending = currentItem.status === 'pending';
    modalActions.hidden = !isPending;
    if (btnGrant) {
      btnGrant.hidden = !(isPending && currentItem.item_type === 'secret_request');
    }

    // Show reply area for ask_human / review_request
    var isQuestion = ['ask_human', 'review_request'].includes(currentItem.item_type);
    modalReply.hidden = !(isPending && isQuestion);
    if (replyDialog) replyDialog.clear();

    overlay.classList.add('open');
    overlay.style.display = 'flex';
    overlay.hidden = false;
  }

  function closeModal() {
    overlay.classList.remove('open');
    overlay.style.display = 'none';
    overlay.hidden = true;
    currentItem = null;
    if (replyDialog) replyDialog.clear();
  }

  btnClose.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    closeModal();
  });
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeModal();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && overlay.classList.contains('open')) closeModal();
  });

  btnApprove.addEventListener('click', function () {
    if (currentItem) resolveItem(currentItem.id, 'approve');
  });
  btnReject.addEventListener('click', function () {
    if (currentItem) resolveItem(currentItem.id, 'reject');
  });
  if (btnGrant) {
    btnGrant.addEventListener('click', async function () {
      if (!currentItem) return;
      try {
        var response = await fetch('/api/inbox/' + currentItem.id + '/grant', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            secret_id: currentItem.metadata && currentItem.metadata.secret_id,
            entity_id: (currentItem.metadata && (currentItem.metadata.entity_id || currentItem.metadata.agent_id)) || currentItem.agent_id,
            entity_type: (currentItem.metadata && currentItem.metadata.entity_type) || 'agent',
          }),
        });
        var payload = await response.json();
        if (!response.ok || !payload.ok) {
          if (typeof showToast === 'function') showToast((payload && payload.error) || 'Grant failed', 'error');
          return;
        }
        if (typeof showToast === 'function') showToast(tr('inbox.grant_success', 'Secret granted'), 'success');
        await load();
        closeModal();
      } catch (err) {
        if (typeof showToast === 'function') showToast(err.message, 'error');
      }
    });
  }
  if (replyDialog) {
    replyDialog.addEventListener('reply-submit', function (e) {
      if (currentItem && e.detail && e.detail.message) {
        resolveItem(currentItem.id, 'reply', { message: e.detail.message });
      }
    });
  }

  if (btnBatchApprove) {
    btnBatchApprove.addEventListener('click', function () { batchResolve('approve'); });
  }
  if (btnBatchReject) {
    btnBatchReject.addEventListener('click', function () { batchResolve('reject'); });
  }

  /* ── Delegate handler (IN1) ─────────────────────────────── */
  var btnDelegate = document.getElementById('inbox-modal-delegate');
  if (btnDelegate) {
    btnDelegate.addEventListener('click', async function () {
      if (!currentItem) return;
      try {
        var r = await fetch('/api/agents');
        var data = await r.json();
        var agents = data.agents || data || [];
        var options = agents.map(function(a) {
          return '<option value="' + (a.agent_id || '') + '">' + (a.name || a.agent_id) + '</option>';
        }).join('');
        if (typeof openDetailDrawer === 'function') {
          openDetailDrawer('Delegate to Agent', '<div style="display:grid;gap:var(--space-3)">' +
            '<select class="glass-input" id="delegate-agent">' + options + '</select>' +
            '<button class="glass-btn glass-btn--primary" id="btn-confirm-delegate">Confirm</button></div>');
          setTimeout(function() {
            var btn = document.getElementById('btn-confirm-delegate');
            if (btn) btn.addEventListener('click', async function() {
              var agentId = document.getElementById('delegate-agent').value;
              var response = await fetch('/api/inbox/' + currentItem.id + '/delegate', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({agent_id: agentId})
              });
              var payload = {};
              try { payload = await response.json(); } catch (e) {}
              if (!response.ok || !payload.ok) {
                if (typeof showToast === 'function') showToast((payload && payload.error) || 'Delegation failed', 'error');
                return;
              }
              if (typeof closeDetailDrawer === 'function') closeDetailDrawer();
              if (typeof showToast === 'function') showToast('Delegated', 'success');
              await updateCounts();
              load();
            });
          }, 100);
        }
      } catch(e) { if (typeof showToast === 'function') showToast('Error loading agents', 'error'); }
    });
  }

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
  updateSelectionUi();
  connectWS();
  setInterval(load, 30000);
})();
