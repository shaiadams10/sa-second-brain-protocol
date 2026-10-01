"""Windows setup: a weekly scheduled task and a desktop shortcut to the dashboard.

The task runs on the day and time in the vault's `[schedule]` (Monday 09:00 unless set),
in the PC's local time. If the PC was off, Windows starts it at the next opportunity,
and `sbrain run` catches up every finished week that is missing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from brain.config import DAYS, Config

TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
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


def register_task(cfg: Config) -> str:
    """Create or replace the weekly task from the vault's [schedule]. Returns a one-line summary."""
    if sys.platform != "win32":
        raise SystemExit("The weekly run uses Windows Task Scheduler; on other systems use cron with `sbrain run`.")
    if cfg.schedule_day not in DAYS or not TIME.match(cfg.schedule_time):
        raise SystemExit(f"Bad schedule in config.toml: {cfg.schedule_day} at {cfg.schedule_time}")
    silent = _script("sbrain-silent")
    vault_arg = f'--vault "{cfg.vault}"'
    _powershell(f"""
$action = New-ScheduledTaskAction -Execute {_ps_quote(silent)} -Argument {_ps_quote(vault_arg + ' run')}
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {cfg.schedule_day} -At {_ps_quote(cfg.schedule_time)}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action -Trigger $trigger -Settings $settings `
  -Description 'Updates the second brain vault from the past week of AI conversations and project folders.' -Force | Out-Null
""")
    return f"Scheduled task '{TASK_NAME}': every {cfg.schedule_day} at {cfg.schedule_time}, catching up after missed runs"


def install(cfg: Config) -> list[str]:
    summary = register_task(cfg)
    dashboard = _script("sbrain-dashboard")
    vault_arg = f'--vault "{cfg.vault}"'
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
    return [summary, f"Desktop shortcut: {link}"]


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
