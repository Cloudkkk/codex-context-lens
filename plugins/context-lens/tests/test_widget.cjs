// Synthetic DOM integration: execute the actual widget without opening an app.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
  constructor(tag) {
    this.tag = tag; this.attrs = new Map(); this.children = []; this.style = {}; this.events = {};
    this.dataset = new Proxy({}, { get: (_, key) => this.getAttribute('data-' + key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())),
      set: (_, key, value) => { this.setAttribute('data-' + key.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), value); return true; } });
  }
  set id(value) { this.setAttribute('id', value); } get id() { return this.getAttribute('id') || ''; }
  set className(value) { this.setAttribute('class', value); } get className() { return this.getAttribute('class') || ''; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(''); }
  setAttribute(key, value) { this.attrs.set(key, String(value)); }
  getAttribute(key) { return this.attrs.has(key) ? this.attrs.get(key) : null; }
  removeAttribute(key) { this.attrs.delete(key); }
  matches(selector) {
    return selector.split(',').some(raw => {
      const s = raw.trim();
      if (s[0] === '#') return this.id === s.slice(1);
      if (s[0] === '.') return this.className.split(/\s+/).includes(s.slice(1));
      const attrs = [...s.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)];
      return attrs.length > 0 && attrs.every(([, key, value]) => this.attrs.has(key) && (value === undefined || this.getAttribute(key) === value));
    });
  }
  closest(selector) { for (let n = this; n; n = n.parentElement) if (n.matches(selector)) return n; return null; }
  querySelectorAll(selector) { return this.children.flatMap(n => [...(n.matches(selector) ? [n] : []), ...n.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  append(...nodes) { for (const n of nodes) { n.remove(); n.parentElement = this; this.children.push(n); } }
  insertBefore(node, reference) { node.remove(); node.parentElement = this; this.children.splice(this.children.indexOf(reference), 0, node); }
  remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
  replaceChildren(...nodes) { for (const n of [...this.children]) n.remove(); this.text = ''; this.append(...nodes); }
  get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
  get isConnected() { for (let n = this; n; n = n.parentElement) if (n.root) return true; return false; }
  getClientRects() {
    for (let n = this; n; n = n.parentElement) if (n.attrs.has('hidden') || n.style.display === 'none') return [];
    return this.isConnected ? [{}] : [];
  }
  getBoundingClientRect() { return { left: 10, top: 200, bottom: 224, width: 420, height: 200 }; }
  addEventListener(name, callback) { this.events[name] = callback; } removeEventListener(name) { delete this.events[name]; }
  attachShadow() { return this.shadowRoot = new Element('shadow'); }
  contains(node) { for (let n = node; n; n = n.parentElement) if (n === this) return true; return false; }
}
function fixture(id, turns, hiddenTurns = []) {
  const root = new Element('html'); root.root = true;
  const head = new Element('head'), body = new Element('body'); root.append(head, body);
  const doc = { head, body, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
    querySelectorAll: s => root.querySelectorAll(s), querySelector: s => root.querySelector(s), addEventListener() {}, removeEventListener() {} };
  const sidebar = new Element('div'); sidebar.setAttribute('data-app-action-sidebar-thread-row', '');
  sidebar.setAttribute('data-app-action-sidebar-thread-active', 'true');
  sidebar.setAttribute('data-app-action-sidebar-thread-id', 'local:' + id); body.append(sidebar);
  const chat = new Element('section'); body.append(chat);
  function replies(container, ids) {
    for (const turn of ids) {
      const wrapper = new Element('article'); wrapper.className = 'group'; wrapper.setAttribute('data-turn-key', 'local-marker-' + turn);
      wrapper.setAttribute('data-content-search-turn-key', turn);
      const message = new Element('div'); message.setAttribute('data-local-conversation-final-assistant', ''); message.textContent = 'A table rendered without Markdown separators';
      const row = new Element('div'), stamp = new Element('span'); stamp.setAttribute('data-assistant-message-sent-time', ''); stamp.textContent = '12:00'; row.append(stamp);
      wrapper.append(message, row); container.append(wrapper);
    }
  }
  replies(chat, turns);
  const hidden = new Element('section'); hidden.setAttribute('hidden', ''); body.append(hidden); replies(hidden, hiddenTurns);
  const requests = [];
  const context = { document: doc, setTimeout, clearTimeout, innerHeight: 900, innerWidth: 1200,
    MutationObserver: class { observe() {} disconnect() {} },
    getComputedStyle: node => ({ visibility: node.style.visibility || 'visible' }),
    addEventListener() {}, removeEventListener() {}, __contextLensRequest: raw => requests.push(JSON.parse(raw)) };
  context.window = context;
  vm.createContext(context);
  for (const file of ['matching.js', 'overlay.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../web', file), 'utf8'), context);
  return { api: context.__codexContextLens, doc, sidebar, requests,
    switch(id, ids) { sidebar.setAttribute('data-app-action-sidebar-thread-id', 'local:' + id); chat.replaceChildren(); replies(chat, ids); } };
}
const tick = () => new Promise(resolve => setTimeout(resolve, 100));
function payload(f, sessionId, ids) {
  return { viewToken: f.api.status().viewToken, sessionId, resolutionReason: 'turn_ids',
    turns: ids.map(turn_id => ({ turn_id, completed: true, used: 100, capacity: 1000 })) };
}

test('draft-only page mounts both completed replies using real session UUID', async t => {
  const f = fixture('client-new-thread:draft', ['one', 'two']); t.after(() => f.api.dispose());
  assert.equal(f.api.update(payload(f, 'real-session', ['one', 'two'])), true);
  await tick();
  assert.equal(f.api.status().buttons, 2);
  assert.equal(f.api.status().sessionId, 'real-session');
  assert.equal(f.api.status().diagnostics.reason, 'mounted');
  assert.equal(f.doc.querySelector('[data-context-lens-button]').dataset.sessionId, 'real-session');
});
test('hidden cached replies neither influence session resolution nor get buttons', async t => {
  const f = fixture('client-new-thread:draft', ['visible-turn'], ['hidden-other-turn']); t.after(() => f.api.dispose());
  assert.deepEqual(Array.from(f.api.status().visibleTurnIds), ['visible-turn']);
  f.api.update(payload(f, 'visible-chat', ['visible-turn'])); await tick();
  assert.equal(f.api.status().buttons, 1);
  assert.equal(f.api.status().diagnostics.hiddenCandidates, 1);
});
test('quick chat switch rejects late old payload and mounts only current chat', async t => {
  const f = fixture('client-new-thread:same', ['a']); t.after(() => f.api.dispose());
  const old = payload(f, 'chat-a', ['a']); f.api.update(old); await tick();
  f.switch('client-new-thread:same', ['b']);
  assert.equal(f.api.status().buttons, 0);
  assert.equal(f.api.update(old), false);
  f.api.update(payload(f, 'chat-b', ['b'])); await tick();
  assert.equal(f.api.status().buttons, 1);
  assert.equal(f.api.status().sessionId, 'chat-b');
  assert.equal(f.doc.querySelector('[data-context-lens-button]').dataset.turnId, 'b');
});
test('two windows with separate chats update without sharing routing state', async t => {
  const a = fixture('client-new-thread:a', ['a']), b = fixture('client-new-thread:b', ['b']);
  t.after(() => { a.api.dispose(); b.api.dispose(); });
  const first = payload(a, 'chat-a', ['a']), second = payload(b, 'chat-b', ['b']);
  a.api.update(first); b.api.update(second); assert.equal(b.api.update(first), false); await tick();
  assert.equal(a.api.status().sessionId, 'chat-a'); assert.equal(b.api.status().sessionId, 'chat-b');
  assert.equal(a.api.status().buttons, 1); assert.equal(b.api.status().buttons, 1);
});
test('unknown log owner waits, then mounts when a later payload resolves it', async t => {
  const f = fixture('client-new-thread:new', ['pending']); t.after(() => f.api.dispose());
  f.api.update({ viewToken: f.api.status().viewToken, sessionId: null, turns: [], resolutionReason: 'waiting_for_turn_log' });
  await tick(); assert.equal(f.api.status().buttons, 0);
  assert.equal(f.api.status().diagnostics.reason, 'waiting_for_turn_log');
  f.api.update(payload(f, 'new-real-chat', ['pending'])); await tick(); assert.equal(f.api.status().buttons, 1);
});
test('visible real turns work without any composer or sidebar session marker', async t => {
  const f = fixture('unused', ['real-turn']); t.after(() => f.api.dispose()); f.sidebar.remove();
  assert.equal(f.api.status().activeThreadId, null);
  f.api.update(payload(f, 'real-chat', ['real-turn'])); await tick();
  assert.equal(f.api.status().sessionId, 'real-chat'); assert.equal(f.api.status().buttons, 1);
});
test('same real chat can appear in two windows with distinct visible turns', async t => {
  const a = fixture('client-new-thread:a', ['one']), b = fixture('client-new-thread:b', ['two']);
  t.after(() => { a.api.dispose(); b.api.dispose(); });
  a.api.update(payload(a, 'same-chat', ['one', 'two'])); b.api.update(payload(b, 'same-chat', ['one', 'two'])); await tick();
  assert.equal(a.api.status().sessionId, 'same-chat'); assert.equal(b.api.status().sessionId, 'same-chat');
  assert.equal(a.api.status().buttons, 1); assert.equal(b.api.status().buttons, 1);
});
test('late hover response cannot open the prior chat after switching', async t => {
  const f = fixture('draft', ['a']); t.after(() => f.api.dispose());
  f.api.update(payload(f, 'chat-a', ['a'])); await tick();
  f.doc.querySelector('[data-context-lens-button]').events.mouseenter();
  await new Promise(resolve => setTimeout(resolve, 160));
  const request = f.requests.find(r => r.turn_id === 'a'); assert.ok(request);
  assert.equal(request.session_id, 'chat-a'); assert.equal(request.view_token, f.api.status().viewToken);
  f.switch('draft', ['b']); f.api.update(payload(f, 'chat-b', ['b']));
  f.api.receive({ id: request.id, report: { session: { id: 'chat-a' } } }); await tick();
  assert.equal(f.api.status().sessionId, 'chat-b'); assert.equal(f.api.status().buttons, 1);
  assert.equal(f.doc.querySelector('#codex-context-lens-popover').style.display, 'none');
});
