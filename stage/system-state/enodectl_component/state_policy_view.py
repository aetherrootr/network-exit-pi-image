"""Read-only Textual presentation for this required system component."""
from enodectl_core.textual_ui import ReadOnlyScreen
from state_policy import details
import service_diagnostics as diagnostics

def screen(): return ReadOnlyScreen('Persistent STATE', lambda: details() + '\n\n' + diagnostics.logs('system-state'))
