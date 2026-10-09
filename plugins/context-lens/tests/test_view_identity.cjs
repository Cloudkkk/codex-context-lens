const { test } = require('node:test');
const assert = require('node:assert/strict');
const { viewToken, acceptsView } = require('../web/matching.js');
const view = (id, turns) => ({ viewToken: viewToken(id, turns) });

test('a draft UI identity can bind a distinct real session UUID', () => {
  const current = view('client-new-thread:draft', ['turn-a']);
  assert.equal(acceptsView({ ...current, sessionId: 'real-uuid' }, current), true);
});
test('late response for the previous chat is rejected after switching', () => {
  assert.equal(acceptsView({ ...view('chat-a', ['turn-a']), sessionId: 'chat-a' }, view('chat-b', ['turn-b'])), false);
});
test('a reused draft alias cannot accept another chat data', () => {
  assert.equal(acceptsView(view('client-new-thread:reused', ['turn-a']), view('client-new-thread:reused', ['turn-b'])), false);
});
test('new visible turns require fresh resolution even without a composer', () => {
  assert.equal(acceptsView(view(null, ['old']), view(null, ['new'])), false);
});
test('virtual list order and duplicate DOM markers do not change identity', () => {
  assert.equal(viewToken('chat-a', ['two', 'one', 'one']), viewToken('chat-a', ['one', 'two']));
});
test('same session in separate windows retains independent visible turn sets', () => {
  const left = view('chat-a', ['one']), right = view('chat-a', ['two']);
  assert.equal(acceptsView(left, right), false);
  assert.equal(acceptsView(left, left), true);
});
