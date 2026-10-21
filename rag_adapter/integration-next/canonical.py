"""Read-only, single local owner adapter for a migrated ISOLATION SQLite copy."""
import dataclasses as dc
import hashlib
import json
import pathlib
import re
import sqlite3
from preview_gate.gate import CanonicalEvidence,FieldEvidence

def read(path):return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))

HEADER_CODES={
    '夹持范围':'overall_clamping_range','总夹持范围':'overall_clamping_range',
    '光孔':'smooth_bore','径向槽':'radial_grooves','径向槽和轴向槽':'radial_axial_grooves',
    '开槽数':'slot_count','槽宽':'slot_width','长度':'length','最大夹持长度':'max_clamping_length',
    '锥度原文':'taper','锥度':'taper','精度':'accuracy','同心度':'accuracy',
    '夹持行径原文':'expansion_range','夹持行径':'expansion_range',
    '选型备注原文':'selection_remarks','订货型号':'order_code','D':'D','L':'L','W':'W',
}

def code(header):
    head=re.sub(r'\([^)]*\)|（[^）]*）','',header).strip()
    return HEADER_CODES.get(head,head)

def checked_scope(chunk,document,version,field_code,header):
    """Brochure field review only; never infer independent verification."""
    meta=chunk.get('metadata',{});params=meta.get('structured_parameters',{})
    if params:
        # Explicit table visual audit plus existing approved brochure-use scope.
        return bool(params.get('visual_audit') and params.get('review',{}).get('review_status')=='confirmed'
            and version.get('metadata',{}).get('approval_basis',{}).get('inherited_original_scope'))
    field_review=meta.get('field_review') or {}
    if field_review.get('visual_source_checked') and 'feature_semantic_summaries' in field_review.get('checked_fields',[]):
        return True  # This chunk's exact publisher semantics, not measured performance.
    reviewed=(document.get('review_info') or {}).get('reviewed_fields',[])
    aliases={'application':'content.application','series':'content.series','machine_labels':'content.machine_labels_verbatim'}
    return header in reviewed or 'content.'+header in reviewed or aliases.get(field_code) in reviewed

def fields_from_row(chunk,document,version):
    text=chunk['text'];params=chunk.get('metadata',{}).get('structured_parameters',{});fields=[]
    for cell in params.get('cells',[]):
        header=cell['header_as_previously_transcribed'];field_code=code(header)
        value=cell.get('source_display_token')
        if value is None and cell.get('value_verbatim') is not None:value=str(cell['value_verbatim'])
        pattern=re.escape(header)+r'[：:]([^；;\n]*)'
        match=re.search(pattern,text);quote=match.group(0) if match else ''
        fields.append(FieldEvidence(field_code,None if value is None else str(value),quote,
            unit=cell.get('unit'),source_checked=checked_scope(chunk,document,version,field_code,header),
            missing_kind=cell.get('missing_kind') or ''))
    # Narrative fields are literal spans with known labels, never completion from scores.
    labels={'material':r'材料|材质','clamping_method':r'夹紧方式|夹持方式',
        'pressure':r'压力','rated_torque':r'额定扭矩','speed':r'转速',
        'accuracy':r'精度|concentricity','roughness':r'粗糙度',
        'application':r'用途|应用','machine_labels':r'适用机床原页标签|机床标签',
        'series':r'系列','company':r'company','brand':r'brand','english_name':r'english_name'}
    if not params:
        for field_code,pattern in labels.items():
            for m in re.finditer(r'(?:'+pattern+r')[：:]([^；;。\n]+)',text,re.I):
                label=m.group(0).split('：')[0].split(':')[0]
                fields.append(FieldEvidence(field_code,m.group(1),m.group(0),
                    source_checked=checked_scope(chunk,document,version,field_code,label)))
        if chunk.get('metadata',{}).get('field_review',{}):
            fields.append(FieldEvidence('features',text,text,
                source_checked=checked_scope(chunk,document,version,'features','feature_semantic_summaries')))
    return tuple(fields)

class CanonicalSnapshot:
    def __init__(self,db_path,lineage_path,owner_scope_path=None):
        self.db_path=pathlib.Path(db_path).resolve(strict=True)
        if 'isolation' not in self.db_path.parts:raise ValueError('explicit isolation DB path required')
        self.lineage=read(lineage_path);self.owner=read(owner_scope_path) if owner_scope_path else {}
        expected={'protocol','principal','scope','database','read_document_ids','send_document_ids'}
        if self.owner and (set(self.owner)!=expected or self.owner['protocol']!='local-owner-isolation-v1'
            or self.owner['scope']!='isolated_evaluation_only' or self.owner['principal']!='local_owner'
            or pathlib.Path(self.owner['database']).resolve()!=self.db_path):raise ValueError('invalid local owner scope')
        for k in ('read_document_ids','send_document_ids'):
            if self.owner and (not isinstance(self.owner[k],list) or not all(isinstance(v,str) for v in self.owner[k])):
                raise ValueError('invalid local owner document list')
        self.by_id={}
        for x in self.lineage:
            mapping=x.get('new',x);cid=x['candidate_chunk_id']
            if cid in self.by_id and self.by_id[cid]!=mapping:raise ValueError('ambiguous lineage')
            self.by_id[cid]=mapping
        self.details={};self.registry={}

    def __call__(self):
        with sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True) as db:
            db.execute('BEGIN')
            docs={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM kb_documents')}
            versions={(r[0],r[1]):json.loads(r[2]) for r in db.execute('SELECT document_id,version,data FROM kb_versions')}
            chunks={r[0]:(r[1],r[2],json.loads(r[3])) for r in db.execute('SELECT id,document_id,version,data FROM kb_chunks')}
        output=[];details={};registry={}
        for cid,mapping in self.by_id.items():
            tup=chunks.get(mapping['chunk_id'])
            if not tup:raise ValueError('canonical mapping chunk missing')
            did,version,chunk=tup
            if did!=mapping['document_id'] or version!=mapping['version'] or chunk['chunk_id']!=mapping['chunk_id']:
                raise ValueError('canonical mapping SQL identity mismatch')
            if chunk.get('metadata',{}).get('candidate_chunk_id')!=cid:raise ValueError('canonical candidate metadata mismatch')
            if mapping.get('text_sha256') and hashlib.sha256(chunk['text'].encode()).hexdigest()!=mapping['text_sha256']:
                raise ValueError('canonical body hash mismatch')
            d=dict(docs[did]);v=versions[(did,version)];meta=chunk.get('metadata',{})
            normalize=lambda value:' '.join(value.casefold().split())
            d['conflict']=bool(d.get('conflict')) or any(other['id']!=did and other.get('enabled')
                and other.get('review_status')!='rejected' and other.get('category')==d.get('category')
                and normalize(other['title'])==normalize(d['title']) for other in docs.values())
            p=meta.get('structured_parameters',{});model=p.get('model_verbatim')
            if not model:
                series=re.search(r'系列[：:]([^；。\n]+)',chunk['text']);model=series.group(1) if series else d['id']
                if model==d['id']:
                    named=re.search(r'[（(]([A-Za-z]+\d+[A-Za-z]*)[）)]',chunk['text'])
                    if named:model=named.group(1)
            subject=d.get('source_company','')+':'+model
            if model:registry[model]=subject
            output.append(CanonicalEvidence(cid,did,version,chunk['chunk_id'],d['version'],d['revision'],subject,
                d.get('source_company',''),chunk['text'],fields_from_row(chunk,d,v),chunk.get('location',{}),
                canonical=True,enabled=d.get('enabled',False),review_status=d.get('review_status','unverified'),
                expires_on=d.get('expires_on',''),conflict=d.get('conflict',False),
                read_authorized=did in self.owner.get('read_document_ids',[]),
                send_authorized=did in self.owner.get('send_document_ids',[])))
            details[cid]={'document':d,'chunk':chunk,'version':v,'model':model}
        self.details=details;self.registry=registry
        return tuple(output)

def write_local_owner_scope(db_path,output):
    """Explicit CLI controller grant: read-only evaluation scope, NO cloud send."""
    db_path=pathlib.Path(db_path).resolve(strict=True)
    if 'isolation' not in db_path.parts:raise ValueError('isolation DB only')
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
        ids=[r[0] for r in db.execute('SELECT id FROM kb_documents')]
    value={'protocol':'local-owner-isolation-v1','principal':'local_owner','scope':'isolated_evaluation_only',
        'database':str(db_path),'read_document_ids':ids,'send_document_ids':[]}
    pathlib.Path(output).write_text(json.dumps(value,indent=2),encoding='utf-8')
