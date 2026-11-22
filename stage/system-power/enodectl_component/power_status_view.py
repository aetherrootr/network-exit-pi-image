"""Read-only Textual presentation for this required system component."""
from enodectl_core.textual_ui import ReadOnlyScreen
from power_status import details

def screen(): return ReadOnlyScreen('System power policy', details)
