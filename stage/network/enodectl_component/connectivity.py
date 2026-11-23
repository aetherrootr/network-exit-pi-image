"""STATE-backed Wi-Fi profiles and Netplan activation, without UI imports."""
import json
import os
from pathlib import Path
import re
import subprocess

import components

DEFAULT = {'wifi_enabled': False, 'wifi_interface': 'auto', 'wifi_ssid': '',
           'wifi_security': 'psk', 'wifi_password': '', 'wifi_hidden': False,
           'wifi_country': ''}
SYS_NET = Path('/sys/class/net')


def directory(): return components.CONFIG / 'connectivity'


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Invalid Wi-Fi settings')
    if any(type(value[name]) is not bool for name in ('wifi_enabled', 'wifi_hidden')):
        raise ValueError('Invalid Wi-Fi switch')
    for name in ('wifi_interface', 'wifi_ssid', 'wifi_security', 'wifi_password', 'wifi_country'):
        if not isinstance(value[name], str) or any(ord(c) < 32 or ord(c) == 127 for c in value[name]):
            raise ValueError('Wi-Fi fields must not contain control characters')
    device = value['wifi_interface']
    if device != 'auto' and not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', device):
        raise ValueError('Use `auto` or a Wi-Fi interface name')
    if device.startswith(('lo', 'wg', 'tailscale', 'tun', 'tap')):
        raise ValueError('Select a Wi-Fi interface, not a tunnel or loopback')
    if value['wifi_security'] not in ('psk', 'sae'):
        raise ValueError('Choose `psk` (WPA2) or `sae` (WPA3)')
    country = value['wifi_country']
    if country and not re.fullmatch(r'[A-Z]{2}|00', country):
        raise ValueError('Use an uppercase two-letter country code, `00`, or leave it blank')
    if len(value['wifi_ssid'].encode('utf-8')) > 32:
        raise ValueError('Wi-Fi SSID exceeds 32 UTF-8 bytes')
    password = value['wifi_password']
    if len(password.encode('utf-8')) > 64:
        raise ValueError('Wi-Fi password is too long')
    if value['wifi_enabled']:
        if not value['wifi_ssid']:
            raise ValueError('Supply a Wi-Fi SSID before enabling Wi-Fi')
        raw_psk = value['wifi_security'] == 'psk' and re.fullmatch(r'[0-9a-fA-F]{64}', password)
        if not raw_psk and not 8 <= len(password.encode('utf-8')) <= 63:
            raise ValueError('Supply an 8-63 byte Wi-Fi password (or a 64-digit hexadecimal WPA2 PSK)')
    return dict(value)


def load():
    path = directory() / 'profile.json'
    return validate(json.loads(path.read_text()) if path.exists() else dict(DEFAULT))


def form_values():
    return {**load(), 'wifi_password': ''}


def prepare(values):
    candidate = {name: values[name] for name in DEFAULT}
    previous = load()
    if (not candidate['wifi_password'] and candidate['wifi_ssid'] == previous['wifi_ssid']
            and candidate['wifi_security'] == previous['wifi_security']):
        candidate['wifi_password'] = previous['wifi_password']
    return validate(candidate)


def wireless_interface(profile):
    candidates = [p.name for p in sorted(SYS_NET.iterdir()) if (p / 'wireless').is_dir()]
    device = profile['wifi_interface']
    if device == 'auto':
        if len(candidates) != 1:
            raise ValueError('Select an explicit Wi-Fi interface when zero or multiple radios are present')
        device = candidates[0]
    if device not in candidates:
        raise ValueError('Selected Wi-Fi radio is unavailable; retain Ethernet and check its driver')
    return device


def netplan(profile):
    profile = validate(profile)
    network = {'version': 2, 'renderer': 'networkd', 'wifis': {}}
    if profile['wifi_enabled']:
        device = wireless_interface(profile)
        wifi = {'dhcp4': True, 'dhcp6': True, 'optional': True,
                'dhcp4-overrides': {'route-metric': 600}, 'dhcp6-overrides': {'route-metric': 600},
                'access-points': {profile['wifi_ssid']: {
                    'auth': {'key-management': profile['wifi_security'], 'password': profile['wifi_password']},
                    'hidden': profile['wifi_hidden']}}}
        if profile['wifi_country']: wifi['regulatory-domain'] = profile['wifi_country']
        # networkd's Netplan Wi-Fi renderer requires the actual device as ID;
        # unlike Ethernet it rejects a match: selector for Wi-Fi devices.
        network['wifis'][device] = wifi
    # JSON is a YAML subset. Serialization also prevents SSID/PSK YAML injection.
    return json.dumps({'network': network}, indent=2) + '\n'


def stage(profile):
    """Save durable backups before publishing a candidate read by Netplan."""
    root = directory()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    if (root / 'pending').exists(): raise ValueError('A Wi-Fi trial is already pending')
    previous = load()
    old_netplan = (root / 'netplan.yaml').read_text() if (root / 'netplan.yaml').exists() else netplan(previous)
    candidate = netplan(profile)
    components.save(root / 'previous-profile.json', json.dumps(previous) + '\n')
    components.save(root / 'previous-netplan.yaml', old_netplan)
    components.save(root / 'pending', '1\n')
    components.save(root / 'profile.json', json.dumps(profile) + '\n')
    components.save(root / 'netplan.yaml', candidate)


def activate():
    try:
        subprocess.run(['netplan', 'apply'], capture_output=True, check=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        # Netplan can include generated configuration/credentials in errors.
        raise ValueError('Wi-Fi activation failed; previous settings will be restored. Check the radio and configuration.') from None


def restore_files():
    root = directory()
    if not (root / 'pending').exists(): return False
    for previous, current in (('previous-profile.json', 'profile.json'), ('previous-netplan.yaml', 'netplan.yaml')):
        components.save(root / current, (root / previous).read_text())
    return True


def commit():
    # Removing pending first is the durable commit point. Orphaned backups are
    # harmless and are replaced by the next trial; boot never restores them.
    root = directory()
    (root / 'pending').unlink(missing_ok=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
    for name in ('previous-profile.json', 'previous-netplan.yaml'):
        (root / name).unlink(missing_ok=True)


def management_interfaces(device):
    result = {device} if device else set()
    links = json.loads(subprocess.check_output(['ip', '-d', '-j', 'link', 'show'], text=True, timeout=10))
    for link in links:
        name = link.get('ifname', '')
        if (link.get('link_type') == 'ether' and not link.get('linkinfo', {}).get('info_kind')
                and re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name)):
            result.add(name)
    return sorted(result)
