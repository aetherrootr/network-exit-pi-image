"""Resolve stage-owned sources without reconstructing a global source rootfs."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
tools = os.environ.get('ENODECTL_TEST_SHIV_PYTHON')
if tools:
    paths = json.loads(subprocess.check_output([tools, '-c', 'import json,sysconfig; print(json.dumps([sysconfig.get_path("purelib"),sysconfig.get_path("platlib")]))'], text=True))
    for path in paths:
        if path not in sys.path: sys.path.append(path)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'stage/enodectl/enodectl_component'))
from image_builder.stage_catalog import catalogue, copy_layer


def bootstrap(root=ROOT):
    _, stages, _ = catalogue(root)
    for stage in stages:
        path = str(root / 'stage' / stage['id'] / 'enodectl_component')
        if path not in sys.path: sys.path.insert(0, path)


def source_path(relative):
    relative = str(relative)
    if relative == 'usr/local/sbin/enodectl':
        return ROOT / 'stage/enodectl/enodectl_component/enodectl_core/cli.py'
    if relative == 'usr/lib/network-exit': return source_library()
    if relative == 'etc/systemd/system': return SourceDirectory(relative)
    name = Path(relative).name
    module = 'wireguard_tool.py' if name == 'network-exit-wg' else name.replace('-', '_')
    if not module.endswith('.py'): module += '.py'
    matches = list((ROOT / 'stage').glob('*/enodectl_component/' + module))
    if len(matches) == 1: return matches[0]
    matches = list((ROOT / 'stage').glob('*/rootfs_layer/' + relative))
    if len(matches) != 1: raise ValueError('No unique stage asset: ' + relative)
    return matches[0]


class SourceLibrary:
    def __truediv__(self, name): return source_path('usr/lib/network-exit/' + name)
    def __str__(self): return str(ROOT / 'stage/enodectl/enodectl_component')


def source_library(): return SourceLibrary()


class SourceDirectory:
    def __init__(self, relative): self.relative = relative
    def __truediv__(self, name): return source_path(self.relative + '/' + name)


def assemble_layers(target):
    _, stages, _ = catalogue(ROOT)
    for stage in stages: copy_layer(ROOT / 'stage' / stage['id'] / 'rootfs_layer', target)


bootstrap()
