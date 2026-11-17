"""Scoped research-note typography and hypothesis / result reading surfaces.

Uses GitHub-style Markdown spacing with the Observatory's explicit theme choice.
Scientific colors belong to per-Cycle verdict badges, independently of branches.
"""

READER_CSS = r"""
<style data-research-reader>
.ex-reader{
  --read-bg:#111318;--read-card:#181c24;--read-soft:#202733;--read-border:#354153;
  --read-text:#e1e7f0;--read-muted:#a5b2c5;--read-blue:#94c5ff;--read-purple:#d0b4ff;
  --read-teal:#81dbce;--read-code-bg:#10151e;--read-quote:#172c31;
  --read-green:#8ee0bc;--read-green-bg:#173b32;--read-red:#ffa5b7;--read-red-bg:#442631;
  --read-amber:#f5d28b;--read-amber-bg:#3b3020;
}
html[data-sciver-theme="blue"] .ex-reader{
  --read-bg:#091b32;--read-card:#10253e;--read-soft:#17334e;--read-border:#365572;
  --read-code-bg:#08182c;--read-quote:#103739;
}
html[data-sciver-theme="white"] .ex-reader{
  --read-bg:#f6f8fb;--read-card:#fff;--read-soft:#f0f4fa;--read-border:#d9e1ec;
  --read-text:#27364b;--read-muted:#596b80;--read-blue:#245eb4;--read-purple:#7543b0;
  --read-teal:#12695e;--read-code-bg:#f4f6fa;--read-quote:#edf8f6;
  --read-green:#176445;--read-green-bg:#e6f5ed;--read-red:#a12a45;--read-red-bg:#fdeef1;
  --read-amber:#80510b;--read-amber-bg:#fff5df;
}
html[data-sciver-theme] .ex-reader{padding:18px;background:var(--read-bg);color:var(--read-text);scroll-behavior:auto;scrollbar-gutter:stable}
.ex-reader-card{padding:20px;border:1px solid var(--read-border);border-radius:12px;background:var(--read-card);margin:0 0 18px;min-width:0}
.ex-reader-section-head{display:flex;align-items:center;gap:11px;margin-bottom:19px}
.ex-section-number{display:grid;place-items:center;flex:none;width:30px;height:30px;border-radius:9px;background:var(--read-soft);color:var(--read-blue);font:700 12px/1 ui-monospace,monospace}
.ex-outcome-details .ex-section-number{color:var(--read-teal)}
.ex-reader-section-head h3{margin:0;font-size:16px;color:var(--read-text);letter-spacing:0}
.ex-reader-section-head p{margin:4px 0 0;font-size:12px;color:var(--read-muted)}
.ex-result-count{margin-left:7px;font-size:12px;font-weight:500;color:var(--read-muted)}
.ex-reader .ex-hypothesis-title{font-size:21px;line-height:1.4;letter-spacing:-.02em;margin:0 0 9px;color:var(--read-text)}
.ex-reader .ex-document-title{font-size:23px;line-height:1.4;margin:16px 0 9px;color:var(--read-blue)}
.ex-report-kicker{color:var(--read-muted);font-size:12px;margin:16px 0 0}
.ex-report-toc{padding:12px 0;margin:0 0 18px;border-bottom:1px solid var(--read-border);color:var(--read-muted);font-size:12px}
.ex-report-toc summary{cursor:pointer}
.ex-report-toc nav{display:flex;flex-direction:column;align-items:stretch;margin-top:8px}
.ex-report-toc .ex-ref{text-align:left;font-size:12px}
.ex-reader .ex-md :is(.research-supplement,.research-adjudication,.research-legacy-summary){margin:20px 0;padding:12px 14px;border:1px solid var(--read-border);border-radius:9px;background:var(--read-card)}
.ex-reader .ex-md :is(.research-supplement,.research-adjudication,.research-legacy-summary)>summary{cursor:pointer;font-size:13px;color:var(--read-blue)}
.ex-reader .ex-md details[open]>summary{margin-bottom:16px}
.ex-legacy-preview{font-size:13px;line-height:1.8;white-space:pre-wrap}
html[data-sciver-theme] .ex-reader .ex-node-time,.ex-result-time{color:var(--read-muted);font-size:11px;font-variant-numeric:tabular-nums;margin:0 0 17px}
.ex-hypothesis-facts,.ex-guide-facts{margin:0}
.ex-hypothesis-facts>div+div,.ex-guide-facts>div+div{margin-top:17px}
.ex-hypothesis-facts dt,.ex-guide-facts dt{font-size:12px;font-weight:650;color:var(--read-muted);margin-bottom:6px}
.ex-hypothesis-facts dd,.ex-guide-facts dd{margin:0}
.ex-hypothesis-facts>div:first-child dd{font-size:15px}
.ex-hypothesis-facts>div+div dd{padding:10px 12px;background:var(--read-soft);border-radius:8px;font-size:13px}
.ex-assessment-note{margin:17px 0 0;font-size:12px;color:var(--read-muted);line-height:1.6}
.ex-whole-assessment{margin-top:17px;font-size:12px}
.ex-reader-documents{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
html[data-sciver-theme] .ex-reader .ex-document-link{display:inline-flex;align-items:center;justify-content:space-between;gap:12px;margin:0;padding:7px 10px;background:transparent;border:1px solid var(--read-border);color:var(--read-blue);font-size:12px;border-radius:7px;text-align:left}
html[data-sciver-theme] .ex-reader .ex-document-link:hover{background:var(--read-soft);border-color:var(--read-blue)}
.ex-cycle-result{border-top:1px solid var(--read-border);padding:14px 0 0;margin-top:14px}
.ex-cycle-result>summary{display:flex;align-items:center;gap:9px;cursor:pointer;list-style:none;min-height:30px}
.ex-cycle-result>summary::-webkit-details-marker{display:none}
.ex-cycle-result>summary::after{content:'⌄';margin-left:auto;font-size:17px;color:var(--read-muted);transform:rotate(-90deg)}
.ex-cycle-result[open]>summary::after{transform:none}
.ex-cycle-label{font-size:13px;font-weight:650;font-variant-numeric:tabular-nums}
.ex-verdict{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:650;color:var(--read-purple);background:var(--read-soft)}
.ex-verdict small{font-size:10px;font-weight:450;opacity:.9}
.ex-verdict[data-verdict="supported"]{color:var(--read-green);background:var(--read-green-bg)}
.ex-verdict[data-verdict="rejected"]{color:var(--read-red);background:var(--read-red-bg)}
.ex-verdict[data-verdict="unadjudicated"]{color:var(--read-amber);background:var(--read-amber-bg)}
.ex-result-body{padding-top:9px}
.ex-conclusion{border-left:3px solid var(--read-teal);padding:1px 0 1px 13px;margin:0 0 20px;font-size:15px}
.ex-guide-facts dd{font-size:13px}
.ex-outcome-record{border:1px solid var(--read-border);border-radius:8px;margin-top:18px;padding:10px 12px;font-size:13px}
.ex-outcome-record>summary{cursor:pointer;color:var(--read-muted);font-size:12px}
.ex-outcome-record[open]>summary{margin-bottom:15px}
.ex-reader-context{border-top:1px solid var(--read-border);padding:14px 2px;color:var(--read-muted);font-size:12px}
.ex-reader-context>summary{cursor:pointer;font-weight:550}
.ex-reader-context[open]>summary{margin-bottom:12px}
.ex-reader-context .ex-section{border-color:var(--read-border)}
.ex-reader-empty{color:var(--read-muted);font-size:13px;line-height:1.7;margin:0}
.ex-reader-notice{padding:10px 12px;border-radius:7px;background:var(--read-amber-bg);color:var(--read-amber);font-size:12px}
/* Markdown reading rhythm: semantic color, generous spacing and bounded tables. */
html[data-sciver-theme] .ex-reader .ex-md{color:var(--read-text);font-size:14px;line-height:1.85;word-break:normal;overflow-wrap:anywhere}
html[data-sciver-theme] .ex-reader .ex-conclusion{font-size:15px}
html[data-sciver-theme] .ex-reader .ex-hypothesis-facts>div+div dd,html[data-sciver-theme] .ex-reader .ex-guide-facts dd{font-size:13px}
html[data-sciver-theme] .ex-reader .ex-md>:first-child{margin-top:0}
html[data-sciver-theme] .ex-reader .ex-md>:last-child{margin-bottom:0}
html[data-sciver-theme] .ex-reader .ex-md p{margin:0 0 14px}
html[data-sciver-theme] .ex-reader .ex-md :is(h1,h2,h3,h4,h5,h6){line-height:1.45;font-weight:650;letter-spacing:0;margin:28px 0 12px;scroll-margin-top:16px}
html[data-sciver-theme] .ex-reader .ex-md h1{font-size:24px;color:var(--read-blue);padding-bottom:12px;border-bottom:1px solid var(--read-border)}
html[data-sciver-theme] .ex-reader .ex-md h2{font-size:19px;color:var(--read-blue);padding-bottom:8px;border-bottom:1px solid var(--read-border)}
html[data-sciver-theme] .ex-reader .ex-md h3{font-size:16px;color:var(--read-purple)}
html[data-sciver-theme] .ex-reader .ex-md :is(h4,h5,h6){font-size:14px;color:var(--read-teal)}
html[data-sciver-theme] .ex-reader .ex-md strong{font-weight:650;color:var(--read-text)}
html[data-sciver-theme] .ex-reader .ex-md :is(a,a[data-ref]){color:var(--read-blue);text-decoration:underline;text-decoration-color:color-mix(in srgb,var(--read-blue) 45%,transparent);text-underline-offset:3px;cursor:pointer}
html[data-sciver-theme] .ex-reader .ex-md :is(a:hover,a:focus-visible){text-decoration-color:currentColor}
html[data-sciver-theme] .ex-reader .ex-md :is(ul,ol){margin:0 0 16px;padding-left:23px}
html[data-sciver-theme] .ex-reader .ex-md li{margin:5px 0}
html[data-sciver-theme] .ex-reader .ex-md li::marker{color:var(--read-blue)}
html[data-sciver-theme] .ex-reader .ex-md li>p{margin:5px 0}
html[data-sciver-theme] .ex-reader .ex-md blockquote{margin:18px 0;padding:13px 16px;border:0;border-left:3px solid var(--read-teal);border-radius:0 8px 8px 0;background:var(--read-quote);color:var(--read-text)}
html[data-sciver-theme] .ex-reader .ex-md code{padding:2px 5px;border-radius:4px;background:var(--read-soft);color:var(--read-purple);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.88em}
html[data-sciver-theme] .ex-reader .ex-md pre{overflow:auto;margin:18px 0;padding:16px;border:1px solid var(--read-border);border-radius:9px;background:var(--read-code-bg);color:var(--read-text);line-height:1.65;tab-size:4}
html[data-sciver-theme] .ex-reader .ex-md pre code{padding:0;border:0;background:transparent;color:inherit;font-size:12px;white-space:pre;overflow-wrap:normal}
html[data-sciver-theme] .ex-reader .ex-md table{display:block;max-width:100%;overflow:auto;border-collapse:separate;border-spacing:0;margin:18px 0;font-size:13px;border:1px solid var(--read-border);border-radius:8px}
html[data-sciver-theme] .ex-reader .ex-md :is(th,td){padding:9px 12px;line-height:1.6;border:0;border-bottom:1px solid var(--read-border)}
html[data-sciver-theme] .ex-reader .ex-md th{position:static;background:var(--read-soft);color:var(--read-blue);font-weight:650}
html[data-sciver-theme] .ex-reader .ex-md tr:nth-child(even) td{background:color-mix(in srgb,var(--read-soft) 45%,transparent)}
html[data-sciver-theme] .ex-reader .ex-md tr:last-child td{border-bottom:0}
html[data-sciver-theme] .ex-reader .ex-md hr{height:1px;border:0;background:var(--read-border);margin:24px 0}
html[data-sciver-theme] .ex-reader .ex-md img{max-width:100%;height:auto;border-radius:8px}
html[data-sciver-theme] .ex-reader .ex-md .katex-display{overflow-x:auto;overflow-y:hidden;padding:10px 0;color:var(--read-blue)}
html[data-sciver-theme] .ex-reader .ex-md .token:is(.comment,.prolog,.doctype,.cdata){color:var(--read-muted)}
html[data-sciver-theme] .ex-reader .ex-md .token:is(.keyword,.boolean,.tag,.atrule){color:var(--read-purple)}
html[data-sciver-theme] .ex-reader .ex-md .token:is(.string,.attr-value,.builtin){color:var(--read-teal)}
html[data-sciver-theme] .ex-reader .ex-md .token:is(.function,.class-name,.number,.property){color:var(--read-blue)}
html[data-sciver-theme] .ex-reader .ex-md .token:is(.operator,.punctuation){color:var(--read-text)}
@media(max-width:600px){html[data-sciver-theme] .ex-reader{padding:12px}.ex-reader-card{padding:16px}.ex-reader .ex-hypothesis-title{font-size:19px}.ex-verdict{flex-wrap:wrap}.ex-verdict small{max-width:100%;overflow-wrap:anywhere}}
</style>
"""
