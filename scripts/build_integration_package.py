#!/usr/bin/env python3
"""Build the complete custom integration; never create a partial overlay."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build(root=ROOT):
    source = root / 'custom_components/ariston'
    manifest = json.loads((source / 'manifest.json').read_text())
    if manifest['domain'] != 'ariston':
        raise ValueError('Unexpected integration domain')
    files = sorted(p for p in source.rglob('*') if p.is_file()
                   and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for path in files:
        if path.suffix == '.py':
            compile(path.read_bytes(), str(path), 'exec')
        elif path.suffix == '.json':
            json.loads(path.read_text())
    for name in ('__init__.py', 'manifest.json', 'sensor.py',
                 'accounting_runtime.py', 'accounting_ledger.py'):
        if source / name not in files:
            raise ValueError('Incomplete integration package: '+name)
    target = root / 'dist/ariston.zip'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:
            raise RuntimeError('Archive verification failed')
        for path in files:
            if z.read(path.relative_to(root).as_posix()) != path.read_bytes():
                raise RuntimeError('Archive content mismatch: '+str(path))
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(checksum+'  ariston.zip\n')
    print(f'{target}: {len(files)} files, SHA256 {checksum}')
    return target

if __name__ == '__main__':
    build()
