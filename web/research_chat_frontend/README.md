# Sciver Research Chat frontend

This is a small Vite/React frontend built on the open-source `@assistant-ui/react` primitives (MIT). It is intentionally a frontend adapter, not a replacement backend: session persistence, task selection, approval previews, and the Codex SDK call remain in `web/tm_observer`.

## Build and verify

```sh
npm ci
npm run typecheck
npm run test:markdown
npm run build
```

The build output is written to `web/tm_observer/research_chat_dist/` and is committed so the Python Observatory can serve it without installing Node.js at runtime. The browser talks to the same-origin `/api/research-chat/<session>/...` endpoints; do not put provider credentials in this frontend.

## Chat capabilities

The existing assistant-ui ExternalStore runtime remains the rendering adapter. Chat now carries durable turn timing, structured progress, attachments, queued follow-ups, retained answer versions, quoted questions and optional completion notifications. Voice input is intentionally outside this change. Session organization, attachment storage and Codex execution stay in the Python service.

Attachment/queue/file-viewing interactions were informed by the MIT-licensed [T3 Code](https://github.com/pingdotgg/t3code/tree/055b44919eeb5d6a59b934fea1cdd34de10c91fe) implementation; this is an adaptation to Sciver's existing session and research boundaries, not a vendored T3 runtime. The existing assistant-ui components use the [MIT license](https://github.com/assistant-ui/assistant-ui/blob/main/LICENSE). Code syntax highlighting uses highlight.js under BSD-3-Clause; its complete notice is packaged at `web/tm_observer/vendor/highlight-LICENSE.txt`.

Run `npm run test:features` for deterministic feature checks, `npm run test:browser` for the mocked browser scenarios, and `npm run test:http-browser` for the real Python HTTP/React integration. The browser checks require Python Playwright with Chromium; they use temporary data and fake model backends. Frontend builds update the committed Python-served assets.

The chat layout follows the user-provided Codex composer reference: project context above the input, a quiet editing surface, and one lower toolbar with attachments on the left and model/reasoning plus send/stop on the right. Model settings and secondary input controls open keyboard-accessible panels; search is opened on demand. The server-rendered new-chat entry and React conversation share neutral theme tokens. `npm run test:design-browser` checks actual server-rendered desktop, mobile and embedded surfaces in both themes and writes screenshots to `/tmp/sciver-chat-redesign/`; it never invokes a model.
