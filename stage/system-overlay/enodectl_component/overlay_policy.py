"""System policy/status backend without UI dependencies."""
from pathlib import Path

def details():
    mounts=Path('/proc/mounts').read_text().splitlines()
    root=next((line for line in mounts if line.split()[1]=='/'),'(unavailable)')
    return 'Root mount: '+root+'\nRAM upper policy: one third of RAM, capped at 256 MiB with an inode limit. Temporary writes clear on reboot; STATE remains persistent.'
