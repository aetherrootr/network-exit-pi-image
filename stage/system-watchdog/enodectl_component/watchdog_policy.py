"""System policy/status backend without UI dependencies."""
import subprocess

def details():
    result=subprocess.run(['/usr/lib/network-exit/node-watchdog'],capture_output=True,text=True,timeout=15,check=False)
    return result.stdout+result.stderr
