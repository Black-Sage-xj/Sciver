"""Read-only archive and benchmark surfaces using operator-owned report data."""
from __future__ import annotations

import html
import math
from collections.abc import Callable
from fnmatch import fnmatchcase
from urllib.parse import quote
from urllib.parse import urlsplit

from .review_reports_style import STYLE, SCRIPT


def e(value) -> str:
    return html.escape(str(value), quote=True)


def number(value) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return "无有效数值"
        return f"{value:,}" if isinstance(value, int) else repr(value)
    return str(value)


def duration(value) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return "未提供"
    seconds = round(value)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def table(columns: list, rows: list, label: str, *, class_name: str = '') -> str:
    head = ''.join(f'<th scope="col">{e(v)}</th>' for v in columns)
    body = ''.join('<tr>' + ''.join(
        f'<th scope="row">{e(v)}</th>' if index == 0 else f'<td>{e(v)}</td>'
        for index, v in enumerate(row)) + '</tr>' for row in rows)
    return (f'<div class="rv-table-scroll" role="region" aria-label="{e(label)}" tabindex="0">'
            f'<table class="{e(class_name)}"><caption class="rv-sr-only">{e(label)}</caption><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def archive_body(entries: list[tuple[str, dict]]) -> str:
    rows = []
    for slug, report in entries:
        benchmark = isinstance(report.get('benchmark'), dict)
        kind = 'benchmark' if benchmark else 'review'
        category = report.get('category') or ('测试结果' if benchmark else '历史审查')
        summary = str(report.get('archive_summary') or report.get('result') or '')
        series = str(report.get('series') or '')
        marker = e(series) if series else (
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l3 3v15H6zM14 3v5h4M9 12h6M9 16h6"/></svg>')
        rows.append(
            f'<article class="rv-archive-entry" data-review-entry data-kind="{kind}">'
            f'<div class="rv-archive-mark" aria-hidden="true">{marker}</div>'
            f'<div class="rv-entry-main"><div class="rv-entry-meta"><span>{e(category)}</span>'
            f'<span>{e(report.get("snapshot_at") or report.get("reviewed_at", ""))}</span></div>'
            f'<h2><a href="/reviews/{e(slug)}/">{e(report["title"])}</a></h2>'
            f'<p>{e(summary)}</p><div class="rv-entry-scope">{e(report.get("scope", ""))}</div></div>'
            f'<a class="rv-open" href="/reviews/{e(slug)}/" aria-label="阅读{e(report["title"])}">阅读说明</a>'
            '</article>')
    tools = (f'<div class="rv-toolbar" data-review-controls><div class="rv-filters" role="group" aria-label="记录类型">'
             '<button type="button" data-review-filter="all" aria-pressed="true">全部记录</button>'
             '<button type="button" data-review-filter="benchmark" aria-pressed="false">测试结果</button>'
             '<button type="button" data-review-filter="review" aria-pressed="false">历史审查</button></div>'
             '<label class="rv-search">搜索记录<input type="search" data-review-search placeholder="题组、模型或报告名称"></label>'
             f'<p class="rv-count" data-review-count role="status">{len(entries)} 份记录</p></div>')
    empty = '<p class="rv-empty">暂无已归档的报告。测试结束后，可在这里阅读结果和原始记录。</p>'
    return (STYLE + '<article class="rv-page" lang="zh-CN" data-review-archive><header class="rv-heading">'
            '<h1>审查报告</h1><p>从测试结果到原始证据，读懂质量、消耗与运行条件。</p></header>'
            + (tools if entries else '') + '<div class="rv-archive-list">' + (''.join(rows) or empty)
            + '</div><p class="rv-empty" data-review-no-results hidden>没有匹配的记录。请更换关键词或记录类型。</p>'
            '</article>' + SCRIPT)


def _downloads(slug: str, report: dict, load: Callable[[str], bytes]) -> str:
    groups: dict[str, list[str]] = {}
    for item in report.get('details', []):
        name = item['file']
        href = '/reviews/' + quote(slug, safe='') + '/files/' + quote(name, safe='')
        preview = ''
        if item.get('preview', True):
            text = load(name).decode('utf-8', errors='replace')
            if len(text) > 30000:
                text = text[:30000] + '\n\n预览已截断，请下载完整原始记录。'
            preview = '<details><summary>展开文本预览</summary><pre>' + e(text) + '</pre></details>'
        checksum = item.get('sha256')
        proof = '<p class="rv-proof">SHA-256 <code>' + e(checksum) + '</code></p>' if checksum else ''
        groups.setdefault(str(item.get('group', '')), []).append(
            '<div class="rv-evidence"><div class="rv-evidence-heading"><h3>' + e(item['title'])
            + '</h3><a href="' + e(href) + '" download>下载原始记录</a></div>' + proof + preview + '</div>')
    items = []
    for group, entries in groups.items():
        if group:
            label = report.get('detail_groups', {}).get(group, group)
            items.append('<details class="rv-evidence-group"><summary>' + e(label)
                         + f'（{len(entries)} 份）</summary>' + ''.join(entries) + '</details>')
        else:
            items.extend(entries)
    return '<section id="evidence" class="rv-section"><h2>原始记录</h2>' + (
        ''.join(items) or '<p>此报告没有附加原始记录。</p>') + '</section>'


def _status(case: dict) -> tuple[str, str]:
    if case.get('official_pass') is True:
        return 'pass', '通过'
    if case.get('official_pass') is False:
        return 'fail', '未通过'
    state = case.get('evaluation', {}).get('status', '')
    if state == 'continuous_partial_scored':
        return 'continuous', '部分连续评分'
    if state == 'continuous_no_measurements':
        return 'continuous', '无有效评分'
    return 'pending', '未评分'


def _badge(kind: str, label: str) -> str:
    return f'<span class="rv-status rv-status-{e(kind)}">{e(label)}</span>'


def _metric_rows(value, prefix='') -> list:
    if isinstance(value, dict):
        return [row for key, nested in value.items()
                for row in _metric_rows(nested, f'{prefix}/{key}' if prefix else str(key))]
    if isinstance(value, list):
        return [row for index, nested in enumerate(value)
                for row in _metric_rows(nested, f'{prefix}/{index}')]
    return [[prefix, number(value)]]


def _cases(benchmark: dict) -> str:
    cases = benchmark['results']['cases']
    notes = benchmark.get('case_notes', {})
    contexts = benchmark.get('case_context', {})
    rows = []
    for index, case in enumerate(cases):
        key = case['case']
        kind, label = _status(case)
        anchor = f'case-{index + 1}'
        native = case.get('native_goal_status', '未记录')
        summary = notes.get(key, case.get('evaluation', {}).get('coverage', '查看原始指标明细。'))
        metrics = _metric_rows(case.get('metrics', {}))
        context = contexts.get(key, {})
        for row in metrics:
            direction = next((direction for pattern, direction in context.get('metric_directions', {}).items()
                              if fnmatchcase(row[0], pattern)), None)
            row.append({'min': '越低越好', 'max': '越高越好'}.get(direction, '见原题'))
        metric_table = table(['原始指标', '数值', '比较方向'], metrics, f'{key} 原始质量指标',
                             class_name='rv-metric-table') if metrics else (
            '<p class="rv-missing">没有有效评分数值。缺失评分不按 0 分处理。</p>')
        missing = case.get('missing_quality_metrics', [])
        missing_html = ('<p class="rv-missing">缺少的指标：' + e('；'.join(missing)) + '</p>') if missing else ''
        usage_rows = [[name, number(value)] for name, value in case.get('tokens', {}).items()]
        verifier = case.get('verifier_summary') or {}
        verifier_text = ('<p>官方检查：' + e(f"通过 {verifier.get('passed', 0)} / {verifier.get('tests', 0)} 项")
                         + '。子项通过不等于整题通过。</p>') if verifier else ''
        source = case.get('evaluation', {}).get('native_result_sha256')
        proof = '<p class="rv-proof">原始评分 SHA-256 <code>' + e(source) + '</code></p>' if source else ''
        source_html = ''
        url = context.get('source_url', '')
        parsed = urlsplit(url)
        if parsed.scheme in {'https', 'http'} and parsed.netloc:
            source_html = '<p><a href="' + e(url) + '" rel="noreferrer">查看官方题目来源</a></p>'
        question = '<p>' + e(context['description']) + '</p>' if context.get('description') else ''
        links = ''.join('<a href="' + e(context[field]) + '">' + label + '</a>'
                        for field, label in [('question_href', '官方原题'), ('plan_href', '本次冻结 Research Plan')]
                        if str(context.get(field, '')).startswith('/reviews/'))
        if links:
            source_html += '<div class="rv-source-links">' + links + '</div>'
        detail = (f'<tr id="{anchor}-detail" class="rv-case-detail" hidden><td colspan="6">'
                  f'<div class="rv-case-detail-inner"><h3>{e(key)} {e(case["title"])}：评分与用量明细</h3>'
                  f'{question}{source_html}<p>{e(summary)}</p>{verifier_text}{missing_html}{metric_table}'
                  '<details class="rv-raw-usage"><summary>查看原始 token 字段</summary>'
                  + table(['字段', '累计用量'], usage_rows, f'{key} 原始 token 字段')
                  + '</details>' + proof + '</div></td></tr>')
        rows.append(f'<tbody data-case-entry data-kind="{e(kind)}"><tr id="{anchor}">'
                    f'<th scope="row"><button type="button" class="rv-case-toggle" aria-expanded="false" '
                    f'aria-controls="{anchor}-detail"><span>{e(key)}</span>{e(case["title"])}</button></th>'
                    f'<td>{_badge(kind, label)}</td><td class="rv-quality">{e(summary)}</td>'
                    f'<td class="rv-number">{e(number(case.get("native_goal_tokens_used")))}</td>'
                    f'<td class="rv-number">{e(duration(case.get("wall_seconds")))}</td>'
                    f'<td><span class="rv-native">{e(native)}</span></td></tr>{detail}</tbody>')
    strip = ''.join(f'<a href="#case-{i + 1}" class="rv-result-cell" data-open-case="{i + 1}">'
                    f'<span>{e(case["case"])}</span>{_badge(*_status(case))}</a>' for i, case in enumerate(cases))
    filters = ''.join(f'<button type="button" data-case-filter="{kind}" aria-pressed="{str(kind == "all").lower()}">{label}</button>'
                      for kind, label in [('all', '全部题目'), ('pass', '官方通过'), ('fail', '官方未通过'), ('continuous', '连续评分')])
    return ('<section id="cases" class="rv-section"><h2>逐题结果</h2>'
            '<p class="rv-section-intro">点击题目查看完整指标与用量。官方结果与 Goal 自判状态分别记录。</p>'
            f'<div class="rv-result-strip" aria-label="逐题官方评分速览">{strip}</div>'
            f'<div class="rv-toolbar"><div class="rv-filters" role="group" aria-label="按官方结果筛选">{filters}</div>'
            f'<p class="rv-count" data-case-count role="status">{len(cases)} 道题目</p></div>'
            '<div class="rv-table-scroll" role="region" aria-label="逐题成绩与消耗" tabindex="0"><table class="rv-case-table">'
            '<caption class="rv-sr-only">逐题成绩与消耗，点击题目展开明细</caption><thead><tr>'
            '<th scope="col">题目与明细</th><th scope="col">官方评分</th><th scope="col">质量与完成范围</th>'
            '<th scope="col">Goal tokensUsed</th><th scope="col">墙时</th><th scope="col">Goal 状态</th>'
            '</tr></thead>' + ''.join(rows) + '</table></div>'
            '<p class="rv-empty" data-case-no-results hidden>这个分类下暂无题目。</p>'
            '<p class="rv-footnote">complete 表示模型自行结束；blocked 表示模型停止推进。两者均不替代官方评分。</p></section>')


def _usage(results: dict) -> str:
    raw = results.get('raw_token_usage', {})
    fields = [('原生 Goal tokensUsed', results.get('native_goal_tokens_used')),
              ('累计请求 token（含缓存）', raw.get('totalTokens')),
              ('输入 token', raw.get('inputTokens')),
              ('其中缓存输入', raw.get('cachedInputTokens')),
              ('输出 token', raw.get('outputTokens')),
              ('其中推理输出', raw.get('reasoningOutputTokens'))]
    values = ''.join('<div><dt>' + e(label) + '</dt><dd>' + e(number(value)) + '</dd></div>' for label, value in fields)
    seconds = results.get('native_wall_sum_seconds')
    hours = f'{seconds / 3600:.4f} 小时' if isinstance(seconds, (int, float)) else '未提供'
    return ('<section id="usage" class="rv-section"><h2>消耗如何计算</h2><dl class="rv-ledger">' + values
            + '<div><dt>各题墙时之和</dt><dd>' + e(hours) + '</dd></div></dl>'
            '<div class="rv-prose"><p>本批 CLI 的 tokensUsed 为未缓存输入加输出。缓存输入属于输入的子集，推理输出属于输出的子集，均不重复相加。</p>'
            '<p>缓存输入不等于免费。墙时包含模型等待和工具计算；各题墙时之和不是整批经过时间，也不是 CPU 核时。</p>'
            '<p>账号未提供逐题美元账单，页面不推算金额。原始字段与逐题记录可在下方下载。</p></div></section>')


def _comparison(benchmark: dict) -> str:
    comparison = benchmark.get('quality_comparison')
    if not comparison:
        return ''
    rows = []
    for row in comparison['rows']:
        mean, baseline = row['mean'], row['baseline']
        rows.append([row['dataset'], row['metric'], f'{mean:.6f} ± {row["se"]:.6f}',
                     f'{baseline:.6f}', f'{mean - baseline:+.6f}'])
    return ('<section id="quality" class="rv-section"><h2>' + e(comparison['title']) + '</h2><p class="rv-section-intro">'
            + e(comparison['description']) + '</p>'
            + table(['数据集', '指标 ↑', '本次均值 ± 标准误', '题面基线均值', '差值'], rows, comparison['title'])
            + '<p class="rv-footnote">' + e(comparison['conclusion']) + '</p></section>')


def _environment(benchmark: dict) -> str:
    environment = benchmark.get('environment', {})
    limitations = ''.join('<li>' + e(text) + '</li>' for text in environment.get('history_notes', []))
    preparation = benchmark.get('preparation')
    future = ''
    if preparation:
        settings = preparation.get('settings', {})
        future = ('<h3>下一轮 Sciver 对照</h3><p>' + e(preparation.get('description', '')) + '</p>'
                  '<dl class="rv-settings"><div><dt>科研角色模型</dt><dd>' + e(settings.get('model', '未指定'))
                  + '</dd></div><div><dt>推理档位</dt><dd>' + e(settings.get('reasoning_effort', '未指定'))
                  + '</dd></div><div><dt>最大 Cycle</dt><dd>' + e(settings.get('max_cycles', '未指定'))
                  + '</dd></div></dl><p class="rv-footnote">允许按既有完成条件提前结束；达到 Cycle 上限不等于官方通过。</p>'
                  + table(['题目', '公开材料', '环境与执行绑定', '启动状态'], preparation.get('rows', []), '下一轮 Sciver 准备状态')
                  + '<p class="rv-footnote">准备状态快照：' + e(preparation.get('snapshot_at', '未记录'))
                  + '。不是实时运行监控。</p>')
    return ('<section id="environment" class="rv-section"><h2>环境与下一轮测试</h2><h3>本次结果适用范围</h3>'
            '<ul class="rv-prose">' + limitations + '</ul>' + future + '</section>')


def report_body(slug: str, report: dict, load: Callable[[str], bytes]) -> str:
    benchmark = report.get('benchmark')
    is_benchmark = isinstance(benchmark, dict)
    title = e(report['title'])
    meta = e(report.get('snapshot_at') or report.get('reviewed_at', ''))
    header = ('<header class="rv-heading"><a class="rv-back" href="/reviews/">全部审查报告</a>'
              f'<h1>{title}</h1><p>{e(report.get("scope", ""))}</p><div class="rv-report-meta"><span>{meta}</span>')
    if is_benchmark:
        results = benchmark['results']
        header += '<span>' + e(results.get('model', '')) + ' / ' + e(results.get('reasoning_effort', '')) + '</span>'
    header += '</div></header>'
    navigation = [('overview', '结论')]
    if is_benchmark:
        navigation += [('cases', '逐题结果'), ('usage', '消耗口径')]
        if benchmark.get('quality_comparison'):
            navigation += [('quality', '连续质量')]
        navigation += [('environment', '环境与后续')]
    else:
        navigation += [('metrics', '对照明细'), ('notes', '解释与勘误')]
    navigation += [('evidence', '原始记录')]
    nav = '<nav class="rv-outline" aria-label="报告目录">' + ''.join(
        f'<a href="#{anchor}">{label}</a>' for anchor, label in navigation) + '</nav>'
    body = ('<section id="overview" class="rv-section rv-overview"><h2>本次结论</h2><p class="rv-verdict">'
            + e(report.get('result', '')) + '</p>')
    if is_benchmark:
        counts = results.get('native_goal_completion_counts', {})
        passed = len(results.get('passed', []))
        denominator = results.get('official_boolean_denominator', 0)
        body += ('<dl class="rv-summary-line"><div><dt>官方通过</dt><dd>' + e(f'{passed} / {denominator}')
                 + '</dd></div><div><dt>Goal 自判 complete</dt><dd>' + e(number(counts.get('complete')))
                 + '</dd></div><div><dt>Goal 自判 blocked</dt><dd>' + e(number(counts.get('blocked')))
                 + '</dd></div></dl><p class="rv-footnote">连续评分题不计入整题布尔通过率的分母。多指标交叉优势保留明细，不合成任意总分。</p>')
    body += '</section>'
    if is_benchmark:
        body += _cases(benchmark) + _usage(results) + _comparison(benchmark) + _environment(benchmark)
    else:
        body += '<section id="metrics" class="rv-section"><h2>对照明细</h2>' + table(
            report.get('columns', []), report.get('metrics', []), '历史审查对照明细') + '</section>'
    if report.get('notes'):
        body += '<section id="notes" class="rv-section"><h2>解释与勘误</h2><ul class="rv-notes">' + ''.join(
            '<li>' + e(note) + '</li>' for note in report['notes']) + '</ul></section>'
    body += _downloads(slug, report, load)
    return (STYLE + '<article class="rv-page rv-report" lang="zh-CN" data-review-report>' + header
            + '<div class="rv-report-layout">' + nav + '<div class="rv-report-content">' + body
            + '</div></div></article>' + SCRIPT)
