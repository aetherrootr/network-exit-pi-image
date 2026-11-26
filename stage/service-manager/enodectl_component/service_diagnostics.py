"""Bounded, read-only diagnostics for registered services and exit health."""
import json
import re
import subprocess

import components
from enodectl_core import registry


def query(args, timeout=8):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, f'{args[0]} query failed or timed out'


def units(name):
    spec = components.service_spec(name)
    return list(dict.fromkeys([*spec['units'], *spec.get('diagnostic_units', [])]))


def status(name):
    spec = components.service_spec(name)
    enabled = spec.get('mandatory') or components.choices().get(name, False)
    lines = [spec['label'], 'Saved startup choice: ' + ('enabled' if enabled else 'disabled')]
    if not enabled: lines.append('Configuration may be saved, but the service is not started. Set Startup ON and apply in its configuration page.')
    for unit in units(name):
        code, output = query(['systemctl', 'show', '--no-pager', '--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,NRestarts,UnitFileState', unit])
        lines += ['', unit, output or f'Status unavailable (exit {code})']
    return '\n'.join(lines)


def redact(text):
    secrets = []
    for file in ('cloudflare-token', 'wireguard-device.key'):
        path = components.CONFIG / file
        if path.is_file(): secrets.append(path.read_text().strip())
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and re.search(r'password|secret|token|private.?key|uuid|psk', key, re.I): secrets.append(item)
                else: collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    for file in ('ddns.json', 'sing-box.json', 'connectivity/profile.json'):
        try: collect(json.loads((components.CONFIG / file).read_text()))
        except (OSError, ValueError): pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret: text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|(?:token|password|secret|private[_-]?key)\s*[:=]\s*)[^\s,;]+', r'\1[redacted]', text)
    return text


def logs(name):
    selected = units(name)
    if not selected: return 'No systemd units are registered for this component.'
    args = ['journalctl', '-b', '--no-pager', '-n', '60', '--output=short-iso']
    for unit in selected: args += ['-u', unit]
    code, output = query(args)
    return 'Recent logs (current boot; last 60 entries):\n' + redact(output or ('No log entries.' if not code else 'Logs unavailable.'))


def report(name):
    return status(name) + '\n\n' + logs(name)


def health():
    """All required and selected service units plus actual HTTPS and Tailscale."""
    errors = []
    code, _ = query(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*', '--connect-timeout', '4', '--max-time', '8', '-o', '/dev/null', 'https://www.cloudflare.com/cdn-cgi/trace'], timeout=10)
    if code: errors.append('Public Internet: HTTPS request to www.cloudflare.com failed. Check DNS, default route and system time.')
    for hook in registry.hooks('required_check'):
        try: hook()
        except (ValueError, OSError, subprocess.SubprocessError) as exc: errors.append(f'Required service check: {exc}')
    chosen = components.choices()
    active_units = [unit for spec in registry.services() if spec.get('mandatory') or chosen.get(spec['name']) for unit in spec.get('health_units', spec['units'])]
    if active_units:
        code, output = query(['systemctl', 'is-active', *active_units])
        states = output.splitlines()
        if len(states) != len(active_units): errors.append('Cannot determine all required service states.')
        else:
            errors.extend(f'{unit}: {state}' for unit, state in zip(active_units, states) if state != 'active')
    if errors: raise ValueError('Health check failed:\n' + '\n'.join(errors))
    return 'Health check passed: public HTTPS, Tailscale authorization and required/enabled service units.'
