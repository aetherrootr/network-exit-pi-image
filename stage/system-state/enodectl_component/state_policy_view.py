"""Read-only Textual presentation for this required system component."""
from enodectl_core.textual_ui import ReadOnlyScreen
from state_policy import details

def screen(): return ReadOnlyScreen('Persistent STATE', details)
