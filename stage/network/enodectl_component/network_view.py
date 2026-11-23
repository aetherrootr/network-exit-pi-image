"""Textual form presentation; backend operations are kept in network_management."""
from enodectl_core.textual_ui import Field, FormScreen
import network_management as backend

def screen():
    return FormScreen('Network and firewall', [
        Field('firewall', 'Host firewall enabled', 'bool'),
        Field('uplink', 'Broadband interface: `auto` or explicit device name (without backticks)'),
        Field('ssh_sources', 'SSH source networks, comma-separated'),
        Field('tcp_ports', 'Additional inbound TCP ports, comma-separated'),
        Field('udp_ports', 'Additional inbound UDP ports, comma-separated'),
    ], backend.load, backend.save, backend.details, help_text='Network settings require a new-connection check and 90-second confirmation. DHCP/DNS/default routes remain OS-managed.')
