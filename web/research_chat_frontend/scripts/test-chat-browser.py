"""Exercise the real React UI against in-memory HTTP fixtures; never contacts a model."""
from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
SID = 'a' * 32
TASK = 'b' * 16
BASE = f'/api/research-chat/{SID}'
STAMP = '2026-11-18T08:00:00Z'


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryFile(mode='w+') as log:
        server = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=ROOT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(origin + '/research-chat/assets/', timeout=1)
                    break
                except Exception:
                    if server.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(0.1)
            else:
                raise RuntimeError('Vite did not start')
            run = {'id': 'run-old', 'status': 'complete', 'stage': 'complete', 'started_at': STAMP, 'completed_at': '2026-11-18T08:00:12Z', 'duration_ms': 12000, 'model': 'mock-model', 'reasoning_effort': 'high', 'events': [{'id': 'e1', 'type': 'tool', 'label': '检索资料', 'status': 'complete', 'text': '公开结果'}], 'usage': {'input_tokens': 123, 'output_tokens': 45}, 'context': {'task_id': TASK}}
            state = {'id': SID, 'model': 'mock-model', 'reasoning_effort': 'high', 'messages': [{'id': 'old-u', 'role': 'user', 'content': '**保留原文**', 'created_at': STAMP}, {'id': 'old-a', 'role': 'assistant', 'content': '# 研究结论\n\n有据可查的结果。', 'created_at': STAMP, 'run': run}], 'active_run': run, 'streaming': False, 'branches': [{'id': 'branch-original', 'label': '原始版本', 'message_count': 2}, {'id': 'branch-new', 'label': '修改版本', 'message_count': 2}], 'current_branch_id': 'branch-original', 'queue': [], 'queue_paused': False, 'pending_questions': [], 'runs': [run]}
            attachments = {}
            posts = []
            failed_once = set()
            streaming = False
            question_requested = False

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.add_init_script('''window.mockNotifications=[]; class MockNotification { static permission='default'; static async requestPermission(){this.permission='granted';return 'granted'} constructor(title,options){window.mockNotifications.push({title,options})} close(){} } window.Notification=MockNotification;''')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def route_api(route):
                    nonlocal streaming, question_requested
                    request = route.request
                    parsed = urllib.parse.urlsplit(request.url)
                    path = parsed.path
                    if request.method == 'GET':
                        if path.endswith('/models') or path.endswith('/settings'):
                            value = {'model': 'mock-model', 'reasoning_effort': 'high', 'available_models': ['mock-model'], 'available_reasoning_efforts': ['high'], 'model_reasoning_efforts': {'mock-model': ['high']}, 'model_catalog': {'status': 'ready', 'stale': False}}
                        elif path.endswith('/state'):
                            value = state
                        elif path.endswith('/progress'):
                            stream_text = state.get('stream_text', '')
                            offset = int(urllib.parse.parse_qs(parsed.query).get('offset', ['0'])[0])
                            value = {'status': 'running' if streaming else 'complete', 'streaming': streaming, 'text': stream_text[offset:], 'next_offset': len(stream_text), 'active_run': state['active_run'], 'queue': state['queue'], 'queue_paused': state['queue_paused'], 'pending_questions': state['pending_questions']}
                        elif path.endswith('/attachments'):
                            value = {'attachments': list(attachments.values())}
                        elif '/attachments/' in path:
                            aid = path.split('/attachments/')[1].split('/')[0]
                            value = {'attachment': attachments[aid]}
                        else:
                            route.fulfill(status=404, json={'error': 'Unexpected fixture GET: ' + path})
                            return
                        route.fulfill(json=value)
                        return
                    body = request.post_data_json if request.headers.get('content-type', '').startswith('application/json') else None
                    posts.append((path, body))
                    if path.endswith('/attachments'):
                        name = urllib.parse.unquote(request.headers.get('x-file-name', ''))
                        if name == 'retry.txt' and name not in failed_once:
                            failed_once.add(name)
                            route.fulfill(status=503, json={'error': '模拟上传失败'})
                            return
                        aid = f'{len(attachments) + 1:032x}'
                        attachment = {'id': aid, 'name': name, 'media_type': request.headers.get('content-type'), 'size': len(request.post_data_buffer or b''), 'kind': 'document', 'status': 'ready', 'url': BASE + '/attachments/' + aid + '/content', 'text_preview': '文件预览内容'}
                        attachments[aid] = attachment
                        route.fulfill(json={'attachment': attachment})
                        return
                    if path.endswith('/message') or path.endswith('/regenerate'):
                        if path.endswith('/message'):
                            ids = body.get('attachment_ids', [])
                            state['messages'].append({'id': 'new-u-' + str(len(posts)), 'role': 'user', 'content': body.get('content') or '请查看所附文件。', 'created_at': STAMP, 'attachment_ids': ids, 'attachments': [attachments[aid] for aid in ids]})
                        streaming = True
                        state['streaming'] = True
                        state['active_run'] = {**run, 'id': 'run-new', 'status': 'running', 'stage': 'thinking', 'started_at': STAMP, 'completed_at': None, 'duration_ms': None}
                    elif path.endswith('/queue'):
                        action = body['action']
                        if action == 'add':
                            state['queue'].append({'id': 'q-' + str(len(posts)), 'content': body.get('content', ''), 'attachment_ids': body.get('attachment_ids', [])})
                        elif action == 'edit':
                            next(item for item in state['queue'] if item['id'] == body['queue_id'])['content'] = body['content']
                        elif action == 'remove':
                            state['queue'] = [item for item in state['queue'] if item['id'] != body['queue_id']]
                        else:
                            state['queue_paused'] = False
                    elif path.endswith('/steer'):
                        state['messages'].append({'id': 'steer-u', 'role': 'user', 'content': body['content'], 'created_at': STAMP})
                    elif path.endswith('/branch'):
                        state['current_branch_id'] = body['branch_id']
                    elif path.endswith('/answer'):
                        assert body['answers'] == {'q1': ['继续分析']}
                        state['pending_questions'] = []
                        question_requested = True
                    elif path.endswith('/stop'):
                        streaming = False
                        state['streaming'] = False
                        state['active_run'] = {**run, 'id': 'run-new', 'status': 'stopped', 'stage': 'stopped'}
                    route.fulfill(json={'ok': True})

                page.route('**/api/**', route_api)

                def route_document(route):
                    response = route.fetch()
                    html = response.text().replace('<div id="tm-research-chat-app"></div>', f'<section class="tm-research-chat" data-mode="research" data-task-id="{TASK}" data-session="{SID}"><div id="tm-research-chat-app" data-session-id="{SID}"></div></section>')
                    route.fulfill(response=response, body=html)

                page.route(origin + '/research-chat/assets/', route_document)
                def search_chat(value):
                    field = page.get_by_label('搜索当前对话', exact=True)
                    if not field.is_visible():
                        page.get_by_label('查找对话内容', exact=True).click()
                    field.fill(value)

                page.goto(origin + '/research-chat/assets/')
                expect(page.locator('.sc-user-plain')).to_have_text('**保留原文**')
                expect(page.get_by_label('编辑并重发', exact=True)).to_have_count(1)
                expect(page.locator('time')).to_have_count(2)
                expect(page.locator('.sc-run-metadata')).to_contain_text('12 秒')
                page.get_by_label('对话版本').select_option('branch-new')
                expect(page.get_by_label('对话版本')).to_have_value('branch-new')
                page.get_by_title('引用回答', exact=True).click(force=True)
                composer = page.get_by_label('发送给 Sciver 的消息', exact=True)
                expect(composer).to_contain_text('')
                assert '> # 研究结论' in composer.input_value()
                composer.fill('待发送草稿')
                page.reload()
                expect(composer).to_have_value('待发送草稿')
                file_input = page.locator('input[type=file]').first
                file_input.set_input_files({'name': 'retry.txt', 'mimeType': 'text/plain', 'buffer': b'hello'})
                expect(page.locator('.sc-attachment-error')).to_contain_text('模拟上传失败')
                page.get_by_role('button', name='重试', exact=True).click()
                expect(page.get_by_label('待发送附件', exact=True)).to_contain_text('已就绪')
                page.reload()
                expect(page.get_by_label('待发送附件', exact=True)).to_contain_text('retry.txt')
                page.get_by_role('button', name='retry.txt', exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('文件预览内容')
                page.get_by_label('关闭附件预览').click()
                page.get_by_label('本聊天文件', exact=True).click()
                expect(page.get_by_label('本聊天文件列表')).to_contain_text('retry.txt')
                page.get_by_label('本聊天文件列表').get_by_role('button', name='收起', exact=True).click()
                with page.expect_download() as download:
                    page.get_by_label('导出此回答', exact=True).click(force=True)
                assert download.value.suggested_filename.endswith('.md')
                page.get_by_label('更多输入选项', exact=True).click()
                page.get_by_label('启用回复提醒', exact=True).click()
                page.get_by_label('发送快捷键').select_option('ctrlEnter')
                page.get_by_label('关闭输入选项', exact=True).click()
                composer.press('Enter')
                assert composer.input_value().endswith('\n'), 'plain Enter must insert newline in Ctrl+Enter mode'
                before_ime = len(posts)
                composer.dispatch_event('keydown', {'key': 'Enter', 'code': 'Enter', 'isComposing': True})
                assert len(posts) == before_ime, 'IME confirmation must never send a message'
                page.get_by_label('发送消息', exact=True).click()
                expect(page.get_by_label('生成期间发送方式')).to_be_visible()
                expect(composer).to_have_value('')
                assert any(path.endswith('/message') and body.get('attachment_ids') for path, body in posts if body)
                composer.fill('排队问题')
                page.get_by_label('加入待发送队列', exact=True).click()
                expect(page.locator('.sc-queue-panel')).to_contain_text('排队问题')
                page.locator('.sc-queue-panel').get_by_role('button', name='编辑', exact=True).click()
                page.get_by_label('编辑待发送消息').fill('修改后的排队问题')
                page.locator('.sc-queue-panel').get_by_role('button', name='保存', exact=True).click()
                expect(page.locator('.sc-queue-panel')).to_contain_text('修改后的排队问题')
                page.locator('.sc-queue-panel').get_by_role('button', name='撤回', exact=True).click()
                expect(page.locator('.sc-queue-panel')).to_have_count(0)
                page.get_by_label('生成期间发送方式').select_option('steer')
                composer.fill('立即补充方向')
                page.get_by_label('即时补充当前任务', exact=True).click()
                expect(composer).to_have_value('')
                assert any(path.endswith('/steer') and body['content'] == '立即补充方向' for path, body in posts if body)
                state['pending_questions'] = [{'request_id': 'request-1', 'questions': [{'id': 'q1', 'header': '下一步', 'question': '如何继续？', 'options': [{'label': '继续分析', 'description': '读取更多证据'}, {'label': '总结'}]}]}]
                expect(page.locator('.sc-question-panel')).to_be_visible()
                page.get_by_label('继续分析', exact=False).check()
                page.get_by_role('button', name='提交回答', exact=True).click()
                expect(page.locator('.sc-question-panel')).to_have_count(0)
                assert question_requested
                # A hidden tab still polls and reports completion after explicit opt-in.
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})")
                streaming = False
                state['streaming'] = False
                state['active_run'] = {**run, 'id': 'run-new'}
                expect(page.get_by_label('生成期间发送方式')).to_have_count(0)
                page.wait_for_function('window.mockNotifications.length === 1')
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false})")
                # Pasting and dropping actual browser Files runs the same upload path.
                page.evaluate("""() => { const dt=new DataTransfer();dt.items.add(new File(['pasted'],'pasted.txt',{type:'text/plain'}));document.querySelector('.tm-aui-input').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})); }""")
                expect(page.get_by_label('待发送附件', exact=True)).to_contain_text('pasted.txt')
                page.evaluate("""() => { const dt=new DataTransfer();dt.items.add(new File(['dropped'],'dropped.csv',{type:'text/csv'}));document.querySelector('.tm-aui-shell').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true})); }""")
                expect(page.get_by_label('待发送附件', exact=True)).to_contain_text('dropped.csv')
                page.get_by_label('待发送附件', exact=True).get_by_role('button',name='移除pasted.txt',exact=True).click()
                page.get_by_label('待发送附件', exact=True).get_by_role('button',name='移除dropped.csv',exact=True).click()
                composer.fill('第二个任务')
                composer.press('Control+Enter')
                expect(page.get_by_label('生成期间发送方式')).to_be_visible()
                page.get_by_label('停止生成', exact=True).click()
                expect(page.get_by_role('button', name='重试上一条消息')).to_be_visible()
                search_chat('有据可查')
                expect(page.locator('.sc-search-current')).to_contain_text('有据可查')
                # Long histories render a bounded window; both local and global search reveal early hits.
                state['messages'] = [{'id': f'history-{i}', 'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'第{i}条 唯一记录{i}', 'created_at': STAMP} for i in range(1000)]
                page.reload()
                expect(page.locator('[data-chat-message-id]')).to_have_count(100)
                search_chat('唯一记录12')
                expect(page.locator('.sc-search-current')).to_have_attribute('data-chat-message-id', 'history-12')
                assert page.locator('[data-chat-message-id]').count() <= 100
                search_chat('')
                page.evaluate("location.hash='message-history-2'")
                expect(page.locator('.sc-anchor-current')).to_have_attribute('data-chat-message-id', 'history-2')
                assert page.locator('[data-chat-message-id]').count() <= 100
                # Sending from an old window must reveal the new user and streamed answer.
                state['stream_text'] = '早期定位后，新的流式回答已显示。'
                composer.fill('从历史锚点继续讨论')
                page.get_by_label('发送消息', exact=True).click()
                expect(page.locator('.sc-user-plain').last).to_have_text('从历史锚点继续讨论')
                expect(page.locator('.tm-aui-assistant-content').last).to_contain_text('新的流式回答已显示')
                expect(page.get_by_label('搜索当前对话')).to_have_value('')
                assert not page.evaluate('location.hash')
                assert page.locator('[data-chat-message-id]').count() <= 100
                # Queueing or steering while browsing a searched old window also returns to live work.
                search_chat('唯一记录12')
                expect(page.locator('.sc-search-current')).to_have_attribute('data-chat-message-id', 'history-12')
                page.get_by_label('生成期间发送方式').select_option('queue')
                composer.fill('从历史窗口排队')
                page.get_by_label('加入待发送队列', exact=True).click()
                expect(page.get_by_label('搜索当前对话')).to_have_value('')
                expect(page.locator('.tm-aui-assistant-content').last).to_contain_text('新的流式回答已显示')
                search_chat('唯一记录12')
                expect(page.locator('.sc-search-current')).to_have_attribute('data-chat-message-id', 'history-12')
                page.get_by_label('生成期间发送方式').select_option('steer')
                composer.fill('从历史窗口即时补充')
                page.get_by_label('即时补充当前任务', exact=True).click()
                expect(page.get_by_label('搜索当前对话')).to_have_value('')
                expect(page.locator('.sc-user-plain').last).to_have_text('从历史窗口即时补充')
                expect(page.locator('.tm-aui-assistant-content').last).to_contain_text('新的流式回答已显示')
                # No model route exists in this fixture. Every mutating action above is inspected.
                assert not errors, errors
                browser.close()
                print('PASS: React UI raw text, persisted timing/drafts/attachments, upload retry/preview/send, branch selection, quote/search, queue edit/withdraw, steer, SDK answers, IME/shortcuts, clipboard/drop, hidden-tab notifications and bounded history/search anchors and return-to-live sends/queue/steer; zero model calls')
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
