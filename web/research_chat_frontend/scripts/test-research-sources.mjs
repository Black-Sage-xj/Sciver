import assert from 'node:assert/strict';
import { mergeSources, removeSubmittedSources, sourceMarkdown } from '../src/research-sources.ts';

const task = 'a'.repeat(16);
const source = { task_id: task, kind: 'report', id: '1', section: 'results', quote: 'A bounded result.',
  source_version: 'f'.repeat(64), label: 'Study', href: `/research-explorer/?observer_id=${task}#report%3A1%23results`, validated_at: 'now' };
const draft = mergeSources([], source, task);
assert.equal(draft.length, 1);
assert.equal(mergeSources(draft, source, task), draft);
assert.equal(mergeSources(draft, {...source, task_id: 'b'.repeat(16)}, task), draft);
assert.equal(mergeSources(draft, {...source, source_version: ''}, task), draft);
assert.equal(mergeSources(draft, {...source, href: 'javascript:alert(1)'}, task), draft);
assert.equal(mergeSources(draft, null, task), draft);
let full = draft;
for (let id = 2; id < 7; id++) full = mergeSources(full, {...source, id: String(id)}, task);
assert.equal(full.length, 4);
const addedWhileSending = {...source, id:'2'};
assert.deepEqual(removeSubmittedSources([source,addedWhileSending], [source]), [addedWhileSending]);
const rendered = sourceMarkdown(draft);
assert.ok(rendered.includes(source.href));
assert.ok(rendered.includes('已核验快照 ffffffff'));
assert.ok(rendered.includes('> A bounded result.'));
assert.equal(sourceMarkdown([]), '');
console.log('PASS: scoped removable draft sources, deduplication, bounded attachments and snapshot backlinks');
