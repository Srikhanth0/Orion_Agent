/**
 * renderer.js — ORION Widget renderer process.
 *
 * WebSocket client, UI logic, tab management, settings I/O.
 */
const { ipcRenderer } = require('electron');

// ── State ──────────────────────────────────────────────────────────────────────
let ws = null;
let panelOpen = false;
let autoScroll = true;
let logCount = 0;
const MAX_LOG_LINES = 500;
const WS_URL = 'ws://localhost:8765';
let reconnectDelay = 1000;
let reconnectTimer = null;

// Log filter state
const logFilters = { INFO: true, WARN: true, ERROR: true };

// ── WebSocket ──────────────────────────────────────────────────────────────────

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    setStatus('online');
    reconnectDelay = 1000;
    hideReconnectBanner();
    addSystemMsg('Connected to ORION');
    ws.send(JSON.stringify({ type: 'get_stats' }));
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case 'agent_response':
          hideTyping();
          addMsg('agent', data.text);
          break;
        case 'log':
          addLog(data.level, data.msg);
          break;
        case 'stats':
          updateDashboard(data);
          break;
        case 'config':
          loadConfigToUI(data);
          break;
      }
    } catch (e) {
      console.warn('Bad WS message:', e);
    }
  };

  ws.onclose = () => {
    setStatus('offline');
    showReconnectBanner();
    scheduleReconnect();
  };

  ws.onerror = () => {
    setStatus('offline');
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
    reconnectDelay = Math.min(reconnectDelay * 2, 10000);
  }, reconnectDelay);
}

function setStatus(state) {
  const dot = document.getElementById('status-dot');
  dot.className = 'status-dot' + (state === 'offline' ? ' offline' : '') + (state === 'processing' ? ' processing' : '');
}

function showReconnectBanner() {
  document.getElementById('reconnect-banner').classList.add('visible');
}
function hideReconnectBanner() {
  document.getElementById('reconnect-banner').classList.remove('visible');
}

// ── Panel Toggle ───────────────────────────────────────────────────────────────

function togglePanel() {
  panelOpen = !panelOpen;
  document.getElementById('orion-panel').classList.toggle('open', panelOpen);
  if (panelOpen) {
    const input = document.getElementById('chat-input');
    if (input) input.focus();
  }
}

ipcRenderer.on('toggle-panel', togglePanel);

// ── Tab Switching ──────────────────────────────────────────────────────────────

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    if (btn.dataset.tab === tabId) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });
  document.querySelectorAll('.tab-content').forEach(tc => {
    if (tc.id === 'tab-' + tabId) {
      tc.classList.add('active');
    } else {
      tc.classList.remove('active');
    }
  });
}

// ── Chat ────────────────────────────────────────────────────────────────────────

function addMsg(role, text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function addSystemMsg(text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg system';
  div.textContent = text;
  container.appendChild(div);
}

function showTyping() {
  document.getElementById('typing-indicator').classList.add('visible');
}
function hideTyping() {
  document.getElementById('typing-indicator').classList.remove('visible');
}

function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addSystemMsg('Not connected to ORION. Reconnecting...');
    connectWS();
    return;
  }

  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'user_message', text }));
  input.value = '';
  input.style.height = '38px';
  showTyping();
  setStatus('processing');
}

function handleChatKey(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

// Auto-resize textarea
function autoResize(el) {
  el.style.height = '38px';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

// ── Logs ────────────────────────────────────────────────────────────────────────

function addLog(level, msg) {
  const output = document.getElementById('log-output');
  const t = new Date().toTimeString().slice(0, 8);
  const normalLevel = level.toUpperCase().replace(/\s/g, '');

  const line = document.createElement('div');
  line.className = `log-line ${normalLevel.toLowerCase()}`;
  line.dataset.level = normalLevel;

  const timeSpan = document.createElement('span');
  timeSpan.className = 'log-time';
  timeSpan.textContent = t;

  const levelSpan = document.createElement('span');
  levelSpan.className = 'log-level';
  levelSpan.textContent = normalLevel;

  const msgSpan = document.createElement('span');
  msgSpan.className = 'log-msg';
  msgSpan.textContent = msg;

  line.appendChild(timeSpan);
  line.appendChild(levelSpan);
  line.appendChild(msgSpan);

  // Apply filter
  const filterKey = normalLevel === 'WARNING' ? 'WARN' : normalLevel;
  if (!logFilters[filterKey]) {
    line.classList.add('hidden');
  }

  output.appendChild(line);
  logCount++;

  // Cap at MAX_LOG_LINES
  while (logCount > MAX_LOG_LINES) {
    output.removeChild(output.firstChild);
    logCount--;
  }

  if (autoScroll) {
    output.scrollTop = output.scrollHeight;
  }
}

function toggleLogFilter(level) {
  logFilters[level] = !logFilters[level];

  // Update chip UI
  document.querySelector(`.log-chip[data-level="${level}"]`).classList.toggle('active', logFilters[level]);

  // Show/hide lines
  document.querySelectorAll('#log-output .log-line').forEach(line => {
    const lineLevel = line.dataset.level;
    const key = lineLevel === 'WARNING' ? 'WARN' : lineLevel;
    line.classList.toggle('hidden', !logFilters[key]);
  });
}

// Pause auto-scroll on hover
document.addEventListener('DOMContentLoaded', () => {
  const logOutput = document.getElementById('log-output');
  if (logOutput) {
    logOutput.addEventListener('mouseenter', () => { autoScroll = false; });
    logOutput.addEventListener('mouseleave', () => { autoScroll = true; });
  }
});

// ── Dashboard ──────────────────────────────────────────────────────────────────

let uptimeInterval = null;
let uptimeBase = 0;

function updateDashboard(data) {
  document.getElementById('stat-tasks').textContent = data.tasks_run || 0;
  document.getElementById('stat-success').textContent = (data.success_rate || 0) + '%';

  const avgMs = data.avg_duration_ms || 0;
  document.getElementById('stat-duration').textContent = avgMs > 0 ? (avgMs / 1000).toFixed(1) + 's' : '—';

  // Start uptime ticker
  uptimeBase = data.uptime_seconds || 0;
  if (!uptimeInterval) {
    uptimeInterval = setInterval(() => {
      uptimeBase++;
      document.getElementById('stat-uptime').textContent = formatUptime(uptimeBase);
    }, 1000);
  }
  document.getElementById('stat-uptime').textContent = formatUptime(uptimeBase);

  // MCP servers
  const mcpContainer = document.getElementById('mcp-servers');
  mcpContainer.innerHTML = '';
  (data.mcp_servers || []).forEach(s => {
    const row = document.createElement('div');
    row.className = 'mcp-row';
    row.innerHTML = `
      <div class="mcp-dot ${s.status === 'offline' ? 'offline' : ''}"></div>
      <span class="mcp-name">${escapeHtml(s.name)}</span>
      <span class="mcp-status-pill">${s.status === 'online' ? 'LIVE' : 'IDLE'}</span>
    `;
    mcpContainer.appendChild(row);
  });

  // Recent activity
  const actContainer = document.getElementById('recent-activity');
  actContainer.innerHTML = '';
  (data.recent_tasks || []).forEach(t => {
    const row = document.createElement('div');
    row.className = 'activity-row';
    row.innerHTML = `
      <div class="activity-icon ${t.success ? 'success' : 'fail'}">${t.success ? '✓' : '✗'}</div>
      <span class="activity-task">${escapeHtml(t.task)}</span>
      <span class="activity-time">${escapeHtml(t.time || '')}</span>
    `;
    actContainer.appendChild(row);
  });

  setStatus('online');
}

function formatUptime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

// ── Settings ───────────────────────────────────────────────────────────────────

function saveSetting(key, value) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'config_update', key, value }));
  }
}

function loadConfigToUI(cfg) {
  // Populate settings from received config
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (!el || val === undefined) return;
    if (el.type === 'checkbox') el.checked = !!val;
    else if (el.type === 'range') { el.value = val; const span = el.nextElementSibling; if (span) span.textContent = val; }
    else el.value = val;
  };
  setVal('setting-model', cfg.model);
  setVal('setting-vision-model', cfg.vision_model);
  setVal('setting-temperature', cfg.temperature);
  setVal('setting-vision-validation', cfg.vision_validation);
  setVal('setting-auto-retry', cfg.auto_retry);
  setVal('setting-memory', cfg.chromadb_memory);
  setVal('setting-max-retries', cfg.max_retries);
  setVal('setting-telegram', cfg.telegram_enabled);
  setVal('setting-slack', cfg.slack_enabled);
  setVal('setting-log-file', cfg.log_to_file);
}

function requestConfig() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'get_config' }));
  }
}

function confirmDanger(action) {
  const msg = action === 'clear_memory'
    ? 'Clear all memory data? This cannot be undone.'
    : 'Reset all settings to defaults?';
  if (confirm(msg)) {
    saveSetting(action, true);
    addSystemMsg(action === 'clear_memory' ? 'Memory cleared.' : 'Settings reset.');
  }
}

// ── Dragging ───────────────────────────────────────────────────────────────────

let isDragging = false;
let dragStartX, dragStartY;

document.addEventListener('DOMContentLoaded', () => {
  const header = document.querySelector('.panel-header');
  if (!header) return;

  header.addEventListener('mousedown', (e) => {
    if (e.target.tagName === 'BUTTON') return;
    isDragging = true;
    dragStartX = e.screenX;
    dragStartY = e.screenY;
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const deltaX = e.screenX - dragStartX;
    const deltaY = e.screenY - dragStartY;
    dragStartX = e.screenX;
    dragStartY = e.screenY;
    ipcRenderer.send('window-drag', { deltaX, deltaY });
  });

  document.addEventListener('mouseup', () => {
    isDragging = false;
  });
});

// ── Utilities ──────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Init ────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  // Request config on settings tab switch
  document.querySelector('.tab-btn[data-tab="settings"]')?.addEventListener('click', requestConfig);
});

// Explicitly export to window to ensure inline HTML onclick handlers work
window.togglePanel = togglePanel;
window.switchTab = switchTab;
window.sendMessage = sendMessage;
window.handleChatKey = handleChatKey;
window.autoResize = autoResize;
window.toggleLogFilter = toggleLogFilter;
window.saveSetting = saveSetting;
window.confirmDanger = confirmDanger;
window.connectWS = connectWS;
