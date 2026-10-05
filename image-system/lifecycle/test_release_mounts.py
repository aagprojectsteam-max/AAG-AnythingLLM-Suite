import copy,unittest,yaml
from release_mounts import add_mounts
from release_gate import IntegrityError
TEXT='''# keep this comment
services:
  anythingllm:
    image: unchanged
    environment:
      PRIVATE_SETTING: stays-local
    volumes:
      - /old:/app/old:ro # keep this too
      - /data:/app/storage
  unrelated:
    image: unchanged-too
'''
ROWS=[{'source':'/new','destination':'/app/new'}]
class MountTests(unittest.TestCase):
 def test_only_required_readonly_mount_added(self):
  result=add_mounts(TEXT,ROWS);expected=yaml.safe_load(TEXT);expected['services']['anythingllm']['volumes'].insert(0,'/new:/app/new:ro')
  self.assertEqual(yaml.safe_load(result),expected);self.assertIn('# keep this comment',result);self.assertIn('# keep this too',result)
 def test_idempotent_preserves_bytes(self):
  result=add_mounts(TEXT,ROWS);self.assertEqual(add_mounts(result,ROWS),result)
 def test_matching_existing_mount_needs_no_rewrite(self):self.assertEqual(add_mounts(TEXT,[{'source':'/old','destination':'/app/old'}]),TEXT)
 def test_conflicting_source_rejected(self):self.assertRaises(IntegrityError,add_mounts,TEXT,[{'source':'/wrong','destination':'/app/old'}])
 def test_existing_writable_mount_rejected(self):self.assertRaises(IntegrityError,add_mounts,TEXT,[{'source':'/data','destination':'/app/storage'}])
 def test_duplicate_target_rejected(self):self.assertRaises(IntegrityError,add_mounts,TEXT.replace('- /old:', '- /old:/app/old:ro\n      - /old:'),[{'source':'/old','destination':'/app/old'}])
 def test_long_form_readonly_mount_preserved(self):
  text=TEXT.replace('- /old:/app/old:ro # keep this too','- type: bind\n        source: /old\n        target: /app/old\n        read_only: true')
  self.assertEqual(add_mounts(text,[{'source':'/old','destination':'/app/old'}]),text)
if __name__=='__main__':unittest.main()
