// ==UserScript==
// @name         CLIProxyAPI Codex Quota Auto Manager
// @version      0.3.0
// @description  Manage Codex auth availability with visual panel and manual 401 cleanup.
// @author       keggin
// @match        *://172.207.250.61:8317/management.html*
// @match        *://172.207.250.61:8317/*
// @include      *management.html*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  function reportBootstrapError(e) {
    const message = String((e && e.message) || e || 'unknown error');
    console.error('[CodexQuotaAuto] bootstrap failed:', e);
    try {
      window.__codexQuotaAutoBootstrapError = message;
    } catch (_) {
    }
    try {
      alert(`CodexQuotaAuto 启动失败: ${message}`);
    } catch (_) {
    }
  }

  try {
    const CONFIG = {
    apiBase: `${location.origin}/v0/management`,
    intervalMs: 2 * 60 * 1000,
    requestTimeoutMs: 30 * 1000,
    maxConcurrency: 100,
    deleteConcurrency: 50,
    invalidatedText: '401 Your authentication token has been invalidated. Please try signing in again.',
    strongInvalid401Keywords: [
      'authentication token has been invalidated',
      'please try signing in again',
      'invalid_token',
      'token is invalid',
      'token has expired'
    ],
    suspect401Keywords: [
      'invalid credentials',
      'unauthorized'
    ],
    minConsecutiveInvalidBeforeDelete: 2,
    storageKey: 'codex_quota_manager_management_key',
    panelId: 'codex-quota-auto-panel',
    probe: {
      method: 'POST',
      url: 'https://chatgpt.com/backend-api/codex/responses/compact',
      header: {
        Authorization: 'Bearer $TOKEN$',
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'Openai-Beta': 'responses=experimental',
        Version: '0.98.0',
        'User-Agent': 'codex_cli_rs/0.98.0 (Windows 10; x86_64) Tampermonkey/1.0'
      },
      data: JSON.stringify({
        model: 'gpt-5-mini',
        input: [
          {
            role: 'user',
            content: [
              {
                type: 'input_text',
                text: 'ping'
              }
            ]
          }
        ]
      })
    }
  };

  const state = {
    running: false,
    cycle: 0,
    invalidCountByAuthIndex: new Map(),
    rowsByAuthIndex: new Map(),
    lastDeletedNames: [],
    lastRunAt: '',
    nextRunAt: '',
    lastError: ''
  };

  function log(...args) {
    console.log('[CodexQuotaAuto]', ...args);
  }

  function warn(...args) {
    console.warn('[CodexQuotaAuto]', ...args);
  }

  function err(...args) {
    console.error('[CodexQuotaAuto]', ...args);
  }

  function toLocalTime(ts) {
    if (!ts) return '-';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '-';
    return d.toLocaleString();
  }

  function trimText(text, max = 160) {
    const raw = String(text || '').replace(/\s+/g, ' ').trim();
    if (raw.length <= max) return raw;
    return `${raw.slice(0, max)}...`;
  }

  function getManagementKey() {
    const direct = (localStorage.getItem(CONFIG.storageKey) || '').trim();
    if (direct) return direct;

    const candidates = [
      'management_key',
      'managementKey',
      'remote_management_key',
      'remoteManagementKey',
      'cpapi_management_key',
      'cliproxy_management_key'
    ];

    for (const key of candidates) {
      const value = (localStorage.getItem(key) || '').trim();
      if (value) {
        localStorage.setItem(CONFIG.storageKey, value);
        return value;
      }
    }

    return '';
  }

  function setManagementKey(key) {
    const value = String(key || '').trim();
    if (!value) {
      throw new Error('management key is empty');
    }
    localStorage.setItem(CONFIG.storageKey, value);
  }

  function clearManagementKey() {
    localStorage.removeItem(CONFIG.storageKey);
  }

  async function requestJSON(path, options, managementKey) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CONFIG.requestTimeoutMs);

    try {
      const resp = await fetch(`${CONFIG.apiBase}${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${managementKey}`,
          ...(options && options.headers ? options.headers : {})
        }
      });

      const text = await resp.text();
      let data = null;

      try {
        data = text ? JSON.parse(text) : null;
      } catch (_) {
        data = null;
      }

      return { ok: resp.ok, status: resp.status, data, rawText: text };
    } finally {
      clearTimeout(timer);
    }
  }

  async function listAuthFiles(managementKey) {
    const res = await requestJSON('/auth-files', { method: 'GET' }, managementKey);
    if (!res.ok) {
      throw new Error(`list auth files failed: HTTP ${res.status} ${res.rawText}`);
    }
    return Array.isArray(res.data && res.data.files) ? res.data.files : [];
  }

  async function probeCodexAuth(authIndex, managementKey) {
    const payload = {
      auth_index: authIndex,
      method: CONFIG.probe.method,
      url: CONFIG.probe.url,
      header: CONFIG.probe.header,
      data: CONFIG.probe.data
    };

    const res = await requestJSON(
      '/api-call',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      },
      managementKey
    );

    if (!res.ok) {
      throw new Error(`probe api-call failed: HTTP ${res.status} ${res.rawText}`);
    }

    return res.data || { status_code: 0, body: '' };
  }

  async function deleteAuthFileByName(name, managementKey) {
    const q = encodeURIComponent(name);
    const res = await requestJSON(`/auth-files?name=${q}`, { method: 'DELETE' }, managementKey);
    if (!res.ok) {
      throw new Error(`delete auth file failed: HTTP ${res.status} ${res.rawText}`);
    }
    return res.data || { status: 'ok' };
  }

  function isCodexFile(file) {
    const provider = String((file && (file.provider || file.type)) || '').toLowerCase();
    return provider === 'codex';
  }

  function isEnabled(file) {
    return !(file && (file.disabled === true || file.status === 'disabled'));
  }

  function analyzeUnauthorized401(result) {
    if (!result) {
      return {
        is401: false,
        isStrongInvalid: false,
        isSuspectInvalid: false,
        reason: ''
      };
    }

    const statusCode = Number(result.status_code || 0);
    if (statusCode !== 401) {
      return {
        is401: false,
        isStrongInvalid: false,
        isSuspectInvalid: false,
        reason: ''
      };
    }

    const body = String(result.body || '');
    const lower = body.toLowerCase();

    if (lower.includes(String(CONFIG.invalidatedText).toLowerCase())) {
      return {
        is401: true,
        isStrongInvalid: true,
        isSuspectInvalid: false,
        reason: CONFIG.invalidatedText
      };
    }

    for (const keyword of CONFIG.strongInvalid401Keywords) {
      if (lower.includes(String(keyword).toLowerCase())) {
        return {
          is401: true,
          isStrongInvalid: true,
          isSuspectInvalid: false,
          reason: `401 命中强失效关键词: ${keyword}`
        };
      }
    }

    for (const keyword of CONFIG.suspect401Keywords) {
      if (lower.includes(String(keyword).toLowerCase())) {
        return {
          is401: true,
          isStrongInvalid: false,
          isSuspectInvalid: true,
          reason: `401 命中疑似失效关键词: ${keyword}`
        };
      }
    }

    return {
      is401: true,
      isStrongInvalid: false,
      isSuspectInvalid: true,
      reason: '401 未授权（未命中明确失效文案）'
    };
  }

  function markInvalid(authIndex) {
    const current = state.invalidCountByAuthIndex.get(authIndex) || 0;
    const next = current + 1;
    state.invalidCountByAuthIndex.set(authIndex, next);
    return next;
  }

  function clearInvalid(authIndex) {
    state.invalidCountByAuthIndex.delete(authIndex);
  }

  function getInvalidCount(authIndex) {
    return state.invalidCountByAuthIndex.get(authIndex) || 0;
  }

  function getPanelRoot() {
    return document.getElementById(CONFIG.panelId);
  }

  function getReopenButton() {
    return document.getElementById(`${CONFIG.panelId}-reopen`);
  }

  function setPanelVisible(visible) {
    const root = getPanelRoot();
    const reopenBtn = getReopenButton();
    if (root) root.style.display = visible ? '' : 'none';
    if (reopenBtn) reopenBtn.style.display = visible ? 'none' : 'block';
  }

  function ensureReopenButton() {
    if (getReopenButton()) return;
    const btn = document.createElement('button');
    btn.id = `${CONFIG.panelId}-reopen`;
    btn.textContent = '打开面板';
    btn.style.cssText = [
      'position:fixed',
      'right:12px',
      'bottom:12px',
      'z-index:2147483647',
      'color:#e5e7eb',
      'background:#1e293b',
      'border:1px solid #475569',
      'border-radius:6px',
      'padding:6px 10px',
      'cursor:pointer',
      'display:none'
    ].join(';');
    btn.addEventListener('click', () => setPanelVisible(true));
    document.body.appendChild(btn);
  }

  function ensurePanelStyle() {
    const styleId = `${CONFIG.panelId}-style`;
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
#${CONFIG.panelId} {
  position: fixed;
  right: 12px;
  bottom: 12px;
  width: 620px;
  min-width: 480px;
  min-height: 320px;
  max-height: 82vh;
  resize: both;
  overflow: auto;
  z-index: 2147483647;
  color: #e5e7eb;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  font-size: 12px;
}
#${CONFIG.panelId} .cqa-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid #334155;
  background: #020617;
}
#${CONFIG.panelId} .cqa-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
#${CONFIG.panelId} .cqa-title {
  font-size: 13px;
  font-weight: 700;
}
#${CONFIG.panelId} .cqa-body {
  padding: 10px;
  max-height: calc(82vh - 40px);
  overflow: auto;
}
#${CONFIG.panelId} .cqa-grid {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  gap: 6px;
  margin-bottom: 8px;
}
#${CONFIG.panelId} .cqa-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
#${CONFIG.panelId} .cqa-meta {
  color: #94a3b8;
}
#${CONFIG.panelId} input[type="text"] {
  width: 100%;
  min-width: 160px;
  color: #e5e7eb;
  background: #111827;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 5px 8px;
}
#${CONFIG.panelId} button {
  color: #e5e7eb;
  background: #1e293b;
  border: 1px solid #475569;
  border-radius: 6px;
  padding: 4px 8px;
  cursor: pointer;
}
#${CONFIG.panelId} button:hover {
  background: #334155;
}
#${CONFIG.panelId} button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
#${CONFIG.panelId} button.danger {
  background: #3f1010;
  border-color: #7f1d1d;
}
#${CONFIG.panelId} button.danger:hover {
  background: #5f1717;
}
#${CONFIG.panelId} table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
}
#${CONFIG.panelId} th, #${CONFIG.panelId} td {
  padding: 6px 4px;
  border-bottom: 1px solid #233047;
  text-align: left;
  vertical-align: top;
}
#${CONFIG.panelId} th {
  color: #93c5fd;
}
#${CONFIG.panelId} .ok { color: #22c55e; }
#${CONFIG.panelId} .bad { color: #ef4444; }
#${CONFIG.panelId} .warn { color: #f59e0b; }
#${CONFIG.panelId} .muted { color: #94a3b8; }
`;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    if (getPanelRoot()) return;
    ensurePanelStyle();

    const root = document.createElement('div');
    root.id = CONFIG.panelId;
    root.innerHTML = `
      <div class="cqa-header">
        <div class="cqa-header-left">
          <button id="cqa-close">关闭</button>
          <div class="cqa-title">Codex 账号状态管理</div>
        </div>
        <button id="cqa-run-now">立即检测</button>
      </div>
      <div class="cqa-body">
        <div class="cqa-grid">
          <input id="cqa-key" type="text" placeholder="Management Key" />
          <button id="cqa-save-key">保存</button>
          <button id="cqa-clear-key">清空</button>
          <button id="cqa-delete-401" class="danger">一键删除全部401</button>
        </div>
        <div class="cqa-row">
          <span class="cqa-meta" id="cqa-cycle-meta"></span>
        </div>
        <div class="cqa-row cqa-meta" id="cqa-stats"></div>
        <div class="cqa-row cqa-meta" id="cqa-last-error"></div>
        <table>
          <thead>
            <tr>
              <th>账号</th>
              <th>状态</th>
              <th>HTTP</th>
              <th>连续命中</th>
              <th>最近检测</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="cqa-tbody"></tbody>
        </table>
      </div>
    `;
    document.body.appendChild(root);
    ensureReopenButton();
    setPanelVisible(true);

    const keyInput = root.querySelector('#cqa-key');
    keyInput.value = getManagementKey();
root.querySelector('#cqa-close').addEventListener('click', () => {
  setPanelVisible(false);
});

root.querySelector('#cqa-run-now').addEventListener('click', () => {
  runCycle(true);
});


    root.querySelector('#cqa-save-key').addEventListener('click', () => {
      try {
        setManagementKey(keyInput.value);
        state.lastError = '';
      } catch (e) {
        state.lastError = String((e && e.message) || e);
      }
      renderPanel();
    });

    root.querySelector('#cqa-clear-key').addEventListener('click', () => {
      clearManagementKey();
      keyInput.value = '';
      state.lastError = '';
      renderPanel();
    });

    root.querySelector('#cqa-delete-401').addEventListener('click', () => {
      deleteAll401();
    });
  }

  function upsertRow(file) {
    const authIndex = String(file.auth_index || '').trim();
    if (!authIndex) return null;

    const old = state.rowsByAuthIndex.get(authIndex) || {};
    const row = {
      authIndex,
      name: String(file.name || '').trim(),
      email: String(file.email || '').trim(),
      status: old.status || 'pending',
      statusCode: old.statusCode || 0,
      reason: old.reason || '',
      bodySnippet: old.bodySnippet || '',
      lastProbeAt: old.lastProbeAt || '',
      candidateDelete: !!old.candidateDelete,
      deleted: !!old.deleted
    };

    state.rowsByAuthIndex.set(authIndex, row);
    return row;
  }

  function setRow(authIndex, patch) {
    const row = state.rowsByAuthIndex.get(authIndex);
    if (!row) return;
    Object.assign(row, patch || {});
  }

  function statusClass(row) {
    if (row.deleted) return 'bad';
    if (row.status === 'usable') return 'ok';
    if (row.status === 'invalidated' || row.status === 'candidate' || row.status === 'probe-failed') return 'bad';
    if (row.status === 'quota-limited' || row.status === 'unauthorized') return 'warn';
    return 'muted';
  }

  function statusText(row) {
    if (row.deleted) return '已删除';
    switch (row.status) {
      case 'usable':
        return '可用';
      case 'invalidated':
        return '401 令牌失效';
      case 'candidate':
        return '待删候选';
      case 'unauthorized':
        return '401 未授权';
      case 'quota-limited':
        return '额度/频率受限';
      case 'probe-failed':
        return '探测失败';
      case 'probing':
        return '检测中';
      default:
        return row.status || '待检测';
    }
  }

  function isDeletable401Row(row) {
    return !row.deleted && (row.status === 'invalidated' || row.status === 'unauthorized' || row.status === 'candidate');
  }

  function computeStats() {
    const rows = Array.from(state.rowsByAuthIndex.values()).filter((row) => !row.deleted);
    let invalid = 0;

    for (const row of rows) {
      if (isDeletable401Row(row)) invalid += 1;
    }

    return {
      total: rows.length,
      invalid,
      usable: rows.length - invalid
    };
  }

  function renderPanel() {
    const root = getPanelRoot();
    if (!root) return;

    root.querySelector('#cqa-cycle-meta').textContent =
      `轮次=${state.cycle} 运行中=${state.running ? '是' : '否'} 下次=${toLocalTime(state.nextRunAt)}`;

    const stats = computeStats();
    root.querySelector('#cqa-stats').textContent =
      `总数=${stats.total} 可用=${stats.usable} 不可用=${stats.invalid} 本轮删除=${state.lastDeletedNames.length}`;

    root.querySelector('#cqa-last-error').textContent = state.lastError ? `错误: ${state.lastError}` : '';

    const rows = Array.from(state.rowsByAuthIndex.values()).sort((a, b) => {
      const an = String(a.name || '').toLowerCase();
      const bn = String(b.name || '').toLowerCase();
      return an.localeCompare(bn);
    });

    const tbody = root.querySelector('#cqa-tbody');
    tbody.innerHTML = '';

    for (const row of rows) {
      const tr = document.createElement('tr');

      const tdName = document.createElement('td');
      tdName.textContent = row.email ? `${row.name} (${row.email})` : row.name || row.authIndex;

      const tdStatus = document.createElement('td');
      tdStatus.className = statusClass(row);
      tdStatus.textContent = statusText(row);

      const tdCode = document.createElement('td');
      tdCode.textContent = String(row.statusCode || '');

      const tdHits = document.createElement('td');
      tdHits.textContent = String(getInvalidCount(row.authIndex));

      const tdProbe = document.createElement('td');
      tdProbe.textContent = toLocalTime(row.lastProbeAt);

      const tdAction = document.createElement('td');
      const delBtn = document.createElement('button');
      delBtn.className = 'danger';
      delBtn.textContent = '删除';
      delBtn.disabled = !isDeletable401Row(row);
      delBtn.addEventListener('click', () => deleteOneCandidate(row.authIndex));
      tdAction.appendChild(delBtn);

      tr.appendChild(tdName);
      tr.appendChild(tdStatus);
      tr.appendChild(tdCode);
      tr.appendChild(tdHits);
      tr.appendChild(tdProbe);
      tr.appendChild(tdAction);

      tbody.appendChild(tr);
    }

    const keyInput = root.querySelector('#cqa-key');
    if (document.activeElement !== keyInput) {
      keyInput.value = getManagementKey();
    }
  }

  function classifyProbeResult(probe) {
    const statusCode = Number((probe && probe.status_code) || 0);
    const body = String((probe && probe.body) || '');

    const unauthorized = analyzeUnauthorized401(probe);
    if (unauthorized.isStrongInvalid) {
      return { status: 'invalidated', statusCode, reason: unauthorized.reason, body };
    }
    if (unauthorized.isSuspectInvalid) {
      return { status: 'unauthorized', statusCode, reason: unauthorized.reason, body };
    }

    if (statusCode >= 200 && statusCode < 300) {
      return { status: 'usable', statusCode, reason: 'probe ok', body };
    }
    if (statusCode === 400) {
      return { status: 'usable', statusCode: 200, reason: '可用', body };
    }
    if (statusCode === 429) {
      return { status: 'quota-limited', statusCode, reason: 'quota/rate limited', body };
    }
    return { status: 'probe-failed', statusCode, reason: statusCode ? `non-2xx (${statusCode})` : 'status unknown', body };
  }

  async function deleteOneCandidate(authIndex, options = {}) {
    const row = state.rowsByAuthIndex.get(authIndex);
    if (!row) return;

    const managementKey = getManagementKey();
    if (!managementKey) {
      state.lastError = 'missing management key';
      renderPanel();
      return;
    }

    if (!options.skipConfirm) {
      if (!confirm(`确认删除认证文件？\n${row.name || authIndex}`)) return;
    }

    try {
      await deleteAuthFileByName(row.name, managementKey);
      clearInvalid(authIndex);
      setRow(authIndex, {
        deleted: true,
        reason: '已手动删除',
        lastProbeAt: new Date().toISOString()
      });
      state.lastDeletedNames.push(row.name);
      renderPanel();
    } catch (e) {
      state.lastError = String((e && e.message) || e);
      renderPanel();
    }
  }

  async function deleteAll401() {
    const targets = Array.from(state.rowsByAuthIndex.values()).filter((row) => isDeletable401Row(row));
    if (targets.length === 0) {
      state.lastError = '没有可删除的401账号';
      renderPanel();
      return;
    }

    if (!confirm(`确认删除 ${targets.length} 个401账号？`)) return;

    const queue = [...targets];
    await runWithConcurrency(queue, CONFIG.deleteConcurrency, async (row) => {
      await deleteOneCandidate(row.authIndex, { skipConfirm: true });
    });
  }

  function askManagementKey() {
    const current = getManagementKey();
    const value = prompt('请输入 Management Key（仅保存在当前浏览器 localStorage）', current || '');
    if (!value || !value.trim()) return '';
    const key = value.trim();
    localStorage.setItem(CONFIG.storageKey, key);
    return key;
  }

  async function runWithConcurrency(items, limit, handler) {
    const workers = [];
    const max = Math.max(1, Math.min(Number(limit) || 1, items.length || 1));
    for (let i = 0; i < max; i += 1) {
      workers.push((async () => {
        for (;;) {
          const next = items.pop();
          if (!next) return;
          await handler(next);
        }
      })());
    }
    await Promise.all(workers);
  }

  async function runCycle(interactive) {
    if (state.running) {
      warn('previous cycle still running, skip');
      return;
    }

    ensurePanel();
    state.running = true;
    state.cycle += 1;
    state.lastDeletedNames = [];
    state.lastError = '';
    state.lastRunAt = new Date().toISOString();
    state.nextRunAt = new Date(Date.now() + CONFIG.intervalMs).toISOString();
    renderPanel();

    try {
      let managementKey = getManagementKey();
      if (!managementKey && interactive) {
        managementKey = askManagementKey();
      }
      if (!managementKey) {
        state.lastError = '缺少 Management Key，请先在面板输入并保存';
        warn('missing management key, skip cycle');
        return;
      }

      const files = await listAuthFiles(managementKey);
      const codexFiles = files.filter(isCodexFile).filter(isEnabled);
      const seen = new Set();

      log(`cycle #${state.cycle} start; enabled codex files: ${codexFiles.length}; concurrency=${CONFIG.maxConcurrency}`);

      const queue = [...codexFiles];

      await runWithConcurrency(queue, CONFIG.maxConcurrency, async (file) => {
        const authIndex = String(file.auth_index || '').trim();
        const name = String(file.name || '').trim();
        if (!authIndex || !name) {
          warn('skip invalid auth entry', file);
          return;
        }

        seen.add(authIndex);
        upsertRow(file);

        try {
          const probe = await probeCodexAuth(authIndex, managementKey);
          const result = classifyProbeResult(probe);

          if (result.status === 'invalidated' || result.status === 'unauthorized') {
            const hit = markInvalid(authIndex);

            setRow(authIndex, {
              status: result.status,
              statusCode: result.statusCode,
              reason: result.reason,
              bodySnippet: trimText(result.body),
              lastProbeAt: new Date().toISOString(),
              candidateDelete: true,
              deleted: false
            });

            warn(`unauthorized-like hit ${hit}/${CONFIG.minConsecutiveInvalidBeforeDelete} for ${name} (${result.status})`);
          } else {
            clearInvalid(authIndex);
            setRow(authIndex, {
              status: result.status,
              statusCode: result.statusCode,
              reason: result.reason,
              bodySnippet: trimText(result.body),
              lastProbeAt: new Date().toISOString(),
              candidateDelete: false,
              deleted: false
            });
          }
        } catch (e) {
          setRow(authIndex, {
            status: 'probe-failed',
            statusCode: 0,
            reason: String((e && e.message) || e),
            bodySnippet: '',
            lastProbeAt: new Date().toISOString(),
            candidateDelete: false,
            deleted: false
          });
          warn(`probe failed for ${name}:`, e);
        }

        renderPanel();
      });

      for (const [authIndex] of state.rowsByAuthIndex.entries()) {
        if (!seen.has(authIndex)) {
          state.rowsByAuthIndex.delete(authIndex);
          state.invalidCountByAuthIndex.delete(authIndex);
        }
      }

      log(`cycle #${state.cycle} done`);
    } catch (e) {
      state.lastError = String((e && e.message) || e);
      err('cycle failed:', e);
    } finally {
      state.running = false;
      renderPanel();
    }
  }

  function getStateSnapshot() {
    return {
      cycle: state.cycle,
      running: state.running,
      lastRunAt: state.lastRunAt,
      nextRunAt: state.nextRunAt,
      invalidCountByAuthIndex: Array.from(state.invalidCountByAuthIndex.entries()),
      lastDeletedNames: [...state.lastDeletedNames],
      rows: Array.from(state.rowsByAuthIndex.values()),
      config: {
        apiBase: CONFIG.apiBase,
        intervalMs: CONFIG.intervalMs,
        maxConcurrency: CONFIG.maxConcurrency,
        minConsecutiveInvalidBeforeDelete: CONFIG.minConsecutiveInvalidBeforeDelete,
        probeURL: CONFIG.probe.url
      }
    };
  }

  function exposeDebugAPI() {
    window.codexQuotaAuto = {
      runNow: runCycle,
      setManagementKey: setManagementKey,
      clearManagementKey: clearManagementKey,
      deleteCandidateByAuthIndex: (authIndex) => deleteOneCandidate(String(authIndex || '').trim()),
      getState: getStateSnapshot
    };
  }

  function bootstrap() {
    exposeDebugAPI();

    const start = () => {
      ensurePanel();
      renderPanel();
      runCycle(false);
      setInterval(() => runCycle(false), CONFIG.intervalMs);

      const observer = new MutationObserver(() => {
        if (!getPanelRoot()) {
          ensurePanel();
          renderPanel();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    };

    if (document.body) {
      start();
      return;
    }

    const timer = setInterval(() => {
      if (document.body) {
        clearInterval(timer);
        start();
      }
    }, 100);
  }

    bootstrap();
  } catch (e) {
    reportBootstrapError(e);
  }
})();
