"""Noninteractive Tailscale/Headscale configuration and authorization checks."""
import json
import subprocess
from urllib.parse import urlsplit
import components
import network_config
import setup_state
import tailscale_control as control


def authorization_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in value): return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme == 'https' and parsed.hostname and parsed.username is None and parsed.password is None:
            return value
    except ValueError: pass
    return ''


def login_status(status, url=''):
    url = authorization_url(url or status.get('AuthURL'))
    lines = ['State: ' + status['BackendState'], 'Control server: ' + control.preferences()]
    if url:
        lines += ['Authorization URL:', url, 'Open the URL on another computer, then check status again.']
    elif status['BackendState'] == 'Running':
        lines.append('Authorization: connected; no login URL is needed.')
    else:
        lines.append('Authorization URL: not available yet.')
        lines.append('Check network connectivity, DNS and the control server. Use Network / Wi-Fi / firewall to configure an Internet uplink, then check status again or retry Apply configuration.')
    health = status.get('Health')
    if isinstance(health, list):
        lines.extend('Health: ' + item for item in health if isinstance(item, str) and item)
    return '\n'.join(lines)


def load():
    path = components.CONFIG / 'tailscale-control.json'
    target = control.control_url(json.loads(path.read_text())['login_server']) if path.exists() else control.preferences()
    return {'provider': 'official' if target == control.OFFICIAL else 'headscale',
            'headscale_url': target if target != control.OFFICIAL else '',
            'advertise_exit': bool(components.choices().get('tailscale_exit')), 'reauth_confirm': False}


def check():
    if control.backend_status()['BackendState'] != 'Running':
        raise ValueError('Complete Tailscale authorization before finishing setup')
    path = components.CONFIG / 'tailscale-control.json'
    if path.exists() and control.preferences() != control.control_url(json.loads(path.read_text())['login_server']):
        raise ValueError('Tailscale is not using the saved control plane')


def save(values):
    if values['provider'] not in ('official', 'headscale'): raise ValueError('Choose `official` or `headscale`')
    target = control.OFFICIAL if values['provider'] == 'official' else control.control_url(values['headscale_url'].strip())
    if values['provider'] == 'headscale' and target == control.OFFICIAL: raise ValueError('Choose `official` for the official control plane')
    current = control.preferences()
    before = control.backend_status()
    identity = before.get('HaveNodeKey', before['BackendState'] not in ('NoState', 'NeedsLogin'))
    switching = current != target and identity
    if switching and not values['reauth_confirm']:
        raise ValueError('Changing control plane may disconnect this session. Explicitly confirm reauthentication first.')
    complete = (components.CONFIG / 'initialized').exists()
    setup_state.invalidate(components.CONFIG)
    components.save(components.CONFIG / 'tailscale-control.json', json.dumps({'provider': values['provider'], 'login_server': target}) + '\n')
    selected = {**components.choices(), 'tailscale': True, 'tailscale_exit': bool(values['advertise_exit'])}
    network_config.sync(selected=selected)
    components.save(components.CONFIG / 'components.json', json.dumps(selected) + '\n')
    command = ['tailscale', 'up', '--json', '--timeout=5s', f'--login-server={target}',
               '--accept-dns=false', '--accept-routes=false', '--advertise-routes=', '--exit-node=', '--ssh=false',
               f'--advertise-exit-node={str(bool(values["advertise_exit"])).lower()}']
    if switching: command.append('--force-reauth')
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    status = control.backend_status()
    if status['BackendState'] == 'Running' and control.preferences() == target:
        if complete:
            from enodectl_core import registry
            for hook in registry.hooks('before_complete'): hook()
            setup_state.complete(components.CONFIG)
        return 'Tailscale is connected to the selected control plane. Device identity was retained unless reauthentication was confirmed.'
    # The CLI's JSON stream and local status can supply the authorization URL.
    url = authorization_url(status.get('AuthURL'))
    if not url:
        # `up --json` may emit more than one JSON object as the login progresses.
        pending = result.stdout.strip()
        decoder = json.JSONDecoder()
        while pending:
            try: item, end = decoder.raw_decode(pending)
            except ValueError: break
            if isinstance(item, dict): url = authorization_url(item.get('AuthURL')) or url
            pending = pending[end:].lstrip()
    if control.preferences() != target:
        raise ValueError('Tailscale is not using the requested control plane. No automatic fallback was attempted.\n' + login_status(status))
    if url:
        return 'Authorization is pending. After approval, use Check status and Finish / exit.\n\n' + login_status(status, url)
    raise ValueError('Tailscale authorization is incomplete. The requested control plane was retained for retry.\n\n' + login_status(status))


def details():
    return login_status(control.backend_status())
