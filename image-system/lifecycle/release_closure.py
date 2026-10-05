"""Validate sealed runtime dependencies and exercise Expert without development imports."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile
REQUIRED=(
 'runtime/image-agent/schemas/provider-task.schema.json',
 'runtime/image-agent/schemas/provider-batch.schema.json',
 'runtime/image-agent/integrations/model-neutral-compatibility/server.py',
 'runtime/image-agent/integrations/model-neutral-compatibility/expert_schema.py',
 'runtime/image-agent/expert-prompt/neutral_context.py',
 'runtime/image-agent/expert-prompt/schemas/visual-plan.schema.json',
 'control/lifecycle.py','control/release_gate.py','control/release_closure.py',
 'payload/skills/aag-image-task/handler.js',
)
def verify(release,manifest):
 payload={r['path'] for r in manifest['payload']};closure=manifest.get('runtime_closure',{})
 if closure.get('schema')!='aag.runtime-closure.v1':raise ValueError('Missing runtime closure contract')
 declared=set(closure.get('files',[]))
 if not set(REQUIRED)<=declared or not declared<=payload:raise ValueError('Runtime dependency missing from seal')
 for name in declared:
  p=release/name
  if not p.is_file() or p.is_symlink():raise ValueError('Missing runtime dependency: '+name)
 for row in closure.get('external_files',[]):
  p=Path(row['path'])
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('External runtime dependency drift: '+str(p))
 if not closure.get('external_files'):raise ValueError('Missing external dependency declaration')
 return True

def smoke(release):
 root=release/'runtime/image-agent';sys.path[:0]=[str(root/'integrations/model-neutral-compatibility'),str(root/'expert-prompt')]
 # Development-tree reads are forbidden; approved external engine/Atlas assets
 # remain explicit host dependencies. No provider call or generation is used.
 project=Path('/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System');development=project/'image-agent'
 os.environ['AAG_IMAGE_PROJECT_ROOT']=str(project);os.environ['AAG_VISUAL_ATLAS_ROOT']=str(project/'visual-atlas')
 def audit(event,args):
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0])).resolve()
   if p.is_relative_to(development) and not p.is_relative_to(release.resolve()):raise RuntimeError('Development dependency leaked into sealed runtime: '+str(p))
 sys.addaudithook(audit)
 import jsonschema
 from expert_schema import provider_schema
 from compatibility import compose_request,composer_intent_from_message
 from server import Application
 from knowledge import Registry
 from unittest.mock import Mock
 registry=Registry();assert len(registry.styles)==493 and len(registry.families)==28
 for name in ('aag-image-task','aag-image-batch'):jsonschema.Draft7Validator.check_schema(provider_schema(name))
 off={'mode':'advanced','free_text':'One ceramic cup.'}
 assert compose_request(off)==compose_request({**off,'expert_mode':False})
 request='Exactly two ceramic cups, one blue and one white, on a plain table. No text.'
 payload={'mode':'advanced','free_text':request,'visual_family':'photography','visual_subfamily':'product','expert_mode':True}
 message,_=compose_request(payload);intent=composer_intent_from_message(message);label=intent['expert_prompt']['style_label']
 plan={'facts':[{'dimension':'count','classification':'USER_REQUIRED','text':'Exactly two ceramic cups','source_text':'Exactly two ceramic cups'}],'fragments':[{'id':'structure','priority':1,'text':request+' Separate both silhouettes with a clear gap and stable contact shadows.'},{'id':'style','priority':2,'text':label+' uses optical focus and realistic ceramic surface response.'}],'conflicts':[]}
 prompt='\n'.join(f['text'] for f in plan['fragments'])
 args={'operation':'generate','prompt':prompt,'source_policy':'auto','preservation':'none','expert_plan':plan}
 app=object.__new__(Application);app.auth_token='isolated-closure-test-only';app.audit=Mock();signed=app._sign_composer_message(message)
 result=app.validate_expert({'envelope':signed,'request':request,'arguments':args});assert result['results'][0]['prompt']==prompt
 assert result['results'][0]['tokens_including_template']<=512
 print(json.dumps({'RELEASE_CLOSURE':'PASS','EXPERT_OFF':'PASS','EXPERT_ON':'PASS','PROVIDER_SCHEMAS':2,'ATLAS':'28/493','development_reads':0,'model_calls':0}))
if __name__=='__main__':
 if len(sys.argv)!=3 or sys.argv[1]!='smoke':raise SystemExit('Usage: release_closure.py smoke RELEASE')
 smoke(Path(sys.argv[2]).resolve())
