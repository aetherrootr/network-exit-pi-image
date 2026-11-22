"""System policy/status backend without UI dependencies."""
import os
from components import STATE

def details():
    space=os.statvfs(STATE)
    return 'STATE mounted: '+str(os.path.ismount(STATE))+'\nCapacity MiB: '+str(space.f_blocks*space.f_frsize//1048576)+'\nAvailable MiB: '+str(space.f_bavail*space.f_frsize//1048576)+'\nIdentities/configuration persist across reboot and compatible OTA. Expansion uses detected card capacity.'
