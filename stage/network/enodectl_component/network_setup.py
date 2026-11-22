"""Apply network protection before starting selected services."""
import network_config

def apply_selection(selected): network_config.sync(selected=selected)
