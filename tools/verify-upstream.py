#!/usr/bin/env python3
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); cfg=json.load(open(sys.argv[2])); target=cfg['supported'][0]['targets']; bad=[]
for rel,want in target.items():
 p=root/rel
 if not p.is_file(): bad.append(f'MISSING {rel}'); continue
 got=hashlib.sha256(p.read_bytes()).hexdigest()
 if got!=want: bad.append(f'HASH_MISMATCH {rel} expected={want} actual={got}')
if bad: print('\n'.join(bad)); raise SystemExit(1)
print(f'UPSTREAM_PATCH_BASE=PASS files={len(target)}')
