"""Repeatable WireGuard configuration; never rotate an existing device key."""
import configparser
import json
import subprocess
import components
import network_config
from service_config import publish
import wireguard_tool as wg


def load():
    values = {'peer_key': '', 'peer_host': '', 'peer_port': '51820'}
    path = components.CONFIG / 'wireguard/wg-home.conf'
    if path.exists():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(path.read_text())
        endpoint = parser['Peer']['Endpoint'].rsplit(':', 1)
        values.update(peer_key=parser['Peer']['PublicKey'], peer_host=endpoint[0], peer_port=endpoint[1])
    return values


def save(values):
    peer = wg.key(values['peer_key'].strip())
    host = wg.endpoint(values['peer_host'].strip())
    port = wg.port(values['peer_port'].strip())
    device = network_config.uplink(network_config.load())
    path = components.CONFIG / 'wireguard-device.key'
    if not path.exists():
        key = subprocess.check_output(['wg', 'genkey'], text=True).strip()
        wg.key(key)
        components.save(path, key + '\n')
    private = wg.private(path)
    files = {components.CONFIG / 'wireguard/wg-home.conf': wg.wireguard(private, peer, host, port),
             components.CONFIG / 'rules.nft': wg.home_nft(device),
             components.CONFIG / 'forwarding.conf': f'net.ipv4.conf.all.rp_filter=0\nnet.ipv4.conf.default.rp_filter=0\nnet.ipv4.conf.{device}.rp_filter=0\n'}
    def apply():
        network_config.sync()
        if components.choices().get('wireguard'):
            subprocess.run(['systemctl', 'restart', 'network-exit-guard.service', 'wg-quick@wg-home.service'], check=True, capture_output=True)
    publish('wireguard', files, apply=apply)
    return 'WireGuard settings saved. Existing device identity was retained. Review its public key before enabling.'


def details():
    path = components.CONFIG / 'wireguard-device.key'
    if not path.exists(): return 'No device key yet. Saving the first valid peer generates one.'
    text = 'Device public key: ' + subprocess.check_output(['wg', 'pubkey'], input=wg.private(path) + '\n', text=True, timeout=5).strip()
    from service_diagnostics import query
    for field in ('latest-handshakes', 'transfer'):
        code, result = query(['wg', 'show', 'wg-home', field], timeout=3)
        text += '\n' + field + ':\n' + (result if not code else 'Interface not running or data unavailable.')
    return text
