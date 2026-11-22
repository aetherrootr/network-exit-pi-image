#!/usr/bin/env python3
"""Build a shiv archive from core and the stages selected in image_builder/stages.json."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from image_builder.stage_catalog import catalogue, runtime_manifest


def copy_sources(source, destination):
    for path in sorted(source.rglob('*')):
        if path.name == '.gitkeep' or '__pycache__' in path.parts or path.suffix == '.pyc': continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink(): raise ValueError('Python sources cannot be symlinks')
        if path.is_dir(): target.mkdir(parents=True, exist_ok=True)
        else:
            if target.exists(): raise ValueError('Duplicate Python asset: ' + str(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def package(root, target, python_version='3.13'):
    root, target = Path(root), Path(target)
    _, stages, owners = catalogue(root)
    if not any(stage['id'] == 'enodectl' for stage in stages): raise ValueError('The enodectl stage is required')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise ValueError('Controller archive already exists')
    with tempfile.TemporaryDirectory(prefix='enodectl-package-') as directory:
        work = Path(directory)
        payload = work / 'site-packages'
        payload.mkdir()
        requirements = []
        for stage in stages:
            folder = root / 'stage' / stage['id']
            copy_sources(folder / 'enodectl_component', payload)
            requirement_file = folder / 'requirements.txt'
            if requirement_file.exists():
                requirements += [line.strip() for line in requirement_file.read_text().splitlines()
                                 if line.strip() and not line.lstrip().startswith('#')]
        if requirements:
            dependencies = work / 'dependencies'
            dependencies.mkdir()
            inputs = work / 'requirements.txt'
            inputs.write_text('\n'.join(requirements) + '\n')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                            '--no-cache-dir', '--target', str(dependencies), '--only-binary=:all:', '--no-compile',
                            '--platform', 'manylinux2014_aarch64', '--implementation', 'cp',
                            '--python-version', python_version, '--abi', 'cp' + python_version.replace('.', ''),
                            '-r', str(inputs)], check=True)
            copy_sources(dependencies, payload)
        manifest = runtime_manifest(stages)
        manifest['python_version'] = python_version
        manifest['rootfs_assets'] = owners
        manifest['builder'] = {'shiv': importlib.metadata.version('shiv')}
        manifest['dependencies'] = {dist.metadata['Name']: dist.version
                                    for dist in importlib.metadata.distributions(path=[str(payload)])}
        (payload / 'enodectl_core/modules.json').write_text(json.dumps(manifest, indent=2) + '\n')
        temporary = work / 'enodectl.pyz'
        subprocess.run([sys.executable, '-m', 'shiv', '--site-packages', str(payload),
                        '--entry-point', 'enodectl_core.cli:main', '--python', '/usr/bin/python3',
                        '--reproducible', '--no-modify', '--output-file', str(temporary)], check=True)
        shutil.copy2(temporary, target)
        target.chmod(0o755)
        target.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--python-version', default='3.13')
    args = parser.parse_args()
    try: print(package(args.root, args.target, args.python_version))
    except (ValueError, OSError, subprocess.SubprocessError) as exc: parser.exit(1, f'Controller packaging failed: {exc}\n')


if __name__ == '__main__': main()
