"""One-operation OTA form backend; verification choices are never persisted."""
from ota_recovery import CONFIRMATION
import update


def load():
    return {'bundle': '', 'skip_signature_verification': False, 'confirmation': '', 'retry_failed': False}


def save(values):
    import json
    source = json.loads(update.SOURCE.read_text())
    update.install(values['bundle'].strip() or None, source,
                   retry_failed=bool(values['retry_failed']),
                   skip_signature_verification=bool(values['skip_signature_verification']),
                   confirmation=values['confirmation'], report=lambda message: None)
    return 'OTA installed to the inactive slot. Inspect bootchain status, then use sudo enodectl reboot. Three trial boots are available.'
