/* Worker control is deliberately separate from the teaching and answer-review flows. */
(() => {
  'use strict';

  const endpoint = '/api/worker/control';
  const labels = {
    OBSERVE_ONLY: '仅观察', ASSISTED: '人工辅助', CONTROLLED_AUTO: '受控自动',
    AUTO_ACTIVE: '自动运行', REQUESTED: '已申请接管', QUIESCING: '正在安全停下',
    OWNED: '人工接管中', RESUME_CHECK: '恢复前检查',
    HEALTHY: '健康', LOGIN_REQUIRED: '需要登录', VERIFICATION_REQUIRED: '需要人工验证',
    RATE_LIMITED: '受限', ACCESS_DENIED: '访问被拒绝', PAGE_CHANGED: '页面变化',
    DESKTOP_UNAVAILABLE: '桌面不可用', UNKNOWN: '状态不明'
  };
  const accountActions = [
    ['stop', '暂停自动化'], ['request_takeover', '申请接管'],
    ['confirm_takeover', '确认已停止，开始接管'], ['request_resume', '发起恢复前检查'],
    ['resume', '恢复自动化']
  ];
  const commandActions = [
    ['cancel', '取消任务'], ['verify', '核验发送结果'],
    ['mark_manually_handled', '标记人工已处理']
  ];
  let snapshot = null;
  let busy = false;
  let status;
  let body;
  let panel;

  function node(tag, value, className) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  }

  function stateLabel(value) { return labels[value] || String(value || '未知'); }
  function allowed(record, action) {
    return Array.isArray(record.available_actions) && record.available_actions.includes(action);
  }
  function setStatus(message, error = false) {
    if (error) panel.hidden = false;
    status.textContent = message;
    status.classList.toggle('error', error);
  }
  function button(action, label, record, payload, extraDisabled = false) {
    const item = node('button', label, action === 'stop' ? 'danger' : 'secondary');
    item.type = 'button';
    item.dataset.workerAction = action;
    item.disabled = busy || !allowed(record, action) || extraDisabled;
    item.addEventListener('click', () => submit(payload));
    return item;
  }
  function actions(items) {
    const group = node('div', undefined, 'actions');
    items.forEach(item => group.append(item));
    return group;
  }
  function textLine(label, value) {
    return node('p', `${label}：${value}`);
  }
  function workerFor(account) {
    if (!Array.isArray(snapshot.workers)) return null;
    return snapshot.workers.find(worker => worker.worker_id === account.active_worker ||
      worker.account_id === account.account_id) || null;
  }
  function renderAccount(account) {
    const box = node('article', undefined, 'panel');
    box.append(node('h3', `账号 ${account.account_id}`));
    const worker = workerFor(account);
    const phase = account.takeover || 'AUTO_ACTIVE';
    box.append(textLine('接管阶段', stateLabel(phase)));
    box.append(textLine('运行状态', account.stop ? '已暂停' : '未暂停'));
    box.append(textLine('桌面', worker ? (worker.gui_ready === true ? '可用' : '不可用，等待人工检查') : '未接入 Worker'));
    if (worker) box.append(textLine('Worker 健康', stateLabel(worker.health?.state || worker.health)));
    if (account.pause_reason) box.append(textLine('暂停原因', stateLabel(account.pause_reason)));
    if (account.quarantined) box.append(textLine('资源隔离', '是，需先核验未知操作'));
    const observed = worker?.health;
    const interactive = observed && typeof observed === 'object' &&
      observed.connected === true && observed.interactive_desktop === true &&
      observed.desktop_unlocked === true && observed.native_call_pending === false;
    const safeToConfirm = interactive && !account.quarantined &&
      (phase === 'REQUESTED' || phase === 'QUIESCING');
    const controls = accountActions.map(([action, label]) => button(action, label, account,
      { action, account_id: account.account_id }, action === 'confirm_takeover' && !safeToConfirm));
    box.append(actions(controls));
    if (phase === 'REQUESTED' || phase === 'QUIESCING') {
      box.append(textLine('提示', '申请后需等待自动操作停在安全检查点；此时尚未授予人工操作权。'));
    } else if (phase === 'RESUME_CHECK') {
      box.append(textLine('提示', '当前仅做只读健康检查与原动作核验，服务端确认后才能恢复。'));
    }
    return box;
  }
  function evidenceChoices(command) {
    // Only opaque, server-provided evidence references may be submitted.
    const source = command.evidence_refs;
    return Array.isArray(source) ? source.filter(value => typeof value === 'string' &&
      /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,199}$/.test(value)) : [];
  }
  function renderCommand(command) {
    const box = node('article', undefined, 'panel');
    box.append(node('h3', `命令 ${command.command_id}`));
    box.append(textLine('任务', command.task_id || '未提供'));
    box.append(textLine('账号', command.account_id || '未提供'));
    box.append(textLine('动作', command.action || '未知'));
    box.append(textLine('状态', command.status || '未知'));
    if (command.reason_code) box.append(textLine('原因', command.reason_code));
    const choices = evidenceChoices(command);
    const select = document.createElement('select');
    select.setAttribute('aria-label', `命令 ${command.command_id} 的核验依据`);
    select.append(node('option', '选择已登记的证据引用'));
    select.firstChild.value = '';
    choices.forEach(ref => {
      const option = node('option', ref);
      option.value = ref;
      select.append(option);
    });
    select.disabled = busy || !allowed(command, 'mark_manually_handled') || choices.length === 0;
    if (allowed(command, 'mark_manually_handled')) box.append(select);
    const controls = commandActions.map(([action, label]) => {
      const item = button(action, label, command,
        { action, command_id: command.command_id },
        action === 'mark_manually_handled' && choices.length === 0);
      if (action === 'mark_manually_handled') {
        item.addEventListener('click', event => { event.stopImmediatePropagation();
          if (select.value) submit({ action,
            command_id: command.command_id, evidence_ref: select.value });
        }, true);
        select.addEventListener('change', () => { item.disabled = busy || !select.value; });
        item.disabled = true;
      }
      return item;
    });
    box.append(actions(controls));
    return box;
  }
  function render() {
    body.replaceChildren();
    if (!snapshot || typeof snapshot !== 'object') return;
    body.append(textLine('权限模式', stateLabel(snapshot.mode || 'OBSERVE_ONLY')));
    const accounts = Array.isArray(snapshot.accounts) ? snapshot.accounts : [];
    const commands = Array.isArray(snapshot.commands) ? snapshot.commands : [];
    body.append(node('h3', '账号与接管'));
    if (accounts.length) accounts.forEach(account => body.append(renderAccount(account)));
    else body.append(textLine('当前状态', '尚未接入 Worker 账号；没有可执行的桌面命令。'));
    body.append(node('h3', '待核验命令'));
    if (commands.length) commands.forEach(command => body.append(renderCommand(command)));
    else body.append(textLine('当前状态', '暂无 Worker 命令。'));
  }
  async function decode(response) {
    let data;
    try { data = await response.json(); } catch { throw new Error('服务端返回格式异常'); }
    if (!response.ok) throw new Error(data.error || `操作失败（${response.status}）`);
    return data;
  }
  async function refresh() {
    const response = await fetch(endpoint, { cache: 'no-store', credentials: 'same-origin' });
    snapshot = await decode(response);
    // An unused single-machine setup needs no empty Worker controls. Keep any unresolved work visible.
    panel.hidden = ['workers', 'accounts', 'commands'].every(key =>
      Array.isArray(snapshot[key]) && snapshot[key].length === 0);
    render();
    setStatus('Worker 状态已更新。');
  }
  async function submit(payload) {
    if (busy || !snapshot) return;
    const csrf = snapshot.csrf_token;
    if (typeof csrf !== 'string' || !csrf) {
      setStatus('缺少操作凭据，请刷新状态。', true);
      return;
    }
    busy = true;
    render();
    setStatus('正在提交操作…');
    try {
      const result = await decode(await fetch(endpoint, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(payload) }));
      await refresh();
      if (result.result?.status === 'WAITING_HUMAN' || result.result?.verification_required === true) {
        setStatus('仍待可信的只读核验；未确认发送成功，也不会自动重试。', true);
      }
    } catch (error) {
      setStatus(error.message || '操作失败，请手动刷新并核验状态。', true);
    } finally {
      busy = false;
      render();
    }
  }
  function mount() {
    const main = document.querySelector('main');
    if (!main || document.getElementById('worker-control-panel')) return;
    panel = node('section', undefined, 'card');
    panel.id = 'worker-control-panel';
    panel.hidden = true;
    const heading = node('div', undefined, 'section-title');
    const title = node('div');
    title.append(node('h2', 'Worker 运行与人工接管'));
    title.append(node('p', '状态来自服务端；申请接管、确认停下和恢复前检查分开执行。'));
    heading.append(title);
    panel.append(heading);
    status = node('p', '正在读取 Worker 状态…');
    status.id = 'worker-control-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    panel.append(status);
    const reload = node('button', '刷新 Worker 状态', 'secondary');
    reload.type = 'button';
    reload.addEventListener('click', () => refresh().catch(error => setStatus(error.message || '读取失败', true)));
    document.getElementById('refresh')?.addEventListener('click', () =>
      refresh().catch(error => setStatus(error.message || '读取失败', true)));
    panel.append(actions([reload]));
    body = node('div');
    body.id = 'worker-control-body';
    panel.append(body);
    main.prepend(panel);
    refresh().catch(error => setStatus(error.message || '读取失败', true));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
