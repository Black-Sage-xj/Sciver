import assert from 'node:assert/strict';
import { chatLink, isGraphLink } from '../src/chat-links.ts';
const id = 'a'.repeat(16), origin = 'https://research.example.org';
for (const path of ['/tmp/task/notes/outcomes/1.md', 'notes/outcomes/1.md',
  'file:///tmp/task/notes/outcomes/1.md', '/notes/method.md#Conditions',
  'research_plan.md:3', origin+'/tmp/task/notes/outcomes/1.md']) {
  const result = chatLink(path, id, origin);
  assert.ok(result.startsWith('/research-chat/open?'));
  const url = new URL(result, origin);
  assert.equal(url.searchParams.get('task_id'), id);
  assert.equal(url.searchParams.get('target'), path.startsWith(origin) ? path.slice(origin.length) : path);
}
for (const url of ['https://example.com/paper.md', '/run/abc/', '#section', 'mailto:research@example.org']) {
  assert.equal(chatLink(url, id, origin), url);
}
assert.equal(chatLink('/tmp/task/notes/a.md', '', origin), null);
assert.equal(chatLink('javascript:alert(1)', id, origin), null);
assert.equal(chatLink('data:text/html,unsafe', id, origin), null);
for (const href of ['/research-explorer/?observer_id='+id+'#report%3A2',
  origin+'/research-explorer#node%3AH-0002',
  '/research-chat/open?task_id='+id+'&target=notes%2Foutcomes%2F2.md']) {
  assert.equal(isGraphLink(href, origin+'/research-chat/'), true);
}
for (const href of ['https://elsewhere.example/research-explorer/#report%3A2',
  '/research-explorer-fake/', '#section', 'javascript:alert(1)', 'http://[invalid']) {
  assert.equal(isGraphLink(href, origin+'/research-chat/'), false);
}
console.log('PASS: local/absolute/file citations, scoped task IDs, anchors and external-link preservation');
