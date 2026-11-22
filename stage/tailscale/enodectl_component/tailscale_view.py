"""Textual form presentation; backend operations are kept in tailscale_config."""
from enodectl_core.textual_ui import Field, FormScreen
import tailscale_config as backend

def screen():
    return FormScreen('Tailscale / Headscale', [
        Field('provider', 'Control plane', 'choice', (('Official Tailscale', 'official'), ('Headscale', 'headscale'))),
        Field('headscale_url', 'Headscale HTTPS origin (when selected)'),
        Field('advertise_exit', 'Advertise as an exit node', 'bool'),
        Field('reauth_confirm', 'I confirm a control-plane switch may disconnect this session', 'bool'),
    ], backend.load, backend.save, backend.details, help_text='Tailscale is mandatory. Switching control plane explicitly reauthenticates. Check status to obtain/verify the authorization URL.')
