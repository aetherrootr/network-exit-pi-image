"""Check selected stage commands and systemd units in the installed image."""
import subprocess
from enodectl_core import registry


def verify():
    units = []
    for stage in registry.stages():
        if 'view' in stage.get('service', {}):
            registry.resolve(stage['service']['view'])
        units += stage.get('systemd_units', [])
        for check in stage.get('verify', []):
            result = subprocess.run(check['command'], capture_output=True, text=True, check=True)
            if check.get('contains') and check['contains'] not in result.stdout:
                raise ValueError('Installed capability check failed: ' + stage['id'])
            print(result.stdout.strip())
    subprocess.run(['systemd-analyze', 'verify', '--man=no', '--generators=no', *units], check=True)
