"""Canonical AAG image lifecycle. Fixed commands; no provider or generation calls."""
from __future__ import annotations
import stat,concurrent.futures,fcntl,http.client,json,os,pathlib,socket,socketserver,subprocess,sys,threading,time,urllib.request,uuid
from http.server import BaseHTTPRequestHandler
P=pathlib.Path('/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System')
STORAGE=P.parent/'storage';STATE=STORAGE/'aag-image-lifecycle';SOCKET=STATE/'control.sock'
USER_UNITS={'comfy':'aag-comfyui-session.service','comfy_bridge':'aag-comfyui-docker-bridge.service','image_proxy':'aag-comfyui-image-proxy.service'}
SYSTEM_UNITS={'hub':'aag-image-hub.service','hub_bridge':'aag-image-hub-docker-bridge.service','upscale':'aag-upscale-engine.service','upscale_bridge':'aag-upscale-docker-bridge.service'}
CONTROL_UNITS=('aag-model-compatibility.service','aag-composer-loopback-relay.service','aag-image-lifecycle.service')
SHARED=('anythingllm','docker','aag-model-compatibility.service','aag-composer-loopback-relay.service','qdrant','aag-vector-worker.service')
class Failure(Exception):
 def __init__(self,code,message,component='system',retryable=True):self.code,self.message,self.component,self.retryable=code,message,component,retryable
 def safe(self):return {'code':self.code,'message':self.message,'component':self.component,'retryable':self.retryable}
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex);tmp.write_text(json.dumps(data));tmp.chmod(0o600);os.replace(tmp,path)
def alive(pid):
 try:os.kill(int(pid),0);return True
 except (OSError,ValueError,TypeError):return False
def process_start(pid):
 try:return pathlib.Path('/proc/'+str(int(pid))+'/stat').read_text().rsplit(')',1)[1].split()[19]
 except (OSError,ValueError,TypeError,IndexError):return None
def read_json(path):
 try:return json.loads(path.read_text())
 except (OSError,ValueError):return {}
def get(url):
 try:
  with urllib.request.urlopen(url,timeout=1.5) as response:return json.load(response)
 except Exception:return None
def relay_health(path):
 connection=http.client.HTTPConnection('localhost',timeout=1.5)
 try:
  connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.settimeout(1.5);connection.sock.connect(str(path))
  connection.request('GET','/health',headers={'Host':'127.0.0.1:18080'});response=connection.getresponse();raw=response.read(65537)
  return response.status==200 and len(raw)<=65536 and json.loads(raw).get('status')=='ok'
 except (OSError,ValueError,http.client.HTTPException):return False
 finally:connection.close()
def user_bus_environment():
 # The lifecycle belongs to the invoking Unix user, even from a terminal without
 # desktop environment variables. Never infer READY from a missing bus.
 env=os.environ.copy();root=pathlib.Path('/run/user')/str(os.getuid());bus=root/'bus'
 try:
  if root.stat().st_uid==os.getuid() and bus.stat().st_uid==os.getuid() and stat.S_ISSOCK(bus.stat().st_mode):
   env['XDG_RUNTIME_DIR']=str(root);env['DBUS_SESSION_BUS_ADDRESS']='unix:path='+str(bus)
 except OSError:pass
 return env
class Host:
 def run(self,args,timeout=90):
  try:subprocess.run(args,stdin=subprocess.DEVNULL,stdout=sys.stderr,stderr=sys.stderr,timeout=timeout,check=True,env=user_bus_environment())
  except subprocess.TimeoutExpired:raise Failure('OPERATION_TIMEOUT','A required component did not finish in time.')
  except subprocess.CalledProcessError:raise Failure('COMPONENT_COMMAND_FAILED','A required component command failed. See the local administrator log.')
 def units(self,user,names):
  try:
   r=subprocess.run(['systemctl']+(['--user'] if user else [])+['is-active',*names],text=True,capture_output=True,timeout=3,env=user_bus_environment() if user else None)
   states=r.stdout.strip().splitlines()
   if len(states)!=len(names):return {name:'unavailable' for name in names}
   return dict(zip(names,states))
  except Exception:return {name:'unavailable' for name in names}
 def measure(self):
  def probe():
   urls={'hub':'http://127.0.0.1:18190/health','hub_bridge':'http://172.18.0.1:18190/health','comfy':'http://127.0.0.1:8188/system_stats','comfy_bridge':'http://172.18.0.1:18188/system_stats','upscale':'http://127.0.0.1:18191/health','upscale_bridge':'http://172.18.0.1:18191/health','reference':'http://127.0.0.1:8188/object_info','compatibility':'http://127.0.0.1:18080/health'}
   with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:return dict(zip(urls,pool.map(get,urls.values())))
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
   fp=pool.submit(probe);fu=pool.submit(self.units,True,[*USER_UNITS.values(),*CONTROL_UNITS]);fs=pool.submit(self.units,False,list(SYSTEM_UNITS.values()));probes,users,systems=fp.result(),fu.result(),fs.result()
  components={}
  for key,unit in {**USER_UNITS,**SYSTEM_UNITS}.items():
   state=(users if key in USER_UNITS else systems).get(unit,'unknown');health=probes.get(key)
   if key.startswith(('hub','upscale')):health=isinstance(health,dict) and health.get('status')=='ok'
   elif key.startswith('comfy'):health=isinstance(health,dict) and isinstance(health.get('system'),dict)
   if key=='image_proxy':
    try:
     with socket.create_connection(('127.0.0.1',18189),timeout=.5):health=True
    except OSError:health=False
   if health and state=='active':value='READY'
   elif not health and state in ('inactive','failed','unknown'):value='OFF' if state in ('inactive','unknown') else 'ERROR'
   else:value='ERROR'
   components[key]={'state':value,'unit_state':state}
  nodes=probes['reference'] or {};required={'PuLIDInsightFaceLoader','PuLIDEVACLIPLoader','PuLIDModelLoader','ApplyPuLIDFlux2','ReferenceLatent'}
  components['reference']={'state':'READY' if required<=set(nodes) else ('OFF' if components['comfy']['state']=='OFF' else 'ERROR')}
  control=probes.get('compatibility') or {}
  components['compatibility']={'state':'READY' if control.get('status')=='ok' and users.get(CONTROL_UNITS[0])=='active' else 'ERROR','owned':False}
  components['composer_relay']={'state':'READY' if users.get(CONTROL_UNITS[1])=='active' and relay_health(STORAGE/'aag-composer-relay/composer.sock') else 'ERROR','owned':False}
  components['lifecycle_control']={'state':'READY' if users.get(CONTROL_UNITS[2])=='active' else 'ERROR','owned':False}
  return components
 def verify(self):
  self.run(['/usr/bin/python3','-I',str(P/'image-agent/releases/runtime-control/release_gate.py'),'verify'],60)
 def busy(self):
  for name in ('aag-human-identity-state','aag-human-identity-scene-state'):
   if any((STORAGE/name/'inbox').glob('*.json')):raise Failure('IMAGE_WORK_QUEUED','Reference identity work is queued.','reference')
  lease=STORAGE/'aag-image-agent-state/scheduler/lease'
  if lease.exists():raise Failure('IMAGE_WORK_ACTIVE','An image resource lease is held. Finish the current job before stopping.','scheduler')
  waiters=STORAGE/'aag-image-agent-state/scheduler/waiters'
  if waiters.exists() and any(waiters.glob('*.json')):raise Failure('IMAGE_WORK_QUEUED','Image work is queued. Finish or cancel it before stopping.','scheduler')
  queue=get('http://127.0.0.1:8188/queue')
  if queue and (queue.get('queue_running') or queue.get('queue_pending')):raise Failure('IMAGE_WORK_ACTIVE','ComfyUI has running or queued work.','comfy')
  up=get('http://127.0.0.1:18191/health')
  if up and up.get('busy'):raise Failure('IMAGE_WORK_ACTIVE','Upscale work is running.','upscale')
  worker=self.units(True,['aag-human-identity-bridge.service','aag-human-identity-scene-bridge.service'])
  if any(s in ('active','activating') for s in worker.values()):raise Failure('IMAGE_WORK_ACTIVE','A reference identity worker is running.','reference')
  upstate=self.units(False,[SYSTEM_UNITS['upscale']]).get(SYSTEM_UNITS['upscale'])
  if upstate not in ('inactive','unknown') and up is None:raise Failure('QUEUE_STATUS_UNAVAILABLE','Cannot verify that Upscale is idle.','upscale')
  if any(s=='unavailable' for s in worker.values()):raise Failure('QUEUE_STATUS_UNAVAILABLE','Cannot verify reference worker state.','reference')
  states=self.units(True,[USER_UNITS['comfy']])
  if states.get(USER_UNITS['comfy']) not in ('inactive','unknown') and queue is None:raise Failure('QUEUE_STATUS_UNAVAILABLE','Cannot verify that ComfyUI is idle.','comfy')
 def start(self):
  print('[0/6] Approved release integrity...',flush=True)
  self.verify()
  self.run(['systemctl','--user','start',*CONTROL_UNITS],30)
  print('[1/6] Image Hub...',flush=True);self.run(['sudo','-n','/usr/local/libexec/aag-image-hub-control','start'])
  print('[2/6] ComfyUI and owned bridges...',flush=True)
  active=self.units(True,[USER_UNITS['comfy']]).get(USER_UNITS['comfy'])=='active'
  if get('http://127.0.0.1:8188/system_stats') and not active:raise Failure('UNOWNED_COMFY_PROCESS','ComfyUI is running outside the owned lifecycle unit. It was not changed.','comfy',False)
  self.run(['systemctl','--user','start',USER_UNITS['comfy_bridge'],USER_UNITS['image_proxy']])
  if not active:self.run(['systemd-run','--user','--unit=aag-comfyui-session','--collect','--property=Type=exec','--property=TimeoutStopSec=45','--property=SendSIGKILL=no','--working-directory=/mnt/data/AI/ComfyUI',str(P/'bin/aag-ai-start')],15)
  print('[3/6] Wait for ComfyUI...',flush=True)
  deadline=time.monotonic()+240
  while not get('http://127.0.0.1:8188/system_stats'):
   if time.monotonic()>deadline:raise Failure('READINESS_TIMEOUT','ComfyUI did not become ready.','comfy')
   time.sleep(1)
  print('[4/6] Comfy bridge...',flush=True)
  if not get('http://172.18.0.1:18188/system_stats'):raise Failure('BRIDGE_UNAVAILABLE','The ComfyUI bridge is unavailable.','comfy_bridge')
  print('[5/6] Reference identity...',flush=True);self.run([str(P/'bin/aag-reference-status')],90)
  print('[6/6] Upscale...',flush=True);self.run([str(P/'bin/aag-upscale-start')],120)
 def stop(self):
  self.busy()
  # Fixed ownership set. Shared/control-plane services and identity watchers remain running.
  self.run([str(P/'bin/aag-upscale-stop')],60)
  self.run(['systemctl','--user','stop',USER_UNITS['comfy']],60)
  self.run(['systemctl','--user','stop',USER_UNITS['image_proxy'],USER_UNITS['comfy_bridge']],30)
  self.run(['sudo','-n','/usr/local/libexec/aag-image-hub-control','stop'],60)
def target_reached(components,target):
 return bool(components) and all(v['state']==target for v in components.values() if target!='OFF' or v.get('owned',True))
class Manager:
 def __init__(self,root=STATE,host=None):self.root=pathlib.Path(root);self.host=host or Host();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
 def observation(self):
  components=self.host.measure();phase=read_json(self.root/'operation.json');states=[x['state'] for x in components.values()];error=None;operation=None
  if phase.get('state') in ('STARTING','STOPPING'):
   if alive(phase.get('pid')) and phase.get('process_start')==process_start(phase.get('pid')) and time.time()<phase.get('deadline',0):state=phase['state'];operation={'id':phase['id'],'action':phase['action']}
   else:state='ERROR';error=Failure('INTERRUPTED_OPERATION','The previous operation was interrupted or timed out. Retry the requested action.').safe()
  elif phase.get('state')=='ERROR':state='ERROR';error=phase['error']
  elif all(s=='READY' for s in states):state='READY'
  elif target_reached(components,'OFF'):state='OFF'
  else:
   state='ERROR';failed=next((k for k,v in components.items() if v['state']!='READY'),'system');error=Failure('COMPONENT_UNAVAILABLE','A required image component is unavailable.',failed).safe()
  return {'schema':'aag.image.lifecycle.v1','state':state,'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'components':components,'operation':operation,'error':error}
 def operate(self,action):
  if action not in ('start','stop'):raise Failure('INVALID_ACTION','Only start and stop are supported.',retryable=False)
  with (self.root/'operation.lock').open('a+') as lock:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return self.observation()
   components=self.host.measure();target='READY' if action=='start' else 'OFF'
   if target_reached(components,target):
    # A READY no-op must still prove its approved release at the CLI boundary.
    try:
     if action=='start':self.host.verify()
     atomic(self.root/'operation.json',{'state':target})
    except Failure as e:atomic(self.root/'operation.json',{'state':'ERROR','error':e.safe()})
    return self.observation()
   phase={'id':uuid.uuid4().hex,'action':action,'state':'STARTING' if action=='start' else 'STOPPING','pid':os.getpid(),'deadline':time.time()+720,'process_start':process_start(os.getpid())}
   atomic(self.root/'operation.json',phase)
   try:
    getattr(self.host,action)();components=self.host.measure()
    if not target_reached(components,target):raise Failure('FINAL_HEALTH_FAILED','Final component health did not match the requested state.',next(k for k,v in components.items() if v['state']!=target))
    atomic(self.root/'operation.json',{'state':target})
   except Failure as e:atomic(self.root/'operation.json',{'state':'ERROR','error':e.safe()})
   except Exception:atomic(self.root/'operation.json',{'state':'ERROR','error':Failure('LIFECYCLE_FAILED','Lifecycle operation failed. Retry or inspect the private local log.').safe()})
   return self.observation()
def artifact_resolver():
 # Explicit sibling load also works in the sealed runtime under Python -I.
 import importlib.util
 spec=importlib.util.spec_from_file_location('aag_artifact_files',pathlib.Path(__file__).with_name('artifact_files.py'))
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return module.ArtifactFiles(STORAGE)
def artifact_host_timezone(localtime=pathlib.Path('/etc/localtime'), timezone_file=pathlib.Path('/etc/timezone'), zone_root=pathlib.Path('/usr/share/zoneinfo')):
 # Read the Ubuntu host configuration, never a container/browser timezone or
 # client-supplied offset. ZoneInfo/Intl apply the rules for the artifact date.
 from zoneinfo import ZoneInfo
 try:key=str(localtime.resolve(strict=True).relative_to(zone_root.resolve(strict=True)))
 except ValueError:
  key=timezone_file.read_text().strip()
  target=(zone_root/key).resolve(strict=True)
  if not target.is_relative_to(zone_root.resolve(strict=True)) or target.read_bytes()!=localtime.read_bytes():raise ValueError('Host timezone unavailable')
 ZoneInfo(key)
 return {'schema':'aag.host-timezone.v1','timezone':key}
class ControlServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
 daemon_threads=True
 def __init__(self,socket_path,manager,artifacts=None):
  self.manager=manager;self.mutex=threading.Lock();self.worker=None;self.artifacts=artifacts
  super().__init__(str(socket_path),Handler)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,code,data):
  raw=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/artifacts/timezone':
   try:return self.send(200,artifact_host_timezone())
   except Exception:return self.send(503,{'error':{'code':'HOST_TIMEZONE_UNAVAILABLE'}})
  if self.path!='/status':return self.send(404,{'error':{'code':'INVALID_ACTION'}})
  return self.send(200,self.server.manager.observation())
 def do_POST(self):
  if self.path in ('/artifacts/resolve','/artifacts/reveal'):
   try:
    if self.headers.get('Transfer-Encoding'):raise ValueError()
    n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=2048:raise ValueError()
    ref=json.loads(self.rfile.read(n))
   except ValueError:return self.send(400,{'error':{'code':'REFERENCE_INVALID'}})
   try:
    resolver=self.server.artifacts or artifact_resolver()
    return self.send(200,resolver.resolve(ref,reveal=self.path.endswith('/reveal')))
   except Exception as error:
    return self.send(getattr(error,'status',503),{'error':{'code':getattr(error,'code','ARTIFACT_UNAVAILABLE'),'message':'The current image file could not be accessed.'}})
  if self.path not in ('/start','/stop'):return self.send(404,{'error':{'code':'INVALID_ACTION'}})
  try:
   if self.headers.get('Transfer-Encoding'):raise ValueError()
   n=int(self.headers.get('Content-Length','0'))
   if n>2 or n<0 or json.loads(self.rfile.read(n) or b'{}')!={}:raise ValueError()
  except ValueError:return self.send(400,{'error':{'code':'ARGUMENTS_FORBIDDEN','message':'Lifecycle actions accept no arguments.'}})
  action=self.path[1:]
  with self.server.mutex:
   status=self.server.manager.observation()
   if self.server.worker and self.server.worker.poll() is None:
    op=read_json(self.server.manager.root/'operation.json');return self.send(202 if op.get('action')==action else 409,status)
   if status['state'] in ('STARTING','STOPPING'):return self.send(202 if status['operation']['action']==action else 409,status)
   if status['state']=='OFF' and action=='stop':return self.send(200,status)
   log=(self.server.manager.root/'last-operation.log').open('w');os.chmod(log.name,0o600)
   self.server.worker=subprocess.Popen([str(P/'bin'/('aag-image-'+action))],stdin=subprocess.DEVNULL,stdout=log,stderr=log);log.close()
   for _ in range(30):
    phase=read_json(self.server.manager.root/'operation.json')
    if phase.get('pid')==self.server.worker.pid or self.server.worker.poll() is not None:break
    time.sleep(.05)
   return self.send(202,self.server.manager.observation())
def main():
 if len(sys.argv)!=2 or sys.argv[1] not in ('status','start','stop','serve'):raise SystemExit('Usage: lifecycle.py {status|start|stop|serve}')
 manager=Manager();action=sys.argv[1]
 if action=='serve':
  SOCKET.unlink(missing_ok=True)
  with ControlServer(SOCKET,manager) as server:SOCKET.chmod(0o600);server.serve_forever()
 else:
  result=manager.observation() if action=='status' else manager.operate(action)
  print(json.dumps(result,ensure_ascii=False))
  if action!='status':
   target='READY' if action=='start' else 'OFF';print('AAG IMAGE SYSTEM — '+result['state']);raise SystemExit(0 if result['state']==target else 1)
if __name__=='__main__':main()
