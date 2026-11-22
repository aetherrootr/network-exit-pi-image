#!/usr/bin/env python3
"""Validate stage declarations and source syntax without executing stage code."""
import ast
import json
from pathlib import Path
import subprocess
from image_builder.stage_catalog import catalogue

ROOT = Path(__file__).resolve().parents[1]
_, stages, owners = catalogue(ROOT)
for stage in stages:
    folder = ROOT / 'stage' / stage['id']
    subprocess.run(['bash', '-n', str(folder / 'install.sh')], check=True)
    for area in ('enodectl_component', 'rootfs_layer', 'config'):
        for path in (folder / area).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts or path.name == '.gitkeep': continue
            text = path.read_text()
            text.encode('ascii')
            if area == 'config' and path.suffix == '.json': json.loads(text)
            if path.suffix == '.py' or text.startswith(('#!/usr/bin/python3', '#!/usr/bin/env python3')):
                ast.parse(text, filename=str(path))
            elif text.startswith('#!/bin/bash'): subprocess.run(['bash', '-n', str(path)], check=True)
            elif text.startswith('#!/bin/sh'): subprocess.run(['sh', '-n', str(path)], check=True)
print(f'Validated {len(stages)} stages and {len(owners)} owned image files.')
