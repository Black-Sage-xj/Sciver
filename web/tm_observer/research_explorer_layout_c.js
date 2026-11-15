/* Layout C projects one display predecessor; source science records remain intact. */
(function(root){
'use strict';
const compare=(a,b)=>String(a).localeCompare(String(b),'en',{numeric:true});
function projectForest(nodes,edges){
  const ordered=[...nodes].sort((a,b)=>compare(a.id,b.id));
  const incoming=new Map(ordered.map(n=>[n.id,[]]));
  for(const edge of edges)if(edge.source!==edge.target&&incoming.has(edge.source)&&incoming.has(edge.target))
    incoming.get(edge.target).push(edge);
  const parent=new Map(),children=new Map(ordered.map(n=>[n.id,[]])),selectedEdges=[];
  const components=new Map(ordered.map(n=>[n.id,n.id]));
  const find=id=>{
    let root=id;
    while(components.get(root)!==root)root=components.get(root);
    while(id!==root){const next=components.get(id);components.set(id,root);id=next;}
    return root;
  };
  for(const node of ordered){
    const candidates=incoming.get(node.id).sort((a,b)=>compare(a.source,b.source)||compare(a.id,b.id));
    for(const edge of candidates){
      const sourceRoot=find(edge.source),targetRoot=find(edge.target);
      // With at most one incoming edge, joining an existing component would
      // close a directed cycle. Try the next natural source in that case.
      if(sourceRoot===targetRoot)continue;
      parent.set(node.id,edge.source);children.get(edge.source).push(node.id);
      selectedEdges.push(edge);components.set(targetRoot,sourceRoot);break;
    }
  }
  const roots=ordered.filter(n=>!parent.has(n.id)).map(n=>n.id);
  const traversal=[...roots];
  for(let i=0;i<traversal.length;i++)traversal.push(...children.get(traversal[i]));
  return {parent,children,roots,edges:selectedEdges,traversal};
}
function treeCoordinates(nodes,edges,{gapX=64,gapY=36,rootGap=64}={}){
  const forest=projectForest(nodes,edges),{parent,children,roots,traversal}=forest;
  const lookup=new Map(nodes.map(n=>[n.id,n])),depth=new Map();
  const ordered=traversal.map(id=>lookup.get(id));
  const width=n=>n.style?.size?.[0]||352;
  const height=n=>n.style?.size?.[1]||154;
  const columnWidths=[];
  for(const n of ordered){
    const level=parent.has(n.id)?depth.get(parent.get(n.id))+1:0;
    depth.set(n.id,level);columnWidths[level]=Math.max(columnWidths[level]||0,width(n));
  }
  const columnX=[0];
  for(let i=1;i<columnWidths.length;i++)
    columnX[i]=columnX[i-1]+columnWidths[i-1]/2+gapX+columnWidths[i]/2;
  const spans=new Map();
  // Iterative passes also handle long single branches without stack overflow.
  for(let i=ordered.length-1;i>=0;i--){
    const n=ordered[i],kids=children.get(n.id);
    const total=kids.reduce((sum,id)=>sum+spans.get(id),0)+Math.max(0,kids.length-1)*gapY;
    spans.set(n.id,Math.max(height(n),total));
  }
  const positions=new Map(),bands=new Map();let rootY=0;
  for(const id of roots){
    const pending=[{id,top:rootY}];
    while(pending.length){
      const {id,top}=pending.pop(),node=lookup.get(id),span=spans.get(id);
      const x=columnX[depth.get(id)],y=top+span/2;
      bands.set(id,{top,span});
      node.style={...node.style,x,y};positions.set(id,{x,y,depth:depth.get(id)});
      const kids=children.get(id);
      const total=kids.reduce((sum,kid)=>sum+spans.get(kid),0)+Math.max(0,kids.length-1)*gapY;
      let childTop=top+(span-total)/2;
      for(const kid of kids){pending.push({id:kid,top:childTop});childTop+=spans.get(kid)+gapY;}
    }
    rootY+=spans.get(id)+rootGap;
  }
  // Early C centers each parent between its first and last child. Keep the
  // variable-height band as a bound so large cards never overlap another tree.
  for(let i=ordered.length-1;i>=0;i--){
    const node=ordered[i],kids=children.get(node.id);
    if(!kids.length)continue;
    const {top,span}=bands.get(node.id);
    const midpoint=(positions.get(kids[0]).y+positions.get(kids[kids.length-1]).y)/2;
    const y=Math.max(top+height(node)/2,Math.min(top+span-height(node)/2,midpoint));
    node.style.y=y;positions.get(node.id).y=y;
  }
  return {...forest,positions};
}
const api={projectForest,treeCoordinates};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
root.SciverTreeLayout=api;
})(typeof globalThis!=='undefined'?globalThis:this);
