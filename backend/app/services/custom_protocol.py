"""Custom protocol asset generators — Windows `.reg` files + installer metadata.

A user downloads `<app_id>.reg` from the portal, double-clicks it, and Windows
registers a custom URI scheme that points to a locally installed launcher EXE.
The portal can then trigger that scheme via `heaxhub://launch?app=<id>&job=<id>`.
"""
from __future__ import annotations

from typing import Any


def generate_reg_file(app_id: str, protocol: str, exe_path: str) -> str:
    """Return Windows .reg file content registering `protocol://` -> exe_path.

    `exe_path` should already be a Windows-style absolute path
    (e.g. ``C:\\Program Files\\HEAXLauncher\\launcher.exe``). Inside the .reg file
    we escape backslashes by doubling them, as the Registry editor expects.
    """
    if not app_id or not protocol or not exe_path:
        raise ValueError("app_id, protocol, exe_path are all required")

    # Inside .reg files, backslashes in string values must be doubled.
    safe_exe = exe_path.replace("\\", "\\\\").replace('"', '\\"')
    safe_protocol = protocol.strip().lower()

    return (
        "Windows Registry Editor Version 5.00\r\n"
        "\r\n"
        f'; HEAXHub custom protocol — app_id={app_id}\r\n'
        f"[HKEY_CLASSES_ROOT\\{safe_protocol}]\r\n"
        f'@="URL:{safe_protocol} Protocol"\r\n'
        '"URL Protocol"=""\r\n'
        "\r\n"
        f"[HKEY_CLASSES_ROOT\\{safe_protocol}\\DefaultIcon]\r\n"
        f'@="\\"{safe_exe}\\",0"\r\n'
        "\r\n"
        f"[HKEY_CLASSES_ROOT\\{safe_protocol}\\shell]\r\n"
        "\r\n"
        f"[HKEY_CLASSES_ROOT\\{safe_protocol}\\shell\\open]\r\n"
        "\r\n"
        f"[HKEY_CLASSES_ROOT\\{safe_protocol}\\shell\\open\\command]\r\n"
        f'@="\\"{safe_exe}\\" \\"%1\\""\r\n'
    )


def generate_installer_metadata(app_id: str, version: str) -> dict[str, Any]:
    """JSON payload an Inno-Setup style installer wrapper consumes at install time."""
    if not app_id or not version:
        raise ValueError("app_id and version are required")
    return {
        "app_id": app_id,
        "version": version,
        "protocol": f"heaxhub-{app_id}",
        "install_steps": [
            {
                "step": "register_protocol",
                "scheme": f"heaxhub-{app_id}",
                "target_exe": "{app}\\launcher.exe",
            },
            {
                "step": "create_shortcut",
                "name": f"HEAXHub {app_id}",
                "target": "{app}\\launcher.exe",
            },
        ],
        "uninstall_steps": [
            {"step": "unregister_protocol", "scheme": f"heaxhub-{app_id}"},
        ],
    }
