'use strict';
(()=>{
if(!['/research','/research/results'].includes(location.pathname))return;
const host=document.createElement('details');host.id='search-connection';host.className='panel search-connection';
host.innerHTML=`<summary>搜索连接设置</summary>
<form id="search-settings-form" autocomplete="off"><div class="search-connection-grid">
<label>搜索供应商<select id="search-provider"><option value="deepseek">DeepSeek（推荐）</option><option value="aliyun">阿里云 CleverSee</option><option value="tavily">Tavily</option></select></label>
<label id="search-tier-label">阿里云范围与档位<select id="search-tier"><option value="">请选择档位</option><option value="CNLiteBasic">国内 · 极速版 · 8元/千次</option><option value="CNAuto">国内 · 智能版 · 18元/千次</option><option value="GlobalAdvanced">全球 · 深度检索 · 56元/千次</option></select></label>
<label class="search-key-field"><span id="search-key-label">搜索 API Key</span><input id="search-api-key" type="password" autocomplete="new-password" maxlength="4096" spellcheck="false" placeholder="自行填写；不会回显已保存的 Key"></label></div>
<label id="deepseek-key-consent-label" class="inline"><input id="deepseek-key-consent" type="checkbox">同意此 Key 用于 DeepSeek Search/Fetch，仅本次运行保存；不自动读取模型 Key。</label>
<p id="search-price-note"></p><label id="search-persist-label" class="inline" hidden><input id="search-persist-key" type="checkbox">在本机加密保存（仅当前 Windows 用户）</label>
<div class="actions"><button id="search-save-key" type="submit">保存搜索连接</button><button id="search-restore-key" type="button" class="secondary" disabled>恢复此供应商密钥</button><button id="search-clear-key" type="button" class="secondary" disabled>清除此供应商密钥</button><button id="search-reload-key" type="button" class="secondary">刷新状态</button></div></form>
<p id="search-key-status" role="status" aria-live="polite">正在读取连接状态…</p>
<details id="search-billing-details"><summary>计费、用途与限制</summary><p>只发送明确确认的公开条件或 URL，不自动重试或切换供应商。DeepSeek 完整查询限128个 Unicode 字符，count 为1–50，业务默认10；查询拓展不使用 offset 分页。网页条数不等于企业数，海外覆盖尚未验证。</p><p>DeepSeek Search 与 Fetch 各5元/1000次成功非空请求；失败/空结果不计费，count 不改价，模型 token 另计。两项工具各账号分别5 QPS，Fetch 另有域名限速。客户端超时的服务端最终结果可能未知。11月1日收费说明仅涉及此前 Harness/Claude Code/Codex 服务端工具调用；独立 API 按公开价格估算，以供应商账单为准。</p><div id="search-usage" aria-live="polite"></div><a id="search-doc-link" href="https://api-docs.deepseek.com/zh-cn/guides/search/" target="_blank" rel="noopener noreferrer">官方协议与计费说明</a></details>
<details id="deepseek-fetch-panel"><summary>网页抓取（DeepSeek Fetch）</summary><form id="deepseek-fetch-form"><label>公开网页地址<input id="deepseek-fetch-url" type="url" maxlength="8192" required placeholder="https://公开网站/页面"></label><label class="inline"><input id="deepseek-fetch-consent" type="checkbox">将此 URL 发给 DeepSeek Fetch；成功非空约0.005元，token另计。勿填带账号、密钥或私人资料的地址。</label><button id="deepseek-fetch-button" type="submit" disabled>抓取这一页</button></form><p id="deepseek-fetch-status" role="status" aria-live="polite"></p><p id="deepseek-fetch-source"></p><pre id="deepseek-fetch-content" class="tool-evidence" tabindex="0"></pre></details>`;
const main=document.querySelector('main'),pool=main.querySelector('.candidate-pool');if(pool)pool.before(host);else main.append(host);
const bar=document.createElement('div');bar.id='search-current';bar.className='search-current';bar.tabIndex=-1;bar.setAttribute('role','status');bar.setAttribute('aria-live','polite');
bar.innerHTML='<div><strong id="current-search-provider">当前搜索：读取中</strong><span id="current-search-status"></span><small id="current-search-price"></small><p id="search-save-feedback"></p></div><button id="open-search-config" type="button" class="secondary" aria-controls="search-connection">更改</button>';
function place(){const form=document.querySelector('#discovery-search-form');if(document.body.classList.contains('results-page'))document.querySelector('.page-content').prepend(bar);else form.prepend(bar);}
place();
function revealIfCovered(){
 const r=bar.getBoundingClientRect(),edge=document.querySelector('.coast-topbar')?.getBoundingClientRect().bottom||0;
 if(!host.open&&r.height&&r.bottom>0&&r.top<edge+8)bar.scrollIntoView({behavior:'instant',block:'center'});
}
window.addEventListener('pageshow',()=>setTimeout(revealIfCovered,0));
const $=s=>host.querySelector(s);let state=null,csrf='',busy=false,fetching=false,dirty=false;
function open(){host.open=true;host.scrollIntoView({behavior:'instant',block:'nearest'});$('#search-provider').focus();}
bar.querySelector('#open-search-config').onclick=open;
function controls(){
 for(const e of host.querySelectorAll('#search-settings-form button,#search-settings-form input,#search-settings-form select'))e.disabled=busy||!state;
 $('#search-persist-label').hidden=!state?.credential_persistence_available;$('#search-restore-key').hidden=state?.provider==='deepseek';$('#search-restore-key').disabled=busy||!state?.credential_saved_encrypted;
 $('#search-clear-key').disabled=busy||(!state?.key_present&&!state?.configured&&!state?.credential_saved_encrypted);$('#search-reload-key').disabled=busy;
 $('#deepseek-fetch-button').disabled=busy||fetching||!state?.configured||state?.provider!=='deepseek'||!$('#deepseek-fetch-consent').checked;$('#deepseek-fetch-url').disabled=fetching;$('#deepseek-fetch-consent').disabled=fetching;
}
async function api(data,path='/api/search/settings'){
 const response=await fetch(path,{cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)}:{})});const value=await response.json();if(!response.ok)throw value;return value;
}
function usage(){
 const u=state?.usage,area=$('#search-usage');area.replaceChildren();if(!u)return;
 const note=document.createElement('p');note.textContent='本次运行共尝试 '+u.requests_attempted+' 次；以下是本机观察估算，不是账单，模型 token 另计。';area.append(note);
 for(const [tool,v]of Object.entries(u.deepseek_tools||{})){const p=document.createElement('p');const min=v.estimated_cost_min_cny??v.estimated_cost_cny,max=v.estimated_cost_max_cny??v.attempt_upper_bound_cny;p.textContent='DeepSeek '+(tool==='search'?'Search':'Fetch')+'：调用 '+v.attempts+'；服务商 HTTP 200 '+(v.provider_http_200??'未记录')+'，非空 '+(v.provider_nonempty??v.successful_nonempty)+'；本地可用 '+(v.business_usable??'未记录')+'，正文不足 '+(v.business_insufficient??0)+'，全部过滤 '+(v.business_filtered_empty??0)+'，解析失败 '+(v.business_parse_failed??0)+'；费用估算 '+min.toFixed(3)+'–'+max.toFixed(3)+' 元（未知部分保留区间，非账单）。';area.append(p);}
 const boundary=document.createElement('p');boundary.textContent='本地失败不等于服务商未计费；HTTP 200 非空响应按可能计费记录。服务商结果未知时保留费用区间，正文不足不自动重试。';area.append(boundary);
 const p=document.createElement('p');p.textContent='阿里云按尝试次数估算；Tavily 按账号套餐。最终以供应商账单为准。';area.append(p);
}
function render(){
 $('#search-provider').value=state.provider;$('#search-tier').value=state.engine_type||'';$('#search-tier-label').hidden=state.provider!=='aliyun';$('#search-key-label').textContent=state.provider_label+' API Key';
 const deep=state.provider==='deepseek';$('#deepseek-key-consent-label').hidden=!deep;$('#deepseek-fetch-panel').hidden=!deep;
 $('#search-price-note').textContent=deep?'Search/Fetch 每次成功非空约0.005元，模型 token 另计；每次调用需单独确认。':state.provider==='aliyun'?(state.price_cny_per_request==null?'请选择阿里云档位；保存不会发起搜索。':'每次约'+state.price_cny_per_request.toFixed(3)+'元；每次调用需单独确认。'):'按 Tavily 账号套餐计费；每次调用需单独确认。';
 const label=state.provider==='deepseek'?'DeepSeek':state.provider_label||state.provider;
 const status=state.configured?(state.connection_validation?.state==='verified'?'本次会话实测连接成功':'会话 Key 已保存 · 尚未验证连接'):state.key_present?'Key 已保存 · 请补齐档位':'未配置';
 bar.querySelector('#current-search-provider').textContent='当前搜索：'+label;bar.querySelector('#current-search-status').textContent=status;bar.dataset.state=state.configured?'configured':'not-configured';
 const feedback=bar.querySelector('#search-save-feedback');
 if(!dirty&&!feedback.dataset.error)feedback.textContent=state.configured?'已保存（本次会话）':'';
 bar.querySelector('#current-search-price').textContent=deep?'每次成功非空约0.005元，token另计；仅在确认后调用。':state.provider==='aliyun'&&state.price_cny_per_request!=null?'每次约'+state.price_cny_per_request.toFixed(3)+'元；仅在确认后调用。':'按供应商套餐计费；仅在确认后调用。';
 $('#search-key-status').textContent=status;$('#search-doc-link').href=deep?'https://api-docs.deepseek.com/zh-cn/guides/search/':state.provider==='aliyun'?'https://help.aliyun.com/zh/document_detail/3061214.html':'https://docs.tavily.com/documentation/api-credits';
 usage();controls();window.dispatchEvent(new CustomEvent('overseas-search-config-changed',{detail:{...state,message:status}}));
 setTimeout(revealIfCovered,0);
}
async function load(){if(busy)return;busy=true;controls();try{const [meta,value]=await Promise.all([fetch('/api/meta',{cache:'no-store'}).then(r=>r.json()),api()]);csrf=meta.csrf;state=value;render();}catch(e){$('#search-key-status').textContent=e.error||'连接状态读取失败，请刷新。';bar.querySelector('#current-search-status').textContent='状态读取失败';}finally{busy=false;controls();}}
async function save(data,collapse=false){
 if(busy||!state)return;busy=true;controls();try{
  state=await api({...data,revision:state.revision});$('#search-api-key').value='';$('#search-persist-key').checked=false;$('#deepseek-key-consent').checked=false;dirty=false;render();
  const feedback=bar.querySelector('#search-save-feedback');delete feedback.dataset.error;feedback.textContent=state.configured?'保存成功 · 本次会话已配置':'设置已保存 · 当前连接未配置';
  if(collapse){host.open=false;bar.scrollIntoView({behavior:'instant',block:'center'});bar.focus({preventScroll:true});}
 }catch(e){host.open=true;$('#search-key-status').textContent=(e.error||'保存失败')+'；输入已保留。';const feedback=bar.querySelector('#search-save-feedback');feedback.dataset.error='true';feedback.textContent='保存失败，仍使用原连接';$('#search-provider').value=state.provider;$('#search-tier').value=state.engine_type||'';}
 finally{delete data.api_key;busy=false;controls();}
}
$('#search-provider').onchange=()=>{const provider=$('#search-provider').value;$('#search-api-key').value='';$('#search-persist-key').checked=false;$('#deepseek-key-consent').checked=false;dirty=false;$('#deepseek-fetch-content').textContent='';$('#deepseek-fetch-source').textContent='';$('#deepseek-fetch-consent').checked=false;save({provider});};
$('#search-tier').onchange=()=>save({provider:'aliyun',engine_type:$('#search-tier').value});$('#search-api-key').oninput=()=>{dirty=Boolean($('#search-api-key').value);bar.querySelector('#search-save-feedback').textContent='';};
$('#search-settings-form').onsubmit=e=>{e.preventDefault();if(!state)return;const data={provider:state.provider,persist:$('#search-persist-key').checked};if($('#search-api-key').value.trim())data.api_key=$('#search-api-key').value.trim();if(state.provider==='deepseek'&&data.api_key){if(!$('#deepseek-key-consent').checked){$('#search-key-status').textContent='请先同意把此 Key 用于 Search/Fetch。';return;}data.consent=true;}save(data,true);};
$('#search-restore-key').onclick=()=>save({provider:state.provider,restore_key:true,consent:true},true);$('#search-clear-key').onclick=()=>save({provider:state.provider,clear_key:true});$('#search-reload-key').onclick=load;$('#deepseek-fetch-consent').onchange=controls;
$('#deepseek-fetch-form').onsubmit=async e=>{
 e.preventDefault();if(fetching||busy||!state?.configured||state.provider!=='deepseek'||!$('#deepseek-fetch-consent').checked)return;
 fetching=true;controls();$('#deepseek-fetch-status').textContent='正在抓取这一页；不会自动重试…';$('#deepseek-fetch-content').textContent='';$('#deepseek-fetch-source').textContent='';
 try{const v=await api({url:$('#deepseek-fetch-url').value.trim(),consent:true},'/api/search/fetch');$('#deepseek-fetch-content').textContent=v.content;$('#deepseek-fetch-source').textContent=v.source_url+' · '+v.retrieved_at;const quality=v.content_quality;$('#deepseek-fetch-status').textContent=(quality?.message||'已取得页面片段，尚未完成背景调查')+' · '+v.title+' · '+(quality?.character_count??v.content.length)+' 字符。'+(quality?.next_action||'可手动选择其他公开页面，需重新确认费用。');$('#deepseek-fetch-status').dataset.quality=quality?.state||'unverified';}
 catch(error){$('#deepseek-fetch-status').textContent=error.error||'抓取未完成，没有自动重试。';}
 finally{fetching=false;$('#deepseek-fetch-consent').checked=false;await load();controls();}
};
host.addEventListener('toggle',()=>{if(host.open&&!dirty)load();});window.addEventListener('overseas-search-completed',()=>{if(!dirty&&!fetching)load();});window.addEventListener('focus',()=>{if(!dirty&&!fetching)load();});window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});controls();load();
})();
