import type { Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export const remarkPlugins: NonNullable<Options['remarkPlugins']> = [remarkGfm, remarkMath];

/** Keep CJK edge punctuation visible while giving CommonMark valid boundaries. */
function normalizeCjkStrong(text: string, protectedPrefix: string): string {
  const cjk = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
  return text.replace(/(?<![\\*])\*\*([^*\n]+?)(?<!\\)\*\*(?!\*)/g, (whole, content: string, offset: number) => {
    const linePrefix = text.slice(text.lastIndexOf('\n', offset) + 1, offset);
    if (!cjk.test(content) || /[\[\]<>`\\]/.test(content) || content.includes(protectedPrefix)
        || /^\s|\s$/.test(content) || /^(?: {4}|\t)/.test(linePrefix)
        || /(?:https?:\/\/|www\.)\S*$/i.test(linePrefix)) return whole;
    const leading = content.match(/^[“‘「『（【《〈]+/u)?.[0] || '';
    const trailing = content.match(/[，。！？：；、”’」』）】》〉]+$/u)?.[0] || '';
    const moveLeading = leading && cjk.test(text[offset - 1] || '');
    const moveTrailing = trailing && cjk.test(text[offset + whole.length] || '');
    if (!moveLeading && !moveTrailing) return whole;
    const core = content.slice(moveLeading ? leading.length : 0,
      moveTrailing ? content.length - trailing.length : content.length);
    if (!core || !cjk.test(core)) return whole;
    return `${moveLeading ? leading : ''}**${core}**${moveTrailing ? trailing : ''}`;
  });
}
export const rehypePlugins: NonNullable<Options['rehypePlugins']> = [[rehypeKatex, { throwOnError: false }]];

/**
 * Accept both Markdown's `$...$` / `$$...$$` and the chat rule's `\\(...\\)` /
 * `\\[...\\]` delimiters. Protect fenced and inline code before adapting the
 * latter, so examples remain literal and the Markdown parser retains its
 * normal code/security behavior.
 */
export function normalizeMathDelimiters(source: string): string {
  const savedCode: Array<{ token: string; value: string }> = [];
  let tokenPrefix = '\uE000TM_MATH_CODE_';
  while (source.includes(tokenPrefix)) tokenPrefix = `\uE000${tokenPrefix}`;

  const stashCode = (value: string): string => {
    const token = `${tokenPrefix}${savedCode.length}\uE001`;
    savedCode.push({ token, value });
    return token;
  };

  let output = '';
  let plain = '';
  let fence: { char: string; length: number; content: string } | null = null;

  const flushPlain = (): void => {
    if (!plain) return;
    let chunk = plain.replace(/(`+)([\s\S]*?)\1(?!`)/g, (codeSpan) => stashCode(codeSpan));
    chunk = chunk.replace(/(?<!\\)\\\[([\s\S]*?)(?<!\\)\\\]/g, (_match, expression: string) => `\n$$\n${expression.trim()}\n$$\n`);
    chunk = chunk.replace(/(?<!\\)\\\(([\s\S]+?)(?<!\\)\\\)/g, (_match, expression: string) => `$${expression}$`);
    // Protect already-normalized math before adapting Markdown emphasis.
    chunk = chunk.replace(/(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<!\\)\$(?!\$)(?:\\.|[^$\n\\])+(?<!\\)\$(?!\$)/g, (math) => stashCode(math));
    chunk = normalizeCjkStrong(chunk, tokenPrefix);
    output += chunk;
    plain = '';
  };

  for (const line of source.match(/[^\n]*(?:\n|$)/g) ?? []) {
    if (!line) continue;
    if (fence) {
      fence.content += line;
      const closePattern = new RegExp(`^ {0,3}${fence.char}{${fence.length},}[ \\t]*(?:\\r?\\n)?$`);
      if (closePattern.test(line)) {
        output += stashCode(fence.content);
        fence = null;
      }
      continue;
    }

    const opening = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (opening) {
      flushPlain();
      fence = { char: opening[1][0], length: opening[1].length, content: line };
      continue;
    }
    plain += line;
  }

  if (fence) output += stashCode(fence.content);
  flushPlain();
  for (const { token, value } of savedCode) output = output.split(token).join(value);
  return output;
}
