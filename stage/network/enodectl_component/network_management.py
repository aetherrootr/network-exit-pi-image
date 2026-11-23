"""Network form conversion and trial lifecycle; no Textual dependency."""
import network_config as network
import connectivity
import time
from service_config import TrialChange


def load():
    config = network.load()
    return {**config, **connectivity.form_values(), 'ssh_sources': ','.join(config['ssh_sources']),
            'tcp_ports': ','.join(map(str, config['tcp_ports'])), 'udp_ports': ','.join(map(str, config['udp_ports']))}


def save(values):
    config = {**{name: values[name] for name in network.DEFAULT},
              'ssh_sources': [v.strip() for v in values['ssh_sources'].split(',') if v.strip()],
              'tcp_ports': [int(v.strip()) for v in values['tcp_ports'].split(',') if v.strip()],
              'udp_ports': [int(v.strip()) for v in values['udp_ports'].split(',') if v.strip()]}
    start = time.monotonic()
    link_settings = connectivity.prepare(values) if 'wifi_enabled' in values else None
    wifi_changed = link_settings is not None and link_settings != connectivity.load()
    token = network.begin(network.validate(config), link_settings=link_settings)
    remaining = max(0.01, network.TRIAL_SECONDS - (time.monotonic() - start))
    message = ('Keep Ethernet connected. Verify a NEW SSH connection over the Wi-Fi address or Tailscale before keeping these Wi-Fi settings.'
               if wifi_changed else 'Verify a NEW SSH connection before keeping these settings.')
    return TrialChange(message, remaining,
                       lambda: network.confirm(token), network.rollback)


def details(): return '\n'.join(network.diagnostics())
