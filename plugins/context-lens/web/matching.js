(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.__codexContextLensMatching = api;
})(globalThis, function () {
  'use strict';
  function canonical(text) {
    return String(text || '').normalize('NFKC')
      // Image alt text is an accessibility attribute, not DOM textContent.
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/^\s*(```|~~~)[^\n]*$/gm, '')
      .replace(/^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]*|[-+*][ \t]+|\d+[.)][ \t]+)/gm, '')
      .replace(/[`*~]/g, '')
      // DOM textContent does not insert Markdown paragraph/list whitespace.
      .replace(/\s+/g, '');
  }
  function indexTurns(turns) {
    const index = new Map();
    for (const turn of turns) {
      if (!turn.turn_id) continue;
      // Duplicate IDs cannot identify a unique usage record.
      index.set(turn.turn_id, index.has(turn.turn_id) ? null : turn);
    }
    return index;
  }
  function matchTurn(keys, text, turns, turnId = null, index = null) {
    // A real turn ID is authoritative, even while its log is still arriving.
    if (turnId) return (index || indexTurns(turns)).get(turnId) || null;
    const exact = turns.filter(turn => keys.some(key => key === turn.turn_id || key.endsWith(turn.turn_id)
      || (turn.message_id && key.endsWith(turn.message_id))));
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) return null;
    const body = canonical(text);
    const matches = turns.filter(turn => {
      const prefix = canonical(turn.text_prefix).slice(0, 96);
      return prefix.length >= 12 && body.includes(prefix);
    });
    // Duplicate wording is ambiguous. Never guess by list position.
    return matches.length === 1 ? matches[0] : null;
  }
  function resolveSessionId(composerIds, sidebarId) {
    const clean = id => String(id || '').replace(/^local:/, '').trim();
    const ids = [...new Set(composerIds.map(clean).filter(Boolean))];
    const sidebar = clean(sidebarId);
    if (ids.length === 1) return ids[0];
    if (ids.length > 1) return ids.includes(sidebar) ? sidebar : null;
    return sidebar || null;
  }
  return { canonical, indexTurns, matchTurn, resolveSessionId };
});
