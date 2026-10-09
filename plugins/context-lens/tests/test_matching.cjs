const { test } = require('node:test');
const assert = require('node:assert/strict');
const { canonical, matchTurn, resolveSessionId } = require('../web/matching.js');
const turn = (id, text) => ({ turn_id: id, text_prefix: text });

test('Markdown paragraphs and list markers match concatenated DOM text', () => {
  const t = turn('one', '是的，刚确认：\n\n- **已安装**：Context Lens **0.2.0**\n- **已启用**\n- **面板监控已连接**桌面窗口（`attached`）\n\n不需要重新安装。');
  const rendered = '是的，刚确认：已安装：Context Lens 0.2.0已启用面板监控已连接桌面窗口（attached）不需要重新安装。';
  assert.equal(matchTurn(['turn-local-index'], rendered, [t]), t);
});
test('long local link is removed before prefix clipping', () => {
  const t = turn('one', '最简单是直接把[插件 ZIP 包](/Users/' + 'long-directory/'.repeat(30) + 'plugin.zip)发给别人。包里不包含你的会话日志，每个人读取自己电脑的目录。');
  assert.equal(matchTurn([], '最简单是直接把插件 ZIP 包发给别人。包里不包含你的会话日志，每个人读取自己电脑的目录。', [t]), t);
});
test('numbered lists, headings, and blockquotes match rendered text', () => {
  const source = '## 安装方法\n\n1. 打开设置页面并选择 Hooks\n2. 信任插件的 SessionStart\n> 之后新建聊天即可启动面板';
  assert.equal(canonical(source), canonical('安装方法打开设置页面并选择 Hooks信任插件的 SessionStart之后新建聊天即可启动面板'));
});
test('code fence language does not become a match requirement', () => {
  assert.equal(canonical('执行这条命令：\n```sh\ncodex plugin list\n```'), canonical('执行这条命令：codex plugin list'));
});
test('duplicate reply wording stays unmatched', () => {
  assert.equal(matchTurn([], '这是一段重复出现的较长回复内容。', [turn('one', '这是一段重复出现的较长回复内容。'), turn('two', '这是一段重复出现的较长回复内容。')]), null);
});
test('exact ID disambiguates identical wording', () => {
  const turns = [turn('one', '这是一段重复出现的较长回复内容。'), turn('two', '这是一段重复出现的较长回复内容。')];
  assert.equal(matchTurn(['local:two'], '', turns), turns[1]);
});
test('short generic replies and unrelated nodes are not guessed', () => {
  assert.equal(matchTurn([], '好的', [turn('one', '好的')]), null);
  assert.equal(matchTurn(['turn-0'], '另一条完全不同的较长回复内容。', [turn('one', '这条回复只属于第一轮请求。')]), null);
});
test('preview image alt text does not prevent matching a short reply', () => {
  const t = turn('one', '已删除这两段文案，**0.2.5 已生效**，分发包也已同步更新。\n\n![更新后的面板预览（模拟数据）](/Users/example/面板预览.png)');
  assert.equal(matchTurn([], '已删除这两段文案，0.2.5 已生效，分发包也已同步更新。', [t]), t);
});
test('images embedded between paragraphs are excluded from matching', () => {
  assert.equal(canonical('第一段回复内容。\n![图示](image.png)\n第二段回复内容。'), canonical('第一段回复内容。第二段回复内容。'));
});
test('visible composer overrides a stale sidebar draft selection', () => {
  assert.equal(resolveSessionId(['local:real-session'], 'client-new-thread:draft'), 'real-session');
});
test('ambiguous visible composers require a matching sidebar selection', () => {
  assert.equal(resolveSessionId(['one', 'two'], 'client-new-thread:draft'), null);
  assert.equal(resolveSessionId(['one', 'two'], 'local:two'), 'two');
});
test('sidebar remains a fallback when a composer is unavailable', () => {
  assert.equal(resolveSessionId([], 'local:session'), 'session');
  assert.equal(resolveSessionId([], null), null);
});
