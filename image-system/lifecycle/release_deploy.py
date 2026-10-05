"""Local governed deployment of an already validated seal, with complete rollback.

No source files are used as payload. The mounted public directory inode is retained.
"""
from pathlib import Path
import fcntl,json,os,shutil,stat,subprocess,sys,time,urllib.request
from release_gate import P,BASE,sha,load,require,verify_manifest,verify
from release_candidate import PUBLIC,atomic
from lifecycle import Host,STATE
from release_mounts import plan_mounts,recreate

def run(args):subprocess.run(args,check=True,timeout=180)
def install(source,target,mode):
 if target.is_file() and sha(source)==sha(target) and stat.S_IMODE(target.stat().st_mode)==mode:return
 target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_name(target.name+'.release-install');shutil.copyfile(source,tmp);tmp.chmod(mode);os.replace(tmp,target)
def tree(source,target):
 target.mkdir(parents=True,exist_ok=True)
 for child in target.iterdir():
  if child.is_dir():shutil.rmtree(child)
  else:child.unlink()
 for child in source.iterdir():
  if child.is_dir():shutil.copytree(child,target/child.name)
  else:shutil.copy2(child,target/child.name)
def restore(backup):
 data=load(backup/'ROLLBACK.json')
 run(['docker','stop','--time','30','anythingllm'])
 subprocess.run(['systemctl','--user','stop','aag-image-lifecycle.service'],check=False,timeout=30)
 if not data['previous_lifecycle_active']:subprocess.run(['systemctl','--user','disable','aag-image-lifecycle.service'],check=False,timeout=15)
 for row in data['files']:
  p=Path(row['target'])
  if row['backup']:require(sha(backup/row['backup'])==row['sha256'],'Rollback artifact drift');install(backup/row['backup'],p,row['mode'])
  elif p.exists():p.unlink()
 tree(backup/'public',PUBLIC);require(PUBLIC.stat().st_ino==data['public_inode'],'Public inode changed')
 for name in ('CURRENT.json','current'):
  path=BASE/name;path.unlink(missing_ok=True)
 if data['previous_pointer']:
  atomic(BASE/'CURRENT.json',data['previous_pointer']);os.symlink(data['previous_pointer']['release_id'],BASE/'current')
 run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','restart','aag-model-compatibility.service'])
 if data['previous_lifecycle_active']:run(['systemctl','--user','start','aag-image-lifecycle.service'])
 if data.get('compose'):
  c=data['compose'];require(sha(backup/'compose.yaml')==c['sha256'],'Compose rollback artifact drift');install(backup/'compose.yaml',Path(c['path']),c['mode']);recreate(c)
 else:run(['docker','start','anythingllm'])
 atomic(backup/'RESTORED.json',{'restored_at':time.time()})
def deploy(release):
 m=load(release/'APPROVED-MANIFEST.json');verify_manifest(release,m,deployed=False);Host().busy()
 # Resolve write permissions before stopping a healthy application. Identical fixed helpers are read-only dependencies.
 for row in m['deployments']:
  target=Path(row['target']);source=release/row['source']
  if target.is_file() and sha(source)==sha(target) and stat.S_IMODE(target.stat().st_mode)==row['mode']:continue
  parent=target.parent
  while not parent.exists():parent=parent.parent
  require(os.access(parent,os.W_OK),'Deployment target parent is not writable: '+str(parent))
 compose=plan_mounts(m)
 require(release.parent.resolve()==BASE.resolve() and m['release_id']==release.name,'Invalid release location')
 backup=P/'evaluation/resilient-lifecycle-20261004'/('deployment-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()));backup.mkdir(mode=0o700)
 files=[]
 for i,row in enumerate(m['deployments']):
  target=Path(row['target'])
  if str(target).startswith(str(PUBLIC)+'/'):continue
  exists=target.is_file();b='files/'+str(i)
  if exists:(backup/'files').mkdir(exist_ok=True);shutil.copy2(target,backup/b)
  files.append({'target':str(target),'backup':b if exists else None,'sha256':sha(target) if exists else None,'mode':stat.S_IMODE(target.stat().st_mode) if exists else row['mode']})
 shutil.copytree(PUBLIC,backup/'public');inode=PUBLIC.stat().st_ino
 previous=load(BASE/'CURRENT.json') if (BASE/'CURRENT.json').exists() else None
 active=subprocess.run(['systemctl','--user','is-active','--quiet','aag-image-lifecycle.service']).returncode==0
 if compose:
  shutil.copy2(compose['path'],backup/'compose.yaml');require(sha(backup/'compose.yaml')==compose['sha256'],'Compose backup mismatch')
 atomic(backup/'ROLLBACK.json',{'compose':{k:v for k,v in compose.items() if k!='updated'} if compose else None,'files':files,'public_inode':inode,'previous_pointer':previous,'previous_lifecycle_active':active,'candidate':str(release),'manifest_sha256':sha(release/'APPROVED-MANIFEST.json')})
 for row in files:
  if row['backup']:require(sha(backup/row['backup'])==row['sha256'],'Backup verification failed')
 require(len(list((backup/'public').rglob('*')))==len(list(PUBLIC.rglob('*'))),'Frontend backup incomplete')
 print('ROLLBACK_BACKUP='+str(backup),flush=True)
 try:
  run(['docker','stop','--time','30','anythingllm'])
  if active:run(['systemctl','--user','stop','aag-image-lifecycle.service'])
  for row in m['deployments']:
   if str(row['target']).startswith(str(PUBLIC)+'/'):continue
   install(release/row['source'],Path(row['target']),row['mode'])
  tree(release/'public',PUBLIC);require(PUBLIC.stat().st_ino==inode,'Public inode changed')
  link=BASE/'current.next';link.unlink(missing_ok=True);os.symlink(release.name,link);os.replace(link,BASE/'current')
  atomic(BASE/'CURRENT.json',{'release_id':release.name,'manifest_sha256':sha(release/'APPROVED-MANIFEST.json')})
  run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','restart','aag-model-compatibility.service'])
  run(['systemctl','--user','enable','--now','aag-image-lifecycle.service'])
  if compose:
   require(sha(Path(compose['path']))==compose['sha256'],'Compose changed since backup')
   staged=backup/'compose.updated.yaml';staged.write_text(compose['updated']);staged.chmod(0o600);install(staged,Path(compose['path']),compose['mode']);recreate(compose)
  else:run(['docker','start','anythingllm'])
  for _ in range(90):
   try:
    with urllib.request.urlopen('http://127.0.0.1:3000/',timeout=2) as response:
     if response.status==200:break
   except Exception:time.sleep(1)
  else:raise RuntimeError('AnythingLLM did not become ready')
  http={}
  for path,mime in [('/','text/html'),('/index.js','application/javascript'),('/index.css','text/css')]:
   with urllib.request.urlopen('http://127.0.0.1:3000'+path,timeout=10) as response:
    body=response.read();require(response.status==200 and mime in response.headers['Content-Type'] and len(body)>50,'HTTP asset failed')
    http[path]={'status':response.status,'content_type':response.headers['Content-Type'],'bytes':len(body)}
  for _ in range(60):
   if subprocess.check_output(['docker','inspect','anythingllm','--format','{{.State.Health.Status}}'],text=True).strip()=='healthy':break
   time.sleep(1)
  else:raise RuntimeError('AnythingLLM healthcheck failed')
  result=verify();atomic(backup/'DEPLOYED.json',{'release':result,'http':http,'public_inode_preserved':True,'anythingllm_health':'healthy'})
  print('DEPLOYMENT=PASS',flush=True)
 except BaseException:
  restore(backup);raise
 return backup
if __name__=='__main__':
 if len(sys.argv)!=3 or sys.argv[1] not in ('deploy','rollback'):raise SystemExit('Usage: release_deploy.py deploy RELEASE | rollback BACKUP')
 BASE.mkdir(parents=True,exist_ok=True)
 with (BASE/'.governed.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if sys.argv[1]=='rollback':restore(Path(sys.argv[2]))
  else:
   STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
   with (STATE/'operation.lock').open('a+') as lifecycle_lock:
    fcntl.flock(lifecycle_lock,fcntl.LOCK_EX|fcntl.LOCK_NB);deploy(Path(sys.argv[2]))
