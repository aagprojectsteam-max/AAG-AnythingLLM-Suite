"""Verify the approved release, never historical hashes of development source.

CURRENT.json is a local administrator trust pointer, not a cryptographic signature.
Only the governed candidate command creates seals after running the release suite.
"""
from pathlib import Path
import hashlib,json,re,subprocess,sys
P=Path('/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System')
BASE=P/'image-agent/releases/approved'
class IntegrityError(Exception):pass
def require(value,message):
 if not value:raise IntegrityError(message)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def relative(root,name):
 require(isinstance(name,str) and name and not Path(name).is_absolute() and '..' not in Path(name).parts,'Invalid relative path')
 p=root/name;require(p.resolve().is_relative_to(root.resolve()),'Path escapes release');return p
def verify_manifest(release,m,project=P,deployed=True,live=False):
 require(isinstance(m,dict) and m.get('schema') in ('aag.approved-release.v1','aag.approved-release.v2'),'Malformed release manifest')
 require(m.get('validation',{}).get('result')=='PASS','Release is not validated')
 require(m.get('family_count')==28 and m.get('style_count')==493,'Invalid release taxonomy counts')
 for key in ('payload','frozen','atlas_assets','deployments','schemas','bridge_mounts'):
  require(isinstance(m.get(key),list) and bool(m[key]),'Missing manifest section: '+key)
 expected=set()
 for row in m['payload']:
  require(set(row)=={'path','sha256'},'Malformed payload row');p=relative(release,row['path'])
  require(row['path'] not in expected,'Duplicate payload path');expected.add(row['path'])
  require(re.fullmatch('[a-f0-9]{64}',row['sha256']) and p.is_file() and sha(p)==row['sha256'],'Release artifact drift: '+row['path'])
 actual={str(p.relative_to(release)) for p in release.rglob('*') if p.is_file() and p != release/'APPROVED-MANIFEST.json'}
 require(actual==expected,'Release file inventory drift')
 if m['schema']=='aag.approved-release.v2':
  import importlib.util
  spec=importlib.util.spec_from_file_location('aag_release_closure',Path(__file__).with_name('release_closure.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  module.verify(release,m)
 for row in m['frozen']+m['atlas_assets']:
  p=relative(project,row['path']);require(p.is_file() and sha(p)==row['sha256'],'Frozen/Atlas artifact drift: '+row['path'])
 taxonomy=load(relative(release,m['taxonomy']))
 families=taxonomy['families'];ids=[f['id']+'/'+s['id'] for f in families for s in f['subfamilies']]
 require(len(families)==28 and len({f['id'] for f in families})==28 and len(ids)==493 and len(set(ids))==493,'Invalid 28/493 taxonomy bijection')
 require(set(ids)==set(m['atlas_ids']) and len(m['atlas_ids'])==493,'Atlas mapping inconsistency')
 # Validate schemas themselves, as well as their exact tested bytes.
 import jsonschema
 for name in m['schemas']:
  schema=load(relative(release,name));jsonschema.validators.validator_for(schema).check_schema(schema)
 for row in m['deployments']:
  require(row['source'] in expected and Path(row['target']).is_absolute(),'Invalid deployment row')
  source=relative(release,row['source']);target=Path(row['target'])
  if deployed:require(target.is_file() and sha(target)==sha(source),'Deployed release drift: '+str(target))
 if deployed:
  for tree in m.get('trees',[]):
   actual={str(p.relative_to(Path(tree['target']))) for p in Path(tree['target']).rglob('*') if p.is_file()}
   require(actual==set(tree['files']),'Deployed frontend tree inventory drift')
 if live:
  mounts=json.loads(subprocess.check_output(['docker','inspect','anythingllm','--format','{{json .Mounts}}'],text=True,timeout=10))
  for row in m['bridge_mounts']:
   require(any(v['Source']==row['source'] and v['Destination']==row['destination'] and not v['RW'] for v in mounts),'Missing/read-write runtime bridge mount')
   if row.get('sha256'):
    digest=subprocess.check_output(['docker','exec','anythingllm','sha256sum',row['destination']],text=True,timeout=10).split()[0]
    require(digest==row['sha256'],'Runtime bridge inode drift')
 return {'release_id':m['release_id'],'RELEASE_INTEGRITY':'PASS','frozen':len(m['frozen']),'atlas_images':493,'payload_files':len(expected),'deployments':len(m['deployments'])}
def verify(base=BASE,live=True):
 pointer=load(base/'CURRENT.json');require(set(pointer)=={'release_id','manifest_sha256'},'Malformed approval pointer')
 require(re.fullmatch('[A-Za-z0-9_-]+',pointer['release_id']),'Invalid release ID')
 release=base/pointer['release_id'];manifest=release/'APPROVED-MANIFEST.json'
 require(sha(manifest)==pointer['manifest_sha256'],'Approved manifest drift')
 require((base/'current').resolve()==release.resolve(),'Approved runtime pointer drift')
 return verify_manifest(release,load(manifest),live=live)
if __name__=='__main__':
 try:
  require(sys.argv[1:]==['verify'],'Usage: release_gate.py verify');print(json.dumps(verify()))
 except Exception as e:print('RELEASE_INTEGRITY=FAIL '+str(e),file=sys.stderr);sys.exit(1)
