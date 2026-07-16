from __future__ import annotations

import html
import subprocess
from pathlib import Path


def task_xml(*, task_name: str, script_path: Path, username: str) -> str:
    arguments = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script_path}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Evidence-backed personal second-brain daily and weekly pipeline.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T22:30:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{html.escape(username)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled><Hidden>false</Hidden><ExecutionTimeLimit>PT4H</ExecutionTimeLimit></Settings>
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
