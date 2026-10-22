import type { Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export const remarkPlugins: NonNullable<Options['remarkPlugins']> = [remarkGfm, remarkMath];
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
