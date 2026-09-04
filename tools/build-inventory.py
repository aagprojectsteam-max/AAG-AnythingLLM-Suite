#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parents[1]).resolve()
excluded = {'.git', '__pycache__', '.pytest_cache'}
files = []
for path in sorted(root.rglob('*')):
    if (not path.is_file() and not path.is_symlink()) or any(part in excluded for part in path.parts):
        continue
    rel = path.relative_to(root).as_posix()
    if rel == 'COMPLETE-FILE-INVENTORY.json':
        continue
    if path.is_symlink():
        target = os.readlink(path)
        digest = hashlib.sha256(target.encode()).hexdigest()
        files.append({'repository_path': rel, 'type': 'symlink', 'target': target, 'size': len(target), 'sha256': digest})
    else:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({'repository_path': rel, 'type': 'file', 'size': path.stat().st_size, 'sha256': digest})
doc = {'schema_version': 1, 'generated_utc': '2026-09-04', 'file_count': len(files), 'files': files}
(root / 'COMPLETE-FILE-INVENTORY.json').write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')
