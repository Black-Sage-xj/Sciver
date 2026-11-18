import assert from 'node:assert/strict';
import { bindComposerDraft } from '../src/drafts.ts';
const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};
const composer = () => {
  let text = ''; const listeners = new Set();
  return { getState: () => ({text}),
    setText(value) { text = value; this.notify(); },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    notify() { for (const listener of listeners) listener(); } };
};
const shared = storage(), legacy = storage();
legacy.setItem('sciver-chat-composer-v1:session', 'legacy draft');
const embedded = composer();
const unsubscribe = bindComposerDraft(embedded, shared, legacy, 'session', 'research');
assert.equal(embedded.getState().text, 'legacy draft');
assert.equal(legacy.getItem('sciver-chat-composer-v1:session'), null);
embedded.setText('embedded draft');
const full = composer();
bindComposerDraft(full, shared, legacy, 'session', 'research');
assert.equal(full.getState().text, 'embedded draft');
full.setText('newer standalone draft');
embedded.notify();
const reload = composer();
bindComposerDraft(reload, shared, legacy, 'session', 'research');
assert.equal(reload.getState().text, 'newer standalone draft');
full.setText('');
const cleared = composer();
bindComposerDraft(cleared, shared, legacy, 'session', 'research');
assert.equal(cleared.getState().text, '');
const other = composer();
bindComposerDraft(other, shared, legacy, 'another-session', 'research');
assert.equal(other.getState().text, '');
unsubscribe(); embedded.setText('unsubscribed draft');
assert.equal(shared.getItem('sciver-chat-composer-v2:research:session'), null);
const blocked = { getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }, removeItem() { throw Error('blocked'); } };
const restricted = composer();
assert.doesNotThrow(() => bindComposerDraft(restricted, blocked, blocked, 'session', 'research'));
assert.doesNotThrow(() => restricted.setText('usable'));
console.log('PASS: shared drafts, legacy migration, clearing, isolation, inactive tabs and restricted storage');
