"""Textual form presentation; backend operations are kept in ddns_config."""
from enodectl_core.textual_ui import Field, FormScreen
import ddns_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('DDNS', [
        Field('provider', 'Provider', 'choice', (('Cloudflare', 'cloudflare'), ('DuckDNS', 'duckdns'), ('DynDNS2', 'dyndns2'))),
        Field('hostname', 'Complete DNS hostname'),
        Field('zone', 'Cloudflare zone'),
        Field('server', 'DynDNS2 HTTPS server hostname'),
        Field('login', 'DynDNS2 login'),
        Field('secret', 'Token/password (blank keeps existing secret for the same provider)', 'secret'),
        Field('family', 'Record family', 'choice', (('IPv4 / A', 'ipv4'), ('IPv6 / AAAA', 'ipv6'))),
        Field('interval', 'Update interval in seconds (300-86400)'),
    ], backend.load, backend.save, lambda: diagnostics.status('ddns') + '\n\n' + backend.details(), logs=lambda: diagnostics.logs('ddns'), service_name='ddns', help_text='Set Startup ON to start the timer when applying the profile. No automatic port opening.')
