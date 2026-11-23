"""Scoped presentation and local filters for the read-only review archive."""

STYLE = """<style data-review-report-style>
body.tm-reviews-view .tm-page-head{display:none}
.rv-page{--rv-pass:#6ee7b7;--rv-fail:#fca5a5;--rv-score:#f5cc80;max-width:1280px;margin:4px auto 40px;color:var(--tm-text);line-height:1.7;min-width:0}
html[data-sciver-theme="white"] .rv-page{--rv-pass:#126747;--rv-fail:#a91e3a;--rv-score:#805000}
.rv-page *{box-sizing:border-box}
.rv-page [hidden]{display:none!important}
.rv-page ::selection{background:var(--tm-accent);color:var(--tm-bg)}
.rv-page a{color:var(--tm-link,var(--tm-accent));text-underline-offset:4px}
.rv-page a:hover{color:var(--tm-text)}
.rv-page :focus-visible{outline:2px solid var(--tm-accent);outline-offset:4px}
.rv-page h1{font-size:clamp(27px,3vw,38px);font-weight:680;line-height:1.3;letter-spacing:-.03em;text-wrap:balance;margin:10px 0 12px}
.rv-page h2{font-size:22px;font-weight:650;line-height:1.4;margin:0 0 12px}
.rv-page h3{font-size:16px;margin:27px 0 10px}
.rv-page p{max-width:74ch;overflow-wrap:anywhere}
.rv-heading{padding:6px 0 28px;border-bottom:1px solid var(--tm-border)}
.rv-heading>p{font-size:15px;color:var(--tm-muted);margin:0}
.rv-back{font-size:13px;display:inline-block;margin-bottom:10px}
.rv-report-meta,.rv-entry-meta{display:flex;flex-wrap:wrap;gap:7px 24px;color:var(--tm-muted);font-size:12px;margin-top:17px}
.rv-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:12px 24px;margin:22px 0}
.rv-filters{display:flex;gap:4px;flex-wrap:wrap}
.rv-filters button{min-height:38px;border:1px solid transparent;border-radius:6px;background:transparent;color:var(--tm-muted);padding:6px 12px;font-size:13px;white-space:nowrap}
.rv-filters button:hover{background:var(--tm-hover);color:var(--tm-text)}
.rv-filters button[aria-pressed="true"]{border-color:var(--tm-border);background:var(--tm-hover);color:var(--tm-text);font-weight:650}
.rv-search{display:flex;align-items:center;gap:10px;margin-left:auto;color:var(--tm-muted);font-size:12px}
.rv-search input{border:1px solid var(--tm-border);background:var(--tm-panel);color:var(--tm-text);border-radius:6px;min-height:38px;width:230px;max-width:100%;padding:8px 12px;caret-color:var(--tm-accent)}
.rv-search input::placeholder{color:var(--tm-muted);opacity:1}
.rv-count{color:var(--tm-muted);font-size:12px;margin:0;font-variant-numeric:tabular-nums}
.rv-archive-list{border-top:1px solid var(--tm-border)}
.rv-archive-entry{display:grid;grid-template-columns:56px minmax(0,1fr) auto;align-items:start;gap:22px;padding:28px 0;border-bottom:1px solid var(--tm-border)}
.rv-archive-mark{width:52px;height:60px;display:grid;place-items:center;background:var(--tm-hover);color:var(--tm-accent);border-radius:6px;font-size:28px;font-weight:600;line-height:1;margin-top:6px}
.rv-archive-mark svg{width:25px;height:25px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linejoin:round;stroke-linecap:round}
.rv-entry-main{min-width:0}
.rv-entry-meta{margin:0 0 9px;gap:6px 18px}
.rv-entry-meta span:first-child{color:var(--tm-accent)}
.rv-entry-main h2{font-size:21px;margin:0 0 9px}
.rv-entry-main h2 a{text-decoration:none}
.rv-entry-main h2 a:hover{text-decoration:underline}
.rv-entry-main p{font-size:14px;line-height:1.8;margin:0 0 10px;color:var(--tm-text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.rv-entry-scope{font-size:12px;color:var(--tm-muted);overflow-wrap:anywhere}
.rv-open{font-size:13px;padding:8px 0;margin-top:33px;white-space:nowrap}
.rv-empty{padding:36px 0;color:var(--tm-muted)}
.rv-report-layout{display:grid;grid-template-columns:155px minmax(0,1fr);gap:35px;align-items:start}
.rv-outline{position:sticky;top:28px;display:flex;flex-direction:column;gap:5px;padding-top:34px;font-size:13px}
.rv-outline a{display:block;padding:6px 10px;border-radius:5px;color:var(--tm-muted);text-decoration:none}
.rv-outline a:hover,.rv-outline a:focus-visible{background:var(--tm-hover);color:var(--tm-text)}
.rv-report-content{min-width:0}
.rv-section{padding:35px 0;border-bottom:1px solid var(--tm-border);scroll-margin-top:25px}
.rv-section:last-child{border-bottom:0}
.rv-verdict{font-size:17px;line-height:1.9;margin:0;max-width:70ch}
.rv-section-intro{font-size:14px;color:var(--tm-muted);margin:0 0 20px}
.rv-summary-line{display:flex;gap:30px;flex-wrap:wrap;margin:23px 0 16px}
.rv-summary-line div{display:flex;align-items:baseline;gap:12px}
.rv-summary-line dt{font-size:12px;color:var(--tm-muted)}
.rv-summary-line dd{font-size:20px;line-height:1.3;font-weight:620;font-variant-numeric:tabular-nums;margin:0}
.rv-footnote{font-size:12px;color:var(--tm-muted);line-height:1.8;margin:14px 0 0;max-width:78ch!important}
.rv-status{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;font-size:12px;font-weight:600}
.rv-status::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.rv-status-pass{color:var(--rv-pass)}
.rv-status-fail{color:var(--rv-fail)}
.rv-status-continuous{color:var(--rv-score)}
.rv-status-pending{color:var(--tm-muted)}
.rv-result-strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border:1px solid var(--tm-border);border-radius:8px;overflow:hidden;margin:22px 0 16px;background:var(--tm-panel)}
.rv-result-cell{display:flex;flex-direction:column;gap:7px;padding:11px 12px;text-decoration:none;border-right:1px solid var(--tm-border);border-bottom:1px solid var(--tm-border);font-variant-numeric:tabular-nums}
.rv-result-cell:nth-child(6n){border-right:0}
.rv-result-cell:nth-last-child(-n+6){border-bottom:0}
.rv-result-cell>span:first-child{font-size:13px;color:var(--tm-text);font-weight:600}
.rv-result-cell:hover{background:var(--tm-hover)}
.rv-table-scroll{overflow:auto;max-width:100%;scrollbar-color:var(--tm-muted) var(--tm-panel);scrollbar-width:thin}
.rv-table-scroll>table{min-width:560px}
.rv-page table{width:100%;font-size:13px;line-height:1.7;border-collapse:collapse;margin:0}
.rv-page table th,.rv-page table td{padding:13px 12px;border-bottom:1px solid var(--tm-border);text-align:left;vertical-align:top;overflow-wrap:anywhere}
.rv-page thead th{position:static;background:var(--tm-panel);font-size:11px;color:var(--tm-muted);font-weight:550;white-space:nowrap}
.rv-page tbody th{background:transparent;color:var(--tm-text);font-weight:550}
.rv-page .rv-case-table{min-width:800px}
.rv-case-table th:first-child{width:145px}
.rv-case-table td:nth-child(2){width:116px}
.rv-case-table .rv-quality{min-width:195px;max-width:290px;color:var(--tm-muted);font-size:12px}
.rv-number{font-variant-numeric:tabular-nums;white-space:nowrap}
.rv-native{font-size:12px;color:var(--tm-muted)}
.rv-case-toggle{border:0;background:transparent!important;text-align:left;color:var(--tm-text);display:flex;flex-direction:column;padding:0;gap:4px;font-size:13px;min-height:42px;min-width:100px;line-height:1.7}
.rv-case-toggle>span{color:var(--tm-accent);font-size:11px}
.rv-case-toggle:hover{text-decoration:underline;text-underline-offset:4px}
.rv-case-toggle[aria-expanded="true"]{font-weight:650}
.rv-case-detail>td{padding:0!important}
.rv-case-detail-inner{padding:22px 20px 26px;background:var(--tm-hover)}
.rv-case-detail h3{margin-top:0}
.rv-case-detail table{max-width:760px;table-layout:fixed}
.rv-case-detail table th:first-child{width:55%;word-break:break-word}
.rv-case-detail table td{font-variant-numeric:tabular-nums}
.rv-raw-usage{margin-top:18px}
.rv-page summary{cursor:pointer;color:var(--tm-link,var(--tm-accent));font-size:13px;padding:6px 0;min-height:34px}
.rv-missing{color:var(--rv-score)}
.rv-ledger{max-width:690px;margin:20px 0 24px;font-variant-numeric:tabular-nums}
.rv-ledger>div{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:22px;padding:10px 0;border-bottom:1px solid var(--tm-border);align-items:baseline}
.rv-ledger dt{font-size:13px;color:var(--tm-muted)}
.rv-ledger dd{font-size:17px;font-weight:550;margin:0}
.rv-ledger>div:first-child dd{font-size:23px;color:var(--tm-accent)}
.rv-prose{font-size:14px;line-height:1.9;max-width:74ch;padding-left:20px}
div.rv-prose{padding:0}
.rv-prose li{padding-left:3px;margin:10px 0}
.rv-settings{display:flex;flex-wrap:wrap;gap:12px 35px;margin:18px 0}
.rv-settings dt{color:var(--tm-muted);font-size:12px}
.rv-settings dd{margin:3px 0 0;font-size:15px;font-weight:550}
.rv-notes{padding-left:20px;max-width:78ch}
.rv-notes li{padding-left:4px;margin:0 0 18px;font-size:14px;line-height:1.9}
.rv-evidence{padding:20px 0;border-top:1px solid var(--tm-border)}
.rv-evidence-heading{display:flex;align-items:baseline;justify-content:space-between;gap:15px;flex-wrap:wrap}
.rv-evidence h3{font-size:14px;margin:0}
.rv-evidence-heading a{font-size:12px;white-space:nowrap}
.rv-evidence-group{margin-top:22px}
.rv-evidence-group>summary{font-weight:600;padding:12px 0}
.rv-source-links{display:flex;gap:10px 24px;flex-wrap:wrap;font-size:13px;margin:12px 0 18px}
.rv-proof{font-size:11px;color:var(--tm-muted);overflow-wrap:anywhere;word-break:break-word;max-width:100%!important}
.rv-proof code{font-size:inherit}
.rv-page pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.9;max-width:100%;padding:16px;background:var(--tm-panel);border-radius:6px}
.rv-sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(min-width:1600px){.rv-report-layout{grid-template-columns:170px minmax(0,1fr);gap:50px}}
@media(max-width:1050px){.rv-report-layout{grid-template-columns:minmax(0,1fr);gap:0}.rv-outline{position:static;flex-direction:row;flex-wrap:wrap;gap:0;padding-top:18px;padding-bottom:16px;border-bottom:1px solid var(--tm-border)}.rv-outline a{padding:7px 11px}.rv-search{margin-left:0}.rv-count{margin-left:auto}}
@media(max-width:600px){.rv-heading{padding-bottom:22px}.rv-page h1{font-size:28px}.rv-page h2{font-size:20px}.rv-archive-entry{grid-template-columns:36px minmax(0,1fr);gap:13px;padding:23px 0}.rv-archive-mark{width:34px;height:43px;font-size:22px}.rv-archive-mark svg{width:21px;height:21px}.rv-entry-main h2{font-size:18px}.rv-open{grid-column:2;margin-top:-8px;padding:0}.rv-entry-meta{font-size:11px}.rv-search{width:100%;justify-content:space-between}.rv-search input{flex:1;min-width:0;width:auto}.rv-summary-line{gap:12px 22px}.rv-summary-line div{gap:9px}.rv-summary-line dd{font-size:18px}.rv-result-strip{grid-template-columns:repeat(3,minmax(0,1fr))}.rv-result-cell:nth-child(3n){border-right:0}.rv-result-cell:nth-last-child(-n+6){border-bottom:1px solid var(--tm-border)}.rv-result-cell:nth-last-child(-n+3){border-bottom:0}.rv-section{padding:27px 0}.rv-verdict{font-size:16px}.rv-ledger dd{font-size:15px}.rv-ledger dt{font-size:12px}.rv-ledger>div{gap:12px}.rv-case-detail-inner{padding:18px 12px}.rv-evidence-heading{gap:8px}.rv-outline{font-size:12px}.rv-outline a{padding:7px 9px}}
@media(max-width:600px){
 .rv-result-cell{padding:10px 8px}.rv-result-cell .rv-status{font-size:11px}
 .rv-page .rv-case-table{display:block;min-width:0}
 .rv-case-table>thead{display:none}
 .rv-case-table>tbody{display:block;border-bottom:1px solid var(--tm-border)}
 .rv-case-table>tbody>tr:not(.rv-case-detail){display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 14px;padding:19px 0}
 .rv-page .rv-case-table>tbody>tr>th,.rv-page .rv-case-table>tbody>tr>td{border:0;padding:0;width:auto}
 .rv-case-table>tbody>tr>th:first-child{grid-column:1}
 .rv-case-table>tbody>tr>td:nth-child(2){grid-column:2}
 .rv-case-table>tbody>tr>.rv-quality{grid-column:1/-1;min-width:0;max-width:none}
 .rv-case-table>tbody>tr>td:nth-child(4){grid-column:1;font-size:12px}
 .rv-case-table>tbody>tr>td:nth-child(4)::before{content:"Goal token ";color:var(--tm-muted);font-size:11px}
 .rv-case-table>tbody>tr>td:nth-child(5){grid-column:2;font-size:12px}
 .rv-case-table>tbody>tr>td:nth-child(5)::before{content:"墙时 ";color:var(--tm-muted);font-size:11px}
 .rv-case-table>tbody>tr:not(.rv-case-detail)>td:last-child{grid-column:1/-1}
 .rv-case-table>tbody>tr:not(.rv-case-detail)>td:last-child::before{content:"Goal ";color:var(--tm-muted);font-size:11px}
 .rv-case-toggle{flex-direction:row;gap:10px;min-height:32px;align-items:baseline;min-width:0}
 .rv-case-detail,.rv-case-detail>td{display:block!important}
 .rv-case-detail[hidden]{display:none!important}
 .rv-case-detail-inner{margin:0 0 17px}
 .rv-case-detail .rv-table-scroll>table.rv-metric-table{display:block;min-width:0;width:100%;table-layout:auto}
 .rv-metric-table>thead{display:none}
 .rv-metric-table>tbody{display:block}
 .rv-metric-table>tbody>tr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 14px;padding:13px 0;border-bottom:1px solid var(--tm-border)}
 .rv-page .rv-metric-table>tbody>tr>th,.rv-page .rv-metric-table>tbody>tr>td{padding:0;border:0;width:auto}
 .rv-page .rv-metric-table>tbody>tr>th:first-child{grid-column:1/-1;width:auto;overflow-wrap:anywhere;font-size:12px}
 .rv-metric-table>tbody>tr>td:nth-child(2){grid-column:1;font-size:14px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
 .rv-metric-table>tbody>tr>td:nth-child(2)::before{content:"数值 ";font-size:11px;color:var(--tm-muted)}
 .rv-metric-table>tbody>tr>td:nth-child(3){grid-column:2;font-size:12px;color:var(--tm-muted)}
}
@media print{.rv-outline,.rv-toolbar,.rv-case-toggle{color:inherit}.rv-case-detail[hidden]{display:table-row!important}.rv-report-layout{display:block}.rv-outline,.rv-toolbar,.rv-back{display:none}.rv-page{color:#111;--tm-muted:#444;--tm-border:#ccc;--tm-panel:#fff;--tm-hover:#fff;--rv-pass:#126747;--rv-fail:#a91e3a;--rv-score:#805000}.rv-table-scroll{overflow:visible}.rv-page .rv-case-table{min-width:0}}
</style>"""

SCRIPT = """<script data-review-report-script>
(() => {
  'use strict';
  const archive = document.querySelector('[data-review-archive]');
  if (archive) {
    let category = 'all';
    const search = archive.querySelector('[data-review-search]');
    const update = () => {
      const query = (search?.value || '').trim().toLocaleLowerCase();
      let count = 0;
      archive.querySelectorAll('[data-review-entry]').forEach(entry => {
        entry.hidden = !((category === 'all' || entry.dataset.kind === category) && entry.textContent.toLocaleLowerCase().includes(query));
        if (!entry.hidden) count++;
      });
      const counter = archive.querySelector('[data-review-count]');
      if (counter) counter.textContent = `${count} 份记录`;
      archive.querySelector('[data-review-no-results]').hidden = count !== 0 || !search;
    };
    archive.querySelectorAll('[data-review-filter]').forEach(button => button.addEventListener('click', () => {
      category = button.dataset.reviewFilter;
      archive.querySelectorAll('[data-review-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      update();
    }));
    search?.addEventListener('input', update);
  }
  const report = document.querySelector('[data-review-report]');
  if (!report) return;
  const applyFilter = kind => {
    let count = 0;
    report.querySelectorAll('[data-case-entry]').forEach(entry => {
      entry.hidden = kind !== 'all' && entry.dataset.kind !== kind;
      if (!entry.hidden) count++;
    });
    report.querySelectorAll('[data-case-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.caseFilter === kind)));
    const counter = report.querySelector('[data-case-count]');
    if (counter) counter.textContent = `${count} 道题目`;
    const empty = report.querySelector('[data-case-no-results]');
    if (empty) empty.hidden = count !== 0;
  };
  report.querySelectorAll('[data-case-filter]').forEach(button => button.addEventListener('click', () => applyFilter(button.dataset.caseFilter)));
  report.querySelectorAll('.rv-case-toggle').forEach(button => button.addEventListener('click', () => {
    const detail = document.getElementById(button.getAttribute('aria-controls'));
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    detail.hidden = expanded;
  }));
  const openCase = id => {
    const row = document.getElementById(id);
    const button = row?.querySelector('.rv-case-toggle');
    if (!button) return;
    applyFilter('all');
    button.setAttribute('aria-expanded', 'true');
    document.getElementById(button.getAttribute('aria-controls')).hidden = false;
  };
  report.querySelectorAll('[data-open-case]').forEach(link => link.addEventListener('click', () => openCase(`case-${link.dataset.openCase}`)));
  if (/^#case-\\d+$/.test(location.hash)) openCase(location.hash.slice(1));
})();
</script>"""
