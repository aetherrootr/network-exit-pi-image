"""Transactional configuration publication and service application; no UI imports."""
from dataclasses import dataclass
from typing import Callable
import subprocess
import components
import setup_state
from enodectl_core import registry


@dataclass
class TrialChange:
    message: str
    seconds: int
    confirm: Callable
    rollback: Callable


def publish(name, files, apply=None):
    old = {path: path.read_bytes() if path.exists() else None for path in files}
    completed = (components.CONFIG / 'initialized').exists()
    setup_state.invalidate(components.CONFIG)
    try:
        for path, content in files.items(): components.save(path, content)
        if name in components.UNITS: components.validate(name)
        if apply: apply()
        elif components.choices().get(name):
            subprocess.run(['systemctl', 'restart', *components.UNITS[name]], check=True, capture_output=True)
        if completed:
            for hook in registry.hooks('before_complete'): hook()
            setup_state.complete(components.CONFIG)
    except (ValueError, OSError, subprocess.SubprocessError):
        for path, content in old.items():
            if content is None: path.unlink(missing_ok=True)
            else: components.save(path, content.decode())
        if name in components.UNITS and components.choices().get(name) and all(value is not None for value in old.values()):
            try:
                subprocess.run(['systemctl', 'restart', *components.UNITS[name]], check=True, capture_output=True)
            except (OSError, subprocess.SubprocessError): pass
        # Old settings remain on disk. Completion stays invalid after an apply
        # failure; the operator can retry and run Finish setup once healthy.
        raise ValueError('Configuration could not be applied; previous files restored. Verify service state and retry.') from None
