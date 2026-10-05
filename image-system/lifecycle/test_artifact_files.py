"""Security, current metadata, host reveal and historical artifact contracts."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from artifact_files import ArtifactFiles, ArtifactError, Desktop

NAME='img-33333333-3333-3333-3333-333333333333.png'
JOB='aag-11111111-1111-1111-1111-111111111111'
ART='artifact-22222222-2222-2222-2222-222222222222'

class FileTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='aag-host-files-');self.root=Path(self.temp.name)
  (self.root/'generated-images').mkdir();self.file=self.root/'generated-images'/NAME
  self.bytes=b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',512,256)+bytes(200)
  self.file.write_bytes(self.bytes);self.sha=hashlib.sha256(self.bytes).hexdigest()
  self.payload=dict(storageFilename=NAME,jobId=JOB,artifactId=ART,artifactSha256=self.sha,artifactWidth=512,artifactHeight=256)
  self.db=sqlite3.connect(self.root/'anythingllm.db');self.db.execute('CREATE TABLE workspace_chats (id INTEGER, workspaceId INTEGER, include INTEGER, response TEXT)')
  self.save();self.ref=dict(chatId=7,workspaceId=10,artifact=NAME,jobId=JOB,artifactId=ART)
  self.desktop=Mock();self.desktop.available.return_value=True;self.api=ArtifactFiles(self.root,self.desktop)
 def save(self):
  self.db.execute('DELETE FROM workspace_chats');self.db.execute('INSERT INTO workspace_chats VALUES (7,10,1,?)',(json.dumps({'outputs':[{'type':'imageGenerationCard','payload':self.payload}]}),));self.db.commit()
 def tearDown(self):self.db.close();self.temp.cleanup()
 def rejected(self,ref,code=None):
  with self.assertRaises(ArtifactError) as caught:self.api.resolve(ref,reveal=True)
  if code:self.assertEqual(caught.exception.code,code)
  self.desktop.reveal.assert_not_called()
 def test_details_and_reveal_same_real_canonical_file(self):
  f=self.api.resolve(self.ref)
  self.assertEqual(f['location'],{'scope':'HOST_LOCAL','value':str(self.file.resolve())})
  self.assertEqual(f['applicationPath'],'generated-images/'+NAME);self.assertTrue(f['exists']);self.assertTrue(f['localAccessible'])
  self.assertEqual((f['sizeBytes'],f['format'],f['width'],f['height'],f['sha256']),(len(self.bytes),'PNG',512,256,self.sha))
  shown=self.api.resolve(self.ref,reveal=True);self.assertEqual(shown,{'revealed':True,'file':f});self.desktop.reveal.assert_called_once_with(self.file.resolve())
 def test_new_numeric_filename_uses_same_authoritative_resolver(self):
  for name in ('3-7-20261005-040215.png','3-7-20261005-040215-2.png'):
   self.file=self.file.rename(self.file.with_name(name));self.payload['storageFilename']=name;self.save();self.ref['artifact']=name
   result=self.api.resolve(self.ref,reveal=True)['file']
   self.assertEqual(result['filename'],name);self.assertEqual(result['applicationPath'],'generated-images/'+name)
   self.assertEqual(result['location']['value'],str(self.file));self.assertEqual(result['sha256'],self.sha)
   self.desktop.reveal.assert_called_with(self.file)
 def test_exact_prefixed_names_and_actual_formats(self):
  fixtures=Path(__file__).parents[1]/'tests/fixtures/artifact-formats'
  for ext,fmt in [('png','PNG'),('jpg','JPEG'),('webp','WEBP')]:
   name='AAG-Image-3-7-20261005-041829.'+ext
   self.file=self.file.with_name(name);data=(fixtures/('verified.'+ext)).read_bytes();self.file.write_bytes(data)
   self.payload.update(storageFilename=name,artifactSha256=hashlib.sha256(data).hexdigest(),artifactWidth=32,artifactHeight=24);self.save();self.ref['artifact']=name
   result=self.api.resolve(self.ref,reveal=True)['file'];self.assertEqual(result['format'],fmt);self.assertEqual(result['filename'],name);self.assertEqual((result['width'],result['height']),(32,24));self.desktop.reveal.assert_called_with(self.file)
 def test_arbitrary_paths_and_unknown_keys_rejected(self):
  for key in ('path','command','root','sha256','storageFilename'):
   self.rejected({**self.ref,key:'/etc/passwd'},'REFERENCE_INVALID')
  for value in ('../../secret.png','/etc/passwd','img-'+('3'*36)+'.png','file:///tmp/test','img-33333333-3333-3333-3333-333333333333.png/../x'):
   self.rejected({**self.ref,'artifact':value},'REFERENCE_INVALID')
 def test_forged_or_unauthorized_or_stale_identity(self):
  for key,value in [('chatId',8),('workspaceId',11),('artifactId','artifact-44444444-4444-4444-4444-444444444444'),('jobId',None)]:self.rejected({**self.ref,key:value})
  self.db.execute('UPDATE workspace_chats SET include=0');self.db.commit();self.rejected(self.ref,'ARTIFACT_NOT_FOUND')
 def test_missing_file_never_returns_recorded_path(self):
  self.file.unlink();f=self.api.resolve(self.ref);self.assertEqual(f['state'],'NOT_FOUND');self.assertFalse(f['exists']);self.assertNotIn('location',f);self.assertNotIn('sha256',f);self.rejected(self.ref,'ARTIFACT_NOT_FOUND')
 def test_changed_file_never_returns_stale_metadata(self):
  self.file.write_bytes(self.bytes+b'changed');f=self.api.resolve(self.ref);self.assertEqual(f['state'],'CHANGED');self.assertNotIn('location',f);self.rejected(self.ref,'ARTIFACT_CHANGED')
 def test_symlink_inside_and_outside_root_rejected(self):
  self.file.unlink();outside=self.root/'unrelated.png';outside.write_bytes(self.bytes)
  for target in (outside,self.root/'generated-images'/'other.png'):
   target.write_bytes(self.bytes);self.file.symlink_to(target)
   self.assertEqual(self.api.resolve(self.ref)['state'],'NOT_FOUND');self.rejected(self.ref,'ARTIFACT_NOT_FOUND');self.file.unlink()
 def test_symlink_root_escape_rejected(self):
  self.file.unlink();self.file.parent.rmdir();outside=self.root/'elsewhere';outside.mkdir();(outside/NAME).write_bytes(self.bytes);self.file.parent.symlink_to(outside,target_is_directory=True)
  self.assertEqual(self.api.resolve(self.ref)['state'],'NOT_FOUND');self.rejected(self.ref,'ARTIFACT_NOT_FOUND')
 def test_file_swap_between_resolve_and_reveal_rejected(self):
  def swap():
   self.file.unlink();self.file.write_bytes(self.bytes);return True
  self.desktop.available.side_effect=swap;self.rejected(self.ref,'ARTIFACT_CHANGED')
 def test_directories_and_fifos_are_not_artifacts(self):
  self.file.unlink();self.file.mkdir();self.rejected(self.ref,'ARTIFACT_NOT_FOUND');self.file.rmdir();os.mkfifo(self.file);self.rejected(self.ref,'ARTIFACT_NOT_FOUND')
 def test_legacy_artifact_with_saved_storage_identity(self):
  for k in ('jobId','artifactId','artifactSha256'):self.payload.pop(k)
  self.save();ref={**self.ref,'jobId':None,'artifactId':None};self.assertEqual(self.api.resolve(ref)['sha256'],self.sha);self.assertTrue(self.api.resolve(ref,reveal=True)['revealed'])
 def test_missing_desktop_retains_path_and_rejects_reveal(self):
  self.desktop.available.return_value=False;f=self.api.resolve(self.ref);self.assertFalse(f['localAccessible']);self.assertEqual(f['location']['value'],str(self.file));self.rejected(self.ref,'DESKTOP_UNAVAILABLE')
 def test_reveal_failure_is_not_success(self):
  self.desktop.reveal.side_effect=ArtifactError('REVEAL_FAILED',503)
  with self.assertRaises(ArtifactError):self.api.resolve(self.ref,reveal=True)
 def test_db_missing_no_creation(self):
  self.db.close();(self.root/'anythingllm.db').unlink();self.rejected(self.ref,'ARTIFACT_NOT_FOUND');self.assertFalse((self.root/'anythingllm.db').exists())
 def test_desktop_requests_new_window_for_verified_file_no_shell(self):
  with patch('artifact_files.subprocess.run') as run:
   Desktop().reveal(self.file)
   self.assertEqual(len(run.call_args_list),2)
   self.assertEqual(run.call_args_list[0].args[0],['/usr/bin/gdbus','call','--session','--timeout','5','--dest','org.freedesktop.DBus','--object-path','/org/freedesktop/DBus','--method','org.freedesktop.DBus.StartServiceByName','org.gnome.Nautilus','0'])
   # --select reuses existing windows in Nautilus, defeating visible presentation.
   self.assertEqual(run.call_args.args[0],['/usr/bin/nautilus','--new-window','--',str(self.file)])
   self.assertNotIn('shell',run.call_args.kwargs);self.assertTrue(run.call_args.kwargs['check']);self.assertEqual(run.call_args.kwargs['timeout'],7)
 def test_desktop_launch_failure_and_timeout_fail_closed(self):
  import subprocess
  for error in (FileNotFoundError(),subprocess.CalledProcessError(1,'nautilus'),subprocess.TimeoutExpired('nautilus',7)):
   with patch.object(Desktop,'call',return_value='(uint32 1,)'),patch('artifact_files.subprocess.run',side_effect=error),self.assertRaises(ArtifactError) as caught:Desktop().reveal(self.file)
   self.assertEqual(caught.exception.code,'REVEAL_FAILED')
 def test_activation_failure_never_launches_unowned_gui(self):
  import subprocess
  for error in (FileNotFoundError(),subprocess.CalledProcessError(1,'gdbus'),subprocess.TimeoutExpired('gdbus',7)):
   with patch.object(Desktop,'call',side_effect=error),patch('artifact_files.subprocess.run') as run,self.assertRaises(ArtifactError) as caught:Desktop().reveal(self.file)
   self.assertEqual(caught.exception.code,'REVEAL_FAILED');run.assert_not_called()
 def test_capability_probe_does_not_activate_manager(self):
  with patch.dict(os.environ,{'DISPLAY':':test'}),patch('artifact_files.os.access',return_value=True),patch.object(Desktop,'call',return_value="(['org.freedesktop.FileManager1'],)") as call:
   self.assertTrue(Desktop().available());self.assertTrue(all('List' in c.args[2] for c in call.call_args_list))
  with patch.dict(os.environ,{},clear=True):self.assertFalse(Desktop().available())
  with patch.dict(os.environ,{'DISPLAY':':test'}),patch('artifact_files.os.access',return_value=False),patch.object(Desktop,'call') as call:
   self.assertFalse(Desktop().available());call.assert_not_called()


class IsolatedImportTests(unittest.TestCase):
 def test_host_module_loads_as_sealed_sibling_under_python_isolated_mode(self):
  import subprocess,sys,shutil
  with tempfile.TemporaryDirectory(prefix='aag-artifact-closure-') as d:
   root=Path(d);control=root/'control';control.mkdir();storage=root/'storage';storage.mkdir()
   for name in ('lifecycle.py','artifact_files.py'):shutil.copy2(Path(__file__).with_name(name),control/name)
   script='import importlib.util,pathlib; s=importlib.util.spec_from_file_location("control",'+repr(str(control/'lifecycle.py'))+');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.STORAGE=pathlib.Path('+repr(str(storage))+');r=m.artifact_resolver();assert r.root==m.STORAGE/"generated-images";print("PASS")'
   result=subprocess.run([sys.executable,'-I','-B','-c',script],cwd=root,capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'PASS')

if __name__=='__main__':unittest.main()
