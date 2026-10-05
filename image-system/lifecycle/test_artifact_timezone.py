import http.client,json,pathlib,socket,tempfile,threading,unittest
from zoneinfo import ZoneInfo
import lifecycle

class TimezoneTests(unittest.TestCase):
 def test_actual_host_timezone_is_an_iana_zone(self):
  value=lifecycle.artifact_host_timezone();self.assertEqual(value['schema'],'aag.host-timezone.v1');ZoneInfo(value['timezone'])
 def test_host_symlink_and_copied_tzfile_resolve_same_iana_rules(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);zone_root=pathlib.Path('/usr/share/zoneinfo');local=root/'localtime';config=root/'timezone'
   local.symlink_to(zone_root/'Asia/Jerusalem');config.write_text('Wrong/Zone')
   self.assertEqual(lifecycle.artifact_host_timezone(local,config)['timezone'],'Asia/Jerusalem')
   local.unlink();local.write_bytes((zone_root/'Asia/Jerusalem').read_bytes());config.write_text('Asia/Jerusalem\n')
   self.assertEqual(lifecycle.artifact_host_timezone(local,config)['timezone'],'Asia/Jerusalem')
   config.write_text('UTC')
   with self.assertRaises(ValueError):lifecycle.artifact_host_timezone(local,config)
 def test_private_endpoint_is_read_only_and_rejects_client_zone_arguments(self):
  with tempfile.TemporaryDirectory() as d:
   socket_path=pathlib.Path(d)/'control.sock'
   with lifecycle.ControlServer(socket_path,None) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
     for target,code in [('/artifacts/timezone',200),('/artifacts/timezone?timezone=UTC',404)]:
      connection=http.client.HTTPConnection('localhost');connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.connect(str(socket_path));connection.request('GET',target);response=connection.getresponse();self.assertEqual(response.status,code);data=json.loads(response.read());connection.close()
      if code==200:self.assertEqual(data,lifecycle.artifact_host_timezone())
    finally:server.shutdown();thread.join(timeout=2)
