"""Textual form presentation; backend operations are kept in cloudflared_config."""
from enodectl_core.textual_ui import Field, FormScreen
import cloudflared_config as backend

def screen():
    return FormScreen('Cloudflare Tunnel', [
        Field('token', 'Tunnel token (blank keeps the existing token)', 'secret'),
    ], backend.load, backend.save, None, help_text='No token is prefilled or logged. Saving restarts the service only when enabled.')
