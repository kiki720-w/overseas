'use strict';
// UI view model only. No backend contract, HTTP request or authority is defined here.
(() => {
 // Textarea display uses LF; keep raw server text separately for hashes and codepoint spans.
 const editorText=value=>value.replace(/\r\n?/g,'\n');
 const text=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=String(value??'');if(cls)n.className=cls;return n;};
 class NeedsReviewDraft {
  constructor(host){
   this.host=host;this.context=null;this.value=null;this.epoch=0;this.busy=false;
   this.panel=text('section',null,'needs-review-panel');this.panel.hidden=true;
   this.panel.setAttribute('aria-label','待核草稿');
   this.title=text('h3','待核草稿 · 未审核');
   this.notice=text('p','原始建议尚未完成核验。原文转录只说明出处，不代表整段回复已获证实；编辑不会变成事实核实、批准或发送。','needs-review-notice');
   this.status=text('p');this.status.role='status';this.status.setAttribute('aria-live','polite');
   this.original=text('details');this.original.append(text('summary','查看模型原稿与原始建议（保留原文）'));this.originalText=text('pre');this.original.append(this.originalText);
   this.questions=text('section',null,'needs-review-questions');this.questions.setAttribute('aria-label','尚未完成核对的问题');
   this.sources=text('div',null,'needs-review-sources');this.unknowns=text('div',null,'needs-review-unknowns');
   this.label=text('label','编辑待核回复（仍未审核）');this.input=document.createElement('textarea');this.input.maxLength=12000;this.input.rows=9;this.input.setAttribute('aria-label','编辑待核回复');this.label.append(this.input);
   this.save=text('button','保存待核编辑');this.save.type='button';this.save.disabled=true;
   this.reset=text('button','丢弃本次编辑');this.reset.type='button';this.reset.className='secondary';this.reset.disabled=true;
   this.dismiss=text('button','关闭本次展示');this.dismiss.type='button';this.dismiss.className='secondary';
   const actions=text('div',null,'needs-review-actions');actions.append(this.save,this.reset,this.dismiss);
   this.boundary=text('p','待核稿未进入已批准导出，未发送。当前修改仅保留在本页；保存须由当前会话重新核对版本。','needs-review-boundary');
   this.panel.append(this.title,this.notice,this.status,this.original,this.questions,this.sources,this.unknowns,this.label,actions,this.boundary);host.append(this.panel);
   this.input.oninput=()=>{if(!this.valid()){this.clear();return;}this.status.textContent='本次编辑尚未保存，核验状态未改变。';this.controls();this.changed?.();};
   this.reset.onclick=()=>{if(!this.valid())return this.clear();this.input.value=this.value.savedText;this.status.textContent='已丢弃本次编辑，恢复当前保存的待核文本。';this.controls();};
   this.dismiss.onclick=()=>this.clear();
  }
  signature(scope){return JSON.stringify([scope?.customer,scope?.sessionEpoch,scope?.revisionEpoch]);}
  setContext(scope){const valid=scope&&['customer','sessionEpoch','revisionEpoch'].every(k=>typeof scope[k]==='string'&&scope[k].length>0);const next=valid?scope:null;if(this.signature(next)!==this.signature(this.context)){this.clear();this.context=next?{...next}:null;}}
  valid(){return !!this.value&&!!this.context&&this.signature(this.value.scope)===this.signature(this.context);}
  controls(){const dirty=this.valid()&&this.input.value!==this.value.savedText;this.reset.disabled=this.busy||!dirty;this.save.disabled=this.busy||!dirty||!this.canSave?.();this.input.disabled=this.busy;}
  clear(){this.epoch++;this.value=null;this.busy=false;this.panel.hidden=true;this.input.value='';this.originalText.textContent='';this.questions.replaceChildren();this.sources.replaceChildren();this.unknowns.replaceChildren();this.status.textContent='';this.original.open=false;this.controls();}
  show(view){
   if(!view||!this.context||this.signature(view.scope)!==this.signature(this.context))return false;
   if(this.valid()&&this.input.value!==this.value.savedText){this.status.textContent='修改尚未保存，未用返回内容覆盖。';return false;}
   if(typeof view.originalText!=='string'||typeof view.savedText!=='string'||view.savedText.length>12000||!Array.isArray(view.evidence)||!Array.isArray(view.reasons))return false;
   this.clear();this.value=structuredClone(view);this.value.rawSavedText=view.savedText;this.value.savedText=editorText(view.savedText);this.panel.hidden=false;this.originalText.textContent=view.originalText;this.input.value=this.value.savedText;
   this.status.textContent='当前版本：待核，未审核。';
   this.questions.append(text('h4',view.reviewUnitLabel||'尚未完成核对的问题'),text('p','尚未完成核对 · 出处仅支持逐问列明的转录，空的未知项列表不代表问题已覆盖。','needs-review-question-state'));
   const questionList=text('ol',null,'needs-review-question-list');
   const fullText=(host,value,summary,cls)=>{if(typeof value!=='string')return;if(value.length>240){const d=text('details',null,'needs-review-long-text');d.append(text('summary',summary+'：'+value.slice(0,100)+'…'),text('pre',value,cls));host.append(d);}else host.append(text('p',value,cls));};
   for(const q of view.pendingQuestions||[]){
    const item=text('li',null,'needs-review-question');item.append(text('p',q.label,'needs-review-question-id'));fullText(item,q.text,'展开完整问题原文','needs-review-question-text');
    fullText(item,q.reason,'展开待核原因','needs-review-question-reason');
    const sources=text('details',null,'needs-review-question-evidence');sources.append(text('summary','逐问已有出处 · '+q.evidence.length+' 条（非完整答案核验）'));
    if(!q.evidence.length)sources.append(text('p','此问题暂无绑定的原文出处；不借用其他问题或整体资料清单。'));
    for(const source of q.evidence){const card=text('div',null,'needs-review-source');card.append(text('p',source.label,'needs-review-location'));fullText(card,source.quote,'展开完整引用原文','needs-review-quote');const record=text('details');record.append(text('summary','查看完整出处与转录记录'),text('pre',source.detail));card.append(record);sources.append(card);}
    item.append(sources);questionList.append(item);
   }
   if(!questionList.children.length)this.questions.append(text('p','当前未列出逐问待核项，核对仍未完成；请对照原问题人工确认。','needs-review-question-empty'));else this.questions.append(questionList);
   const transcriptions=view.evidence.filter(e=>e.kind!=='catalog'),catalog=view.evidence.filter(e=>e.kind==='catalog');
   const card=item=>{const n=text('div',null,'needs-review-source');n.append(text('p',item.label),text('p',item.quote,'needs-review-quote'),text('p',item.location,'needs-review-location'));if(typeof item.detail==='string'){const detail=text('details');detail.append(text('summary','查看完整出处与转录记录'),text('pre',item.detail));n.append(detail);}this.sources.append(n);};
   this.sources.append(text('h4','字段证据支持的转录（非整稿核实）'));
   if(!transcriptions.length)this.sources.append(text('p','当前没有可展示的字段转录，不据此补充事实或规格。'));for(const item of transcriptions)card(item);
   if(catalog.length){this.sources.append(text('h4','本次选定出处（仅对应引用范围）'));for(const item of catalog)card(item);}
   this.unknowns.append(text('h4','未知项与待核原因'));
   const list=document.createElement('ul');for(const reason of view.reasons)list.append(text('li',reason));if(!view.reasons.length)list.append(text('li','暂无明确核验原因，不能理解为已通过审核。'));this.unknowns.append(list);if(typeof view.reasonRecords==='string'){const detail=text('details');detail.append(text('summary','查看待核项原始记录'),text('pre',view.reasonRecords));this.unknowns.append(detail);}this.controls();return true;
  }
 }
 window.NeedsReviewDraft=NeedsReviewDraft;
})();

'use strict';
(() => {
 const protocol='deal-review-draft-v4',idPattern=/^[a-f0-9]{32}$/;
 const errorLabels={review_scope_mismatch:'当前会话或客户无权访问此稿，已清除展示。',review_draft_not_found:'此待核稿已不存在，已清除展示。',review_draft_expired:'此临时待核稿已过期；请重新预览并确认生成。',review_context_changed:'客户、资料或模型版本已变化，旧待核稿已停用。',review_draft_conflict:'保存版本发生冲突。保留本页编辑，请重新核对；丢弃本次编辑后才能使用当前版本。'};
 const plain=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
 const editableText=value=>Array.from(value).length<=6000&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value);
 // This component only renders system-derived suggestions inside an authorized review draft.
 class SystemReplyProposal {
  constructor(owner){this.owner=owner;this.host=this.node('section',null,'reply-quality-proposal');this.host.setAttribute('aria-label','系统补充建议（未审核）');this.epoch=0;this.row=null;this.proposal=null;this.hashesMatch=false;this.adoptButton=null;this.hashNotice=null;}
  node(tag,value,cls){const n=document.createElement(tag);if(value!==null&&value!==undefined)n.textContent=String(value);if(cls)n.className=cls;return n;}
  detail(host,title,value){const d=this.node('details'),pre=this.node('pre',typeof value==='string'?value:JSON.stringify(value,null,2));d.append(this.node('summary',title),pre);host.append(d);return d;}
  text(host,value){if(typeof value!=='string')return;if(value.length>220)this.detail(host,'展开完整建议原文：'+Array.from(value).slice(0,80).join('')+'…',value);else host.append(this.node('p',value,'reply-quality-text'));}
  clear(){this.epoch++;this.row=null;this.proposal=null;this.hashesMatch=false;this.adoptButton=null;this.hashNotice=null;this.host.replaceChildren();}
  key(row,p){return JSON.stringify([row.customer_id,row.draft_id,row.draft_version,p?.id]);}
  current(){return !!this.row&&this.owner.current()&&this.owner.row.draft_id===this.row.draft_id&&this.owner.row.draft_version===this.row.draft_version&&this.owner.row.customer_id===this.row.customer_id&&this.key(this.owner.row,this.owner.row.review.reply_quality_proposal)===this.key(this.row,this.proposal);}
  canAdopt(){return this.current()&&this.hashesMatch&&!this.owner.active&&!this.owner.blocked&&this.owner.row.permissions.edit&&typeof this.proposal.suggested_draft==='string'&&editableText(this.proposal.suggested_draft)&&this.owner.row.edited_draft===null&&this.owner.ui.input.value===this.owner.ui.value.savedText;}
  controls(){if(this.adoptButton)this.adoptButton.disabled=!this.canAdopt();}
  adopt(){if(!this.canAdopt())return;this.owner.ui.input.value=this.proposal.suggested_draft;this.owner.ui.input.dispatchEvent(new Event('input',{bubbles:true}));this.owner.ui.status.textContent='已采用系统建议到本页编辑区；仍未审核，尚未保存。模型原稿和系统建议记录均保留。';this.controls();}
  valid(p,original){
   const object=v=>v&&typeof v==='object'&&!Array.isArray(v),hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),id=(v,prefix)=>typeof v==='string'&&new RegExp('^'+prefix+'-[a-f0-9]{24}$').test(v);
   const slot=v=>object(v)&&['known','unknown','ambiguous','conflict'].includes(v.state)&&Array.isArray(v.evidence)&&v.evidence.every(e=>object(e)&&typeof e.quote==='string'&&typeof e.source==='string');
   if(!object(p)||p.protocol!=='reply-quality-proposal-v1'||p.origin!=='system_suggestion'||p.model_output_preserved!==true||p.source_field!=='original.draft'||p.semantic_correctness_validated!==false||!id(p.id,'rqp')||!hash(p.context_sha256)||!hash(p.original_sha256)||!hash(p.derived_sha256)||!(p.suggested_draft===null||typeof p.suggested_draft==='string')||!Array.isArray(p.suggestions)||!Array.isArray(p.changes)||!object(p.slots)||!Object.values(p.slots).every(slot)||!Array.isArray(p.other_quantity_evidence)||!p.other_quantity_evidence.every(object))return false;
   const groups=new Map(),logs=new Map(),length=Array.from(original).length;
   for(const s of p.suggestions){if(!object(s)||s.origin!=='system_suggestion'||!id(s.suggestion_id,'rqs')||s.id!==s.suggestion_id||typeof s.rule!=='string'||typeof s.text!=='string'||!slot(s.slot_state))return false;const previous=groups.get(s.suggestion_id);if(previous&&JSON.stringify(previous)!==JSON.stringify(s))return false;groups.set(s.suggestion_id,s);}
   for(const c of p.changes){const span=c?.source_span;if(!object(c)||c.origin!=='system_suggestion'||!id(c.id,'rqc')||!id(c.suggestion_id,'rqs')||typeof c.rule!=='string'||!['insert','remove_exact_question'].includes(c.operation)||!object(span)||!Number.isInteger(span.start)||!Number.isInteger(span.end)||span.start<0||span.end<span.start||span.end>length||(c.operation==='insert'&&typeof c.added_text!=='string')||(c.operation==='remove_exact_question'&&typeof c.removed_text!=='string')||(c.slot_state!==undefined&&!slot(c.slot_state)))return false;const previous=logs.get(c.id);if(previous&&JSON.stringify(previous)!==JSON.stringify(c))return false;logs.set(c.id,c);}
   return true;
  }
  show(value){
   this.clear();const epoch=this.epoch;this.row=structuredClone(value);const p=this.row.review.reply_quality_proposal;this.proposal=p;
   this.host.append(this.node('h4','系统补充建议（未审核）'),this.node('p','来源：system_suggestion · 系统派生预览，不是模型原稿、已知事实或引用证明。默认不应用，也不代表批准或发送。','reply-quality-boundary'));
   const count=Array.isArray(value.review.question_checks)?value.review.question_checks.length:0;if(value.review.review_unit_label==='待核对原文片段')this.host.append(this.node('p','待核对原文片段 · '+count+' 段；片段数不代表已回答的语义问题数。','reply-quality-unit'));
   if(p===undefined||p===null){this.host.append(this.node('p','此稿未提供系统补充建议，模型原稿保留。'));return;}
   if(!this.valid(p,value.original.draft)){this.host.append(this.node('p','建议格式暂不支持；保留模型原稿，不采用到编辑区。','reply-quality-unsupported'));return;}
   const groups=new Map();for(const suggestion of p.suggestions)if(!groups.has(suggestion.suggestion_id))groups.set(suggestion.suggestion_id,{suggestion,changes:[]});
   const seenLogs=new Set();for(const change of p.changes){if(seenLogs.has(change.id))continue;seenLogs.add(change.id);if(!groups.has(change.suggestion_id))groups.set(change.suggestion_id,{suggestion:null,changes:[]});groups.get(change.suggestion_id).changes.push(change);}
   const states={known:'输入明确陈述',unknown:'未知',ambiguous:'含义不明确',conflict:'输入冲突'},fields={scenario:'询盘场景',process:'加工工艺',fixture_quantity:'夹具数量',drawing:'图纸',dimensions:'尺寸',interface:'接口',machine:'机型',accuracy:'精度要求',measurement_method:'测量方法'};
   const slot=(host,label,value)=>{host.append(this.node('p',label+' · '+states[value.state]+(value.state==='known'?'（未独立核实）':'')));if(value.value!==null&&value.value!==undefined)host.append(this.node('p','输入值：'+plain(value.value)));for(const e of value.evidence){host.append(this.node('p','来源：'+e.source+' / '+(e.field??e.record_id??'未指定字段')+' · 原文码点偏移 '+(e.start??'未知')+'–'+(e.end??'未知'),'reply-quality-source'),this.node('p',e.quote,'reply-quality-quote'));}};
   const list=this.node('ol',null,'reply-quality-list');for(const [id,group] of groups){const row=this.node('li',null,'reply-quality-row');row.dataset.suggestionId=id;row.dataset.dedupScope=this.key(value,p);row.append(this.node('p',id+' · system_suggestion','reply-quality-source'));const first=group.changes[0];this.text(row,group.suggestion?.text??(first.operation==='remove_exact_question'?'删除重复固定追问建议：\n'+first.removed_text:first.added_text));const state=group.suggestion?.slot_state??first?.slot_state;if(state){const d=this.node('details');d.append(this.node('summary','查看输入状态与准确原文（非已核实事实）'));slot(d,'关联输入',state);row.append(d);}if(group.changes.length)this.detail(row,'查看操作日志 · '+group.changes.length+' 条（原稿码点偏移）',group.changes);list.append(row);}
   this.host.append(list);if(!groups.size)this.host.append(this.node('p','当前没有新增逻辑建议；仍未审核。'));
   const slots=this.node('details',null,'reply-quality-slots');slots.append(this.node('summary','查看各字段状态与来源'));for(const [name,state] of Object.entries(p.slots)){const card=this.node('section');slot(card,fields[name]||name,state);slots.append(card);}this.host.append(slots);
   if(p.other_quantity_evidence.length)this.detail(this.host,'其他对象数量原文（未解析，不是夹具数量事实）',p.other_quantity_evidence);
   this.detail(this.host,'查看系统建议追溯标识与文本哈希（非授权凭据）',{proposal_id:p.id,context_sha256:p.context_sha256,original_sha256:p.original_sha256,derived_sha256:p.derived_sha256,source_field:p.source_field,origin:p.origin});
   if(typeof p.suggested_draft==='string'){this.detail(this.host,'查看建议稿 · 未接受 · 未审核',p.suggested_draft).className='reply-quality-preview';this.adoptButton=this.node('button','采用到编辑区（仍未审核）');this.adoptButton.type='button';this.adoptButton.id='review-proposal-adopt';this.adoptButton.disabled=true;this.adoptButton.onclick=()=>this.adopt();this.host.append(this.adoptButton);this.host.append(this.node('p','有已保存或未保存的用户编辑时，仅查看并手动合并；采用不会自动保存。超过6000字的建议不能直接采用，不静默截断。','reply-quality-boundary'));}
   this.hashNotice=this.node('p','正在核对原稿及派生稿 UTF-8 文本哈希；尚未允许采用。','reply-quality-hash');this.host.append(this.hashNotice);
   const sha=async text=>{const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('');};
   Promise.all([sha(value.original.draft),sha(p.suggested_draft??value.original.draft)]).then(([original,derived])=>{if(epoch!==this.epoch||!this.current())return;this.hashesMatch=original===p.original_sha256&&derived===p.derived_sha256;this.hashNotice.textContent=this.hashesMatch?'文本哈希一致；系统建议仍未审核，不证明语义正确。':'文本哈希不匹配：建议已过期或待核，不能采用到编辑区。';this.controls();}).catch(()=>{if(epoch!==this.epoch||!this.current())return;this.hashNotice.textContent='无法核对文本哈希；建议保持待核，不能采用到编辑区。';this.controls();});
  }
 }

 function valid(value){
  if(!value||value.protocol!==protocol||value.status!=='needs_review'||typeof value.customer_id!=='string'||!idPattern.test(value.draft_id)||!Number.isInteger(value.draft_version)||value.draft_version<1||value.approved!==false||value.unsent!==true||value.evidence_status!=='unreviewed'||typeof value.edited!=='boolean')return false;
  const p=value.permissions;if(!p||p.view!==true||['send','approve','approved_export','publish'].some(k=>p[k]!==false)||typeof p.edit!=='boolean'||typeof p.discard!=='boolean')return false;
  const o=value.original,r=value.review,s=value.scope;if(!o||!r||!s||['draft','match','angle','next_step','unknowns','stage_blocker','notice'].some(k=>typeof o[k]!=='string')||!['questions','hypotheses','facts'].every(k=>Array.isArray(o[k])))return false;
  if(!(value.edited_draft===null||typeof value.edited_draft==='string')||Array.from(value.edited_draft??o.draft).length>6000||value.edited!==(value.edited_draft!==null))return false;
  const objects=items=>items.every(v=>v&&typeof v==='object'&&!Array.isArray(v));
  for(const k of ['unresolved_questions','question_checks'])if(r[k]!==undefined&&(!Array.isArray(r[k])||!objects(r[k])||r[k].some(q=>typeof q.question_id!=='string'||!Number.isInteger(q.ordinal)||q.ordinal<1||typeof q.text_verbatim!=='string'||(k==='unresolved_questions'&&typeof q.reason!=='string')||(k==='question_checks'&&(!Array.isArray(q.supported_transcriptions)||!objects(q.supported_transcriptions))))))return false;
  if(r.coverage_complete!==undefined&&r.coverage_complete!==false)return false;
  return Number.isInteger(s.customer_version)&&Number.isInteger(s.profile_revision)&&typeof s.context_token==='string'&&typeof s.question==='string'&&typeof s.request_id==='string'&&(s.prepare_id===null||typeof s.prepare_id==='string')&&['supported_transcriptions','source_catalog','unknowns','issues'].every(k=>Array.isArray(r[k])&&objects(r[k]))&&r.request_fully_semantically_validated===false&&r.edited_content_fact_checked===false&&Number.isFinite(Date.parse(value.expires_at));
 }
 class DealReviewDraftController {
  constructor(host,bridge){
   this.bridge=bridge;this.ui=new NeedsReviewDraft(host);this.row=null;this.scope=null;this.localStamp='';this.serial=0;this.inputEpoch=0;this.active=null;this.blocked=false;this.expiry=null;
   this.message=document.createElement('p');this.message.className='needs-review-message';this.message.role='status';this.message.setAttribute('aria-live','polite');host.append(this.message);
   this.proposal=new SystemReplyProposal(this);this.ui.original.after(this.proposal.host);
   this.lifetime=document.createElement('p');this.lifetime.className='needs-review-lifetime';this.ui.title.after(this.lifetime);
   this.refresh=this.button('重新核对当前稿');this.refresh.id='review-draft-refresh';this.discard=this.button('丢弃待核稿');this.discard.id='review-draft-discard';this.ui.save.id='review-draft-save';this.ui.input.id='review-draft-input';this.ui.input.maxLength=12000;
   this.ui.reset.id='review-draft-reset';this.ui.panel.querySelector('.needs-review-actions').append(this.refresh,this.discard);
   this.ui.canSave=()=>this.current()&&!this.blocked&&this.row.permissions.edit&&editableText(this.ui.input.value);
   this.ui.changed=()=>{this.inputEpoch++;if(!editableText(this.ui.input.value))this.ui.status.textContent='编辑最多 6000 字，不能含非文本控制字符；当前内容未发送。';this.controls();};this.ui.save.onclick=()=>this.work('edit');this.discard.onclick=()=>this.work('discard');this.refresh.onclick=()=>this.work('view');
   const reset=this.ui.reset.onclick;this.ui.reset.onclick=()=>{reset();this.blocked=false;this.controls();};this.ui.dismiss.onclick=()=>this.clear();
   this.ui.boundary.textContent='编辑最多 6000 字，仍未审核，不改变原文出处或核验状态。本待核稿不提供 TXT 下载；批准导出、批准和发送保持关闭。';
   window.addEventListener('focus',()=>{if(this.row&&!this.active)this.work('view');});window.addEventListener('pagehide',()=>this.clear());window.addEventListener('pageshow',e=>{if(e.persisted)this.clear();});
  }
  button(label){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;return b;}
  current(){return !!this.row&&this.localStamp===this.bridge.stamp()&&this.row.customer_id===this.bridge.customer()?.id&&this.row.scope.customer_version===this.bridge.customer()?.version;}
  controls(){const current=this.current(),busy=!!this.active;this.ui.busy=busy;this.ui.controls();this.ui.input.readOnly=!current||!this.row?.permissions.edit;this.refresh.disabled=!current||busy;this.discard.disabled=!current||busy||!this.row?.permissions.discard;this.ui.dismiss.disabled=false;this.proposal?.controls();}
  clear(message=''){this.serial++;this.active?.controller.abort();this.active=null;clearTimeout(this.expiry);this.expiry=null;this.row=null;this.scope=null;this.blocked=false;this.localStamp='';this.proposal.clear();this.ui.clear();this.lifetime.textContent='';this.message.textContent=message;}
  accepted(value,expected){return valid(value)&&value.customer_id===expected.customer_id&&value.scope.customer_version===expected.customer_version&&value.scope.profile_revision===expected.revision&&value.scope.context_token===expected.context_token&&value.scope.request_id===expected.request_id&&value.scope.prepare_id===(expected.prepare_id??null)&&value.scope.question===expected.question;}
  accept(value,expected){
   if(!this.accepted(value,expected)||value.customer_id!==this.bridge.customer()?.id||value.scope.customer_version!==this.bridge.customer()?.version)throw Error('待核响应范围或权限标记不匹配，未展示。');
   this.clear();this.scope={...expected};this.localStamp=this.bridge.stamp();this.apply(value);return true;
  }
  model(value){
   const o=value.original,r=value.review;
   const sections=[['回复原文',o.draft],['匹配建议（未核实）',o.match],['沟通切入建议',o.angle],['下一步建议',o.next_step],['原始未知项',o.unknowns],['阶段卡点建议',o.stage_blocker],['原始问题',o.questions],['原始推测',o.hypotheses],['原始事实建议与出处（非独立证明）',o.facts],['原始提示',o.notice]];
   const fields={pressure:'压力',air_interface:'气路接口'},media={air:'气动',hydraulic:'液压'},qualifiers={operating:'工作',supply:'供压'};
   const evidence=r.supported_transcriptions.map(v=>({kind:'transcription',label:['字段原文转录',v.model,fields[v.field]||v.field,media[v.medium]||v.medium,qualifiers[v.qualifier]||v.qualifier].filter(x=>typeof x==='string'&&x).join(' · '),quote:typeof v.quote==='string'?v.quote:plain(v),location:'只核对对应型号、字段与条件，不表示整稿已核实。',detail:plain(v)}));
   for(const source of r.source_catalog)evidence.push({kind:'catalog',label:'本次选定出处'+(typeof source.ref_id==='string'?' · '+source.ref_id:''),quote:typeof source.quote==='string'?source.quote:plain(source),location:'出处只支持对应引用范围，不表示整段建议已核实。',detail:plain(source)});
   const checks=Array.isArray(r.question_checks)?r.question_checks:[];
   const pendingQuestions=(Array.isArray(r.unresolved_questions)?r.unresolved_questions:[]).map(q=>{
    const matches=checks.filter(c=>c.question_id===q.question_id&&c.ordinal===q.ordinal&&c.text_verbatim===q.text_verbatim);
    const tuples=matches.length===1&&Array.isArray(matches[0].supported_transcriptions)?matches[0].supported_transcriptions:[];
    return {label:String(q.ordinal)+' · '+q.question_id+' · 尚未完成核对',text:q.text_verbatim,reason:q.reason,evidence:tuples.map(t=>({label:['原文出处',t.ref_id||t.source_id,t.document_id,Number.isInteger(t.version)?'v'+t.version:null,t.chunk_id].filter(Boolean).join(' · '),quote:typeof t.quote==='string'?t.quote:plain(t),detail:plain(t)}))};
   });
   if(!Array.isArray(r.unresolved_questions)&&r.unparsed_question_scope)pendingQuestions.push({label:'原问题范围 · 尚未完成核对',text:plain(r.unparsed_question_scope),reason:'旧稿未提供逐问核对记录，请对照原问题人工确认。',evidence:[]});
   const paths={draft:'回复原文',match:'匹配建议',angle:'沟通建议',next_step:'下一步建议',unknowns:'未知项',stage_blocker:'卡点建议',edited_draft:'编辑回复'};
   const reasons=[...r.unknowns.map(v=>typeof v==='string'?v:[v.model,fields[v.field]||v.field,v.state,v.reason].filter(x=>x!==null&&x!==undefined).map(plain).join(' · ')||plain(v)),...r.issues.map(v=>[paths[v.path]||v.path,v.reason,v.original_text].filter(x=>x!==null&&x!==undefined).map(plain).join(' · '))];
   if(r.unparsed_question_scope)reasons.push('未解析的问题范围：'+plain(r.unparsed_question_scope));reasons.push('原问题尚未完成完整语义核验；编辑内容未经过事实核实。');
   return {scope:{customer:value.customer_id,sessionEpoch:this.localStamp,revisionEpoch:String(value.draft_version)},originalText:sections.map(([label,v])=>label+'\n'+plain(v)).join('\n\n'),savedText:value.edited_draft??o.draft,evidence,reasons,pendingQuestions,reviewUnitLabel:r.review_unit_label==='待核对原文片段'?'待核对原文片段':null,reasonRecords:plain({unknowns:r.unknowns,issues:r.issues,unparsed_question_scope:r.unparsed_question_scope,request_fully_semantically_validated:r.request_fully_semantically_validated,edited_content_fact_checked:r.edited_content_fact_checked})};
  }
  apply(value,preserve=null){
   if(Date.parse(value.expires_at)<=Date.now()){this.clear(errorLabels.review_draft_expired);return;}
   this.row=structuredClone(value);this.ui.clear();const model=this.model(value);this.ui.setContext(model.scope);this.ui.show(model);this.ui.title.textContent='待核草稿 v'+value.draft_version+' · 未审核';this.proposal.show(value);this.ui.status.textContent=value.edited?'已保存本次编辑；仍未审核，未改变原始建议和证据状态。':'已取回原始待核草稿；仍未审核，未发送。';
   this.lifetime.textContent='临时待核稿，到期时间：'+new Date(value.expires_at).toISOString().replace('T',' ').slice(0,19)+' UTC';
   if(preserve!==null&&preserve!==this.ui.value.savedText){this.ui.input.value=preserve;this.blocked=true;this.ui.status.textContent='已核对服务器当前版本；本页修改保留但尚未保存。请先丢弃本次编辑，再基于当前版本修改。';}
   clearTimeout(this.expiry);this.expiry=setTimeout(()=>this.clear(errorLabels.review_draft_expired),Math.min(2147483647,Date.parse(value.expires_at)-Date.now()));this.controls();
  }
  async work(kind){
   if(this.active)return;if(!this.current()){this.clear('当前客户或资料范围已变化，待核展示已清除。');return;}
   if(kind==='edit'&&(!this.row.permissions.edit||this.blocked||this.ui.input.value===this.ui.value.savedText||!editableText(this.ui.input.value)))return;
   if(kind==='discard'&&!this.row.permissions.discard)return;
   const op={serial:++this.serial,stamp:this.localStamp,customer:this.row.customer_id,id:this.row.draft_id,version:this.row.draft_version,controller:new AbortController(),input:this.ui.input.value,inputEpoch:this.inputEpoch};this.active=op;this.message.textContent='';this.controls();
   const accepts=()=>this.active===op&&op.serial===this.serial&&this.current()&&op.stamp===this.bridge.stamp()&&!op.controller.signal.aborted;
   try{
    const base={customer_id:op.customer,draft_id:op.id,expected_draft_version:op.version};
    const url='/api/deals/rag/review-drafts/'+kind;
    const result=await this.bridge.api(kind==='view'?url+'?customer_id='+encodeURIComponent(op.customer)+'&draft_id='+encodeURIComponent(op.id):url,kind==='view'?null:kind==='edit'?{...base,draft:op.input}:base,op.controller.signal);
    if(!accepts())return;
    if(kind==='discard'){if(result.protocol!==protocol||result.status!=='discarded'||result.customer_id!==op.customer||result.draft_id!==op.id)throw Error('丢弃回执不匹配，未清除当前稿。');this.clear('待核稿已由服务器丢弃，未发送。');return;}
    const proposalImmutable=kind!=='edit'||['reply_quality_proposal','review_unit_label','semantic_question_count'].every(k=>JSON.stringify(result.review?.[k])===JSON.stringify(this.row.review[k]));
    const immutable=proposalImmutable&&JSON.stringify(result.original)===JSON.stringify(this.row.original)&&['supported_transcriptions','source_catalog','unknowns','unparsed_question_scope','unresolved_questions','question_checks','coverage_complete','request_check_status','missing_scope','question_split_scope'].every(k=>JSON.stringify(result.review?.[k])===JSON.stringify(this.row.review[k]));
    if(!this.accepted(result,this.scope)||!immutable||result.draft_id!==op.id||(kind==='edit'&&(result.draft_version!==op.version+1||result.edited_draft!==op.input||!result.edited))||(kind==='view'&&result.draft_version<op.version))throw Error('待核回执版本、原始内容或范围不匹配，未覆盖本页文本。');
    const currentInput=this.ui.input.value,dirty=currentInput!==this.ui.value.savedText,lateEdit=kind==='edit'&&this.inputEpoch!==op.inputEpoch;this.active=null;this.blocked=false;this.apply(result,(kind==='view'&&dirty)||lateEdit?currentInput:null);
   }catch(e){if(!accepts())return;if([403,404,410].includes(e.status)||e.code==='review_context_changed'){this.clear(errorLabels[e.code]||'当前会话已无法访问此待核稿，已清除。');return;}if(e.code==='review_draft_conflict')this.blocked=true;this.ui.status.textContent=errorLabels[e.code]||e.message||'待核操作未完成，修改仍保留在本页。';}
   finally{if(this.active===op){this.active=null;if(!this.current())this.clear('范围已变化，旧响应未写入当前页面。');else this.controls();}}
  }
 }
 window.DealReviewDraftController=DealReviewDraftController;
})();

if(!window.KnowledgeSelection)window.KnowledgeSelection=class {
  constructor(host,api,changed){this.api=api;this.changed=changed;this.items=[];this.selected=new Map();this.serial=0;this.cid='';this.box=document.createElement('details');const summary=document.createElement('summary');summary.textContent='选择工厂知识资料（可选，默认不发送）';const note=document.createElement('p');note.textContent='最多选择6个片段。原文核对只覆盖列明字段，仍是企业自述，不证明技术能力、认证或客户需求。未核实、过期和冲突资料需人工核对。';this.query=document.createElement('input');this.query.placeholder='本地关键词，例如 CNC';this.query.maxLength=200;this.query.setAttribute('aria-label','知识资料关键词');const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='检索本地资料';button.onclick=()=>this.search();this.status=document.createElement('p');this.list=document.createElement('div');const link=document.createElement('a');link.href='/knowledge';link.target='_blank';link.rel='noopener';link.textContent='管理工厂知识库 ↗';this.box.append(summary,note,this.query,button,this.status,this.list,link);host.append(this.box);}
  setCustomer(cid){if(this.cid===cid)return;this.cid=cid;this.serial++;this.selected.clear();this.list.replaceChildren();this.status.textContent='未选择工厂资料；检索只在本机进行。';}
  selections(){return [...this.selected.values()].map(x=>({document_id:x.document_id,version:x.version,chunk_id:x.chunk_id}));}
  async search(){const serial=++this.serial;this.status.textContent='正在本机检索…';try{const r=await this.api('/api/knowledge/search?q='+encodeURIComponent(this.query.value));if(serial!==this.serial)return;this.list.replaceChildren();this.status.textContent=r.items.length?`返回 ${r.items.length} 个片段；已选 ${this.selected.size}/6`:'没有匹配资料；不自动扩大范围。';for(const item of r.items){const label=document.createElement('label');label.className='preserve';label.style.display='block';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=this.selected.has(item.chunk_id);const text=document.createElement('span');text.textContent=` ${item.title} / ${item.source_company||'所属主体未知'} / ${item.filename} / v${item.version} / ${JSON.stringify(item.location)} / ${item.review_label||item.review_status}${item.expired?' / 已过期':''}${item.conflict?' / 可能冲突':''}\n${item.text}\n`;checkbox.onchange=()=>{if(checkbox.checked&&this.selected.size>=6){checkbox.checked=false;this.status.textContent='最多选择6个片段';return;}if(checkbox.checked)this.selected.set(item.chunk_id,item);else this.selected.delete(item.chunk_id);this.status.textContent=`已选 ${this.selected.size}/6；需重新预览并确认发送范围`;this.changed();};label.append(checkbox,text);this.list.append(label);}}catch(e){if(serial===this.serial)this.status.textContent='知识库不可用：'+e.message;}}
  static preview(value){const items=value.knowledge?.items||[];return '\n\n工厂知识资料：'+(items.length?items.map(x=>`${x.title} / ${x.source_company||'所属主体未知'} / ${x.filename} / v${x.version} / ${JSON.stringify(x.location)} / ${x.review_label||x.review_status}${x.expired?' / 已过期':''}${x.conflict?' / 可能冲突':''}\n${x.review_info?'核对范围：'+x.review_info.reviewed_fields.join('、')+'；'+x.review_info.scope_notice+'\n':''}${x.text}`).join('\n\n'):'未选择，不发送知识库资料。');}
};

if(!window.AnalysisHistory){
'use strict';
// Shared business history. All model/user text is rendered as text, never HTML.
window.AnalysisHistory = class {
  constructor(host,module,api,isDirty=()=>false) {
    this.host=host;this.module=module;this.api=api;this.isDirty=isDirty;this.cid='';this.serial=0;this.page=1;
    this.details=document.createElement('details');
    const title=document.createElement('summary');title.textContent='分析历史 · 本机保存 · 草稿未发送';
    this.status=document.createElement('p');this.status.className='muted';
    this.refresh=document.createElement('button');this.refresh.type='button';this.refresh.className='secondary';this.refresh.textContent='刷新分析历史';this.refresh.onclick=()=>this.load();
    this.list=document.createElement('div');this.nav=document.createElement('div');
    this.details.append(title,this.status,this.refresh,this.list,this.nav);host.append(this.details);
  }
  setCustomer(cid) {this.cid=cid;this.page=1;this.serial++;this.list.replaceChildren();this.nav.replaceChildren();this.host.hidden=!cid;if(cid)this.load();}
  markHistorical() {this.status.textContent='当前输入有修改；下面结果来自已保存输入，属于历史参考。请先保存再重新分析。';for(const x of this.list.querySelectorAll('[data-history-status]'))x.textContent='历史参考 · 当前输入已有修改';}
  async load() {
    const cid=this.cid,serial=++this.serial,page=this.page;if(!cid)return;
    this.status.textContent='正在读取本机分析历史…';
    try {
      const data=await this.api(`/api/analyses?customer_id=${encodeURIComponent(cid)}&module=${this.module}&page=${page}&page_size=10`);
      if(serial!==this.serial||cid!==this.cid)return;
      this.list.replaceChildren();this.nav.replaceChildren();this.status.textContent=`共 ${data.total} 次分析 · 本模块 · 第 ${page}/${data.pages} 页。历史未留存的正文无法补回。`;
      for(const row of data.items)this.render(row);
      if(!data.items.length)this.list.textContent='暂无已留存分析。生成后会保存到本机；原来只有调用日志的结果不能恢复。';
      if(data.pages>1)for(const [label,delta] of [['上一页',-1],['下一页',1]]){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;b.disabled=page+delta<1||page+delta>data.pages;b.onclick=()=>{this.page+=delta;this.load();};this.nav.append(b);}
    } catch(e) {if(serial===this.serial){this.status.textContent=e.status===404?'当前运行的后端尚未启用分析历史；页面仍可分析，但本次结果可能未留存。需更新并重启后端，内存中的 Key 会清除。':'分析历史暂不可用：'+e.message;this.list.replaceChildren();}}
  }
  render(row) {
    const item=document.createElement('details');item.dataset.analysisId=row.id;item.className='advice-details';
    const title=document.createElement('summary');title.textContent=`${new Date(row.created_at).toLocaleString('zh-CN')} · ${row.mode==='mock'?'本地规则演示':row.provider+' / '+row.model} · ${row.status==='ok'?'已保存':row.status==='failed'?'失败':'未确认完成'}`;
    const state=document.createElement('p');state.dataset.historyStatus='';state.textContent=row.historical||this.isDirty()?'历史参考 · 输入已变更，未覆盖新资料':'基于当前已保存输入 · 仍需人工核实';
    const meta=document.createElement('p');meta.className='muted preserve';meta.textContent=`客户 ${row.customer_id} · ${this.module==='development'?'客户开发':'成交推进'} · 输入客户 v${row.customer_version} / 连接 v${row.profile_revision}\n输入阶段：${row.input_stage}；卡点：${row.input_blocker||'未填写'}\n本次 ${row.input_scope.record_count}/${row.input_scope.records_total} 条记录，${row.input_scope.input_bytes} 字节；记录ID：${row.input_scope.record_ids.join('、')||'无'}\n范围：客户 ${row.input_scope.customer_fields.join('、')}；工厂 ${row.input_scope.factory_fields.join('、')}；记录 ${row.input_scope.record_fields.join('、')}\n完成：${row.completed_at||'未知（进程中断时不会自动重发）'} · 草稿始终未发送`;
    const scope=document.createElement('details'),scopeTitle=document.createElement('summary');scopeTitle.textContent='输入范围、版本与时间';scope.append(scopeTitle,meta);
    item.append(title,state,scope);
    if(row.status!=='ok'){const p=document.createElement('p');p.textContent=row.error_summary||'请求尚未确认完成；请核对后再决定是否发起新请求。没有可用生成正文。';item.append(p);this.list.append(item);return;}
    const r=row.result,body=document.createElement('p');body.className='preserve';body.textContent=`匹配：${r.match}\n切入角度：${r.angle}\n阶段理由 / 卡点：${r.stage_blocker}\n下一步：${r.next_step}\n\n事实原文引用：\n${r.facts.map(f=>f.statement+'\n'+f.evidence.map(e=>`${e.source} / ${e.source==='knowledge'?e.title+' / '+(e.source_company||'所属主体未知')+' / v'+e.version+' / '+JSON.stringify(e.location)+' / '+(e.review_label||e.review_status||'需核实')+'（非独立证明）':e.field||e.record_id}：${e.quote}`).join('\n')).join('\n\n')||'无具备引用的事实'}\n\n推断（未核实）：${r.hypotheses.join('；')||'无'}\n未知：${r.unknowns}\n待核实：${r.questions.join('；')||'无'}\n${r.notice}`;
    const original=document.createElement('details'),origTitle=document.createElement('summary'),origText=document.createElement('p');origTitle.textContent='原始草稿 · 未发送';origText.className='preserve';origText.textContent=r.draft;original.append(origTitle,origText);
    const label=document.createElement('label');label.textContent='人工编辑草稿 · 未发送';const draft=document.createElement('textarea');draft.rows=8;draft.maxLength=12000;draft.value=row.edited_draft??r.draft;label.append(draft);
    const feedback=document.createElement('p');feedback.className='muted';feedback.textContent=row.edited_draft===null?'尚未保存人工编辑稿':`人工编辑 v${row.draft_version} · ${row.draft_updated_at}`;
    const save=document.createElement('button');save.type='button';save.className='secondary';save.textContent='保存人工编辑稿到分析历史';let busy=false;
    save.onclick=async()=>{
      if(busy)return;if(!draft.value.trim()){feedback.textContent='草稿不能为空';return;}
      busy=true;save.disabled=true;draft.disabled=true;const cid=this.cid,serial=this.serial;
      try {const saved=await this.api('/api/analysis/draft',{customer_id:row.customer_id,module:this.module,analysis_id:row.id,draft:draft.value,draft_version:row.draft_version});if(cid!==this.cid||serial!==this.serial)return;row.draft_version=saved.draft_version;row.edited_draft=saved.edited_draft;feedback.textContent=`已保存人工编辑 v${saved.draft_version} · 未发送`;} catch(e){feedback.textContent=`保存未确认：${e.message}。输入已保留，请刷新历史核对版本。`;} finally {busy=false;save.disabled=false;draft.disabled=false;}
    };
    item.append(body,original,label,save,feedback);this.list.append(item);
  }
};

}
'use strict';
(() => {
  const $ = s => document.querySelector(s);
  const stages = ['待开发','已联系','需求确认','报价沟通','样品验证','商务谈判','成交','暂缓'];
  const buffers = new Map();
  const knowledgePicker=new KnowledgeSelection($('.rag-advanced'),api,()=>invalidateAnalysis());
  knowledgePicker.box.querySelector('summary').textContent='搜索并手动选择片段（高级）';
  let ragPrepared=null, initialModeSet=false;
  const question=()=>$('#rag-question').value.trim();
  const analysisHistory=new AnalysisHistory($('#analysis-history'),'deals',api,()=>!!customer()&&($('#stage-select').value!==customer().stage||$('#blocker').value!==(customer().blocker||'')||!!$('#record-text').value.trim()));
  let currentAnalysis=null;
  let state = null, selected = '', revision = 0, writing = false, analyzing = false, loading = false;
  let analysisController = null, draftSaved = '', uncertainRecord = null;
  let modelProfile = null, modelContext = null, contextController = null, contextSerial = 0, contextLoading = false;
  const realMode = () => $('#analysis-provider').value === 'model';
  const providerLabels = {deepseek:'DeepSeek','qwen-cn':'千问 · 北京','kimi-cn':'Kimi · 国内','kimi-global':'Kimi · 国际','glm-cn':'智谱 · 中国','doubao-cn':'豆包 · 北京','minimax-cn':'MiniMax · 中国',custom:'自定义 OpenAI 兼容'};
  const modelErrors = {deal_sources_changed:'资料版本、审核或权限已变化，旧预览失效，请重新检索。',deal_prepare_expired:'资料预览已过期，请重新检索并同意。',deal_scope_mismatch:'预览客户或会话已变化，请重新检索。',provider_auth:'供应商认证失败（401），请检查凭据与目标地址。',provider_forbidden:'供应商拒绝访问（403），请检查账号权限、区域或策略限制。',model_not_found:'模型不存在或不可用（404），请核对模型 ID。',provider_rate_limit:'供应商限流或额度限制（429），请稍后重试。',provider_timeout:'供应商响应超时，请稍后重试。',profile_changed:'模型连接已改变，请刷新发送范围并重新确认。',context_changed:'客户、工厂或相关记录已改变，请刷新发送范围并重新确认。',context_too_large:'客户上下文超过输入上限，请整理资料后重新预览；没有静默删减证据。',key_required:'未配置密钥，请前往模型设置。',model_required:'未选择默认模型，请前往模型设置。',demo_data_forbidden:'演示环境或虚构客户不能发送给真实模型。',invalid_response:'模型输出格式无效，本次没有显示生成结果。',invalid_evidence:'模型引用未通过本机校验，没有可用建议。请查看分析历史中的安全原因；不要连续付费重试。',unsafe_output:'模型输出未通过校验，本次未显示结果。',provider_unavailable:'供应商服务暂不可用，请稍后重试。'};
  const modelErrorText = e => modelErrors[e.code] || ({401:modelErrors.provider_auth,403:modelErrors.provider_forbidden,404:modelErrors.model_not_found,429:modelErrors.provider_rate_limit,504:modelErrors.provider_timeout}[e.status]) || '模型请求失败，请核对本机服务和模型设置后重试；没有回退为 demo。';
  const initialUrl = new URL(location.href);
  const hashParams = new URLSearchParams(initialUrl.hash.slice(1));
  let requested = initialUrl.searchParams.get('customer_id') || initialUrl.searchParams.get('customer') || hashParams.get('customer_id') || hashParams.get('customer') || '';
  const customer = () => state?.customers.find(c => c.id === selected);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function toast(message) { $('#toast').textContent = message; $('#toast').style.display = 'block'; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').style.display = 'none', 7000); }
  async function api(path, data, signal, historyProof) {
    const controller = new AbortController();
    const cancel = () => controller.abort();
    if (signal) { if (signal.aborted) controller.abort(); else signal.addEventListener('abort', cancel, {once:true}); }
    const timer = setTimeout(cancel, path.startsWith('/api/model/') || path === '/api/analyze' || path === '/api/deals/rag/generate' || path === '/api/deals/structured/generate' ? 65000 : path === '/api/deals/rag/prepare' ? 195000 : 20000);
    try {
      const headers={};
      if(historyProof){if(!['/api/deals/structured/history','/api/deals/structured/history-view','/api/deals/structured/history-access/meta','/api/deals/structured/history-access/disconnect'].includes(path.split('?')[0])||! /^[A-Za-z0-9_-]{43}$/.test(historyProof))throw Error('History proof scope invalid');headers['X-History-Proof']=historyProof;}
      if(data)Object.assign(headers,{'Content-Type':'application/json','X-CSRF-Token':state.csrf});
      const response = await fetch(path, {cache:'no-store', signal:controller.signal,headers, ...(data ? {method:'POST',body:JSON.stringify(data)} : {})});
      let value; try { value = await response.json(); } catch (_) { throw Error('服务返回了无法读取的内容，请检查本地服务。'); }
      if (!response.ok) {
        const error = Error(value.error || `请求失败（${response.status}）`);
        error.status = response.status; error.code = value.code || ''; throw error;
      }
      return value;
    } catch(e) {
      if (e.name === 'AbortError') throw Error('请求已取消或超时。保存请求可能已到达服务器，请刷新记录后核对再重试。');
      if (e instanceof TypeError) throw Error('无法连接本地服务，请确认服务已启动。保存请求请先刷新核对，再重试。');
      throw e;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
  }
  const reviewStamp=()=>JSON.stringify({customer:selected,epoch:revision,customerVersion:customer()?.version,modelRevision:modelProfile?.revision,csrf:state?.csrf,factory:state?.factory,knowledge:knowledgePicker.selections(),question:question(),stage:$('#stage-select').value,blocker:$('#blocker').value,record:$('#record-text').value,manual:$('#rag-manual').checked});
  const needsReview=new window.DealReviewDraftController($('.advice-panel'),{api,customer,stamp:reviewStamp});
  Object.assign(modelErrors,{review_scope_mismatch:'当前会话或客户范围已变化，请刷新后重新预览。',review_context_changed:'客户、资料或模型版本已变化，请重新预览。',review_draft_capacity:'临时待核稿数量已达上限，请丢弃不再需要的稿或等待到期。',invalid_review_input:'待核稿输入不符合约定，未进行保存。'});
  let reviewPageActive=true,reviewPageEpoch=0;
  window.addEventListener('pagehide',()=>{reviewPageActive=false;reviewPageEpoch++;analysisController?.abort();needsReview.clear();});
  window.addEventListener('pageshow',event=>{if(event.persisted){reviewPageActive=true;reviewPageEpoch++;needsReview.clear();}});
  window.addEventListener('deal-context-invalidated',()=>needsReview.clear());
  for(const selector of ['#stage-form','#record-form','#rag-question','#rag-manual'])$(selector).addEventListener('input',()=>needsReview.clear());
  function setControls() {
    document.body.classList.toggle('needs-review-rag-active',realMode()&&!!question());
    $('#workspace').setAttribute('aria-busy', String(writing || loading));
    $('#customer-select').disabled = !state || writing || loading;
    $('#customer-search').disabled = writing || loading;
    $('#reload').disabled = writing || loading;
    for (const el of $('#stage-form').elements) el.disabled = writing || loading;
    for (const el of $('#record-form').elements) el.disabled = writing || loading;
    $('#analyze').disabled = !customer() || writing || analyzing || loading;
    if (realMode()) $('#analyze').disabled ||= !modelContext?.real_allowed || !modelContext?.configured || !modelContext?.model || contextLoading || !$('#analysis-consent').checked;
    $('#analyze').textContent = analyzing ? (realMode() ? '正在调用真实模型…' : '正在生成 demo 建议…') : (realMode() ? '确认并生成 AI 建议' : '生成 demo 规则建议');
    $('#analysis-provider').disabled = writing || loading;
    $('#preview-context').disabled = writing || loading || contextLoading || analyzing;
    $('#analysis-consent').disabled = !modelContext?.real_allowed || !modelContext?.configured || !modelContext?.model || contextLoading || analyzing;
    $('#save-draft').disabled = writing || !$('#draft').value.trim() || draftSaved === $('#draft').value.trim();
    $('#copy-draft').disabled = writing || !$('#draft').value.trim();
    $('#draft').disabled = writing || loading;
    $('#rag-prepare').disabled=!customer()||!realMode()||!question()||writing||loading||contextLoading||analyzing;
    $('#rag-question').disabled=writing||loading;
    $('#rag-manual').disabled=writing||loading;
    if(realMode()&&question()){ $('#analyze').disabled ||= !ragPrepared?.ready_to_generate||!ragPrepared?.prepare_id;$('#analyze').textContent=analyzing?'正在生成，草稿仍未发送…':ragPrepared?.answer_coverage?.generation_mode==='clarification_only'?'同意并生成澄清草稿':'同意并生成来源限定草稿'; }
  }
  function invalidateAnalysis(clearContext=true) {
    if(clearContext){ragPrepared=null;$('#rag-coverage').replaceChildren();$('#rag-coverage').hidden=true;$('#rag-sources').replaceChildren();$('#rag-clarification').replaceChildren();$('#rag-clarification').hidden=true;$('#rag-status').textContent='当前预览已清除，请核对问题后点击本地预览。';}
    currentAnalysis=null; revision++; analysisController?.abort(); analysisController = null; analyzing = false;
    $('#analysis').hidden = true; $('#analysis-placeholder').hidden = false; $('#draft').value = ''; draftSaved = '';
    $('#analysis-placeholder').textContent = '先保存阶段、卡点与沟通记录，再生成建议。';
    $('#analysis-consent').checked = false;
    if (clearContext) { contextSerial++; contextController?.abort(); contextController=null; modelContext=null; contextLoading=false; $('#context-scope').textContent='请预览本次发送范围后确认。'; }
    setControls();
    window.dispatchEvent(new CustomEvent("deal-context-invalidated"));
  }
  function renderModelMode() {
    $('#real-consent').hidden = !realMode();
    const configured=modelProfile?.configured;
    $('#connection-summary').textContent=realMode()?`真实 AI · ${providerLabels[modelProfile?.provider]||'未配置供应商'} / ${modelProfile?.model||'未选择模型'}`:'demo 规则 · 本地生成，不外发';
    $('#analysis-mode-note').textContent=realMode()?(configured?'使用所选供应商，失败不会回退 demo。':'尚未配置模型密钥，请前往模型设置。'):'离线规则演示，不调用真实模型或发送邮件。';
    if(state?.isolated_mock)$('#analysis-mode-note').textContent='隔离 mock 生成，仅验证引用与确认协议，不代表真实模型可用。';
    $('#model-setup-link').hidden=false;
    $('#analysis-target').textContent=modelProfile?.base_url||'未配置供应商地址';
    setControls();
  }
  function renderRag(result) {
    const host=$('#rag-sources');host.replaceChildren();
    const clarification=$('#rag-clarification');clarification.replaceChildren();clarification.hidden=true;
    const method=result.retrieval?.mode||'unknown';
    const labels={local_keyword_bm25_fallback:'关键词回退',local_e5_rerank:'本地混合排序',keyword:'关键词检索',keyword_fallback:'关键词回退',bm25:'关键词检索',manual:'高级手选',hybrid:'混合检索',hybrid_e5_rerank:'本地混合排序'};
    const fallbackLabels={local_models_missing:'本地排序模型未配置，使用关键词检索',low_memory:'可用内存不足，使用关键词检索',local_memory_low:'可用内存不足，使用关键词检索',local_timeout:'本地排序超时，使用关键词检索',local_dependency_missing:'本地排序依赖不可用，使用关键词检索',local_model_failure:'本地排序不可用，使用关键词检索'};
    const fallback=result.retrieval?.fallback_code;
    const limitation=(result.limitations||[]).join(' ');
    if(result.status==='clarify_model'){
      $('#rag-status').textContent='需要先明确型号或版本，尚未准备生成依据。';
      clarification.hidden=false;const p=document.createElement('p');p.textContent='请补充准确型号，或明确需分别对比的参数版本。原因：'+(result.clarification?.reason||'资料范围有歧义');clarification.append(p);
      const options=result.clarification?.options;
      if(Array.isArray(options))for(const value of options){if(typeof value!=='string')continue;const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=value;b.onclick=()=>{const q=$('#rag-question');q.value=(q.value+' '+value).slice(0,200);q.dispatchEvent(new Event('input'));};clarification.append(b);}
      return;
    }
    if(result.status!=='ready'){$('#rag-status').textContent='没有找到可引用的已发布资料。'+limitation;return;}
    const sources=result.model_context?.knowledge?.items||[];
    $('#rag-status').textContent='已准备 '+sources.length+' 个引用片段 · '+(labels[method]||method)+(fallback?' · '+(fallbackLabels[fallback]||'本地排序受限，当前使用回退检索'):'')+'。'+limitation+' 尚未调用生成。';
    for(const item of sources){const detail=document.createElement('details');detail.className='rag-source';const summary=document.createElement('summary');summary.textContent=[item.title,item.source_company||'发布主体未知','v'+item.version,item.review_label||item.review_status].filter(Boolean).join(' · ');const location=document.createElement('p');location.className='muted';location.textContent='原始位置：'+JSON.stringify(item.location||{})+' · 文档 '+item.document_id+' / 片段 '+item.chunk_id;const quote=document.createElement('p');quote.className='evidence-quote';quote.textContent=item.text||'未提供可引用原文';const review=document.createElement('p');review.className='muted';review.textContent=item.review_info?.scope_notice||'发布状态不代表独立工程验证。';detail.append(summary,location,quote,review);host.append(detail);}
  }
  async function prepareRag() {
    if(!realMode()||!customer()||!question()||writing||loading||analyzing)return;
    invalidateAnalysis();
    const ticket=++contextSerial,cid=selected,q=question(),controller=new AbortController();
    contextController=controller;contextLoading=true;
    DealCoverage.render($('#rag-coverage'),null,{message:'正在本地检索并准备资料预览；尚未请求生成模型。'});setControls();
    try{
      const selections=knowledgePicker.selections();
      const data={customer_id:cid,question:q,...($('#rag-manual').checked?{knowledge_selections:selections}:{})};
      const value=await api('/api/deals/rag/prepare',data,controller.signal);
      if(ticket!==contextSerial||cid!==selected||q!==question()||!realMode())return;
      if(value.question!==q)throw Error('预览问题不匹配，请重新检索。');
      if(value.status==='ready'&&value.model_context?.customer_id!==cid)throw Error('预览客户不匹配。');
      renderRag(value);
      ragPrepared=value;modelContext=value.status==='ready'?value.model_context:null;
      DealCoverage.render($('#rag-coverage'),value.answer_coverage,{question:q,sources:modelContext?.knowledge?.items,
        message:value.status==='no_sources'?'暂无合法可用来源；未取得生成令牌。':value.status==='clarify_model'?'需要重新确认型号或选定范围；尚未取得生成令牌。':'资料预览已准备；来源合法不代表参数已回答。'});
      if(modelContext){$('#analysis-target').textContent=`${providerLabels[modelContext.provider]||modelContext.provider} · ${modelContext.base_url} · ${modelContext.model}`;$('#context-scope').textContent='本次范围包含当前客户、已保存记录和下列选定资料。仅明确同意后才请求生成；草稿始终未发送。'+KnowledgeSelection.preview(modelContext);}
      else $('#context-scope').textContent='当前预览不能生成，请补充或确认资料后重新检索。';
    }catch(e){if(ticket===contextSerial&&cid===selected&&q===question()){ragPrepared=null;modelContext=null;DealCoverage.render($('#rag-coverage'),null,{message:'预览未完成：'+e.message+'。请重新检索；尚未调用生成模型。'});}}
    finally{if(ticket===contextSerial){contextLoading=false;contextController=null;setControls();}}
  }
  async function previewContext() {
    if(realMode()&&question())return prepareRag();
    if(!realMode()||!customer()||writing||loading)return;
    invalidateAnalysis();
    contextController?.abort(); const ticket=++contextSerial, cid=selected, controller=new AbortController(); contextController=controller; modelContext=null;contextLoading=true;$('#analysis-consent').checked=false;$('#context-scope').textContent='正在本机预览发送范围，不会调用供应商…';setControls();
    try{const value=await api('/api/model/context?customer_id='+encodeURIComponent(cid)+'&knowledge_selections='+encodeURIComponent(JSON.stringify(knowledgePicker.selections())),null,controller.signal);if(ticket!==contextSerial||cid!==selected||!realMode())return;if(value.customer_id!==cid)throw Error('客户范围不匹配');value.knowledge_selections=knowledgePicker.selections();modelContext=value;$('#analysis-target').textContent=`${providerLabels[value.provider]||value.provider} · ${value.base_url} · ${value.model||'未选择模型'}`;$('#context-scope').textContent=value.real_allowed?`本次发送工厂配置、当前客户资料，以及最近 ${value.record_count} 条已保存记录（该客户共 ${value.records_total} 条），约 ${(value.input_bytes/1024).toFixed(1)} KiB。\n客户字段：${(value.fields?.customer||[]).join('、')}\n工厂字段：${(value.fields?.factory||[]).join('、')}\n记录字段：${(value.fields?.records||[]).join('、')}${Number.isInteger(value.evidence_reference_count)?'\n本次附带 '+value.evidence_reference_count+' 个本机原文引用编号，由后端核对并还原原文。':''}\n可能产生供应商费用；不会发送邮件或正式报价。`:`真实调用不可用：${value.reason||'请核对工作区、客户和连接配置。'}`;$('#context-scope').textContent+=KnowledgeSelection.preview(value);}
    catch(e){if(ticket===contextSerial&&cid===selected){$('#context-scope').textContent=modelErrorText(e);modelContext=null;}}
    finally{if(ticket===contextSerial){contextLoading=false;contextController=null;setControls();}}
  }
  $('#rag-question').addEventListener('input',()=>{invalidateAnalysis();analysisHistory.markHistorical();});
  $('#rag-manual').addEventListener('change',()=>invalidateAnalysis());
  $('#rag-prepare').onclick=prepareRag;
  function renderFacts(facts) {
    const host=$('#analysis-facts');host.replaceChildren();
    if(!Array.isArray(facts)||!facts.length){host.textContent='未提供可核实的结构化事实依据，请查看原始资料。';return;}
    for(const fact of facts){const item=document.createElement('div');item.className='fact-entry';const p=document.createElement('p');p.textContent=String(fact.statement||'');item.append(p);DealCoverage.appendFactRole(item,fact);for(const ev of Array.isArray(fact.evidence)?fact.evidence:[]){const quote=document.createElement('p');quote.className='evidence-quote';quote.textContent=`依据 · ${ev.source||''}${ev.field?' / '+ev.field:''}${ev.record_id?' / 记录 '+ev.record_id:''}${ev.source==='knowledge'?' / '+ev.title+' / '+(ev.source_company||'所属主体未知')+' / v'+ev.version+' / '+JSON.stringify(ev.location)+' / '+(ev.review_label||ev.review_status||'需核实')+'（非独立证明）':''}：${ev.quote||''}`;item.append(quote);}host.append(item);}
  }
  function remember() {
    if (!selected) return;
    const f = $('#record-form').elements;
    const c = customer();
    buffers.set(selected, {kind:f.kind.value, text:f.text.value, date:f.next_date.value, stage:$('#stage-select').value, blocker:$('#blocker').value, dirtyStage:!!c && ($('#stage-select').value !== c.stage || $('#blocker').value !== (c.blocker || ''))});
  }
  function renderPicker() {
    const query = $('#customer-search').value.trim().toLowerCase();
    const list = state.customers.filter(c => c.id === selected || `${c.name} ${c.country} ${c.product}`.toLowerCase().includes(query));
    $('#customer-select').innerHTML = '<option value="">请选择客户</option>' + list.map(c => `<option value="${esc(c.id)}">${esc(c.name)}${c.demo ? ' · 虚构演示' : ''} · ${esc(c.stage)}</option>`).join('');
    $('#customer-select').value = selected;
    $('#picker-note').textContent = query ? `筛选显示 ${list.length} 位客户；当前客户始终保留。` : '';
  }
  function renderRecords() {
    const all = state.records.filter(r => r.customer_id === selected);
    $('#history-count').textContent=String(all.length);
    const kind = $('#record-filter').value;
    const filtered = all.filter(r => !kind || r.kind === kind).sort((a,b) => String(b.created_at).localeCompare(String(a.created_at)));
    $('#record-summary').textContent = `本客户共 ${all.length} 条记录 · 当前显示 ${filtered.length} 条${customer()?.demo ? ' · 虚构演示练习' : ''}`;
    const today = new Date(), localDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
    $('#records').innerHTML = filtered.map(r => {
      const time = new Date(r.created_at), date = Number.isNaN(time.getTime()) ? r.created_at : time.toLocaleString('zh-CN');
      const text = String(r.text || '');
      const content = Array.from(text).length > 600 ? `<details class="long-record"><summary>${esc(Array.from(text).slice(0,120).join(''))}… <span>展开完整记录（${Array.from(text).length} 字符）</span></summary><p>${esc(text)}</p></details>` : `<p>${esc(text)}</p>`;
      return `<article class="timeline-item"><div class="timeline-meta"><span class="tag">${esc(r.kind)}</span><time>${esc(date)}</time>${customer()?.demo ? '<span class="tag demo">演示记录</span>' : ''}</div>${content}${r.next_date ? `<p class="next-date ${r.next_date < localDate ? 'overdue' : ''}">计划跟进：${esc(r.next_date)}${r.next_date < localDate ? ' · 日期已过，请核对是否完成' : ''}</p>` : ''}</article>`;
    }).join('') || '<p class="empty">暂无此类型记录。可先录入客户历史沟通。</p>';
  }
  function renderCustomer() {
    const c = customer();
    $('#workspace').hidden = !c; $('#empty-state').hidden = !!c;
    knowledgePicker.setCustomer(selected);analysisHistory.setCustomer(selected);
    if (!c) { $('#empty-state').innerHTML = state.customers.length ? '请选择客户后开始推进。' : '还没有客户。<a href="/">前往客户开发页录入客户 →</a>'; setControls(); return; }
    $('#customer-name').textContent = c.name;
    $('#customer-detail').textContent = `${c.type} · ${c.country || '国家未知'} · ${c.product || '产品待确认'}`;
    $('#customer-tags').innerHTML = (c.demo ? '<span class="tag demo">虚构演示客户 · 非真实采购信息</span>' : '<span class="tag">手工录入 · 需核实</span>') + (c.priority ? '<span class="tag">★ 重点客户</span>' : '');
    $('#stage-badge').textContent = `已保存阶段：${c.stage}`;
    $('#customer-facts').textContent = c.facts || '尚未录入已核实事实。';
    $('#customer-unknowns').textContent = c.unknowns || '尚未录入；请核实技术需求、采购流程、预算和交期。';
    $('#customer-hypotheses').textContent = `未核实推测：${c.hypotheses || '未填写'}`;
    $('#stage-track').innerHTML = stages.map(s => `<li class="${s === c.stage ? 'current' : ''}" ${s === c.stage ? 'aria-current="step"' : ''}>${s}</li>`).join('');
    const buffer = buffers.get(c.id), f = $('#record-form').elements;
    $('#stage-select').value = buffer?.dirtyStage ? buffer.stage : c.stage; $('#blocker').value = buffer?.dirtyStage ? buffer.blocker : c.blocker || '';
    f.kind.value = buffer?.kind || '沟通'; f.text.value = buffer?.text || ''; f.next_date.value = buffer?.date || '';
    $('#record-count').textContent = `${Array.from(f.text.value).length} / 12000 字符`;
    renderRecords(); setControls();
  }
  function choose(id) {
    if (writing) return;
    remember(); $('#rag-question').value=''; selected = state.customers.some(c => c.id === id) ? id : ''; invalidateAnalysis();
    $('#record-filter').value = '';
    // Hash works with the current static router; query parameters are also accepted on entry.
    history.replaceState(null, '', '/deals' + (selected ? '#customer_id=' + encodeURIComponent(selected) : ''));
    renderPicker(); renderCustomer(); $('#operation-status').textContent = '当前客户已切换；未保存输入分别保留。';
    $('#operation-status').dataset.state = 'idle'; $('#save-feedback').hidden = true;
    renderModelMode(); if(realMode())previewContext();
  }
  async function refresh() {
    if (loading || writing) return;
    remember(); loading = true; setControls(); $('#operation-status').textContent = '正在刷新…'; $('#operation-status').dataset.state = 'loading';
    try {
      const next = await api('/api/state');
      if (!Array.isArray(next.customers) || !Array.isArray(next.records) || !next.factory || !next.csrf) throw Error('共享资料格式不完整，请检查服务。');
      state = next;
      if(state.isolated_mock)$('#analysis-provider').querySelector('option[value=model]').textContent='隔离 mock · 模型协议模拟';
      if(!initialModeSet){$('#analysis-provider').value=state.mode==='local'?'model':'mock';initialModeSet=true;}
      try { modelProfile=await api('/api/model/settings'); } catch (_) { modelProfile=null; }
      if (requested) {
        selected = state.customers.some(c => c.id === requested) ? requested : '';
        if (!selected) toast('指定客户不存在，请重新选择。'); requested = '';
      } else if (!state.customers.some(c => c.id === selected)) selected = '';
      invalidateAnalysis();
      $('#mode').textContent = state.mode === 'demo' ? '演示环境 · 本地数据库' : '本地资料 · 离线建议';
      $('#connection').textContent = state.mode === 'demo' ? '演示环境：客户与录入记录用于虚构练习，不可发送给真实模型。demo 建议是本地规则，不代表真实采购意图。' : '本地工作区：demo 不外发；真实 AI 由你逐次确认后发送当前客户上下文。不会发送邮件或报价。';
      $('#factory-summary').textContent = `共享工厂：${state.factory.name || '待填写'} · 产品：${state.factory.products || '待确认'} · 本阶段重点：${state.factory.focus || '待确认'} · 目标国家：${state.factory.countries || '待确认'}`;
      renderPicker(); renderCustomer(); $('#operation-status').textContent = '共享资料已刷新；未保存输入仍保留在当前页面。';
      $('#operation-status').dataset.state = 'idle'; $('#save-feedback').hidden = true;
      renderModelMode();
    } catch(e) {
      $('#connection').textContent = `读取失败：${e.message} 点击“刷新共享资料”重试。`;
      $('#operation-status').textContent = '刷新失败';
      $('#operation-status').dataset.state = 'error';
      if (!state) $('#empty-state').textContent = '暂时无法加载客户。请确认本地服务已启动，然后点击刷新。';
      toast(e.message);
    } finally { loading = false; setControls(); if(realMode()&&state)previewContext(); }
  }
  async function write(action, draftText='') {
    if (writing || loading || !customer()) return;
    let succeeded=false;const previousAnalysis=currentAnalysis;
    writing = true; invalidateAnalysis(); setControls(); $('#operation-status').textContent = '正在保存，请勿关闭页面…';
    $('#operation-status').dataset.state = 'loading'; $('#save-feedback').hidden = true;
    try { await action(); succeeded=true; $('#operation-status').textContent = '已保存到本地'; $('#operation-status').dataset.state = 'saved'; }
    catch(e) {
      $('#operation-status').textContent = '保存未确认，请核对后重试'; $('#operation-status').dataset.state = 'error';
      const conflict = e.status === 409 || e.code === 'stale_customer';
      $('#save-feedback').className = 'save-feedback ' + (conflict ? 'conflict' : 'error');
      $('#save-feedback').textContent = conflict ? '客户资料已被另一页面更新，本次没有覆盖。你的输入已保留，请点击“刷新共享资料”，核对最新资料后重新保存。' : `保存未确认：${e.message} 你的输入已保留，请刷新核对后再重试。`;
      $('#save-feedback').hidden = false; toast(e.message);
      if(draftText){
        currentAnalysis=previousAnalysis;
        // Keep edited text, while discarding the old AI recommendation.
        for(const id of ['angle','analysis-unknowns','analysis-hypotheses','analysis-blocker','analysis-notice'])$('#'+id).textContent='';
        $('#analysis-facts').replaceChildren();$('#next-step').textContent='草稿保存未确认，请先查看历史记录再重试。';
        $('#result-mode').textContent='人工编辑草稿 · 保存未确认 · 未发送';
        $('#draft').value=draftText;$('#analysis').hidden=false;$('#analysis-placeholder').hidden=true;
      }
    }
    finally { writing = false; setControls(); if(realMode()&&(succeeded||!draftText))previewContext(); }
  }
  function replaceCustomer(c) { const i = state.customers.findIndex(x => x.id === c.id); if (i >= 0) state.customers[i] = c; }
  async function saveRecord(data) {
    const fingerprint = JSON.stringify(data);
    if (uncertainRecord?.fingerprint === fingerprint) {
      const fresh = await api('/api/state');
      const match = fresh.records.find(r => !uncertainRecord.before.has(r.id) && r.customer_id === data.customer_id && r.kind === data.kind && r.text === data.text && r.next_date === data.next_date);
      if (match) { state.records = fresh.records; uncertainRecord = null; renderRecords(); toast('服务器已有相同记录，已刷新显示；未重复保存。'); return; }
    }
    if(uncertainRecord?.fingerprint !== fingerprint)uncertainRecord = {fingerprint,before:new Set(state.records.map(r=>r.id))};
    const r = await api('/api/record', data); uncertainRecord = null;
    if (!state.records.some(x => x.id === r.id)) state.records.unshift(r);
    renderRecords();
  }
  $('#reload').onclick = refresh;
  $('#customer-search').addEventListener('input', () => state && renderPicker());
  $('#customer-select').addEventListener('change', e => choose(e.target.value));
  $('#record-filter').addEventListener('change', () => state && renderRecords());
  for (const form of [$('#stage-form'), $('#record-form')]) form.addEventListener('input', () => { analysisHistory.markHistorical();if (analyzing || !$('#analysis').hidden || modelContext || contextLoading) invalidateAnalysis(); });
  $('#record-text').addEventListener('input', e => { $('#record-count').textContent = `${Array.from(e.target.value).length} / 12000 字符`; });
  $('#stage-form').addEventListener('submit', e => {
    e.preventDefault();
    const c = customer(), stage = $('#stage-select').value, blocker = $('#blocker').value.trim();
    if (!c || !stages.includes(stage) || Array.from(blocker).length > 2000) return toast('请选择有效阶段，卡点最多 2000 字符。');
    write(async () => {
      // Fetch latest customer fields to avoid overwriting a customer-development edit made earlier.
      const fresh = await api('/api/state'), latest = fresh.customers.find(x => x.id === c.id);
      if (!latest) throw Error('客户已不存在，请刷新客户列表。');
      state.csrf = fresh.csrf;
      const saved = await api('/api/customer', {...latest, stage, blocker});
      replaceCustomer(saved); remember(); renderPicker(); renderCustomer(); toast('阶段与卡点已保存。');
    });
  });
  $('#record-form').addEventListener('submit', e => {
    e.preventDefault();
    const f = e.target.elements, text = f.text.value.trim(), cid = selected;
    if (!text) return toast('请填写沟通或跟进内容，不能只输入空格。');
    if (Array.from(text).length > 12000) return toast('记录内容最多 12000 字符，请分条录入。');
    const data = {customer_id:cid,kind:f.kind.value,text,next_date:f.next_date.value};
    write(async () => { await saveRecord(data); f.text.value = ''; f.next_date.value = ''; remember(); $('#record-count').textContent = '0 / 12000 字符'; toast('记录已保存，仅存于本地。'); });
  });
  $('#analyze').onclick = async () => {
    if (analyzing || writing || loading || !customer()) return;
    const c = customer();
    if ($('#stage-select').value !== c.stage || $('#blocker').value.trim() !== (c.blocker || '') || $('#record-text').value.trim()) return toast('还有未保存的阶段、卡点或记录，请先保存后生成建议。');
    const useReal=realMode(), context=modelContext, prepared=ragPrepared, useRag=useReal&&!!question();
    if(useReal&&(!context?.real_allowed||!context?.configured||!context?.model||!$('#analysis-consent').checked))return toast('请先配置模型、预览发送范围并确认本次真实调用。');
    if(useRag&&(!prepared?.prepare_id||!prepared.ready_to_generate))return toast('请先重新检索并预览当前问题。');
    invalidateAnalysis(false); analyzing = true;
    const cid = selected, version = revision, pageEpoch=reviewPageEpoch, controller = new AbortController(); analysisController = controller;
    $('#analysis-placeholder').textContent = useReal?'正在向已确认供应商请求建议…':'正在生成 demo 本地规则建议…'; setControls();
    try {
      const data=useRag?{customer_id:cid,prepare_id:prepared.prepare_id,context_token:context.context_token,revision:context.revision,request_id:crypto.randomUUID(),consent:true,provider:'model'}:useReal?{customer_id:cid,module:'deals',request_id:crypto.randomUUID(),provider:'model',consent:true,revision:context.revision,context_token:context.context_token,knowledge_selections:context.knowledge_selections||[]}:{customer_id:cid,module:'deals',request_id:crypto.randomUUID(),provider:'mock',knowledge_selections:knowledgePicker.selections()};
      if(useReal){await api('/api/deals/structured/meta',null,controller.signal);if(cid!==selected||version!==revision||!reviewPageActive||pageEpoch!==reviewPageEpoch)return;}
      const result = await api(useRag?'/api/deals/rag/generate':'/api/analyze', data, controller.signal);
      if (cid !== selected || version !== revision || !reviewPageActive || pageEpoch!==reviewPageEpoch) return;
      if(result?.status==='needs_review'||result?.protocol==='deal-review-draft-v4'){
        if(!useReal)throw Error('演示结果不能授予待核稿访问。');
        needsReview.accept(result,{customer_id:cid,customer_version:c.version,revision:data.revision,context_token:data.context_token,request_id:data.request_id,prepare_id:data.prepare_id??null,question:useRag?(prepared.question||question()):''});
        $('#analysis').hidden=true;$('#analysis-placeholder').hidden=true;$('#analysis-consent').checked=false;
        if(useRag)DealCoverage.render($('#rag-coverage'),result.rag?.answer_coverage||prepared.answer_coverage,{question:prepared.question,sources:context.knowledge?.items,message:'已取回待核草稿；原始建议尚未审核，字段转录不等于整稿核实。'});
        $('#rag-status').textContent='已生成待核草稿，请核对原始建议、出处与未知项。编辑不会批准或发送。';
        return;
      }
      if(result.mode!==(useReal?'model':'mock')||['next_step','angle','unknowns','draft'].some(k=>typeof result[k]!=='string'))throw {code:'invalid_response'};
      if(useReal&&(result.customer_id!==cid||result.revision!==context.revision||result.context_token!==context.context_token))throw {code:'context_changed'};
      $('#next-step').textContent = result.next_step; $('#angle').textContent = result.angle; $('#analysis-unknowns').textContent = result.unknowns;
      $('#analysis-notice').textContent = `${result.historical?'历史参考 · 输入已变更。':''}${useReal?'真实 AI 输出':'demo 规则演示'}，需人工核实。${DealCoverage.notice(result.notice) || ''}`;
      $('#result-mode').textContent=useReal?`AI · ${providerLabels[result.provider]||result.provider} / ${result.model||context.model}`:'demo · 本地规则，非真实模型';
      if(useRag)DealCoverage.render($('#rag-coverage'),result.rag?.answer_coverage||prepared.answer_coverage,{question:prepared.question,sources:context.knowledge?.items,message:'草稿已生成，仍未发送。字段转录与未校验范围分别保留。'});
      renderFacts(result.facts);$('#analysis-hypotheses').textContent=Array.isArray(result.hypotheses)&&result.hypotheses.length?result.hypotheses.join('\n'):'未提供推测；不要将缺失信息当作事实。';
      $('#analysis-blocker').textContent=result.stage_blocker||'未提供阶段卡点建议；不会自动更新客户阶段。';
      if(Array.isArray(result.questions)&&result.questions.length)$('#analysis-unknowns').textContent+='\n待询问：\n'+result.questions.join('\n');
      currentAnalysis=result.analysis_id?{id:result.analysis_id,version:0}:null; analysisHistory.load();
      $('#draft').value = result.draft; $('#analysis').hidden = false; $('#analysis-placeholder').hidden = true;
    } catch(e) { if (cid === selected && version === revision && reviewPageActive && pageEpoch===reviewPageEpoch) { analysisHistory.load();if(useRag){ragPrepared=null;modelContext=null;$('#analysis-consent').checked=false;DealCoverage.render($('#rag-coverage'),null,{message:[409,410].includes(e.status)?'旧预览已失效，请重新检索并确认。':'生成未确认成功，请核对历史后重新预览；不会自动重发。'});}$('#analysis-placeholder').textContent = useReal?modelErrorText(e):'demo 建议生成失败，可重试。';toast(useReal?modelErrorText(e):e.message||'demo 输出格式异常');if(useReal&&[409,413].includes(e.status)){modelContext=null;$('#context-scope').textContent='资料或连接发生变化，请点击“刷新发送范围”重新确认。';} } }
    finally { if (version === revision) { analyzing = false; analysisController = null; setControls(); } }
  };
  $('#analysis-provider').onchange=()=>{invalidateAnalysis();renderModelMode();if(realMode())previewContext();};
  $('#preview-context').onclick=previewContext;
  $('#analysis-consent').onchange=setControls;
  $('#draft').addEventListener('input', setControls);
  $('#copy-draft').onclick = async () => {
    try { await navigator.clipboard.writeText($('#draft').value); toast('草稿已复制，请人工审核后自行使用。'); }
    catch(_) { $('#draft').focus(); $('#draft').select(); toast('浏览器无法自动复制，已选中草稿，请按 Ctrl+C。'); }
  };
  $('#save-draft').onclick = () => {
    const text = $('#draft').value.trim(), cid = selected;
    if (!text || writing || text === draftSaved) return;
    const analysis=currentAnalysis;
    const recordText = '【回复草稿 · 未发送 · 需人工审核】\n' + text;
    if (Array.from(recordText).length > 12000) return toast('草稿加标注后超过 12000 字符，请缩短后保存。');
    // Capture before write() invalidates the displayed recommendation.
    write(async () => { if(analysis){const saved=await api('/api/analysis/draft',{customer_id:cid,module:'deals',analysis_id:analysis.id,draft:text,draft_version:analysis.version});analysis.version=saved.draft_version;analysisHistory.load();}await saveRecord({customer_id:cid,kind:'跟进',text:recordText,next_date:''}); draftSaved = text; toast('已保存为未发送的跟进草稿记录。'); }, text);
  };
  window.addEventListener('focus',async()=>{if(!state)return;try{const current=await api('/api/model/settings');if(modelProfile&&current.revision!==modelProfile.revision){modelProfile=current;invalidateAnalysis();renderModelMode();}}catch(_){}});
  window.StructuredDealBridge={api,customer:()=>customer(),factory:()=>state?.factory,contextRevision:()=>revision,modelRevision:()=>modelProfile?.revision,selections:()=>ragPrepared?.knowledge_selections||knowledgePicker.selections(),dirty:()=>!!customer()&&($("#stage-select").value!==customer().stage||$("#blocker").value!==(customer().blocker||"" )||!!$("#record-text").value.trim())};
  refresh();
})();

fetch('/api/meta').then(r=>r.json()).then(m=>{if(!m.isolated_mock)return;const b=document.createElement('div');b.id='isolated-mock-banner';b.setAttribute('role','note');b.textContent='隔离验收 · 合成资料 / mock 生成 · 不连接正式 DB，不代表真实模型或动态索引验收';b.style.cssText='position:sticky;top:0;z-index:1000;background:#e5eef9;color:#153b68;padding:12px 18px;border-bottom:1px solid #a7c1df;font:600 14px/1.5 sans-serif';(document.querySelector('.workspace-main')||document.body).prepend(b);}).catch(()=>{});
