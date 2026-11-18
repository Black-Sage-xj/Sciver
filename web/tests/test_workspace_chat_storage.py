"""Changing accounts in one browser must not recover another workspace's chat."""
from pathlib import Path
from urllib.parse import urlsplit

from tm_observer.research_chat_ui import page_body
from tm_observer.research_chat_widget import WIDGET_STYLE, widget_html


def test_widget_sessions_and_new_chat_drafts_are_workspace_scoped():
    from playwright.sync_api import expect, sync_playwright

    script = Path(__file__).parents[1] / "tm_observer/research_chat_widget.js"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()

        def respond(route):
            path = urlsplit(route.request.url).path
            if path == "/research-chat/widget.js":
                route.fulfill(body=script.read_text(), content_type="application/javascript")
                return
            if path in {"/one-widget", "/two-widget", "/one-chat", "/two-chat"}:
                workspace = "one" if path.startswith("/one-") else "two"
                body = (WIDGET_STYLE + widget_html() if path.endswith("widget")
                        else page_body({"tasks": [], "sessions": []}))
                document = (f'<!doctype html><html><head><meta name="sciver-workspace" '
                            f'content="{workspace}"></head><body>{body}</body></html>')
                route.fulfill(body=document, content_type="text/html")
            elif path.endswith(".js"):
                route.fulfill(body="", content_type="application/javascript")
            else:
                route.fulfill(body="<body></body>", content_type="text/html")

        page.route("http://workspace.test/**", respond)
        page.goto("http://workspace.test/one-widget")
        page.evaluate("""() => {
          sessionStorage.setItem('sciver-chat-session-v1:', 'a'.repeat(32));
          sessionStorage.setItem('sciver-workspace:one:sciver-chat-session-v1:', 'b'.repeat(32));
        }""")
        page.reload()
        page.locator("#sc-chat-launcher").click()
        expect(page.locator("#sc-chat-frame")).to_have_attribute("src", f"/research-chat/?embed=1&session={'b' * 32}")
        page.goto("http://workspace.test/two-widget")
        expect(page.locator("#sc-chat-panel")).to_be_hidden()
        page.locator("#sc-chat-launcher").click()
        expect(page.locator("#sc-chat-frame")).to_have_attribute("src", "/research-chat/?embed=1&new=1")

        page.goto("http://workspace.test/one-chat")
        prompt = page.locator("[data-research-prompt]")
        prompt.fill("Workspace one private draft")
        page.evaluate("""() => {
          const key = Object.keys(localStorage).find(key => key.startsWith('sciver-workspace:one:tm-research-chat-draft-v1:'));
          localStorage.setItem(key.replace('sciver-workspace:one:', ''), 'Legacy private draft');
        }""")
        page.goto("http://workspace.test/two-chat")
        expect(prompt).to_have_value("")
        prompt.fill("Workspace two private draft")
        page.goto("http://workspace.test/one-chat")
        expect(prompt).to_have_value("Workspace one private draft")
        page.goto("http://workspace.test/two-chat")
        expect(prompt).to_have_value("Workspace two private draft")
        browser.close()
