"""Textual form presentation; backend operations are kept in network_management."""
from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static
from enodectl_core.textual_ui import Details, Field, FormScreen, display, error_message
import network_management as backend


class WiFiScanScreen(ModalScreen):
    BINDINGS = [('escape', 'back', 'Back')]
    DEFAULT_CSS = '''
    WiFiScanScreen { align: center middle; }
    WiFiScanScreen > Vertical { width: 90%; height: 80%; padding: 1; border: ascii $accent; background: $surface; }
    WiFiScanScreen DataTable { height: 1fr; }
    WiFiScanScreen Static { height: auto; }
    WiFiScanScreen Horizontal { height: 3; }
    '''

    def __init__(self, interface):
        super().__init__()
        self.interface = interface
        self.networks = []
        self.busy = False

    def compose(self):
        with Vertical():
            yield Static('Nearby Wi-Fi networks', markup=False)
            yield Static('Scanning...', id='scan-status', markup=False)
            yield DataTable(id='wifi-networks', cursor_type='row')
            with Horizontal():
                yield Button('Scan again', id='scan-again')
                yield Button('Back / manual SSID', id='scan-back')
            yield Static('Up/Down: network | Enter: choose | Right: actions | Esc: back')

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns('SSID', 'Signal (dBm)', 'Security')
        table.focus()
        self.start_scan()

    def start_scan(self):
        if self.busy: return
        self.busy = True
        for button in self.query(Button): button.disabled = True
        self.query_one('#scan-status', Static).update('Scanning Wi-Fi radio; this may take a few seconds...')
        self.query_one(DataTable).clear()
        self.scan_networks()

    @work(thread=True, exclusive=True)
    def scan_networks(self):
        try: self.app.call_from_thread(self.show_networks, backend.scan(self.interface))
        except Exception as exc: self.app.call_from_thread(self.scan_failed, exc)

    def ready(self):
        self.busy = False
        for button in self.query(Button): button.disabled = False

    def show_networks(self, networks):
        self.networks = networks
        table = self.query_one(DataTable)
        for index, network in enumerate(networks):
            security = ' / '.join('WPA2 Personal' if mode == 'psk' else 'WPA3 Personal' for mode in network['modes'])
            table.add_row(Text(network['ssid']), f"{network['signal']:.0f}" if network['signal'] > -999 else 'Unknown',
                          security or 'Unsupported', key=str(index))
        self.ready()
        self.query_one('#scan-status', Static).update(f'{len(networks)} visible networks. Choose a WPA2/WPA3 Personal network.' if networks else 'No visible networks found. Try again or enter the SSID manually.')
        (table if networks else self.query_one('#scan-again', Button)).focus()

    def scan_failed(self, exc):
        self.ready()
        message = error_message(exc)
        self.query_one('#scan-status', Static).update(Text(display(message)))
        self.query_one('#scan-again', Button).focus()
        self.app.push_screen(Details(message, title='Wi-Fi scan error'))

    def on_data_table_row_highlighted(self, event): event.stop()

    def on_data_table_row_selected(self, event):
        event.stop()
        if self.busy: return
        selected = self.networks[int(event.row_key.value)]
        if not selected['modes']:
            self.app.push_screen(Details('This network does not advertise WPA2/WPA3 Personal. Select a supported network or enter its SSID manually.', title='Unsupported Wi-Fi security'))
        else: self.dismiss(selected)

    def on_button_pressed(self, event):
        if event.button.id == 'scan-again': self.start_scan()
        else: self.action_back()

    def action_back(self):
        if not self.busy: self.dismiss(None)


class NetworkScreen(FormScreen):
    def before_field(self, field):
        if field.name == 'wifi_ssid': yield Button('Scan Wi-Fi networks', id='scan-wifi')

    def extra_action(self, button):
        if button == 'scan-wifi':
            interface = self.query_one('#field-wifi_interface', Input).value
            self.app.push_screen(WiFiScanScreen(interface), self.select_network)

    def select_network(self, network):
        if network is None: return
        ssid = self.query_one('#field-wifi_ssid', Input)
        security = self.query_one('#field-wifi_security', Select)
        password = self.query_one('#field-wifi_password', Input)
        mode = security.value if security.value in network['modes'] else network['modes'][0]
        if ssid.value != network['ssid'] or security.value != mode: password.value = ''
        ssid.value = network['ssid']
        security.value = mode
        self.query_one('#field-wifi_enabled', Select).value = True
        self.query_one('#field-wifi_hidden', Select).value = False
        password.focus()
        self.query_one('#form-result', Static).update('Wi-Fi network selected. Enter its password, then apply and confirm the network trial.')


def screen():
    return NetworkScreen('Network, Wi-Fi and firewall', [
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
