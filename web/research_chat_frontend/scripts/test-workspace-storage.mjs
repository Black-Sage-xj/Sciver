import assert from 'node:assert/strict';
import { workspaceStorage, workspaceStorageKey } from '../src/workspace-storage.ts';
import { bindComposerDraft } from '../src/drafts.ts';

const makeStorage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};
let scope = 'account-one';
globalThis.document = { querySelector: () => ({ content: scope }) };
globalThis.window = { localStorage: makeStorage(), sessionStorage: makeStorage() };
const shared = workspaceStorage('localStorage'), session = workspaceStorage('sessionStorage');
const composer = () => {
  let text = ''; const listeners = new Set();
  return { getState: () => ({ text }), setText(value) { text = value; for (const fn of listeners) fn(); },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); } };
};

window.localStorage.setItem('sciver-chat-composer-v2:research:same-session', 'legacy private draft');
shared.setItem('new-chat-draft', 'account one private draft');
session.setItem('widget-session', 'account-one-session');
const first = composer();
bindComposerDraft(first, shared, session, 'same-session', 'research');
assert.equal(first.getState().text, '');
first.setText('account one composer');
scope = 'account-two';
assert.equal(shared.getItem('new-chat-draft'), null);
assert.equal(session.getItem('widget-session'), null);
const second = composer();
bindComposerDraft(second, shared, session, 'same-session', 'research');
assert.equal(second.getState().text, '');
second.setText('account two composer');
scope = 'account-one';
assert.equal(shared.getItem('new-chat-draft'), 'account one private draft');
assert.equal(session.getItem('widget-session'), 'account-one-session');
const restored = composer();
bindComposerDraft(restored, shared, session, 'same-session', 'research');
assert.equal(restored.getState().text, 'account one composer');
assert.equal(workspaceStorageKey('key', ''), 'key');
assert.equal(workspaceStorageKey('key', 'scope'), 'sciver-workspace:scope:key');
console.log('PASS: account-scoped browser drafts, widget sessions and composer restoration');
