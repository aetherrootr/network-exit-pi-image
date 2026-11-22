"""Optional DDNS service hooks."""
import subprocess
from components import choices
import ddns
import network_config

def validate():
    ddns.load()
    network_config.uplink(network_config.load())
def after_disable(selected): subprocess.run(['systemctl','stop','network-exit-ddns.service'],check=True,capture_output=True)
