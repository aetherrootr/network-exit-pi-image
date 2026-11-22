"""Validate the ordered stage catalogue and assemble stage-owned image layers."""
import argparse
import json
from pathlib import Path
import re
import shutil


def catalogue(root):
    root = Path(root)
    plan = json.loads((root / 'image_builder/stages.json').read_text())
    if not isinstance(plan, list) or not plan:
        raise ValueError('Stage plan must be a non-empty JSON array')
    seen, modules, owners = set(), [], {}
    for name in plan:
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', name) or name in seen:
            raise ValueError('Invalid or duplicate stage ID')
        folder = root / 'stage' / name
        meta = json.loads((folder / 'stage.json').read_text())
        if any(key in meta for key in ('config_sections', 'service_actions', 'setup', 'setup_role')):
            raise ValueError('Register repeatable service views instead of legacy setup sections: ' + name)
        meta['systemd_units'] = sorted(path.name for path in (folder / 'rootfs_layer/etc/systemd/system').glob('*')
                                       if path.is_file() and path.suffix in ('.service', '.timer'))
        if meta['id'] != name or meta['context'] not in ('host-shell', 'host', 'chroot'):
            raise ValueError('Invalid stage metadata: ' + name)
        for field in ('title', 'description'):
            if not isinstance(meta.get(field), str) or not meta[field] or any(c in meta[field] for c in '|\n\r\0'):
                raise ValueError(f'{name} needs a one-line {field} without pipe separators')
        for dependency in meta.get('requires', []):
            if dependency not in seen: raise ValueError(f'{name} must follow {dependency}')
        for required in ('stage.json', 'install.sh'):
            path = folder / required
            if not path.is_file() or path.is_symlink(): raise ValueError(f'{name} needs a regular {required}')
        for optional in ('rootfs_layer', 'enodectl_component', 'requirements.txt', 'config'):
            path = folder / optional
            if path.is_symlink() or (path.exists() and
                    not (path.is_file() if optional == 'requirements.txt' else path.is_dir())):
                raise ValueError(f'{name} has an invalid {optional}')
        for path in (folder / 'rootfs_layer').rglob('*'):
            if path.name == '.gitkeep' or path.is_dir(): continue
            relative = path.relative_to(folder / 'rootfs_layer').as_posix()
            if path.is_symlink(): raise ValueError('Stage layers cannot supply symlinks: ' + relative)
            if relative in owners: raise ValueError(f'Duplicate image asset {relative}: {owners[relative]}, {name}')
            owners[relative] = name
        seen.add(name)
        modules.append(meta)
    for relative in owners:
        for parent in Path(relative).parents:
            if parent.as_posix() in owners:
                raise ValueError('An image asset overlaps a file parent: ' + relative)
    for meta in modules:
        for required in meta.get('runtime_requires', []):
            if required not in seen: raise ValueError(f'{meta["id"]} requires runtime stage {required}')
    for key in ('commands', 'private'):
        keys = set()
        for meta in modules:
            values = [v['name'] for v in meta.get(key, [])] if key == 'commands' else meta.get(key, {})
            for value in values:
                if value in keys: raise ValueError(f'Duplicate {key} registration: {value}')
                keys.add(value)
    python_modules = set()
    for meta in modules:
        source = root / 'stage' / meta['id'] / 'enodectl_component'
        for file in source.rglob('*.py'):
            if '__pycache__' in file.parts: continue
            parts = list(file.relative_to(source).with_suffix('').parts)
            if parts[-1] == '__init__': parts.pop()
            name = '.'.join(parts)
            if name in python_modules: raise ValueError('Duplicate Python module: ' + name)
            python_modules.add(name)
    references = []
    for meta in modules:
        references += [meta[key] for key in ('before_services', 'before_complete', 'required_check', 'status') if key in meta]
        for command in meta.get('commands', []):
            references += [command[key] for key in ('register', 'handler') if key in command]
        service = meta.get('service', {})
        references += [service[key] for key in ('validate', 'before_enable', 'after_disable', 'view') if key in service]
        if service and (not service.get('mandatory') and (not service.get('units') or not service.get('validate'))):
            raise ValueError('Service stages need units and optional-service validation: ' + meta['id'])
        for private in meta.get('private', {}).values():
            if 'module' in private and private['module'] not in python_modules:
                raise ValueError('Unknown private Python module: ' + private['module'])
            if 'call' in private: references.append(private['call'])
    for reference in references:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*', reference) or reference.split(':')[0] not in python_modules:
            raise ValueError('Unknown or invalid stage entrypoint: ' + reference)
    return plan, modules, owners


def copy_layer(source, target):
    source, target = Path(source), Path(target)
    for path in sorted(source.rglob('*')):
        if path.name == '.gitkeep': continue
        relative = path.relative_to(source)
        destination = target / relative
        # Refuse symlink traversal in the mounted base image too.
        for parent in [destination, *destination.parents]:
            if parent == target.parent: break
            if parent.is_symlink(): raise ValueError('Image layer traverses a symlink: ' + str(relative))
        if path.is_symlink(): raise ValueError('Unsupported layer symlink: ' + str(path))
        if path.is_dir():
            if not destination.exists():
                destination.mkdir(parents=True)
                destination.chmod(path.stat().st_mode & 0o777)
        else:
            if destination.exists() and not destination.is_file():
                raise ValueError('Image asset would replace a non-file: ' + str(relative))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def runtime_manifest(modules):
    return {'schema': 1, 'stages': modules}


def entries(root):
    _, stages, _ = catalogue(root)
    return [(meta['id'], meta['context'], meta['title'], meta['description'],
             'stage/' + meta['id'] + '/install.sh') for meta in stages]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'assets', 'layer'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--stage')
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    try:
        _, modules, owners = catalogue(args.root)
        if args.action == 'plan':
            for entry in entries(args.root): print('|'.join(entry))
        elif args.action == 'assets': print(json.dumps(owners, indent=2))
        else:
            if args.stage not in [m['id'] for m in modules] or args.target is None:
                raise ValueError('A selected stage and target are required')
            copy_layer(args.root / 'stage' / args.stage / 'rootfs_layer', args.target)
    except (ValueError, OSError, KeyError) as exc: parser.exit(1, f'Stage plan: {exc}\n')


if __name__ == '__main__': main()
