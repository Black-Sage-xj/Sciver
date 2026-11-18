// Preserve packaged font URLs by content when the bundler changes collision order.
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const directory = resolve(here, '../../tm_observer/research_chat_dist');
const manifest = JSON.parse(await readFile(resolve(here, 'font-assets.json'), 'utf8'));
const names = new Map(manifest.map(({ sha256, name }) => [sha256, name]));
const fonts = (await readdir(directory)).filter((name) => /\.(?:ttf|woff2?)$/.test(name));
const snapshots = await Promise.all(fonts.map(async (name) => {
  const data = await readFile(resolve(directory, name));
  const hash = createHash('sha256').update(data).digest('hex');
  const target = names.get(hash);
  if (!target) throw new Error(`Font content changed: ${name}; review dependency changes and update font-assets.json`);
  return { name, target, data };
}));
if (snapshots.length !== manifest.length || new Set(snapshots.map((font) => font.target)).size !== manifest.length) {
  throw new Error('Packaged font set changed; review and update font-assets.json');
}
const mapping = new Map(snapshots.map(({ name, target }) => [name, target]));
const escaped = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const pattern = new RegExp([...mapping.keys()].sort((a,b) => b.length-a.length).map(escaped).join('|'), 'g');
for (const name of (await readdir(directory)).filter((name) => /\.(?:css|js)$/.test(name))) {
  const path = resolve(directory, name);
  const text = await readFile(path, 'utf8');
  await writeFile(path, text.replace(pattern, (match) => mapping.get(match)));
}
// Read every input before writing, because file names can swap with each other.
for (const { target, data } of snapshots) await writeFile(resolve(directory, target), data);
console.log(`Preserved ${snapshots.length} packaged font URLs by SHA-256`);
