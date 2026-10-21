(()=>{'use strict';
const $=s=>document.querySelector(s),icon=n=>'<img class="orb-icon" src="/assets/orbital/icon-'+n+'.svg" alt="" width="22" height="22">';
// The approved orbital structure is present in HTML before the first paint.
const products=$('#discovery-products'),countries=$('#discovery-countries'),countriesLabel=countries.closest('label'),detail=$('#candidate-detail');
$('.clear-product').onclick=()=>{products.value='';products.dispatchEvent(new Event('input',{bubbles:true}));products.focus();};
$('.detail-close')?.addEventListener('click',()=>{detail.hidden=true;});
let data=[],selected=null,active=0,globe=null,latestCandidates=[];const trigger=$('#country-trigger'),popover=$('#country-popover'),query=$('#country-query'),options=$('#country-options');
function close(returnFocus=false){if(popover.matches(':popover-open'))popover.hidePopover();popover.hidden=true;trigger.setAttribute('aria-expanded','false');query.setAttribute('aria-expanded','false');if(returnFocus)trigger.focus();}
function positionCountryPopover(){
 if(popover.hidden)return;
 const anchor=trigger.getBoundingClientRect(),gap=12;
 const sidebar=document.querySelector('.orbital-rail').getBoundingClientRect().right;
 const topbar=document.querySelector('.coast-topbar').getBoundingClientRect().bottom;
 if(anchor.bottom<=topbar||anchor.top>=innerHeight){close();return;}
 const width=Math.min(320,innerWidth-sidebar-gap*2);
 const left=Math.max(sidebar+gap,Math.min(anchor.right-width,innerWidth-width-gap));
 const below=innerHeight-anchor.bottom-gap,above=anchor.top-topbar-gap;
 const upward=below<220&&above>below,maxHeight=Math.min(360,Math.max(120,upward?above:below));
 popover.style.width=width+'px';popover.style.left=left+'px';popover.style.maxHeight=maxHeight+'px';
 options.style.maxHeight=Math.max(60,maxHeight-106)+'px';
 const height=popover.getBoundingClientRect().height;
 popover.style.top=Math.max(topbar+gap,Math.min(upward?anchor.top-gap-height:anchor.bottom+gap,innerHeight-height-gap))+'px';
}
function open(){if(trigger.disabled)return;popover.hidden=false;popover.showPopover();trigger.setAttribute('aria-expanded','true');query.setAttribute('aria-expanded','true');query.value='';active=0;render();positionCountryPopover();query.focus();}
function matches(){const q=query.value.trim().toLocaleLowerCase();return data.filter(c=>[c.name,c.english,c.code,...c.aliases].some(v=>String(v).toLocaleLowerCase().includes(q)));}
function choose(c){selected=c;countries.value=c.name;countries.dispatchEvent(new Event('input',{bubbles:true}));$('#country-current').textContent=c.name;close(true);globe?.focus(c);document.dispatchEvent(new CustomEvent('overseas:country',{detail:{code:c.code,name:c.name}}));}
function render(){const rows=matches();options.replaceChildren();$('#country-empty').hidden=rows.length>0;$('#country-footer').textContent='全部国家与地区 · '+data.length+' 项';rows.forEach((c,i)=>{const b=document.createElement('button');b.type='button';b.className='country-option';b.id='country-option-'+c.code;b.setAttribute('role','option');b.setAttribute('aria-selected',String(c.code===selected?.code));b.tabIndex=i===active?0:-1;b.dataset.code=c.code;const name=document.createElement('span');name.className='country-name';if(c.code===selected?.code)name.innerHTML=icon('map-pin');name.append(document.createTextNode(c.name));b.append(name);if(c.code===selected?.code)b.insertAdjacentHTML('beforeend',icon('check'));b.onclick=()=>choose(c);b.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(c);}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=(i+(e.key==='ArrowDown'?1:-1)+rows.length)%rows.length;render();options.children[active]?.focus();}else if(e.key==='Home'||e.key==='End'){e.preventDefault();active=e.key==='Home'?0:rows.length-1;render();options.children[active]?.focus();}};options.append(b);});}
trigger.onclick=()=>popover.hidden?open():close();trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();open();if(e.key==='ArrowUp')options.lastElementChild?.focus();}};
query.oninput=()=>{active=0;render();positionCountryPopover();};
query.onkeydown=e=>{if(['ArrowDown','ArrowUp','Enter'].includes(e.key)){e.preventDefault();if(e.key==='Enter'){const c=matches()[0];if(c)choose(c);}else (e.key==='ArrowDown'?options.firstElementChild:options.lastElementChild)?.focus();}};
window.addEventListener('resize',positionCountryPopover);window.addEventListener('scroll',positionCountryPopover,{capture:true,passive:true});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!popover.hidden){e.preventDefault();close(true);}if(e.key==='Tab'&&!popover.hidden)setTimeout(()=>{if(!$('#country-picker').contains(document.activeElement))close();},0);});document.addEventListener('pointerdown',e=>{if(!$('#country-picker').contains(e.target))close();});
new MutationObserver(()=>{trigger.disabled=countries.disabled;query.disabled=countries.disabled;if(countries.disabled)close();}).observe(countries,{attributes:true,attributeFilter:['disabled']});
document.addEventListener('overseas:candidates',e=>{latestCandidates=e.detail;globe?.setCandidates(e.detail);});document.addEventListener('overseas:candidate-selected',e=>{if(detail)detail.hidden=false;globe?.selectCandidate(e.detail);});
fetch('/assets/orbital/countries.json').then(r=>{if(!r.ok)throw Error('国家数据暂不可用');return r.json();}).then(async rows=>{data=rows;countriesLabel.hidden=true;trigger.disabled=countries.disabled;selected=data.find(x=>x.code==='CN');render();window.overseasCountries={all:data,get selected(){return selected;},choose};const module=await import('/assets/orbital/orbital-globe.js');globe=await module.createGlobe({countries:data,initial:selected,onCandidate:id=>document.querySelector('[data-candidate-id="'+CSS.escape(id)+'"]')?.click()});globe.setCandidates(latestCandidates);window.overseasGlobe=globe;}).catch(e=>{$('#earth-state').textContent='地球暂不可用；客户检索与国家选择可继续使用。';if(!data.length){close();document.querySelector("#country-picker").hidden=true;countriesLabel.hidden=false;countries.setAttribute('aria-label','目标国家或地区（手动填写）');document.querySelector("#earth-state").textContent='国家数据加载失败，请在搜索范围中手动填写国家；刷新页面可重试。';}console.warn(e.message);});
})();
