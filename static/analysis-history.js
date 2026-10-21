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
    if(this.module==='deals'&&row.input_scope.deal_question)item.append(Object.assign(document.createElement('p'),{textContent:'历史问题：'+row.input_scope.deal_question+'。这是当时来源与结果，不代表当前预览。',className:'muted preserve'}));
    if(row.status!=='ok'){const p=document.createElement('p');p.textContent=row.error_summary||'请求尚未确认完成；请核对后再决定是否发起新请求。没有可用生成正文。';item.append(p);this.list.append(item);return;}
    const r=row.result,body=document.createElement('p');body.className='preserve';body.textContent=`匹配：${r.match}\n切入角度：${r.angle}\n阶段理由 / 卡点：${r.stage_blocker}\n下一步：${r.next_step}\n\n事实原文引用：\n${r.facts.map(f=>(this.module==='deals'&&window.DealCoverage?DealCoverage.factRole(f)+'\n'+(f.parameter_bindings||[]).map(x=>DealCoverage.binding(x)+' · '+x.literal).join('\n')+'\n':'')+f.statement+'\n'+f.evidence.map(e=>`${e.source} / ${e.source==='knowledge'?e.title+' / '+(e.source_company||'所属主体未知')+' / v'+e.version+' / '+JSON.stringify(e.location)+' / '+(e.review_label||e.review_status||'需核实')+'（非独立证明）':e.field||e.record_id}：${e.quote}`).join('\n')).join('\n\n')||'无具备引用的事实'}\n\n推断（未核实）：${r.hypotheses.join('；')||'无'}\n未知：${r.unknowns}\n待核实：${r.questions.join('；')||'无'}\n${this.module==='deals'&&window.DealCoverage?DealCoverage.notice(r.notice):r.notice}`;
    const original=document.createElement('details'),origTitle=document.createElement('summary'),origText=document.createElement('p');origTitle.textContent='原始草稿 · 未发送';origText.className='preserve';origText.textContent=r.draft;original.append(origTitle,origText);
    const label=document.createElement('label');label.textContent=this.module==='deals'?'人工编辑草稿 · 未重新校验转录 · 未发送':'人工编辑草稿 · 未发送';const draft=document.createElement('textarea');draft.rows=8;draft.maxLength=12000;draft.value=row.edited_draft??r.draft;label.append(draft);
    const feedback=document.createElement('p');feedback.className='muted';feedback.textContent=row.edited_draft===null?'尚未保存人工编辑稿':`人工编辑 v${row.draft_version} · ${row.draft_updated_at}`;
    const save=document.createElement('button');save.type='button';save.className='secondary';save.textContent='保存人工编辑稿到分析历史';let busy=false;
    save.onclick=async()=>{
      if(busy)return;if(!draft.value.trim()){feedback.textContent='草稿不能为空';return;}
      busy=true;save.disabled=true;draft.disabled=true;const cid=this.cid,serial=this.serial;
      try {const saved=await this.api('/api/analysis/draft',{customer_id:row.customer_id,module:this.module,analysis_id:row.id,draft:draft.value,draft_version:row.draft_version});if(cid!==this.cid||serial!==this.serial)return;row.draft_version=saved.draft_version;row.edited_draft=saved.edited_draft;feedback.textContent=`已保存人工编辑 v${saved.draft_version} · 未发送`;} catch(e){feedback.textContent=`保存未确认：${e.message}。输入已保留，请刷新历史核对版本。`;} finally {busy=false;save.disabled=false;draft.disabled=false;}
    };
    const quality=document.createElement('p');quality.className='muted preserve';quality.dataset.draftQuality='';
    const q=row.draft_quality??r.draft_quality;quality.textContent=q?`原始草稿 ${q.word_count} 词 · 目标 80–160 词\n${q.issues.map(x=>x.message).join('\n')||'有限规则检查未发现问题，仍须人工核对来源与语义。'}\n${q.review_protocol==='outreach-boundaries-v2'?'本次分析未执行 Fetch；观察时间见输入范围，缺失则未知；业务轮次未知。':'旧版检查缺少证据时序与供方能力规则，不能视为已通过新版检查。'}\n语义与来源尚未全面验证，零警告也需人工审核。仅提示，不截断、不自动改写或付费重试。`:'历史原稿未运行此版本质量检查；保留原文，需人工核对。';
    const editedQuality=document.createElement('p');editedQuality.className='muted';editedQuality.dataset.editedDraftQuality='';
    const showWordCount=()=>{const n=(draft.value.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g)||[]).length;editedQuality.textContent=`人工编辑稿 ${n} 词 · 目标 80–160 词 · ${n>=80&&n<=160?'词数在范围内，仍需人工审核':'需人工调整；原文完整保留'}`;};
    draft.addEventListener('input',showWordCount);showWordCount();
    item.append(body,quality,original,label,editedQuality,save,feedback);this.list.append(item);
  }
};
