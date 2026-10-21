/* Same-tab return context only; no business data or credentials. */
(() => {
  'use strict';
  const key='overseas-customs-return-v1';
  const routes=new Set(['/','/deals','/research','/research/results','/knowledge']);
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');
    if(!link||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const url=new URL(link.href,location.href);
    if(url.origin!==location.origin||url.pathname!=='/customs'||!routes.has(location.pathname))return;
    const id=link.id||'customs-origin-link';link.id=id;
    try{sessionStorage.setItem(key,JSON.stringify({url:location.pathname+location.search+location.hash,id,x:scrollX,y:scrollY}));}catch{}
  });
  window.addEventListener('pageshow',()=>{
    if(location.pathname==='/customs')return;
    try{
      const saved=JSON.parse(sessionStorage.getItem(key)||'null');
      if(!saved||saved.url!==location.pathname+location.search+location.hash)return;
      sessionStorage.removeItem(key);
      setTimeout(()=>{
        (document.getElementById(saved.id)||document.querySelector('a[href^="/customs"]'))?.focus({preventScroll:true});
        scrollTo({left:saved.x,top:saved.y,behavior:'instant'});
      },0);
    }catch{}
  });
  window.overseasCustomsReturn=()=>{
    try{
      const saved=JSON.parse(sessionStorage.getItem(key)||'null');
      const previous=new URL(document.referrer);
      if(saved&&previous.origin===location.origin&&routes.has(previous.pathname)&&saved.url===previous.pathname+previous.search+previous.hash){location.replace(saved.url);return;}
    }catch{}
    location.assign('/deals');
  };
})();
