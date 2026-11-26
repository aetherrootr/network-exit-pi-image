"""Textual form presentation; backend operations are kept in wireguard_config."""
from enodectl_core.textual_ui import Field, FormScreen
import wireguard_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('WireGuard', [
        Field('peer_key', 'Remote peer public key'),
        Field('peer_host', 'Remote peer IPv4 address or DNS hostname'),
        Field('peer_port', 'Remote peer UDP port'),
    ], backend.load, backend.save, lambda: diagnostics.status('wireguard') + '\n\n' + backend.details(), logs=lambda: diagnostics.logs('wireguard'), service_name='wireguard', help_text='Device private keys never appear here. The broadband interface is configured in Network/firewall.')
