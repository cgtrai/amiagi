/* ================================================================
   Amiagi Keybindings & Command Palette
   ================================================================
   Shortcuts:
   - Ctrl+K / Cmd+K     → open command palette / global search
   - Ctrl+Shift+P       → command palette (commands mode)
   - Ctrl+Enter         → send prompt
   - Ctrl+1..9          → switch agent tabs
   - Esc                → close modal / sidebar
   - ?                  → help overlay (when no input focused)
   ================================================================ */

(function() {
  'use strict';

  /* ── Command registry ───────────────────────────────────── */

  var COMMANDS = [
    { id: 'search',          label: 'Global Search',           shortcut: 'Ctrl+K',       action: openSearch },
    { id: 'command-palette',  label: 'Command Palette',         shortcut: 'Ctrl+Shift+P', action: openCommandPalette },
    { id: 'send-prompt',     label: 'Send Prompt',             shortcut: 'Ctrl+Enter',   action: sendPrompt },
    { id: 'close-overlay',   label: 'Close Overlay / Sidebar', shortcut: 'Esc',          action: closeOverlay },
    { id: 'help',            label: 'Show Help',               shortcut: '?',            action: showHelp },
    { id: 'tab-1',           label: 'Switch to Agent Tab 1',   shortcut: 'Ctrl+1',       action: function() { switchTab(0); } },
    { id: 'tab-2',           label: 'Switch to Agent Tab 2',   shortcut: 'Ctrl+2',       action: function() { switchTab(1); } },
    { id: 'tab-3',           label: 'Switch to Agent Tab 3',   shortcut: 'Ctrl+3',       action: function() { switchTab(2); } },
    { id: 'tab-4',           label: 'Switch to Agent Tab 4',   shortcut: 'Ctrl+4',       action: function() { switchTab(3); } },
    { id: 'tab-5',           label: 'Switch to Agent Tab 5',   shortcut: 'Ctrl+5',       action: function() { switchTab(4); } },
    { id: 'nav-monitoring',  label: 'Go to Monitoring',        shortcut: '',             action: function() { switchSection && switchSection('monitoring'); } },
    { id: 'nav-teams',       label: 'Go to Teams',             shortcut: '',             action: function() { switchSection && switchSection('teams'); } },
    { id: 'nav-models',      label: 'Go to Models',            shortcut: '',             action: function() { switchSection && switchSection('models'); } },
  ];

  window.__amiagi_commands = COMMANDS;

  /* ── Palette overlay ────────────────────────────────────── */

  var paletteEl = null;
  var paletteInput = null;
  var paletteList = null;
  var paletteMode = 'commands';   // 'commands' | 'search'

  function ensurePalette() {
    if (paletteEl) return;
    paletteEl = document.createElement('div');
    paletteEl.className = 'command-palette-overlay';
    paletteEl.innerHTML =
      '<div class="command-palette">' +
        '<input class="command-palette-input" placeholder="Type a command or search…" autocomplete="off"/>' +
        '<ul class="command-palette-list"></ul>' +
      '</div>';
    document.body.appendChild(paletteEl);
    paletteInput = paletteEl.querySelector('.command-palette-input');
    paletteList = paletteEl.querySelector('.command-palette-list');

    paletteEl.addEventListener('click', function(e) {
      if (e.target === paletteEl) closePalette();
    });
    paletteInput.addEventListener('input', onPaletteInput);
    paletteInput.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closePalette();
      if (e.key === 'Enter') {
        var active = paletteList.querySelector('.active');
        if (active) active.click();
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        navigateList(e.key === 'ArrowDown' ? 1 : -1);
      }
    });
  }

  function openPalette(mode) {
    ensurePalette();
    paletteMode = mode || 'commands';
    paletteInput.placeholder = paletteMode === 'search'
      ? 'Search agents, tasks, files, prompts…'
      : 'Type a command…';
    paletteInput.value = '';
    paletteEl.style.display = 'flex';
    renderPaletteItems('');
    setTimeout(function() { paletteInput.focus(); }, 50);
  }

  function closePalette() {
    if (paletteEl) paletteEl.style.display = 'none';
  }

  function onPaletteInput() {
    var q = paletteInput.value.toLowerCase().trim();
    if (paletteMode === 'search' && q.length >= 2) {
      doGlobalSearch(q);
    } else {
      renderPaletteItems(q);
    }
  }

  function renderPaletteItems(filter) {
    var filtered = COMMANDS.filter(function(c) {
      return c.label.toLowerCase().indexOf(filter) !== -1;
    });
    paletteList.innerHTML = '';
    filtered.forEach(function(cmd, i) {
      var li = document.createElement('li');
      li.className = 'command-palette-item' + (i === 0 ? ' active' : '');
      li.textContent = cmd.label;
      if (cmd.shortcut) {
        var kbd = document.createElement('span');
        kbd.className = 'shortcut-hint';
        kbd.textContent = cmd.shortcut;
        li.appendChild(kbd);
      }
      li.addEventListener('click', function() {
        closePalette();
        cmd.action();
      });
      paletteList.appendChild(li);
    });
  }

  function navigateList(dir) {
    var items = paletteList.querySelectorAll('.command-palette-item');
    if (!items.length) return;
    var activeIdx = -1;
    items.forEach(function(el, i) { if (el.classList.contains('active')) activeIdx = i; });
    items.forEach(function(el) { el.classList.remove('active'); });
    var next = (activeIdx + dir + items.length) % items.length;
    items[next].classList.add('active');
    items[next].scrollIntoView({ block: 'nearest' });
  }

  async function doGlobalSearch(query) {
    try {
      var resp = await fetch('/api/search?q=' + encodeURIComponent(query) + '&limit=10');
      var results = await resp.json();
      paletteList.innerHTML = '';
      if (!results.length) {
        paletteList.innerHTML = '<li class="command-palette-item" style="color:var(--text-muted)">No results</li>';
        return;
      }
      results.forEach(function(r, i) {
        var li = document.createElement('li');
        li.className = 'command-palette-item' + (i === 0 ? ' active' : '');
        li.innerHTML = '<strong>[' + r.entity_type + ']</strong> ' + r.title;
        paletteList.appendChild(li);
      });
    } catch(e) {
      paletteList.innerHTML = '<li class="command-palette-item" style="color:var(--text-muted)">Search error</li>';
    }
  }

  /* ── Command actions ────────────────────────────────────── */

  function openSearch() {
    // Prefer the <global-search> Web Component if present in the page
    var gs = document.querySelector('global-search');
    if (gs && typeof gs.open === 'function') {
      gs.open();
    } else {
      openPalette('search');
    }
  }
  function openCommandPalette() { openPalette('commands'); }

  function sendPrompt() {
    // Find active chat input and trigger send
    var chatInput = document.querySelector('.chat-input-row input:focus')
                 || document.querySelector('#mobile-chat-input');
    if (chatInput && chatInput.value.trim()) {
      var sendBtn = chatInput.parentElement.querySelector('button');
      if (sendBtn) sendBtn.click();
    }
  }

  function closeOverlay() {
    closePalette();
    if (typeof closeMobileMenu === 'function') closeMobileMenu();
    // Close any visible modal
    document.querySelectorAll('.modal-overlay.active, .help-overlay.active').forEach(function(el) {
      el.classList.remove('active');
      el.style.display = 'none';
    });
  }

  function switchTab(idx) {
    var tabs = document.querySelectorAll('#agent-tab-bar .agent-tab');
    if (tabs[idx]) tabs[idx].click();
  }

  function showHelp() {
    var overlay = document.getElementById('help-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'help-overlay';
      overlay.className = 'help-overlay';
      overlay.innerHTML =
        '<div class="help-card glass-panel">' +
          '<h3>Keyboard Shortcuts</h3>' +
          '<table class="help-table">' +
          COMMANDS.filter(function(c){ return c.shortcut; }).map(function(c) {
            return '<tr><td><kbd>' + c.shortcut + '</kbd></td><td>' + c.label + '</td></tr>';
          }).join('') +
          '</table>' +
          '<p style="margin-top:1rem;font-size:0.8rem;color:var(--text-muted)">Press Esc to close</p>' +
        '</div>';
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) { overlay.style.display = 'none'; }
      });
      document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';
  }

  /* ── Global key listener ────────────────────────────────── */

  document.addEventListener('keydown', function(e) {
    var isInput = (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable);

    // Ctrl+K or Cmd+K → search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      openSearch();
      return;
    }
    // Ctrl+Shift+P → command palette
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'P') {
      e.preventDefault();
      openCommandPalette();
      return;
    }
    // Ctrl+Enter → send
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      sendPrompt();
      return;
    }
    // Esc → close
    if (e.key === 'Escape') {
      closeOverlay();
      return;
    }
    // Ctrl+1..9 → tab switch
    if ((e.ctrlKey || e.metaKey) && e.key >= '1' && e.key <= '9') {
      e.preventDefault();
      switchTab(parseInt(e.key) - 1);
      return;
    }
    // ? → help (only when no input focused)
    if (e.key === '?' && !isInput) {
      showHelp();
      return;
    }
  });

})();
