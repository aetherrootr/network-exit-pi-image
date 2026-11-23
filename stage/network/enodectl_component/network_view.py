"""Textual form presentation; backend operations are kept in network_management."""
from enodectl_core.textual_ui import Field, FormScreen
import network_management as backend

def screen():
    return FormScreen('Network, Wi-Fi and firewall', [
        Field('firewall', 'Host firewall enabled', 'bool'),
        Field('uplink', 'Broadband interface: `auto` or explicit device name (without backticks)'),
        Field('ssh_sources', 'SSH source networks, comma-separated'),
        Field('tcp_ports', 'Additional inbound TCP ports, comma-separated'),
        Field('udp_ports', 'Additional inbound UDP ports, comma-separated'),
        Field('wifi_enabled', 'Wi-Fi auto-connect enabled', 'bool'),
        Field('wifi_interface', 'Wi-Fi interface: `auto` for a single radio (without backticks)'),
        Field('wifi_ssid', 'Wi-Fi SSID'),
        Field('wifi_security', 'Wi-Fi authentication', 'choice', (('WPA2 Personal', 'psk'), ('WPA3 Personal', 'sae'))),
        Field('wifi_password', 'Wi-Fi password (blank keeps it for the same SSID/authentication)', 'secret'),
        Field('wifi_hidden', 'Hidden Wi-Fi SSID', 'bool'),
        Field('wifi_country', 'Wi-Fi country code, e.g. `JP`, or blank (without backticks)'),
    ], backend.load, backend.save, backend.details,
        help_text='Initial setup: Ethernet `192.168.77.1/24`; PC `192.168.77.2/24`, no gateway/DNS. Completing setup removes this temporary address; normal Ethernet uses DHCP. Wi-Fi credentials persist on STATE. Changes require a NEW SSH connection and confirmation within 90 seconds.')
