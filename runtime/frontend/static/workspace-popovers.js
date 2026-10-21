(()=>{'use strict';
const entries=[...document.querySelectorAll('.more-tools,#more-filters')].map(details=>({details,trigger:details.querySelector('summary'),panel:details.querySelector(':scope>div')})).filter(x=>x.trigger&&x.panel);
function close(entry,focus=false){if(entry.panel.matches(':popover-open'))entry.panel.hidePopover();entry.details.open=false;entry.trigger.setAttribute('aria-expanded','false');if(focus)entry.trigger.focus();}
function place(entry){
if(!entry.details.open||!entry.panel.matches(':popover-open'))return;
const anchor=entry.trigger.getBoundingClientRect(),rail=document.querySelector('.workspace-sidebar,.orbital-rail')?.getBoundingClientRect().right||0,header=document.querySelector('.coast-topbar')?.getBoundingClientRect().bottom||0,gap=12;
if(anchor.bottom<=header||anchor.top>=innerHeight){close(entry);return;}
const width=Math.min(entry.details.id==='more-filters'?280:260,innerWidth-rail-gap*2),below=innerHeight-anchor.bottom-gap,above=anchor.top-header-gap,up=below<220&&above>below;
Object.assign(entry.panel.style,{width:width+'px',left:Math.max(rail+gap,Math.min(anchor.right-width,innerWidth-width-gap))+'px',maxHeight:Math.max(120,(up?above:below)-gap)+'px'});
const height=entry.panel.getBoundingClientRect().height;entry.panel.style.top=Math.max(header+gap,Math.min(up?anchor.top-gap-height:anchor.bottom+gap,innerHeight-height-gap))+'px';
}
for(const entry of entries){
entry.panel.setAttribute('popover','manual');entry.panel.classList.add('workspace-menu-popover');entry.trigger.setAttribute('aria-expanded',String(entry.details.open));
entry.details.addEventListener('toggle',()=>{if(entry.details.open){for(const other of entries)if(other!==entry)close(other);entry.panel.showPopover();entry.trigger.setAttribute('aria-expanded','true');place(entry);}else close(entry);});
entry.details.addEventListener('focusout',()=>setTimeout(()=>{if(!entry.details.contains(document.activeElement))close(entry);},0));
}
function outside(e){for(const entry of entries)if(entry.details.open&&!entry.details.contains(e.target))close(entry);}
document.addEventListener('pointerdown',outside,true);
document.addEventListener('keydown',e=>{if(e.key==='Escape')for(const entry of entries)if(entry.details.open){e.preventDefault();close(entry,true);}});
window.addEventListener('resize',()=>entries.forEach(place));window.addEventListener('scroll',()=>entries.forEach(place),{capture:true,passive:true});
})();
