#!/usr/bin/env python3
"""Confirm a trial only after local state, network policy, SSH and the agent respond."""
import json
from pathlib import Path
import subprocess
import sys
from bootstate import current_slot, load
from trial import is_trial

try:
    if not is_trial():
        sys.exit(0)
    platform = json.loads(Path('/run/network-exit/platform.json').read_text())
    state = load(str(Path(platform['boot_control']).resolve(strict=True)))
    if state['ne_pending'] == current_slot():
        subprocess.run(['mountpoint', '-q', '/var/lib/network-exit'], check=True)
        subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'network-exit-network.service'], check=True)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'ssh.service'], check=True)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'tailscaled.service'], check=True)
        subprocess.run(['tailscale', 'status', '--json'], check=True, stdout=subprocess.DEVNULL)
        # No public ping, DNS reachability or peer handshake is a hard boot gate.
        subprocess.run(['rauc', 'status', 'mark-good'], check=True)
except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
    sys.exit(f'Trial boot not confirmed: {exc}')
