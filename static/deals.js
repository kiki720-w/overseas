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
  const knowledgePicker=new KnowledgeSelection($('#real-consent').parentElement,api,()=>invalidateAnalysis());
  const analysisHistory=new AnalysisHistory($('#analysis-history'),'deals',api,()=>!!customer()&&($('#stage-select').value!==customer().stage||$('#blocker').value!==(customer().blocker||'')||!!$('#record-text').value.trim()));
  let currentAnalysis=null;
  let state = null, selected = '', revision = 0, writing = false, analyzing = false, loading = false;
  let analysisController = null, draftSaved = '', uncertainRecord = null;
  let modelProfile = null, modelContext = null, contextController = null, contextSerial = 0, contextLoading = false;
  const realMode = () => $('#analysis-provider').value === 'model';
  const providerLabels = {deepseek:'DeepSeek','qwen-cn':'千问 · 北京','kimi-cn':'Kimi · 国内','kimi-global':'Kimi · 国际','glm-cn':'智谱 · 中国','doubao-cn':'豆包 · 北京','minimax-cn':'MiniMax · 中国',custom:'自定义 OpenAI 兼容'};
  const modelErrors = {provider_auth:'供应商认证失败（401），请检查凭据与目标地址。',provider_forbidden:'供应商拒绝访问（403），请检查账号权限、区域或策略限制。',model_not_found:'模型不存在或不可用（404），请核对模型 ID。',provider_rate_limit:'供应商限流或额度限制（429），请稍后重试。',provider_timeout:'供应商响应超时，请稍后重试。',profile_changed:'模型连接已改变，请刷新发送范围并重新确认。',context_changed:'客户、工厂或相关记录已改变，请刷新发送范围并重新确认。',context_too_large:'客户上下文超过输入上限，请整理资料后重新预览；没有静默删减证据。',key_required:'未配置密钥，请前往模型设置。',model_required:'未选择默认模型，请前往模型设置。',demo_data_forbidden:'演示环境或虚构客户不能发送给真实模型。',invalid_response:'模型输出格式无效，本次没有显示生成结果。',invalid_evidence:'模型引用未通过本机校验，没有可用建议。请查看分析历史中的安全原因；不要连续付费重试。',unsafe_output:'模型输出未通过校验，本次未显示结果。',provider_unavailable:'供应商服务暂不可用，请稍后重试。'};
  const modelErrorText = e => modelErrors[e.code] || ({401:modelErrors.provider_auth,403:modelErrors.provider_forbidden,404:modelErrors.model_not_found,429:modelErrors.provider_rate_limit,504:modelErrors.provider_timeout}[e.status]) || '模型请求失败，请核对本机服务和模型设置后重试；没有回退为 demo。';
  const initialUrl = new URL(location.href);
  const hashParams = new URLSearchParams(initialUrl.hash.slice(1));
  let requested = initialUrl.searchParams.get('customer_id') || initialUrl.searchParams.get('customer') || hashParams.get('customer_id') || hashParams.get('customer') || '';
  const customer = () => state?.customers.find(c => c.id === selected);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function toast(message) { $('#toast').textContent = message; $('#toast').style.display = 'block'; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').style.display = 'none', 7000); }
  async function api(path, data, signal) {
    const controller = new AbortController();
    const cancel = () => controller.abort();
    if (signal) { if (signal.aborted) controller.abort(); else signal.addEventListener('abort', cancel, {once:true}); }
    const timer = setTimeout(cancel, path.startsWith('/api/model/') || path === '/api/analyze' ? 65000 : 20000);
    try {
      const response = await fetch(path, {cache:'no-store', signal:controller.signal, ...(data ? {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(data)} : {})});
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
  function setControls() {
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
  }
  function invalidateAnalysis(clearContext=true) {
    currentAnalysis=null; revision++; analysisController?.abort(); analysisController = null; analyzing = false;
    $('#analysis').hidden = true; $('#analysis-placeholder').hidden = false; $('#draft').value = ''; draftSaved = '';
    $('#analysis-placeholder').textContent = '先保存阶段、卡点与沟通记录，再生成建议。';
    $('#analysis-consent').checked = false;
    if (clearContext) { contextSerial++; contextController?.abort(); contextController=null; modelContext=null; contextLoading=false; $('#context-scope').textContent='请预览本次发送范围后确认。'; }
    setControls();
  }
  function renderModelMode() {
    $('#real-consent').hidden = !realMode();
    const configured=modelProfile?.configured;
    $('#connection-summary').textContent=realMode()?`真实 AI · ${providerLabels[modelProfile?.provider]||'未配置供应商'} / ${modelProfile?.model||'未选择模型'}`:'demo 规则 · 本地生成，不外发';
    $('#analysis-mode-note').textContent=realMode()?(configured?'使用所选供应商，失败不会回退 demo。':'尚未配置模型密钥，请前往模型设置。'):'离线规则演示，不调用真实模型或发送邮件。';
    $('#model-setup-link').hidden=false;
    $('#analysis-target').textContent=modelProfile?.base_url||'未配置供应商地址';
    setControls();
  }
  async function previewContext() {
    if(!realMode()||!customer()||writing||loading)return;
    invalidateAnalysis();
    contextController?.abort(); const ticket=++contextSerial, cid=selected, controller=new AbortController(); contextController=controller; modelContext=null;contextLoading=true;$('#analysis-consent').checked=false;$('#context-scope').textContent='正在本机预览发送范围，不会调用供应商…';setControls();
    try{const value=await api('/api/model/context?customer_id='+encodeURIComponent(cid)+'&knowledge_selections='+encodeURIComponent(JSON.stringify(knowledgePicker.selections())),null,controller.signal);if(ticket!==contextSerial||cid!==selected||!realMode())return;if(value.customer_id!==cid)throw Error('客户范围不匹配');value.knowledge_selections=knowledgePicker.selections();modelContext=value;$('#analysis-target').textContent=`${providerLabels[value.provider]||value.provider} · ${value.base_url} · ${value.model||'未选择模型'}`;$('#context-scope').textContent=value.real_allowed?`本次发送工厂配置、当前客户资料，以及最近 ${value.record_count} 条已保存记录（该客户共 ${value.records_total} 条），约 ${(value.input_bytes/1024).toFixed(1)} KiB。\n客户字段：${(value.fields?.customer||[]).join('、')}\n工厂字段：${(value.fields?.factory||[]).join('、')}\n记录字段：${(value.fields?.records||[]).join('、')}${Number.isInteger(value.evidence_reference_count)?'\n本次附带 '+value.evidence_reference_count+' 个本机原文引用编号，由后端核对并还原原文。':''}\n可能产生供应商费用；不会发送邮件或正式报价。`:`真实调用不可用：${value.reason||'请核对工作区、客户和连接配置。'}`;$('#context-scope').textContent+=KnowledgeSelection.preview(value);}
    catch(e){if(ticket===contextSerial&&cid===selected){$('#context-scope').textContent=modelErrorText(e);modelContext=null;}}
    finally{if(ticket===contextSerial){contextLoading=false;contextController=null;setControls();}}
  }
  function renderFacts(facts) {
    const host=$('#analysis-facts');host.replaceChildren();
    if(!Array.isArray(facts)||!facts.length){host.textContent='未提供可核实的结构化事实依据，请查看原始资料。';return;}
    for(const fact of facts){const item=document.createElement('div');item.className='fact-entry';const p=document.createElement('p');p.textContent=String(fact.statement||'');item.append(p);for(const ev of Array.isArray(fact.evidence)?fact.evidence:[]){const quote=document.createElement('p');quote.className='evidence-quote';quote.textContent=`依据 · ${ev.source||''}${ev.field?' / '+ev.field:''}${ev.record_id?' / 记录 '+ev.record_id:''}${ev.source==='knowledge'?' / '+ev.title+' / '+(ev.source_company||'所属主体未知')+' / v'+ev.version+' / '+JSON.stringify(ev.location)+' / '+(ev.review_label||ev.review_status||'需核实')+'（非独立证明）':''}：${ev.quote||''}`;item.append(quote);}host.append(item);}
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
    remember(); selected = state.customers.some(c => c.id === id) ? id : ''; invalidateAnalysis();
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
    const useReal=realMode(), context=modelContext;
    if(useReal&&(!context?.real_allowed||!context?.configured||!context?.model||!$('#analysis-consent').checked))return toast('请先配置模型、预览发送范围并确认本次真实调用。');
    invalidateAnalysis(false); analyzing = true;
    const cid = selected, version = revision, controller = new AbortController(); analysisController = controller;
    $('#analysis-placeholder').textContent = useReal?'正在向已确认供应商请求建议…':'正在生成 demo 本地规则建议…'; setControls();
    try {
      const data=useReal?{customer_id:cid,module:'deals',request_id:crypto.randomUUID(),provider:'model',consent:true,revision:context.revision,context_token:context.context_token,knowledge_selections:context.knowledge_selections||[]}:{customer_id:cid,module:'deals',request_id:crypto.randomUUID(),provider:'mock',knowledge_selections:knowledgePicker.selections()};
      const result = await api('/api/analyze', data, controller.signal);
      if (cid !== selected || version !== revision) return;
      if(result.mode!==(useReal?'model':'mock')||['next_step','angle','unknowns','draft'].some(k=>typeof result[k]!=='string'))throw {code:'invalid_response'};
      if(useReal&&(result.customer_id!==cid||result.revision!==context.revision||result.context_token!==context.context_token))throw {code:'context_changed'};
      $('#next-step').textContent = result.next_step; $('#angle').textContent = result.angle; $('#analysis-unknowns').textContent = result.unknowns;
      $('#analysis-notice').textContent = `${result.historical?'历史参考 · 输入已变更。':''}${useReal?'真实 AI 输出':'demo 规则演示'}，需人工核实。${result.notice || ''}`;
      $('#result-mode').textContent=useReal?`AI · ${providerLabels[result.provider]||result.provider} / ${result.model||context.model}`:'demo · 本地规则，非真实模型';
      renderFacts(result.facts);$('#analysis-hypotheses').textContent=Array.isArray(result.hypotheses)&&result.hypotheses.length?result.hypotheses.join('\n'):'未提供推测；不要将缺失信息当作事实。';
      $('#analysis-blocker').textContent=result.stage_blocker||'未提供阶段卡点建议；不会自动更新客户阶段。';
      if(Array.isArray(result.questions)&&result.questions.length)$('#analysis-unknowns').textContent+='\n待询问：\n'+result.questions.join('\n');
      currentAnalysis=result.analysis_id?{id:result.analysis_id,version:0}:null; analysisHistory.load();
      $('#draft').value = result.draft; $('#analysis').hidden = false; $('#analysis-placeholder').hidden = true;
    } catch(e) { if (cid === selected && version === revision) { analysisHistory.load();$('#analysis-placeholder').textContent = useReal?modelErrorText(e):'demo 建议生成失败，可重试。';toast(useReal?modelErrorText(e):e.message||'demo 输出格式异常');if(useReal&&[409,413].includes(e.status)){modelContext=null;$('#context-scope').textContent='资料或连接发生变化，请点击“刷新发送范围”重新确认。';} } }
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
  refresh();
})();
