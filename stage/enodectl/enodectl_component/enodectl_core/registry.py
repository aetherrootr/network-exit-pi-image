"""Load immutable build-selected stage registrations."""
import importlib
import importlib.util
from functools import lru_cache
import json
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def manifest():
    bundled = Path(__file__).with_name('modules.json')
    if bundled.exists(): return json.loads(bundled.read_text())
    # Source-tree development only. Devices always read bundled metadata.
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location('stage_catalog', root / 'image_builder/stage_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, stages, _ = module.catalogue(root)
    for stage in stages:
        folder = str(root / 'stage' / stage['id'] / 'enodectl_component')
        if folder not in sys.path: sys.path.insert(0, folder)
    return module.runtime_manifest(stages)


def stages(): return manifest()['stages']


def resolve(reference):
    module, function = reference.split(':', 1)
    return getattr(importlib.import_module(module), function)


def services(): return [stage['service'] for stage in stages() if 'service' in stage]


def hooks(name): return [resolve(stage[name]) for stage in stages() if name in stage]
