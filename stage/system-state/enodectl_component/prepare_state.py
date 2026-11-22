#!/usr/bin/env python3
"""Prepare identities, runtime platform paths and selected service activation."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from state_capacity import grow
from trial import is_trial
from accounts import apply_policy
import setup_state
import sshca

STATE = Path('/var/lib/network-exit')
cmdline = Path('/proc/cmdline').read_text()
match = re.search(r'(?:^|\s)network_exit.state=([0-9a-fA-F]{8})-04(?:\s|$)', cmdline)
if not match or not os.path.ismount(STATE):
    raise SystemExit('Missing A/B STATE mount; refusing ephemeral identity setup')
disk = match[1].lower()
apply_policy()
if not is_trial(cmdline):
    try:
        grow(f'{disk}-04')
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        # A failed expansion must not reset identities or prevent SSH recovery.
        print(f'STATE expansion deferred; existing data retained: {exc}', file=sys.stderr)
else:
    print('STATE expansion deferred during the bounded trial boot.')
(STATE / 'data').mkdir(mode=0o750, exist_ok=True)
runtime = Path('/run/network-exit')
runtime.mkdir(mode=0o755, exist_ok=True)
runtime.chmod(0o755)
setup_state.publish(STATE / 'config')
sshca.ensure(STATE / 'config')
platform = {'layout': 'ab-v2', 'boot_control': f'/dev/disk/by-partuuid/{disk}-03'}
(runtime / 'platform.json').write_text(json.dumps(platform) + '\n')
rauc = Path('/run/rauc')
rauc.mkdir(exist_ok=True)
template = Path('/usr/share/network-exit/rauc-system.conf.in').read_text()
(rauc / 'system.conf').write_text(template.replace('@DISK_ID@', disk))
for kind in ['ed25519', 'rsa']:
    key = STATE / f'ssh/ssh_host_{kind}_key'
    if not key.exists():
        subprocess.run(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(key)], check=True)
# Start only services selected by a completed initialization. No package installs.
config = STATE / 'config/components.json'
if config.exists():
    choices = json.loads(config.read_text())
    units = ['network-exit-network.service', 'tailscaled.service']
    for name, unit in [('wireguard', 'network-exit-guard.service'),
                       ('wireguard', 'wg-quick@wg-home.service'),
                       ('cloudflared', 'network-exit-cloudflared.service'),
                       ('singbox', 'network-exit-singbox.service'),
                       ('ddns', 'network-exit-ddns.timer')]:
        if choices.get(name): units.append(unit)
    # --no-block avoids ordering deadlocks with units ordered after this service.
    subprocess.run(['systemctl', 'start', '--no-block', *units], check=True)
