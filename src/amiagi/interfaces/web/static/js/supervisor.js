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
  const cycleCount    = document.getElementById('sv-cycle-count');
  const tokensCount   = document.getElementById('sv-tokens-count');
  const sessionCost   = document.getElementById('sv-session-cost');
  const errorsCount   = document.getElementById('sv-errors-count');
  const queueCount    = document.getElementById('sv-queue-count');
  const btnRefresh    = document.getElementById('btn-sv-refresh');
  const btnCommands   = document.getElementById('btn-sv-commands');
  const streamSource  = document.getElementById('sv-stream-source');
  const streamTarget  = document.getElementById('sv-stream-target');
  const streamEvent   = document.getElementById('sv-stream-event');
  const streamIssue   = document.getElementById('sv-stream-issue');
  const streamChannelFilter = document.getElementById('sv-stream-channel-filter');
  const streamLevelFilter = document.getElementById('sv-stream-level-filter');
  const btnStreamClear = document.getElementById('btn-sv-stream-clear');
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

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function insertSupervisorCommand(command) {
    if (!inputText) return;
    const trimmed = String(command || '').trim();
    if (!trimmed) return;
    const prefix = inputText.value && !inputText.value.endsWith(' ') ? ' ' : '';
    inputText.value = (inputText.value || '') + prefix + trimmed;
    inputText.focus();
    inputText.setSelectionRange(inputText.value.length, inputText.value.length);
    if (inputStatus) {
      inputStatus.textContent = 'Command ready';
      inputStatus.className = 'operator-input-status operator-input-status--ok';
    }
  }

  function syncStreamFilter() {
    if (!liveStream || typeof liveStream.setFilter !== 'function') return;
    liveStream.setFilter({
      channel: streamChannelFilter ? streamChannelFilter.value : 'all',
      level: streamLevelFilter ? streamLevelFilter.value : 'all',
    });
  }

  function updateStreamSummary(detail) {
    if (!detail) return;
    var source = detail.source_label || detail.agent_id || detail.actor || '—';
    var target = detail.target_agent || (detail.target_scope === 'broadcast' ? 'all' : '—');
    var type = detail.type || '—';
    var issue = (type === 'error' || /failed$/i.test(type || ''))
      ? (detail.message || detail.summary || type)
      : '—';
    if (streamSource) streamSource.textContent = 'Source: ' + source;
    if (streamTarget) streamTarget.textContent = 'Target: ' + target;
    if (streamEvent) streamEvent.textContent = 'Event: ' + type;
    if (streamIssue) streamIssue.textContent = 'Issue: ' + issue;
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

      if (cycleCount) cycleCount.textContent = d.cycle != null ? d.cycle : '0';
      if (tokensCount) tokensCount.textContent = d.tokens_session != null ? d.tokens_session : '0';
      if (sessionCost) sessionCost.textContent = d.cost_session != null ? '$' + Number(d.cost_session || 0).toFixed(2) : '$0.00';
      if (errorsCount) errorsCount.textContent = d.error_count != null ? d.error_count : '0';
      if (queueCount) queueCount.textContent = d.queue_length != null ? d.queue_length : '0';
      renderCurrentTask(d.current_task || null);
    } catch {
      // Fallback: individual fetches
      try { var td = await (await fetch('/api/tasks/stats')).json(); if (queueCount) queueCount.textContent = td.pending != null ? td.pending : 0; } catch {}
    }

    // Agent list still needs full details
    try {
      var ad = await (await fetch('/api/agents')).json();
      renderAgents(ad.agents || []);
      populateTargetSelect(ad.agents || []);
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
    if (!commands.length) {
      return '<p class="text-muted">No operator commands available</p>';
    }
    return commands.map(function (item) {
      var support = item.web_support || 'insert';
      var note = item.web_note ? '<div class="text-muted">' + escapeHtml(item.web_note) + '</div>' : '';
      var supportBadge = '<span class="meta-tag">' + escapeHtml(support) + '</span>';
      var actionButton = support === 'run'
        ? '<button class="glass-btn glass-btn--primary" data-supervisor-command-run="' + escapeHtml(item.command) + '">Run</button>'
        : '<button class="glass-btn" data-supervisor-command="' + escapeHtml(item.command) + '">Insert</button>';
      return (
        '<div class="drawer-item supervisor-command-item">' +
          '<div class="drawer-item__content">' +
            '<strong>' + escapeHtml(item.command) + '</strong>' +
            '<div class="text-muted">' + escapeHtml(item.description) + '</div>' +
            note +
            supportBadge +
          '</div>' +
          actionButton +
        '</div>'
      );
    }).join('');
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
          '<span class="meta-tag">' + (a.model_name || 'N/A') + '</span>' +
          '<div class="agent-control-actions">' +
            (isPaused
              ? '<button class="glass-btn" data-action="resume" data-id="' + a.agent_id + '">Resume</button>'
              : '<button class="glass-btn" data-action="pause" data-id="' + a.agent_id + '">Pause</button>'
            ) +
            (isTerminated
              ? ''
              : '<button class="glass-btn glass-btn--danger" data-action="terminate" data-id="' + a.agent_id + '">Stop</button>'
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
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, body: d }; });
      })
      .then(function (d) {
        if (d.ok && d.body.ok) {
          if (liveStream && liveStream.append) {
            liveStream.append('Agent ' + id + ' → ' + action, 'info', 'user', {
              type: 'agent.lifecycle.manual',
              source_label: 'Operator',
              target_agent: id,
              target_scope: 'agent',
            });
          }
          refresh();
        } else {
          if (liveStream && liveStream.append) {
            liveStream.append(((d.body && d.body.error) || 'failed'), 'error', 'user', {
              type: 'agent.lifecycle.manual.failed',
              source_label: 'Operator',
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
            source_label: 'Operator',
            target_agent: id,
            target_scope: 'agent',
          });
        }
      });
  });

  /* ── Operator input form (2.4) ──────────────────────────── */
  if (inputForm) {
    inputForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var message = inputText.value.trim();
      if (!message) return;

      var target = inputTarget ? inputTarget.value : '';
      if (message.startsWith('/')) {
        inputStatus.textContent = '…';
        executeSlashCommand(message)
          .then(function (res) {
            if (res.ok && res.body.ok) {
              inputStatus.textContent = 'Command executed';
              inputStatus.className = 'operator-input-status operator-input-status--ok';
              inputText.value = '';
              if (liveStream && liveStream.append) {
                liveStream.append('Command executed: ' + message, 'info', 'user', {
                  type: 'operator.command.executed',
                  source_label: 'Operator',
                  summary: 'Operator command executed: ' + message,
                });
              }
              if (res.body.output && typeof openDetailDrawer === 'function') {
                openDetailDrawer('Command Output', '<pre class="drawer-pre">' + escapeHtml(res.body.output) + '</pre>');
              }
              refresh();
            } else {
              inputStatus.textContent = 'Error: ' + ((res.body && res.body.error) || 'command failed');
              inputStatus.className = 'operator-input-status operator-input-status--err';
              if (typeof showToast === 'function') showToast((res.body && res.body.error) || 'Command failed', 'error');
            }
          })
          .catch(function () {
            inputStatus.textContent = 'Error: network error';
            inputStatus.className = 'operator-input-status operator-input-status--err';
          });
        return;
      }

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
            inputStatus.textContent = d.response || 'Sent';
            inputStatus.className = 'operator-input-status operator-input-status--ok';
            inputText.value = '';
            if (liveStream && liveStream.append) {
              var dispatch = d.dispatch || {};
              liveStream.append(dispatch.summary || ('Operator → all: ' + message), 'info', dispatch.channel || 'user', dispatch);
            }
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
            spawnStatus.textContent = 'Spawned: ' + d.agent_id;
            spawnStatus.className = 'spawn-status spawn-status--ok';
            spawnName.value = '';
            if (liveStream && liveStream.append) {
              liveStream.append('Agent spawned: ' + d.agent_id, 'info', 'user', {
                type: 'agent.spawn.manual',
                source_label: 'Operator',
                target_agent: d.agent_id,
                target_scope: 'agent',
              });
            }
            refresh();
          } else {
            spawnStatus.textContent = 'Error: ' + (d.error || 'failed');
            spawnStatus.className = 'spawn-status spawn-status--err';
          }
        })
        .catch(function () {
          spawnStatus.textContent = 'Error: network error';
          spawnStatus.className = 'spawn-status spawn-status--err';
        });
    });
  }

  /* ── Listen to <live-stream> events for auto-refresh ────── */
  if (liveStream) {
    liveStream.addEventListener('stream-event', function (e) {
      var type = e.detail && e.detail.type;
      updateStreamSummary(e.detail || null);
      if (type && (type.startsWith('agent.') || type.startsWith('inbox.'))) {
        refresh();
      }
    });
  }

  /* ── Current task actions (S3) ──────────────────────────── */
  document.getElementById('ct-btn-pause')?.addEventListener('click', function () {
    fetch('/api/system/current-task/pause', { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
      .then(function (res) {
        if (res.ok && res.body.ok) {
          if (liveStream && liveStream.append) {
            liveStream.append('Current task paused', 'warn', 'user', { type: 'system.current_task.paused', source_label: 'Operator' });
          }
          refresh();
        }
        else if (typeof showToast === 'function') showToast((res.body && res.body.error) || 'Pause failed', 'error');
      })
      .catch(function () { if (typeof showToast === 'function') showToast('Pause failed', 'error'); });
  });
  document.getElementById('ct-btn-stop')?.addEventListener('click', function () {
    fetch('/api/system/current-task/stop', { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
      .then(function (res) {
        if (res.ok && res.body.ok) {
          if (liveStream && liveStream.append) {
            liveStream.append('Current task stopped', 'warn', 'user', { type: 'system.current_task.stopped', source_label: 'Operator' });
          }
          refresh();
        }
        else if (typeof showToast === 'function') showToast((res.body && res.body.error) || 'Stop failed', 'error');
      })
      .catch(function () { if (typeof showToast === 'function') showToast('Stop failed', 'error'); });
  });
  document.getElementById('ct-btn-retry')?.addEventListener('click', function () {
    fetch('/api/system/current-task/retry', { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
      .then(function (res) {
        if (res.ok && res.body.ok) {
          if (liveStream && liveStream.append) {
            liveStream.append('Current task retried', 'warn', 'user', { type: 'system.current_task.retried', source_label: 'Operator' });
          }
          refresh();
        }
        else if (typeof showToast === 'function') showToast((res.body && res.body.error) || 'Retry failed', 'error');
      })
      .catch(function () { if (typeof showToast === 'function') showToast('Retry failed', 'error'); });
  });

  /* ── Action bar handlers (S5) ───────────────────────────── */
  var btnNewPrompt = document.getElementById('btn-sv-new-prompt');
  if (btnNewPrompt) {
    btnNewPrompt.addEventListener('click', function () {
      var el = document.getElementById('operator-input-text');
      if (el) el.focus();
    });
  }

  if (btnCommands) {
    btnCommands.addEventListener('click', function () {
      fetch('/api/system/commands')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (typeof openDetailDrawer === 'function') {
            openDetailDrawer('Operator Commands', renderCommandCatalog(data.commands || []));
          }
        })
        .catch(function () {
          if (typeof showToast === 'function') showToast('Could not load operator commands', 'error');
        });
    });
  }

  streamChannelFilter?.addEventListener('change', syncStreamFilter);
  streamLevelFilter?.addEventListener('change', syncStreamFilter);
  btnStreamClear?.addEventListener('click', function () {
    if (liveStream && typeof liveStream.clearEntries === 'function') {
      liveStream.clearEntries();
    }
    if (streamSource) streamSource.textContent = 'Source: —';
    if (streamTarget) streamTarget.textContent = 'Target: —';
    if (streamEvent) streamEvent.textContent = 'Event: —';
    if (streamIssue) streamIssue.textContent = 'Issue: —';
  });

  var btnQueue = document.getElementById('btn-sv-queue');
  if (btnQueue) {
    btnQueue.addEventListener('click', function () {
      fetch('/api/tasks?status=pending').then(function (r) { return r.json(); }).then(function (data) {
        var tasks = data.items || data.tasks || [];
        var html = tasks.map(function (t) {
          return '<div class="drawer-item"><strong>' + (t.title || t.task_id) + '</strong> — ' + (t.status || 'pending') + '</div>';
        }).join('');
        if (typeof openDetailDrawer === 'function') {
          openDetailDrawer('Task Queue', html || '<p class="text-muted">Queue is empty</p>');
        }
      }).catch(function () {});
    });
  }

  var btnReset = document.getElementById('btn-sv-reset');
  if (btnReset) {
    btnReset.addEventListener('click', function () {
      if (!confirm('Reset session? This will clear budget counters and error counts.')) return;
      fetch('/api/system/reset', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            if (typeof showToast === 'function') showToast('Session reset', 'success');
            if (liveStream && liveStream.append) {
              liveStream.append('Session reset', 'info', 'user', { type: 'system.reset', source_label: 'Operator' });
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
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-supervisor-command]');
    if (!btn) return;
    insertSupervisorCommand(btn.dataset.supervisorCommand || '');
  });
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-supervisor-command-run]');
    if (!btn) return;
    var command = btn.dataset.supervisorCommandRun || '';
    executeSlashCommand(command).then(function (res) {
      if (res.ok && res.body.ok) {
        if (typeof showToast === 'function') showToast('Command executed', 'success');
        if (liveStream && liveStream.append) {
          liveStream.append('Command executed: ' + command, 'info', 'user', {
            type: 'operator.command.executed',
            source_label: 'Operator',
            summary: 'Operator command executed: ' + command,
          });
        }
        if (res.body.output && typeof openDetailDrawer === 'function') {
          openDetailDrawer('Command Output', '<pre class="drawer-pre">' + escapeHtml(res.body.output) + '</pre>');
        }
        refresh();
      } else if (typeof showToast === 'function') {
        showToast((res.body && res.body.error) || 'Command failed', 'error');
      }
    }).catch(function () {
      if (typeof showToast === 'function') showToast('Command failed', 'error');
    });
  });

  /* ── Init ───────────────────────────────────────────────── */
  btnRefresh.addEventListener('click', refresh);
  syncStreamFilter();
  refresh();
  setInterval(refresh, 15000);
})();
