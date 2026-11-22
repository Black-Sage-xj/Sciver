from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import json

from tm_observer.collector import TaskLocation, _task_display_name
from tm_observer.http_task_routes import post_task


def test_rename_persists_display_name_without_changing_config(tmp_path):
    directory = tmp_path / '.theory-master'
    directory.mkdir()
    config = directory / 'config.json'
    config.write_text('{"task_name": "Original"}')
    run = {'observer_id': 'a' * 16, 'task_root': str(tmp_path)}
    class Cache:
        def get(self):
            return {'runs': [run]}
        def include_created_task(self, root):
            assert root == tmp_path
    body = json.dumps({'name': '新名称'}).encode()
    class Handler:
        headers = {'Origin': 'http://localhost', 'Host': 'localhost', 'X-TM-Operator-Intent': '1',
                   'Content-Type': 'application/json', 'Content-Length': str(len(body))}
        rfile = BytesIO(body)
        def _authorized(self): return True
        def _send(self, data, content_type, status): self.result = (json.loads(data), status)
    handler = Handler()
    assert post_task(handler, '/api/run/' + 'a' * 16 + '/rename', SimpleNamespace(cache=Cache()), None)
    assert handler.result[0] == {'name': '新名称'}
    location = TaskLocation(tmp_path, tmp_path, directory / 'task.json', config, {'task_name': 'Original'})
    assert _task_display_name(location) == '新名称'
    assert json.loads(config.read_text()) == {'task_name': 'Original'}
