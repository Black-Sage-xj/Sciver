/* One Markdown reader for graph documents, research reports and offline exports. */
(()=>{
'use strict';
const fallbackEscape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function create(options={}){
const esc=options.escape||fallbackEscape;
const resolve=options.resolve||(()=>null);
const base=options.base||(()=> '');
function render(text,source,context={}){
  text=String(text??'');
  const resolver=context.resolve||resolve;
  const imageUrl=context.imageUrl||options.imageUrl;
  const resolvedHrefs=new Set();
  if(!window.marked||!window.DOMPurify)return '<pre>'+esc(text)+'</pre>';
  const math=[];
  let protectedText=text.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|(\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g,(raw,code)=>{
    if(code)return code;
    const block=raw.startsWith('$$');math.push({value:raw.slice(block?2:1,block?-2:-1),block});
    return 'TMKATEXPLACEHOLDER'+(math.length-1)+'END';
  });
  protectedText=protectedText.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|!\[\[([^\]\n]+)\]\]/g,(full,code,link)=>{
    if(code)return code;
    const [target,label]=link.split('|'),width=/^\d{1,4}$/.test(label||'')?Number(label):null;
    if(target.split('#')[0].toLowerCase().endsWith('.md'))return '[['+link+']]';
    return '<img src="'+esc(target)+'" alt="'+esc(width?target:(label||target))+
      '" data-vault-image="1"'+(width?' width="'+width+'"':'')+'>';
  });
  protectedText=protectedText.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|(?<!!)\[\[([^\]\n]+)\]\]/g,(full,code,link)=>{
    if(code)return code;
    const ref=resolver(link,source),label=link.split('|')[1]||ref?.label||link.split('|')[0];
    const caption=label.replace(/\[/g,'\\[').replace(/\]/g,'\\]');
    if(ref?.href)resolvedHrefs.add(ref.href);
    return ref?.href?'['+caption+']('+ref.href+')':ref?'['+caption+'](#tmref='+encodeURIComponent(ref.kind+':'+ref.id)+
      (ref.anchor?'%23'+encodeURIComponent(ref.anchor):'')+')':esc(label);
  });
  const result=window.DOMPurify.sanitize(window.marked.parse(protectedText));
  const doc=new DOMParser().parseFromString('<div id="parsed">'+result+'</div>','text/html');
  const wrap=doc.getElementById('parsed');
  if(window.Prism)wrap.querySelectorAll('pre code[class*="language-"]').forEach(code=>{
    window.Prism.highlightElement(code);
  });
  wrap.querySelectorAll('img').forEach(img=>{
    const raw=img.getAttribute('src')||'';
    if(!/^https?:\/\//i.test(raw)){
      if(!raw||/^[a-z][a-z\d+.-]*:/i.test(raw)||raw.startsWith('//')){img.remove();return}
      let target;try{target=decodeURIComponent(raw.split('#')[0])}catch{img.remove();return}
      const url=imageUrl?imageUrl(target,source||'',Boolean(img.dataset.vaultImage)):
        base()+'/image?path='+encodeURIComponent(target)+'&source='+encodeURIComponent(source||'')+
        (img.dataset.vaultImage?'&vault=1':'');
      if(!url){img.remove();return}
      img.setAttribute('src',url);
    }
    img.removeAttribute('srcset');img.removeAttribute('sizes');
    img.setAttribute('loading','lazy');img.setAttribute('referrerpolicy','no-referrer');
  });
  wrap.querySelectorAll('a').forEach(a=>{
    const href=a.getAttribute('href')||'';
    if(href.startsWith('#tmref=')){a.dataset.ref=decodeURIComponent(href.slice(7));a.removeAttribute('href')}
    else if(href.startsWith('#')){}
    else if(/^https?:\/\//i.test(href)){a.target='_blank';a.rel='noopener noreferrer'}
    else if(resolvedHrefs.has(href)&&!(/^[a-z][a-z\d+.-]*:/i.test(href)||href.startsWith('//'))){}
    else{const ref=resolver(href,source);if(ref?.href){
      if(/^(?:javascript|data|vbscript):/i.test(ref.href))a.removeAttribute('href');
      else a.setAttribute('href',ref.href);
    }else if(ref){
      a.dataset.ref=ref.kind+':'+ref.id+(ref.anchor?'#'+ref.anchor:'');a.removeAttribute('href');
    }else a.removeAttribute('href')}
    if(a.dataset.ref){a.tabIndex=0;a.setAttribute('role','button')}
  });
  wrap.querySelectorAll('*').forEach(el=>{for(const child of Array.from(el.childNodes)){
    if(child.nodeType!==3||!child.textContent.includes('TMKATEXPLACEHOLDER'))continue;
    const fragment=doc.createDocumentFragment(),re=/TMKATEXPLACEHOLDER(\d+)END/g;
    let pos=0,m;
    while((m=re.exec(child.textContent))){
      fragment.append(doc.createTextNode(child.textContent.slice(pos,m.index)));
      const spec=math[Number(m[1])];
      if(!spec){fragment.append(doc.createTextNode(m[0]));pos=re.lastIndex;continue}
      const box=doc.createElement(spec.block?'div':'span');
      try{if(window.katex)box.innerHTML=window.katex.renderToString(spec.value,{
        displayMode:spec.block,throwOnError:false,trust:false,output:'html'
      });else box.textContent=spec.value}catch{box.textContent=spec.value}
      fragment.append(box);pos=re.lastIndex;
    }
    fragment.append(doc.createTextNode(child.textContent.slice(pos)));child.replaceWith(fragment);
  }});
  return wrap.innerHTML;
}
return {render};
}
globalThis.SciverMarkdown={create};
})();
