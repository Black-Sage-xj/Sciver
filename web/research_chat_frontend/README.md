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
