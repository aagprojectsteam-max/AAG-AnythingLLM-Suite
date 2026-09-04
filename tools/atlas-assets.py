#!/usr/bin/env python3
import argparse, hashlib, json, pathlib, shutil, sys, tarfile
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def verify(root, manifest):
 errors=[]
 for e in manifest['entries']:
  for k in ('reference','thumbnail'):
   rel=pathlib.Path(e[k]['path']); rel=pathlib.Path(*rel.parts[1:]) if rel.parts and rel.parts[0]=='visual-atlas' else rel
   p=root/rel
   if not p.is_file(): errors.append(f'MISSING {rel}')
   elif p.stat().st_size!=e[k]['bytes'] or sha(p)!=e[k]['sha256']: errors.append(f'INCOMPATIBLE {rel}')
 print(f"ATLAS_ASSETS={'PASS' if not errors else 'FAIL'} expected=986 errors={len(errors)}")
 for x in errors[:20]: print(x)
 return not errors
ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['verify','install']); ap.add_argument('--source',required=True); ap.add_argument('--target'); ap.add_argument('--manifest',default=str(pathlib.Path(__file__).parents[1]/'atlas-assets-manifest.json')); ns=ap.parse_args()
m=json.load(open(ns.manifest)); src=pathlib.Path(ns.source).expanduser().resolve()
if ns.command=='verify': raise SystemExit(0 if verify(src,m) else 1)
if not ns.target: ap.error('--target is required for install')
if not verify(src,m): raise SystemExit(1)
dst=pathlib.Path(ns.target).expanduser(); dst.mkdir(parents=True,exist_ok=True)
for name in ('images','thumbs'): shutil.copytree(src/name,dst/name,dirs_exist_ok=True)
for name in ('atlas-manifest.json','preview-index.json','retrieval-aliases.json','product-assets.json','visual-taxonomy.json'):
 p=src/'manifest'/name
 if p.exists(): (dst/'manifest').mkdir(exist_ok=True); shutil.copy2(p,dst/'manifest'/name)
print(f'ATLAS_INSTALL=PASS target={dst}')

