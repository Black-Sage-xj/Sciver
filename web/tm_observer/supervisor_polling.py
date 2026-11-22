"""Update the legacy Supervisor transcript in place without restarting its chat."""

def refresh_script(*, active: bool, waiting: bool) -> str:
    if not active and not waiting:
        return ''
    delay = 3000 if waiting else 8000
    return '<script>(()=>{' + f'const delay={delay};' + r'''
const selector='.tm-supervisor-page';
// Web components reflect default attributes during hydration. Keep the server
// signature separately so unchanged messages retain their DOM and scroll state.
const signatures=new WeakMap();
document.querySelectorAll(selector+' loquix-message-list').forEach(list=>{
  Array.from(list.children).forEach(message=>signatures.set(message,message.outerHTML));
});
function cloneMessage(message){
  const clone=message.cloneNode(true);signatures.set(clone,message.outerHTML);return clone;
}
const composers=()=>Array.from(document.querySelectorAll('loquix-chat-composer'));
const hasDraft=()=>composers().some(c=>{
  const p=c.shadowRoot?.querySelector('loquix-prompt-input');
  const attachments=c.querySelector('loquix-attachment-panel');
  return Boolean(String(p?.value||'').trim()||attachments?.attachments?.length);
});
const editing=()=>{
  const a=document.activeElement;
  return Boolean(a?.matches?.('input,textarea,select,[contenteditable=true],loquix-chat-composer')||composers().some(c=>c.shadowRoot?.activeElement));
};
const busy=()=>document.visibilityState==='hidden'||document.querySelector('details[open]')||editing()||hasDraft();
function copyBlock(current,next,query){
  const old=current.querySelector(query), fresh=next.querySelector(query);
  if(old&&fresh&&old.outerHTML!==fresh.outerHTML) old.replaceWith(fresh.cloneNode(true));
  else if(old&&!fresh) old.remove();
  else if(!old&&fresh)current.insertBefore(fresh.cloneNode(true),current.querySelector('.tm-supervisor-session'));
}
function patchSession(current,next){
  copyBlock(current,next,'.tm-incident-bar');
  const oldList=current.querySelector('loquix-message-list');
  const newList=next.querySelector('loquix-message-list');
  if(oldList&&newList&&oldList.innerHTML!==newList.innerHTML){
    const scroll=oldList.shadowRoot?.querySelector('[part="scroll-container"]');
    const top=scroll?.scrollTop||0;
    const atEnd=!scroll||scroll.scrollHeight-scroll.clientHeight-top<32;
    Array.from(newList.children).forEach((message,index)=>{
      const existing=oldList.children[index];
      if(!existing)oldList.append(cloneMessage(message));
      else if(signatures.get(existing)!==message.outerHTML)existing.replaceWith(cloneMessage(message));
    });
    while(oldList.children.length>newList.children.length)oldList.lastElementChild.remove();
    requestAnimationFrame(()=>{if(scroll)scroll.scrollTop=atEnd?scroll.scrollHeight:top;});
  }
  const oldForm=current.querySelector('loquix-chat-composer');
  const newForm=next.querySelector('loquix-chat-composer');
  if(oldForm&&newForm) oldForm.toggleAttribute('disabled',newForm.hasAttribute('disabled'));
  else if(oldForm&&!newForm){
    oldForm.remove(); current.querySelector('.tm-incident-error')?.remove();
  }else if(newForm&&!oldForm){
    const chat=current.querySelector('.tm-incident-chat');
    chat.append(newForm.cloneNode(true));
    const error=next.querySelector('.tm-incident-error');
    if(error)chat.append(error.cloneNode(true));
  }
  const actions=current.querySelector('.tm-incident-actions');
  const newActions=next.querySelector('.tm-incident-actions');
  if(newActions&&!actions)current.querySelector('.tm-incident-chat')?.append(newActions.cloneNode(true));
  else copyBlock(current,next,'.tm-incident-actions');
  current.querySelectorAll('loquix-chat-composer').forEach(c=>window.tmBindComposer?.(c));
  current.querySelectorAll('[data-incident-resolve],[data-incident-close]').forEach(button=>{
    const operation=button.dataset.incidentResolve?'resolve':'close';
    const session=button.dataset.incidentResolve||button.dataset.incidentClose;
    button.onclick=()=>window.tmIncidentPost?.('/api/supervisor-session/'+session+'/'+operation,{note:operation+' by operator'},button);
  });
}
async function poll(){
  try{
    if(busy())return;
    const response=await fetch(location.href.split('#')[0],{cache:'no-store'});
    if(!response.ok||busy())return;
    const next=new DOMParser().parseFromString(await response.text(),'text/html').querySelector(selector);
    const current=document.querySelector(selector);
    // Login/error responses must never replace the live transcript.
    if(!next||!current||busy())return;
    copyBlock(current,next,'.tm-supervisor-cycles');
    copyBlock(current,next,'.tm-supervisor-recovery');
    next.querySelectorAll('.tm-supervisor-session').forEach((section,index)=>{
      const old=Array.from(current.querySelectorAll('.tm-supervisor-session')).find(s=>s.id===section.id);
      if(old)patchSession(old,section);
      else{
        const added=section.cloneNode(true);
        added.querySelectorAll('loquix-message-list').forEach(list=>Array.from(list.children).forEach(message=>signatures.set(message,message.outerHTML)));
        current.insertBefore(added,current.querySelectorAll('.tm-supervisor-session')[index]||null);
        patchSession(added,section);
      }
    });
  }catch(error){/* Keep the transcript and retry transient transport errors. */}
  finally{setTimeout(poll,delay);}
}
setTimeout(poll,delay);
})();</script>'''
