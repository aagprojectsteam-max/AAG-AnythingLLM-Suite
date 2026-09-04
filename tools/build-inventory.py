#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parents[1]).resolve()
excluded = {'.git', '__pycache__', '.pytest_cache'}
files = []
for path in sorted(root.rglob('*')):
    if not path.is_file() or any(part in excluded for part in path.parts):
        continue
    rel = path.relative_to(root).as_posix()
    if rel == 'COMPLETE-FILE-INVENTORY.json':
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files.append({'repository_path': rel, 'size': path.stat().st_size, 'sha256': digest})
doc = {'schema_version': 1, 'generated_utc': '2026-09-04', 'file_count': len(files), 'files': files}
(root / 'COMPLETE-FILE-INVENTORY.json').write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')

