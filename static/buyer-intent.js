/* Workspace preferences only. Saving does not make a provider request. */
(()=>{'use strict';
const form=document.querySelector('#discovery-search-form');if(!form)return;
const panel=document.createElement('details');panel.id='buyer-intent';panel.className='buyer-intent';
panel.innerHTML=`<summary>买家用途 <span id="buyer-intent-summary">正在读取</span></summary>
<p>卖方产品：<strong id="buyer-seller-product"></strong></p>
<p class="note">查找使用这些产品的加工企业。工艺词用于构造查询，不能证明采购需求。</p>
<label>目标买家<select id="buyer-target"><option value="terminal_processor">使用产品的加工企业</option><option value="as_entered">按原输入寻找企业</option></select></label>
<label>用途参考<select id="buyer-profile"><option value="">按卖方产品自动匹配</option><option value="gear_hobbing_workholding">滚齿加工</option><option value="gear_workholding">齿轮加工 / 磨齿</option><option value="machining_center_workholding">CNC 加工服务</option></select></label>
<label>买家加工工艺 / 用途<textarea id="buyer-processes" rows="2" maxlength="260" placeholder="其他行业可自填，每行一项，最多4项。例如 metal finishing services"></textarea></label>
<p class="note">自填工艺词优先；留空时按用途参考匹配。未知行业请自填。生成计划后可核对实际查询。</p>
<div class="actions"><button id="buyer-save" type="button" disabled>保存用途</button><button id="buyer-cancel" type="button" class="secondary" disabled>取消修改</button></div>
<p id="buyer-status" role="status" aria-live="polite">正在读取用途设置</p>`;
form.after(panel);
const $=s=>panel.querySelector(s);let state=null,csrf='',dirty=false,busy=false;
function controls(){for(const e of panel.querySelectorAll('input,select,textarea,button'))e.disabled=busy||!state;$('#buyer-cancel').disabled=busy||!dirty;}
function seller(){$('#buyer-seller-product').textContent=document.querySelector('#discovery-products').value.trim()||'尚未填写';}
function paint(){const c=state.config;$('#buyer-target').value=c.target;$('#buyer-profile').value=c.profile||'';$('#buyer-processes').value=(c.process_terms||[]).join('\n');dirty=false;$('#buyer-intent-summary').textContent=c.target==='as_entered'?'按原输入':c.process_terms?.length?c.process_terms.join(' / '):c.profile?$('#buyer-profile').selectedOptions[0].textContent:'自动匹配加工用途';seller();controls();}
async function api(data){const r=await fetch('/api/discovery/buyer-settings',{cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)}:{})});const v=await r.json();if(!r.ok)throw Error(v.error||'用途设置暂时不可用');return v;}
panel.addEventListener('input',()=>{dirty=true;$('#buyer-status').textContent='有未保存的修改';controls();});
$('#buyer-cancel').onclick=()=>{paint();$('#buyer-status').textContent='已取消修改，保留已保存用途';};
$('#buyer-save').onclick=async()=>{try{const terms=$('#buyer-processes').value.split(/\n/).map(v=>v.trim()).filter(Boolean);if(terms.length>4||terms.some(v=>v.length<2||v.length>64))throw Error('最多4项工艺，每项2–64个字符');busy=true;controls();const config={target:$('#buyer-target').value,process_terms:terms};if($('#buyer-profile').value)config.profile=$('#buyer-profile').value;state=await api({config,revision:state.revision});paint();$('#buyer-status').textContent='用途已保存到当前工作区；未发起搜索';}catch(e){$('#buyer-status').textContent=e.message;}finally{busy=false;controls();}};
form.addEventListener('submit',e=>{if(dirty||!state||busy){e.preventDefault();e.stopImmediatePropagation();panel.open=true;$('#buyer-status').textContent=dirty?'请先保存或取消用途修改':'用途设置尚未就绪，请稍后重试';$('#buyer-save').focus();}},true);
document.querySelector('#discovery-products').addEventListener('input',seller);
window.buyerIntentReady=(async()=>{try{csrf=(await(await fetch('/api/meta',{cache:'no-store'})).json()).csrf;state=await api();paint();$('#buyer-status').textContent=state.saved?'已读取工作区保存的用途':'尚未单独保存，使用自动匹配';}catch(e){$('#buyer-status').textContent=e.message;throw e;}})();
window.buyerIntentReady.catch(()=>{});
})();
