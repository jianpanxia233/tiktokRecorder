/* DouyinLiveRecorder 桌面版前端 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const POLL_MS = 2500;
  const MAX_LOG_LINES = 800;

  let running = false;
  let timer = null;
  let logBuffer = [];

  /* ───────────────────────────── 工具 ───────────────────────────── */

  function toast(message, isError) {
    const box = $('toast');
    box.textContent = message;
    box.className = 'show' + (isError ? ' err' : '');
    clearTimeout(box._t);
    box._t = setTimeout(() => { box.className = ''; }, 2600);
  }

  function whenReady(fn) {
    if (window.pywebview && window.pywebview.api) return fn();
    window.addEventListener('pywebviewready', fn, { once: true });
  }

  async function call(method, ...args) {
    try {
      return await window.pywebview.api[method](...args);
    } catch (err) {
      toast(`调用 ${method} 失败：${err}`, true);
      return null;
    }
  }

  /* 点下去立刻有反馈：禁用按钮 + 转圈，等接口返回再恢复 */
  async function withBusy(button, work) {
    if (!button) return work();
    button.classList.add('loading');
    button.disabled = true;
    try {
      return await work();
    } finally {
      button.classList.remove('loading');
      button.disabled = false;
    }
  }

  function humanUptime(seconds) {
    if (!seconds) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h) return `${h} 小时 ${m} 分`;
    if (m) return `${m} 分 ${s} 秒`;
    return `${s} 秒`;
  }

  /* ───────────────────────────── 激活 ───────────────────────────── */

  function showGate(message, isError) {
    $('app').classList.add('hidden');
    $('gate').classList.remove('hidden');
    if (message) {
      const box = $('gate-msg');
      box.textContent = message;
      box.className = 'gate-msg' + (isError ? ' error' : '');
    }
    stopPolling();
  }

  function paintLicense(lic) {
    $('gate-machine').textContent = lic.machine_code || '—';
    $('license-text').textContent = lic.ok ? lic.describe : lic.message;
    if (lic.ok) {
      $('gate').classList.add('hidden');
      $('app').classList.remove('hidden');
      refreshAll();
      startPolling();
    } else {
      showGate(lic.message, lic.status !== 'not_activated');
    }
    return lic.ok;
  }

  async function refreshLicense() {
    const lic = await call('license_state');
    if (lic) paintLicense(lic);
  }

  async function doActivate() {
    const input = $('gate-input');
    const key = input.value.trim();
    if (!key) { toast('请先输入卡密', true); return; }
    const lic = await withBusy($('btn-activate'), () => call('activate', key));
    if (!lic) return;
    if (lic.ok) {
      input.value = '';
      toast('激活成功');
      paintLicense(lic);
    } else {
      const box = $('gate-msg');
      box.textContent = lic.message;
      box.className = 'gate-msg error';
      toast(lic.message, true);
    }
  }

  /* ───────────────────────────── 状态 ───────────────────────────── */

  function renderSummary(status) {
    const banner = $('hint-banner');
    const text = $('hint-text');
    const summary = status.summary || {};
    const proxy = status.proxy || {};
    const proxyBtn = $('btn-hint-proxy');

    if (!status.running) {
      banner.classList.add('hidden');
      return;
    }
    banner.classList.remove('hidden');
    proxyBtn.classList.add('hidden');

    // 代理端口没人监听是最常见也最致命的问题，优先提示
    if (proxy.enabled && proxy.listening === false) {
      banner.className = 'banner warn';
      text.textContent =
        `代理 ${proxy.address} 没有在监听（VPN 没开或换了端口），TikTok 会一直连不上。`;
      proxyBtn.classList.remove('hidden');
      return;
    }

    if (summary.recent_error_count > 0) {
      banner.className = 'banner warn';
      text.textContent =
        `最近 ${summary.window_minutes} 分钟有 ${summary.recent_error_count} 条错误：${summary.last_error}`;
      return;
    }
    banner.className = 'banner info';
    if (summary.last_event_at) {
      const detail = (summary.last_event || '').slice(24).trim();
      text.textContent = `最近一次直播事件（${summary.last_event_at}）：${detail}`;
    } else {
      text.textContent =
        '监控中：目前没有房间开播。有主播开播才会开始录制并生成文件，可到「日志」页确认。';
    }
  }

  function applyStatus(status) {
    running = status.running;
    $('dot').className = 'dot' + (running ? ' on' : '');
    $('status-text').textContent = running ? '监控中' : '已停止';
    $('run-info').textContent = running ? `已运行 ${humanUptime(status.uptime)}` : '未运行';

    const toggle = $('btn-toggle');
    if (!toggle.classList.contains('loading')) {
      toggle.textContent = running ? '停止录制' : '启动录制';
      toggle.className = running ? 'ghost danger' : 'primary';
    }
    if (status.save_path) $('save-path').textContent = `保存目录：${status.save_path}`;
    renderProxy(status.proxy);
    renderSummary(status);
  }

  /* ───────────────────────────── 代理 / VPN ───────────────────────────── */

  function renderProxy(proxy) {
    if (!proxy) return;
    const line = $('proxy-now');
    const parts = [];
    if (!proxy.enabled) {
      parts.push('录制器当前<strong>没有启用代理</strong>');
    } else if (proxy.listening === true) {
      parts.push(`录制器走 <code>${proxy.address}</code> · 端口在监听`);
    } else if (proxy.listening === false) {
      parts.push(`录制器走 <code>${proxy.address}</code> · <span style="color:var(--danger)">端口没有在监听</span>`);
    } else {
      parts.push(`录制器走 <code>${proxy.address}</code> · 远端代理无法本地判断`);
    }
    if (proxy.tun) {
      parts.push(`检测到 TUN 全局模式（<code>${proxy.tun}</code>），这种模式下录制器可以不设代理`);
    }
    line.innerHTML = parts.join('；');
  }

  function renderProxyResults(result) {
    const box = $('proxy-results');
    box.textContent = '';

    const candidates = (result.candidates || []).filter((item) => item.alive);
    const tunNote = result.tun
      ? `检测到 TUN 全局模式（${result.tun}）—— 这种情况不用填代理端口，直接让录制器走系统路由。`
      : '';

    if (!candidates.length) {
      const tip = document.createElement('p');
      tip.className = 'field-hint';
      tip.textContent = tunNote
        || '没有找到正在运行的代理。请先打开你的 VPN 客户端，确认它开启了「允许局域网/HTTP 代理」之类的选项，再点一次自动检测。';
      box.appendChild(tip);
      if (result.tun) box.appendChild(buildDisableProxyButton());
      return;
    }

    const current = (result.current || {}).address;
    candidates.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'proxy-item' + (item.address === current ? ' current' : '');

      const main = document.createElement('div');
      main.className = 'pi-main';
      const addr = document.createElement('div');
      addr.className = 'pi-addr';
      addr.textContent = item.address;
      const meta = document.createElement('div');
      meta.className = 'pi-meta';
      const bits = [item.client || '未知客户端'];
      if (item.latency_ms != null) bits.push(`${item.latency_ms}ms`);
      if (item.source === 'system') bits.push('来自系统代理设置');
      if (item.source === 'env') bits.push('来自环境变量');
      meta.textContent = bits.join(' · ');
      main.append(addr, meta);

      if (item.tiktok) {
        const tag = document.createElement('span');
        tag.className = 'tag ' + (item.tiktok === 'ok' ? 'ok' : item.tiktok === 'challenge' ? 'warn' : 'bad');
        tag.textContent = item.tiktok === 'ok' ? 'TikTok 可用'
          : item.tiktok === 'challenge' ? 'TikTok 被风控' : 'TikTok 不通';
        tag.title = item.tiktok_note || '';
        row.appendChild(tag);
      }

      const use = document.createElement('button');
      use.className = item.address === current ? 'ghost' : 'primary';
      use.textContent = item.address === current ? '当前使用中' : '使用这个';
      use.disabled = item.address === current;
      use.addEventListener('click', async () => {
        const applied = await withBusy(use, () => call('apply_proxy', item.address));
        if (!applied) return;
        toast(applied.message, !applied.ok);
        if (applied.ok) {
          renderProxy(applied.proxy);
          renderProxyResults(await call('detect_proxies', false));
          refreshStatus();
        }
      });

      row.append(main, use);
      box.appendChild(row);
    });

    if (result.tun) {
      const note = document.createElement('p');
      note.className = 'field-hint';
      note.textContent = tunNote;
      box.appendChild(note);
      box.appendChild(buildDisableProxyButton());
    }
  }

  function buildDisableProxyButton() {
    const button = document.createElement('button');
    button.className = 'ghost';
    button.textContent = '关闭录制器代理，改走系统全局';
    button.addEventListener('click', async () => {
      const result = await withBusy(button, () => call('disable_proxy'));
      if (!result) return;
      toast(result.message, !result.ok);
      if (result.ok) { renderProxy(result.proxy); refreshStatus(); }
    });
    return button;
  }

  async function detectProxies() {
    const deep = $('chk-detect-tiktok').checked;
    const result = await withBusy($('btn-proxy-detect'), () => call('detect_proxies', deep));
    if (!result) return;
    if (result.ok === false) { toast(result.message, true); return; }
    renderProxy(result.current);
    renderProxyResults(result);
    const found = (result.candidates || []).filter((item) => item.alive).length;
    toast(found ? `找到 ${found} 个可用代理` : '没有找到正在运行的代理', !found);
  }

  async function refreshStatus() {
    const status = await call('status');
    if (status) applyStatus(status);
  }

  async function toggleRecording() {
    const button = $('btn-toggle');
    const stopping = running;
    // 先改文案再发请求，避免"点了半天没反应"的错觉
    button.textContent = stopping ? '正在停止…' : '正在启动…';
    const result = await withBusy(button, () => call(stopping ? 'stop' : 'start'));
    if (result) toast(result.message, !result.ok);
    await refreshStatus();
  }

  /* ───────────────────────────── 房间 ───────────────────────────── */

  function renderRooms(rooms) {
    const list = $('room-list');
    list.textContent = '';
    $('room-count').textContent = `共 ${rooms.length} 个直播间`;

    if (!rooms.length) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '还没有直播间，在上面添加一个吧';
      list.appendChild(empty);
      return;
    }

    rooms.forEach((room) => {
      const item = document.createElement('li');

      const main = document.createElement('div');
      main.className = 'rm-main';
      const name = document.createElement('div');
      name.className = 'rm-name';
      name.textContent = room.name;
      const url = document.createElement('div');
      url.className = 'rm-url';
      url.textContent = room.url;
      main.append(name, url);

      const remove = document.createElement('button');
      remove.className = 'ghost danger';
      remove.textContent = '删除';
      remove.addEventListener('click', async () => {
        const result = await withBusy(remove, () => call('del_room', room.url));
        if (result) { renderRooms(result.rooms || []); toast(result.message); }
      });

      item.append(main, remove);
      list.appendChild(item);
    });
  }

  async function loadRooms() {
    const rooms = await call('get_rooms');
    if (rooms) renderRooms(rooms);
  }

  async function addRoom() {
    const url = $('room-url');
    const name = $('room-name');
    if (!url.value.trim()) { toast('请输入直播间地址', true); return; }
    const result = await withBusy($('btn-add-room'),
      () => call('add_room', url.value.trim(), name.value.trim()));
    if (!result) return;
    toast(result.message, !result.ok);
    if (result.ok) {
      url.value = '';
      name.value = '';
      renderRooms(result.rooms || []);
    }
  }

  async function clearRooms() {
    const result = await withBusy($('btn-clear-rooms'), () => call('clear_rooms'));
    if (result) { renderRooms(result.rooms || []); toast(result.message); }
  }

  /* ───────────────────────────── 日志 ───────────────────────────── */

  function appendLogs(lines) {
    if (!lines || !lines.length) return;
    const view = $('log-view');
    const fragment = document.createDocumentFragment();
    lines.forEach((entry) => {
      const span = document.createElement('span');
      span.className = entry.source === '状态' ? 'ln-state' : 'ln-debug';
      span.textContent = `[${entry.source}] ${entry.text}\n`;
      fragment.appendChild(span);
    });
    view.appendChild(fragment);
    logBuffer = logBuffer.concat(lines);
    while (view.childNodes.length > MAX_LOG_LINES * 1.5) {
      view.removeChild(view.firstChild);
    }
    if ($('log-follow').checked) view.scrollTop = view.scrollHeight;
  }

  /* ───────────────────────────── 设置 ───────────────────────────── */

  function renderSettings(data) {
    const form = $('settings-form');
    form.textContent = '';
    data.schema.forEach((item) => {
      const field = document.createElement('div');
      field.className = 'field';

      const label = document.createElement('div');
      label.className = 'field-label';
      label.textContent = item.label;

      const right = document.createElement('div');
      right.style.flex = '1';

      const body = document.createElement('div');
      body.className = 'field-body';

      const value = data.values[item.key] ?? '';
      let input;
      if (item.type === 'select') {
        input = document.createElement('select');
        (item.options || []).forEach((option) => {
          const node = document.createElement('option');
          node.value = option;
          node.textContent = option;
          input.appendChild(node);
        });
        input.value = value || item.default || (item.options || [''])[0];
      } else {
        input = document.createElement('input');
        input.value = value;
        if (item.type === 'number') { input.type = 'number'; input.min = '0'; }
        input.spellcheck = false;
      }
      input.dataset.key = item.key;
      body.appendChild(input);

      if (item.type === 'folder') {
        const browse = document.createElement('button');
        browse.className = 'ghost';
        browse.textContent = '浏览';
        browse.addEventListener('click', async () => {
          const picked = await withBusy(browse, () => call('pick_folder'));
          if (picked) input.value = picked;
        });
        body.appendChild(browse);
      }

      right.appendChild(body);
      if (item.hint) {
        const hint = document.createElement('p');
        hint.className = 'field-hint';
        hint.textContent = item.hint;
        right.appendChild(hint);
      }

      field.append(label, right);
      form.appendChild(field);
    });
    $('config-path').textContent = `配置文件：${data.config_file}`;
  }

  async function loadConfig() {
    const data = await call('get_config');
    if (data) renderSettings(data);
  }

  async function saveConfig() {
    const values = {};
    document.querySelectorAll('#settings-form [data-key]').forEach((node) => {
      values[node.dataset.key] = node.value;
    });
    const result = await withBusy($('btn-save-config'), () => call('save_config', values));
    if (result) toast(result.message, !result.ok);
  }

  /* ───────────────────────────── 关于 ───────────────────────────── */

  function renderAbout(about, lic) {
    const list = $('about-list');
    list.textContent = '';
    const rows = [
      ['软件名称', about.name],
      ['版本', about.version],
      ['开源项目', about.upstream_name],
      ['原作者', about.upstream_author],
      ['版权声明', about.copyright],
      ['开源协议', about.license],
      ['项目地址', about.upstream_url, true],
      ['运行环境', `${about.system} · Python ${about.python}`],
    ];
    rows.forEach(([key, value, isLink]) => {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      if (isLink) {
        const a = document.createElement('a');
        a.href = '#';
        a.textContent = value;
        a.addEventListener('click', (event) => {
          event.preventDefault();
          call('open_url', value);
        });
        dd.appendChild(a);
      } else {
        dd.textContent = value;
      }
      list.append(dt, dd);
    });

    const licList = $('license-list');
    licList.textContent = '';
    const licRows = [
      ['状态', lic.message],
      ['有效期', lic.describe],
      ['机器码', lic.machine_code],
      ['卡号', lic.key_id == null ? '—' : String(lic.key_id)],
      ['授权文件', lic.storage && lic.storage.ok ? lic.storage.file : '不可写（激活会失败，把这条发给卖家）'],
    ];
    licRows.forEach(([key, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = value;
      licList.append(dt, dd);
    });
  }

  async function loadAbout() {
    const about = await call('about');
    const lic = await call('license_state');
    if (about && lic) renderAbout(about, lic);
  }

  /* ───────────────────────────── 轮询 ───────────────────────────── */

  /* 一次调用同时拿回状态和新日志：pywebview 每次桥接都有开销，
     两个请求并成一个，点击按钮时明显不那么卡 */
  async function pollOnce() {
    const data = await call('poll');
    if (!data) return;
    if (data.status) applyStatus(data.status);
    appendLogs(data.lines);
  }

  function startPolling() {
    if (timer) return;
    timer = setInterval(pollOnce, POLL_MS);
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  async function refreshAll() {
    await refreshStatus();
    await loadRooms();
    await loadConfig();
    await loadAbout();
  }

  /* ───────────────────────────── 事件绑定 ───────────────────────────── */

  function switchTab(name) {
    document.querySelectorAll('.tabs button').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === name);
    });
    document.querySelectorAll('section[data-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.panel !== name);
    });
    if (name === 'about') loadAbout();
    if (name === 'settings') refreshStatus();
    if (name === 'logs') {
      const view = $('log-view');
      view.scrollTop = view.scrollHeight;
    }
  }

  function bindTabs() {
    document.querySelectorAll('.tabs button').forEach((button) => {
      button.addEventListener('click', () => switchTab(button.dataset.tab));
    });
  }

  function bindEvents() {
    $('btn-activate').addEventListener('click', doActivate);
    $('gate-input').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') doActivate();
    });
    $('btn-copy-machine').addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText($('gate-machine').textContent);
        toast('机器码已复制');
      } catch {
        toast('复制失败，请手动选中复制', true);
      }
    });

    $('btn-toggle').addEventListener('click', toggleRecording);
    $('btn-add-room').addEventListener('click', addRoom);
    $('room-url').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') addRoom();
    });
    $('btn-clear-rooms').addEventListener('click', clearRooms);
    $('btn-open-folder').addEventListener('click', (event) =>
      withBusy(event.currentTarget, () => call('open_path', '')));
    $('btn-hint-goto').addEventListener('click', () => switchTab('logs'));
    $('btn-hint-proxy').addEventListener('click', () => {
      switchTab('settings');
      detectProxies();
    });
    $('btn-proxy-detect').addEventListener('click', detectProxies);
    $('btn-log-clear').addEventListener('click', () => {
      logBuffer = [];
      $('log-view').textContent = '';
    });
    $('btn-save-config').addEventListener('click', saveConfig);
    $('btn-open-gate').addEventListener('click', () => showGate('输入新的卡密以替换当前授权'));
  }

  /* ───────────────────────────── 启动 ───────────────────────────── */

  whenReady(async () => {
    bindTabs();
    bindEvents();
    $('gate-msg').textContent = '正在检查授权…';

    const about = await call('about');
    if (about) $('app-ver').textContent = `v${about.version}`;

    const configured = await call('license_configured');
    if (configured === false) {
      showGate('发卡密钥未配置：请先在开发机上运行 tools/gen_secret.py 再打包', true);
      return;
    }
    refreshLicense();
  });
})();
