"""Operator-selected official Tailscale or Headscale control plane."""
import ipaddress
import json
import re
import subprocess
from urllib.parse import urlsplit

OFFICIAL = 'https://controlplane.tailscale.com'
OFFICIAL_ALIASES = {OFFICIAL, 'https://login.tailscale.com'}


def control_url(value):
    """Require an HTTPS origin; do not accept embedded credentials or CLI text."""
    message = 'Use an HTTPS control-server URL without credentials, path, query or fragment'
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError(message)
    parsed = urlsplit(value)
    host, port = parsed.hostname, parsed.port
    if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.netloc.endswith(':') or parsed.query or parsed.fragment
            or '?' in value or '#' in value):
        raise ValueError(message)
    try:
        address = ipaddress.ip_address(host)
        host = f'[{address.compressed}]' if address.version == 6 else str(address)
    except ValueError:
        if (len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                      for label in host.split('.'))):
            raise ValueError(message)
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(message)
    origin = 'https://' + host + (f':{port}' if port is not None and port != 443 else '')
    return OFFICIAL if origin in OFFICIAL_ALIASES else origin


def preferences():
    # Read only the local API. Never print or persist the full private prefs.
    prefs = json.loads(subprocess.check_output(['tailscale', 'debug', 'prefs'], text=True, timeout=10))
    if not isinstance(prefs, dict) or not isinstance(prefs.get('ControlURL'), str):
        raise ValueError('Cannot determine the current Tailscale control server')
    return control_url(prefs['ControlURL'] or OFFICIAL)


def backend_status():
    status = json.loads(subprocess.check_output(['tailscale', 'status', '--json'], text=True, timeout=10))
    if (not isinstance(status, dict) or not isinstance(status.get('BackendState'), str)
            or not status['BackendState'] or ('HaveNodeKey' in status and not isinstance(status['HaveNodeKey'], bool))):
        raise ValueError('Cannot determine the current Tailscale login state')
    return status
