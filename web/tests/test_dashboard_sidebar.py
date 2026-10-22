"""Global Observatory sidebar collapse is a layout-only client preference."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from tm_observer.dashboard_ui import dashboard_shell, _VIEWS


class SidebarCollapseTests(unittest.TestCase):
    def test_all_routes_have_accessible_toggle_and_icon_titles(self):
        for role in ("research_chat", "run", "explorer", "workflow", "architecture", "services", "settings"):
            with self.subTest(role=role):
                page=dashboard_shell("Test", "<p>body</p>", active=role)
                self.assertIn('class="tm-brand-name">Sciver</div>', page)
                self.assertIn('src="/favicon.svg"', page)
                self.assertIn('href="/favicon.svg"', page)
                self.assertNotIn("TheoryMaster", page)
                self.assertIn('data-sidebar-toggle', page)
                self.assertIn('aria-label="收起侧边栏"', page)
                self.assertIn('class="tm-sidebar-pane"', page)
                self.assertIn('<rect x="3" y="3" width="18" height="18" rx="2"/>', page)
                self.assertIn('<path d="M10 3v18"/>', page)
                self.assertNotIn('d="m15 5-7 7 7 7"', page)
                self.assertNotIn('tm-sidebar-toggle svg { transform:rotate', page)
                self.assertIn('aria-expanded="true"', page)
                self.assertEqual(page.count('class="tm-nav-text"'), len(_VIEWS))
                self.assertNotIn('title="Research Explorer"', page)
                self.assertNotIn('title="Explore map"', page)
                self.assertIn("tm-observer-sidebar-collapsed-v1", page)
                self.assertIn("body.tm-sidebar-collapsed .tm-main { margin-left:0; }", page)
                self.assertNotIn("margin-left:72px", page)
                self.assertIn("data-workspace-nav", page)
                self.assertIn("data-sidebar-edge", page)
                self.assertIn("body.tm-sidebar-collapsed.tm-sidebar-peek .tm-sidebar", page)
                self.assertIn(".tm-sidebar-toggle { display:none; }", page)
                self.assertIn("body.stage-height-focus.tm-sidebar-collapsed .tm-main { margin-left:0; }", page)

    def test_mobile_header_collapses_navigation_without_losing_routes(self):
        from bs4 import BeautifulSoup
        from tm_observer.dashboard_ui import STYLE
        for role in ("research_chat", "run", "explorer", "workflow", "architecture", "services", "settings"):
            with self.subTest(role=role):
                page=dashboard_shell("Test", "<p>body</p>", active=role)
                dom=BeautifulSoup(page,"html.parser")
                nav=dom.select_one("nav#tm-main-nav")
                menu=dom.select_one("button[data-mobile-nav-toggle]")
                self.assertIsNotNone(nav)
                self.assertEqual(len(nav.select("a.tm-nav-link")), len(_VIEWS))
                self.assertEqual([a.get("href") for a in nav.select("a.tm-nav-link")],
                                 [url for _key, _label, url in _VIEWS])
                self.assertEqual(nav.select_one('a[href="/"]').get_text(strip=True), "Task Console")
                self.assertEqual(menu.get("aria-controls"),nav.get("id"))
                self.assertEqual(menu.get("aria-expanded"),"false")
                self.assertEqual(menu.get("aria-label"),"打开导航菜单")
                self.assertEqual(len(nav.select('a[aria-current="page"]')),1)
        self.assertIn(".tm-mobile-nav-toggle { display:none;", STYLE)
        self.assertIn(".tm-mobile-nav-toggle { display:inline-flex; }",STYLE)
        self.assertIn(".tm-nav { display:none; flex-direction:column;",STYLE)
        self.assertIn(".tm-sidebar.tm-mobile-nav-open .tm-nav { display:flex; }",STYLE)
        self.assertIn("html, body { max-width:100%; overflow-x:clip; }",STYLE)
        self.assertIn(".tm-tui-table-scroll { overflow-x:auto; }",STYLE)

    @unittest.skipUnless(shutil.which("node"),"Node.js unavailable")
    def test_mobile_menu_click_escape_and_outside_close(self):
        page=dashboard_shell("Test","<p>body</p>",active="run")
        js=page.split("<script data-mobile-nav-script>",1)[1].split("</script>",1)[0]
        script=r"""
const vm=require('vm'), assert=require('assert');
const source=process.argv[1];
const classes=new Set(),listeners={},attrs={};
const button={addEventListener(type,fn){listeners['button:'+type]=fn},
  setAttribute(key,value){attrs[key]=value},focus(){listeners.focused=true}};
const sidebar={classList:{contains(v){return classes.has(v)},
  toggle(v,on){if(on)classes.add(v);else classes.delete(v)}},
  contains(target){return target===button||target===sidebar}};
const document={querySelector(sel){return sel==='.tm-sidebar'?sidebar:
 sel==='[data-mobile-nav-toggle]'?button:null},
 addEventListener(type,fn){listeners['doc:'+type]=fn}};
vm.runInNewContext(source,{document});
assert.equal(attrs['aria-expanded'],undefined);
listeners['button:click']();
assert(classes.has('tm-mobile-nav-open'));
assert.equal(attrs['aria-expanded'],'true');
assert.equal(attrs['aria-label'],'关闭导航菜单');
listeners['doc:click']({target:button});
assert(classes.has('tm-mobile-nav-open'));
listeners['doc:keydown']({key:'Escape'});
assert(!classes.has('tm-mobile-nav-open'));
assert.equal(attrs['aria-expanded'],'false');
assert(listeners.focused);
listeners['button:click']();
listeners['doc:click']({target:{}});
assert(!classes.has('tm-mobile-nav-open'));
"""
        process=subprocess.run(["node","-e",script,js],capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stderr)

    @unittest.skipUnless(shutil.which("node"),"Node.js unavailable")
    def test_persisted_and_reversible_without_storage(self):
        page=dashboard_shell("Test","<p>body</p>",active="run")
        js=page.rsplit("<script>",1)[-1].split("</script>",1)[0]
        script=r"""
const vm=require('vm'), assert=require('assert');
const source=process.argv[1];
function mount(initial,blocked=false,mobile=false){
  const handlers={},attrs={},classNames=new Set(),elements={};
  function elem(key){
    const e={listeners:{},attributes:{},classList:{contains(){return false}},
      addEventListener(k,fn){this.listeners[k]=fn},
      setAttribute(k,v){this.attributes[k]=v},
      focus(){handlers.focused=key},
      contains(target){return target===this||target?.owner===this},
      querySelector(sel){return sel==='.tm-nav-link'?navlink:null}};
    elements[key]=e;return e;
  }
  const toggle=elem('toggle'),sidebar=elem('sidebar'),workspace=elem('workspace'),
    edge=elem('edge'),mobileMenu=elem('mobileMenu'),navlink=elem('navlink');
  mobileMenu.click=()=>{handlers.mobileClicks=(handlers.mobileClicks||0)+1};
  navlink.focus=()=>{handlers.focused='navlink'};
  const document={body:{classList:{toggle(cls,on){if(on)classNames.add(cls);else classNames.delete(cls)}}},
    querySelector(sel){return {
      '[data-sidebar-toggle]':toggle,'.tm-sidebar':sidebar,
      '[data-workspace-nav]':workspace,'[data-sidebar-edge]':edge,
      '[data-mobile-nav-toggle]':mobileMenu}[sel]||null},
    addEventListener(k,fn){handlers['document:'+k]=fn}};
  const window={matchMedia:()=>({matches:!mobile}),
    addEventListener(k,fn){handlers['window:'+k]=fn}};
  const storage={getItem(){if(blocked)throw Error('storage disabled');return initial},
    setItem(_,v){if(blocked)throw Error('storage disabled');initial=v}};
  vm.runInNewContext(source,{document,window,localStorage:storage});
  return {handlers,attr:toggle.attributes,workspace:workspace.attributes,classNames,
    read:()=>initial,click:(key,e={detail:1})=>elements[key].listeners.click(e),
    enter:()=>edge.listeners.mouseenter(),leave:()=>sidebar.listeners.mouseleave(),
    outside:()=>handlers['document:click']({target:{}}),
    escape:()=>handlers['document:keydown']({key:'Escape'})};
}
let state=mount('1');
assert(state.classNames.has('tm-sidebar-collapsed'));
assert(!state.classNames.has('tm-sidebar-peek'));
assert.equal(state.attr['aria-expanded'],'false');
assert.equal(state.workspace['aria-expanded'],'false');
state.enter();
assert(state.classNames.has('tm-sidebar-peek'),'left-edge mouse hover temporarily opens navigation');
state.leave();
assert(!state.classNames.has('tm-sidebar-peek'),'mouse leaving drawer closes temporary navigation');
state.click('workspace');
assert(state.classNames.has('tm-sidebar-peek'),'Workspace breadcrumb opens navigation');
assert.equal(state.read(),'1','temporary reveal does not modify saved collapsed preference');
state.outside();
assert(!state.classNames.has('tm-sidebar-peek'),'outside click dismisses popover');
state.click('workspace',{detail:0});
assert.equal(state.handlers.focused,'navlink','keyboard opening focuses navigation');
state.escape();
assert(!state.classNames.has('tm-sidebar-peek'));
assert.equal(state.handlers.focused,'workspace');
state.enter();
state.click('toggle');
assert(!state.classNames.has('tm-sidebar-collapsed'),'toggle pins navigation open');
assert(!state.classNames.has('tm-sidebar-peek'));
assert.equal(state.read(),'0');
assert.equal(state.attr['aria-expanded'],'true');
state.click('toggle');
assert(state.classNames.has('tm-sidebar-collapsed'),'toggle collapses full navigation again');
assert.equal(state.read(),'1');
let fallback=mount(null,true);
fallback.click('toggle');
assert(fallback.classNames.has('tm-sidebar-collapsed'));
fallback.click('workspace');
assert(fallback.classNames.has('tm-sidebar-peek'),'hover navigation works without localStorage');
let phone=mount('1',false,true);
phone.click('workspace');
assert.equal(phone.handlers.mobileClicks,1,'touch/mobile reuses mobile menu');
assert(!phone.classNames.has('tm-sidebar-peek'),'mobile does not enable desktop drawer');
"""
        proc=subprocess.run(["node","-e",script,js],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)

if __name__=="__main__":
    unittest.main()
