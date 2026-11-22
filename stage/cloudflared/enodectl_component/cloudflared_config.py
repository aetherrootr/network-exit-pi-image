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
    return 'Cloudflare Tunnel configuration saved. Existing enabled service was restarted.'
