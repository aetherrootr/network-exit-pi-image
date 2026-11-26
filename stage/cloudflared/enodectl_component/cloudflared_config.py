"""Cloudflare token storage/application independent of the configuration form."""
import components
from service_config import publish


def load(): return {'token': ''}


def save(values):
    path = components.CONFIG / 'cloudflare-token'
    token = values['token'].strip() or (path.read_text().strip() if path.exists() else '')
    if not token or any(not 33 <= ord(char) < 127 for char in token):
        raise ValueError('Supply a nonempty Tunnel token without whitespace or control characters')
    publish('cloudflared', {path: token + '\n'})
    if components.choices().get('cloudflared'):
        return 'Cloudflare Tunnel token saved; service restarted. Check status and View logs to verify registration.'
    return 'Token saved, but the tunnel is disabled. Return to the dashboard, Stage toggle Cloudflare Tunnel ON and Apply changes.'
