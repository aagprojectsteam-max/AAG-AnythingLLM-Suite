import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import release_gate as g
class ReleaseTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.release=self.root/'release';self.release.mkdir();self.target=self.root/'deployed';self.target.write_text('tested');self.frozen=self.root/'frozen';self.frozen.write_text('contract');self.asset=self.root/'asset';self.asset.write_bytes(b'image')
  taxonomy={'families':[{'id':'f'+str(i),'subfamilies':[{'id':'s'+str(j)} for j in range(17 if i<27 else 34)]} for i in range(28)]}
  (self.release/'taxonomy.json').write_text(json.dumps(taxonomy));(self.release/'runtime.js').write_text('tested');(self.release/'schema.json').write_text(json.dumps({'type':'object','properties':{'x':{'type':'string'}}}))
  self.m={'schema':'aag.approved-release.v1','release_id':'test','validation':{'result':'PASS'},'family_count':28,'style_count':493,'payload':[{'path':p.name,'sha256':g.sha(p)} for p in self.release.iterdir()],'frozen':[{'path':'frozen','sha256':g.sha(self.frozen)}],'atlas_assets':[{'path':'asset','sha256':g.sha(self.asset)}],'deployments':[{'source':'runtime.js','target':str(self.target)}],'schemas':['schema.json'],'bridge_mounts':[{'source':'bridge','destination':'/app/bridge'}],'taxonomy':'taxonomy.json','atlas_ids':[f['id']+'/'+s['id'] for f in taxonomy['families'] for s in f['subfamilies']]}
 def tearDown(self):self.temp.cleanup()
 def verify(self):return g.verify_manifest(self.release,self.m,self.root)
 def reseal_payload(self):self.m['payload']=[{'path':p.name,'sha256':g.sha(p)} for p in self.release.iterdir()]
 def test_approved_unchanged_and_dirty_source(self):
  self.assertEqual(self.verify()['RELEASE_INTEGRITY'],'PASS');(self.root/'development.py').write_text('legitimate changed source');self.assertEqual(self.verify()['RELEASE_INTEGRITY'],'PASS')
 def test_frozen_corruption(self):
  self.frozen.write_text('changed');self.assertRaises(g.IntegrityError,self.verify)
 def test_release_corruption(self):
  (self.release/'runtime.js').write_text('bad');self.assertRaises(g.IntegrityError,self.verify)
 def test_deployed_frontend_drift(self):
  self.target.write_text('bad');self.assertRaises(g.IntegrityError,self.verify)
 def test_missing_bridge(self):
  with patch.object(g.subprocess,'check_output',return_value='[]'):self.assertRaises(g.IntegrityError,g.verify_manifest,self.release,self.m,self.root,True,True)
 def test_stale_live_bridge_inode(self):
  self.m['bridge_mounts'][0]['sha256']='a'*64
  mounts=json.dumps([{'Source':'bridge','Destination':'/app/bridge','RW':False}])
  with patch.object(g.subprocess,'check_output',side_effect=[mounts,'b'*64+' /app/bridge']):self.assertRaises(g.IntegrityError,g.verify_manifest,self.release,self.m,self.root,True,True)
 def test_invalid_schema_even_rehashed(self):
  (self.release/'schema.json').write_text('{"type":"not-a-type"}');self.reseal_payload();self.assertRaises(Exception,self.verify)
 def test_missing_atlas_asset(self):
  self.asset.unlink();self.assertRaises(g.IntegrityError,self.verify)
 def test_invalid_mapping_and_counts(self):
  self.m['atlas_ids'][0]=self.m['atlas_ids'][1];self.assertRaises(g.IntegrityError,self.verify)
 def test_invalid_taxonomy_even_rehashed(self):
  data=g.load(self.release/'taxonomy.json');data['families'][0]['subfamilies'].pop();(self.release/'taxonomy.json').write_text(json.dumps(data));self.reseal_payload();self.assertRaises(g.IntegrityError,self.verify)
 def test_malformed_manifests_and_traversal(self):
  for key in ('payload','frozen','bridge_mounts','schemas'):
   m=copy.deepcopy(self.m);m[key]=[];self.assertRaises(g.IntegrityError,g.verify_manifest,self.release,m,self.root)
  self.m['payload'][0]['path']='../frozen';self.assertRaises(g.IntegrityError,self.verify)
 def test_resealed_tested_candidate_requires_exact_deployed_bytes(self):
  (self.release/'runtime.js').write_text('new tested candidate');self.reseal_payload();self.assertRaises(g.IntegrityError,self.verify);self.target.write_text('new tested candidate');self.assertEqual(self.verify()['RELEASE_INTEGRITY'],'PASS')
 def test_unsealed_bytecode_cache_fails_closed(self):
  cache=self.release/'__pycache__';cache.mkdir();(cache/'runtime.pyc').write_bytes(b'corrupt cache');self.assertRaises(g.IntegrityError,self.verify)
 def test_unlisted_release_file(self):
  (self.release/'injected.js').write_text('bad');self.assertRaises(g.IntegrityError,self.verify)
if __name__=='__main__':unittest.main()
