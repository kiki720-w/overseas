'use strict';
(() => {
 const bridge=window.StructuredDealBridge;if(!bridge)return;
 const panel=document.createElement('section');panel.className='structured-deal';panel.id='structured-deal';
 const node=(tag,text)=>{const e=document.createElement(tag);if(text)e.textContent=text;return e;};
 const heading=node('h3','结构化建议与人工确认');const status=node('p','正在检查结构化功能…');status.role='status';status.setAttribute('aria-live','polite');
 const notice=node('p','模型解释是建议；原文引用不等于独立证实。未确认承诺不能进入正文；中性澄清草稿只含固定问题，仍需人工审核。人工确认仅存本地解释；当前没有审批身份，不能正式审批或导出。');
 const preview=node('pre');preview.hidden=true;
 const actions=node('div');actions.className='structured-actions';
 const prepare=node('button','预览本次发送范围');prepare.type='button';prepare.className='secondary';prepare.id='structured-prepare';
 const generate=node('button','生成结构化建议');generate.type='button';generate.id='structured-generate';generate.disabled=true;
 const cancel=node('button','停止等待');cancel.type='button';cancel.className='secondary';cancel.id='structured-cancel';cancel.hidden=true;
 const refresh=node('button','核对已保存结果');refresh.type='button';refresh.className='secondary';refresh.id='structured-refresh';
 const historyButton=node('button','查看历史（只读）');historyButton.type='button';historyButton.className='secondary';historyButton.id='structured-history';
 const historyPanel=node('section');historyPanel.id='structured-history-panel';historyPanel.hidden=true;
 const connection=node('section');connection.id='history-read-connection';connection.hidden=true;
 const connectionStatus=node('p','历史读取未连接。请用本机连接工具领取一次性码，自行输入；最多30天，只读，可撤销。');connectionStatus.role='status';
 const codeLabel=node('label','本机一次性连接码'),code=node('input');code.type='password';code.autocomplete='off';code.maxLength=64;code.id='history-connection-code';codeLabel.append(code);
 const bind=node('button','连接历史只读查看');bind.type='button';bind.className='secondary';bind.id='history-connect';
 const disconnect=node('button','退出此浏览器历史读取');disconnect.type='button';disconnect.className='secondary';disconnect.id='history-disconnect';disconnect.hidden=true;
 connection.append(node('h4','本机历史读取连接'),connectionStatus,codeLabel,bind,disconnect);
 const historyStatus=node('p');historyStatus.role='status';const historyList=node('div'),historyDetail=node('div');historyPanel.append(node('h4','历史分析与未审批草稿'),historyStatus,historyList,historyDetail);
 const consentLabel=node('label'),consent=node('input');consentLabel.className='inline structured-consent-label';consent.type='checkbox';consent.id='structured-consent';consentLabel.append(consent,node('span','我同意本次向上方已配置模型发送这些已保存资料（会产生费用）'));
 const evidence=node('div');evidence.id='structured-evidence';
 const confirm=node('button','确认勾选的原文解释');confirm.type='button';confirm.className='secondary';confirm.disabled=true;confirm.id='structured-confirm';
 const draftLabel=node('label','本地未审批草稿（修改会撤销全部确认）');const draft=node('textarea');draft.id='structured-draft';draft.maxLength=12000;draftLabel.append(draft);draftLabel.hidden=true;
 const save=node('button','保存修改为未审批草稿');save.type='button';save.className='secondary';save.disabled=true;save.id='structured-save';
 const reject=node('button','撤回本地草稿');reject.type='button';reject.className='secondary';reject.disabled=true;
 const exportButton=node('button','正式审批 / 导出暂不可用');exportButton.disabled=true;exportButton.type='button';
 const internalDownload=node('button','下载内部未审核稿 TXT');internalDownload.type='button';internalDownload.className='secondary';internalDownload.id='structured-internal-download';internalDownload.disabled=true;
 const downloadNotice=node('p','仅下载服务器已保存的当前稿。文件明确标注未审核，须人工核对；正式审批、批准导出与自动发送仍关闭。');
 actions.append(prepare,generate,cancel,refresh);panel.append(heading,status,notice,preview,consentLabel,actions,evidence,confirm,draftLabel,save,reject,internalDownload,downloadNotice,exportButton);
 document.querySelector('.advice-panel').append(panel);
 actions.append(historyButton);panel.append(connection,historyPanel);
 let meta=null,prepared=null,row=null,cid='',busy=false,serial=0,edited=false,active=null,away=false,restoreVersion=0,restoreController=null,draftRevision=0;
 const key=()=>`structured-deal-v1:${cid}`;
 const call=(action,data,signal)=>bridge.api('/api/deals/structured/'+action,data,signal);
 const current=()=>bridge.customer()?.id||'';
 let historySelection=0;
 let historyConnection=null;
 async function historyKey(create=false,remove=false){
  const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('overseas-history-browser-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('keys');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(Error('浏览器本地读取存储不可用'));});
  try{if(remove){await new Promise((resolve,reject)=>{const t=db.transaction('keys','readwrite');t.objectStore('keys').delete('reader');t.oncomplete=resolve;t.onerror=reject;});return null;}
   let key=await new Promise((resolve,reject)=>{const r=db.transaction('keys').objectStore('keys').get('reader');r.onsuccess=()=>resolve(r.result);r.onerror=reject;});
   if(!key&&create){const fresh=await crypto.subtle.generateKey({name:'HMAC',hash:'SHA-256',length:256},false,['sign']);await new Promise((resolve,reject)=>{const t=db.transaction('keys','readwrite'),s=t.objectStore('keys'),r=s.get('reader');r.onsuccess=()=>{key=r.result||fresh;if(!r.result)s.put(key,'reader');};t.oncomplete=resolve;t.onerror=reject;});}
   if(key&&(key.extractable||key.algorithm.name!=='HMAC'))throw Error('浏览器读取证明无效');return key;
  }finally{db.close();}
 }
 async function historyProof(create=false){const key=await historyKey(create);if(!key)return null;const value=await crypto.subtle.sign('HMAC',key,new TextEncoder().encode('OverseasHistoryBrowserProof/v1'));return btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');}
 const historyAPI=async(path,data)=>bridge.api(path,data,null,await historyProof());
 async function checkHistoryConnection(){try{historyConnection=await historyAPI('/api/deals/structured/history-access/meta');connection.hidden=!historyConnection.configured;disconnect.hidden=!historyConnection.connected;codeLabel.hidden=bind.hidden=!!historyConnection.connected;connectionStatus.textContent=historyConnection.connected?'已连接，只读 '+historyConnection.owned_records+' 条获准分析；最长30天，不恢复审批或发送。':'历史读取未连接、到期或已撤销。请用本机连接工具领取一次性码，自行输入；仅本机，只读。';if(historyConnection.configured&&!historyConnection.connected)clearHistory();}catch(e){connectionStatus.textContent='暂时无法核对历史连接；没有重新调用模型。';}}
 bind.onclick=async()=>{bind.disabled=true;const entered=code.value;code.value='';try{await bridge.api('/api/deals/structured/history-access/connect',{code:entered,browser_proof:await historyProof(true)});await checkHistoryConnection();}catch(e){connectionStatus.textContent='连接未完成：'+e.message+'；失效码需由你重新领取，不会自动绑定。';}finally{bind.disabled=false;}};
 disconnect.onclick=async()=>{disconnect.disabled=true;try{await historyAPI('/api/deals/structured/history-access/disconnect',{});await historyKey(false,true);clearHistory();await checkHistoryConnection();}catch(e){connectionStatus.textContent='退出未完成，请用本机工具撤销连接。';}finally{disconnect.disabled=false;}};
 window.addEventListener('focus',checkHistoryConnection);checkHistoryConnection();
 const historyReasons={context_changed:'客户、记录、工厂或所选资料已变化',knowledge_version_changed_or_disabled:'所选知识资料版本已更新或停用',source_unavailable_or_out_of_scope:'原资料不可用、过期或超出核实范围',model_revision_changed:'模型配置版本已变化',policy_changed:'生成/校验规则已变化',already_invalidated:'该版本此前已失效',no_reusable_result:'请求失败、未完成或草稿已撤回'};
 function clearHistory(){historySelection++;historyPanel.hidden=true;historyList.replaceChildren();historyDetail.replaceChildren();}
 historyButton.onclick=async()=>{
  if(!cid||away||busy)return;const view=stamp();historyPanel.hidden=false;historyStatus.textContent='正在读取当前会话有权访问的历史…';historyList.replaceChildren();historyDetail.replaceChildren();
  try{const list=await historyAPI('/api/deals/structured/history?customer_id='+encodeURIComponent(view.customer));if(!validView(view)||list.customer_id!==view.customer)return;
   historyStatus.textContent=list.items.length?'历史仅供只读核查，不恢复批准、编辑、导出或发送。':'当前会话没有可访问的历史；重启后的会话绑定尚未恢复，这不证明数据库没有记录。';
   for(const record of list.items)for(const revision of record.items){const button=node('button','结果 '+record.id.slice(0,8)+' · 修订 '+revision.revision+(revision.draft_saved?' · 有保存草稿':' · 未保存草稿'));button.type='button';button.className='secondary';historyList.append(button);
    button.onclick=async()=>{const selected=stamp(),selection=++historySelection;historyDetail.replaceChildren();try{const value=await historyAPI('/api/deals/structured/history-view?id='+encodeURIComponent(record.id)+'&customer_id='+encodeURIComponent(selected.customer)+'&revision='+revision.revision);if(selection!==historySelection||!validView(selected)||value.customer_id!==selected.customer)return;
     historyDetail.append(node('p',value.validity.reasons.length?'当前已失效：'+value.validity.reasons.map(x=>historyReasons[x]||x).join('；'):'来源版本仍一致，但历史始终只读，不能复用批准。'));
     for(const ref of value.evidence||[]){const s=ref.source;historyDetail.append(node('p',(s.source||'')+' · '+(s.document_id||s.record_id||s.field||'')+' · v'+(s.version||1)+' · 原文：“'+s.quote+'”'));}
     const analysis=node('pre',JSON.stringify(value.envelope,null,2));historyDetail.append(node('h4','已保存分析（历史建议）'),analysis);
     if(value.draft){const old=node('textarea');old.readOnly=true;old.value=value.draft;old.setAttribute('aria-label','历史未审批草稿，只读');historyDetail.append(node('h4',value.draft_label),old);}else historyDetail.append(node('p','此版本没有保存草稿，不能重建或自动生成。'));
    }catch(e){if(selection===historySelection&&validView(selected))historyDetail.append(node('p','历史读取失败：'+e.message+'；没有重新调用模型。'));}};
   }
  }catch(e){if(validView(view))historyStatus.textContent='历史读取失败：'+e.message+'；没有重新调用模型。';}
 };
 document.querySelector('#customer-select').addEventListener('change',clearHistory);window.addEventListener('deal-context-invalidated',clearHistory);
 function inputScope(){return {customer_id:current(),knowledge_selections:JSON.parse(JSON.stringify(bridge.selections())),deal_question:document.querySelector('#rag-question').value.trim()||null};}
 function inputSignature(){return JSON.stringify({scope:inputScope(),selected:document.querySelector('#customer-select').value,version:bridge.customer()?.version,factory:bridge.factory?.(),contextRevision:bridge.contextRevision?.(),modelRevision:bridge.modelRevision?.(),stage:document.querySelector('#stage-select').value,blocker:document.querySelector('#blocker').value,record:document.querySelector('#record-text').value,manual:document.querySelector('#rag-manual').checked});}
 function stamp(){return {epoch:serial,customer:cid,input:inputSignature()};}
 function validView(view){return !away&&view.epoch===serial&&view.customer===cid&&view.customer===current()&&document.querySelector('#customer-select').value===cid&&view.input===inputSignature();}
 function validPrepared(){return !!prepared&&validView(prepared.stamp)&&prepared.scope.customer_id===cid&&prepared.context.customer_id===cid&&(!Number.isInteger(bridge.modelRevision?.())||bridge.modelRevision()===prepared.context.revision);}
 function accepts(operation){return active===operation&&validView(operation.stamp)&&!operation.controller.signal.aborted&&(operation.kind!=='edit'||operation.draftRevision===draftRevision);}
 function stopRequests(){active?.controller.abort();active=null;busy=false;restoreVersion++;restoreController?.abort();restoreController=null;}
 const labels={gear_fixture:'齿轮工装夹具',cnc_fixture:'CNC 夹具',steel:'钢材',aluminum:'铝材',turning:'车削',milling:'铣削',potential_product_fit:'产品可能匹配，仍需核实',drawing_needed:'待询问图纸（未证明缺失）',price:'价格',lead_time:'交期',certification:'认证',accuracy:'精度',capability:'制造可行性',products:'产品',capabilities:'生产能力',factory:'工厂资料',customer:'客户资料',record:'沟通记录',knowledge:'知识资料'};
 const fields={name:'名称',products:'产品',focus:'应用方向',capabilities:'生产能力',type:'客户类型',country:'国家',product:'需求产品',website:'官网',email:'邮箱',contact_name:'联系人',source:'资料来源',source_date:'来源日期',facts:'已记录事实',hypotheses:'待核实推断',unknowns:'未知项',signals:'线索',stage:'阶段',blocker:'卡点',priority:'重点标记'};
 const valueText=c=>{const v=c.value;if(!v)return labels[c.code||c.slot]||c.code||c.slot;if(v.kind==='category')return labels[v.value]||v.value;if(v.kind==='measurement')return v.amount+' '+({day:'天',mm:'毫米'})[v.unit];if(v.kind==='money')return `${v.currency} ${v.amount} · ${v.quantity} 件 · ${v.basis==='total'?'总价':'单价'}`;if(v.kind==='certification')return v.standard;return '制造可行性待工程核对';};
 function controls(){const dirty=bridge.dirty();if(meta?.enabled)document.body.classList.add('structured-enabled');prepare.disabled=away||busy||!cid||dirty||!meta?.enabled;generate.disabled=away||busy||!validPrepared()||!consent.checked||dirty||!meta?.real_generation_allowed||!prepared?.context.configured||!prepared?.context.real_allowed;confirm.disabled=away||busy||dirty||edited||row?.customer_id!==cid||!row?.actions.confirm;save.disabled=away||busy||!edited||row?.customer_id!==cid||!row?.active||!row?.actions.edit;reject.disabled=away||busy||row?.customer_id!==cid||!row?.active;internalDownload.disabled=away||busy||dirty||edited||row?.customer_id!==cid||!row?.actions.internal_download;draft.disabled=busy;draft.readOnly=!row?.actions.edit;cancel.hidden=!busy;cancel.disabled=!busy;refresh.disabled=away||busy||!cid||dirty;}
 function clear(message){serial++;stopRequests();prepared=null;consent.checked=false;preview.hidden=true;preview.textContent='';row=null;evidence.replaceChildren();draft.value='';draftLabel.hidden=true;edited=false;status.textContent=message;controls();}
 function render(value){row=value;edited=false;evidence.replaceChildren();draft.value=value.active?value.draft||'':'';draftLabel.hidden=!value.active||!value.draft;
  draftLabel.firstChild.nodeValue=value.draft_label||'本地未审批草稿（修改会撤销全部确认）';
  const stateLabels={generating:'请求进行中；重试同一请求不会再次调用模型',failed:'模型返回未通过验证，没有可用草稿',suggested:'建议待核对，尚未确认',locally_confirmed:'已保存本地解释；草稿未审批',edited_unapproved:'人工修改已撤销确认；重新分析后才可再次核对',invalidated:'资料、模型或规则变化；结果已失效',rejected:'草稿已撤回'};
  status.textContent=stateLabels[value.status]||value.status;if(value.error)status.textContent+=' · '+value.error;
  if(Array.isArray(value.diagnostics))for(const d of value.diagnostics.slice(0,8)){if(d&&/^[a-z_]{1,40}$/.test(d.reason)&&typeof d.path==='string'&&d.path.length<=160)status.textContent+=' | '+d.reason+' @ '+d.path;}
  if(value.pending?.length)status.textContent+=' · 待确认承诺：'+value.pending.join('、')+(value.draft_kind==='clarification_only_unreviewed'?'（未纳入中性澄清草稿）':'（不生成承诺正文）');
  for(const [i,claim] of (value.envelope?.claims||[]).entries()){
   const item=node('div');item.className='structured-evidence';const label=node('label');
   if(['factory_fact','customer_quote'].includes(claim.kind)){const check=node('input');check.type='checkbox';check.dataset.claim=String(i);check.checked=value.confirmed?.includes(i)||false;check.disabled=!value.actions.confirm;label.append(check);}
   label.append(node('span',({factory_fact:'建议：工厂目录解释',customer_quote:'建议：客户要求解释',inference:'内部推断（不进入草稿）',pending_commitment:'待确认承诺（不进入正文）'})[claim.kind]+' · '+valueText(claim)));item.append(label);
   for(const ref of (value.evidence||[]).filter(r=>r.claim_index===i)){const s=ref.source,loc=s.location||{};const where=s.source==='knowledge'?`${s.filename} · v${s.version} · ${loc.page?'第 '+loc.page+' 页':loc.paragraph?'第 '+loc.paragraph+' 段':'所选片段'}`:fields[s.field]||s.record_id||'';item.append(node('p',`${ref.reference.source_id} · ${labels[s.source]||s.source} · ${where} · 原文：“${s.quote}”`));}
   evidence.append(item);
  }
  const local=sessionStorage.getItem(key()+':unsaved:'+row.id);if(value.active&&value.actions.edit&&local!==null&&local!==value.draft){draft.value=local;edited=true;status.textContent='修改尚未保存，确认已撤销；网络失败不会丢失输入。';for(const check of evidence.querySelectorAll('input')){check.checked=false;check.disabled=true;}}else if(!value.active||local===value.draft)sessionStorage.removeItem(key()+':unsaved:'+row.id);controls();
 }
 async function restore(){if(!cid||away||busy||bridge.dirty())return;const id=sessionStorage.getItem(key());if(!id)return;const view=stamp(),version=++restoreVersion;restoreController?.abort();const controller=new AbortController();restoreController=controller;try{const value=await bridge.api(`/api/deals/structured/view?id=${encodeURIComponent(id)}&customer_id=${encodeURIComponent(view.customer)}`,null,controller.signal);if(version===restoreVersion&&validView(view)&&value.customer_id===view.customer&&!controller.signal.aborted)render(value);}catch(e){if(version===restoreVersion&&validView(view)&&!controller.signal.aborted)status.textContent='已保存结果暂不可读：'+e.message;}}
 function sync(){const next=current();if(next!==cid){cid=next;clear(cid?'先保存客户资料与沟通记录，再预览发送范围。':'请先选择客户。');restore();}else controls();}
 async function work(kind,fn){sync();if(busy||away)return;restoreVersion++;restoreController?.abort();const operation={kind,stamp:stamp(),draftRevision,controller:new AbortController()};active=operation;busy=true;controls();try{await fn(operation);}catch(e){if(accepts(operation))status.textContent=e.message;}finally{if(active===operation){if(!validView(operation.stamp))clear('范围已变化，请重新预览；旧响应未被采用。');else{active=null;busy=false;if(kind==='edit'&&operation.draftRevision!==draftRevision)status.textContent='等待期间又有修改，当前未保存输入已保留；请核对已保存结果。';controls();}}}}
 prepare.onclick=()=>work('prepare',async operation=>{if(!cid||bridge.dirty())throw Error('请先保存阶段、卡点和客户材料。');const scope=inputScope();prepared=null;consent.checked=false;preview.hidden=true;const value=await call('prepare',scope,operation.controller.signal);if(!accepts(operation))return;const c=value.model_context;if(c?.customer_id!==scope.customer_id||scope.customer_id!==current())throw Error('预览客户不匹配，已拒绝旧范围。');prepared={scope,context:c,stamp:operation.stamp};preview.textContent=`${c.provider} / ${c.model||'未选择模型'}\n客户：${c.customer_name} · 版本 ${c.customer_version}\n工厂字段：${c.fields.factory.map(k=>fields[k]||k).join('、')}\n客户字段：${c.fields.customer.map(k=>fields[k]||k).join('、')}\n已保存记录：${c.record_count} / ${c.records_total} · ${c.input_bytes} bytes\n${window.KnowledgeSelection.preview(c).trim()}`;preview.hidden=false;consent.checked=false;status.textContent=c.real_allowed&&c.configured?'请核对范围，再同意本次模型调用。':'当前客户或模型尚不能调用；请检查模型设置。';});
 generate.onclick=()=>work('generate',async operation=>{if(!validPrepared()||!consent.checked||bridge.dirty())throw Error('请重新预览当前客户并同意。');const payload={...prepared.scope,consent:true,revision:prepared.context.revision,context_token:prepared.context.context_token};if(payload.customer_id!==operation.stamp.customer||payload.customer_id!==current())throw Error('当前客户与发送范围不匹配，未调用模型。');const pendingKey=`structured-deal-v1:${payload.customer_id}:pending`,signature=JSON.stringify(payload);let entries=[];try{const stored=JSON.parse(sessionStorage.getItem(pendingKey));if(Array.isArray(stored))entries=stored.filter(x=>typeof x.signature==='string'&&/^[a-f0-9]{32}$/.test(x.request_id));}catch{}let pending=entries.find(x=>x.signature===signature);if(!pending){if(entries.length>=16)throw Error('尚未核对的请求过多，请先核对已有结果；不会自动重复发送。');pending={signature,request_id:crypto.randomUUID().replaceAll('-','')};entries.push(pending);}sessionStorage.setItem(pendingKey,JSON.stringify(entries));payload.request_id=pending.request_id;status.textContent='正在向已配置模型请求结构化建议…';const value=await call('generate',payload,operation.controller.signal);if(!accepts(operation))return;if(value.customer_id!==operation.stamp.customer)throw Error('结果客户不匹配，未展示。');sessionStorage.setItem(key(),value.id);if(!['generating','failed'].includes(value.status))sessionStorage.setItem(pendingKey,JSON.stringify(entries.filter(x=>x.request_id!==pending.request_id)));render(value);consent.checked=false;});
 confirm.onclick=()=>work('confirm',async operation=>{if(row?.customer_id!==operation.stamp.customer||edited||bridge.dirty())throw Error('请重新核对当前客户。');const value=await call('confirm',{id:row.id,customer_id:row.customer_id,snapshot:row.snapshot,indices:[...evidence.querySelectorAll('input:checked')].map(x=>Number(x.dataset.claim))},operation.controller.signal);if(accepts(operation)&&value.customer_id===operation.stamp.customer)render(value);});
 draft.oninput=()=>{if(!row?.actions.edit){draft.value=row?.draft||'';controls();return;}draftRevision++;edited=true;sessionStorage.setItem(key()+':unsaved:'+row.id,draft.value);status.textContent='修改尚未保存；全部确认已在界面撤销，不能审批或导出。';for(const check of evidence.querySelectorAll('input')){check.checked=false;check.disabled=true;}controls();};
 save.onclick=()=>work('edit',async operation=>{if(row?.customer_id!==operation.stamp.customer)throw Error('草稿客户已改变。');const id=row.id,target=row.customer_id;const value=await call('edit',{id,customer_id:target,snapshot:row.snapshot,draft:draft.value},operation.controller.signal);if(accepts(operation)&&value.customer_id===target){sessionStorage.removeItem(`structured-deal-v1:${target}:unsaved:${id}`);render(value);}});
 reject.onclick=()=>work('reject',async operation=>{if(row?.customer_id!==operation.stamp.customer)throw Error('草稿客户已改变。');const id=row.id,target=row.customer_id;const value=await call('reject',{id,customer_id:target,snapshot:row.snapshot},operation.controller.signal);if(accepts(operation)&&value.customer_id===target){sessionStorage.removeItem(`structured-deal-v1:${target}:unsaved:${id}`);render(value);}});consent.onchange=controls;
 internalDownload.onclick=()=>work('internal-download',async operation=>{
  if(edited||bridge.dirty()||row?.customer_id!==operation.stamp.customer||!row?.actions.internal_download)throw Error('请先保存修改并核对当前客户的草稿版本。');
  const expected={id:row.id,customer_id:row.customer_id,snapshot:row.snapshot},revision=row.revision;
  const value=await call('internal-download',expected,operation.controller.signal);
  if(!accepts(operation))return;
  if(value.kind!=='internal_unreviewed_txt'||value.approved!==false||value.id!==expected.id||value.customer_id!==expected.customer_id||value.snapshot!==expected.snapshot||value.revision!==revision||typeof value.text!=='string'||!value.text.startsWith('内部未审核草稿 / INTERNAL UNREVIEWED DRAFT\n')||!/^internal-unreviewed-[a-f0-9]{16}-r[1-9][0-9]*\.txt$/.test(value.filename))throw Error('下载版本或文件信息不匹配，已拒绝。');
  const bytes=new TextEncoder().encode(value.text),hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
  if(!accepts(operation))return;
  if(hash!==value.sha256||bytes.length!==value.bytes)throw Error('下载内容校验失败，未创建文件。');
  const url=URL.createObjectURL(new Blob([bytes],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=value.filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status.textContent='已下载内部未审核稿；须人工核对，未批准、未发送。';
 });
 cancel.onclick=()=>clear('已停止等待；已发出的请求可能仍完成。重新预览后核对同一请求，不自动重复调用模型。');refresh.onclick=restore;
 window.addEventListener('deal-context-invalidated',()=>{sync();clear('发送范围已失效。请先保存修改，然后重新预览。');if(!bridge.dirty())restore();});
 document.querySelector('#customer-select').addEventListener('change',sync);window.addEventListener('focus',()=>{sync();restore();});
 for(const form of [document.querySelector('#stage-form'),document.querySelector('#record-form')])form.addEventListener('input',()=>clear('输入已改变，旧范围与请求已停用；请先保存再预览。'));
 document.querySelector('#rag-question').addEventListener('input',()=>clear('问题已改变，请重新预览。'));
 document.querySelector('#rag-manual').addEventListener('change',()=>clear('资料选择方式已改变，请重新预览。'));
 new MutationObserver(()=>{if(prepared&&!validPrepared())clear('资料范围已改变，请重新预览。');}).observe(document.querySelector('#rag-sources'),{childList:true,subtree:true});
 window.addEventListener('pagehide',()=>{away=true;clear('已离开此页，旧请求不会写入当前界面。');});
 window.addEventListener('pageshow',event=>{if(event.persisted){away=false;clear('已返回此页，请重新核对当前范围。');sync();restore();}});
 new MutationObserver(sync).observe(document.querySelector('#customer-select'),{childList:true});
 (async()=>{try{meta=await bridge.api('/api/deals/structured/meta');if(meta.enabled){for(const selector of ['#analyze','#analysis','#analysis-placeholder','.model-controls','#real-consent'])document.querySelector(selector).hidden=true;const settings=node('a','模型设置');settings.href='/settings';document.querySelector('.advice-panel').append(settings);}else status.textContent='结构化验证器未启用；模型连接位于查找页的小入口。';sync();controls();}catch(e){status.textContent='结构化功能未部署：'+e.message;prepare.disabled=true;}})();
})();
