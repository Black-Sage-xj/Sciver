export type ModelCatalog = {
  available_models: string[];
  available_reasoning_efforts: string[];
  model_reasoning_efforts: Record<string, string[]>;
  model_default_reasoning_efforts?: Record<string, string>;
  model_catalog: { status: string; stale: boolean; updated_at: string | null; reason?: string; scope?: string };
};

export const effortLabels: Record<string, string> = {
  none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'XHigh', max: 'Max', ultra: 'Ultra',
};
const strings = (value: unknown): string[] => Array.isArray(value)
  ? [...new Set(value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())))] : [];

export function mergeCatalog(previous: ModelCatalog, value: Partial<ModelCatalog>): ModelCatalog {
  const metadata = value.model_catalog || previous.model_catalog;
  const changedScope = typeof metadata.scope === 'string' && metadata.scope !== previous.model_catalog.scope;
  const usable = metadata.status !== 'unavailable' || changedScope;
  const providedStrings = (items: unknown): string[] | undefined => Array.isArray(items)
    && items.every((item) => typeof item === 'string' && Boolean(item.trim())) ? strings(items) : undefined;
  const models = providedStrings(value.available_models);
  const efforts = providedStrings(value.available_reasoning_efforts);
  const byModel = value.model_reasoning_efforts;
  return {
    available_models: usable && models !== undefined ? models : changedScope ? [] : previous.available_models,
    available_reasoning_efforts: usable && efforts !== undefined ? efforts : changedScope ? [] : previous.available_reasoning_efforts,
    model_reasoning_efforts: usable && byModel && typeof byModel === 'object'
      ? Object.fromEntries(Object.entries(byModel).map(([model, values]) => [model, strings(values)]))
      : changedScope ? {} : previous.model_reasoning_efforts,
    model_default_reasoning_efforts: usable && value.model_default_reasoning_efforts
      ? value.model_default_reasoning_efforts : changedScope ? {} : previous.model_default_reasoning_efforts,
    model_catalog: metadata,
  };
}

export function choicesWithCurrent(values: string[], current: string): string[] {
  return [...new Set(current ? [current, ...values] : values)];
}

export function effortsForModel(catalog: ModelCatalog, model: string): string[] {
  return Object.hasOwn(catalog.model_reasoning_efforts, model)
    ? catalog.model_reasoning_efforts[model] : catalog.available_reasoning_efforts;
}

export function effortAfterModelChange(catalog: ModelCatalog, model: string, current: string): string {
  if (!Object.hasOwn(catalog.model_reasoning_efforts, model)) return current;
  const supported = effortsForModel(catalog, model);
  if (!supported.length || supported.includes(current)) return current;
  const preferred = catalog.model_default_reasoning_efforts?.[model];
  return preferred && supported.includes(preferred) ? preferred : supported[0];
}

export function catalogHint(catalog: ModelCatalog, model: string, effort: string): string {
  if (catalog.model_catalog.status === 'unqueried') return '模型列表将自动更新。';
  const notes = [];
  if (catalog.model_catalog.stale || catalog.model_catalog.status === 'unavailable') {
    notes.push('模型列表暂时无法更新，已保留当前选项。');
  }
  if (!catalog.available_models.includes(model)) notes.push('当前模型未在最新列表中，已保留当前设置。');
  if (!effortsForModel(catalog, model).includes(effort)) notes.push('当前推理强度不在该模型支持列表，请选择合适强度。');
  return notes.join(' ') || '模型列表已更新。';
}

export function createCatalogRefresher(
  fetchCatalog: () => Promise<Partial<ModelCatalog>>,
  onCatalog: (value: Partial<ModelCatalog>) => void,
  onError: () => void,
  now: () => number = Date.now,
  cooldown = 5000,
): () => Promise<void> {
  let pending: Promise<void> | null = null;
  let lastAttempt = -Infinity;
  return () => {
    if (pending) return pending;
    if (now() - lastAttempt < cooldown) return Promise.resolve();
    lastAttempt = now();
    pending = Promise.resolve().then(fetchCatalog).then(onCatalog).catch(onError).finally(() => { pending = null; });
    return pending;
  };
}

export function watchCatalogRefresh(refresh: () => Promise<void>, controls: HTMLElement[]): () => void {
  const frame = window.frameElement;
  const whenVisible = () => { if (!document.hidden && (!frame || frame.getClientRects().length)) void refresh(); };
  const onKey = (event: KeyboardEvent) => {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) whenVisible();
  };
  for (const control of controls) {
    control.addEventListener('pointerdown', whenVisible);
    control.addEventListener('focus', whenVisible);
    control.addEventListener('keydown', onKey);
  }
  window.addEventListener('focus', whenVisible);
  document.addEventListener('visibilitychange', whenVisible);
  const timer = window.setInterval(whenVisible, 30000);
  // A minimized same-origin chat iframe stays alive, but its catalog need not poll.
  const observer = frame ? new MutationObserver(whenVisible) : null;
  for (let host = frame; host; host = host.parentElement) observer?.observe(host, { attributes: true, attributeFilter: ['hidden', 'style', 'class'] });
  whenVisible();
  return () => {
    window.clearInterval(timer);
    observer?.disconnect();
    window.removeEventListener('focus', whenVisible);
    document.removeEventListener('visibilitychange', whenVisible);
    for (const control of controls) {
      control.removeEventListener('pointerdown', whenVisible);
      control.removeEventListener('focus', whenVisible);
      control.removeEventListener('keydown', onKey);
    }
  };
}

// Updating the native option list must never emit a change event or replace a draft.
export function updateSelect(select: HTMLSelectElement, available: string[], labels: Record<string, string> = {}): void {
  const selected = select.value;
  const values = choicesWithCurrent(available, selected);
  const labelsForValues = values.map((value) => (labels[value] || value) + (available.includes(value) ? '' : '（当前）'));
  if (select.options.length === values.length && Array.from(select.options).every((option, index) =>
    option.value === values[index] && option.textContent === labelsForValues[index])) return;
  select.replaceChildren(...values.map((value, index) => new Option(labelsForValues[index], value)));
  select.value = selected;
}
