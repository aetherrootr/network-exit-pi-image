"""Strict DDNS profiles and one-shot updates through the broadband interface."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import components
import network_config

STATE = Path('/var/lib/network-exit/ddns')
PROVIDERS = ('cloudflare', 'duckdns', 'dyndns2')


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in value.split('.')):
        raise ValueError('Use an ASCII DNS name without a URL, port or wildcard')
    return value.lower()


def validate(profile):
    if set(profile) != {'provider', 'hostname', 'zone', 'server', 'login', 'secret', 'family', 'interval'}:
        raise ValueError('Invalid DDNS profile fields')
    if profile['provider'] not in PROVIDERS or profile['family'] not in ('ipv4', 'ipv6'):
        raise ValueError('Choose a supported DDNS provider and IPv4 or IPv6')
    hostname(profile['hostname'])
    if type(profile['interval']) is not int or not 300 <= profile['interval'] <= 86400:
        raise ValueError('DDNS interval must be 300 to 86400 seconds')
    for field in ('login', 'secret'):
        value = profile[field]
        if not isinstance(value, str) or len(value) > 2048 or any(not 32 <= ord(c) < 127 for c in value):
            raise ValueError('DDNS credentials must be printable ASCII without control characters')
    if not profile['secret']: raise ValueError('A DDNS token or password is required')
    if profile['provider'] == 'cloudflare':
        zone = hostname(profile['zone'])
        if profile['hostname'] != zone and not profile['hostname'].endswith('.' + zone):
            raise ValueError('The hostname must belong to the Cloudflare zone')
    if profile['provider'] == 'duckdns' and not profile['hostname'].endswith('.duckdns.org'):
        raise ValueError('Use the complete hostname ending in .duckdns.org')
    if profile['provider'] == 'dyndns2':
        hostname(profile['server'])
        if not re.fullmatch(r'[A-Za-z0-9@._+:-]+', profile['login']):
            raise ValueError('Use a DynDNS2 login containing letters, digits or @ . _ + : -')
    return dict(profile)


def load():
    return validate(json.loads((components.CONFIG / 'ddns.json').read_text()))


def quote(value):
    # ddclient's password lexer strips a single outer quote pair before parsing
    # comments/escapes. The printable single-line value must stay literal.
    return "'" + value + "'"


def render(profile, address):
    profile = validate(profile)
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.version != (4 if profile['family'] == 'ipv4' else 6):
        raise ValueError('DDNS requires a global address of the selected family')
    family = profile['family']
    lines = ['ssl=yes', 'daemon=0', 'syslog=no', 'timeout=30',
             'usev4=' + ('ipv4' if family == 'ipv4' else 'disabled'),
             'usev6=' + ('ipv6' if family == 'ipv6' else 'disabled'),
             family + '=' + str(ip), 'protocol=' + profile['provider']]
    if profile['provider'] == 'cloudflare':
        lines += ['zone=' + profile['zone'], 'login=token']
    elif profile['provider'] == 'dyndns2':
        lines += ['server=' + profile['server'], 'login=' + quote(profile['login'])]
    lines += ['password=' + quote(profile['secret']), profile['hostname'], '']
    return '\n'.join(lines)


def discover(family, device):
    network_config.interface(device)
    url = 'https://api.ipify.org' if family == 'ipv4' else 'https://api6.ipify.org'
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
                             '--interface', device, '-4' if family == 'ipv4' else '-6',
                             '--connect-timeout', '10', '--max-time', '20', '--max-filesize', '128', url],
                            capture_output=True, text=True, check=True, timeout=25,
                            env={**os.environ, 'http_proxy': '', 'https_proxy': '', 'ALL_PROXY': ''})
    address = ipaddress.ip_address(result.stdout.strip())
    if not address.is_global or address.version != (4 if family == 'ipv4' else 6):
        raise ValueError('The broadband address check did not return a global address of the selected family')
    return str(address)


def status():
    path = STATE / 'status.json'
    return json.loads(path.read_text()) if path.exists() else {}


def update():
    if not components.choices().get('ddns'): return
    profile = load()
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = status()
    now = time.time()
    if now >= previous.get('last_attempt', 0) and now - previous.get('last_attempt', 0) < profile['interval']:
        return
    result = {**previous, 'last_attempt': now, 'hostname': profile['hostname'], 'error': None}
    try:
        device = network_config.uplink(network_config.load())
        address = discover(profile['family'], device)
        # Credentials are in a private file, never command arguments or logs.
        with tempfile.TemporaryDirectory(prefix='ddns-', dir='/run') as directory:
            path = Path(directory) / 'ddclient.conf'
            components.save(path, render(profile, address))
            environment = {key: value for key, value in os.environ.items() if 'proxy' not in key.lower()}
            subprocess.run(['/usr/bin/ddclient', '-daemon=0', '-foreground', '-file=' + str(path),
                            '-cache=' + str(STATE / 'ddclient.cache')],
                           capture_output=True, check=True, timeout=90, env=environment)
        result.update(last_success=now, address=address, interface=device)
    except (ValueError, OSError, subprocess.SubprocessError):
        result['error'] = 'DDNS check/update failed; verify uplink, clock, provider and credentials'
        components.save(STATE / 'status.json', json.dumps(result) + '\n')
        raise ValueError(result['error']) from None
    components.save(STATE / 'status.json', json.dumps(result) + '\n')
