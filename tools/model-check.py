#!/usr/bin/env python3
import argparse, os, pathlib, re
p=argparse.ArgumentParser(); p.add_argument('--models', required=True); p.add_argument('--comfyui'); p.add_argument('--llamacpp'); a=p.parse_args()
root=pathlib.Path(a.models).expanduser()
checks=[
 ('image:flux2-klein-4b','flux-2-klein-4b-fp8.safetensors','REQUIRED'),
 ('image:qwen3-text-encoder','qwen_3_4b.safetensors','REQUIRED'),
 ('image:flux2-vae','flux2-vae.safetensors','REQUIRED'),
 ('identity:pulid-v1.1','pulid_v1.1.safetensors','OPTIONAL'),
 ('identity:juggernaut-xl-v9','Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors','OPTIONAL'),
]
found={p.name for p in root.rglob('*') if p.is_file()} if root.exists() else set()
for ident,name,kind in checks: print(f"MODEL {ident}={'FOUND' if name in found else 'MISSING'} requirement={kind} filename={name}")
gguf=list(root.rglob('*.gguf')) if root.exists() else []
print(f"MODEL local-llm={'FOUND' if gguf else 'OPTIONAL'} count={len(gguf)} relationship=mmproj-and-MTP-must-match-selected-model")

