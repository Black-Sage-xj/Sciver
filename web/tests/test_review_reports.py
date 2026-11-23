"""Read-only report regression: verdict semantics, private archives and browser use."""
import json
import os
import threading
from pathlib import Path

import pytest

from tm_observer.review_reports import attachment, page_body, read_report


@pytest.fixture
def archive(tmp_path):
    folder = tmp_path / 'reviews' / 'toy-results'
    folder.mkdir(parents=True)
    cases = [
        {'case': 'Q1', 'title': 'Toy recovery', 'official_pass': True,
         'native_goal_status': 'complete', 'metrics': {'error': 0.12}, 'evaluation': {}},
        {'case': 'Q2', 'title': 'Toy fit', 'official_pass': False,
         'native_goal_status': 'complete', 'metrics': {'error': 0.5}, 'evaluation': {}},
        {'case': 'Q3', 'title': 'Toy missing', 'official_pass': None,
         'native_goal_status': 'complete', 'metrics': {},
         'evaluation': {'status': 'continuous_no_measurements'}},
        {'case': 'Q4', 'title': 'Toy partial', 'official_pass': None,
         'native_goal_status': 'blocked', 'metrics': {'accuracy': 0.8},
         'evaluation': {'status': 'continuous_partial_scored'}},
    ]
    for case in cases:
        case.update(native_goal_tokens_used=1234, wall_seconds=3661,
                    tokens={'inputTokens': 1000, 'cachedInputTokens': 500, 'outputTokens': 734})
    report = {
        'title': 'Toy result explanation', 'scope': 'Public toy report', 'snapshot_at': '2026-01-01',
        'series': 'Q', 'result': 'Official and model verdicts are separate.',
        'details': [{'file': 'evidence.txt', 'title': 'Original evidence'}],
        'benchmark': {'results': {'cases': cases, 'passed': ['Q1'], 'official_boolean_denominator': 2,
                                  'native_goal_completion_counts': {'complete': 3, 'blocked': 1},
                                  'model': 'fixture', 'reasoning_effort': 'fixture',
                                  'native_goal_tokens_used': 4936, 'raw_token_usage': {}},
                      'preparation': {'snapshot_at': '2026-01-02', 'description': 'Not started',
                                      'settings': {'model': 'fixture', 'reasoning_effort': 'fixture', 'max_cycles': 80},
                                      'rows': [['Q1', 'checked', 'pending', 'not started']]}},
    }
    (folder / 'evidence.txt').write_bytes(b'original\n<script>alert("toy")</script>\n')
    (folder / 'report.json').write_text(json.dumps(report))
    legacy = tmp_path / 'reviews' / 'old-review'
    legacy.mkdir()
    (legacy / 'report.json').write_text(json.dumps({'title': 'Old audit', 'reviewed_at': 'old',
        'result': 'Preserved conclusion', 'columns': ['Metric', 'Value'], 'metrics': [['x', '2']],
        'notes': ['Preserved correction']}))
    return tmp_path, folder, report


def test_report_preserves_boolean_denominator_and_missing_scores(archive):
    root, _, _ = archive
    body = page_body(root, 'toy-results')
    assert '<dd>1 / 2</dd>' in body
    assert 'data-kind="pass"' in body and 'data-kind="fail"' in body
    assert body.count('data-case-entry data-kind="continuous"') == 2
    assert '无有效评分' in body and '部分连续评分' in body
    assert '缺失评分不按 0 分处理' in body
    assert '4,936' in body and '01:01:01' in body
    assert '<dd>80</dd>' in body and '不是实时运行监控' in body
    assert 'complete 表示模型自行结束' in body


def test_archive_and_legacy_reports_keep_content_and_links(archive):
    root, _, _ = archive
    body = page_body(root)
    assert '/reviews/toy-results/' in body and '/reviews/old-review/' in body
    assert 'data-review-search' in body and 'data-review-filter="benchmark"' in body
    old = page_body(root, 'old-review')
    for expected in ['Old audit', 'Preserved conclusion', 'Preserved correction', '<td>2</td>']:
        assert expected in old


def test_all_operator_text_is_escaped_but_download_is_exact(archive):
    root, folder, report = archive
    report['title'] = '<img src=x onerror=alert(1)>'
    report['benchmark']['results']['cases'][0]['title'] = '<script>bad()</script>'
    (folder / 'report.json').write_text(json.dumps(report))
    body = page_body(root, 'toy-results')
    assert '<img src=x' not in body and '<script>bad()' not in body
    assert '&lt;script&gt;bad()' in body
    assert '&lt;script&gt;alert' in body
    assert attachment(root, 'toy-results', 'evidence.txt') == (folder / 'evidence.txt').read_bytes()


def test_attachments_reject_traversal_unknown_files_and_symlinks(archive):
    root, folder, report = archive
    for name in ['../report.json', 'report.json', '/etc/passwd']:
        with pytest.raises(ValueError):
            attachment(root, 'toy-results', name)
    (folder / 'evidence.txt').unlink()
    (folder / 'evidence.txt').symlink_to(folder / 'report.json')
    with pytest.raises(ValueError):
        attachment(root, 'toy-results', 'evidence.txt')
    for slug in ['../toy-results', 'toy-results/..', '<script>']:
        with pytest.raises(ValueError):
            read_report(root, slug)


def test_archive_skips_invalid_reports_and_blocks_symlinked_json(archive):
    root, folder, _ = archive
    bad = root / 'reviews' / 'broken'
    bad.mkdir()
    (bad / 'report.json').write_text('[]')
    assert 'Toy result explanation' in page_body(root)
    (folder / 'report.json').rename(folder / 'actual.json')
    (folder / 'report.json').symlink_to(folder / 'actual.json')
    with pytest.raises(ValueError):
        read_report(root, 'toy-results')


def test_large_preview_is_bounded_without_truncating_download(archive):
    root, folder, _ = archive
    content = b'x' * 40000
    (folder / 'evidence.txt').write_bytes(content)
    body = page_body(root, 'toy-results')
    assert '预览已截断' in body
    assert content.decode() not in body
    assert attachment(root, 'toy-results', 'evidence.txt') == content


@pytest.mark.skipif(os.environ.get('TM_BROWSER_E2E') != '1', reason='browser test is opt-in')
@pytest.mark.parametrize('width,theme', [(1440, 'black'), (1440, 'white'), (390, 'blue'), (320, 'white')])
def test_review_filters_details_download_and_responsive_layout(archive, width, theme):
    from playwright.sync_api import sync_playwright, expect
    from tm_observer.web import ThreadingHTTPServer, make_handler

    root, folder, _ = archive

    class Cache:
        search_root = root

        def get(self):
            return {'runs': []}

    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(Cache(), field_notes_state_dir=root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': width, 'height': 1000}, reduced_motion='reduce')
                page.add_init_script(f'localStorage.setItem("sciver-theme-v1", {json.dumps(theme)})')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_port}'
                page.goto(base + '/reviews/')
                page.locator('[data-review-filter="benchmark"]').click()
                expect(page.locator('[data-review-entry]:visible')).to_have_count(1)
                page.locator('[data-review-search]').fill('no matching record')
                expect(page.locator('[data-review-no-results]')).to_be_visible()
                page.locator('[data-review-search]').fill('Toy')
                page.get_by_role('heading', name='Toy result explanation').get_by_role('link').click()
                expect(page.locator('[data-case-entry]:visible')).to_have_count(4)
                page.locator('[data-case-filter="pass"]').click()
                expect(page.locator('[data-case-entry]:visible')).to_have_count(1)
                page.locator('[data-case-filter="continuous"]').click()
                expect(page.locator('[data-case-entry]:visible')).to_have_count(2)
                page.locator('[data-open-case="1"]').click()
                expect(page.locator('[data-case-entry]:visible')).to_have_count(4)
                expect(page.locator('#case-1-detail')).to_be_visible()
                if width <= 600:
                    metric = page.locator('#case-1-detail .rv-metric-table')
                    assert metric.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
                    bounds = metric.bounding_box()
                    for value in metric.locator('tbody td').all():
                        box = value.bounding_box()
                        assert box['x'] >= bounds['x'] - 1
                        assert box['x'] + box['width'] <= bounds['x'] + bounds['width'] + 1
                toggle = page.locator('#case-1 .rv-case-toggle')
                expect(toggle).to_have_attribute('aria-expanded', 'true')
                toggle.focus()
                page.keyboard.press('Enter')
                expect(page.locator('#case-1-detail')).to_be_hidden()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                response = page.request.get(base + '/reviews/toy-results/files/evidence.txt')
                assert response.status == 200 and response.body() == (folder / 'evidence.txt').read_bytes()
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
