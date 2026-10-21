"""Pure strict-output schema/payload/parser adapter. No credentials, transport, tool execution or retries."""
import copy,itertools,json,re

FUNCTION='submit_deal_envelope'
MAX_BYTES=262144
class Rejection(ValueError):
 def __init__(self,reason,path='$'):
  self.reason=reason;self.path=path;super().__init__(reason+' @ '+path)

def provider_schema(local):
 original=copy.deepcopy(local);defs=local.get('$defs',{});changes=[]
 def convert(node,path='$',stack=()):
  if '$ref' in node:
   ref=node['$ref']
   if not ref.startswith('#/$defs/') or set(node)-{'$ref','title','description'}:raise Rejection('unsupported_schema',path)
   name=ref[8:]
   if name not in defs or name in stack:raise Rejection('unsupported_schema',path)
   changes.append({'path':path,'rule':'inline_finite_ref'})
   return convert(defs[name],path,stack+(name,))
  if node.get('type')=='object':
   if set(node)-{'type','properties','required','additionalProperties','title','description','$defs'} or node.get('additionalProperties') is not False:raise Rejection('unsupported_schema',path)
   props=node.get('properties',{});required=set(node.get('required',[]));optional=[k for k in props if k not in required]
   if len(optional)>4:raise Rejection('unsupported_schema',path)
   built={}
   for key,prop in props.items():
    prop=copy.deepcopy(prop)
    if key in optional and 'anyOf' in prop and any(p.get('type')=='null' for p in prop['anyOf']):
     branches=[p for p in prop['anyOf'] if p.get('type')!='null']
     if not branches:raise Rejection('unsupported_schema',path+'.'+key)
     prop=branches[0] if len(branches)==1 else {'anyOf':branches}
     changes.append({'path':path+'.'+key,'rule':'optional_null_represented_by_absent_branch'})
    built[key]=convert(prop,path+'.'+key,stack)
   variants=[]
   for bits in itertools.product([False,True],repeat=len(optional)):
    chosen=required|{k for k,bit in zip(optional,bits) if bit}
    selected={k:v for k,v in built.items() if k in chosen}
    variants.append({'type':'object','properties':selected,'required':list(selected),'additionalProperties':False})
   if optional:changes.append({'path':path,'rule':'optional_object_fields_split_into_required_variants'})
   return variants[0] if len(variants)==1 else {'anyOf':variants}
  out={}
  for key,value in node.items():
   if key in ('$defs','title','description','default','discriminator'):continue
   if key in ('maxItems','minItems','minLength','maxLength'):
    changes.append({'path':path,'rule':'local_only_'+key});continue
   if key in ('oneOf','anyOf'):
    out['anyOf']=[convert(v,path+'.variant',stack) for v in value]
    if key=='oneOf':changes.append({'path':path,'rule':'tagged_disjoint_oneOf_to_anyOf'})
   elif key=='items':out[key]=convert(value,path+'.items',stack)
   elif key=='const':out['enum']=[value]
   elif key in ('type','enum','minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf','pattern','format'):out[key]=copy.deepcopy(value)
   else:raise Rejection('unsupported_schema',path)
  if out.get('type')=='null':raise Rejection('unsupported_schema',path)
  return out
 result=convert(local)
 audit_schema(result)
 assert local==original,'schema mutation'
 return result,changes

def audit_schema(node):
 allowed={'type','properties','required','additionalProperties','items','anyOf','enum','minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf','pattern','format'}
 if not isinstance(node,dict) or set(node)-allowed:raise Rejection('unsupported_schema')
 if 'type' in node and node['type'] not in ('object','string','number','integer','boolean','array'):raise Rejection('unsupported_schema')
 if 'format' in node and node['format'] not in ('email','hostname','ipv4','ipv6','uuid'):raise Rejection('unsupported_schema')
 if node.get('type')=='object':
  if set(node.get('required',[]))!=set(node.get('properties',{})) or node.get('additionalProperties') is not False:raise Rejection('unsupported_schema')
  for value in node['properties'].values():audit_schema(value)
 if 'items' in node:audit_schema(node['items'])
 for branch in node.get('anyOf',[]):audit_schema(branch)

def payload(schema,messages):
 audit_schema(schema)
 return {'model':'deepseek-flash','messages':copy.deepcopy(messages),'max_tokens':3000,'thinking':{'type':'disabled'},'stream':False,
  'tools':[{'type':'function','function':{'name':FUNCTION,'description':'Return proposed deal data only; this function is never executed.','strict':True,'parameters':copy.deepcopy(schema)}}],
  'tool_choice':{'type':'function','function':{'name':FUNCTION}}}

def matches_provider_subset(schema,value):
 """Local test oracle for documented JSON-schema subset; NOT a provider probe."""
 if 'anyOf' in schema:return any(matches_provider_subset(branch,value) for branch in schema['anyOf'])
 kind=schema.get('type')
 types={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool}
 if kind in types and type(value)is not types[kind]:return False
 if kind=='number' and (type(value)not in (int,float)):return False
 if 'enum' in schema and value not in schema['enum']:return False
 if kind=='object':
  if set(value)!=set(schema['required']):return False
  return all(matches_provider_subset(schema['properties'][key],item) for key,item in value.items())
 if kind=='array':return all(matches_provider_subset(schema['items'],item) for item in value)
 if kind in ('number','integer'):
  if 'minimum' in schema and value<schema['minimum']:return False
  if 'maximum' in schema and value>schema['maximum']:return False
  if 'exclusiveMinimum' in schema and value<=schema['exclusiveMinimum']:return False
  if 'exclusiveMaximum' in schema and value>=schema['exclusiveMaximum']:return False
 if kind=='string' and 'pattern' in schema and re.search(schema['pattern'],value)is None:return False
 return True

def strict_json(raw):
 if not isinstance(raw,str) or not raw.strip() or len(raw.encode('utf-8'))>MAX_BYTES:raise Rejection('invalid_arguments')
 def unique(pairs):
  result={}
  for key,value in pairs:
   if key in result:raise Rejection('duplicate_key')
   result[key]=value
  return result
 def nonfinite(value):raise Rejection('invalid_json')
 try:value=json.loads(raw,object_pairs_hook=unique,parse_constant=nonfinite)
 except (ValueError,TypeError) as e:
  if isinstance(e,Rejection):raise
  raise Rejection('invalid_json')
 if not isinstance(value,dict):raise Rejection('invalid_arguments')
 return value

def arguments(response):
 try:
  choices=response['choices']
  if not isinstance(choices,list) or len(choices)!=1:raise Rejection('unexpected_choices')
  if type(choices[0].get('index'))is not int or choices[0]['index']!=0:raise Rejection('invalid_response_layer')
  if choices[0]['finish_reason']!='tool_calls':raise Rejection('incomplete_or_unexpected_finish')
  message=choices[0]['message']
  if not isinstance(message,dict) or message.get('role')!='assistant':raise Rejection('invalid_response_layer')
  if message.get('refusal') is not None:raise Rejection('refused')
  if message.get('content') not in (None,''):raise Rejection('competing_output')
  calls=message['tool_calls']
  if not isinstance(calls,list) or len(calls)!=1:raise Rejection('missing_or_multiple_tools')
  call=calls[0]
  if call['type']!='function' or call['function']['name']!=FUNCTION:raise Rejection('unexpected_tool')
  if not isinstance(call.get('id'),str) or not call['id'] or len(call['id'])>200:raise Rejection('invalid_tool_id')
  raw=call['function']['arguments'];strict_json(raw);return raw
 except (KeyError,TypeError,IndexError,AttributeError):raise Rejection('invalid_response_layer')

MODE='deepseek_strict_tool_v1'
BETA_BASE='https://api.deepseek.com/beta'
def check_profile(provider,base_url,model):
 if provider!='deepseek' or base_url!='https://api.deepseek.com' or model!='deepseek-flash':
  raise Rejection('strict_provider_unsupported')

def raw_arguments_for_evidence(response):
 # Untrusted original, never interpreted as a valid result; credential-echo
 # protection must have already run in ModelService.request.
 try:
  calls=response['choices'][0]['message']['tool_calls']
  if len(response['choices'])==1 and isinstance(calls,list) and len(calls)==1:
   raw=calls[0]['function']['arguments']
   if isinstance(raw,str) and len(raw.encode('utf-8'))<=MAX_BYTES:return raw
 except (KeyError,TypeError,IndexError,AttributeError):pass
 return None
