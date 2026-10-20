// Reuse requests across formula cards and enlarged views; bound model concurrency.
const cache=new Map();
let active=0;
const waiting=[];
async function slot(){if(active>=2)await new Promise(resolve=>waiting.push(resolve));active++;}
function release(){active--;waiting.shift()?.();}
export function locateFormula(evidence,expression){
  const url=evidence?.metadata?.source_url;
  if(!url)return Promise.reject(new Error('缺少原文地址'));
  const key=JSON.stringify([url,expression]);
  if(cache.has(key))return cache.get(key);
  const pending=(async()=>{
    await slot();
    try{
      const response=await fetch(url);const source=await response.json();
      if(!response.ok)throw new Error(source.detail || '原页读取失败');

      const path=url.split('?')[0].replace(/\/(?:source|formula-page)$/,'/location');
      const located=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page_no:source.page_no ?? null,quote:expression})});
      const position=await located.json();
      if(!located.ok)throw new Error(position.detail || '原页定位失败');
      return {...source,...position,highlight_boxes:(position.highlight_boxes || []).filter(b=>[b.left,b.top,b.width,b.height].every(Number.isFinite) && b.left>=0 && b.top>=0 && b.width>0 && b.height>0 && b.left+b.width<=100.01 && b.top+b.height<=100.01)};
    }finally{release();}
  })();
  cache.set(key,pending);
  pending.then(value=>{if(!['located','located_text'].includes(value.position_status))cache.delete(key);},()=>cache.delete(key));
  if(cache.size>128)cache.delete(cache.keys().next().value);
  return pending;
}
