"""Load, validate and apply sing-box configuration without terminal dependencies."""
import json
import subprocess
import tempfile
from pathlib import Path
import components
from service_config import publish
from singbox_setup import private_proxy


def load():
    path = components.CONFIG / 'sing-box.json'
    # Do not generate/change default credentials merely by opening a form.
    return {'config': path.read_text() if path.exists() else ''}


def save(values):
    text = values['config'].strip()
    config = json.loads(text) if text else private_proxy('127.0.0.1')
    content = json.dumps(config, indent=2) + '\n'
    with tempfile.TemporaryDirectory(dir=components.STATE) as directory:
        candidate = Path(directory) / 'config.json'
        components.save(candidate, content)
        result = subprocess.run(['sing-box', 'check', '-c', str(candidate)], capture_output=True, check=False)
        if result.returncode: raise ValueError('sing-box rejected the configuration; no files changed')
    publish('singbox', {components.CONFIG / 'sing-box.json': content})
    return 'sing-box configuration saved. Enabled service was restarted; credentials remain on STATE.'
