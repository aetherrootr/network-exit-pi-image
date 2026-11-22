"""Authoritative persistent setup state and an unprivileged login hint."""
from pathlib import Path
from components import save

RUNTIME_READY = Path('/run/network-exit/setup-complete')


def publish(config):
    """Mirror the private completion marker without exposing any credentials."""
    if (Path(config) / 'initialized').is_file():
        RUNTIME_READY.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        # Initialization uses umask 077; this directory holds public runtime
        # metadata and must remain traversable by the ordinary login account.
        RUNTIME_READY.parent.chmod(0o755)
        save(RUNTIME_READY, '1\n', mode=0o644)
    else:
        RUNTIME_READY.unlink(missing_ok=True)


def invalidate(config):
    # Remove the public hint first. A login always rechecks private state as root.
    RUNTIME_READY.unlink(missing_ok=True)
    (Path(config) / 'initialized').unlink(missing_ok=True)


def complete(config):
    save(Path(config) / 'initialized', '1\n')
    publish(config)
