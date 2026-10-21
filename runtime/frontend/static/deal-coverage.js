'use strict';
// Source display and finite field transcription; never arbitrary answer verification.
window.DealCoverage={
  labels:{supported_extracts:'已支持字段原文转录',partial:'部分字段有原文，仍需补充',evidence_missing:'本次所选预览未覆盖请求字段',needs_model_confirmation:'需要确认型号',needs_scope_confirmation:'需要确认介质或适用条件',conflicting_evidence:'资料数值存在冲突',unchecked:'请求或来源范围未校验',not_assessed:'未进行参数转录校验'},
  fields:{pressure:'压力',air_interface:'气路接口'},
  media:{air:'气动',hydraulic:'液压',unspecified:'介质未注明'},
  qualifiers:{operating:'工作压力',supply:'供压',unspecified:'限定词未注明'},
  reasons:{unchecked_question_structure:'问题未完整匹配有限字段格式，请确认原问题范围',unsupported_or_ambiguous_parameter_clause:'来源含未支持或不明确的参数写法',model_not_bound_by_prefix_or_heading:'来源参数尚未绑定明确型号',negative_or_uncertain_source:'来源有否定、撤销或待确认表述，不作为适用规格',complete_clause_not_in_reference:'完整参数行未落在当前引用范围内'},
  text(tag,value,cls){const e=document.createElement(tag);e.textContent=String(value??'');if(cls)e.className=cls;return e;},
  binding(row){return [row.model||'型号待确认',this.fields[row.field]||row.field,this.media[row.medium]||row.medium,this.qualifiers[row.qualifier]||row.qualifier,row.conditions||'未注明附加条件'].filter(Boolean).join(' · ');},
  render(host,coverage,options={}){
    host.replaceChildren();host.hidden=false;host.dataset.coverageState=coverage?.status||'unavailable';
    host.append(this.text('p',options.message||'资料预览已准备；检索到来源不代表参数已回答。','coverage-lifecycle'));
    if(!coverage){host.append(this.text('p','尚未取得字段覆盖信息，请重新检索预览。'));return;}
    host.append(this.text('h3',this.labels[coverage.status]||'范围未确认'));
    host.append(this.text('p','仅检查支持格式内的字段原文。不是独立工程证明，也不保证整段回答正确。','muted'));
    if(options.question||coverage.question_verbatim)host.append(this.text('p','原问题：'+(options.question||coverage.question_verbatim),'preserve'));
    const sections=[['请求的局部型号与字段',coverage.requested_parameters,(r)=>this.binding(r)+(r.question_clause?'\n问题子句：'+r.question_clause:'')],
      ['未覆盖字段',coverage.missing,(r)=>this.binding(r)],['待确认范围',coverage.ambiguous,(r)=>this.binding(r)],['冲突字段',coverage.conflicts,(r)=>this.binding(r)],
      ['未支持的原片段',coverage.unchecked_fragments,(r)=>r],['未校验原因',coverage.unverified_reasons,(r)=>this.reasons[r]||r],['建议确认',coverage.clarification_questions,(r)=>r]];
    for(const [title,rows,describe] of sections){if(!rows?.length)continue;const block=document.createElement('div');block.append(this.text('h4',title));const list=document.createElement('ul');for(const row of rows)list.append(this.text('li',describe(row),'preserve'));block.append(list);host.append(block);}
    if(coverage.supported_extracts?.length){const block=document.createElement('details');block.open=true;block.append(this.text('summary','可转录的原文字段（仍保留全局缺项或未校验状态）'));
      for(const row of coverage.supported_extracts){const p=document.createElement('div');p.className='coverage-extract';const source=options.sources?.find(s=>s.document_id===row.document_id&&s.chunk_id===row.chunk_id);p.append(this.text('p',this.binding(row)),this.text('p',row.label_verbatim||row.literal,'preserve'),this.text('p','原文值：'+row.value+' '+(row.unit==='thread_literal'?'':row.unit)+(row.conditions?' · '+row.conditions:''),'preserve'),this.text('p',`${row.ref_id} · v${row.version} · ${source?.title||row.document_id} · ${source?JSON.stringify(source.location):'原文第 '+row.source_line+' 行'} · ${row.model_binding||'绑定方式未注明'}`,'muted'),this.text('p',row.quote,'preserve'));block.append(p);}host.append(block);}
  },
  factRole(fact){return fact.answer_support==='verified_transcription'?'该字段原文已支持转录（不是全题核实）':'仅原文展示（未作为参数回答）';},
  appendFactRole(host,fact){host.append(this.text('p',this.factRole(fact),'coverage-fact-role'));for(const row of fact.parameter_bindings||[])host.append(this.text('p',this.binding(row)+'\n'+row.literal+' · v'+row.version,'muted preserve'));},
  notice(text){return /replaced|excluded|conservatively/i.test(text||'')?'未校验的工程表述已替换为澄清或排除。原文引用保留；这不是已回答参数。\n'+text:text;}
};
