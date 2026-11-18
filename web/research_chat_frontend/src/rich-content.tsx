import { Children, isValidElement, useId, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import bash from 'highlight.js/lib/languages/bash';
import cpp from 'highlight.js/lib/languages/cpp';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import { normalizeMathDelimiters, rehypePlugins, remarkPlugins } from './markdown';
import { chatLink, isGraphLink } from './chat-links';
import { csvText, localAsset, markdownOutline } from './rich-content-utils';
import './rich-content.css';

for (const [name, language] of Object.entries({ python, javascript, typescript, json, bash, cpp, css, xml })) hljs.registerLanguage(name, language);

function plain(children: ReactNode): string {
  return Children.toArray(children).map(child => typeof child === 'string' || typeof child === 'number' ? String(child)
    : isValidElement<{children?: ReactNode}>(child) ? plain(child.props.children) : '').join('');
}

function save(text: string, name: string, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function HighlightedCode({children}: {children?: ReactNode}) {
  const [state, setState] = useState('复制代码');
  const code = plain(children);
  const child = Children.toArray(children).find(isValidElement);
  const language = (isValidElement<{className?: string}>(child) ? child.props.className || '' : '').replace(/^language-/, '');
  const highlighted = code.length < 50_000 && language && hljs.getLanguage(language) ? hljs.highlight(code, {language}).value : '';
  return <div className="tm-rich-code"><div className="tm-rich-tools"><span>{language}</span>
    <button type="button" onClick={() => { void navigator.clipboard.writeText(code).then(() => setState('已复制'), () => setState('复制失败，请选择文本复制')); }}>{state}</button>
    <button type="button" onClick={() => save(code, 'code.' + ({python:'py', javascript:'js', typescript:'ts', bash:'sh'}[language] || language || 'txt'))}>下载代码</button></div>
    <pre>{highlighted ? <code className="hljs" dangerouslySetInnerHTML={{__html:highlighted}} /> : children}</pre></div>;
}

function RichTable({children}: {children?: ReactNode}) {
  const ref = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const rows = () => Array.from(ref.current?.querySelectorAll('tr') || []).map(row => Array.from(row.querySelectorAll('th,td')).map(cell => cell.textContent || ''));
  return <div ref={ref} className={'tm-rich-table' + (expanded ? ' expanded' : '')}>
    <div className="tm-rich-tools"><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起表格' : '展开表格'}</button>
      <button type="button" onClick={() => { void navigator.clipboard.writeText(rows().map(row => row.join('\t')).join('\n')).then(() => setCopied(true), () => setCopied(false)); }}>{copied ? '已复制' : '复制表格'}</button>
      <button type="button" onClick={() => save('\uFEFF' + csvText(rows()), 'table.csv', 'text/csv;charset=utf-8')}>下载 CSV</button></div>
    <div className="tm-rich-table-scroll" tabIndex={0}><table>{children}</table></div></div>;
}

function RichImage({src, alt}: {src?: string; alt?: string}) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!src) return <span>图片不可预览</span>;
  return <span className="tm-rich-image"><button type="button" onClick={() => setOpen(true)} title="放大图片">
    {failed ? <span>{alt || '图片'}（点击打开）</span> : <img src={src} alt={alt || '回答中的图片'} loading="lazy" onError={() => setFailed(true)} />}</button>
    {open && <dialog open className="tm-rich-image-dialog" aria-label="图片预览" onCancel={() => setOpen(false)}>
      <div><button type="button" autoFocus onClick={() => setOpen(false)}>关闭</button> <a href={src} target="_blank" rel="noopener noreferrer">打开原图</a> <a href={src + (src.includes('?') ? '&' : '?') + 'download=1'} download>下载</a></div>
      <img src={src} alt={alt || '放大图片'} /></dialog>}</span>;
}

export function RichMarkdown({text, taskId, assetUrl}: {
  text: string; taskId: string; onQuote?: (text:string) => void; assetUrl?: (src:string) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  const prefix = useId().replace(/:/g, '');
  const outline = markdownOutline(text);
  let headingIndex = 0;
  const sessionId = document.querySelector<HTMLElement>('.tm-research-chat')?.dataset.session || '';
  const asset = assetUrl || ((src:string) => localAsset(src, sessionId));
  const heading = (level:number, children:ReactNode) => {
    const item = outline[headingIndex++]; const Tag = `h${level}` as 'h1';
    return <Tag id={prefix + '-' + (item?.slug || headingIndex)}>{children}</Tag>;
  };
  const long = text.length > 12_000;
  return <div className="tm-rich-content">
    {outline.length >= 3 && <details className="tm-rich-outline"><summary>回答目录（{outline.length}）</summary><nav>{outline.map(item =>
      <a key={item.slug} style={{paddingInlineStart:(item.level - 1) * 10}} href={'#' + prefix + '-' + item.slug}
        onClick={() => setExpanded(true)}>{item.text}</a>)}</nav></details>}
    <div className={long && !expanded ? 'tm-rich-long collapsed' : 'tm-rich-long'}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}
        urlTransform={(url, key) => key === 'src' ? asset(url) : /^file:/i.test(url) ? url : defaultUrlTransform(url)}
        components={{ pre: HighlightedCode, table: RichTable, img: ({src,alt}) => <RichImage src={typeof src === 'string' ? src : ''} alt={alt} />,
          h1:({children})=>heading(1,children),h2:({children})=>heading(2,children),h3:({children})=>heading(3,children),
          h4:({children})=>heading(4,children),h5:({children})=>heading(5,children),h6:({children})=>heading(6,children),
          a:({href,children})=>{
            const raw=href || ''; const localFile=!/^https?:|^\/api\//i.test(raw) && /\.(?:pdf|png|jpe?g|webp|gif|csv|json|docx?|xlsx?|pptx?|odt|ods|odp|zip|txt|html)(?:[?#]|$)/i.test(raw);
            const target=localFile ? asset(raw) : chatLink(raw,taskId,location.origin);
            return target ? <a href={target} target={isGraphLink(target,location.href) ? '_top' : target.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a> : <span>{children}（无法预览）</span>;
          } }}>{normalizeMathDelimiters(text)}</ReactMarkdown>
    </div>
    {long && <button type="button" className="tm-rich-expand" onClick={() => setExpanded(!expanded)}>{expanded ? '折叠长回答' : '展开完整回答'}</button>}
  </div>;
}
