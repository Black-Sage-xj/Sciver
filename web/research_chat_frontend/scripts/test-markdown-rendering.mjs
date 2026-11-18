import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { normalizeMathDelimiters, rehypePlugins, remarkPlugins } from '../src/markdown.ts';

function render(markdown) {
  return renderToStaticMarkup(React.createElement(
    ReactMarkdown,
    { remarkPlugins, rehypePlugins },
    normalizeMathDelimiters(markdown),
  ));
}

const inline = render(String.raw`分解框架：$\Delta E_g=L_g-Q_g$，其中 $Q_g\geq 0$。`);
assert.equal((inline.match(/class="katex"/g) || []).length, 2, 'inline math should render twice');
assert.match(inline, /<msub>/, 'subscripts should be represented in rendered MathML');
assert.match(inline, /Δ/, 'Greek letters should render');

const display = render(String.raw`公式：

$$
\Delta E_g = L_g - Q_g,\quad Q_g \geq 0
$$`);
assert.match(display, /class="katex-display"/, 'dollar-delimited block math should render as display math');

const slashDelimiters = render(String.raw`行内 \(\Delta E_g=L_g-Q_g\)，以及块级：

\[
Q_g \geq 0
\]`);
assert.match(slashDelimiters, /class="katex"/, 'parenthesized math delimiters should render');
assert.match(slashDelimiters, /class="katex-display"/, 'bracketed math delimiters should render as display math');

const markdown = render('**粗体**\n\n| 量 | 值 |\n| --- | --- |\n| Q | 0 |');
assert.ok(markdown.includes('<strong>粗体</strong>'), 'Markdown emphasis should remain supported');
assert.match(markdown, /<table>/, 'GFM tables should remain supported');

const code = render('代码 `$x$` 和 `\\(x\\)`；代码块：\n\n```tex\n$y$ \\(z\\)\n```');
assert.doesNotMatch(code, /class="katex"/, 'code spans and fenced code must not be parsed as math');
assert.ok(code.includes('<code>$x$</code>'), 'inline code should remain literal');
assert.match(code, /\$y\$/, 'fenced code content should remain literal');

assert.doesNotThrow(() => render(String.raw`流式生成中：$\Delta E_g=L_g-Q_g`), 'unfinished streamed math must not crash rendering');
const unsafeHtml = render('<img src=x onerror=alert(1)>');
assert.ok(!unsafeHtml.includes('<img'), 'raw HTML must not introduce executable image elements');
assert.ok(unsafeHtml.includes('&lt;img'), 'raw HTML should remain escaped text without rehype-raw');

console.log('PASS: inline/display math, alternate delimiters, GFM, code preservation, partial stream, and raw-HTML safety');

const chineseStrong = render('**建议阅读顺序：**先读综述，再回答**你更想先看环链，还是交联？**我们继续检索。');
assert.ok(chineseStrong.includes('<strong>建议阅读顺序</strong>：先读'), 'Chinese punctuation adjacent to prose should close bold');
assert.ok(chineseStrong.includes('<strong>你更想先看环链，还是交联</strong>？我们'), 'Chinese question punctuation should close bold without spaces');
assert.ok(render('比较**“环链与线性链”**之间的差异。').includes('“<strong>环链与线性链</strong>”'), 'Chinese quotation marks and visible text should be preserved');
const protectedStrong = render(String.raw`代码 \`**建议：**后文\`；公式 $x ** y$；转义 \*\*问题？\*\*后文。`.replace(/\\`/g, '`'));
assert.ok(protectedStrong.includes('<code>**建议：**后文</code>'), 'code containing CJK bold markers must remain literal');
assert.ok(protectedStrong.includes('**问题？**后文'), 'escaped bold delimiters must remain literal');
assert.doesNotMatch(protectedStrong, /<strong>/, 'math, code and escaped literal markers must not become bold');
console.log('PASS: CJK punctuation emphasis with exact text, code/math/escape preservation');
const literalCjkMath = String.raw`$\text{**建议：**后文}$`;
assert.equal(normalizeMathDelimiters(literalCjkMath), literalCjkMath, 'CJK emphasis-like content inside math must remain unchanged');
const indentedCjkCode = '    **建议：**后文';
assert.equal(normalizeMathDelimiters(indentedCjkCode), indentedCjkCode, 'indented code must remain unchanged');
const quotedCjkCode = '```md\n**建议：**后文\n```';
assert.equal(normalizeMathDelimiters(quotedCjkCode), quotedCjkCode, 'fenced CJK code must remain unchanged');
