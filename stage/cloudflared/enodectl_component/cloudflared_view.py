"""Textual form presentation; backend operations are kept in cloudflared_config."""
from enodectl_core.textual_ui import Field, FormScreen
import cloudflared_config as backend
import service_diagnostics as diagnostics

def screen():
    return FormScreen('Cloudflare Tunnel', [
        Field('token', 'Tunnel token (blank keeps the existing token)', 'secret'),
    ], backend.load, backend.save, lambda: diagnostics.status('cloudflared'), logs=lambda: diagnostics.logs('cloudflared'), service_name='cloudflared',
        help_text='Set Startup ON and apply the token together to start the tunnel. Inspect status and logs to verify registration.')
