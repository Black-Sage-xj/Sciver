"""Browser regression for stable, scrollable Supervisor transcripts (no models)."""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tm_observer.supervisor_view import _refresh_script, SUPERVISOR_STYLE
from tm_observer.supervisor_incident_ui import dialogue_html, CHAT_MODULE, DIALOGUE_SCRIPT, COMPOSER_STYLE


@unittest.skipUnless(os.environ.get('TM_BROWSER_E2E')=='1','set TM_BROWSER_E2E=1 for browser verification')
class RealBrowserSmokeTests(unittest.TestCase):
    def test_updates_keep_document_draft_scroll_and_composer_clear_of_messages(self):
        from playwright.sync_api import sync_playwright
        state={'version':0,'requests':0,'login':False}
        vendor=Path(__file__).resolve().parents[1]/'tm_observer/vendor/loquix-incident.js'
        def content():
            rows=[{'id':str(i),'role':'supervisor','content':f'Context {i}. '+('Readable scientific context. '*12)} for i in range(20)]
            if state['version']:rows.append({'id':'update','role':'supervisor','content':'New reply'})
            articles=''.join(f'<section class="tm-supervisor-session" id="SUP-{i}"><div class="tm-incident-bar">Cycle 4 · Session {i}</div>'+dialogue_html(f'SIS-{i}',rows,active=i==2,waiting=False)+'</section>' for i in range(3))
            return ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}*{box-sizing:border-box}'+SUPERVISOR_STYLE+COMPOSER_STYLE+'</style><main class="tm-main"><div class="tm-content"><section class="tm-supervisor-page">'+articles+'</section></div></main>'+CHAT_MODULE+DIALOGUE_SCRIPT+_refresh_script(active=True,waiting=True)).encode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/vendor/loquix-incident.js':raw=vendor.read_bytes();mime='text/javascript'
                else:
                    state['requests']+=1;raw=b'<h1>Login</h1>' if state['login'] else content();mime='text/html'
                self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(raw)
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TM_BROWSER_EXECUTABLE'))
                try:
                    page=browser.new_page(viewport={'width':1280,'height':900});page.set_default_timeout(5000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.add_init_script("const originalTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>originalTimeout(fn,ms===3000||ms===8000?50:ms,...args)");page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='domcontentloaded')
                    page.wait_for_function('document.querySelector("loquix-chat-composer").shadowRoot')
                    page.wait_for_timeout(350)
                    page.evaluate('''window.originalComposer=document.querySelector('loquix-chat-composer');window.originalList=document.querySelector('loquix-message-list');
                      originalList.shadowRoot.querySelector('[part="scroll-container"]').scrollTop=80;''')
                    page.wait_for_timeout(50)
                    state['version']=1
                    page.wait_for_timeout(150)
                    page.wait_for_function('document.querySelector("[message-id=update]")')
                    self.assertTrue(page.evaluate('originalComposer===document.querySelector("loquix-chat-composer")&&originalList===document.querySelector("loquix-message-list")'))
                    page.wait_for_function('originalList.shadowRoot.querySelector("[part=scroll-container]").scrollTop===80')
                    page.evaluate('originalComposer.shadowRoot.querySelector("loquix-prompt-input").value="unsent draft"')
                    page.wait_for_timeout(100);count=state['requests'];page.wait_for_timeout(200);self.assertEqual(state['requests'],count)
                    self.assertEqual(page.evaluate('originalComposer.shadowRoot.querySelector("loquix-prompt-input").value'),'unsent draft')
                    page.evaluate('originalComposer.shadowRoot.querySelector("loquix-prompt-input").value=""')
                    state['login']=True;page.wait_for_timeout(150);page.wait_for_timeout(100)
                    self.assertEqual(page.locator('.tm-supervisor-session').count(),3)
                    for width,height in [(1280,900),(390,844),(960,560)]:
                        page.set_viewport_size({'width':width,'height':height})
                        sizes=page.locator('.tm-supervisor-session').evaluate_all('els=>els.map(e=>({height:e.getBoundingClientRect().height,list:e.querySelector("loquix-message-list").getBoundingClientRect().height}))')
                        self.assertTrue(all(s['list']>=120 for s in sizes),sizes)
                        self.assertTrue(all(s['height']>=340 for s in sizes),sizes)
                        page.locator('loquix-chat-composer').scroll_into_view_if_needed()
                        self.assertTrue(page.evaluate('''()=>{const c=originalComposer.getBoundingClientRect(),l=originalComposer.parentElement.querySelector('loquix-message-list').getBoundingClientRect();return l.bottom<=c.top+1}'''))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    self.assertEqual(errors,[])
                    self.assertGreaterEqual(state['requests'],2)
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=3)
