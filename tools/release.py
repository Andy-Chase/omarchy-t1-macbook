#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Create a deterministic source-only release archive with per-file checksums."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = 'omarchy-t1-macbook-0.1.0-rc1'


def included(path):
    rel = path.relative_to(ROOT)
    if not path.is_file() or path.is_symlink():
        return False
    if rel.parts[0] not in {'touchbar', 'runtime', 'tools', 'tests', 'drivers', 'docs', 'patches', 'LICENSES', '.github'}:
        return len(rel.parts) == 1 and rel.name in {'Makefile', 'README.md', 'LICENSE', 'CREDITS.md', 'CHANGELOG.md', '.gitignore'}
    if any(x.startswith('.') for x in rel.parts[1:]) or '__pycache__' in rel.parts or path.name.endswith('.mod.c'):
        return False
    if rel.parts[:2] == ('docs', 'previews') and path.suffix == '.png':
        return True
    return path.suffix in {'.py', '.c', '.h', '.md', '.json', '.txt', '.patch', '.yml', '.yaml'} or path.name in {'Makefile', 'apple-t1-touchbar-bind'}


def main():
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    paths = sorted(p for p in ROOT.rglob('*') if included(p))
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    archive = output / (TOP + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in [(str(p.relative_to(ROOT)), p.read_bytes()) for p in paths] + [('SOURCE-SHA256.json', (json.dumps(manifest, indent=2) + '\n').encode())]:
            info = zipfile.ZipInfo(TOP + '/' + name, date_time=(2026, 11, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(checksum + '  ' + archive.name + '\n')
    print(archive)
    print('Source files:', len(paths))
    print('SHA256:', checksum)


if __name__ == '__main__':
    main()
