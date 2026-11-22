"""Network form conversion and trial lifecycle; no Textual dependency."""
import network_config as network
import time
from service_config import TrialChange


def load():
    config = network.load()
    return {**config, 'ssh_sources': ','.join(config['ssh_sources']),
            'tcp_ports': ','.join(map(str, config['tcp_ports'])), 'udp_ports': ','.join(map(str, config['udp_ports']))}


def save(values):
    config = {**values, 'ssh_sources': [v.strip() for v in values['ssh_sources'].split(',') if v.strip()],
              'tcp_ports': [int(v.strip()) for v in values['tcp_ports'].split(',') if v.strip()],
              'udp_ports': [int(v.strip()) for v in values['udp_ports'].split(',') if v.strip()]}
    start = time.monotonic()
    token = network.begin(network.validate(config))
    remaining = max(0.01, network.TRIAL_SECONDS - (time.monotonic() - start))
    return TrialChange('Verify a NEW SSH connection before keeping these settings.', remaining,
                       lambda: network.confirm(token), network.rollback)


def details(): return '\n'.join(network.diagnostics())
