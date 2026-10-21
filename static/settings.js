'use strict';
(() => {
  if(['/settings','/settings.html'].includes(location.pathname)){location.replace('/research?model=open');return;}
  if(!document.querySelector('#model-entry'))return;
  const $=s=>document.querySelector(s);
  const CURRENT='__current', CUSTOM='__custom';
  const errors={catalog_changed:'预置目录已更新，请刷新后重新选择。你的非秘密输入仍保留。',catalog_option_invalid:'所选型号已不在当前目录，请刷新后重新选择。',provider_auth:'认证失败（401），请核对是否使用了所选平台的 Key。',provider_forbidden:'平台拒绝访问（403），请检查账号权限、区域或策略限制。',model_not_found:'该型号未开放或不可用（404），请检查账号模型权限。',models_unsupported:'该平台不支持此认证检查方式。可另行确认合成生成测试，型号不会自动更换。',provider_rate_limit:'平台限流或额度限制（429），请稍后重试并检查账户额度。',provider_timeout:'平台响应超时，请稍后重试。',profile_changed:'连接已被另一页面修改，请刷新并核对后重试。',key_required:'当前服务没有 Key，重启后需重新填写。',model_required:'请先选择并保存模型。',consent_required:'请先确认本次调用与可能费用。',endpoint_invalid:'自定义地址格式不正确，请使用支持的 HTTPS 基础地址。',unsafe_endpoint:'只允许支持的 HTTPS 公网供应商地址。',key_persistence_unavailable:'当前环境不能使用 Windows 用户级加密；没有保存明文。',credential_storage_failed:'本机加密存储失败，请保留原密钥自行重试。',credential_restore_failed:'无法恢复此 Windows 用户和连接范围的密钥，请重新填写。',invalid_response:'平台返回格式不符合支持的文本协议。',provider_unavailable:'平台服务暂不可用，请稍后重试。',redirect_blocked:'已阻止平台重定向，请核对实际地址。'};
  let meta=null,saved=null,catalog=null,choice='',epoch=0,running=null,serial=0,testStateStale=false;
  const item=()=>catalog?.items.find(x=>x.option_id===choice);
  const customActive=()=>choice===CUSTOM||(choice===CURRENT&&saved?.provider==='custom');
  function selection(){const selected=item();if(selected)return {provider:selected.provider,base_url:selected.base_url,model:selected.model,label:selected.label,key_hint:selected.key_hint,source_url:selected.source_url,provider_label:selected.provider_label};if(customActive())return {provider:'custom',base_url:$('#custom-url').value.trim(),model:$('#custom-model').value.trim(),label:'自定义兼容模型',key_hint:'需要这个自定义平台对应的 API Key',provider_label:'自定义平台'};if(choice===CURRENT&&saved)return {...saved,label:`当前已保存：${saved.model||'未指定型号'}`,key_hint:'需要当前已保存平台对应的 API Key',provider_label:saved.provider};return null;}
  function sameEndpoint(a,b){if(!a||!b)return false;try{const normalize=s=>{const url=new URL(s);let p=url.pathname.replace(/\/+$/,'');if(url.hostname==='api.deepseek.com'&&p==='/v1')p='';return `${url.protocol}//${url.host}${p}`;};return a.provider===b.provider&&normalize(a.base_url)===normalize(b.base_url);}catch(_){return false;}}
  function dirty(){const selected=selection();return !saved||!selected||!sameEndpoint(selected,saved)||selected.model!==saved.model||$('#connection-name').value.trim()!==(saved.name||'')||!!$('#api-key').value||$('#persist-key').checked;}
  function message(text,kind=''){const el=$('#settings-feedback');el.textContent=text;el.className='settings-feedback '+kind;el.hidden=false;renderCompact();}
  function errorText(e){return errors[e.code]||({401:errors.provider_auth,403:'本地请求被拒绝（403），请刷新连接后重新保存；此提示不代表平台认证结果。',404:'接口或型号不可用（404），请刷新目录并检查平台权限。',429:errors.provider_rate_limit,504:errors.provider_timeout,409:errors.profile_changed}[e.status])||e.safeMessage||'本次请求失败，请核对当前连接后重试。';}
  async function api(path,data,signal){const controller=new AbortController(),abort=()=>controller.abort();if(signal){if(signal.aborted)controller.abort();else signal.addEventListener('abort',abort,{once:true});}const timer=setTimeout(abort,65000);try{const response=await fetch(path,{cache:'no-store',signal:controller.signal,...(data?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':meta.csrf},body:JSON.stringify(data)}:{})});let value;try{value=await response.json();}catch(_){throw {safeMessage:'本机响应格式异常，请检查服务版本。'};}if(!response.ok)throw {status:response.status,code:value.code};return value;}catch(e){if(e.name==='AbortError')throw {safeMessage:'请求已取消或超时，请核对状态后重试。'};if(e instanceof TypeError)throw {safeMessage:'无法连接本机服务，请确认服务已启动。'};throw e;}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}}
  function checkedTime(value){const date=new Date(value);return value&&!Number.isNaN(date.getTime())?date.toISOString().replace('T',' ').replace(/\.\d{3}Z$/,' UTC'):'时间未记录';}
  function renderTestResults(){if(!saved)return;const changed=dirty()||testStateStale,binding=`已保存配置 v${saved.revision} · ${saved.model||'未指定型号'}`;for(const [id,status,time,title] of [['authentication-result',saved.models_status,saved.models_checked_at,'认证'],['test-result',saved.generation_status,saved.generation_checked_at,'生成']]){const el=$('#'+id);let summary,statusClass='';if(status==='ok'){summary=`历史${title}测试通过 · ${checkedTime(time)}\n详细响应未保留；不代表客户分析全链路已验收。`;statusClass='success';}else if(status==='failed'||status==='unsupported'){summary=`历史${title}测试${status==='unsupported'?'不支持':'失败'} · ${checkedTime(time)}\n详细响应未保留，请核对平台权限或兼容性。`;statusClass='error';}else summary=`当前已保存配置尚未${title==='认证'?'验证认证':'运行生成测试'}。此前配置的结果不适用于此配置。`;el.textContent=changed?`以下记录仅属于此前的${binding}。${testStateStale?'配置已变更，请刷新核对；旧记录不适用于当前配置。':'当前修改尚未验证。'}\n${summary}`:`${binding}\n${summary}`;el.className='check-result'+(!changed&&statusClass?' '+statusClass:'');}}
  function statusText(){if(!saved)return '尚未读取';if(testStateStale)return '配置已变更 · 请刷新';if(!selection()&&!saved.model&&!saved.configured&&!$('#api-key').value&&$('#connection-name').value.trim()===(saved.name||''))return '尚未配置';if(dirty())return '有未保存修改';if(!saved.configured)return '未填写 Key';if(saved.generation_status==='failed')return '生成测试失败 · 可重试';if(saved.models_status==='failed'||saved.models_status==='unsupported')return '认证'+(saved.models_status==='unsupported'?'不支持':'失败')+' · 生成'+(saved.generation_status==='ok'?'曾通过':'未测试');if(saved.generation_status==='ok')return '生成测试通过';if(saved.models_status==='ok')return '认证通过 · 生成未测试';if(saved.last_status==='failed')return '验证失败 · 可重试';return '已保存 · 未验证';}
  function controls(){const active=!!running,locked=active&&['load','save','clear'].includes(running.kind);for(const el of $('#settings-form').elements)el.disabled=!!locked||!saved;$('#custom-model').disabled=!!locked||!saved||!customActive();$('#custom-url').disabled=!!locked||!saved||!customActive();$('#custom-model').required=customActive();$('#custom-url').required=customActive();$('#settings-reload').disabled=active;$('#save-settings').disabled=active||!saved||!selection();$('#remove-key').disabled=active||(!saved?.configured&&!saved?.credential_saved_encrypted)||dirty()||testStateStale;$('#credential-storage').hidden=!saved?.credential_persistence_available;$('#persist-key').disabled=active||!saved?.credential_persistence_available;$('#restore-key').disabled=active||!saved?.credential_saved_encrypted||dirty()||testStateStale;$('#credential-state').textContent=saved?.credential_saved_encrypted?'当前连接有本机加密副本；重启后可明确恢复。':'当前连接尚无本机加密副本。';$('#verify-connection').disabled=active||!saved?.configured||dirty()||testStateStale;$('#test-connection').disabled=active||!saved?.configured||!saved?.model||dirty()||testStateStale||!$('#test-consent').checked;$('#settings-form').setAttribute('aria-busy',String(!!locked));if(locked&&pickerOpen)closePicker();$('#settings-state').textContent=statusText();const selected=selection(),cross=saved&&selected&&!sameEndpoint(selected,saved);$('#key-state').textContent=cross?'新平台 · 重新填 Key':saved?.configured?'Key 在服务端内存':'未配置';$('#key-help').textContent=cross?'已切换平台，请填写这个平台的 Key；旧平台 Key 不会复用。':saved?.configured?(saved.credential_persistence_available?'同一平台可留空保留内存 Key。可选本机加密保存，重启后由你明确恢复。':'同一平台可留空保留已有 Key。仅在服务端内存中，重启后需重新填写。'):(saved?.credential_persistence_available?'默认仅内存；选择本机加密保存后，可在重启后明确恢复。':'仅在服务端内存中保存，重启后需重新填写。');$('#platform-hint').textContent=selected?.key_hint||'请选择后端已核验的模型型号。';$('#destination-label').textContent=selected?.provider_label||'尚未选择模型';$('#destination').textContent=(selected?.base_url||'尚未配置地址')+(saved&&dirty()?'（未保存）':'');$('#destination-model').textContent=selected?`${selected.label} · ${selected.model||'尚未指定型号'}`:'';const link=$('#platform-link');link.hidden=true;if(selected?.source_url){try{const url=new URL(selected.source_url);if(url.protocol==='https:'){link.href=url.href;link.hidden=false;}}catch(_){}}$('#verify-connection').textContent=running?.kind==='verify'?'正在验证…':'验证认证';$('#test-connection').textContent=running?.kind==='test'?'正在测试…':'测试生成';renderCompact();}
  let pickerOpen=false,options=[],activeOption=-1;
  function choiceLabel(){return item()?.label||(choice===CURRENT?`当前已保存：${saved?.model||'未指定型号'}（保留）`:choice===CUSTOM?'高级：自定义兼容模型':'');}
  function pickerOptions(){const entries=(catalog?.items||[]).map(x=>({value:x.option_id,label:x.label,group:x.provider_label||'预置模型',search:`${x.label} ${x.provider_label} ${x.model}`}));if(saved&&(choice===CURRENT||((saved.model||saved.configured)&&!catalog?.items.some(x=>sameEndpoint(x,saved)&&x.model===saved.model))))entries.push({value:CURRENT,label:`当前已保存：${saved.model||'未指定型号'}（保留）`,group:'已有连接',search:`${saved.model} 当前已保存 保留`});entries.push({value:CUSTOM,label:'高级：自定义兼容模型',group:'高级',search:'高级 自定义兼容模型 custom'});return entries;}
  function highlightOption(index){activeOption=index;const nodes=$('#model-options').querySelectorAll('[role=option]');nodes.forEach((node,i)=>node.classList.toggle('highlighted',i===index));if(nodes[index]){$('#model-search').setAttribute('aria-activedescendant',nodes[index].id);nodes[index].scrollIntoView({block:'nearest'});}else $('#model-search').removeAttribute('aria-activedescendant');}
  function renderChoices(){const q=pickerOpen?$('#model-search').value.trim().toLowerCase():'',list=$('#model-options');options=pickerOptions().filter(x=>!q||x.search.toLowerCase().includes(q));list.replaceChildren();let group='';options.forEach((entry,index)=>{if(entry.group!==group){group=entry.group;const heading=document.createElement('div');heading.className='model-option-group';heading.setAttribute('role','presentation');heading.textContent=group;list.append(heading);}const node=document.createElement('div');node.id='model-option-'+index;node.className='model-option';node.setAttribute('role','option');node.setAttribute('aria-selected',String(entry.value===choice));node.dataset.value=entry.value;node.textContent=entry.label;node.onpointerdown=e=>{if(e.pointerType==='mouse')e.preventDefault();};node.onclick=()=>chooseOption(entry.value);list.append(node);});if(!options.length){const empty=document.createElement('div');empty.className='model-option-empty';empty.textContent='未找到匹配型号，请尝试其他关键词';list.append(empty);}$('#model-choice').value=choice;if(!pickerOpen)$('#model-search').value=choiceLabel();list.hidden=!pickerOpen;$('#model-search').setAttribute('aria-expanded',String(pickerOpen));highlightOption(pickerOpen?Math.max(0,options.findIndex(x=>x.value===choice)):-1);$('#model-picker-status').textContent=pickerOpen?`${options.length} 个候选型号`:'';$('#catalog-note').textContent=catalog?`目录核验：${catalog.verified_at||'未注明日期'} · 非实时权限验证`:'预置目录暂不可用，已有连接仍保留。点击刷新重试。';}
  function openPicker(){if(pickerOpen||$('#model-search').disabled)return;pickerOpen=true;$('#model-search').value='';renderChoices();}
  function closePicker(){pickerOpen=false;$('#model-options').hidden=true;$('#model-search').value=choiceLabel();$('#model-search').setAttribute('aria-expanded','false');$('#model-search').removeAttribute('aria-activedescendant');$('#model-picker-status').textContent='';}
  function chooseOption(value){if($('#model-search').disabled)return;const previous=selection(),changed=value!==choice;choice=value;const next=selection();if(changed&&!sameEndpoint(previous,next))$('#api-key').value='';if(choice===CUSTOM)$('#advanced-settings').open=true;closePicker();renderChoices();if(changed)edited();$('#model-search').focus();}
  function fill(){testStateStale=false;const match=catalog?.items.find(x=>x.option_id===saved.option_id)||catalog?.items.find(x=>x.provider===saved.provider&&sameEndpoint(x,saved)&&x.model===saved.model);choice=match?.option_id||(saved.model||saved.configured?CURRENT:'');$('#connection-name').value=saved.name||'';$('#custom-url').value=saved.base_url||'';$('#custom-model').value=saved.model||'';$('#api-key').value='';$('#persist-key').checked=false;$('#test-consent').checked=false;$('#advanced-settings').open=false;closePicker();renderChoices();renderTestResults();controls();}
  function edited(){epoch++;if(running&&['verify','test'].includes(running.kind)){running.controller.abort();running=null;message('选择或 Key 已改变，旧验证结果不会用于当前连接。');}$('#test-consent').checked=false;renderTestResults();controls();}
  async function operation(kind,fn){if(running)return;const ticket={id:++serial,epoch,kind,controller:new AbortController()};running=ticket;controls();try{await fn(ticket);}catch(e){if(ticket.epoch===epoch){if(['verify','test'].includes(kind)){testStateStale=e.code==='profile_changed';try{const latest=await api('/api/model/settings',null,ticket.controller.signal);if(ticket.epoch===epoch&&latest.revision===saved.revision){saved=latest;testStateStale=false;}else if(ticket.epoch===epoch)testStateStale=true;}catch(_){}if(ticket.epoch===epoch)renderTestResults();}if(ticket.epoch===epoch){message(errorText(e),'error');const el=kind==='verify'?$('#authentication-result'):kind==='test'?$('#test-result'):null;if(el){el.textContent+='\n本次请求：'+errorText(e);el.className='check-result error';}}}}finally{if(running?.id===ticket.id)running=null;controls();}}
  async function refresh(initial=false){const preserve=saved&&dirty();await operation('load',async ticket=>{meta=await api('/api/meta',null,ticket.controller.signal);const values=await Promise.allSettled([api('/api/model/settings',null,ticket.controller.signal),api('/api/model/catalog',null,ticket.controller.signal)]);if(ticket.epoch!==epoch)return;if(values[0].status!=='fulfilled')throw values[0].reason;saved=values[0].value;testStateStale=false;catalog=values[1].status==='fulfilled'&&Array.isArray(values[1].value.items)?values[1].value:null;if(!preserve)fill();else{renderChoices();renderTestResults();controls();}message(preserve?'最新配置与目录已读取，未保存输入仍保留，请核对后保存。':!catalog?'预置目录暂不可用。已有配置未改变，可刷新重试。':initial?'选择型号并填写对应平台 Key。保存不会调用模型。':'配置与目录已刷新。');});}
  $('#settings-reload').onclick=()=>{closePicker();refresh();};
  $('#model-search').onfocus=openPicker;$('#model-search').onclick=openPicker;
  $('#model-search').oninput=()=>{pickerOpen=true;renderChoices();};
  $('#model-search').onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(!pickerOpen){openPicker();highlightOption(e.key==='ArrowDown'?0:options.length-1);}else if(options.length)highlightOption((activeOption+(e.key==='ArrowDown'?1:-1)+options.length)%options.length);}else if(e.key==='Enter'){e.preventDefault();if(pickerOpen&&options[activeOption])chooseOption(options[activeOption].value);else openPicker();}else if(e.key==='Escape'){e.preventDefault();closePicker();}else if(e.key==='Tab')closePicker();};
  document.addEventListener('pointerdown',e=>{if(!$('#model-picker').contains(e.target))closePicker();});
  $('#model-picker').addEventListener('focusout',e=>{if(!$('#model-picker').contains(e.relatedTarget))closePicker();});
  $('#custom-url').oninput=()=>{$('#api-key').value='';edited();};for(const id of ['connection-name','custom-model','api-key'])$('#'+id).addEventListener('input',edited);$('#test-consent').onchange=controls;$('#persist-key').onchange=edited;
  function saveBody(){const selected=item();if(selected)return {option_id:selected.option_id,catalog_version:catalog.version,revision:saved.revision,persist:$('#persist-key').checked,...(saved.name!==$('#connection-name').value.trim()?{name:$('#connection-name').value.trim()}:{})};const current=selection();return {provider:current.provider,base_url:current.base_url,model:current.model,name:$('#connection-name').value.trim(),revision:saved.revision,persist:$('#persist-key').checked};}
  $('#settings-form').onsubmit=e=>{e.preventDefault();if(running||!saved||!selection())return;const data=saveBody();if($('#api-key').value.trim())data.api_key=$('#api-key').value.trim();$('#api-key').value='';operation('save',async ticket=>{try{const result=await api('/api/model/settings',data,ticket.controller.signal);if(ticket.epoch!==epoch)return;saved=result;fill();message(saved.configured?'型号与连接已保存。可主动验证认证或确认合成生成测试。':'型号已保存，尚未填写对应平台 Key。','success');}finally{delete data.api_key;}});};
  $('#restore-key').onclick=()=>{if(running||!saved||dirty()||!saved.credential_saved_encrypted)return;const data=saveBody();data.persist=false;data.restore_key=true;data.consent=true;operation('save',async ticket=>{saved=await api('/api/model/settings',data,ticket.controller.signal);fill();message('已将当前连接的加密密钥恢复到内存；没有显示密钥或调用供应商。','success');});};
  $('#remove-key').onclick=()=>{if(!saved||dirty())return;const data=saveBody();data.persist=false;data.clear_key=true;operation('clear',async ticket=>{saved=await api('/api/model/settings',data,ticket.controller.signal);fill();message('当前连接的内存 Key 与本机加密副本已清除。');});};
  async function verifyResult(ticket,revision){const result=await api('/api/model/settings',null,ticket.controller.signal);if(ticket.epoch!==epoch)return null;if(result.revision!==revision)throw {code:'profile_changed'};saved=result;return result;}
  $('#verify-connection').onclick=()=>{if(dirty()||!saved?.configured)return;operation('verify',async ticket=>{const revision=saved.revision,result=await api('/api/model/models',{consent:true,revision},ticket.controller.signal);if(ticket.epoch!==epoch)return;if(result.revision!==revision||result.status!=='ok')throw {code:'profile_changed'};if(!await verifyResult(ticket,revision))return;renderTestResults();message('本次认证已通过；未发送 CRM 资料或触发生成。','success');});};
  $('#test-connection').onclick=()=>{if(dirty()||!saved?.configured||!$('#test-consent').checked)return;$('#test-consent').checked=false;operation('test',async ticket=>{const revision=saved.revision,result=await api('/api/model/test',{consent:true,revision},ticket.controller.signal);if(ticket.epoch!==epoch)return;if(result.revision!==revision||result.status!=='ok')throw {code:'profile_changed'};if(!await verifyResult(ticket,revision))return;renderTestResults();message('合成生成通过，当前选择可用于业务页逐次确认后的分析。','success');});};
  function pendingEdits(){
    if(!saved)return false;
    if(!choice&&!saved.model&&!saved.configured&&!$('#api-key').value&&$('#connection-name').value.trim()===(saved.name||''))return false;
    return dirty();
  }
  function editorOpen(){return $('#model-editor').matches(':popover-open')||$('#model-editor').classList.contains('is-open');}
  function renderCompact(){
    const feedback=$('#settings-feedback'),failed=feedback.classList.contains('error'),state=$('#settings-state');
    feedback.dataset.initialAdvice=String(feedback.textContent==='选择型号并填写对应平台 Key。保存不会调用模型。');
    $('#current-model').textContent=saved?.model||(!saved?'模型未读取':'选择模型');
    $('#current-provider').textContent=saved?.provider||'';
    $('#current-revision').textContent=saved?'已保存 v'+saved.revision:'';
    const bad=saved?.models_status==='failed'||saved?.generation_status==='failed'||saved?.models_status==='unsupported'||saved?.last_status==='failed';
    state.dataset.state=failed?'error':!saved?'loading':pendingEdits()?'unsaved':bad?'error':saved.generation_status==='ok'?'ready':'pending';
    state.textContent=failed&&!saved?'读取失败':!saved?'读取中':pendingEdits()?'未保存':!catalog?'目录不可用':bad?'验证失败':!saved.configured?'未填 Key':saved.generation_status==='ok'?'测试通过':saved.models_status==='ok'?'认证通过':'未验证';
    const full=statusText();state.title=full;
    $('#edit-model').title=(saved?.model||'尚未选择模型')+' · '+full+' · 修改全局模型连接';
    $('#edit-model').setAttribute('aria-label','模型：'+(saved?.model||'尚未选择')+'，'+full+'，修改全局连接');
    $('#edit-model').setAttribute('aria-expanded',String(editorOpen()));
    const problems=[];
    if(saved?.models_status==='failed')problems.push('认证验证未通过');
    if(saved?.models_status==='unsupported')problems.push('认证检查不受支持');
    if(saved?.generation_status==='failed')problems.push('生成测试未通过');
    const note=$('#current-error');note.textContent=problems.join(' · ');note.hidden=!problems.length;
    const summary=$('#summary-feedback');summary.textContent=feedback.textContent;summary.hidden=true;
    $('#model-editor').classList.toggle('has-pending-edits',pendingEdits());
  }
  function setupCompactEditor(){
    const editor=$('#model-editor'),guard=$('#unsaved-guard'),trigger=$('#edit-model');let returnFocus=null,mainScroll=null;
    const preserveSearchScroll=()=>{if(mainScroll!==null)$('.orbital-main').scrollLeft=mainScroll;};
    trigger.addEventListener('pointerdown',()=>{if(!editorOpen())mainScroll=$('.orbital-main').scrollLeft;},true);
    function position(){
      if(!editorOpen())return;
      const r=trigger.getBoundingClientRect(),gap=12;
      const width=Math.min(640,innerWidth-gap*2),left=Math.max(gap,Math.min(r.left,innerWidth-width-gap));
      const anchorVisible=r.bottom>0&&r.top<innerHeight;
      let top=anchorVisible?Math.max(gap,Math.min(r.bottom+8,innerHeight-240)):gap;
      const country=$('#country-trigger').getBoundingClientRect();
      if(anchorVisible&&country.left<left+width&&country.right>left&&country.bottom>top&&country.top<top+240)top=Math.min(country.bottom+8,innerHeight-240);
      editor.style.width=width+'px';editor.style.left=left+'px';editor.style.top=top+'px';
      editor.style.maxHeight=Math.max(200,innerHeight-top-gap)+'px';
    }
    function close(){
      closePicker();guard.hidden=true;$('#test-consent').checked=false;
      if(editor.matches(':popover-open'))editor.hidePopover();editor.classList.remove('is-open');
      controls();trigger.focus({preventScroll:true});preserveSearchScroll();
    }
    function requestClose(){
      if(running&&['save','clear','verify','test'].includes(running.kind))return false;
      if(pendingEdits()){
        closePicker();returnFocus=document.activeElement;guard.hidden=false;
        $('#continue-editing').focus({preventScroll:true});editor.scrollTop=editor.scrollHeight;preserveSearchScroll();return false;
      }
      close();return true;
    }
    function open(){
      if(editorOpen())return;
      if(mainScroll===null)mainScroll=$('.orbital-main').scrollLeft;
      // Dismiss existing home pickers through their existing behavior.
      if(!$('#country-popover').hidden)$('#country-trigger').click();
      $('.orbital-advanced').open=false;
      guard.hidden=true;$('#connection-validation').open=false;
      if(editor.showPopover)editor.showPopover();else editor.classList.add('is-open');
      position();$('#model-editor-title').focus({preventScroll:true});renderCompact();preserveSearchScroll();
    }
    trigger.onclick=()=>editorOpen()?requestClose():open();
    $('#close-model-editor').onclick=requestClose;$('#cancel-model-edit').onclick=requestClose;
    editor.addEventListener('keydown',e=>{
      if(e.key!=='Tab')return;
      const all=[...editor.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length);
      const first=all[0],last=all.at(-1);if(!first)return;
      if(e.shiftKey&&(document.activeElement===first||document.activeElement===$('#model-editor-title'))){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&(document.activeElement===last||document.activeElement===$('#model-editor-title'))){e.preventDefault();first.focus();}
    },true);
    document.addEventListener('keydown',e=>{
      if(!editorOpen())return;
      if(e.key==='Escape'){
        if(pickerOpen&&e.target===$('#model-search')){e.preventDefault();e.stopImmediatePropagation();closePicker();return;}
        e.preventDefault();e.stopImmediatePropagation();requestClose();
      }else if((e.key==='Enter'||e.key===' ')&&!editor.contains(e.target)&&e.target!==trigger){
        if(!requestClose()){e.preventDefault();e.stopImmediatePropagation();}
      }
    },true);
    // Capture before existing picker handlers, preserving drafts when outside controls are clicked.
    document.addEventListener('pointerdown',e=>{
      if(!editorOpen()||editor.contains(e.target)||trigger.contains(e.target))return;
      if(!requestClose()){e.preventDefault();e.stopImmediatePropagation();}
    },true);
    document.addEventListener('click',e=>{
      if(!editorOpen()||editor.contains(e.target)||trigger.contains(e.target))return;
      if(!requestClose()){e.preventDefault();e.stopImmediatePropagation();}
    },true);
    $('#continue-editing').onclick=()=>{guard.hidden=true;const target=returnFocus&&editor.contains(returnFocus)&&!returnFocus.disabled&&returnFocus.getClientRects().length?returnFocus:$('#model-editor-title');target.focus({preventScroll:true});};
    $('#discard-model-edit').onclick=()=>{if(running)return;fill();close();};
    window.addEventListener('resize',position);window.addEventListener('scroll',position,{passive:true});
    window.addEventListener('beforeunload',e=>{if(pendingEdits()){e.preventDefault();e.returnValue='';}});
    // Compatibility links open the same embedded editor, never change a model.
    if(new URLSearchParams(location.search).get('model')==='open'){if(location.hash==='#model-entry'){history.replaceState(null,'',location.pathname+location.search);scrollTo(0,0);}open();}
  }

  setupCompactEditor();controls();refresh(true);
})();
