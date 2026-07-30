from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def task_xml(*, task_name: str, script_path: Path, username: str) -> str:
    arguments = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script_path}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Evidence-backed personal second-brain daily and weekly pipeline.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T22:30:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{html.escape(username)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>false</StartWhenAvailable><Enabled>true</Enabled><Hidden>false</Hidden><ExecutionTimeLimit>PT4H</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>powershell.exe</Command><Arguments>{html.escape(arguments)}</Arguments><WorkingDirectory>{html.escape(str(script_path.parent))}</WorkingDirectory></Exec></Actions>
</Task>"""


def install_task(*, task_name: str, script_path: Path) -> None:
    username = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    xml = task_xml(task_name=task_name, script_path=script_path.resolve(), username=username)
    command = (
        "$xml = [Console]::In.ReadToEnd(); "
        f"Register-ScheduledTask -TaskName {task_name!r} -Xml $xml -Force | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        input=xml,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def task_status(task_name: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Get-ScheduledTask -TaskName {task_name!r} | Select-Object TaskName,State | ConvertTo-Json"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "not installed"


def task_details(task_name: str) -> dict[str, Any]:
    """Return display-safe scheduler metadata without machine identities or commands."""

    if os.name != "nt":
        return {"installed": False, "state": "unsupported", "last_run": None, "next_run": None}
    escaped = task_name.replace("'", "''")
    script = f"""
$task = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName '{escaped}' -ErrorAction Stop
[pscustomobject]@{{
  installed = $true
  state = [string]$task.State
  last_run = if ($info.LastRunTime.Year -lt 2000) {{ $null }} else {{ $info.LastRunTime.ToString('o') }}
  next_run = if ($info.NextRunTime.Year -lt 2000) {{ $null }} else {{ $info.NextRunTime.ToString('o') }}
  last_result = [int64]$info.LastTaskResult
  missed_runs = [int]$info.NumberOfMissedRuns
}} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return {"installed": False, "state": "not installed", "last_run": None, "next_run": None}
    try:
        return dict(json.loads(result.stdout))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"installed": False, "state": "unavailable", "last_run": None, "next_run": None}


def run_canary(script_path: Path) -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path.resolve()),
            "-Canary",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Scheduled-task canary failed")
    return result.stdout[-4000:]
