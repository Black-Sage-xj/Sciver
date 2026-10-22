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
