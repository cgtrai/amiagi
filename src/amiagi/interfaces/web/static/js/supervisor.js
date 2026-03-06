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
  const agentList     = document.getElementById('agent-control-list');
  const liveStream    = document.getElementById('supervisor-live-stream');
  const agentsCount   = document.getElementById('sv-agents-count');
  const tasksCount    = document.getElementById('sv-tasks-count');
  const inboxCount    = document.getElementById('sv-inbox-count');
  const uptimeEl      = document.getElementById('sv-uptime');
  const btnRefresh    = document.getElementById('btn-sv-refresh');
  const inputForm     = document.getElementById('operator-input-form');
  const inputText     = document.getElementById('operator-input-text');
  const inputTarget   = document.getElementById('operator-input-target');
  const inputStatus   = document.getElementById('operator-input-status');
  const spawnForm     = document.getElementById('spawn-agent-form');
  const spawnName     = document.getElementById('spawn-name');
  const spawnRole     = document.getElementById('spawn-role');
  const spawnStatus   = document.getElementById('spawn-status');

  /* ── Helpers ────────────────────────────────────────────── */
  function stateClass(state) {
    const s = (state || '').toLowerCase();
    if (s === 'idle') return 'agent-state--idle';
    if (s === 'working') return 'agent-state--working';
    if (s === 'paused') return 'agent-state--paused';
    if (s === 'error' || s === 'terminated') return 'agent-state--error';
    return '';
  }

  /* ── Consolidated fetch via /api/system/state (2.3) ─────── */
  async function refresh() {
    try {
      const r = await fetch('/api/system/state');
      const d = await r.json();

      // Agents
      if (d.agents) {
        agentsCount.textContent = d.agents.total;
      }
      // Tasks
      if (d.tasks) {
        tasksCount.textContent = d.tasks.pending;
      }
      // Inbox
      if (d.inbox) {
        inboxCount.textContent = d.inbox.pending;
      }
      // Uptime
      if (d.uptime) {
        uptimeEl.textContent = d.uptime;
      }
    } catch {
      // Fallback: individual fetches
      try { var ad = await (await fetch('/api/agents')).json(); agentsCount.textContent = ad.total; } catch {}
      try { var ic = await (await fetch('/api/inbox/count')).json(); inboxCount.textContent = ic.pending; } catch {}
    }

    // Agent list still needs full details
    try {
      var ad = await (await fetch('/api/agents')).json();
      renderAgents(ad.agents || []);
      populateTargetSelect(ad.agents || []);
    } catch {}
  }

  /* ── Populate target-agent dropdown ─────────────────────── */
  function populateTargetSelect(agents) {
    if (!inputTarget) return;
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

  /* ── Render agents ──────────────────────────────────────── */
  function renderAgents(agents) {
    if (!agents.length) {
      agentList.innerHTML = '<p class="text-muted">No active agents</p>';
      return;
    }

    agentList.innerHTML = agents.map(function (a) {
      var sc = stateClass(a.state);
      var isPaused = a.state && a.state.toLowerCase() === 'paused';
      var isTerminated = a.state && a.state.toLowerCase() === 'terminated';
      return (
        '<div class="agent-control-row" data-agent="' + a.agent_id + '">' +
          '<span class="agent-name">' + (a.name || a.agent_id) + '</span>' +
          '<span class="agent-state ' + sc + '">' + (a.state || '—') + '</span>' +
          '<span class="text-muted" style="font-size:0.7rem">' + (a.role || '') + '</span>' +
          '<div class="agent-control-actions">' +
            (isPaused
              ? '<button class="glass-btn" data-action="resume" data-id="' + a.agent_id + '">▶ Resume</button>'
              : '<button class="glass-btn" data-action="pause" data-id="' + a.agent_id + '">⏸ Pause</button>'
            ) +
            (isTerminated
              ? ''
              : '<button class="glass-btn glass-btn--danger" data-action="terminate" data-id="' + a.agent_id + '">⏹ Stop</button>'
            ) +
          '</div>' +
        '</div>'
      );
    }).join('');
  }

  /* ── Lifecycle actions ──────────────────────────────────── */
  agentList.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-action]');
    if (!btn) return;
    var action = btn.dataset.action;
    var id = btn.dataset.id;
    fetch('/api/agents/' + id + '/' + action, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          if (liveStream && liveStream.append) liveStream.append('✓ Agent ' + id + ' → ' + action);
          refresh();
        } else {
          if (liveStream && liveStream.append) liveStream.append('✗ ' + (d.error || 'failed'), 'error');
        }
      })
      .catch(function () {
        if (liveStream && liveStream.append) liveStream.append('✗ network error', 'error');
      });
  });

  /* ── Operator input form (2.4) ──────────────────────────── */
  if (inputForm) {
    inputForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var message = inputText.value.trim();
      if (!message) return;

      var target = inputTarget ? inputTarget.value : '';
      var body = { message: message };
      if (target) body.target_agent = target;

      inputStatus.textContent = '…';
      fetch('/api/system/input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            inputStatus.textContent = '✓ ' + (d.response || 'sent');
            inputStatus.className = 'operator-input-status operator-input-status--ok';
            inputText.value = '';
            if (liveStream && liveStream.append) liveStream.append('⌨ operator: ' + message);
          } else {
            inputStatus.textContent = '✗ ' + (d.error || 'failed');
            inputStatus.className = 'operator-input-status operator-input-status--err';
          }
        })
        .catch(function () {
          inputStatus.textContent = '✗ network error';
          inputStatus.className = 'operator-input-status operator-input-status--err';
        });
    });
  }

  /* ── Spawn agent form (2.11, 2.12) ─────────────────────── */
  if (spawnForm) {
    spawnForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var name = spawnName.value.trim();
      if (!name) return;

      spawnStatus.textContent = '…';
      fetch('/api/agents/spawn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          role: spawnRole.value,
        }),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            spawnStatus.textContent = '✓ Spawned: ' + d.agent_id;
            spawnStatus.className = 'spawn-status spawn-status--ok';
            spawnName.value = '';
            if (liveStream && liveStream.append) liveStream.append('+ Agent spawned: ' + d.agent_id);
            refresh();
          } else {
            spawnStatus.textContent = '✗ ' + (d.error || 'failed');
            spawnStatus.className = 'spawn-status spawn-status--err';
          }
        })
        .catch(function () {
          spawnStatus.textContent = '✗ network error';
          spawnStatus.className = 'spawn-status spawn-status--err';
        });
    });
  }

  /* ── Listen to <live-stream> events for auto-refresh ────── */
  if (liveStream) {
    liveStream.addEventListener('stream-event', function (e) {
      var type = e.detail && e.detail.type;
      if (type && (type.startsWith('agent.') || type.startsWith('inbox.'))) {
        refresh();
      }
    });
  }

  /* ── Init ───────────────────────────────────────────────── */
  btnRefresh.addEventListener('click', refresh);
  refresh();
  setInterval(refresh, 15000);
})();
