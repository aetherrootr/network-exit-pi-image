"""Read-only Textual presentation for this required system component."""
from enodectl_core.textual_ui import ReadOnlyScreen
from ota_status import details

def screen(): return ReadOnlyScreen('A/B update services', details)
