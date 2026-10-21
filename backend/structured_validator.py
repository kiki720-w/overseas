"""Existing Pydantic v2 runtime. No model credentials, HTTP or SQLite access."""
import sys,json
# Validator is pure computation: disallow all network operations, including imports.
def deny_network(event,args):
 if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.bind'):raise RuntimeError('network_forbidden')
sys.addaudithook(deny_network)
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'structured_core'))
from contracts import ModelEnvelope
from pydantic import ValidationError
from composer import compose,compose_clarification
def unique(pairs):
 out={}
 for k,v in pairs:
  if k in out:raise ValueError('duplicate')
  out[k]=v
 return out
try:
 raw=sys.stdin.buffer.read(262145)
 if len(raw)>262144:raise ValueError('input_limit')
 data=json.loads(raw,object_pairs_hook=unique)
 if set(data)=={'schema'} and data['schema'] is True:
  result={'schema':ModelEnvelope.model_json_schema()}
 else:
  if set(data)!={'raw','confirmed'}:raise ValueError('shape')
  value=json.loads(data['raw'],object_pairs_hook=unique)
  env=ModelEnvelope.model_validate(value,strict=True)
  selected=data['confirmed']
  if not isinstance(selected,list) or any(type(i)is not int for i in selected):raise ValueError('indices')
  # Only explicitly confirmed non-inference claims reach the local composer.
  # Pending commitments ALWAYS remain, and block normal composition.
  claims=[c for i,c in enumerate(env.claims) if i in selected or c.kind in ('inference','pending_commitment')]
  controlled=ModelEnvelope.model_validate(dict(schema_version='deal-intent-v1',claims=[c.model_dump(mode='json') for c in claims],intents=env.intents),strict=True) if claims or env.intents else None
  body,internal,pending=compose(controlled) if controlled else (None,[],['no_external_intent'])
  # A separate fixed-question preview accepts no claim values or source prose.
  # Missing drawings cannot be asserted from a drawing_needed inference.
  clarification=compose_clarification(env.intents) if controlled and any(c.kind=='pending_commitment' for c in controlled.claims) else None
  result={'envelope':env.model_dump(mode='json'),'draft':body,'internal':internal,'pending':pending,'clarification_draft':clarification}
 sys.stdout.write(json.dumps(result,ensure_ascii=False))
except ValidationError as error:
 # Never emit Pydantic input, messages or URLs; only allowlisted schema locations.
 allowed={'claims','intents','schema_version','factory_fact','customer_quote','inference','pending_commitment','reference','support_refs','source_id','version','start','end','slot','value','kind','code','amount','unit','currency','quantity','basis','standard'}
 diagnostics=[]
 for item in error.errors(include_input=False,include_url=False)[:8]:
  path='$'
  for part in item['loc']:
   if type(part) is int and 0<=part<=100:path+='['+str(part)+']'
   elif part in allowed:path+='.'+part
   else:path+='.[field]'
  reason={'missing':'missing_field','list_type':'invalid_field_type','model_type':'invalid_field_type','string_type':'invalid_field_type'}.get(item['type'],'invalid_structure')
  diagnostics.append({'reason':reason,'path':path[:160]})
 sys.stdout.write(json.dumps({'error':'schema_invalid','diagnostics':diagnostics}))
 sys.exit(2)
except json.JSONDecodeError:
 sys.stdout.write('{"error":"schema_invalid","diagnostics":[{"reason":"invalid_json","path":"$"}]}')
 sys.exit(2)
except Exception:
 sys.stdout.write('{"error":"schema_invalid","diagnostics":[{"reason":"invalid_wire","path":"$"}]}')
 sys.exit(2)
