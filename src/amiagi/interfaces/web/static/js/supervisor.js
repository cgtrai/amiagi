/**
 * supervisor.js — Mission Control client logic.
 *
 * Fetches consolidated system state from GET /api/system/state,
 * renders inline agent controls (pause / resume / terminate),
 * handles operator input form and agent spawning.
 * Subscribes to the <live-stream> Web Component for real-time events.
 */
(function () {
  'use strict';

  /* ── DOM refs ───────────────────────────────────────────── */
  const supervisorRoot = document.querySelector('.supervisor-main');
  const agentList     = document.getElementById('agent-control-list');
  const liveStream    = document.getElementById('supervisor-live-stream');
  const connectionBanner = document.getElementById('supervisor-connection-banner');
  const gpuRamAvailability = document.getElementById('sv-gpu-ram');
  const tokensCount   = document.getElementById('sv-tokens-count');
  const sessionCost   = document.getElementById('sv-session-cost');
  const gpuUsage      = document.getElementById('sv-gpu-usage');
  const queueCount    = document.getElementById('sv-queue-count');
  const btnRefresh    = document.getElementById('btn-sv-refresh');
  const btnCommands   = document.getElementById('btn-sv-commands');
  const btnHistory    = document.getElementById('btn-sv-history');
  const streamChannelFilter = document.getElementById('sv-stream-channel-filter');
  const streamLevelFilter = document.getElementById('sv-stream-level-filter');
  const btnStreamClear = document.getElementById('btn-sv-stream-clear');
  const inputForm     = document.getElementById('operator-input-form');
  const inputText     = document.getElementById('operator-input-text');
  const inputTarget   = document.getElementById('operator-input-target');
  const inputStatus   = document.getElementById('operator-input-status');
  const btnAddAgent   = document.getElementById('btn-sv-add-agent');
  const detailDrawer  = document.getElementById('detail-drawer');
  const drawerBody    = document.getElementById('drawer-body');
  const appShell      = document.querySelector('.app-shell');
  const REFRESH_INTERVAL_MS = 30000;

  const INPUT_HISTORY_KEY = 'amiagi_supervisor_input_history';
  const INPUT_HISTORY_LIMIT = 50;
  let inputHistory = loadInputHistory();
  let inputHistoryIndex = inputHistory.length;
  let inputDraftValue = '';
  let drawerSwipeStartX = null;
  let drawerSwipeStartY = null;
  let lastAgentsSnapshot = '';
  let lastTargetSnapshot = '';
  const AGENT_THREAD_ENTRY_LIMIT = 80;
  const AGENT_THREAD_AUTO_SCROLL_THRESHOLD = 60;
  const agentThreadEntries = new Map();
  const agentLastActivity = new Map();
  const agentThreadDroppedCounts = new Map();
  const agentThreadScrollFrames = new Map();
  const agentThreadAutoScroll = new Map();
  const actorStateSnapshot = new Map();
  const pendingActions = new Set();
  let streamSessionId = '';
  let streamActiveAgents = [];

  /* ── Helpers ────────────────────────────────────────────── */
  function stateClass(state) {
    const s = (state || '').toLowerCase();
    if (s === 'idle') return 'agent-state--idle';
    if (s === 'working') return 'agent-state--working';
    if (s === 'paused') return 'agent-state--paused';
    if (s === 'terminated') return 'agent-state--terminated';
    if (s === 'error') return 'agent-state--error';
    return '';
  }

  function stateLabel(state) {
    var normalized = String(state || '').toLowerCase();
    if (normalized === 'idle') return window.t('supervisor.state_idle', 'Idle');
    if (normalized === 'working') return window.t('supervisor.state_working', 'Working');
    if (normalized === 'paused') return window.t('supervisor.state_paused', 'Paused');
    if (normalized === 'terminated') return window.t('supervisor.state_terminated', 'Terminated');
    if (normalized === 'error') return window.t('supervisor.state_error', 'Error');
    return String(state || '—') || '—';
  }

  function stateIconMarkup(state) {
    var normalized = String(state || '').toLowerCase();
    if (normalized === 'idle') {
      return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="5"/></svg>';
    }
    if (normalized === 'working') {
      return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v4"/><path d="M12 18v4"/><path d="m4.93 4.93 2.83 2.83"/><path d="m16.24 16.24 2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><path d="m4.93 19.07 2.83-2.83"/><path d="m16.24 7.76 2.83-2.83"/></svg>';
    }
    if (normalized === 'paused') {
      return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
    }
    if (normalized === 'terminated') {
      return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    }
    if (normalized === 'error') {
      return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>';
    }
    return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="5"/></svg>';
  }

  function formatSessionCost(amount, currency) {
    var value = Number(amount || 0);
    var code = String(currency || 'USD').trim().toUpperCase();
    if (code === 'USD') return '$' + value.toFixed(2);
    if (code === 'EUR') return 'EUR ' + value.toFixed(2);
    if (code === 'PLN') return 'PLN ' + value.toFixed(2);
    return code + ' ' + value.toFixed(2);
  }

  function formatPercent(value) {
    var numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < 0) return '--%';
    var bounded = Math.max(0, Math.min(100, Math.round(numeric)));
    return String(bounded).padStart(2, '0') + '%';
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function getCurrentUserLabel() {
    if (!supervisorRoot) return 'Operator';
    return String(supervisorRoot.dataset.currentUserLabel || '').trim() || 'Operator';
  }

  function iconMarkup(name) {
    if (name === 'edit') {
      return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg>';
    }
    if (name === 'resume') {
      return '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="8 5 19 12 8 19 8 5"/></svg>';
    }
    if (name === 'pause') {
      return '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
    }
    if (name === 'stop') {
      return '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    }
    return '';
  }

  function renderAgentIconButton(options) {
    var label = String(options && options.label || '').trim();
    var icon = String(options && options.icon || '').trim();
    var className = String(options && options.className || '').trim();
    var attrs = [
      'type="button"',
      'class="glass-btn glass-btn--ghost agent-control-action' + (className ? (' ' + className) : '') + '"',
      'title="' + escapeAttr(label) + '"',
      'aria-label="' + escapeAttr(label) + '"'
    ];
    if (options && options.dataAction) {
      attrs.push('data-action="' + escapeAttr(options.dataAction) + '"');
    }
    if (options && options.dataId) {
      attrs.push('data-id="' + escapeAttr(options.dataId) + '"');
    }
    if (options && options.setupUrl) {
      attrs.push('data-agent-setup-url="' + escapeAttr(options.setupUrl) + '"');
    }
    return '<button ' + attrs.join(' ') + '>' + iconMarkup(icon) + '</button>';
  }

  function insertSupervisorCommand(command) {
    if (!inputText) return;
    const trimmed = String(command || '').trim();
    if (!trimmed) return;
    inputText.value = trimmed;
    inputText.focus();
    inputText.setSelectionRange(inputText.value.length, inputText.value.length);
    inputText.dispatchEvent(new Event('input', { bubbles: true }));
    if (inputStatus) {
      inputStatus.textContent = 'Command copied to input';
      inputStatus.className = 'operator-input-status operator-input-status--ok';
    }
    if (typeof closeDetailDrawer === 'function') {
      closeDetailDrawer();
    }
  }

  window.supervisorInsertCommand = insertSupervisorCommand;

  function appendSupervisorStreamMessage(text, level, meta, channel) {
    if (!liveStream || typeof liveStream.append !== 'function') return;
    var normalizedText = String(text || '').trim();
    if (!normalizedText) return;
    liveStream.append(normalizedText, level || 'info', channel || 'supervisor', meta || {});
  }

  function normalizeCommandOutputLines(output) {
    return String(output || '')
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean);
  }

  function appendLogStyleStreamEntry(sourceLabel, message, level, channel, meta) {
    var normalizedMessage = String(message || '').trim();
    if (!normalizedMessage) return;
    var resolvedSource = String(sourceLabel || '').trim() || 'System';
    appendSupervisorStreamMessage(resolvedSource + ': ' + normalizedMessage, level || 'info', Object.assign({
      type: 'log',
      source_label: resolvedSource,
      message: normalizedMessage,
      summary: resolvedSource + ': ' + normalizedMessage,
    }, meta || {}), channel || 'system');
  }

  function loadInputHistory() {
    try {
      var raw = window.sessionStorage.getItem(INPUT_HISTORY_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter(Boolean).slice(-INPUT_HISTORY_LIMIT) : [];
    } catch (_) {
      return [];
    }
  }

  function saveInputHistory() {
    try {
      window.sessionStorage.setItem(INPUT_HISTORY_KEY, JSON.stringify(inputHistory.slice(-INPUT_HISTORY_LIMIT)));
    } catch (_) { /* ignore */ }
  }

  function rememberInputHistory(entry) {
    var value = String(entry || '').trim();
    if (!value) return;
    if (inputHistory[inputHistory.length - 1] === value) {
      inputHistoryIndex = inputHistory.length;
      inputDraftValue = '';
      return;
    }
    inputHistory = inputHistory.filter(function (item) { return item !== value; });
    inputHistory.push(value);
    if (inputHistory.length > INPUT_HISTORY_LIMIT) {
      inputHistory = inputHistory.slice(-INPUT_HISTORY_LIMIT);
    }
    saveInputHistory();
    inputHistoryIndex = inputHistory.length;
    inputDraftValue = '';
  }

  function moveThroughInputHistory(direction) {
    if (!inputText || !inputHistory.length) return;
    if (direction < 0) {
      if (inputHistoryIndex === inputHistory.length) {
        inputDraftValue = inputText.value;
      }
      inputHistoryIndex = Math.max(0, inputHistoryIndex - 1);
      inputText.value = inputHistory[inputHistoryIndex] || '';
    } else {
      inputHistoryIndex = Math.min(inputHistory.length, inputHistoryIndex + 1);
      inputText.value = inputHistoryIndex === inputHistory.length
        ? inputDraftValue
        : (inputHistory[inputHistoryIndex] || '');
    }
    inputText.focus();
    inputText.setSelectionRange(inputText.value.length, inputText.value.length);
  }

  function resetInputHistoryCursor() {
    inputHistoryIndex = inputHistory.length;
    inputDraftValue = inputText ? inputText.value : '';
  }

  function buildAgentSetupUrl(agent) {
    return '/agents/' + encodeURIComponent(agent.agent_id || '');
  }

  function withSupervisorAgents(agents) {
    var items = Array.isArray(agents) ? agents.slice() : [];
    var knownIds = new Set(items.map(function (agent) {
      return String((agent && agent.agent_id) || '').trim().toLowerCase();
    }).filter(Boolean));

    if (streamActiveAgents.some(function (agentId) { return String(agentId || '').trim().toLowerCase() === 'kastor'; }) && !knownIds.has('kastor')) {
      items.unshift({
        agent_id: 'kastor',
        name: 'Kastor',
        role: 'supervisor',
        state: 'working',
        model_name: 'system',
        is_virtual: true,
      });
    }

    if (!knownIds.has('router')) {
      items.push({
        agent_id: 'router',
        name: 'Router',
        role: 'system',
        state: String(actorStateSnapshot.get('router') || 'active').toLowerCase(),
        model_name: 'system',
        is_virtual: true,
      });
    }

    return items;
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function normalizePendingTargets(targets) {
    if (!targets) return [];
    return (Array.isArray(targets) ? targets : [targets]).filter(Boolean);
  }

  function setPendingUiState(targets, isPending) {
    normalizePendingTargets(targets).forEach(function (target) {
      if ('disabled' in target) {
        target.disabled = isPending;
      }
      if (isPending) {
        target.setAttribute('aria-busy', 'true');
      } else {
        target.removeAttribute('aria-busy');
      }
    });
  }

  function runSingleFlight(key, targets, callback) {
    if (pendingActions.has(key)) {
      return Promise.resolve(false);
    }
    pendingActions.add(key);
    setPendingUiState(targets, true);
    return Promise.resolve()
      .then(callback)
      .finally(function () {
        pendingActions.delete(key);
        setPendingUiState(targets, false);
      })
      .then(function () {
        return true;
      });
  }

  function isEditableTarget(target) {
    if (!target || typeof target.closest !== 'function') return false;
    if (target.isContentEditable) return true;
    return !!target.closest('input, textarea, select, [contenteditable="true"]');
  }

  function focusOperatorInput() {
    if (!inputText) return;
    inputText.focus();
    inputText.setSelectionRange(inputText.value.length, inputText.value.length);
  }

  function getAgentFocusTargets() {
    return Array.from(agentList.querySelectorAll('.agent-control-row__summary.agent-control-row--link'));
  }

  function focusAgentSummaryByOffset(offset) {
    var focusTargets = getAgentFocusTargets();
    if (!focusTargets.length) return;
    var currentIndex = focusTargets.findIndex(function (element) {
      return element === document.activeElement;
    });
    if (currentIndex === -1) {
      focusTargets[offset > 0 ? 0 : focusTargets.length - 1].focus();
      return;
    }
    var nextIndex = currentIndex + offset;
    if (nextIndex < 0) nextIndex = 0;
    if (nextIndex >= focusTargets.length) nextIndex = focusTargets.length - 1;
    focusTargets[nextIndex].focus();
  }

  function safeDomToken(value) {
    return String(value || '').trim().replace(/[^a-zA-Z0-9_-]/g, '-');
  }

  function agentThreadOwner(agentId) {
    var normalizedId = String(agentId || '').trim();
    return normalizedId ? ('agent:' + normalizedId) : '';
  }

  function agentThreadScreenId(agentId) {
    return 'agent-thread-screen-' + safeDomToken(agentId);
  }

  function agentThreadCountId(agentId) {
    return 'agent-thread-count-' + safeDomToken(agentId);
  }

  function agentLastActivityId(agentId) {
    return 'agent-last-activity-' + safeDomToken(agentId);
  }

  function agentRetentionId(agentId) {
    return 'agent-thread-retention-' + safeDomToken(agentId);
  }

  function syncAgentPanelState(agents) {
    var activeAgentIds = new Set((agents || []).map(function (agent) {
      return String(agent.agent_id || '').trim();
    }).filter(Boolean));
    Array.from(agentLastActivity.keys()).forEach(function (agentId) {
      if (!activeAgentIds.has(agentId)) {
        agentLastActivity.delete(agentId);
      }
    });
    Array.from(agentThreadDroppedCounts.keys()).forEach(function (agentId) {
      if (!activeAgentIds.has(agentId)) {
        agentThreadDroppedCounts.delete(agentId);
      }
    });
    Array.from(agentThreadAutoScroll.keys()).forEach(function (agentId) {
      if (!activeAgentIds.has(agentId)) {
        agentThreadAutoScroll.delete(agentId);
      }
    });
    Array.from(agentThreadScrollFrames.keys()).forEach(function (agentId) {
      if (!activeAgentIds.has(agentId)) {
        cancelAnimationFrame(agentThreadScrollFrames.get(agentId));
        agentThreadScrollFrames.delete(agentId);
      }
    });
  }

  function formatLastActivityLabel(value) {
    var label = window.t('supervisor.last_activity', 'Last activity');
    if (!value) return label + ': —';
    var date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return label + ': —';
    return label + ': ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function updateAgentLastActivity(agentId, timestamp) {
    var normalizedId = String(agentId || '').trim();
    if (!normalizedId) return;
    var resolvedTimestamp = timestamp || new Date().toISOString();
    agentLastActivity.set(normalizedId, resolvedTimestamp);
    var badge = document.getElementById(agentLastActivityId(normalizedId));
    if (badge) {
      badge.textContent = formatLastActivityLabel(resolvedTimestamp);
    }
  }

  function ensureAgentThreadBuffer(agentId) {
    var owner = agentThreadOwner(agentId);
    if (!owner) return [];
    if (!agentThreadEntries.has(owner)) {
      agentThreadEntries.set(owner, []);
    }
    return agentThreadEntries.get(owner);
  }

  function shouldAutoScrollAgentThread(agentId) {
    var normalizedId = String(agentId || '').trim();
    if (!normalizedId) return true;
    if (!agentThreadAutoScroll.has(normalizedId)) {
      agentThreadAutoScroll.set(normalizedId, true);
    }
    return agentThreadAutoScroll.get(normalizedId) !== false;
  }

  function updateAgentThreadAutoScroll(agentId, screen) {
    var normalizedId = String(agentId || '').trim();
    if (!normalizedId || !screen) return;
    var distanceFromBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight;
    agentThreadAutoScroll.set(normalizedId, distanceFromBottom < AGENT_THREAD_AUTO_SCROLL_THRESHOLD);
  }

  function bindAgentThreadScroll(agentId) {
    var normalizedId = String(agentId || '').trim();
    if (!normalizedId) return;
    var screen = document.getElementById(agentThreadScreenId(normalizedId));
    if (!screen || screen.dataset.autoscrollBound === 'true') return;
    screen.dataset.autoscrollBound = 'true';
    screen.addEventListener('scroll', function () {
      updateAgentThreadAutoScroll(normalizedId, screen);
    });
  }

  function pruneAgentThreadBuffers(agents) {
    var activeOwners = new Set((agents || []).map(function (agent) {
      return agentThreadOwner(agent.agent_id);
    }).filter(Boolean));
    Array.from(agentThreadEntries.keys()).forEach(function (owner) {
      if (!activeOwners.has(owner)) {
        agentThreadEntries.delete(owner);
      }
    });
    activeOwners.forEach(function (owner) {
      if (!agentThreadEntries.has(owner)) {
        agentThreadEntries.set(owner, []);
      }
    });
  }

  function normalizeThreadOwners(detail) {
    var owners = [];
    if (detail && Array.isArray(detail.thread_owners)) {
      owners = detail.thread_owners.map(function (owner) {
        return String(owner || '').trim();
      }).filter(Boolean);
    }
    if (!owners.length && detail && detail.thread_owner) {
      owners.push(String(detail.thread_owner || '').trim());
    }
    if (!owners.length && detail && detail.target_agent) {
      owners.push(agentThreadOwner(detail.target_agent));
    }
    if (!owners.length && detail && detail.agent_id) {
      owners.push(agentThreadOwner(detail.agent_id));
    }
    return Array.from(new Set(owners.filter(Boolean)));
  }

  function formatThreadTimestamp(detail) {
    var raw = detail && (detail.timestamp || detail.created_at || detail.ts || detail.time);
    var date = raw ? new Date(raw) : new Date();
    if (Number.isNaN(date.getTime())) {
      date = new Date();
    }
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function summarizeThreadFlow(detail) {
    var source = (detail && (detail.from || detail.source_label || detail.actor || detail.agent_id)) || 'system';
    var target = (detail && detail.to)
      || ((detail && detail.target_scope === 'broadcast')
      ? 'all'
      : ((detail && (detail.target_agent || detail.agent_id)) || 'screen'));
    return String(source) + ' -> ' + String(target);
  }

  function buildAgentThreadEntry(detail) {
    var summary = String((detail && (detail.summary || detail.message || detail.message_type || detail.type)) || 'Event').trim() || 'Event';
    var source = String((detail && (detail.from || detail.source_label || detail.actor || detail.agent_id)) || 'System').trim() || 'System';
    var target = String((detail && detail.to)
      || ((detail && detail.target_scope === 'broadcast')
      ? 'all'
      : ((detail && detail.target_agent) || 'screen'))).trim();
    var status = String((detail && detail.status) || '').trim();
    var type = String((detail && (detail.message_type || detail.type)) || 'event');
    var channel = String((detail && detail.channel) || 'system').toLowerCase();
    return {
      summary: summary,
      type: type,
      status: status,
      level: String((detail && detail.level) || 'info').toLowerCase(),
      flow: summarizeThreadFlow(detail || {}),
      time: formatThreadTimestamp(detail || {}),
      channel: channel,
      chips: [
        { text: source, className: 'entry-chip entry-chip--source' },
        { text: 'to ' + target, className: 'entry-chip entry-chip--target' },
        { text: type, className: 'entry-chip entry-chip--type' },
      ].concat(status ? [{ text: status, className: 'entry-chip entry-chip--status' }] : []),
    };
  }

  function scheduleAgentThreadScrollToBottom(agentId) {
    var normalizedId = String(agentId || '').trim();
    if (!normalizedId) return;
    if (!shouldAutoScrollAgentThread(normalizedId)) return;
    var previousFrame = agentThreadScrollFrames.get(normalizedId);
    if (previousFrame != null) {
      cancelAnimationFrame(previousFrame);
    }
    var frameId = requestAnimationFrame(function () {
      agentThreadScrollFrames.delete(normalizedId);
      var screen = document.getElementById(agentThreadScreenId(normalizedId));
      if (!screen) return;
      screen.scrollTop = screen.scrollHeight;
      updateAgentThreadAutoScroll(normalizedId, screen);
    });
    agentThreadScrollFrames.set(normalizedId, frameId);
  }

  function renderAgentThread(agentId) {
    var screen = document.getElementById(agentThreadScreenId(agentId));
    var countBadge = document.getElementById(agentThreadCountId(agentId));
    var retentionBadge = document.getElementById(agentRetentionId(agentId));
    var normalizedId = String(agentId || '').trim();
    var entries = ensureAgentThreadBuffer(normalizedId);
    var droppedCount = Number(agentThreadDroppedCounts.get(normalizedId) || 0);
    if (countBadge) {
      countBadge.textContent = entries.length + ' msg';
    }
    if (retentionBadge) {
      retentionBadge.hidden = droppedCount <= 0;
      retentionBadge.textContent = droppedCount > 0
        ? ('Retention active: showing last ' + AGENT_THREAD_ENTRY_LIMIT + ' entries')
        : '';
    }
    if (!screen) return;
    bindAgentThreadScroll(normalizedId);
    if (!entries.length) {
      screen.innerHTML = '<div class="agent-thread-screen__empty">' + escapeHtml(window.t('supervisor.agent_waiting', 'Waiting for agent activity')) + '</div>';
      scheduleAgentThreadScrollToBottom(normalizedId);
      return;
    }
    screen.innerHTML = entries.map(function (entry) {
      var sourceClass = {
        executor: 'stream-source-executor',
        supervisor: 'stream-source-supervisor',
        system: 'stream-source-system',
        user: 'stream-source-user'
      }[entry.channel] || '';
      var chipsHtml = (entry.chips || []).map(function (chip) {
        return '<span class="' + escapeAttr(chip.className) + '">' + escapeHtml(chip.text) + '</span>';
      }).join('');
      return (
        '<article class="agent-thread-entry agent-thread-entry--' + escapeAttr(entry.level) + (sourceClass ? (' ' + sourceClass) : '') + '">' +
          '<div class="agent-thread-entry__meta">' +
            '<span class="agent-thread-entry__time">' + escapeHtml(entry.time) + '</span>' +
            '<span class="agent-thread-entry__flow">' + escapeHtml(entry.flow) + '</span>' +
          '</div>' +
          '<div class="agent-thread-entry__chips">' + chipsHtml + '</div>' +
          '<div class="agent-thread-entry__summary">' + escapeHtml(entry.summary) + '</div>' +
        '</article>'
      );
    }).join('');
    scheduleAgentThreadScrollToBottom(normalizedId);
  }

  function routeAgentThreadEvent(detail) {
    var owners = normalizeThreadOwners(detail);
    if (!owners.length) return;
    if (detail && String(detail.message_type || detail.type || '').toLowerCase() === 'actor_state' && detail.actor) {
      actorStateSnapshot.set(String(detail.actor).toLowerCase(), String(detail.status || detail.state || '').toLowerCase());
    }
    var entry = buildAgentThreadEntry(detail || {});
    owners.forEach(function (owner) {
      if (!owner || !owner.startsWith('agent:')) return;
      var agentId = owner.slice('agent:'.length);
      var previousEntries = ensureAgentThreadBuffer(agentId);
      var nextDroppedCount = Math.max(0, previousEntries.length + 1 - AGENT_THREAD_ENTRY_LIMIT);
      var entries = previousEntries.concat(entry).slice(-AGENT_THREAD_ENTRY_LIMIT);
      agentThreadEntries.set(owner, entries);
      if (nextDroppedCount > 0) {
        agentThreadDroppedCounts.set(agentId, Number(agentThreadDroppedCounts.get(agentId) || 0) + nextDroppedCount);
      }
      updateAgentLastActivity(agentId, detail && (detail.timestamp || detail.created_at || detail.ts || detail.time));
      renderAgentThread(agentId);
    });
  }

  function clearAgentThreads() {
    agentThreadEntries.forEach(function (_, owner) {
      agentThreadEntries.set(owner, []);
    });
    Array.from(agentList.querySelectorAll('[data-agent]')).forEach(function (node) {
      renderAgentThread(node.dataset.agent || '');
    });
  }

  function setDrawerWide(expanded) {
    if (!detailDrawer) return;
    detailDrawer.classList.toggle('detail-drawer--wide', !!expanded);
    if (appShell) {
      appShell.classList.toggle('drawer-wide', !!expanded);
    }
    detailDrawer.style.width = '';
  }

  function isDrawerWide() {
    return !!(detailDrawer && detailDrawer.classList.contains('detail-drawer--wide'));
  }

  function decorateSupervisorDrawer(options) {
    if (!detailDrawer || !drawerBody) return;
    var expandable = !!(options && options.expandable);
    detailDrawer.classList.toggle('detail-drawer--supervisor', expandable);
    if (!expandable) {
      setDrawerWide(false);
      return;
    }
    setDrawerWide(false);
    var toggle = drawerBody.querySelector('[data-supervisor-drawer-toggle]');
    if (toggle) {
      toggle.addEventListener('click', function () {
        setDrawerWide(!isDrawerWide());
      });
    }
  }

  function renderExpandableDrawer(title, htmlContent) {
    if (typeof openDetailDrawer !== 'function') return;
    openDetailDrawer(title, htmlContent);
    decorateSupervisorDrawer({ expandable: true });
  }

  function renderHistoryCatalog() {
    if (!inputHistory.length) {
      return (
        '<div class="supervisor-drawer-content supervisor-drawer-content--history">' +
          '<div class="supervisor-drawer-toolbar">' +
            '<strong>' + escapeHtml(window.t('supervisor.history', 'Command History')) + '</strong>' +
            '<button type="button" class="glass-btn glass-btn--ghost glass-btn--sm" data-supervisor-drawer-toggle="history">' + escapeHtml(window.t('supervisor.drawer_expand', 'Expand')) + '</button>' +
          '</div>' +
          '<p class="text-muted">' + escapeHtml(window.t('supervisor.history_empty', 'No commands in this session yet')) + '</p>' +
        '</div>'
      );
    }

    var items = inputHistory.slice().reverse().map(function (command) {
      return (
        '<button type="button" class="drawer-item supervisor-history-item" data-supervisor-command="' + escapeAttr(command) + '" onclick="window.supervisorInsertCommand && window.supervisorInsertCommand(this.dataset.supervisorCommand)">' +
          '<span class="supervisor-history-item__command">' + escapeHtml(command) + '</span>' +
        '</button>'
      );
    }).join('');

    return (
      '<div class="supervisor-drawer-content supervisor-drawer-content--history">' +
        '<div class="supervisor-drawer-toolbar">' +
          '<strong>' + escapeHtml(window.t('supervisor.history', 'Command History')) + '</strong>' +
          '<button type="button" class="glass-btn glass-btn--ghost glass-btn--sm" data-supervisor-drawer-toggle="history">' + escapeHtml(window.t('supervisor.drawer_expand', 'Expand')) + '</button>' +
        '</div>' +
        '<div class="supervisor-history-list">' + items + '</div>' +
      '</div>'
    );
  }

  function syncStreamFilter() {
    if (!liveStream || typeof liveStream.setFilter !== 'function') return;
    liveStream.setFilter({
      channel: streamChannelFilter ? streamChannelFilter.value : 'all',
      level: streamLevelFilter ? streamLevelFilter.value : 'all',
    });
  }

  function setStreamConnectionState(detail) {
    if (!detail) return;
    var badge = document.getElementById('supervisor-status-badge');
    var status = String(detail.status || '').toLowerCase();
    if (status === 'connected') {
      if (connectionBanner) {
        connectionBanner.hidden = true;
        connectionBanner.textContent = '';
        delete connectionBanner.dataset.state;
      }
      if (badge) {
        badge.textContent = 'LIVE';
      }
      return;
    }
    if (status === 'reconnecting') {
      var seconds = Math.max(1, Math.round(Number(detail.delayMs || 0) / 1000));
      if (connectionBanner) {
        connectionBanner.hidden = false;
        connectionBanner.dataset.state = 'reconnecting';
        connectionBanner.textContent = 'Connection lost. Reconnecting in ' + seconds + 's';
      }
      if (badge) {
        badge.textContent = 'RECONNECTING';
      }
    }
  }

  function renderCurrentTask(ct) {
    var panel = document.getElementById('current-task-panel');
    if (!panel) return;
    if (ct && ct.task_id) {
      panel.style.display = '';
      document.getElementById('ct-task-title').textContent = ct.title || ct.task_id;
      document.getElementById('ct-agent').textContent = ct.agent_id || '';
      document.getElementById('ct-model').textContent = ct.model_name || '';
      var pct = ct.progress_pct || 0;
      document.getElementById('ct-progress-fill').style.width = pct + '%';
      document.getElementById('ct-progress-text').textContent = pct + '% ' + (ct.steps_done || 0) + '/' + (ct.steps_total || 0);
    } else {
      panel.style.display = 'none';
    }
  }

  /* ── Consolidated fetch via /api/system/state (2.3) ─────── */
  async function refresh() {
    try {
      const r = await fetch('/api/system/state');
      const d = await r.json();

      if (gpuRamAvailability) gpuRamAvailability.textContent = formatPercent(d.gpu_ram_used_pct);
      if (tokensCount) tokensCount.textContent = d.tokens_session != null ? d.tokens_session : '0';
      if (sessionCost) sessionCost.textContent = formatSessionCost(d.cost_session, d.cost_currency);
      if (gpuUsage) gpuUsage.textContent = formatPercent(d.gpu_usage_pct);
      if (queueCount) queueCount.textContent = d.queue_length != null ? d.queue_length : '0';
      renderCurrentTask(d.current_task || null);
    } catch {
      // Fallback: individual fetches
      try { var td = await (await fetch('/api/tasks/stats')).json(); if (queueCount) queueCount.textContent = td.pending != null ? td.pending : 0; } catch {}
    }

    // Agent list still needs full details
    try {
      var ad = await (await fetch('/api/agents')).json();
      var mergedAgents = withSupervisorAgents(ad.agents || []);
      renderAgents(mergedAgents);
      populateTargetSelect(mergedAgents);
    } catch {}

    // Current task panel (S3/S6)
    try {
      if (!document.getElementById('ct-task-title').textContent) {
        var ct = await (await fetch('/api/system/current-task')).json();
        renderCurrentTask(ct);
      }
    } catch (e) { /* ignore */ }
  }

  /* ── Populate target-agent dropdown ─────────────────────── */
  function populateTargetSelect(agents) {
    if (!inputTarget) return;
    var nextSnapshot = JSON.stringify((agents || []).map(function (a) {
      return [a.agent_id || '', a.name || a.agent_id || ''];
    }));
    if (nextSnapshot === lastTargetSnapshot) {
      return;
    }
    lastTargetSnapshot = nextSnapshot;
    // Keep first option (all)
    while (inputTarget.options.length > 1) {
      inputTarget.remove(1);
    }
    agents.forEach(function (a) {
      var opt = document.createElement('option');
      opt.value = a.agent_id;
      opt.textContent = a.name || a.agent_id;
      inputTarget.appendChild(opt);
    });
  }

  function renderCommandCatalog(commands) {
    var visibleCommands = (commands || []).filter(function (item) {
      return (item.web_support || 'insert') !== 'unsupported';
    });
    if (!visibleCommands.length) {
      return '<div class="supervisor-drawer-content"><p class="text-muted">No operator commands available</p></div>';
    }
    var items = visibleCommands.map(function (item) {
      return (
        '<button type="button" class="drawer-item supervisor-command-item" data-supervisor-command="' + escapeAttr(item.command) + '" onclick="window.supervisorInsertCommand && window.supervisorInsertCommand(this.dataset.supervisorCommand)">' +
          '<div class="drawer-item__content supervisor-command-item__content">' +
            '<strong>' + escapeHtml(item.command) + '</strong>' +
            '<div class="text-muted">' + escapeHtml(item.description) + '</div>' +
          '</div>' +
        '</button>'
      );
    }).join('');
    return (
      '<div class="supervisor-drawer-content supervisor-drawer-content--commands">' +
        '<div class="supervisor-drawer-toolbar">' +
          '<strong>' + escapeHtml(window.t('supervisor.system_commands', 'System Commands')) + '</strong>' +
          '<button type="button" class="glass-btn glass-btn--ghost glass-btn--sm" data-supervisor-drawer-toggle="commands">' + escapeHtml(window.t('supervisor.drawer_expand', 'Expand')) + '</button>' +
        '</div>' +
        '<div class="supervisor-command-list">' + items + '</div>' +
      '</div>'
    );
  }

  function renderSpawnAgentDrawer() {
    return (
      '<div class="supervisor-drawer-content supervisor-drawer-content--spawn">' +
        '<div class="supervisor-drawer-toolbar">' +
          '<strong>' + escapeHtml(window.t('supervisor.spawn_agent', 'Add Agent')) + '</strong>' +
          '<button type="button" class="glass-btn glass-btn--ghost glass-btn--sm" data-supervisor-drawer-toggle="spawn">' + escapeHtml(window.t('supervisor.drawer_expand', 'Expand')) + '</button>' +
        '</div>' +
        '<form class="spawn-form" id="spawn-agent-drawer-form">' +
          '<div class="spawn-form-row">' +
            '<input type="text" id="spawn-drawer-name" class="operator-input-field" placeholder="' + escapeAttr(window.t('supervisor.spawn_name', 'Agent name')) + '" required />' +
            '<select id="spawn-drawer-role" class="operator-input-select" aria-label="Agent role">' +
              '<option value="executor">executor</option>' +
              '<option value="reviewer">reviewer</option>' +
              '<option value="researcher">researcher</option>' +
              '<option value="planner">planner</option>' +
            '</select>' +
          '</div>' +
          '<div class="spawn-form-row">' +
            '<button type="submit" class="glass-btn glass-btn--success">' + escapeHtml(window.t('supervisor.spawn', 'Spawn')) + '</button>' +
          '</div>' +
          '<div class="spawn-status" id="spawn-drawer-status"></div>' +
        '</form>' +
      '</div>'
    );
  }

  function submitSpawnAgent(name, role, statusElement) {
    var normalizedName = String(name || '').trim();
    if (!normalizedName) {
      if (statusElement) {
        statusElement.textContent = 'Error: name required';
        statusElement.className = 'spawn-status spawn-status--err';
      }
      return Promise.resolve();
    }

    if (statusElement) {
      statusElement.textContent = '…';
      statusElement.className = 'spawn-status';
    }

    return fetch('/api/agents/spawn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: normalizedName,
        role: role || 'executor',
      }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
      .then(function (result) {
        var d = result.body || {};
        if (result.ok && d.ok) {
          if (statusElement) {
            statusElement.textContent = 'Spawned: ' + d.agent_id;
            statusElement.className = 'spawn-status spawn-status--ok';
          }
          if (liveStream && liveStream.append) {
            liveStream.append('Agent spawned: ' + d.agent_id, 'info', 'user', {
              type: 'agent.spawn.manual',
              source_label: getCurrentUserLabel(),
              target_agent: d.agent_id,
              target_scope: 'agent',
            });
          }
          refresh();
          if (typeof closeDetailDrawer === 'function') {
            closeDetailDrawer();
          }
          return;
        }
        if (statusElement) {
          statusElement.textContent = 'Error: ' + (d.error || 'failed');
          statusElement.className = 'spawn-status spawn-status--err';
        }
      })
      .catch(function () {
        if (statusElement) {
          statusElement.textContent = 'Error: network error';
          statusElement.className = 'spawn-status spawn-status--err';
        }
      });
  }

  function executeSlashCommand(command) {
    return fetch('/api/system/commands/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: command })
    }).then(function (r) {
      return r.json().then(function (body) {
        return { ok: r.ok, body: body };
      });
    });
  }

  /* ── Render agents ──────────────────────────────────────── */
  function renderAgents(agents) {
    pruneAgentThreadBuffers(agents || []);
    syncAgentPanelState(agents || []);
    var nextSnapshot = JSON.stringify((agents || []).map(function (a) {
      return {
        agent_id: a.agent_id || '',
        name: a.name || '',
        state: a.state || '',
        role: a.role || '',
        model_name: a.model_name || '',
      };
    }));
    if (nextSnapshot === lastAgentsSnapshot) {
      return;
    }
    lastAgentsSnapshot = nextSnapshot;

    if (!agents.length) {
      agentList.innerHTML = '<p class="text-muted">No active agents</p>';
      return;
    }

    agentList.innerHTML = agents.map(function (a) {
      var sc = stateClass(a.state);
      var isPaused = a.state && a.state.toLowerCase() === 'paused';
      var isTerminated = a.state && a.state.toLowerCase() === 'terminated';
      var hasModel = !!(a.model_name && String(a.model_name).trim());
      var isVirtual = !!a.is_virtual;
      var setupUrl = buildAgentSetupUrl(a);
      var threadEntries = ensureAgentThreadBuffer(a.agent_id);
      var lastActivity = formatLastActivityLabel(agentLastActivity.get(String(a.agent_id || '').trim()));
      var modelMarkup = hasModel
        ? '<span class="agent-model-link meta-tag"' + (isVirtual ? '' : ' data-agent-setup-url="' + escapeAttr(setupUrl) + '" title="' + escapeAttr(window.t('supervisor.open_agent_setup', 'Open agent setup')) + '"') + '>' + escapeHtml(a.model_name) + '</span>'
        : '<span class="agent-model-link agent-model-link--warning"' + (isVirtual ? '' : ' data-agent-setup-url="' + escapeAttr(setupUrl) + '" title="' + escapeAttr(window.t('supervisor.assign_model_hint', 'Assign a model to the agent')) + '"') + '><span class="agent-model-warning-icon" aria-hidden="true">⚠</span><span>' + escapeHtml(window.t('supervisor.assign_model_required', 'Assign a model to the agent!')) + '</span></span>';
      var editButton = isVirtual
        ? ''
        : renderAgentIconButton({
            icon: 'edit',
            label: window.t('common.edit', 'Edit'),
            setupUrl: setupUrl,
          });
      var lifecycleButton = isVirtual
        ? ''
        : (isPaused
          ? renderAgentIconButton({
              icon: 'resume',
              label: window.t('workflows.resume', 'Resume'),
              dataAction: 'resume',
              dataId: a.agent_id,
            })
          : renderAgentIconButton({
              icon: 'pause',
              label: window.t('supervisor.pause_agent', 'Pause agent'),
              dataAction: 'pause',
              dataId: a.agent_id,
            }));
      var terminateButton = (isVirtual || isTerminated)
        ? ''
        : renderAgentIconButton({
            icon: 'stop',
            label: window.t('supervisor.stop_agent', 'Stop agent'),
            dataAction: 'terminate',
            dataId: a.agent_id,
            className: 'agent-control-action--danger',
          });
      return (
        '<div class="agent-control-row' + (hasModel ? '' : ' agent-control-row--warning') + '" data-agent="' + escapeAttr(a.agent_id) + '">' +
          '<div class="agent-control-row__header">' +
            '<div class="agent-control-row__summary' + (isVirtual ? '' : ' agent-control-row--link') + '"' + (isVirtual ? '' : ' data-agent-setup-url="' + escapeAttr(setupUrl) + '" role="link" tabindex="0" aria-label="' + escapeAttr(window.t('supervisor.open_agent_setup', 'Open agent setup')) + '"') + '>' +
              '<span class="agent-name agent-name--link"' + (isVirtual ? '' : ' data-agent-setup-url="' + escapeAttr(setupUrl) + '"') + '>' + (a.name || a.agent_id) + '</span>' +
              '<span class="agent-state ' + sc + '" title="' + escapeAttr(stateLabel(a.state)) + '" aria-label="' + escapeAttr(stateLabel(a.state)) + '">' + stateIconMarkup(a.state) + '</span>' +
              '<span class="text-muted" style="font-size:0.7rem">' + (a.role || '') + '</span>' +
              modelMarkup +
              '<span class="meta-tag agent-thread-count" id="' + agentThreadCountId(a.agent_id) + '">' + threadEntries.length + ' msg</span>' +
              '<span class="meta-tag agent-last-activity" id="' + agentLastActivityId(a.agent_id) + '">' + escapeHtml(lastActivity) + '</span>' +
              '<span class="meta-tag agent-thread-retention" id="' + agentRetentionId(a.agent_id) + '" hidden></span>' +
            '</div>' +
            '<div class="agent-control-actions">' +
              editButton +
              lifecycleButton +
              terminateButton +
            '</div>' +
          '</div>' +
          '<section class="agent-thread-screen" aria-label="Agent communication screen">' +
            '<div class="agent-thread-screen__list" id="' + agentThreadScreenId(a.agent_id) + '"></div>' +
          '</section>' +
        '</div>'
      );
    }).join('');

    agents.forEach(function (agent) {
      renderAgentThread(agent.agent_id);
    });
  }

  /* ── Lifecycle actions ──────────────────────────────────── */
  agentList.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-action]');
    if (btn) {
      var action = btn.dataset.action;
      var id = btn.dataset.id;
      runSingleFlight('agent:' + id + ':' + action, btn, function () {
        return fetch('/api/agents/' + id + '/' + action, { method: 'POST' })
          .then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, body: d }; });
          })
          .then(function (d) {
            if (d.ok && d.body.ok) {
              if (liveStream && liveStream.append) {
                liveStream.append('Agent ' + id + ' → ' + action, 'info', 'user', {
                  type: 'agent.lifecycle.manual',
                  source_label: getCurrentUserLabel(),
                  target_agent: id,
                  target_scope: 'agent',
                });
              }
              refresh();
            } else {
              if (liveStream && liveStream.append) {
                liveStream.append(((d.body && d.body.error) || 'failed'), 'error', 'user', {
                  type: 'agent.lifecycle.manual.failed',
                  source_label: getCurrentUserLabel(),
                  target_agent: id,
                  target_scope: 'agent',
                });
              }
            }
          })
          .catch(function () {
            if (liveStream && liveStream.append) {
              liveStream.append('network error', 'error', 'user', {
                type: 'agent.lifecycle.manual.failed',
                source_label: getCurrentUserLabel(),
                target_agent: id,
                target_scope: 'agent',
              });
            }
          });
      });
      return;
    }

    var setupTarget = e.target.closest('[data-agent-setup-url]');
    if (!setupTarget) return;
    var setupUrl = setupTarget.getAttribute('data-agent-setup-url');
    if (setupUrl) {
      window.location.href = setupUrl;
    }
  });

  agentList.addEventListener('keydown', function (e) {
    var row = e.target.closest('.agent-control-row--link[data-agent-setup-url]');
    if (!row) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    var setupUrl = row.getAttribute('data-agent-setup-url');
    if (setupUrl) {
      window.location.href = setupUrl;
    }
  });

  /* ── Operator input form (2.4) ──────────────────────────── */
  if (inputForm) {
    inputForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var message = inputText.value.trim();
      if (!message) return;
      rememberInputHistory(message);
      var normalizedMessage = message.toLowerCase();

      if (normalizedMessage === 'help' || normalizedMessage === '/help') {
        openCommandCatalog();
        inputStatus.textContent = 'Command list ready';
        inputStatus.className = 'operator-input-status operator-input-status--ok';
        inputText.value = '';
        resetInputHistoryCursor();
        return;
      }

      var target = inputTarget ? inputTarget.value : '';
      var submitButton = inputForm.querySelector('button[type="submit"]');
      runSingleFlight('operator-input', [submitButton, inputText, inputTarget], function () {
        if (message.startsWith('/')) {
          inputStatus.textContent = '…';
          return executeSlashCommand(message)
            .then(function (res) {
              if (res.ok && res.body.ok) {
                inputStatus.textContent = 'Command executed';
                inputStatus.className = 'operator-input-status operator-input-status--ok';
                inputText.value = '';
                if (commandToCatalog(message)) {
                  openCommandCatalog();
                } else if (res.body.output) {
                  normalizeCommandOutputLines(res.body.output).forEach(function (line) {
                    appendLogStyleStreamEntry(getCurrentUserLabel(), line, 'info', 'user', {
                      type: 'operator.command.output',
                      command: message,
                    });
                  });
                }
                refresh();
              } else {
                inputStatus.textContent = 'Error: ' + ((res.body && res.body.error) || 'command failed');
                inputStatus.className = 'operator-input-status operator-input-status--err';
                appendLogStyleStreamEntry(getCurrentUserLabel(), (res.body && res.body.error) || 'command failed', 'error', 'user', {
                  type: 'operator.command.failed',
                  command: message,
                });
                if (typeof showToast === 'function') showToast((res.body && res.body.error) || 'Command failed', 'error');
              }
            })
            .catch(function () {
              inputStatus.textContent = 'Error: network error';
              inputStatus.className = 'operator-input-status operator-input-status--err';
              appendLogStyleStreamEntry(getCurrentUserLabel(), 'network error', 'error', 'user', {
                type: 'operator.command.failed',
                command: message,
              });
            });
        }

        var body = { message: message };
        if (target) body.target_agent = target;

        inputStatus.textContent = '…';
        return fetch('/api/system/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.ok) {
              inputStatus.textContent = d.response || 'Sent';
              inputStatus.className = 'operator-input-status operator-input-status--ok';
              inputText.value = '';
              resetInputHistoryCursor();
            } else {
              inputStatus.textContent = 'Error: ' + (d.error || 'failed');
              inputStatus.className = 'operator-input-status operator-input-status--err';
            }
          })
          .catch(function () {
            inputStatus.textContent = 'Error: network error';
            inputStatus.className = 'operator-input-status operator-input-status--err';
          });
      });
    });
  }

  /* ── Spawn agent via drawer (2.11, 2.12) ────────────────── */
  if (btnAddAgent) {
    btnAddAgent.addEventListener('click', function () {
      renderExpandableDrawer(window.t('supervisor.spawn_agent', 'Add Agent'), renderSpawnAgentDrawer());
    });
  }

  if (drawerBody) {
    drawerBody.addEventListener('submit', function (e) {
      if (!e.target || e.target.id !== 'spawn-agent-drawer-form') return;
      e.preventDefault();
      var nameInput = document.getElementById('spawn-drawer-name');
      var roleInput = document.getElementById('spawn-drawer-role');
      var submitButton = e.target.querySelector('button[type="submit"]');
      var statusEl = document.getElementById('spawn-drawer-status');
      runSingleFlight('spawn-agent', [submitButton, nameInput, roleInput], function () {
        return submitSpawnAgent(nameInput ? nameInput.value : '', roleInput ? roleInput.value : 'executor', statusEl);
      });
    });
  }

  /* ── Listen to <live-stream> events for auto-refresh ────── */
  if (liveStream) {
    liveStream.addEventListener('stream-event', function (e) {
      var detail = e.detail || null;
      var type = detail && detail.type;
      routeAgentThreadEvent(detail);
      if (!detail || detail.replayed) {
        return;
      }
      if (type && (type.startsWith('agent.') || type.startsWith('inbox.'))) {
        refresh();
      }
    });
    liveStream.addEventListener('stream-connection', function (e) {
      setStreamConnectionState(e.detail || null);
    });
    liveStream.addEventListener('stream-config', function (e) {
      var detail = e.detail || {};
      streamSessionId = String(detail.session_id || '').trim();
      streamActiveAgents = Array.isArray(detail.active_agents) ? detail.active_agents.slice() : [];
      setStreamConnectionState({ status: 'connected' });
      refresh();
    });
  }

  /* ── Current task actions (S3) ──────────────────────────── */
  function bindCurrentTaskAction(buttonId, endpoint, successMessage, successType, failureMessage) {
    var button = document.getElementById(buttonId);
    if (!button) return;
    button.addEventListener('click', function () {
      runSingleFlight('current-task:' + endpoint, button, function () {
        return fetch('/api/system/current-task/' + endpoint, { method: 'POST' })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
          .then(function (res) {
            if (res.ok && res.body.ok) {
              if (liveStream && liveStream.append) {
                liveStream.append(successMessage, 'warn', 'user', { type: successType, source_label: getCurrentUserLabel() });
              }
              refresh();
            }
            else if (typeof showToast === 'function') showToast((res.body && res.body.error) || failureMessage, 'error');
          })
          .catch(function () { if (typeof showToast === 'function') showToast(failureMessage, 'error'); });
      });
    });
  }

  bindCurrentTaskAction('ct-btn-pause', 'pause', 'Current task paused', 'system.current_task.paused', 'Pause failed');
  bindCurrentTaskAction('ct-btn-stop', 'stop', 'Current task stopped', 'system.current_task.stopped', 'Stop failed');
  bindCurrentTaskAction('ct-btn-retry', 'retry', 'Current task retried', 'system.current_task.retried', 'Retry failed');

  /* ── Action bar handlers (S5) ───────────────────────────── */
  if (btnCommands) {
    btnCommands.addEventListener('click', function () {
      openCommandCatalog();
    });
  }

  if (btnHistory) {
    btnHistory.addEventListener('click', function () {
      renderExpandableDrawer(window.t('supervisor.history', 'Command History'), renderHistoryCatalog());
    });
  }

  streamChannelFilter?.addEventListener('change', syncStreamFilter);
  streamLevelFilter?.addEventListener('change', syncStreamFilter);
  btnStreamClear?.addEventListener('click', function () {
    if (liveStream && typeof liveStream.clearEntries === 'function') {
      liveStream.clearEntries();
    }
    clearAgentThreads();
  });

  var btnReset = document.getElementById('btn-sv-reset');
  if (btnReset) {
    btnReset.addEventListener('click', function () {
      if (!confirm('Reset session? This will clear budget counters and error counts.')) return;
      runSingleFlight('session-reset', btnReset, function () {
        return fetch('/api/system/reset', { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.ok) {
              if (typeof showToast === 'function') showToast('Session reset', 'success');
              if (liveStream && liveStream.append) {
                liveStream.append('Session reset', 'info', 'user', { type: 'system.reset', source_label: getCurrentUserLabel() });
              }
              refresh();
            } else {
              if (typeof showToast === 'function') showToast('Reset failed', 'error');
            }
          })
          .catch(function () {
            if (typeof showToast === 'function') showToast('Network error', 'error');
          });
      });
    });
  }

  function commandToCatalog(command) {
    var normalized = String(command || '').trim().toLowerCase();
    return normalized === '/help' || normalized === 'help';
  }

  function openCommandCatalog() {
    fetch('/api/system/commands')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderExpandableDrawer(window.t('supervisor.system_commands', 'System Commands'), renderCommandCatalog(data.commands || []));
      })
      .catch(function () {
        if (typeof showToast === 'function') showToast('Could not load operator commands', 'error');
      });
  }

  if (inputText) {
    inputText.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveThroughInputHistory(-1);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveThroughInputHistory(1);
      }
    });
    inputText.addEventListener('input', resetInputHistoryCursor);
  }

  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) {
      return;
    }

    if (e.key === '/') {
      if (isEditableTarget(e.target)) {
        return;
      }
      e.preventDefault();
      focusOperatorInput();
      return;
    }

    if (e.key === 'Escape') {
      if (document.activeElement === inputText) {
        inputText.blur();
      }
      return;
    }

    if (isEditableTarget(e.target)) {
      return;
    }

    if (e.key === 'j') {
      e.preventDefault();
      focusAgentSummaryByOffset(1);
      return;
    }

    if (e.key === 'k') {
      e.preventDefault();
      focusAgentSummaryByOffset(-1);
    }
  });

  if (detailDrawer) {
    detailDrawer.addEventListener('touchstart', function (e) {
      if (!detailDrawer.classList.contains('detail-drawer--supervisor')) return;
      var touch = e.changedTouches && e.changedTouches[0];
      if (!touch) return;
      drawerSwipeStartX = touch.clientX;
      drawerSwipeStartY = touch.clientY;
    }, { passive: true });
    detailDrawer.addEventListener('touchend', function (e) {
      if (!detailDrawer.classList.contains('detail-drawer--supervisor')) return;
      var touch = e.changedTouches && e.changedTouches[0];
      if (!touch || drawerSwipeStartX == null || drawerSwipeStartY == null) return;
      var dx = touch.clientX - drawerSwipeStartX;
      var dy = touch.clientY - drawerSwipeStartY;
      drawerSwipeStartX = null;
      drawerSwipeStartY = null;
      if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy)) return;
      if (dx < 0) setDrawerWide(true);
      if (dx > 0) setDrawerWide(false);
    }, { passive: true });
  }

  /* ── Init ───────────────────────────────────────────────── */
  btnRefresh.addEventListener('click', refresh);
  syncStreamFilter();
  resetInputHistoryCursor();
  refresh();
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      refresh();
    }
  });
  setInterval(function () {
    if (document.visibilityState !== 'visible') {
      return;
    }
    refresh();
  }, REFRESH_INTERVAL_MS);
})();
