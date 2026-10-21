(()=>{'use strict';
// Preserve saved-customer bookmarks on the operational results page. No fetch intent.
const savedCustomer=new URL(location.href).searchParams.get('customer_id');
if(savedCustomer){const target=new URL('/research/results',location.origin);target.searchParams.set('customer_id',savedCustomer);location.replace(target.href);return;}

const $=s=>document.querySelector(s),key='overseas.discovery.view.'+location.origin,intentKey='overseas.discovery.intent.'+location.origin;
let navigating=false,searchConfigured=false;let saved={};try{saved=JSON.parse(sessionStorage.getItem(key)||'{}')}catch{}
const conditions=()=>({products:$('#discovery-products').value.trim(),countries:$('#discovery-countries').value.trim(),type:$('#discovery-type').value,count:Number($('#search-result-count').value),need_content:$('#search-need-content').checked,ori_question:$('#search-original-question').value.trim()});
const remember=()=>{saved.conditions=conditions();sessionStorage.setItem(key,JSON.stringify(saved))};
if(document.body.dataset.orbitalHome!=='static'){
for(const s of ['.orbital-workflow','#candidate-detail','#candidate-pagination','#search-plan-options','#candidate-batch-summary'])$(s)?.remove();
$('.orbital-advanced summary').textContent='买家条件';$('#search-message').textContent='点击查找后将这些公开条件发送所选搜索供应商，执行一次查询；不自动入库。';$('#search-state').hidden=true;$('#search-consent').closest('label').hidden=true;
const consentNote=document.createElement('p');consentNote.className='home-search-note';consentNote.textContent='点击后发送产品、市场等公开条件，执行一次所选供应商查询。';$('#hero-form-slot').append(consentNote);
const old=$('#candidate-refresh'),more=document.createElement('a');more.id='candidate-more';more.href='/research/results';more.className='button secondary';more.textContent='查看更多';old.replaceWith(more);more.onclick=()=>remember();
const note=document.createElement('p');note.className='home-results-note';note.setAttribute('aria-live','polite');$('.candidate-pool').append(note);

}
const more=$('#candidate-more'),note=$('.home-results-note');more.onclick=()=>remember();
$('#discovery-products').value=saved.conditions?.products||'';
$('#search-result-count').value=String(saved.conditions?.count||20);$('#search-need-content').checked=saved.conditions?.need_content===true;$('#search-original-question').value=saved.conditions?.ori_question||'';for(const id of ['discovery-products','discovery-countries','discovery-type','search-result-count','search-need-content','search-original-question'])$('#'+id).addEventListener('input',remember);
$('#discovery-search').disabled=!searchConfigured;$('#discovery-search').textContent='查找客户';$('#discovery-search').setAttribute('aria-label','查找客户：执行一次公开查询');
$('#discovery-search-form').onsubmit=e=>{e.preventDefault();if(navigating)return;if(!$('#discovery-products').value.trim()){note.textContent='请先填写产品或应用。';$('#discovery-products').focus();return;}navigating=true;$('#discovery-search').disabled=true;remember();const id=crypto.randomUUID();sessionStorage.setItem(intentKey,JSON.stringify({id,expires:Date.now()+120000,conditions:conditions()}));location.assign('/research/results?intent='+encodeURIComponent(id));};
async function read(){try{const response=await fetch('/api/discovery/results?page=1&page_size=4');if(!response.ok)throw Error('候选列表暂不可用');const data=await response.json(),host=$('#candidate-list');host.replaceChildren();$('#candidate-count').textContent=String(data.total_all);note.textContent=data.total_all?'首页展示 '+data.items.length+' 项，共 '+data.total_all+' 项已有候选。':'暂无已有候选；可填写条件查找，或在完整结果页分析已知企业。';
for(const [i,c] of data.items.entries()){const a=document.createElement('a');a.href='/research/results?candidate_id='+encodeURIComponent(c.candidate_id);a.className='candidate-row';a.dataset.candidateId=c.candidate_id;const text=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;n.className=cls||'';return n};const title=text('strong',c.suggested_name||c.name||'名称待核实','candidate-name');title.append(text('small',c.country?c.country+' · 国家级':'位置未知'));a.append(text('span',String(i+1).padStart(2,'0'),'candidate-ordinal'),text('span',(c.suggested_name||'?')[0],'candidate-monogram'),title,text('span',c.state==='draft'?'已暂存':'待核对','candidate-status'),text('span','查看证据 →','candidate-view'));a.onclick=()=>{remember();saved.candidate_id=c.candidate_id;sessionStorage.setItem(key,JSON.stringify(saved))};host.append(a)}document.dispatchEvent(new CustomEvent('overseas:candidates',{detail:data.items}));
}catch(e){note.textContent=e.message+'；不会自动搜索。'}}
window.addEventListener('overseas-search-config-changed',e=>{searchConfigured=e.detail.configured===true;$('#discovery-search').disabled=!searchConfigured;$('#search-message').textContent=e.detail.message;});
fetch('/api/discovery/meta').then(r=>r.json()).then(meta=>{const select=$('#discovery-type');select.replaceChildren(new Option('不限买家类型',''));for(const v of meta.types||[])select.append(new Option(v,v));select.value=saved.conditions?.type||'';searchConfigured=meta.search?.configured===true;$('#discovery-search').disabled=!searchConfigured;if(!searchConfigured)$('#search-message').textContent=meta.search?.message||'请先配置搜索供应商、范围与独立 Key；模型 Key 不用于搜索。';}).catch(()=>{$('#search-message').textContent='连接状态暂不可用；没有发起搜索。'});
if(saved.conditions?.countries){const restore=()=>{if(!window.overseasCountries){setTimeout(restore,100);return;}const c=window.overseasCountries.all.find(c=>c.name===saved.conditions.countries||c.code===saved.conditions.countries);if(c)window.overseasCountries.choose(c);else $('#discovery-countries').value=saved.conditions.countries};restore()}
window.addEventListener('pageshow',()=>{navigating=false;$('#discovery-search').disabled=!searchConfigured;read()});window.addEventListener('pagehide',remember);read();

// Keep the native details control; dismiss the home conditions without submitting.
const buyerConditions = $('.orbital-advanced');
const buyerSummary = buyerConditions?.querySelector('summary');
function closeBuyerConditions(returnFocus = false) {
  if (!buyerConditions?.open) return;
  buyerConditions.open = false;
  if (returnFocus) buyerSummary.focus();
}
document.addEventListener('pointerdown', event => {
  if (buyerConditions?.open && !buyerConditions.contains(event.target)) closeBuyerConditions();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.defaultPrevented && buyerConditions?.open) {
    event.preventDefault();
    closeBuyerConditions(true);
  }
});
$('#discovery-type').addEventListener('change', () => closeBuyerConditions(true));

})();
