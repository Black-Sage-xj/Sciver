"""Branded, self-contained sign-in page shared by both authentication modes."""
from __future__ import annotations

import base64
import html
from functools import lru_cache
from pathlib import Path

from .appearance_ui import THEME_BOOTSTRAP_SCRIPT


@lru_cache(maxsize=4)
def _artwork(name: str) -> str:
    # Embed packaged, official artwork: the account gateway protects other URLs.
    data = (Path(__file__).with_name("static") / name).read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")


STYLE = """<style>
:root {
  color-scheme: dark;
  font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-synthesis: none;
  --ground: #07162f;
  --ink: #f4f7fc;
  --secondary: #b5c5de;
  --rule: #293c59;
  --blue: #1762f5;
}
* { box-sizing: border-box; }
html { background: var(--ground); scrollbar-color: #526785 var(--ground); }
body { margin: 0; color: var(--ink); background: var(--ground); }
body, input, button { font-size: 14px; line-height: 1.6; }
button, input { font-family: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
a { color: inherit; text-decoration-thickness: 1px; text-underline-offset: 5px; }
a:hover { color: #408ef7; }
button { cursor: pointer; }
:focus-visible { outline: 3px solid #408ef7; outline-offset: 5px; }
::selection { color: #fff; background: #1a45bb; }
.page { min-height: 100svh; max-width: 1440px; margin: auto; padding: 32px 72px 24px; display: flex; flex-direction: column; }
.skip-link { position: absolute; top: -80px; left: 24px; z-index: 2; padding: 12px 20px; border-radius: 8px; color: #08162c; background: #fff; }
.skip-link:focus { top: 16px; }
.masthead { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.brand { display: block; width: 168px; height: 60px; object-fit: contain; }
.brand-light { display: none; }
.source-link { display: inline-flex; gap: 9px; align-items: center; min-height: 44px; color: var(--secondary); text-decoration: none; }
.icon { width: 19px; height: 19px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.github-icon { fill: currentColor; stroke: none; }
main { flex: 1; display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(340px, 1fr); align-items: center; gap: clamp(48px, 7vw, 112px); padding: 72px 0 76px; }
.intro { min-width: 0; }
.hero-head { display: flex; align-items: center; justify-content: space-between; gap: 32px; }
h1 { font-size: clamp(36px, 3.8vw, 54px); font-weight: 650; line-height: 1.45; letter-spacing: -.035em; margin: 0; text-wrap: balance; }
h1 span { display: block; white-space: nowrap; }
.hero-symbol { width: clamp(72px, 9vw, 120px); height: auto; flex: none; }
.hero-copy { color: var(--secondary); font-size: 16px; line-height: 1.95; max-width: 32em; margin: 30px 0 0; }
.capabilities { list-style: none; margin: 42px 0 0; padding: 0; display: grid; gap: 23px; }
.capabilities li { display: flex; gap: 16px; align-items: flex-start; }
.capabilities .icon { color: #78b4ff; margin-top: 4px; width: 21px; height: 21px; }
.capabilities h2 { font-size: 15px; font-weight: 550; margin: 0 0 3px; }
.capabilities p { font-size: 13px; line-height: 1.8; color: var(--secondary); margin: 0; }
.login-panel { min-width: 0; width: 100%; max-width: 450px; justify-self: end; color: #08162c; background: #fff; color-scheme: light; padding: 42px 36px 30px; border-radius: 18px; box-shadow: 0 20px 60px #020b1c30; }
.login-panel h2 { margin: 0 0 10px; font-size: 27px; line-height: 1.4; font-weight: 650; letter-spacing: -.035em; }
.login-lead { color: #52627c; font-size: 14px; margin: 0 0 32px; line-height: 1.8; }
.field { margin: 0 0 21px; }
.field label { display: block; margin-bottom: 8px; font-weight: 550; }
.field input { display: block; width: 100%; height: 50px; padding: 12px 14px; border: 1px solid #ccd7e7; border-radius: 8px; color: #08162c; background: #f8faff; caret-color: #1762f5; transition: border-color .15s, background-color .15s; }
.field input::placeholder { color: #64748b; opacity: 1; }
.field input:hover { border-color: #8ba5c9; }
.field input:focus { outline: 2px solid #1762f5; outline-offset: 2px; border-color: #1762f5; background: #fff; }
.password-wrap { position: relative; }
.password-wrap input { padding-right: 54px; }
.password-toggle { display: none; position: absolute; right: 4px; top: 3px; align-items: center; justify-content: center; width: 44px; height: 44px; border: 0; border-radius: 6px; background: transparent; color: #52627c; }
.password-toggle:hover { background: #e9eff9; color: #08162c; }
.password-toggle[aria-pressed="true"] .eye-slash { display: none; }
.submit { display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; min-height: 50px; margin-top: 28px; padding: 12px; border: 1px solid #1762f5; border-radius: 8px; font-weight: 550; background: #1762f5; color: #fff; transition: background-color .15s; }
.submit:hover { background: #1a45bb; border-color: #1a45bb; }
.submit:disabled { cursor: wait; background: #335cb5; border-color: #335cb5; }
.submit .icon { width: 18px; height: 18px; }
.account-note { margin: 18px 0 0; color: #52627c; font-size: 12px; line-height: 1.8; text-align: center; }
.intro-link { display: flex; justify-content: center; align-items: center; gap: 8px; border-top: 1px solid #e4eaf3; padding-top: 23px; margin-top: 28px; color: #52627c; font-size: 13px; }
.intro-link a { color: #1a45bb; }
.error { margin: 0 0 24px; padding: 12px 14px; color: #9a2525; border: 1px solid #efc7c7; border-radius: 8px; background: #fff5f5; font-size: 13px; overflow-wrap: anywhere; }
.error span { display: block; margin-top: 4px; font-size: 12px; }
.page-footer { display: flex; justify-content: space-between; gap: 24px; border-top: 1px solid var(--rule); padding-top: 20px; color: var(--secondary); font-size: 12px; }
.page-footer p { margin: 0; }
.page-footer a { text-decoration: none; }
html[data-sciver-theme="white"] { color-scheme: light; --ground: #f4f7fc; --ink: #08162c; --secondary: #52627c; --rule: #d8e2ef; }
html[data-sciver-theme="white"] .brand-dark { display: none; }
html[data-sciver-theme="white"] .brand-light { display: block; }
html[data-sciver-theme="white"] .capabilities .icon { color: #1762f5; }
html[data-sciver-theme="white"] .login-panel { border: 1px solid #d8e2ef; box-shadow: 0 16px 48px #1838680b; }
@media (min-width: 1500px) { .page { padding-top: 42px; padding-bottom: 32px; } }
@media (max-width: 1100px) {
  .page { padding-right: 40px; padding-left: 40px; }
  main { gap: 44px; grid-template-columns: minmax(0, 1.15fr) minmax(330px, 1fr); }
  h1 { font-size: 38px; }
  .hero-head { gap: 16px; }
  .hero-symbol { width: 76px; }
  .login-panel { padding: 34px 28px 28px; }
}
@media (max-width: 800px) {
  .page { padding: 20px 24px 24px; max-width: 580px; }
  .brand { width: 140px; height: 50px; }
  .source-link { font-size: 13px; gap: 7px; }
  .source-link .desktop-label { display: none; }
  main { display: flex; flex-direction: column; align-items: stretch; padding: 36px 0 42px; gap: 0; }
  .intro { display: contents; }
  .hero-head { order: 0; justify-content: space-between; gap: 20px; }
  h1 { font-size: 34px; line-height: 1.4; }
  .hero-symbol { width: 62px; }
  .hero-copy { order: 1; font-size: 14px; line-height: 1.9; margin: 20px 0 28px; }
  .login-panel { order: 2; max-width: none; padding: 30px 26px 24px; border-radius: 14px; }
  .login-panel h2 { font-size: 24px; }
  .login-lead { margin-bottom: 26px; font-size: 13px; }
  .field input { font-size: 16px; }
  .capabilities { order: 3; margin-top: 40px; gap: 24px; }
  .page-footer { flex-direction: column; gap: 8px; font-size: 11px; }
}
@media (max-width: 360px) {
  .page { padding-right: 18px; padding-left: 18px; }
  h1 { font-size: 29px; }
  .hero-symbol { width: 54px; }
  .login-panel { padding-right: 22px; padding-left: 22px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; } }
@media (forced-colors: active) { .login-panel, .submit { border: 1px solid ButtonText; } }
</style>"""

SCRIPT = """<script>
(function() {
  "use strict";
  const form = document.querySelector("form");
  const password = document.getElementById("password");
  const toggle = document.querySelector(".password-toggle");
  const submit = form.querySelector(".submit");
  const label = submit.querySelector("span");
  toggle.style.display = "flex";
  toggle.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(show));
    toggle.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
  });
  form.addEventListener("submit", () => {
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    label.textContent = "正在登录…";
  });
  window.addEventListener("pageshow", () => {
    submit.disabled = false;
    submit.removeAttribute("aria-busy");
    label.textContent = "登录工作区";
  });
})();
</script>"""


def _icon(paths: str, *, extra: str = "") -> str:
    return f'<svg class="icon {extra}" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'


def login_html(error: str | None = None) -> str:
    message = ""
    if error:
        translations = {
            "Incorrect username or password.": "用户名或密码不正确，请重新输入。",
            "Invalid login request.": "登录请求无效，请刷新页面后重试。",
        }
        translated = translations.get(error)
        detail = f'<span lang="en">{html.escape(error)}</span>' if translated else ""
        message = f'<div class="error" role="alert" id="login-error">{html.escape(translated or error)}{detail}</div>'
    error_description = ' aria-describedby="login-error"' if error else ""
    github = _icon('<path d="M12 .9a11.1 11.1 0 0 0-3.51 21.63c.55.1.76-.24.76-.53v-2.08c-3.09.67-3.74-1.31-3.74-1.31-.51-1.29-1.24-1.63-1.24-1.63-1.01-.69.07-.68.07-.68 1.12.08 1.71 1.15 1.71 1.15.99 1.7 2.6 1.21 3.23.92.1-.72.39-1.21.7-1.49-2.47-.28-5.07-1.24-5.07-5.49 0-1.21.43-2.2 1.14-2.98-.11-.28-.49-1.41.11-2.94 0 0 .93-.3 3.05 1.14A10.6 10.6 0 0 1 12 6.24c.94 0 1.88.13 2.77.37 2.12-1.44 3.05-1.14 3.05-1.14.6 1.53.22 2.66.11 2.94.71.78 1.14 1.77 1.14 2.98 0 4.26-2.6 5.21-5.08 5.48.4.35.75 1.02.75 2.06V22c0 .29.2.64.77.53A11.1 11.1 0 0 0 12 .9Z"/>', extra="github-icon")
    arrow = _icon('<path d="M5 12h14m-6-6 6 6-6 6"/>')
    plan = _icon('<path d="M8 3H5a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1h-3M9 3h6v4H9zM8 12h8m-8 5h5"/>')
    agents = _icon('<circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="m10.6 7.7-4.2 8.6m7-8.6 4.2 8.6M8 19h8"/>')
    evidence = _icon('<path d="M4 6h6l2 2h8v12H4zM4 6V3h7l2 3h7v2m-12 6 3 3 5-5"/>')
    eye = _icon('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="eye-slash" d="m3 3 18 18"/>')
    return f'''<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Sciver 自主科学发现系统。与 AI 持续协作推进研究，让研究过程可审查、可修正、可复现。">
<meta name="theme-color" content="#07162f">
<link rel="icon" type="image/svg+xml" href="{_artwork('sciver-mark.svg')}">
<title>登录科研工作区 · Sciver Observatory Login</title>
{THEME_BOOTSTRAP_SCRIPT}{STYLE}
</head><body>
<a class="skip-link" href="#username">前往登录</a>
<div class="page">
  <header class="masthead">
    <div>
      <img class="brand brand-dark" src="{_artwork('sciver-lockup-dark.svg')}" width="168" height="60" alt="Sciver 自主科学发现系统">
      <img class="brand brand-light" src="{_artwork('sciver-lockup-light.svg')}" width="168" height="60" alt="Sciver 自主科学发现系统">
    </div>
    <a class="source-link" href="https://github.com/Black-Sage-xj/Sciver" target="_blank" rel="noopener noreferrer">{github}<span><span class="desktop-label">在 </span>GitHub<span class="desktop-label"> 了解 Sciver</span></span></a>
  </header>
  <main>
    <section class="intro" aria-labelledby="discovery-title">
      <div class="hero-head">
        <h1 id="discovery-title"><span>从提出猜想，</span><span>到获得发现。</span></h1>
        <img class="hero-symbol" src="{_artwork('sciver-symbol.svg')}" width="187" height="326" alt="" aria-hidden="true">
      </div>
      <p class="hero-copy">面向复杂、开放的科学问题，<br>与 AI 持续协作，让探索有迹可循。</p>
      <ul class="capabilities">
        <li>{plan}<div><h2>从问题到研究计划</h2><p>通过对话明确目标，让探索有清晰的起点。</p></div></li>
        <li>{agents}<div><h2>多 Agent 协同探索</h2><p>提出假设、独立验证，在持续迭代中推进研究。</p></div></li>
        <li>{evidence}<div><h2>让进展与证据持续留存</h2><p>保留过程与产物，便于审查、修正和复现。</p></div></li>
      </ul>
    </section>
    <section class="login-panel" aria-labelledby="login-title">
      <h2 id="login-title">登录科研工作区</h2>
      <p class="login-lead">继续你的探索，查看研究进展与证据。</p>
      {message}
      <form method="post" action="/login"{error_description}>
        <div class="field">
          <label for="username">用户名</label>
          <input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="输入用户名" required>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <div class="password-wrap">
            <input id="password" name="password" type="password" autocomplete="current-password" placeholder="输入密码" required>
            <button class="password-toggle" type="button" aria-label="显示密码" aria-pressed="false" aria-controls="password">{eye}</button>
          </div>
        </div>
        <button class="submit" type="submit" title="Sign in"><span>登录工作区</span>{arrow}</button>
        <p class="account-note">需要账号或忘记密码？请联系工作区管理员。</p>
      </form>
      <div class="intro-link"><span>初次了解 Sciver？</span><a href="https://github.com/Black-Sage-xj/Sciver/blob/main/README.zh-CN.md" target="_blank" rel="noopener noreferrer">阅读项目介绍</a></div>
    </section>
  </main>
  <footer class="page-footer">
    <p lang="en">Sci(ence) + (Disco)ver</p>
    <p>人机协作，持续探索。</p>
  </footer>
</div>
{SCRIPT}
</body></html>'''
