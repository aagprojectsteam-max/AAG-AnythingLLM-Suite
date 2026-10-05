#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,hmac,json,sys
from pathlib import Path
from reference_validator import ReferenceError,antelope_detector,validate_reference
ROOT=Path('/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System');MODEL_ROOT=ROOT/'image-agent/phase-7/candidate/assets/insightface'
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(8<<20),b''):h.update(chunk)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--config',required=True);p.add_argument('--result',required=True);a=p.parse_args();target=Path(a.result);result={'schema_version':'aag.human-identity.reference-validation.v1','status':'FAIL','raw_embedding_written':False,'network_required':False,'model_root_source':'sealed-phase7'}
 try:
  request=json.loads(Path(a.request).read_text());config=json.loads(Path(a.config).read_text());validated=validate_reference(request['reference_path'],config['authorized_reference_roots'],antelope_detector(str(MODEL_ROOT)));digest=sha(validated.path)
  if not hmac.compare_digest(digest,str(request.get('reference_sha256',''))):raise ReferenceError('REFERENCE_CHANGED','Staged reference hash does not match trusted invocation provenance.')
  result.update(status='PASS',reference_path=validated.path,reference_sha256=digest,original_sha256=request.get('original_sha256'),reference_kind=request.get('reference_kind'),fixture_id=request.get('fixture_id'),identity_domain=request.get('identity_domain'),source_index=request.get('source_index'),bytes=validated.bytes,dimensions=[validated.width,validated.height],format=validated.format,face_count=validated.face_count,face_bbox=list(validated.face_bbox),detection_score=validated.detection_score)
 except ReferenceError as e:result.update(error_code=e.code,error=str(e))
 except Exception:result.update(error_code='REFERENCE_VALIDATION_TECHNICAL_FAILURE',error='Reference validation failed.')
 if target.exists():raise FileExistsError(target)
 target.parent.mkdir(parents=True,exist_ok=True);partial=target.with_name('.'+target.name+'.partial');partial.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');partial.chmod(0o600);partial.replace(target);print(json.dumps({k:result.get(k) for k in ('status','error_code','face_count')}));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':sys.exit(main())
