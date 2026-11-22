"""Shared persistence and validation for operator-controlled components."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from enodectl_core import registry

CONFIG = Path('/etc/network-exit')
STATE = Path('/var/lib/network-exit')
UNITS = {s['name']: s['units'] for s in registry.services() if not s.get('mandatory')}


def service_spec(name):
    return next(s for s in registry.services() if s['name'] == name)


def save(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


def choices():
    path = CONFIG / 'components.json'
    return json.loads(path.read_text()) if path.exists() else {s['name']: True for s in registry.services() if s.get('mandatory')}


def validate(name):
    if name not in UNITS: raise ValueError('Select an optional installed service; mandatory services cannot be disabled')
    registry.resolve(service_spec(name)['validate'])()


def set_enabled(name, enabled):
    if name not in UNITS:
        raise ValueError('Select an optional installed service; mandatory services cannot be disabled')
    if enabled: validate(name)
    current = choices()
    current.update({s['name']: True for s in registry.services() if s.get('mandatory')})
    current[name] = enabled
    if enabled:
        if 'before_enable' in service_spec(name): registry.resolve(service_spec(name)['before_enable'])(current)
        subprocess.run(['systemctl', 'enable', *UNITS[name]], check=True, capture_output=True)
        subprocess.run(['systemctl', 'restart', *UNITS[name]], check=True, capture_output=True)
    else:
        # Keep the firewall active when disabling the tunnel.
        units = service_spec(name).get('disable_units', UNITS[name])
        subprocess.run(['systemctl', 'disable', '--now', *units], check=True, capture_output=True)
        if 'after_disable' in service_spec(name): registry.resolve(service_spec(name)['after_disable'])(current)
    save(CONFIG / 'components.json', json.dumps(current, indent=2) + '\n')
