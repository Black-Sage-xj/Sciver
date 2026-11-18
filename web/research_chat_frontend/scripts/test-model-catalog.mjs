import assert from 'node:assert/strict';
import {
  catalogHint, choicesWithCurrent, createCatalogRefresher, effortAfterModelChange, effortsForModel,
  mergeCatalog, updateSelect, watchCatalogRefresh,
} from '../src/model-catalog.ts';

const original = {
  available_models: ['old-model'], available_reasoning_efforts: ['low', 'medium'],
  model_reasoning_efforts: {}, model_catalog: {status: 'unqueried', stale: false, updated_at: null},
};
assert.match(catalogHint({...original, model_catalog: {...original.model_catalog, stale: true}}, 'old-model', 'medium'), /自动更新/);
const live = mergeCatalog(original, {
  available_models: ['new-model', 'new-model', 'other-model'], available_reasoning_efforts: ['low', 'high'],
  model_reasoning_efforts: {'new-model': ['high', 'xhigh'], 'other-model': []},
  model_default_reasoning_efforts: {'new-model': 'xhigh'},
  model_catalog: {status: 'catalog_read', stale: false, updated_at: '2026-11-18T12:00:00Z'},
});
assert.deepEqual(live.available_models, ['new-model', 'other-model']);
assert.deepEqual(choicesWithCurrent(live.available_models, 'old-model'), ['old-model', 'new-model', 'other-model']);
assert.deepEqual(effortsForModel(live, 'new-model'), ['high', 'xhigh']);
assert.equal(effortAfterModelChange(live, 'new-model', 'medium'), 'xhigh');
assert.equal(effortAfterModelChange(live, 'new-model', 'high'), 'high');
assert.equal(effortAfterModelChange(live, 'unknown', 'medium'), 'medium');
assert.equal(effortAfterModelChange(live, 'other-model', 'medium'), 'medium');
assert.deepEqual(effortsForModel(live, 'other-model'), []);
assert.deepEqual(effortsForModel(live, 'unknown'), ['low', 'high']);
assert.match(catalogHint(live, 'old-model', 'medium'), /保留当前设置/);
assert.match(catalogHint(live, 'new-model', 'medium'), /推理强度/);
const failed = mergeCatalog(live, {available_models: ['fallback'], model_reasoning_efforts: {},
  model_catalog: {status: 'unavailable', stale: true, updated_at: null}});
assert.deepEqual(failed.available_models, live.available_models);
assert.deepEqual(failed.model_reasoning_efforts, live.model_reasoning_efforts);
assert.match(catalogHint(failed, 'new-model', 'high'), /保留当前选项/);
const accountChanged = mergeCatalog({...live, model_catalog: {...live.model_catalog, scope: 'account-A'}}, {
  available_models: ['fallback'], available_reasoning_efforts: ['medium'], model_reasoning_efforts: {},
  model_catalog: {status: 'unavailable', stale: true, updated_at: null, scope: 'account-B'},
});
assert.deepEqual(accountChanged.available_models, ['fallback']);
assert.deepEqual(accountChanged.model_reasoning_efforts, {});
assert.deepEqual(accountChanged.model_default_reasoning_efforts, {});
assert.deepEqual(choicesWithCurrent(accountChanged.available_models, 'new-model'), ['new-model', 'fallback']);
for (const scope of ['account-A', 'account-B']) {
  const emptyFresh = mergeCatalog({...live, model_catalog: {...live.model_catalog, scope: 'account-A'}}, {
    available_models: [], available_reasoning_efforts: [], model_reasoning_efforts: {},
    model_default_reasoning_efforts: {},
    model_catalog: {status: 'catalog_read', stale: false, updated_at: '2026-11-18T12:01:00Z', scope},
  });
  assert.deepEqual(emptyFresh.available_models, []);
  assert.deepEqual(emptyFresh.available_reasoning_efforts, []);
  assert.deepEqual(emptyFresh.model_reasoning_efforts, {});
  assert.deepEqual(emptyFresh.model_default_reasoning_efforts, {});
  assert.deepEqual(choicesWithCurrent(emptyFresh.available_models, 'new-model'), ['new-model']);
}
assert.deepEqual(mergeCatalog(live, {}).available_models, live.available_models);
assert.deepEqual(mergeCatalog(live, {available_models: [123]}).available_models, live.available_models);



// Native option replacement retains the exact selected ID and never emits input/change.
globalThis.Option = class { constructor(text, value) { this.textContent = text; this.value = value; } };
const select = {
  value: 'old-model', options: [new Option('old-model', 'old-model')],
  replaceChildren(...options) { this.options = options; this.value = options[0]?.value || ''; },
  dispatchEvent() { assert.fail('catalog refresh must not change draft context'); },
};
updateSelect(select, live.available_models);
assert.equal(select.value, 'old-model');
assert.equal(select.options[0].textContent, 'old-model（当前）');
const unchangedOptions = select.options;
updateSelect(select, live.available_models);
assert.equal(select.options, unchangedOptions);

let now = 0, fetches = 0, received = 0, errors = 0, complete;
const refresh = createCatalogRefresher(() => { fetches++; return new Promise(resolve => { complete = resolve; }); },
  () => { received++; }, () => { errors++; }, () => now);
const first = refresh();
assert.equal(refresh(), first);
await Promise.resolve();
assert.equal(fetches, 1);
complete(live);
await first;
now = 1000;
await refresh();
assert.equal(fetches, 1);
now = 5000;
const second = refresh();
await Promise.resolve();
complete(live);
await second;
assert.equal(fetches, 2);
assert.equal(received, 2);
const rejectRefresh = createCatalogRefresher(async () => { throw Error('offline'); }, () => {}, () => { errors++; }, () => now);
await rejectRefresh(); await rejectRefresh();
assert.equal(errors, 1);

// Automatic lifecycle triggers remain visible-only and detach cleanly.
let tick, cleared = false, refreshes = 0;
const fakeWindow = new EventTarget();
fakeWindow.setInterval = callback => { tick = callback; return 1; };
fakeWindow.clearInterval = () => { cleared = true; };
globalThis.window = fakeWindow;
globalThis.document = new EventTarget();
document.hidden = false;
const control = new EventTarget();
const stop = watchCatalogRefresh(async () => { refreshes++; }, [control]);
assert.equal(refreshes, 1);
control.dispatchEvent(new Event('pointerdown'));
window.dispatchEvent(new Event('focus'));
assert.equal(refreshes, 3);
document.hidden = true; tick();
assert.equal(refreshes, 3);
document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); tick();
assert.equal(refreshes, 5);
stop();
window.dispatchEvent(new Event('focus'));
control.dispatchEvent(new Event('pointerdown'));
assert.equal(refreshes, 5);
assert.equal(cleared, true);
let frameVisible = false, notifyFrame, disconnected = false;
window.frameElement = {parentElement: null, getClientRects: () => frameVisible ? [{}] : []};
globalThis.MutationObserver = class {
  constructor(callback) { notifyFrame = callback; }
  observe() {}
  disconnect() { disconnected = true; }
};
const stopEmbedded = watchCatalogRefresh(async () => { refreshes++; }, []);
assert.equal(refreshes, 5);
frameVisible = true; notifyFrame();
assert.equal(refreshes, 6);
frameVisible = false; tick();
assert.equal(refreshes, 6);
stopEmbedded();
assert.equal(disconnected, true);
console.log('PASS: catalog updates, current selections, effort capabilities, native draft isolation, coalescing, failure fallback and lifecycle cleanup');
