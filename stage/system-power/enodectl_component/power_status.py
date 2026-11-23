"""System policy/status backend without UI dependencies."""
def details():
    return 'Poweroff is not supported. Restart requests use `sudo enodectl reboot`. Direct root SSH is disabled; authorized pi administrators use `sudo`. This component cannot be disabled.'
