#!/usr/bin/env python3
"""Generate a reviewable WireGuard exit bundle for this Orange Pi only."""
import argparse
import base64
import os
from pathlib import Path
import re
import subprocess

BLOCKED = '0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4'


def key(value):
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Invalid WireGuard key') from exc
    if len(raw) != 32:
        raise argparse.ArgumentTypeError('WireGuard keys must be 32 bytes')
    return value


def endpoint(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', value):
        raise argparse.ArgumentTypeError('Use the remote peer IPv4 address or domain name without a port')
    return value


def interface(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', value):
        raise argparse.ArgumentTypeError('Invalid network interface name')
    return value


def private(path):
    return key(Path(path).read_text().strip())


def write(directory, name, text, mode=0o600):
    path = directory / name
    path.write_text(text)
    path.chmod(mode)


def port(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Port must be an integer') from exc
    if number not in range(1, 65536):
        raise argparse.ArgumentTypeError('Port must be between 1 and 65535')
    return number


def wireguard(priv, peer, host, peer_port):
    return f'''[Interface]
PrivateKey = {priv}
Address = 10.77.0.2/30
MTU = 1280
Table = off
PostUp = sysctl -w net.ipv4.conf.%i.rp_filter=0

[Peer]
PublicKey = {peer}
AllowedIPs = 10.77.0.1/32
Endpoint = {host}:{peer_port}
PersistentKeepalive = 25
'''


def home_nft(uplink):
    return f'''table inet ne_home_guard {{
 chain input {{
  type filter hook input priority -20; policy accept;
  iifname "wg-home" ct state established,related accept
  iifname "wg-home" counter drop comment "tunnel-local-input"
 }}
 chain forward {{
  type filter hook forward priority -20; policy accept;
  iifname "wg-home" meta nfproto ipv6 counter drop comment "tunnel-ipv6"
  iifname "wg-home" ip saddr != 10.77.0.1 counter drop comment "tunnel-source"
  iifname "wg-home" ip daddr {{ {BLOCKED} }} counter drop comment "tunnel-private-destination"
  iifname "wg-home" oifname "{uplink}" counter accept comment "tunnel-uplink"
  iifname "wg-home" counter drop
  iifname "{uplink}" oifname "wg-home" ip daddr 10.77.0.1 ct state established,related counter accept comment "tunnel-return"
  oifname "wg-home" counter drop comment "tunnel-untracked-return"
 }}
}}
table ip ne_home_nat {{
 chain postrouting {{
  type nat hook postrouting priority srcnat; policy accept;
  iifname "wg-home" ip saddr 10.77.0.1 oifname "{uplink}" counter masquerade comment "tunnel-nat"
 }}
}}
'''


def guard_script():
    tables = [('inet', 'ne_home_guard'), ('ip', 'ne_home_nat')]
    script = '#!/bin/bash\nset -euo pipefail\ntmp=$(mktemp)\ntrap \'rm -f "$tmp"\' EXIT\n{\n'
    for family, name in tables:
        script += f'if nft list table {family} {name} >/dev/null 2>&1; then echo "delete table {family} {name}"; fi\n'
    script += 'cat /etc/network-exit/rules.nft\n} > "$tmp"\nnft -c -f "$tmp"\nnft -f "$tmp"\n'
    return script


def apply_script():
    script = '''#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for cmd in wg wg-quick nft ip systemctl sysctl python3; do command -v "$cmd" >/dev/null; done
[[ ! -e /etc/wireguard/wg-home.conf ]] || { echo '/etc/wireguard/wg-home.conf already exists; merge it manually first.' >&2; exit 1; }
[[ ! -e /etc/network-exit/role ]] || { echo 'This host already has a configured role; review it manually before updating.' >&2; exit 1; }
install -d -m 700 /etc/wireguard /etc/network-exit
install -d /etc/sysctl.d /usr/local/sbin /etc/systemd/system
install -m 600 wg-home.conf /etc/wireguard/
install -m 600 rules.nft /etc/network-exit/rules.nft
install -m 600 forwarding.conf /etc/network-exit/forwarding.conf
cat > /usr/local/sbin/network-exit-guard <<'GUARD'
''' + guard_script() + '''GUARD
chmod 700 /usr/local/sbin/network-exit-guard
cat > /etc/systemd/system/network-exit-guard.service <<'UNIT'
[Unit]
Description=Home network exit firewall
Before=wg-quick@wg-home.service
After=network-pre.target nftables.service
Wants=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/network-exit-guard
# Intentionally keep the firewall when stopped.
[Install]
WantedBy=multi-user.target
UNIT
install -d /etc/systemd/system/wg-quick@wg-home.service.d
cat > /etc/systemd/system/wg-quick@wg-home.service.d/network-exit.conf <<'UNIT'
[Unit]
Wants=network-online.target
Requires=network-exit-guard.service
After=network-online.target network-exit-guard.service
StartLimitIntervalSec=0
[Service]
Restart=on-failure
RestartSec=10s
TimeoutStartSec=90s
TimeoutStopSec=30s
Environment=WG_ENDPOINT_RESOLUTION_RETRIES=5
UNIT
'''
    script += "printf '%s\\n' 'home' > /etc/network-exit/role\nsystemctl daemon-reload\nsystemctl enable --now network-exit-guard.service\n"
    script += '/usr/lib/network-exit/network-runtime sync-wg\nsysctl -p /etc/network-exit/forwarding.conf\n'
    script += 'systemctl enable --now wg-quick@wg-home.service\nwg show\n'
    return script


def render(args):
    priv = private(args.private_key)
    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory is not empty; use a new directory to avoid overwriting keys')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    write(out, 'wg-home.conf', wireguard(priv, args.peer_key, args.peer_host, args.peer_port))
    write(out, 'rules.nft', home_nft(args.uplink))
    forwarding = ('net.ipv4.conf.all.rp_filter=0\nnet.ipv4.conf.default.rp_filter=0\n'
                  f'net.ipv4.conf.{args.uplink}.rp_filter=0\n')
    write(out, 'forwarding.conf', forwarding, 0o644)
    write(out, 'apply.sh', apply_script(), 0o700)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='role', required=True)
    gen = sub.add_parser('keygen', help='Generate a unique device private key; print only the public key')
    gen.add_argument('path')
    home = sub.add_parser('home', help='Generate the Orange Pi exit configuration only')
    home.add_argument('--private-key', required=True)
    home.add_argument('--output', required=True)
    home.add_argument('--peer-key', required=True, type=key)
    home.add_argument('--peer-host', required=True, type=endpoint)
    home.add_argument('--peer-port', default=51820, type=port)
    home.add_argument('--uplink', required=True, type=interface)
    args = parser.parse_args()
    try:
        if args.role == 'keygen':
            path = Path(args.path)
            data = subprocess.check_output(['wg', 'genkey'], text=True).strip()
            with path.open('x') as stream:
                stream.write(data + '\n')
            path.chmod(0o600)
            print(subprocess.check_output(['wg', 'pubkey'], input=data + '\n', text=True).strip())
        else:
            render(args)
            print(f'Configuration generated at {Path(args.output).resolve()}; not yet applied.')
    except (ValueError, OSError, argparse.ArgumentTypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
