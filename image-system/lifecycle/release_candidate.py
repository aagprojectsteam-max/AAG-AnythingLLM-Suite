"""Governed local release: validate source, snapshot exact tested payload, seal.
Never accepts a caller-provided PASS flag. No deployment until every check passes.
"""
from pathlib import Path
import ast,fcntl,hashlib,json,os,shutil,subprocess,sys,time
from release_gate import P,BASE,sha,load,verify_manifest,require
A=P/'image-agent';S=A/'releases/staged'/((A/'VERSION').read_text().strip())
STORAGE=P.parent/'storage';PUBLIC=STORAGE/'aag-image-agent-integration/multi-image-export/public'
def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.install');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.chmod(0o600);os.replace(tmp,path)
def tree_hash(root):
 # Exact existing frontend builder ordering (Node localeCompare), including casing.
 code="""const fs=require('fs'),path=require('path'),crypto=require('crypto');const root=process.argv[1],h=crypto.createHash('sha256');function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){const p=path.join(dir,e.name);if(e.isDirectory())walk(p);else if(e.isFile()&&e.name!=='AAG-BUILD-PROVENANCE.json'){h.update(path.relative(root,p));h.update('\\0');h.update(fs.readFileSync(p));h.update('\\0');}}}walk(root);process.stdout.write(h.digest('hex'));"""
 return subprocess.check_output(['node','-e',code,str(root)],text=True)
def inputs():
 dirs=['src','schemas','skills','integrations','expert-prompt','lifecycle','tools','tests','registry','ordinary-generation/config']
 paths=[]
 for d in dirs:
  paths += [p for p in (A/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc',)]
 return {str(p.relative_to(A)):sha(p) for p in sorted(set(paths))}
def create():
 BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
 release_id=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());release=BASE/release_id;release.mkdir(mode=0o700)
 before=inputs();logs=release/'validation';logs.mkdir()
 mounted=json.loads(subprocess.check_output(['docker','inspect','anythingllm','--format','{{json .Mounts}}'],text=True))
 bridge_before={v['Source']:sha(Path(v['Source'])) for v in mounted if not v['RW'] and v['Source'].startswith(str(STORAGE/'aag-image-agent-integration')) and Path(v['Source']).is_file()}
 def run(name,args,cwd=A):
  with (logs/(name+'.log')).open('w') as f:subprocess.run(args,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,check=True,env={**os.environ,'AAG_CANDIDATE_RELEASE_ID':release_id})
  print(name+'=PASS',flush=True)
 # Validate immutable baseline BEFORE producing any replacement manifest.
 frozen=load(A/'lifecycle/frozen-policy.json')['files']
 if (BASE/'CURRENT.json').exists():
  previous=load(BASE/load(BASE/'CURRENT.json')['release_id']/'APPROVED-MANIFEST.json')
  require(previous['frozen']==frozen,'Frozen policy change requires a separate explicit migration')
 for row in frozen:require(sha(P/row['path'])==row['sha256'],'Frozen baseline drift: '+row['path'])
 for name in before:
  if name.endswith('.py'):ast.parse((A/name).read_text(),filename=name)
 run('node-tests',['npm','test'])
 run('compatibility-tests',['python3','-m','unittest','discover','-s','integrations/model-neutral-compatibility','-p','test_*.py'])
 run('expert-tests',['python3','-m','unittest','discover','-s','expert-prompt/tests','-p','test_*.py'])
 run('lifecycle-integrity-tests',['python3','-m','unittest','discover','-s','lifecycle','-p','test_*.py'])
 run('browser-lifecycle-tests',['python3','-m','unittest','discover','-s','tools','-p','test_browser_lifecycle.py'])
 run('build',['env','AAG_CANDIDATE_RELEASE_ID='+release_id,'node','tools/build.js'])
 run('contracts-security-atlas',['node','tools/doctor.js','--candidate-audit'])
 require(before==inputs(),'Source changed during validation; candidate not sealed')
 provenance=load(S/'anythingllm-public/AAG-BUILD-PROVENANCE.json')
 require(provenance.get('overlayTreeSha256')==tree_hash(A/'integrations/anythingllm/frontend'),'Frontend overlay was not built from current source')
 require(provenance.get('buildToolSha256')==sha(A/'tools/build-anythingllm-frontend.js'),'Frontend build tool changed after build')
 require(provenance['publicTreeSha256']==tree_hash(S/'anythingllm-public'),'Frontend build tree drift')
 m=load(S/'STAGED-MANIFEST.json');deployments=[]
 def copy(source,name,target=None,mode=None):
  dest=release/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
  if target is not None:deployments.append({'source':name,'target':str(target),'mode':mode if mode is not None else (source.stat().st_mode&0o777)})
 for row in m['provider_files']:copy(S/row['path'],'payload/skills/'+row['path'],Path(row['deploy_target']),int(row['mode'],8))
 for row in m['integration_files']:
  for i,target in enumerate(row['targets']):copy(A/row['source'],'payload/integration/'+row['source']+('.'+str(i)),Path(target),int(row['mode'],8))
 public_files=[]
 for p in sorted((S/'anythingllm-public').rglob('*')):
  if p.is_file():name=str(p.relative_to(S/'anythingllm-public'));public_files.append(name);copy(p,'public/'+name,PUBLIC/name,0o600)
 # Complete selective runtime snapshot. No generated artifacts, private state or model weights.
 for d in ['integrations/model-neutral-compatibility','expert-prompt','schemas']:
  for p in sorted((A/d).rglob('*')):
   if not p.is_file() or '__pycache__' in p.parts or p.name.startswith('test_') or any(x in p.relative_to(A/d).parts for x in ('tests','tools')):continue
   if p.suffix not in ('.py','.json','.tsv','.html','.js','.css'):continue
   copy(p,'runtime/image-agent/'+str(p.relative_to(A)))
 for name in ['lifecycle.py','artifact_files.py','release_gate.py','release_closure.py']:copy(A/'lifecycle'/name,'control/'+name,P/'image-agent/releases/runtime-control'/name,0o600)
 for action in ['start','stop','status','release']:copy(A/'integrations/launchers'/('aag-image-'+action),'control/aag-image-'+action,P/'bin'/('aag-image-'+action),0o755)
 for unit,source in [('aag-model-compatibility.service',A/'integrations/model-neutral-compatibility/aag-model-compatibility.service'),('aag-image-lifecycle.service',A/'lifecycle/aag-image-lifecycle.service')]:copy(source,'units/'+unit,Path.home()/'.config/systemd/user'/unit,0o644)
 # Fixed helpers/units are executable runtime dependencies; source edits must not silently change them.
 helpers=['bin/aag-ai-start','bin/aag-reference-status','bin/aag-upscale-start','bin/aag-upscale-stop','libexec/aag-image-hub-control','libexec/aag-upscale-control']
 for name in helpers:copy(P/name,'helpers/'+name,P/name)
 for unit in ['aag-comfyui-docker-bridge.service','aag-comfyui-image-proxy.service','aag-composer-loopback-relay.service']:
  source=Path.home()/'.config/systemd/user'/unit;copy(source,'units/'+unit,source)
 product=load(P/'visual-atlas/manifest/product-assets.json');assets=[{'path':'visual-atlas/manifest/product-assets.json','sha256':sha(P/'visual-atlas/manifest/product-assets.json')}]
 assets += [{'path':k,'sha256':v['sha256']} for k,v in product['metadata'].items()]
 assets += [{'path':v[k]['path'],'sha256':v[k]['sha256']} for v in product['entries'] for k in ('reference','thumbnail')]
 mounts=json.loads(subprocess.check_output(['docker','inspect','anythingllm','--format','{{json .Mounts}}'],text=True))
 bridge=[]
 for mount in mounts:
  if mount['RW'] or not (mount['Source'].startswith(str(STORAGE/'aag-image-agent-integration')) or mount['Source']==str(P/'visual-atlas')):continue
  row={'source':mount['Source'],'destination':mount['Destination']}
  if Path(mount['Source']).is_file():
   matching=next((d for d in deployments if d['target']==mount['Source']),None)
   if matching is None:
    source=Path(mount['Source']);require(bridge_before.get(str(source))==sha(source),'Existing bridge changed during tests')
    name='existing-bridge/'+str(len(bridge))+'/'+source.name;copy(source,name,source);matching=deployments[-1]
   row['sha256']=sha(release/matching['source'])
  bridge.append(row)
 # Explicit native provider overlays are governed payload, including new binds.
 for integration in m['integration_files']:
  destination=integration.get('container_target')
  if not destination:continue
  require(len(integration['targets'])==1,'Container overlay must have one source')
  source=integration['targets'][0]
  row=next(d for d in deployments if d['target']==source)
  existing=next((b for b in bridge if b['destination']==destination),None)
  require(existing is None or existing['source']==source,'Container overlay target conflict')
  if existing is None:bridge.append({'source':source,'destination':destination,'sha256':sha(release/row['source'])})
 atomic(release/'SOURCE-AUDIT.json',{'source_sha256':before,'note':'Audit identity only; active source is not a startup authority.'})
 require(before==inputs(),'Source changed during snapshot')
 manifest={'schema':'aag.approved-release.v2','release_id':release_id,'validation':{'result':'PASS','log_directory':'validation'},'family_count':28,'style_count':493,'frozen':frozen,'atlas_assets':assets,'atlas_ids':[e['key'] for e in product['entries']],'taxonomy':'runtime/image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json','schemas':['payload/skills/aag-image-task/provider-task.schema.json','payload/skills/aag-image-batch/provider-batch.schema.json','payload/skills/aag-image-job/provider-job.schema.json','runtime/image-agent/expert-prompt/schemas/visual-plan.schema.json','runtime/image-agent/schemas/generation-details.schema.json'],'deployments':deployments,'bridge_mounts':bridge,'trees':[{'target':str(PUBLIC),'files':public_files}]}
 # Explicitly declared host dependencies stay external (no model weights copied).
 external=[]
 for root in [P/'bridges/comfyui-docker-bridge',P/'bridges/comfyui-image-proxy',P.parent/'AAG-Upscale-Engine/service']:
  for p in sorted(root.rglob('*')):
   if p.is_file() and p.suffix in ('.py','.sh') and '__pycache__' not in p.parts:external.append({'path':str(p),'sha256':sha(p)})
 for name in ['aag-image-hub.service','aag-image-hub-docker-bridge.service','aag-upscale-engine.service','aag-upscale-docker-bridge.service']:
  p=Path('/etc/systemd/system')/name;external.append({'path':str(p),'sha256':sha(p)})
 p=STORAGE/'comfyui-docker-bridge/proxy.py';external.append({'path':str(p),'sha256':sha(p)})
 profile=load(A/'expert-prompt/engine-budget-profile.json')
 for relative,digest in profile['installed_source_sha256'].items():external.append({'path':'/mnt/data/AI/ComfyUI/'+relative,'sha256':digest})
 for row in manifest['deployments']:
  if row['source'].startswith(('helpers/','units/')):external.append({'path':row['target'],'sha256':sha(release/row['source'])})
 # External files must already match; altered deployed units are verified through deployments instead.
 for row in external:require(Path(row['path']).is_file() and sha(Path(row['path']))==row['sha256'],'External runtime dependency missing or changed: '+row['path'])
 manifest['runtime_closure']={'schema':'aag.runtime-closure.v1','files':[str(p.relative_to(release)) for p in sorted(release.rglob('*')) if p.is_file() and any(str(p.relative_to(release)).startswith(prefix) for prefix in ('runtime/','control/','payload/skills/','helpers/','units/'))],'external_files':external,'services':{'compatibility':'runtime/image-agent/integrations/model-neutral-compatibility/server.py','expert':'runtime/image-agent/expert-prompt/neutral_context.py','lifecycle':'control/lifecycle.py','task':'payload/skills/aag-image-task/handler.js','composer_relay':'declared socat systemd unit','image_proxy':'declared host proxy.py','comfy_bridge':'declared host run.sh/proxy.py'},'platform_dependencies':['system Python with jsonschema','Comfy pinned tokenizers package','systemd user bus','socat','Docker AnythingLLM image','declared frozen Atlas assets','Comfy/Hub/Upscale engine services and model inventory']}
 run('sealed-runtime-closure',['/usr/bin/python3','-I','-B',str(release/'control/release_closure.py'),'smoke',str(release)],cwd=release)
 manifest['payload']=[{'path':str(p.relative_to(release)),'sha256':sha(p)} for p in sorted(release.rglob('*')) if p.is_file()]
 verify_manifest(release,manifest,deployed=False);atomic(release/'APPROVED-MANIFEST.json',manifest)
 print('SEALED_RELEASE='+str(release),flush=True);return release
if __name__=='__main__':
 if sys.argv[1:]!=['create']:raise SystemExit('Usage: release_candidate.py create')
 BASE.mkdir(parents=True,exist_ok=True)
 with (BASE/'.governed.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);create()
