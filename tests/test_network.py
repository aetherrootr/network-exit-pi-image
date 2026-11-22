from _stage_support import source_path, source_library, assemble_layers
"""Test the device firewall/NAT with synthetic packets in an isolated network."""
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run(*command, **kwargs):
    check = kwargs.pop('check', True)
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if check and result.returncode:
        raise RuntimeError(f'{command}: {result.stdout}{result.stderr}')
    return result


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack(f'!{len(data) // 2}H', data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def isolated_checks(parent_namespace):
    # Positively verify isolation before any network mutation. No host routes or
    # firewall rules, external endpoints, hardware devices or service manager are used.
    if os.readlink('/proc/self/ns/net') == parent_namespace or os.geteuid() != 0:
        raise RuntimeError('Refusing to run outside the isolated root-mapped network namespace')
    config = runpy.run_path(str(source_path('usr/local/sbin/network-exit-wg')))
    for name, fixture_port in [('wg-home', 'inject0'), ('end0', 'capture0')]:
        run('ip', 'link', 'add', name, 'type', 'veth', 'peer', 'name', fixture_port)
        run('ip', 'link', 'set', name, 'up')
        run('ip', 'link', 'set', fixture_port, 'up')
    run('ip', 'link', 'set', 'lo', 'up')
    run('ip', 'address', 'add', '10.77.0.2/30', 'dev', 'wg-home')
    run('ip', 'address', 'add', '192.168.1.2/24', 'dev', 'end0')
    run('ip', 'route', 'add', 'default', 'via', '192.168.1.1', 'dev', 'end0')
    run('sysctl', '-w', 'net.ipv4.ip_forward=1', 'net.ipv4.conf.all.rp_filter=0',
        'net.ipv4.conf.wg-home.rp_filter=0', 'net.ipv4.conf.end0.rp_filter=0',
        'net.ipv4.conf.inject0.forwarding=0', 'net.ipv4.conf.capture0.forwarding=0')
    addresses = {item['ifname']: item['address'] for item in json.loads(run('ip', '-j', 'link', 'show').stdout)}
    run('ip', 'neigh', 'add', '10.77.0.1', 'lladdr', addresses['inject0'], 'nud', 'permanent', 'dev', 'wg-home')
    run('ip', 'neigh', 'add', '192.168.1.1', 'lladdr', addresses['capture0'], 'nud', 'permanent', 'dev', 'end0')
    run('nft', '-f', '-', input='table inet untouched {\n chain keep {\n }\n}\n')

    with tempfile.TemporaryDirectory(prefix='network-exit-home-network-test-') as tmp:
        nft_file = Path(tmp) / 'rules.nft'
        nft_file.write_text(config['home_nft']('end0'))
        sys.path.insert(0, str(ROOT / 'tests'))
        import components
        import network_config
        components.CONFIG = Path(tmp)
        network_config.apply({**network_config.DEFAULT, 'firewall': True,
                              'uplink': 'end0', 'udp_ports': [58000]}, {'wireguard': True})
        run('nft', '-f', '-', input='table inet early_accept {\n chain forward {\n type filter hook forward priority -100; policy accept;\n }\n}\n')
        guard = config['guard_script']().replace('/etc/network-exit/rules.nft', shlex.quote(str(nft_file)))
        run('bash', '-c', guard)
        run('bash', '-c', guard)
        run('nft', 'list', 'table', 'inet', 'untouched')
        assert 'end0' in run('ip', 'route', 'get', '9.9.9.9').stdout

        def counter(chain, comment):
            data = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'ne_home_guard', chain).stdout)
            for entry in data['nftables']:
                rule = entry.get('rule', {})
                if rule.get('comment') == comment:
                    return sum(expr.get('counter', {}).get('packets', 0) for expr in rule['expr'])
            raise AssertionError(comment)

        def host_counter(chain, comment):
            data = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'enode_host', chain).stdout)
            for entry in data['nftables']:
                rule = entry.get('rule', {})
                if rule.get('comment') == comment:
                    return sum(expr.get('counter', {}).get('packets', 0) for expr in rule['expr'])
            raise AssertionError(comment)

        def inject(port, source, destination, source_port=40000, destination_port=53):
            payload = b'isolated-home-exit-fixture'
            udp = struct.pack('!HHHH', source_port, destination_port, 8 + len(payload), 0) + payload
            header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), 1, 0,
                                 64, 17, 0, socket.inet_aton(source), socket.inet_aton(destination))
            header = header[:10] + struct.pack('!H', checksum(header)) + header[12:]
            receiving = 'wg-home' if port == 'inject0' else 'end0'
            ethernet = bytes.fromhex(addresses[receiving].replace(':', ''))
            ethernet += bytes.fromhex(addresses[port].replace(':', '')) + b'\x08\x00'
            with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800)) as stream:
                stream.bind((port, 0))
                stream.send(ethernet + header + udp)

        def receive(stream, source, destination):
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    frame = stream.recv(2048)
                except socket.timeout:
                    continue
                if len(frame) < 42 or frame[12:14] != b'\x08\x00' or frame[23] != 17:
                    continue
                if socket.inet_ntoa(frame[26:30]) == source and socket.inet_ntoa(frame[30:34]) == destination:
                    offset = 14 + (frame[14] & 0x0f) * 4
                    return struct.unpack_from('!HH', frame, offset)
            raise AssertionError(f'Expected forwarded UDP packet {source} -> {destination}')

        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800)) as outgoing, \
                socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800)) as returning:
            outgoing.bind(('capture0', 0))
            returning.bind(('inject0', 0))
            outgoing.settimeout(0.1)
            returning.settimeout(0.1)
            inject('inject0', '10.77.0.1', '9.9.9.9')
            nat_port, _ = receive(outgoing, '192.168.1.2', '9.9.9.9')
            assert counter('forward', 'tunnel-uplink') > 0
            inject('capture0', '9.9.9.9', '192.168.1.2', 53, nat_port)
            assert receive(returning, '9.9.9.9', '10.77.0.1') == (53, 40000)
            assert counter('forward', 'tunnel-return') > 0

        for chain, comment, port, source, destination in [
            ('forward', 'tunnel-source', 'inject0', '10.77.0.9', '9.9.9.9'),
            ('forward', 'tunnel-private-destination', 'inject0', '10.77.0.1', '192.168.1.99'),
            ('input', 'tunnel-local-input', 'inject0', '10.77.0.1', '10.77.0.2'),
            ('forward', 'tunnel-untracked-return', 'capture0', '8.8.8.8', '10.77.0.1'),
        ]:
            before = counter(chain, comment)
            inject(port, source, destination, 45000, 45001)
            for _ in range(20):
                if counter(chain, comment) > before:
                    break
                time.sleep(0.01)
            assert counter(chain, comment) > before, comment
        run('bash', '-c', guard)
        run('nft', 'list', 'table', 'inet', 'untouched')
        # Exercise the host firewall with real local deliveries and forwarding;
        # the independent earlier accept chain cannot bypass a later deny.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as allowed, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as denied:
            allowed.bind(('192.168.1.2', 58000))
            denied.bind(('192.168.1.2', 58001))
            allowed.settimeout(1)
            denied.settimeout(0.1)
            inject('capture0', '192.168.1.33', '192.168.1.2', 47000, 58000)
            self_payload, _ = allowed.recvfrom(2048)
            assert self_payload == b'isolated-home-exit-fixture'
            before = host_counter('input', 'host-input-deny')
            inject('capture0', '192.168.1.33', '192.168.1.2', 47000, 58001)
            try: denied.recvfrom(2048)
            except socket.timeout: pass
            else: raise AssertionError('Unexpected delivery to closed host port')
            assert host_counter('input', 'host-input-deny') > before
        before = host_counter('forward', 'host-forward-deny')
        inject('capture0', '192.168.1.33', '9.9.9.9', 47001, 58000)
        time.sleep(0.03)
        assert host_counter('forward', 'host-forward-deny') > before
        network_config.apply({**network_config.DEFAULT, 'uplink': 'end0'}, {'wireguard': True})
        assert run('nft', 'list', 'table', 'inet', 'enode_host', check=False).returncode != 0
        run('nft', 'list', 'table', 'inet', 'ne_home_guard')
        run('nft', 'list', 'table', 'inet', 'untouched')


class DeviceFirewallTests(unittest.TestCase):
    def test_exit_nat_return_traffic_and_access_guards(self):
        missing = [name for name in ['unshare', 'ip', 'nft', 'bash', 'sysctl'] if not shutil.which(name)]
        # Native root can isolate the network directly. Mapping root into a new
        # user namespace drops its ability to traverse runner-owned 0700 homes.
        namespace = ['unshare', '--net'] if os.geteuid() == 0 else ['unshare', '--user', '--map-root-user', '--net']
        probe = run(*namespace, 'true', check=False) if not missing else None
        if missing or probe.returncode:
            reason = f'Missing tools: {missing}' if missing else probe.stderr.strip()
            if os.environ.get('NETWORK_EXIT_NETWORK_TESTS') == 'required':
                self.fail(reason)
            self.skipTest(reason)
        result = run(*namespace, sys.executable,
                     str(Path(__file__).resolve()), '--isolated', os.readlink('/proc/self/ns/net'), check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--isolated':
        isolated_checks(sys.argv[2])
    else:
        unittest.main()
