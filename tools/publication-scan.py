#!/usr/bin/env python3
"""Fail closed on private/runtime payloads; print locations, never secret values."""
import hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else pathlib.Path(__file__).resolve().parents[1]).resolve()
config=json.loads((ROOT/'config/publication-scan-allowlist.json').read_text())
allowed={(r['path'],r['line_sha256'],r['kind']) for r in config['synthetic_test_lines']}
fixtures={r['path']:r['sha256'] for r in config['binary_fixtures']}
patterns={
 'credential':r'(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._~-]{20,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[A-Za-z0-9-]{20,})',
 'assignment':r'''(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|aws_secret_access_key)\s*[:=]\s*["'][A-Za-z0-9_./+=-]{12,}''',
 'jwt':r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{20,}',
 'credential-url':r'https?://[^\s/:@]{2,}:[^\s/@]{8,}@',
}
forbidden_suffixes={'.db','.sqlite','.sqlite3','.pem','.key','.crt','.gguf','.safetensors','.ckpt','.pt','.pth','.onnx','.bin','.param','.engine','.zip','.tar','.gz','.wal','.shm','.pyc','.log','.strace'}
forbidden_parts={'evaluation','backups','node_modules','__pycache__','.pytest_cache','generated-images','generated-files','browser-profile','outputs','uploads','conversations'}
findings=[];count=0;allow_hits=set()
for p in sorted(ROOT.rglob('*')):
 rel=p.relative_to(ROOT).as_posix()
 if '.git' in p.relative_to(ROOT).parts:continue
 if p.is_symlink():
  if not p.resolve().is_relative_to(ROOT):findings.append((rel,0,'external-symlink'))
  continue
 if not p.is_file():continue
 count+=1
 if forbidden_parts.intersection(p.relative_to(ROOT).parts) or p.suffix.lower() in forbidden_suffixes or p.name.endswith(('-wal','-shm','-journal')) or (p.name.startswith('.env') and p.name!='.env.example'):
  findings.append((rel,0,'forbidden-runtime-or-private-file'))
 b=p.read_bytes()
 if len(b)>5*1024*1024:findings.append((rel,0,'large-file-over-5MiB'))
 try:content=b.decode('utf-8')
 except UnicodeDecodeError:
  if fixtures.get(rel)!=hashlib.sha256(b).hexdigest():findings.append((rel,0,'unapproved-binary'))
  continue
 for line_number,line in enumerate(content.splitlines(),1):
  for kind,pattern in patterns.items():
   if not re.search(pattern,line):continue
   key=(rel,hashlib.sha256(line.encode()).hexdigest(),kind)
   if key in allowed:allow_hits.add(key)
   else:findings.append((rel,line_number,kind))
for path,line,kind in findings:print(f'{kind}: {path}:{line}')
print('PUBLICATION_SCAN='+('FAIL' if findings else 'PASS'))
print(f'FILES_SCANNED={count} REVIEWED_SYNTHETIC_LINES={len(allow_hits)}')
sys.exit(bool(findings))
