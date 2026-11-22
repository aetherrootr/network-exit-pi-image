"""Noninteractive Tailscale/Headscale configuration and authorization checks."""
import json
import subprocess
import components
import network_config
import setup_state
import tailscale_control as control


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
    if values['provider'] not in ('official', 'headscale'): raise ValueError('Choose official or headscale')
    target = control.OFFICIAL if values['provider'] == 'official' else control.control_url(values['headscale_url'].strip())
    if values['provider'] == 'headscale' and target == control.OFFICIAL: raise ValueError('Choose official for the official control plane')
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
    url = status.get('AuthURL', '')
    if not url:
        try: url = json.loads(result.stdout).get('AuthURL', '')
        except (ValueError, AttributeError): pass
    if isinstance(url, str) and url.startswith('https://') and not any(ord(c) < 32 for c in url):
        return 'Authorization is pending. Open this URL on an operator computer, then use Check status and Finish setup:\n' + url
    raise ValueError('Tailscale authorization is incomplete. Check status; the requested control plane was retained for retry.')


def details():
    status = control.backend_status()
    return '\n'.join(['State: ' + status['BackendState'], 'Control server: ' + control.preferences(),
                       'Authorization URL: ' + str(status.get('AuthURL', '(none)'))])
