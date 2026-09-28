"""Windows setup: a weekly scheduled task and a desktop shortcut to the dashboard.

The task runs Mondays at 09:00. If the PC was off, Windows starts it at the next
opportunity, and `sbrain run` catches up every finished week that is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from brain.config import Config

TASK_NAME = "Second Brain weekly update"
SHORTCUT = "Second Brain.lnk"


def _script(name: str) -> str:
    found = shutil.which(name) or shutil.which(name + ".exe")
    if not found:
        candidate = Path(sys.executable).parent / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
        raise SystemExit(f"{name} is not installed. Install the engine first: uv tool install --editable <engine repo>")
    return found


def _powershell(script: str) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install(cfg: Config, day: str = "Monday", at: str = "9:00am") -> list[str]:
    if sys.platform != "win32":
        raise SystemExit("sbrain install sets up Windows Task Scheduler; on other systems use cron with `sbrain run`.")
    silent = _script("sbrain-silent")
    dashboard = _script("sbrain-dashboard")
    vault_arg = f'--vault "{cfg.vault}"'
    _powershell(f"""
$action = New-ScheduledTaskAction -Execute {_ps_quote(silent)} -Argument {_ps_quote(vault_arg + ' run')}
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day} -At {_ps_quote(at)}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action -Trigger $trigger -Settings $settings `
  -Description 'Updates the second brain vault from the past week of AI conversations and project folders.' -Force | Out-Null
""")
    desktop = _powershell("[Environment]::GetFolderPath('Desktop')")
    link = str(Path(desktop) / SHORTCUT)
    _powershell(f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)})
$s.TargetPath = {_ps_quote(dashboard)}
$s.Arguments = {_ps_quote(vault_arg)}
$s.WorkingDirectory = {_ps_quote(str(cfg.vault))}
$s.Description = 'Open the second brain logbook'
$s.Save()
""")
    return [f"Scheduled task '{TASK_NAME}': every {day} at {at}, catching up after missed runs",
            f"Desktop shortcut: {link}"]


def uninstall() -> list[str]:
    done = []
    try:
        _powershell(f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false")
        done.append(f"Removed scheduled task '{TASK_NAME}'")
    except SystemExit:
        done.append("No scheduled task to remove")
    desktop = _powershell("[Environment]::GetFolderPath('Desktop')")
    link = Path(desktop) / SHORTCUT
    if link.exists():
        link.unlink()
        done.append(f"Removed {link}")
    return done


def schedule_info() -> dict | None:
    """Next and last run of the scheduled task, for the dashboard."""
    if sys.platform != "win32":
        return None
    try:
        out = _powershell(
            f"$i = Get-ScheduledTaskInfo -TaskName {_ps_quote(TASK_NAME)} -ErrorAction Stop; "
            "'{0:o}|{1:o}|{2}' -f $i.NextRunTime, $i.LastRunTime, $i.LastTaskResult"
        )
    except SystemExit:
        return None
    nxt, last, code = (out.split("|") + ["", "", ""])[:3]
    return {"next": nxt or None, "last": last or None, "last_result": code}
