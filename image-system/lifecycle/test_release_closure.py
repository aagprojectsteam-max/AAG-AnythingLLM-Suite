import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
import release_closure as c
class ClosureTests(unittest.TestCase):
 def test_required_dependency_missing_even_when_manifest_resealed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for name in c.REQUIRED:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
   external=root/'external';external.write_text('fixed')
   m={'payload':[{'path':p} for p in c.REQUIRED],'runtime_closure':{'schema':'aag.runtime-closure.v1','files':list(c.REQUIRED),'external_files':[{'path':str(external),'sha256':c.hashlib.sha256(external.read_bytes()).hexdigest()}]}}
   self.assertTrue(c.verify(root,m))
   for name in c.REQUIRED[:2]:
    path=root/name;path.unlink()
    with self.assertRaises(ValueError):c.verify(root,m)
    path.write_text('{}')
   m['runtime_closure']['files'].remove(c.REQUIRED[0])
   with self.assertRaises(ValueError):c.verify(root,m)
 def test_isolated_runtime_off_on_and_missing_schema(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=Path(__file__).resolve().parents[1]
   for directory in ('integrations/model-neutral-compatibility','expert-prompt','schemas'):
    shutil.copytree(source/directory,root/'runtime/image-agent'/directory,ignore=shutil.ignore_patterns('__pycache__','tests','test_*','tools'))
   control=root/'control';control.mkdir();shutil.copy2(Path(c.__file__),control/'release_closure.py')
   cmd=[sys.executable,'-I','-B',str(control/'release_closure.py'),'smoke',str(root)]
   r=subprocess.run(cmd,capture_output=True,text=True,cwd=root);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['EXPERT_ON'],'PASS')
   (root/'runtime/image-agent/schemas/provider-task.schema.json').unlink()
   r=subprocess.run(cmd,capture_output=True,text=True,cwd=root);self.assertNotEqual(r.returncode,0)
