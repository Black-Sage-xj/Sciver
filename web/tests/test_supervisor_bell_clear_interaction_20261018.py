"""Bell regression: dismissing individual items must not dismiss the popover."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tm_observer.supervisor_bell_ui import BELL_SCRIPT, BELL_STYLE


NODE_HARNESS = r"""
const vm=require('node:vm'), assert=require('node:assert/strict');
class Element {
  constructor(tag='div'){
    this.tagName=tag.toUpperCase();this.className='';this.children=[];this.parentNode=null;
    this.listeners={};this.dataset={};this.attributes={};this.hidden=false;
    this.textContent='';this.disabled=false;
  }
  append(...children){for(const child of children){this.children.push(child);child.parentNode=this;}}
  appendChild(child){this.append(child);return child;}
  replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this.append(...children);}
  remove(){if(this.parentNode){const parent=this.parentNode;parent.children=parent.children.filter(x=>x!==this);this.parentNode=null;}}
  setAttribute(k,v){this.attributes[k]=v;}
  getAttribute(k){return this.attributes[k];}
  addEventListener(name,callback){this.listeners[name]=callback;}
  querySelector(selector){
    if(selector==='.tm-notices-item-top span')return this.querySelector('.tm-notices-item-top')?.children.find(x=>x.tagName==='SPAN')||null;
    return this.querySelectorAll(selector)[0]||null;
  }
  querySelectorAll(selector){
    const found=[];
    const matches=el=>selector==='[data-notice-key]'?Object.hasOwn(el.dataset,'noticeKey'):
      selector.startsWith('[data-')?el.attributes[selector.slice(1,-1)]!==undefined:
      selector.startsWith('.')?el.className.split(' ').includes(selector.slice(1)):el.tagName===selector.toUpperCase();
    function walk(node){for(const child of node.children){if(matches(child))found.push(child);walk(child);}}
    walk(this);return found;
  }
  contains(target){for(let current=target;current;current=current.parentNode)if(current===this)return true;return false;}
  dispatch(name,event){this.listeners[name]?.(event);}
  focus(){}
}
const wrapper=new Element('div'),toggle=new Element('button'),panel=new Element('section'),
 badge=new Element('span'),summary=new Element('span'),list=new Element('div'),more=new Element('button');
panel.hidden=true;
wrapper.append(toggle,panel);panel.append(summary,list,more);toggle.append(badge);
const elements={
  '[data-notices-toggle]':toggle,'[data-notices-panel]':panel,
  '[data-notices-count]':badge,'[data-notices-summary]':summary,
  '[data-notices-list]':list,'[data-notices-more]':more,
};
wrapper.querySelector=selector=>elements[selector]||Element.prototype.querySelector.call(wrapper,selector);
const docEvents={}, storage=new Map();
const entries=[1,2,3].map(n=>({notice_key:'notice-'+n,task:'Example '+n,cycle:String(n),
   url:'/run/8dd38a9977eef7e5/',code:'task_stopped',summary:'needs attention'}));
const context={
  document:{querySelector:selector=>selector==='[data-supervisor-notices]'?wrapper:null,
    createElement:tag=>new Element(tag),addEventListener:(name,fn)=>docEvents[name]=fn,
    hidden:false},
  window:{addEventListener(){}},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  URLSearchParams,fetch:async()=>({ok:true,json:async()=>({items:entries,active_keys:entries.map(x=>x.notice_key),next_offset:null})}),
  setInterval:()=>0,console
};
vm.runInNewContext(process.argv[1],context);
const tick=()=>new Promise(r=>setTimeout(r,0));
function simulateClick(target){
  const path=[];for(let n=target;n;n=n.parentNode)path.push(n);
  const e={target,stopped:false,stopPropagation(){this.stopped=true},composedPath(){return path}};
  // Emulate DOM's frozen bubbling path, even if row is removed during handler.
  for(const n of [...path,context.document]){
    if(e.stopped)break;
    if(n===context.document)docEvents.click?.(e);
    else n.dispatch?.('click',e);
  }
}
(async()=>{
  await tick();await tick();
  assert.equal(list.querySelectorAll('.tm-notices-item').length,3);
  simulateClick(toggle);await tick();await tick();
  assert.equal(panel.hidden,false);
  assert.equal(toggle.getAttribute('aria-expanded'),'true');
  let controls=list.querySelectorAll('.tm-notices-clear');
  simulateClick(controls[0]);
  assert.equal(panel.hidden,false,'clicking clear must never dismiss its containing popup');
  assert.equal(toggle.getAttribute('aria-expanded'),'true');
  assert.equal(list.querySelectorAll('.tm-notices-item').length,2);
  assert.equal(JSON.parse(storage.get('tm.supervisor.notices.v1')).dismissed.length,1);
  controls=list.querySelectorAll('.tm-notices-clear');simulateClick(controls[0]);
  assert.equal(panel.hidden,false,'consecutive clear must preserve open state');
  assert.equal(list.querySelectorAll('.tm-notices-item').length,1);
  simulateClick(list.querySelector('.tm-notices-clear'));
  assert.equal(panel.hidden,false,'empty inbox does not close panel');
  assert(list.querySelector('.tm-notices-empty'),'empty state remains visible');
  assert.equal(JSON.parse(storage.get('tm.supervisor.notices.v1')).dismissed.length,3);
  simulateClick(toggle);assert.equal(panel.hidden,true,'bell still closes explicitly');
  simulateClick(toggle);assert.equal(panel.hidden,false,'bell still reopens');
  // Outside clicks are still supported; the fix must not break dismissal.
  docEvents.click({target:new Element(),composedPath:()=>[]});
  assert.equal(panel.hidden,true,'outside click still closes panel');
  console.log('Three consecutive clear clicks stay open; empty state and explicit dismissal work');
})().catch(e=>{console.error(e);process.exitCode=1});
"""


def test_bell_button_centers_icon_without_inherited_padding():
    assert 'height:35px; padding:0; box-sizing:border-box' in BELL_STYLE
    assert 'display:flex; align-items:center;' in BELL_STYLE
    assert 'justify-content:center;' in BELL_STYLE
    assert 'svg { display:block; width:19px; height:19px; flex:none;' in BELL_STYLE


@pytest.mark.skipif(shutil.which('node') is None, reason='requires Node.js')
def test_consecutive_clear_keeps_popover_and_outside_click_closes():
    script = BELL_SCRIPT.split('>', 1)[1].rsplit('</script>', 1)[0]
    result = subprocess.run(['node', '-e', NODE_HARNESS, script],
                            capture_output=True, text=True, timeout=12)
    assert result.returncode == 0, result.stderr
    assert 'Three consecutive clear clicks stay open' in result.stdout
