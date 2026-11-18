export type OutlineEntry = { level: number; text: string; slug: string };

export function markdownOutline(text: string): OutlineEntry[] {
  const entries: OutlineEntry[] = [];
  const seen = new Map<string, number>();
  let fence = '';
  for (const line of text.split('\n')) {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (marker) { if (!fence) fence = marker[1][0]; else if (marker[1][0] === fence) fence = ''; continue; }
    if (fence) continue;
    const match = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (!match) continue;
    const title = match[2].replace(/[*_`]/g, '');
    const base = title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'section';
    const count = (seen.get(base) || 0) + 1; seen.set(base, count);
    entries.push({ level: match[1].length, text: title, slug: base + (count > 1 ? '-' + count : '') });
  }
  return entries;
}

export function csvText(rows: string[][]): string {
  return rows.map(row => row.map(value => /[",\r\n]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value).join(',')).join('\r\n');
}

export function localAsset(src: string, sessionId: string): string {
  if (/^(?:javascript|data|vbscript):/i.test(src)) return '';
  if (/^(?:https?:\/\/|\/api\/|\/static\/|#)/i.test(src)) return src;
  if (!sessionId) return '';
  return `/api/research-chat/${encodeURIComponent(sessionId)}/artifact?${new URLSearchParams({ path: src })}`;
}
