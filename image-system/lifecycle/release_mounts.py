"""Add only sealed read-only file mounts to the existing Compose service.

The original configuration text (including comments) is preserved. A parsed
semantic comparison proves no settings other than the required mounts changed.
"""
import copy,json,os,stat,subprocess
from pathlib import Path
import yaml
from release_gate import require,sha

def add_mounts(text,rows):
 document=yaml.safe_load(text);expected=copy.deepcopy(document)
 volumes=expected['services']['anythingllm']['volumes']
 require(isinstance(volumes,list),'Compose volumes must be a list')
 additions=[]
 for row in rows:
  source,destination=row['source'],row['destination']
  matches=[]
  for v in volumes:
   if isinstance(v,str):
    parts=v.split(':');target=parts[1] if len(parts)>1 else parts[0]
    if target==destination:matches.append(v)
   elif v.get('target')==destination:matches.append(v)
  if matches:
   require(len(matches)==1,'Duplicate Compose mount target')
   v=matches[0]
   require(v==source+':'+destination+':ro' or isinstance(v,dict) and v.get('type')=='bind' and v.get('source')==source and v.get('read_only') is True,'Conflicting Compose mount')
  else:additions.append(source+':'+destination+':ro')
 if not additions:return text
 node=yaml.compose(text)
 def field(node,name):return next(v for k,v in node.value if k.value==name)
 sequence=field(field(field(node,'services'),'anythingllm'),'volumes')
 require(isinstance(sequence,yaml.SequenceNode) and not sequence.flow_style and sequence.value,'Expected block-style Compose volumes')
 # Insert before the first existing volume, retaining all other bytes.
 offset=sequence.start_mark.index;indent=' '*sequence.start_mark.column
 insert=('\n'+indent).join('- '+json.dumps(v) for v in additions)+'\n'+indent
 updated=text[:offset]+insert+text[offset:]
 expected['services']['anythingllm']['volumes']=additions+volumes
 require(yaml.safe_load(updated)==expected,'Compose edit changed unrelated settings')
 return updated

def plan_mounts(manifest):
 inspected=json.loads(subprocess.check_output(['docker','inspect','anythingllm'],text=True))[0]
 live={(m['Source'],m['Destination'],m['RW']) for m in inspected['Mounts']}
 missing=[r for r in manifest['bridge_mounts'] if (r['source'],r['destination'],False) not in live]
 if not missing:return None
 for row in missing:
  require('sha256' in row and any(d['target']==row['source'] for d in manifest['deployments']),'New mount must be a sealed file deployment')
 labels=inspected['Config']['Labels'];files=labels.get('com.docker.compose.project.config_files','').split(',')
 require(len(files)==1 and labels.get('com.docker.compose.service')=='anythingllm','Expected one existing AnythingLLM Compose file')
 config=Path(files[0]);require(config.is_file() and os.access(config.parent,os.W_OK),'Compose file is not writable')
 original=config.read_text();updated=add_mounts(original,missing)
 image=yaml.safe_load(original)['services']['anythingllm']['image']
 require(subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()==inspected['Image'],'Compose image tag no longer identifies the running image')
 return {'path':str(config),'sha256':sha(config),'mode':stat.S_IMODE(config.stat().st_mode),'project':labels['com.docker.compose.project'],'image_id':inspected['Image'],'image':image,'updated':updated}

def recreate(plan):
 # Fail closed if the mutable tag moved; --pull never preserves the local image.
 require(subprocess.check_output(['docker','image','inspect',plan['image'],'--format','{{.Id}}'],text=True).strip()==plan['image_id'],'Compose image changed since preflight')
 subprocess.run(['docker','compose','-p',plan['project'],'-f',plan['path'],'up','-d','--no-deps','--force-recreate','--pull','never','anythingllm'],check=True,timeout=180)
