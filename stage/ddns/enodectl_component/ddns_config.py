"""Editable DDNS profiles; secrets stay in backend memory and root-only STATE."""
import json
import subprocess
import components
import ddns
from service_config import publish


def load():
    try: profile = ddns.load()
    except FileNotFoundError:
        profile = dict(provider='cloudflare', hostname='', zone='', server='', login='', family='ipv4', interval=300)
    return {**profile, 'secret': '', 'interval': str(profile['interval'])}


def save(values):
    try: previous = ddns.load()
    except FileNotFoundError: previous = {}
    profile = dict(values)
    profile['interval'] = int(profile['interval'])
    profile['secret'] = profile['secret'] or (previous.get('secret', '') if previous.get('provider') == profile['provider'] else '')
    ddns.validate(profile)
    enabled = components.choices().get('ddns', False)
    if enabled:
        subprocess.run(['systemctl', 'stop', 'network-exit-ddns.timer', 'network-exit-ddns.service'], check=True, capture_output=True)
    def apply():
        for file in ('ddclient.cache', 'status.json'): (ddns.STATE / file).unlink(missing_ok=True)
        if enabled: subprocess.run(['systemctl', 'restart', 'network-exit-ddns.timer'], check=True, capture_output=True)
    publish('ddns', {components.CONFIG / 'ddns.json': json.dumps(profile) + '\n'}, apply=apply)
    return 'DDNS profile saved. No record update was requested by this form; the enabled timer handles updates.'


def details():
    return json.dumps(ddns.status(), indent=2) if ddns.status() else 'No update attempts recorded.'
