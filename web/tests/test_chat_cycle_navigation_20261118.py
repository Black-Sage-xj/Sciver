"""Cycle citations reuse their host page and can refocus the same graph twice."""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

import pytest

from tm_observer.research_chat_widget import WIDGET_STYLE, widget_html
from test_research_chat_links import task as citation_task


WEB = Path(__file__).resolve().parents[1]
TASK = "a" * 16
OTHER_TASK = "b" * 16
ORIGIN = "http://cycle-nav.test"


def _widget_fixture(page, start_path: str, target_task: str = TASK, local_reference: bool = False):
    destination = f"/research-explorer/?observer_id={target_task}#report%3A2"
    citation = (
        "/research-chat/open?" + urlencode({"task_id": target_task, "target": "notes/outcomes/2.md"})
        if local_reference else destination
    )
    requests = []
    parent = (
        "<!doctype html><html><head>" + WIDGET_STYLE + "</head><body>"
        + widget_html(TASK)
        + """<script>
window.cycleNavigationEvents=[];
window.addEventListener('sciver-explorer-navigate',event=>{
  window.cycleNavigationEvents.push(event.detail.href);
  sessionStorage.setItem('cycle-navigation-event-count',String(window.cycleNavigationEvents.length));
  event.preventDefault();
});
</script></body></html>"""
    )
    embedded = (
        '<!doctype html><html><body class="tm-chat-embedded">'
        f'<section class="tm-research-chat" data-session="" data-task-id="{TASK}">'
        f'<a id="cycle-link" href="{citation}" target="_blank">Cycle 2 记录</a>'
        '</section><script src="/research-chat/widget.js" defer></script></body></html>'
    )

    def respond(route):
        url = route.request.url
        if url.startswith(ORIGIN + "/research-chat/widget.js"):
            route.fulfill(
                body=(WEB / "tm_observer/research_chat_widget.js").read_text(),
                content_type="application/javascript",
            )
        elif url.startswith(ORIGIN + "/research-chat/?"):
            requests.append(("chat", url))
            route.fulfill(body=embedded, content_type="text/html")
        elif url.startswith(ORIGIN + "/research-chat/open?"):
            query = parse_qs(urlsplit(url).query)
            assert query.get("resolve") == ["1"], "The parent must resolve citations without navigating the iframe"
            kind = "resolve-parent" if route.request.frame.parent_frame is None else "resolve-iframe"
            requests.append((kind, url))
            route.fulfill(body=json.dumps({"destination": destination}), content_type="application/json")
        elif route.request.resource_type == "document":
            requests.append(("host", url))
            route.fulfill(body=parent, content_type="text/html")
        else:
            route.fulfill(body="", content_type="image/svg+xml")

    page.route(ORIGIN + "/**", respond)
    page.goto(ORIGIN + start_path)
    page.locator("#sc-chat-launcher").click()
    link = page.frame_locator("#sc-chat-frame").locator("#cycle-link")
    link.wait_for(state="visible")
    return link, destination, requests


@pytest.mark.parametrize("local_reference", [False, True], ids=["graph-url", "local-document"])
def test_cycle_link_reuses_graph_and_repeated_clicks_keep_the_chat_loaded(local_reference):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        start = f"/research-explorer/?observer_id={TASK}#report%3A2"
        link, destination, requests = _widget_fixture(page, start, local_reference=local_reference)
        original_requests = list(requests)
        for count in (1, 2):
            link.click()
            page.wait_for_function("count=>window.cycleNavigationEvents.length===count", arg=count)
            assert page.evaluate("window.cycleNavigationEvents.at(-1)") == ORIGIN + destination
            assert len(context.pages) == 1, "Cycle citations must not open browser tabs"
            assert [request for request in requests if request[0] in {"host", "chat"}] == original_requests, (
                "The graph and chat iframe must remain loaded"
            )
            assert page.url == ORIGIN + start
        if local_reference:
            assert any(kind == "resolve-parent" for kind, _ in requests)
            assert not any(kind == "resolve-iframe" for kind, _ in requests)
        assert page.frame_locator("#sc-chat-frame").locator("#cycle-link").is_visible()
        browser.close()


@pytest.mark.parametrize(
    ("start_path", "target_task"),
    [
        (f"/run/{TASK}/", TASK),
        (f"/research-explorer/?observer_id={TASK}", OTHER_TASK),
    ],
    ids=["another-page", "another-task"],
)
@pytest.mark.parametrize("local_reference", [False, True], ids=["graph-url", "local-document"])
def test_cycle_link_navigates_the_host_tab_when_the_target_graph_is_elsewhere(start_path, target_task, local_reference):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        link, destination, requests = _widget_fixture(page, start_path, target_task, local_reference)
        link.click()
        page.wait_for_url(ORIGIN + destination)
        assert len(context.pages) == 1, "The current tab must navigate to the requested graph"
        assert len([kind for kind, _ in requests if kind == "host"]) == 2
        assert page.evaluate("sessionStorage.getItem('cycle-navigation-event-count')") is None, (
            "A graph for another task must not consume the navigation event"
        )
        browser.close()


def test_local_citation_json_resolution_keeps_redirect_and_document_permissions(citation_task, monkeypatch):
    from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler

    search, root, ident, state = citation_task
    cache = StateCache(search)
    monkeypatch.setattr(cache, "get", lambda: state)
    monkeypatch.setattr(
        "tm_observer.research_chat.begin_send", lambda *args, **kwargs: pytest.fail("No model calls")
    )
    database_before = (root / "science_graph.sqlite3").read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, field_notes_state_dir=search / "observer"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    expected = f"/research-explorer/?observer_id={ident}#report%3A1"

    def citation_url(target, *, resolve):
        query = {"task_id": ident, "target": target}
        if resolve:
            query["resolve"] = "1"
        return base + "/research-chat/open?" + urlencode(query)

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        with urlopen(citation_url("notes/outcomes/1.md", resolve=True), timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "application/json"
            assert json.load(response) == {"destination": expected}
        with pytest.raises(HTTPError) as redirect:
            build_opener(NoRedirect()).open(citation_url("notes/outcomes/1.md", resolve=False), timeout=5)
        assert redirect.value.code == 303
        assert redirect.value.headers["Location"] == expected
        outside = search / "outside.md"
        outside.write_text("PRIVATE_OUTSIDE_SENTINEL")
        (root / "notes/escape.md").symlink_to(outside)
        for target, status in [(str(outside), 403), ("notes/escape.md", 403), ("notes/missing.md", 404)]:
            with pytest.raises(HTTPError) as denied:
                urlopen(citation_url(target, resolve=True), timeout=5)
            assert denied.value.code == status
            assert "PRIVATE_OUTSIDE_SENTINEL" not in denied.value.read().decode()
        assert (root / "science_graph.sqlite3").read_bytes() == database_before
        assert not list((search / "observer/research-chat").glob("*.json"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def _graph_harness_prefix() -> str:
    tree = ast.parse((WEB / "tests/test_research_explorer_g6.py").read_text())
    for item in tree.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "HARNESS" for target in item.targets
        ):
            harness = ast.literal_eval(item.value)
            break
    else:
        raise AssertionError("Existing graph harness was not found")
    prefix = harness.split("(async()=>{", 1)[0]
    setup = r"""
const cycleListeners=new Map();
const addListener=ctx.window.addEventListener.bind(ctx.window);
ctx.window.addEventListener=(name,callback)=>{
  cycleListeners.set(name,callback);addListener(name,callback);
};
location.origin='http://localhost';
location.href=location.origin+location.pathname+location.search;
nodes[0].semantic.cycle_outcomes=[{cycle_id:'2',verdict:'rejected',summary:'Cycle two result'}];
const originalFetch=ctx.fetch;
ctx.fetch=async url=>String(url).includes('/report?')?
  {ok:true,json:async()=>({id:'notes/outcomes/2.md',cycle_id:'2',title:'Cycle two report',
    content:'# Cycle two report\n\nSynthetic observation.',sections:[]})}:originalFetch(url);
const originalFocus=FakeGraph.prototype.focusElement;
FakeGraph.prototype.focusElement=async function(id,animation){
  this.cycleFocusCalls=(this.cycleFocusCalls||0)+1;
  return originalFocus.call(this,id,animation);
};
const pulseAnimations=[];
const pulseRing=elem('ex-node-pulse');
pulseRing.animate=(frames,options)=>{
  const animation={frames,options,cancelled:false,cancel(){this.cancelled=true}};
  pulseAnimations.push(animation);return animation;
};
ctx.window.matchMedia=()=>({matches:false});
"""
    marker = "vm.runInNewContext(fs.readFileSync("
    assert marker in prefix
    return prefix.replace(marker, setup + "\n" + marker, 1)


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
@pytest.mark.parametrize("cold_start", [False, True], ids=["existing-graph", "report-deep-link"])
def test_cycle_navigation_resolves_report_to_its_node_and_refocuses_on_repeat(cold_start):
    script = _graph_harness_prefix() + f"\nconst coldStart={str(cold_start).lower()};\n" + r"""
(async()=>{
  if(coldStart)location.hash='#report%3A2';
  onReady();await tick();await tick();await tick();
  const listener=cycleListeners.get('sciver-explorer-navigate');
  assert.equal(typeof listener,'function','the graph must accept navigation from its embedded chat');
  const href='http://localhost/research-explorer/?observer_id=0000000000000001#report%3A2';
  const initialFocusCalls=latestGraph.cycleFocusCalls||0;
  assert.equal(pulseAnimations.length,coldStart?1:0,
    'a report deep link must pulse its corresponding node on the first page load');
  if(coldStart)assert.equal(latestGraph.focused,'A','cold report navigation resolves node A');
  const initialAnimations=pulseAnimations.length;
  for(let count=1;count<=2;count++){
    const previousAnimation=pulseAnimations.at(-1);
    let accepted=false;
    listener({detail:{href},preventDefault(){accepted=true}});
    assert.equal(accepted,true,'the existing graph consumes its own navigation');
    await tick();await tick();await tick();await tick();
    assert.equal(latestGraph.focused,'A','Cycle two outcome belongs to node A');
    assert.equal(latestGraph.cycleFocusCalls,initialFocusCalls+count,
      'repeating the same citation must refocus without relying on a hash change');
    assert.equal(history.state.tmExplorerView.selected.kind,'report');
    assert.equal(history.state.tmExplorerView.selected.id,'2');
    assert(elements['ex-reader'].innerHTML.includes('Cycle two report'),'the report remains readable');
    assert.equal(pulseAnimations.length,initialAnimations+count,
      'each click starts a fresh visible pulse, including repeated identical targets');
    if(previousAnimation)assert.equal(previousAnimation.cancelled,true,
      'a repeated click cancels the preceding pulse before starting another');
    const animation=pulseAnimations.at(-1);
    assert.notEqual(animation,previousAnimation);
    assert.equal(pulseRing.hidden,false,'the pulse ring is visible while its animation runs');
    assert(animation.frames.some(frame=>frame.opacity>0),'the pulse must become visible');
    assert(animation.options.duration>0,'the pulse has a bounded positive duration');
    for(const field of ['left','top','width','height']){
      assert(Number.isFinite(parseFloat(pulseRing.style[field])),field+' must be finite');
    }
    assert(parseFloat(pulseRing.style.width)>0&&parseFloat(pulseRing.style.height)>0,
      'the pulse must surround a visible rectangle');
  }
  pulseAnimations.at(-1).onfinish();
  assert.equal(pulseRing.hidden,true,'the ring disappears when the pulse completes');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    process = subprocess.run(
        ["node", "-e", script, str(WEB / "tm_observer/research_explorer_g6.js")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0, process.stderr
