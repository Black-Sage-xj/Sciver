"""Discovery-quality regressions use local provider receipts, never a model or network."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.scholarly_selection import _metadata_issue, _query_variants
from theory_master.scholarly_transport import retrieve_scholarly


def receipt_runner(batches, calls):
    def run(command, **kwargs):
        calls.append(command)
        assert Path(command[1]).name == 'lit_retrieve.py'
        output = Path(command[command.index('--output-dir') + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'papers.json').write_text(json.dumps(batches[len(calls)-1]))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    return run


def article(title, doi, **extra):
    return dict({'title': title, 'doi': doi, 'year': 2024, 'authors': ['A. Researcher'],
                 'source': 'openalex', 'sources': ['openalex'], 'abstract': '', 'score': 0},
                **extra)


def test_complementary_query_plan_is_normalized_and_never_repeated(tmp_path):
    calls = []
    result = retrieve_scholarly({
        'task_type': 'landscape', 'search_target': 'Explain a broad scientific topic',
        'search_queries': [{'query': '  soft   matter fracture ', 'purpose': 'overview'},
                           {'query': 'SOFT MATTER FRACTURE!'}, {'query': ''},
                           {'query': 'soft matter network elasticity'}]},
        tmp_path, runner=receipt_runner([[], []], calls))
    assert result['query_variants'] == ['soft matter fracture', 'soft matter network elasticity']
    assert len(calls) == 2
    assert result['status'] == 'empty'


def test_generic_fallback_removes_task_scaffolding_and_preserves_native_language():
    queries = _query_variants('landscape', 'gap',
        'How does colloidal assembly affect elastic response? Identify recent findings and open questions.',
        '', 'colloidal assembly, elasticity')
    assert 'colloidal assembly' in queries[0]
    assert all('Identify' not in value and 'questions' not in value for value in queries)
    assert _query_variants('landscape', 'gap', '高分子的拓扑和力学', '') == ['高分子的拓扑和力学']


@pytest.mark.parametrize('row,issue', [
    ({'title': 'Fetched page contents ' * 4000}, 'title_exceeds_article_bound'),
    ({'title': 'Just a moment...'}, 'webpage_title_instead_of_article'),
    ({'title': 'Article', 'abstract': 'body ' * 10000}, 'abstract_exceeds_metadata_bound'),
    ({'title': ['Not a normalized title']}, 'missing_title'),
    ({'title': 'Article', 'year': 'not a year'}, 'invalid_year'),
])
def test_provider_webpage_pollution_has_explicit_rejection(row, issue):
    assert _metadata_issue(row) == issue


def test_landscape_ranking_ignores_corrupt_provider_scores_and_records_raw_receipts(tmp_path):
    calls = []
    bad = article('Entire webpage ' * 2000, '10.1234/page', score=1000000)
    rows = [article('Centrality of social networks and topology', '10.1234/social', score=1000),
            bad,
            article('Elasticity and fracture of polymer networks', '10.1234/relevant', score=0),
            article('Unrelated spectroscopy', '10.1234/other', score={'bad': 'score'})]
    result = retrieve_scholarly({'task_type': 'landscape', 'source_gap_id': 'quality',
        'domain_context': 'polymer mechanics, topology and fracture',
        'search_queries': ['polymer network topology fracture', 'polymer network elasticity'],
        'max_candidates': 2}, tmp_path, runner=receipt_runner([rows, []], calls))
    retained = json.loads(Path(result['papers_json']).read_text())
    assert retained[0]['doi'] == '10.1234/relevant'
    assert len(retained) == 2 and result['rejected_metadata_count'] == 1
    assert result['candidate_count_before_limit'] == 3
    quality = json.loads(Path(result['metadata_quality_json']).read_text())
    assert quality['rejections'][0]['reason'] == 'title_exceeds_article_bound'
    raw = json.loads((Path(result['retrieval_dir']) / 'query-01/papers.json').read_text())
    assert raw[1]['title'] == bad['title']
    assert result['metadata_hit_count'] == 2


def test_duplicate_records_retain_complementary_query_and_open_access_provenance(tmp_path):
    calls = []
    first = article('Elasticity of polymer networks', 'https://doi.org/10.1234/ABC',
                    open_access_url='https://repository.edu/source.pdf',
                    open_access_urls=['https://repository.edu/source.pdf'], is_open_access=True)
    second = dict(first, doi='10.1234/abc', source='semanticscholar', sources=['semanticscholar'],
                  open_access_url='https://archive.edu/source.html',
                  open_access_urls=['https://archive.edu/source.html'])
    result = retrieve_scholarly({'task_type': 'landscape',
        'search_queries': ['polymer elasticity', 'polymer topology']},
        tmp_path, runner=receipt_runner([[first], [second]], calls))
    retained = json.loads(Path(result['papers_json']).read_text())
    assert len(retained) == 1
    assert retained[0]['sources'] == ['openalex', 'semanticscholar']
    assert retained[0]['open_access_urls'] == [first['open_access_url'], second['open_access_url']]
    assert retained[0]['discovery_queries'] == ['polymer elasticity', 'polymer topology']
    assert result['metadata_hit_count'] == 1


def test_exact_source_lock_overrides_broad_query_plan(tmp_path):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index('--output-dir')+1])
        output.mkdir(parents=True, exist_ok=True)
        if Path(command[1]).name == 'lit_retrieve.py':
            assert command[command.index('--query')+1] == '10.1234/locked'
            (output/'papers.json').write_text(json.dumps([article('Locked article', '10.1234/locked')]))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    result = retrieve_scholarly({'task_type': 'targeted_source',
        'source_lock': {'doi': '10.1234/locked'},
        'search_queries': ['broad unrelated query', 'another query']}, tmp_path, runner=runner)
    assert result['query_variants'] == ['10.1234/locked']
    assert sum(Path(command[1]).name == 'lit_retrieve.py' for command in calls) == 1


def test_host_sanitizes_preconsolidation_records_before_identity_merge(tmp_path):
    def runner(command, **kwargs):
        output = Path(command[command.index('--output-dir')+1])
        output.mkdir(parents=True, exist_ok=True)
        good = article('Topology and elasticity of polymer networks', '10.1234/source')
        polluted = dict(good, title='Webpage contents ' * 2000)
        (output/'papers.json').write_text(json.dumps([polluted]))
        (output/'fetched.json').write_text(json.dumps([good, polluted]))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    result = retrieve_scholarly({'task_type': 'landscape',
        'search_queries': ['polymer topology elasticity']}, tmp_path, runner=runner)
    rows = json.loads(Path(result['papers_json']).read_text())
    assert len(rows) == 1 and rows[0]['title'] == 'Topology and elasticity of polymer networks'
    assert result['raw_metadata_count'] == 2 and result['rejected_metadata_count'] == 1


def test_doi_lock_cannot_gain_provider_identity_from_title_only_metadata(tmp_path):
    def runner(command, **kwargs):
        output = Path(command[command.index('--output-dir')+1])
        output.mkdir(parents=True, exist_ok=True)
        if Path(command[1]).name == 'lit_retrieve.py':
            (output/'papers.json').write_text(json.dumps([article('Ambiguous article', '')]))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    result = retrieve_scholarly({'task_type': 'targeted_source',
        'source_lock': {'doi': '10.1234/locked', 'title': 'Ambiguous article', 'year': 2024}},
        tmp_path, runner=runner)
    rows = json.loads(Path(result['papers_json']).read_text())
    assert len(rows) == 2
    locked = next(row for row in rows if row.get('source_locked'))
    assert locked['identity_resolution'] == 'unresolved'
    assert result['source_lock_only_count'] == 1
