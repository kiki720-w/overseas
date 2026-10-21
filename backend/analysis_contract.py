"""One output shape for prompt and validation; diagnostics never retain values."""
FIELDS=('match','angle','next_step','draft','unknowns','stage_blocker','notice','facts','hypotheses','questions')
STRING_FIELDS=FIELDS[:7]
import copy,hashlib,json,re,math

COMPAT_POLICY='analysis-projection-v1'
RESERVED_EXTRAS={'approved','approve','send','sendemail','actions','approvedexport','publish','toolcalls','functioncall','apikey','authorization','system','systemprompt','developer','instructions','credentials','key','secrets'}
RESERVED_EXTRAS.update({'price','pricing','quotation','quote','leadtime','delivery','certification','capability','accuracy','commitments','promises','guarantees'})
RESERVED_EXTRAS.update({'customerid','contexttoken','session','csrf','requestid','revision','role'})
OPTIONAL_METADATA={'confidence':'number','summary':'string','warnings':'array','metadata':'object'}

def extra_summaries(value,extras):
 return [{'name':k if isinstance(k,str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',k) else '[unlabelled key]',
          'value_type':type_name(value[k])} for k in extras[:32]]

def finite_metadata(value):
 """Bounded recursive numeric check; does not widen optional field schemas."""
 remaining=128
 def visit(item,depth):
  nonlocal remaining
  remaining-=1
  if remaining<0 or depth>4:return False
  if type(item) is float:return math.isfinite(item)
  if item is None or type(item) in (bool,int,str):return True
  if type(item) in (list,dict):
   if len(item)>16:return False
   return all(visit(child,depth+1) for child in (item.values() if type(item) is dict else item))
  return False
 return visit(value,0)

def optional_metadata_valid(key,value):
 if not finite_metadata(value):return False
 if key=='confidence':return type(value) in (int,float) and 0<=value<=1
 if key=='summary':return isinstance(value,str) and len(value)<=2000
 if key=='warnings':return isinstance(value,list) and len(value)<=12 and all(isinstance(v,str) and len(v)<=1000 for v in value)
 if key=='metadata':
  return isinstance(value,dict) and len(value)<=16 and all(isinstance(k,str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',k) and
   re.sub('[^a-z0-9]','',k.casefold()) not in RESERVED_EXTRAS and (v is None or type(v) in (bool,int,float) or isinstance(v,str) and len(v)<=1000) for k,v in value.items())
 return False

class AdaptationError(ValueError):
 def __init__(self,issue):self.issue=issue;super().__init__(issue['reason'])

def adapt(value):
 """Keep raw input untouched; project only the existing ten business fields.

 Only listed and typed optional metadata is discarded, never interpreted.
 Unrecognized extensions, execution,
 authority and credential fields are rejected. Required/nested contracts remain
 strict; there are no guessed aliases, coercions or missing-field repairs.
 """
 if not isinstance(value,dict):raise AdaptationError(diagnose(value))
 canonical={k:copy.deepcopy(value[k]) for k in FIELDS if k in value}
 issue=diagnose(canonical)
 if issue:raise AdaptationError(issue)
 extras=[k for k in value if k not in FIELDS]
 if len(extras)>32:raise AdaptationError({'reason':'extra_field_limit','extra_field_count':len(extras)})
 if any(not isinstance(k,str) for k in extras):raise AdaptationError({'reason':'extra_key_type','extra_field_count':len(extras)})
 if any(k.startswith('_') or re.sub('[^a-z0-9]','',k.casefold()) in RESERVED_EXTRAS for k in extras):
  raise AdaptationError({'reason':'unsafe_extra_fields','extra_field_count':len(extras),'extra_fields':extra_summaries(value,extras)})
 if any(k not in OPTIONAL_METADATA or not optional_metadata_valid(k,value[k]) for k in extras):
  raise AdaptationError({'reason':'unsupported_extra_fields','extra_field_count':len(extras),'extra_fields':extra_summaries(value,extras)})
 def fingerprint(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
 try:raw_hash=fingerprint(value);canonical_hash=fingerprint(canonical)
 except (TypeError,ValueError):raise AdaptationError({'reason':'root_type','actual_type':'other'})
 diagnostics={'policy':COMPAT_POLICY,'raw_sha256':raw_hash,'canonical_input_sha256':canonical_hash,'raw_field_count':len(value),
  'discarded_field_count':len(extras),'discarded_fields':extra_summaries(value,extras),'raw_values_retained':False,'canonical_fields':list(FIELDS),'semantic_correctness_validated':False}
 return canonical,diagnostics

def public_adaptation(value):
 """Safe diagnostics only: no unknown values, raw body or authority claims."""
 if not isinstance(value,dict) or value.get('policy')!=COMPAT_POLICY:return None
 hashes=('raw_sha256','canonical_input_sha256')
 if any(not isinstance(value.get(k),str) or not re.fullmatch('[0-9a-f]{64}',value[k]) for k in hashes):return None
 fields=value.get('discarded_fields',[])
 if not isinstance(fields,list) or len(fields)>32:return None
 safe=[]
 for item in fields:
  if not isinstance(item,dict):return None
  name=item.get('name')
  if not isinstance(name,str) or not (name=='[unlabelled key]' or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name)):return None
  if item.get('value_type') not in ('null','boolean','string','array','object','number','other'):return None
  safe.append({'name':name,'value_type':item['value_type']})
 if any(type(value.get(k)) is not int or not 0<=value[k]<=42 for k in ('raw_field_count','discarded_field_count')):return None
 return dict(policy=COMPAT_POLICY,**{k:value[k] for k in hashes},raw_field_count=value['raw_field_count'],discarded_field_count=value['discarded_field_count'],
  discarded_fields=safe,canonical_fields=list(FIELDS),raw_values_retained=False,semantic_correctness_validated=False)
def type_name(v):
 return 'null' if v is None else 'boolean' if type(v) is bool else 'string' if isinstance(v,str) else 'array' if isinstance(v,list) else 'object' if isinstance(v,dict) else 'number' if isinstance(v,(int,float)) else 'other'
def diagnose(value):
 if not isinstance(value,dict):return {'reason':'root_type','actual_type':type_name(value)}
 missing=[k for k in FIELDS if k not in value];extra=sum(k not in FIELDS for k in value)
 if missing or extra:return {'reason':'field_set','missing_fields':missing,'extra_field_count':extra}
 for k in FIELDS:
  expected='string' if k in STRING_FIELDS else 'array'
  if type_name(value[k])!=expected:return {'reason':'field_type','field':k,'expected_type':expected,'actual_type':type_name(value[k])}
  if k in STRING_FIELDS and len(value[k])>(6000 if k=='draft' else 2000):return {'reason':'field_length','field':k}
 for k in ('hypotheses','questions'):
  for i,v in enumerate(value[k][:12]):
   if not isinstance(v,str):return {'reason':'item_type','field':k,'item_index':i,'expected_type':'string','actual_type':type_name(v)}
 for i,f in enumerate(value['facts'][:12]):
  if not isinstance(f,dict) or set(f)!={'statement','evidence'}:return {'reason':'fact_shape','field':'facts','item_index':i}
  if not isinstance(f['statement'],str):return {'reason':'fact_statement_type','field':'facts','item_index':i,'actual_type':type_name(f['statement'])}
  if not isinstance(f['evidence'],list):return {'reason':'fact_evidence_type','field':'facts','item_index':i,'actual_type':type_name(f['evidence'])}
 return None
REASONS=('root_type','field_set','field_type','field_length','item_type','fact_shape','fact_statement_type','fact_evidence_type','unsafe_extra_fields','unsupported_extra_fields','extra_field_limit','extra_key_type')
def public_issue(issue):
 if not isinstance(issue,dict) or issue.get('reason') not in REASONS:return None
 out={'reason':issue['reason']}
 if issue.get('field') in FIELDS:out['field']=issue['field']
 if isinstance(issue.get('missing_fields'),list):out['missing_fields']=[k for k in FIELDS if k in issue['missing_fields']]
 for key in ('extra_field_count','item_index'):
  if type(issue.get(key)) is int and 0<=issue[key]<=100000:out[key]=issue[key]
 for key in ('actual_type','expected_type'):
  if issue.get(key) in ('null','boolean','string','array','object','number','other'):out[key]=issue[key]
 if isinstance(issue.get('extra_fields'),list):
  rows=[]
  for item in issue['extra_fields'][:32]:
   if isinstance(item,dict) and isinstance(item.get('name'),str) and (item['name']=='[unlabelled key]' or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',item['name'])) and item.get('value_type') in ('null','boolean','string','array','object','number','other'):
    rows.append({'name':item['name'],'value_type':item['value_type']})
  out['extra_fields']=rows
 return out
PROMPT='\nOutput contract (all fields required; no extra fields): '+', '.join(FIELDS)+'. match, angle, next_step, draft, unknowns, stage_blocker, notice are strings. facts is an array of {statement:string,evidence:[{ref_id:string}]}. hypotheses and questions are arrays of strings. Empty arrays/strings are allowed only when honestly appropriate; never invent facts to fill fields.'
