from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote


def obsidian_uri(vault: Path, note: Path | None = None) -> str:
    relative = note.relative_to(vault).as_posix() if note else "Home.md"
    return f"obsidian://open?vault={quote(vault.name)}&file={quote(relative)}"


def notify(title: str, message: str, *, vault: Path, note: Path | None = None) -> None:
    if os.name != "nt":
        return
    launch = obsidian_uri(vault, note)
    title_json = json.dumps(title)
    message_json = json.dumps(message)
    launch_json = json.dumps(launch)
    script = f"""
$title = {title_json}
$message = {message_json}
$launch = {launch_json}
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast activationType="protocol" launch="' + [Security.SecurityElement]::Escape($launch) + '"><visual><binding template="ToastGeneric"><text>' + [Security.SecurityElement]::Escape($title) + '</text><text>' + [Security.SecurityElement]::Escape($message) + '</text></binding></visual></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Personal Second Brain').Show($toast)
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
