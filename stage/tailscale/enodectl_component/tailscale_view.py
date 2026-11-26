"""Textual form presentation; backend operations are kept in tailscale_config."""
from enodectl_core.textual_ui import Field, FormScreen
import tailscale_config as backend

def screen():
    return FormScreen('Tailscale / Headscale', [
        Field('provider', 'Control plane', 'choice', (('Official Tailscale', 'official'), ('Headscale', 'headscale'))),
        Field('headscale_url', 'Headscale HTTPS origin (when selected)'),
        Field('advertise_exit', 'Advertise as an exit node', 'bool'),
        Field('reauth_confirm', 'I confirm a control-plane switch may disconnect this session', 'bool'),
    ], backend.load, backend.save, backend.details, result_dialog=True,
        help_text='Tailscale is mandatory. Apply configuration to request login. Check status for the URL or connection errors. Switching control plane requires confirmation.')
