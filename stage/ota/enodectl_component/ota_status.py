"""System policy/status backend without UI dependencies."""
import json
import bootchain

def details(): return json.dumps(bootchain.status(),indent=2)
