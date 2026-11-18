export type ReadingSource = {
  task_id: string; kind: 'node' | 'report' | 'doc'; id: string; section: string;
  quote: string; source_version: string; label: string; href: string; validated_at: string;
  hypothesis_id?: string; cycle_id?: string; document_id?: string;
};

export const sourceKey = (source: ReadingSource) =>
  `${source.kind}:${source.id}#${source.section}:${source.source_version}:${source.quote}`;

export function removeSubmittedSources(current: ReadingSource[], submitted: ReadingSource[]): ReadingSource[] {
  const sent = new Set(submitted.map(sourceKey));
  return current.filter((source) => !sent.has(sourceKey(source)));
}

export function mergeSources(current: ReadingSource[], source: ReadingSource, taskId: string): ReadingSource[] {
  if (!source || typeof source !== 'object' || source.task_id !== taskId || !/^[a-f0-9]{64}$/.test(source.source_version)
      || !['node', 'report', 'doc'].includes(source.kind) || typeof source.quote !== 'string'
      || !source.href?.startsWith('/research-explorer/?observer_id=' + taskId + '#')) return current;
  if (current.some((item) => sourceKey(item) === sourceKey(source))) return current;
  return current.length >= 4 ? current : [...current, source];
}

export function sourceMarkdown(sources: ReadingSource[] = []): string {
  if (!sources.length) return '';
  const escape = (value: string) => value.replace(/[\\[\]`*_]/g, '\\$&').replace(/\r?\n/g, ' ');
  return '\n\n' + sources.map((source) =>
    `参考来源：[${escape(source.label)}${source.section ? ' · ' + escape(source.section) : ''}](${source.href})` +
    ` · 已核验快照 ${source.source_version.slice(0, 8)}` +
    (source.quote ? '\n\n' + source.quote.split('\n').map((line) => '> ' + escape(line)).join('\n') : '')
  ).join('\n\n');
}
