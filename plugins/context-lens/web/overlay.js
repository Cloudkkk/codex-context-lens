(() => {
  'use strict';
  const key = '__codexContextLens';
  if (window[key]) return { installed: true, reused: true };
  const marker = 'data-context-lens-button';
  const scopeMarker = 'data-context-lens-hover-scope', scopes = new Set();
  const buttonStyle = document.createElement('style');
  buttonStyle.id = 'codex-context-lens-button-style';
  buttonStyle.textContent = `
    button[${marker}]{display:inline-flex;align-items:center;justify-content:center;gap:4px;height:24px;padding:2px 4px;margin-inline-start:2px;border:0;border-radius:5px;background:transparent;color:var(--color-text-tertiary,light-dark(#8f8f8f,#afafaf));font-family:inherit;font-size:12px;font-weight:400;line-height:1;cursor:pointer;opacity:0;pointer-events:none;white-space:nowrap;flex-shrink:0;transition:background .12s,opacity .12s}
    [${scopeMarker}]:hover button[${marker}],[${scopeMarker}]:focus-within button[${marker}],button[${marker}][aria-expanded="true"]{opacity:1;pointer-events:auto}
    button[${marker}]:hover,button[${marker}]:focus-visible{background:color-mix(in srgb,currentColor 8%,transparent);opacity:1}
    button[${marker}] svg{width:16px;height:16px;display:block;flex-shrink:0}
    button[${marker}] [data-context-lens-percentage]{font-variant-numeric:tabular-nums}
  `;
  document.head.append(buttonStyle);
  let state = { sessionId: null, turns: [] }, activeButton = null, pinned = false;
  let turnIndex = new Map();
  let closeTimer = null, openTimer = null, scanTimer = null, sequence = 0;
  let diagnostics = { candidates: 0, matchedTurns: 0, actionRows: 0, reason: 'starting' };
  const awaiting = new Map(), cache = new Map();
  const host = document.createElement('div');
  host.id = 'codex-context-lens-popover';
  host.style.cssText = 'position:fixed;z-index:2147483000;display:none;';
  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color-scheme:light dark}
    *{box-sizing:border-box}.panel{width:min(420px,calc(100vw - 24px));max-height:min(690px,calc(100vh - 24px));overflow:auto;background:light-dark(#fff,#202126);color:light-dark(#24262b,#e8e9ee);border:1px solid light-dark(#e3e5e9,#3f424a);border-radius:14px;box-shadow:0 10px 42px #0003;padding:20px;font-size:13px;line-height:1.5}
    .head{display:flex;align-items:center;justify-content:space-between;gap:16px}.title{font-size:16px;font-weight:600}.value{font-size:15px;font-variant-numeric:tabular-nums;white-space:nowrap}.sub{font-size:11px;color:light-dark(#838791,#a7aab5);margin-top:5px}.pill{font-size:10px;padding:2px 6px;border-radius:5px;background:light-dark(#f0f3f7,#343843);margin-left:6px}
    .bar{display:flex;height:8px;border-radius:5px;background:light-dark(#f0f1f3,#383a40);overflow:hidden;margin:16px 0}.segment{min-width:0;height:100%;flex-shrink:0}.row{display:grid;grid-template-columns:16px minmax(0,1fr) auto 50px;gap:7px;align-items:center;min-height:32px}.dot{width:10px;height:10px;border-radius:3px}.name{font-size:13px}.num{font-variant-numeric:tabular-nums}.pct{text-align:right;color:light-dark(#878b93,#b0b4c0);font-variant-numeric:tabular-nums;font-size:12px}
    details>summary{list-style:none;cursor:pointer}details>summary::-webkit-details-marker{display:none}.expand{color:light-dark(#8c929d,#abb1bd);font-size:10px;margin-left:5px}.items{padding:3px 0 8px 23px;font-size:11px;color:light-dark(#777e89,#a8adba)}.item{display:flex;justify-content:space-between;gap:12px;padding:3px 0}.item span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.divider{height:1px;background:light-dark(#edf0f4,#383c45);margin:13px 0}.mini{display:grid;grid-template-columns:1fr 1fr;gap:10px}.mini b{display:block;font-size:15px;font-weight:500;font-variant-numeric:tabular-nums}.note{font-size:11px;color:light-dark(#80858e,#a4a9b5);margin-top:12px}.warn{padding:9px;border-radius:7px;background:light-dark(#fff5e7,#493b28);font-size:11px;margin-top:10px}.close{border:0;background:transparent;color:inherit;font-size:18px;cursor:pointer;padding:0 3px}.foot{display:flex;justify-content:space-between;gap:8px;margin-top:12px;font-size:10px;color:light-dark(#979ca5,#a1a7b3)}.free{color:light-dark(#8b929e,#a4a9b5)}
  `;
  const panel = document.createElement('section');
  panel.className = 'panel'; panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '上下文用量明细');
  shadow.append(style, panel); document.body.append(host);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const format = n => n == null ? '—' : n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  const percent = n => n == null ? '—' : n.toFixed(1) + '%';
  function threadId() {
    const row = document.querySelector('[data-app-action-sidebar-thread-row][data-app-action-sidebar-thread-active="true"], [data-app-action-sidebar-thread-row][aria-current="page"]');
    const composerIds = Array.from(document.querySelectorAll('[data-conversation-id]'))
      .filter(node => node.getClientRects().length && !node.closest('[hidden], [aria-hidden="true"]'))
      .map(node => node.getAttribute('data-conversation-id'));
    return window.__codexContextLensMatching.resolveSessionId(composerIds, row?.getAttribute('data-app-action-sidebar-thread-id'));
  }
  function place() {
    if (!activeButton?.isConnected) return hide();
    const r = activeButton.getBoundingClientRect();
    const above = r.top - 22, below = innerHeight - r.bottom - 22;
    const useAbove = above >= below;
    panel.style.maxHeight = Math.max(170, Math.min(690, useAbove ? above : below)) + 'px';
    const h = host.getBoundingClientRect();
    host.style.left = Math.max(12, Math.min(r.left, innerWidth - h.width - 12)) + 'px';
    host.style.top = Math.max(12, useAbove ? r.top - h.height - 9 : Math.min(r.bottom + 9, innerHeight - h.height - 12)) + 'px';
  }
  function hide() { host.style.display = 'none'; activeButton?.setAttribute('aria-expanded', 'false'); activeButton = null; pinned = false; clearTimeout(openTimer); }
  function delayedHide() { clearTimeout(closeTimer); if (!pinned) closeTimer = setTimeout(hide, 180); }
  function render(report) {
    panel.replaceChildren();
    const c = report.context, u = report.latest_usage || {};
    const header = el('div', 'head');
    header.append(el('div', 'title', 'Context window'), el('div', 'value', `${format(c.used)} / ${format(c.capacity)} (${percent(c.percent)})`));
    panel.append(header);
    const bar = el('div', 'bar');
    for (const b of report.buckets) { if (!b.tokens || !c.capacity) continue; const part = el('div', 'segment'); part.style.width = Math.min(100, b.tokens / c.capacity * 100) + '%'; part.style.background = b.color; part.title = b.label; bar.append(part); }
    panel.append(bar);
    for (const b of report.buckets) {
      if (!b.tokens) continue;
      const row = el('div', 'row'), dot = el('span', 'dot'); dot.style.background = b.color;
      const name = el('span', 'name', b.label);
      row.append(dot, name, el('span', 'num', (b.kind === 'estimate' ? '≈ ' : '') + format(b.tokens)), el('span', 'pct', percent(b.percent)));
      panel.append(row);
    }
    const free = el('div', 'row free'), fdot = el('span', 'dot'); fdot.style.background = 'light-dark(#e6e9ee,#4a4e59)';
    free.append(fdot, el('span', 'name', '剩余空间'), el('span', 'num', format(c.free)), el('span', 'pct', c.capacity && c.free != null ? percent(c.free / c.capacity * 100) : '—')); panel.append(free);
    panel.append(el('div', 'divider'));
    const mini = el('div', 'mini');
    for (const [label, value] of [['缓存输入（含在输入内）', u.cached_input_tokens], ['输出（含推理）', u.output_tokens]]) { const item = el('div', 'sub', label); item.append(el('b', '', format(value))); mini.append(item); }
    panel.append(mini);
    if (c.estimate_scaled || c.used == null) panel.append(el('div', 'warn', c.used == null ? '暂无这轮请求的用量记录。' : '可见文本估算超过输入量，已按比例封顶。'));
    const foot = el('div', 'foot'); foot.append(el('span', '', report.session.model), el('span', '', c.usage_timestamp ? new Date(c.usage_timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : '等待日志'));
    panel.append(foot);
  }
  function ask(turnId) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { awaiting.delete(id); reject(new Error('本地插件暂未连接')); }, 6000);
      awaiting.set(id, { resolve, reject, timer });
      window.__contextLensRequest(JSON.stringify({ id, session_id: state.sessionId, turn_id: turnId }));
    });
  }
  async function show(button) {
    clearTimeout(closeTimer);
    if (activeButton && activeButton !== button) activeButton.setAttribute('aria-expanded', 'false');
    activeButton = button;
    button.setAttribute('aria-expanded', 'true'); host.style.display = 'block';
    panel.replaceChildren(el('div', 'title', '上下文明细'), el('div', 'note', '正在读取本地记录…')); place();
    const identity = `${state.sessionId}:${button.dataset.turnId}`;
    try {
      const hit = cache.get(identity), report = hit && Date.now() - hit.time < 3000 ? hit.report : await ask(button.dataset.turnId);
      cache.set(identity, { report, time: Date.now() });
      if (activeButton === button) { render(report); place(); }
    } catch (error) { if (activeButton === button) panel.replaceChildren(el('div', 'title', '暂无明细'), el('div', 'note', error.message)); }
  }
  function matchTurn(node) {
    const container = node.closest('[data-turn-key]') || node;
    const turnId = node.closest('[data-content-search-turn-key]')?.getAttribute('data-content-search-turn-key');
    const keys = [container.getAttribute('data-turn-key'), node.getAttribute('data-content-search-assistant-turn-key'), node.id].filter(Boolean);
    return window.__codexContextLensMatching.matchTurn(keys, turnId ? '' : node.textContent, state.turns, turnId, turnIndex);
  }
  function scan() {
    scanTimer = null;
    diagnostics = { candidates: 0, matchedTurns: 0, actionRows: 0, hiddenCandidates: 0, idMatches: 0, textMatches: 0, unmatchedTurns: 0, reason: 'scanning' };
    const active = threadId();
    if (active && active !== state.sessionId) { diagnostics.reason = 'session_mismatch'; document.querySelectorAll(`[${marker}]`).forEach(n => n.remove()); return hide(); }
    const nodes = document.querySelectorAll('[data-local-conversation-final-assistant], [data-content-search-assistant-turn-key]');
    diagnostics.candidates = nodes.length;
    const keep = new Set(), rows = new Set();
    for (const node of nodes) {
      // Cached workspaces stay in the DOM after switching chats.
      if (!node.getClientRects().length || node.closest('[hidden], [aria-hidden="true"], [inert]') || getComputedStyle(node).visibility === 'hidden') {
        diagnostics.hiddenCandidates++;
        continue;
      }
      const wrapper = node.closest('[data-turn-key]') || node;
      const stamp = wrapper.querySelector('[data-assistant-message-sent-time]');
      const row = stamp?.parentElement;
      if (row) diagnostics.actionRows++;
      if (!row || rows.has(row)) continue;
      const turn = matchTurn(node);
      if (!turn || !turn.completed) { diagnostics.unmatchedTurns++; continue; }
      diagnostics.matchedTurns++;
      if (node.closest('[data-content-search-turn-key]')?.getAttribute('data-content-search-turn-key')) diagnostics.idMatches++;
      else diagnostics.textMatches++;
      rows.add(row);
      const scope = row.closest('.group') || wrapper;
      scope.setAttribute(scopeMarker, ''); scopes.add(scope);
      let button = row.querySelector(`[${marker}]`);
      if (!button) {
        button = document.createElement('button'); button.type = 'button'; button.setAttribute(marker, '');
        button.setAttribute('aria-label', '查看上下文用量明细'); button.setAttribute('aria-haspopup', 'dialog');
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('width', '16'); icon.setAttribute('height', '16');
        icon.setAttribute('fill', 'none'); icon.setAttribute('stroke', 'currentColor'); icon.setAttribute('stroke-width', '1.5');
        icon.setAttribute('stroke-linecap', 'round'); icon.setAttribute('stroke-linejoin', 'round');
        icon.setAttribute('aria-hidden', 'true'); icon.setAttribute('focusable', 'false');
        for (const d of ['M10 3.25A8.9 8.9 0 1 0 20.75 14H10V3.25Z', 'M14 3.25V10H20.75A8.9 8.9 0 0 0 14 3.25Z']) {
          const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); icon.append(path);
        }
        const percentage = el('span'); percentage.setAttribute('data-context-lens-percentage', '');
        button.append(icon, percentage);
        button.addEventListener('mouseenter', () => { clearTimeout(closeTimer); openTimer = setTimeout(() => show(button), 140); });
        button.addEventListener('mouseleave', () => { clearTimeout(openTimer); delayedHide(); });
        button.addEventListener('focus', () => show(button)); button.addEventListener('blur', delayedHide);
        button.addEventListener('click', event => { event.stopPropagation(); if (activeButton === button && pinned) hide(); else { pinned = true; show(button); } });
        row.insertBefore(button, stamp);
      }
      button.dataset.turnId = turn.turn_id;
      const label = turn.capacity ? (turn.used / turn.capacity * 100).toFixed(0) + '%' : format(turn.used);
      const percentage = button.querySelector('[data-context-lens-percentage]');
      if (percentage.textContent !== label) percentage.textContent = label;
      if (button.nextElementSibling !== stamp) row.insertBefore(button, stamp);
      keep.add(button);
    }
    document.querySelectorAll(`[${marker}]`).forEach(button => { if (!keep.has(button)) { if (button === activeButton) hide(); button.remove(); } });
    for (const scope of scopes) { if (!scope.isConnected || !scope.querySelector(`[${marker}]`)) { scope.removeAttribute(scopeMarker); scopes.delete(scope); } }
    diagnostics.reason = keep.size ? 'mounted' : !state.turns.length ? 'no_logged_turns' : !nodes.length ? 'no_message_nodes' : !diagnostics.actionRows ? 'no_action_rows' : 'no_turn_match';
  }
  function schedule() { if (!scanTimer) scanTimer = setTimeout(scan, 80); }
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-turn-key', 'data-content-search-turn-key', 'data-app-action-sidebar-thread-active'] });
  host.addEventListener('mouseenter', () => clearTimeout(closeTimer)); host.addEventListener('mouseleave', delayedHide);
  host.addEventListener('pointerdown', () => { pinned = true; clearTimeout(closeTimer); });
  const onKey = event => { if (event.key === 'Escape') hide(); };
  const onClick = event => { if (activeButton && !host.contains(event.target) && !activeButton.contains(event.target)) hide(); };
  document.addEventListener('keydown', onKey); document.addEventListener('pointerdown', onClick);
  window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
  window[key] = {
    update(payload) { if (state.sessionId !== payload.sessionId) { cache.clear(); hide(); } state = payload; turnIndex = window.__codexContextLensMatching.indexTurns(state.turns); schedule(); },
    receive(payload) { const task = awaiting.get(payload.id); if (!task) return; clearTimeout(task.timer); awaiting.delete(payload.id); payload.error ? task.reject(new Error(payload.error)) : task.resolve(payload.report); },
    status() { return { activeThreadId: threadId(), buttons: document.querySelectorAll(`[${marker}]`).length, sessionId: state.sessionId, turns: state.turns.length, diagnostics }; },
    dispose() { observer.disconnect(); clearTimeout(scanTimer); clearTimeout(closeTimer); clearTimeout(openTimer); for (const task of awaiting.values()) { clearTimeout(task.timer); task.reject(new Error('插件已停止')); } awaiting.clear(); document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', onClick); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); document.querySelectorAll(`[${marker}]`).forEach(b => b.remove()); for (const scope of scopes) scope.removeAttribute(scopeMarker); scopes.clear(); buttonStyle.remove(); host.remove(); delete window[key]; }
  };
  return { installed: true };
})();
