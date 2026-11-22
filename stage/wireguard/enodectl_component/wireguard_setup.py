"""WireGuard service lifecycle and guard operations."""
import subprocess
import components
import network_config
from wireguard_tool import guard_script

def validate():
    subprocess.run(['wg-quick','strip','/etc/wireguard/wg-home.conf'],check=True,capture_output=True)
    subprocess.run(['nft','-c','-f',str(components.CONFIG/'rules.nft')],check=True,capture_output=True)

def before_enable(selected): network_config.sync(selected=selected)
def after_disable(selected): network_config.sync(selected=selected)
def guard(): subprocess.run(['bash','-c',guard_script()],check=True)
