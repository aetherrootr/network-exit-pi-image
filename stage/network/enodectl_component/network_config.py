"""Persistent network policy, owned nftables tables and bounded trial changes."""
from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import importlib
import subprocess
import time
import uuid

import components
import connectivity

STATE = Path('/var/lib/network-exit')
TRIAL_SECONDS = 90
DEFAULT = {'firewall': False, 'uplink': 'auto',
           'ssh_sources': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', 'fc00::/7', 'fe80::/10'],
           'tcp_ports': [], 'udp_ports': []}


def validate(value):
    if set(value) != set(DEFAULT) or type(value['firewall']) is not bool:
        raise ValueError('Invalid network settings')
    interface(value['uplink'], allow_auto=True)
    if not isinstance(value['ssh_sources'], list) or not 1 <= len(value['ssh_sources']) <= 64:
        raise ValueError('Keep at least one SSH source network; Tailscale SSH access is also retained')
    sources = [str(ipaddress.ip_network(item, strict=False)) for item in value['ssh_sources']]
    for name in ('tcp_ports', 'udp_ports'):
        ports = value[name]
        if not isinstance(ports, list) or len(ports) > 64 or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
            raise ValueError('Use up to 64 port numbers between 1 and 65535')
    if 22 in value['tcp_ports']:
        raise ValueError('Configure SSH access with SSH source networks, not an unrestricted port 22 rule')
    return {**value, 'ssh_sources': sources}


def interface(name, allow_auto=False):
    if allow_auto and name == 'auto': return name
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name) or name.startswith(('lo', 'wg', 'tailscale', 'tun', 'tap')):
        raise ValueError('Select a broadband Ethernet/Wi-Fi interface, not a loopback or tunnel')
    return name


def uplink(settings):
    name = settings['uplink']
    if name == 'auto':
        routes = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'default'], text=True))
        previous = components.CONFIG / 'rules.nft'
        candidates = re.findall(r'iifname "wg-home" oifname "([A-Za-z0-9_.-]+)"', previous.read_text()) if previous.exists() else []
        routes = sorted(routes, key=lambda item: item.get('metric', 0))
        # Follow live routing before historical WG hints so Wi-Fi can take over.
        candidates = [r.get('dev', '') for r in routes] + candidates
        candidates += [p.name for p in sorted(Path('/sys/class/net').iterdir())]
        name = next((n for n in candidates if re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', n) and
                     not n.startswith(('lo', 'wg', 'tailscale', 'tun', 'tap'))), '')
    interface(name)
    links = json.loads(subprocess.check_output(['ip', '-d', '-j', 'link', 'show', 'dev', name], text=True))
    if not links or links[0].get('linkinfo', {}).get('info_kind') in ('wireguard', 'tun', 'gre', 'ipip', 'sit', 'vxlan'):
        raise ValueError('The selected broadband interface is unavailable or is a tunnel')
    return name


def load():
    path = components.CONFIG / 'network.json'
    return validate(json.loads(path.read_text()) if path.exists() else dict(DEFAULT))


def host_rules(settings, device, selected, management=None):
    if not settings['firewall']: return ''
    lines = ['table inet enode_host {', ' chain input {',
             '  type filter hook input priority 10; policy drop;',
             '  iifname "lo" accept', '  ct state established,related accept', '  ct state invalid drop',
             '  meta l4proto { icmp, ipv6-icmp } accept',
             '  iifname "tailscale0" accept']
    for link in management or [device]:
        interface(link)
        lines += [f'  iifname "{link}" udp sport 67 udp dport 68 accept',
                  f'  iifname "{link}" udp sport 547 udp dport 546 accept',
                  f'  iifname "{link}" udp dport 41641 accept',
                  f'  iifname "{link}" ip ttl 255 udp dport 5353 accept',
                  f'  iifname "{link}" ip6 hoplimit 255 udp dport 5353 accept']
        for source in settings['ssh_sources']:
            family = 'ip6' if ':' in source else 'ip'
            lines.append(f'  iifname "{link}" {family} saddr {source} tcp dport 22 accept')
    for protocol in ('tcp', 'udp'):
        if settings[protocol + '_ports']:
            ports = ', '.join(str(p) for p in settings[protocol + '_ports'])
            lines.append(f'  iifname "{device}" {protocol} dport {{ {ports} }} accept')
    lines += ['  counter drop comment "host-input-deny"', ' }', ' chain forward {', '  type filter hook forward priority 10; policy drop;',
              '  ct state established,related accept']
    if selected.get('wireguard'):
        lines.append(f'  iifname "wg-home" oifname "{device}" ip saddr 10.77.0.1 accept')
    if selected.get('tailscale_exit'):
        lines.append(f'  iifname "tailscale0" oifname "{device}" accept')
    return '\n'.join(lines + ['  counter drop comment "host-forward-deny"', ' }', '}', ''])


def apply(settings=None, selected=None):
    settings = validate(settings if settings is not None else load())
    selected = selected if selected is not None else components.choices()
    has_wg = (components.CONFIG / 'rules.nft').exists()
    device = uplink(settings) if settings['firewall'] or has_wg else None
    management = connectivity.management_interfaces(device) if settings['firewall'] else None
    rules = host_rules(settings, device, selected, management)
    tables = [('inet', 'enode_host')]
    if has_wg:
        wg_rules = importlib.import_module('wireguard_tool').home_nft(device)
        rules += wg_rules
        tables += [('inet', 'ne_home_guard'), ('ip', 'ne_home_nat')]
    prefix = ''
    for family, table in tables:
        result = subprocess.run(['nft', 'list', 'table', family, table], capture_output=True, check=False)
        if result.returncode == 0: prefix += f'delete table {family} {table}\n'
        elif result.returncode != 1: raise ValueError('Cannot inspect managed firewall tables')
    batch = prefix + rules
    if batch:
        subprocess.run(['nft', '-c', '-f', '-'], input=batch, text=True, check=True, capture_output=True)
        subprocess.run(['nft', '-f', '-'], input=batch, text=True, check=True, capture_output=True)
    # Guard rules are installed before any global forwarding is enabled.
    if has_wg:
        components.save(components.CONFIG / 'rules.nft', wg_rules)
        components.save(components.CONFIG / 'forwarding.conf',
                        'net.ipv4.conf.all.rp_filter=0\nnet.ipv4.conf.default.rp_filter=0\n'
                        f'net.ipv4.conf.{device}.rp_filter=0\n')
        subprocess.run(['sysctl', '-p', str(components.CONFIG / 'forwarding.conf')], check=True, capture_output=True)
    v4 = bool(selected.get('wireguard') or selected.get('tailscale_exit'))
    v6 = bool(selected.get('tailscale_exit'))
    args = ['sysctl', '-w']
    if v6: args += ['net.ipv6.conf.all.accept_ra=2', 'net.ipv6.conf.default.accept_ra=2']
    args += [f'net.ipv4.ip_forward={int(v4)}', f'net.ipv6.conf.all.forwarding={int(v6)}']
    subprocess.run(args, check=True, capture_output=True)
    if device:
        components.save(components.CONFIG / 'active-uplink', device + '\n')


@contextmanager
def lock():
    if os.geteuid() != 0 or not os.path.ismount(STATE):
        raise ValueError('Run with `sudo` on a node with STATE mounted')
    with (STATE / 'network.lock').open('w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _rollback(boot=False):
    pending = STATE / 'network-pending.json'
    if not pending.exists():
        if (connectivity.directory() / 'pending').exists(): connectivity.commit()
        return
    trial = json.loads(pending.read_text())
    previous = validate(trial['previous'])
    components.save(components.CONFIG / 'network.json', json.dumps(previous) + '\n')
    # Restore the old WG uplink hint before resolving an "auto" policy. The
    # candidate may have rewritten these persistent generated files already.
    for name, content in trial.get('generated', {}).items():
        if name not in ('rules.nft', 'forwarding.conf'):
            raise ValueError('Invalid saved network file')
        components.save(components.CONFIG / name, content)
    if connectivity.restore_files() and not boot: connectivity.activate()
    if not boot: apply(previous)
    pending.unlink()
    _sync_directory(STATE)
    if (connectivity.directory() / 'pending').exists(): connectivity.commit()


def rollback():
    with lock(): _rollback()


def restore():
    with lock():
        _rollback(boot=True)
        apply()


def sync(selected=None):
    with lock(): apply(selected=selected)


def begin(settings, link_settings=None):
    settings = validate(settings)
    candidate = connectivity.validate(link_settings) if link_settings is not None else None
    with lock():
        pending = STATE / 'network-pending.json'
        if pending.exists(): raise ValueError('A network trial is already pending confirmation')
        if (connectivity.directory() / 'pending').exists(): connectivity.commit()
        token = uuid.uuid4().hex
        generated = {name: (components.CONFIG / name).read_text() for name in ('rules.nft', 'forwarding.conf')
                     if (components.CONFIG / name).exists()}
        components.save(pending, json.dumps({'previous': load(), 'generated': generated, 'token': token,
                        'deadline': time.monotonic() + TRIAL_SECONDS}) + '\n')
        try:
            subprocess.run(['systemctl', 'restart', 'network-exit-network-rollback.timer'], check=True, capture_output=True)
            if candidate is not None and candidate != connectivity.load():
                connectivity.stage(candidate)
                connectivity.activate()
            apply(settings)
            components.save(components.CONFIG / 'network.json', json.dumps(settings) + '\n')
        except Exception:
            _rollback()
            raise
        return token


def confirm(token):
    with lock():
        path = STATE / 'network-pending.json'
        if not path.exists(): raise ValueError('The network trial was already rolled back')
        trial = json.loads(path.read_text())
        if token != trial['token'] or time.monotonic() >= trial['deadline']:
            _rollback()
            raise ValueError('Network confirmation expired; previous configuration restored')
        path.unlink()
        _sync_directory(STATE)
        if (connectivity.directory() / 'pending').exists(): connectivity.commit()
        subprocess.run(['systemctl', 'stop', 'network-exit-network-rollback.timer'], check=True, capture_output=True)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def refresh_uplink():
    with lock():
        if (STATE / 'network-pending.json').exists(): return
        settings = load()
        if not settings['firewall'] and not (components.CONFIG / 'rules.nft').exists(): return
        device = uplink(settings)
        path = components.CONFIG / 'active-uplink'
        if not path.exists() or path.read_text().strip() != device: apply(settings)


def diagnostics():
    profile = connectivity.form_values()
    lines = ['Direct Ethernet: enode.local; preferred IPv4LL address 169.254.77.1 (conflicts may select another address).',
             'Wi-Fi: ' + ('enabled' if profile['wifi_enabled'] else 'disabled'),
             'DHCP route metrics: Ethernet 100, Wi-Fi 600. Ethernet IPv6 RA metric: 100. No Internet probing/failover is performed.']
    for command in (['ip', '-brief', 'address'], ['ip', '-4', 'route'], ['ip', '-6', 'route']):
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
        lines += [' '.join(command), result.stdout.strip() or result.stderr.strip()]
    lines += ['Resolver configuration', Path('/etc/resolv.conf').read_text()[:2048]]
    return lines
