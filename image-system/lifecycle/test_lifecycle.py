import importlib.util,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import lifecycle as l
class FakeHost:
 def __init__(self,state='OFF'):self.state=state;self.calls=[];self.failure=None;self.block=None;self.manager=None
 def measure(self):return {'comfy':{'state':self.state},'hub':{'state':self.state}}
 def verify(self):self.calls.append('verify')
 def start(self):
  self.calls.append('start')
  if self.block:self.block.wait(2)
  if self.failure:raise self.failure
  self.state='READY'
 def stop(self):
  self.calls.append('stop')
  if self.failure:raise self.failure
  self.state='OFF'
class LifecycleTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.host=FakeHost();self.m=l.Manager(self.tmp.name,self.host)
 def tearDown(self):self.tmp.cleanup()
 def test_user_bus_environment_without_desktop_variables(self):
  with patch.dict(l.os.environ,{},clear=True):
   env=l.user_bus_environment()
   if (Path('/run/user')/str(os.getuid())/'bus').exists():self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],'unix:path=/run/user/'+str(os.getuid())+'/bus')
 def test_unit_bus_failure_is_not_an_unknown_or_ready_unit(self):
  with patch.object(l.subprocess,'run',return_value=type('Result',(),{'stdout':'','returncode':1})()):
   self.assertEqual(l.Host().units(True,['a.service']),{'a.service':'unavailable'})
 def test_transitions_and_idempotency(self):
  self.assertEqual(self.m.observation()['state'],'OFF');self.assertEqual(self.m.operate('stop')['state'],'OFF');self.assertEqual(self.host.calls,[])
  self.assertEqual(self.m.operate('start')['state'],'READY');self.m.operate('start');self.assertEqual(self.host.calls,['start','verify'])
  self.assertEqual(self.m.operate('stop')['state'],'OFF');self.assertEqual(self.host.calls[-1],'stop')
 def test_inflight_duplicate_and_crossover(self):
  self.host.block=threading.Event();t=threading.Thread(target=lambda:self.m.operate('start'));t.start()
  for _ in range(100):
   if (Path(self.tmp.name)/'operation.json').exists():break
   time.sleep(.01)
  self.assertEqual(self.m.observation()['state'],'STARTING');self.assertEqual(self.m.operate('start')['state'],'STARTING');self.assertEqual(self.m.operate('stop')['state'],'STARTING')
  self.host.block.set();t.join();self.assertEqual(self.host.calls,['start']);self.assertEqual(self.m.observation()['state'],'READY')
 def test_failure_timeout_retry(self):
  self.host.failure=l.Failure('OPERATION_TIMEOUT','Timed out.','comfy');r=self.m.operate('start');self.assertEqual(r['state'],'ERROR');self.assertEqual(r['error']['code'],'OPERATION_TIMEOUT')
  self.host.failure=None;self.assertEqual(self.m.operate('start')['state'],'READY')
 def test_busy_stop_preserves_running_work(self):
  self.host.state='READY';self.host.failure=l.Failure('IMAGE_WORK_ACTIVE','Busy');self.assertEqual(self.m.operate('stop')['state'],'ERROR');self.assertEqual(self.host.state,'READY')
 def test_refresh_reads_runtime_not_saved_ready(self):
  l.atomic(Path(self.tmp.name)/'operation.json',{'state':'READY'});self.assertEqual(self.m.observation()['state'],'OFF')
  self.host.state='READY';self.assertEqual(l.Manager(self.tmp.name,self.host).observation()['state'],'READY')
 def test_stale_or_expired_operation_fails_closed(self):
  for pid,deadline in [(99999999,time.time()+50),(os.getpid(),time.time()-1)]:
   l.atomic(Path(self.tmp.name)/'operation.json',{'state':'STARTING','pid':pid,'deadline':deadline,'process_start':l.process_start(pid)});self.assertEqual(self.m.observation()['error']['code'],'INTERRUPTED_OPERATION')
 def test_invalid_actions(self):
  for action in ('restart','start; touch /tmp/x','../stop','status'):
   with self.assertRaises(l.Failure):self.m.operate(action)
 def test_stop_commands_only_owned_services(self):
  h=l.Host();calls=[];h.busy=lambda:None;h.run=lambda args,timeout=90:calls.append(args);h.stop()
  serialized=json.dumps(calls)
  for shared in l.SHARED:self.assertNotIn(shared,[arg for call in calls for arg in call])
  self.assertNotIn('kill',serialized);self.assertNotIn('shell',serialized)
 def test_only_unowned_reference_identity_work_blocks_stop(self):
  # Policy since 2026-10-26: owned engine work (ComfyUI queue, scheduler lease) is drained by
  # the stop itself; only reference-identity work in separate worker units can block it.
  h=l.Host();h.units=lambda user,names:{name:'inactive' for name in names}
  with patch.object(l,'STORAGE',Path(self.tmp.name)),patch.object(l,'get',return_value={'queue_running':[],'queue_pending':[],'busy':False}):
   h.busy()
   lease=Path(self.tmp.name)/'aag-image-agent-state/scheduler/lease';lease.mkdir(parents=True)
   h.busy()
   lease.rmdir();inbox=Path(self.tmp.name)/'aag-human-identity-state/inbox';inbox.mkdir(parents=True);(inbox/'queued.json').write_text('{}')
   with self.assertRaises(l.Failure) as error:h.busy()
  self.assertEqual(error.exception.code,'IMAGE_WORK_QUEUED')
 def test_unverifiable_reference_worker_blocks_stop(self):
  h=l.Host();h.units=lambda user,names:{name:'unavailable' for name in names}
  with patch.object(l,'STORAGE',Path(self.tmp.name)),patch.object(l,'get',return_value=None):
   with self.assertRaises(l.Failure) as error:h.busy()
  self.assertEqual(error.exception.code,'QUEUE_STATUS_UNAVAILABLE')
 def test_stale_relay_socket_does_not_report_healthy(self):
  import socket
  name=Path(self.tmp.name)/'stale.sock';sock=socket.socket(socket.AF_UNIX);sock.bind(str(name));sock.close()
  self.assertTrue(name.is_socket());self.assertFalse(l.relay_health(name))
 def test_subprocess_timeout_is_safe(self):
  with patch.object(l.subprocess,'run',side_effect=l.subprocess.TimeoutExpired(['secret'],1)):
   with self.assertRaises(l.Failure) as error:l.Host().run(['fixed'])
  self.assertNotIn('secret',json.dumps(error.exception.safe()))
if __name__=='__main__':unittest.main()
