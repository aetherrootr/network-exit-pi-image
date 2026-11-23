"""Cloudflare Tunnel service validation."""
import components

def validate():
    if not (components.CONFIG/'cloudflare-token').read_text().strip():
        raise ValueError('Cloudflare Tunnel token is empty; run `sudo enodectl config`')
