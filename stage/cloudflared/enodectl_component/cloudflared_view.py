"""Textual form presentation; backend operations are kept in cloudflared_config."""
from enodectl_core.textual_ui import Field, FormScreen
import cloudflared_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('Cloudflare Tunnel', [
        Field('token', 'Tunnel token (blank keeps the existing token)', 'secret'),
    ], backend.load, backend.save, lambda: diagnostics.status('cloudflared'), logs=lambda: diagnostics.logs('cloudflared'),
        help_text='Saving a token does not enable the tunnel. Enable it in the dashboard and Apply changes; inspect status and logs.')
