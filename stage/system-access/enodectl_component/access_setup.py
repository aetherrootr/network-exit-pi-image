"""Mandatory SSH readiness check; presentation lives in ssh_view."""
import components
import ssh_access


def before_complete(): ssh_access.require_access(components.CONFIG)
