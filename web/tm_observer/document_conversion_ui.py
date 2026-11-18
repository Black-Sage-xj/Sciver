"""Global, opt-in PDF conversion settings without rendering stored credentials."""
from __future__ import annotations


def document_conversion_panel() -> str:
    """Load the public configuration through the guarded settings API."""
    return r'''<section class="tm-task-settings-panel tm-doc-conversion" data-document-conversion
      aria-labelledby="tm-doc-conversion-title">
      <h2 id="tm-doc-conversion-title">PDF 文献转换</h2>
      <p class="tm-task-settings-hint">这是当前 Sciver 服务的共享配置，适用于项目筹备与科研任务。
        默认关闭；关闭时 Literature 仍可使用论文记录、摘要和可读取的 HTML 正文。
        修改后点击“保存转换设置”，后续文献获取采用新配置。</p>
      <form data-doc-conversion-form autocomplete="off">
        <fieldset data-doc-conversion-fields disabled>
          <label class="tm-doc-conversion-toggle" for="tm-doc-conversion-enabled">
            <input id="tm-doc-conversion-enabled" name="enabled" type="checkbox">
            <span>启用 PDF 转换</span>
          </label>
          <div class="tm-doc-conversion-grid">
            <label for="tm-doc-conversion-provider">转换组件
              <select id="tm-doc-conversion-provider" name="provider">
                <option value="marker">本地 Marker</option>
                <option value="mineru">MinerU API</option>
              </select>
            </label>
          </div>
          <div data-doc-conversion-provider="marker">
            <label for="tm-doc-conversion-command">Marker 命令路径（可选）
              <input id="tm-doc-conversion-command" name="marker_executable" type="text"
                placeholder="留空时从服务运行环境查找 marker_single" spellcheck="false">
            </label>
            <p class="tm-doc-conversion-note">Marker 需要安装在运行 Sciver 的机器上。这里不会安装组件或执行转换。</p>
            <p class="tm-doc-conversion-note" data-doc-conversion-availability></p>
          </div>
          <div data-doc-conversion-provider="mineru" hidden>
            <p class="tm-doc-conversion-notice">启用 MinerU API 后，需要转换的 PDF 将上传到所配置的服务，
              并使用该服务的额度或计费规则。保存设置不会上传 PDF 或发起转换。</p>
            <label for="tm-doc-conversion-endpoint">MinerU 服务地址
              <input id="tm-doc-conversion-endpoint" name="online_base_url" type="url"
                placeholder="https://mineru.net" spellcheck="false">
            </label>
            <label for="tm-doc-conversion-token">API Token
              <input id="tm-doc-conversion-token" name="api_key" type="password"
                autocomplete="new-password" spellcheck="false" placeholder="尚未配置">
            </label>
            <p class="tm-doc-conversion-note" data-doc-conversion-token-status>Token 只写入服务端配置，不回显。</p>
            <label class="tm-doc-conversion-toggle" for="tm-doc-conversion-clear-token">
              <input id="tm-doc-conversion-clear-token" name="clear_api_key" type="checkbox">
              <span>删除已保存的 API Token</span>
            </label>
          </div>
          <details class="tm-doc-conversion-advanced">
            <summary>转换范围与超时</summary>
            <div class="tm-doc-conversion-grid">
              <label for="tm-doc-conversion-pages">最大转换页数（0 表示全部）
                <input id="tm-doc-conversion-pages" name="max_pages" type="number" min="0" max="600" step="1">
              </label>
              <label for="tm-doc-conversion-timeout">转换超时（秒）
                <input id="tm-doc-conversion-timeout" name="timeout_s" type="number" min="10" max="3600" step="1">
              </label>
            </div>
            <label class="tm-doc-conversion-toggle" for="tm-doc-conversion-images">
              <input id="tm-doc-conversion-images" name="extract_images" type="checkbox">
              <span>Marker 保留图片文件（MinerU 保留返回的图片；仍需核验图表）</span>
            </label>
          </details>
          <div class="tm-doc-conversion-actions">
            <button type="submit" data-doc-conversion-save>保存转换设置</button>
          </div>
        </fieldset>
      </form>
      <div class="tm-doc-conversion-footer">
        <p role="status" aria-live="polite" data-doc-conversion-status>正在读取转换设置…</p>
        <button type="button" data-doc-conversion-reload>重新读取设置</button>
      </div>
    </section>
    <style>
      .tm-doc-conversion { margin:18px 0; color:var(--tm-text); }
      .tm-doc-conversion fieldset { min-width:0; margin:0; border:0; padding:0; }
      .tm-doc-conversion label { display:block; margin:14px 0 7px; font-size:13px;
        line-height:1.6; color:var(--tm-text); overflow-wrap:anywhere; }
      .tm-doc-conversion .tm-doc-conversion-toggle { display:flex; align-items:flex-start; gap:9px; }
      .tm-doc-conversion input:not([type="checkbox"]), .tm-doc-conversion select {
        display:block; box-sizing:border-box; width:100%; min-width:0; max-width:100%;
        margin:6px 0; padding:10px 12px; border:1px solid var(--tm-border); border-radius:8px;
        background:var(--tm-panel); color:var(--tm-text); font:inherit; }
      .tm-doc-conversion input[type="checkbox"] { margin:4px 0 0; flex-shrink:0; accent-color:var(--tm-accent); }
      .tm-doc-conversion :is(input,select,button,summary):focus-visible {
        outline:2px solid var(--tm-accent); outline-offset:3px; }
      .tm-doc-conversion-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 18px; }
      .tm-doc-conversion-note, .tm-doc-conversion-notice { font-size:12px; line-height:1.75;
        color:var(--tm-muted); overflow-wrap:anywhere; margin:7px 0; }
      .tm-doc-conversion-notice { padding:10px 12px; border:1px solid var(--tm-border); border-radius:8px; }
      .tm-doc-conversion-advanced { margin:16px 0; color:var(--tm-muted); font-size:13px; }
      .tm-doc-conversion summary { cursor:pointer; padding:7px 0; }
      .tm-doc-conversion-actions, .tm-doc-conversion-footer { display:flex; align-items:center;
        justify-content:space-between; flex-wrap:wrap; gap:10px; margin-top:16px; }
      .tm-doc-conversion button { padding:9px 13px; border:1px solid var(--tm-border); border-radius:8px;
        background:var(--tm-panel); color:var(--tm-text); cursor:pointer; font:inherit; font-size:12px; }
      .tm-doc-conversion button:hover { background:var(--tm-hover); }
      .tm-doc-conversion button:disabled, .tm-doc-conversion fieldset:disabled { opacity:.65; }
      .tm-doc-conversion-footer p { margin:0; font-size:12px; color:var(--tm-muted);
        flex:1 1 260px; overflow-wrap:anywhere; }
      .tm-doc-conversion-footer [data-state="error"] { color:var(--tm-text); font-weight:600; }
      @media(max-width:540px) { .tm-doc-conversion-grid { grid-template-columns:minmax(0,1fr); }
        .tm-doc-conversion-footer { align-items:flex-start; } }
    </style>
    <script>(function(){
      'use strict';
      const panel=document.querySelector('[data-document-conversion]');
      if(!panel)return;
      const form=panel.querySelector('form');
      const fields=panel.querySelector('[data-doc-conversion-fields]');
      const status=panel.querySelector('[data-doc-conversion-status]');
      const reload=panel.querySelector('[data-doc-conversion-reload]');
      const input=name=>form.elements.namedItem(name);
      const endpoint='/api/settings/document-conversion';
      let revision=null,busy=false;
      function say(text,error=false){status.textContent=text;status.dataset.state=error?'error':'ready';}
      function chooseProvider(){
        for(const box of panel.querySelectorAll('[data-doc-conversion-provider]')){
          box.hidden=box.dataset.docConversionProvider!==input('provider').value;
        }
      }
      function populate(data){
        revision=data.revision;
        input('enabled').checked=data.enabled===true;
        input('provider').value=data.provider==='mineru'?'mineru':'marker';
        input('marker_executable').value=data.marker_executable||'';
        input('max_pages').value=String(data.max_pages??20);
        input('timeout_s').value=String(data.timeout_s??240);
        input('extract_images').checked=data.extract_images===true;
        input('online_base_url').value=data.online_base_url||'https://mineru.net';
        input('api_key').value='';
        input('clear_api_key').checked=false;
        input('api_key').placeholder=data.api_key_configured?'已配置；留空保留':'尚未配置';
        panel.querySelector('[data-doc-conversion-token-status]').textContent=data.api_key_source==='environment'
          ?'当前使用服务运行环境提供的 API Token。此处可删除配置文件中的 Token；环境中的 Token 需要服务管理员修改。'
          :data.api_key_configured?'已保存 API Token。留空保留原值；只在更换时填写，不会回显。'
          :'尚未配置 API Token。Token 只写入服务端配置，不回显。';
        const available=data.provider==='marker'?data.available:undefined;
        panel.querySelector('[data-doc-conversion-availability]').textContent=available===true
          ?'本地 Marker：已找到可执行命令（未执行转换）。'
          :available===false?'本地 Marker：未找到可执行命令。请安装后填写服务端的命令路径。'
          :'本地 Marker 可用性由服务端检查，保存路径后重新读取。';
        chooseProvider();
      }
      async function read(){
        if(busy)return;
        busy=true; fields.disabled=true; reload.disabled=true;
        say('正在读取转换设置…');
        try{
          const response=await fetch(endpoint,{credentials:'same-origin',cache:'no-store'});
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error(data.error||'读取失败');
          populate(data);
          say(data.enabled?'已启用 '+(data.provider==='mineru'?'MinerU API':'本地 Marker')
              +(data.available===false?'，组件尚不可用，请补齐配置。':'；保存设置不会启动转换。')
            :'PDF 转换已关闭。Literature 可继续使用摘要和可读取的 HTML 正文。');
        }catch(error){say('读取失败：'+String(error.message||error)+'。可重新读取设置。',true);}
        finally{busy=false;fields.disabled=revision===null;reload.disabled=false;}
      }
      form.addEventListener('submit',async event=>{
        event.preventDefault();
        if(busy||revision===null||!form.reportValidity())return;
        busy=true; fields.disabled=true; reload.disabled=true;
        const payload={enabled:input('enabled').checked,provider:input('provider').value,
          marker_executable:input('marker_executable').value.trim(),
          max_pages:Number(input('max_pages').value),timeout_s:Number(input('timeout_s').value),
          extract_images:input('extract_images').checked,online_base_url:input('online_base_url').value.trim(),
          api_key:input('api_key').value,clear_api_key:input('clear_api_key').checked,
          expected_revision:revision};
        say('正在保存转换设置…');
        try{
          const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify(payload)});
          const data=await response.json();
          if(response.status===409){
            say('设置已被其他页面修改。请先“重新读取设置”，再确认并保存本次修改；如需更换 API Token，请重新填写。',true);
            return;
          }
          if(!response.ok||!data.ok)throw new Error(data.error||'保存失败');
          populate(data);
          say('已保存。PDF 转换'+(data.enabled?'已启用':'已关闭')
            +(data.enabled&&data.available===false?'，组件尚不可用，请补齐配置。':'，后续文献获取采用新配置；本次未执行转换。'));
        }catch(error){say('保存失败：'+String(error.message||error)+'。请检查配置后重试。',true);}
        finally{input('api_key').value='';payload.api_key='';busy=false;fields.disabled=false;reload.disabled=false;}
      });
      input('provider').addEventListener('change',chooseProvider);
      input('api_key').addEventListener('input',()=>{if(input('api_key').value)input('clear_api_key').checked=false;});
      input('clear_api_key').addEventListener('change',()=>{if(input('clear_api_key').checked)input('api_key').value='';});
      reload.addEventListener('click',read);
      window.addEventListener('pagehide',()=>{input('api_key').value='';});
      read();
    })();</script>'''
