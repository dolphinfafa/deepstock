"""Register the authorized Windows observation with native Task Scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from deepstock.web.config import settings

TASK = 'Deepstock_Granville_Shadow'
NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


def task_xml(root: Path, python: Path, user: str, now: datetime):
    if now.utcoffset() != timedelta(hours=8):
        raise ValueError('Verified China timezone required')
    start = now.replace(hour=8, minute=10, second=0, microsecond=0)
    if start <= now:
        start += timedelta(days=1)
    ET.register_namespace('', NS)
    task = ET.Element('{' + NS + '}Task', {'version': '1.2'})

    def add(parent, tag, text=None, **attributes):
        element = ET.SubElement(parent, '{' + NS + '}' + tag, attributes)
        element.text = text
        return element

    trigger = add(add(task, 'Triggers'), 'CalendarTrigger')
    add(trigger, 'StartBoundary', start.isoformat())
    add(trigger, 'Enabled', 'true')
    add(add(trigger, 'ScheduleByDay'), 'DaysInterval', '1')
    principal = add(add(task, 'Principals'), 'Principal', id='Author')
    add(principal, 'UserId', user)
    add(principal, 'LogonType', 'InteractiveToken')
    add(principal, 'RunLevel', 'LeastPrivilege')
    options = add(task, 'Settings')
    for key, value in {
        'MultipleInstancesPolicy': 'IgnoreNew', 'DisallowStartIfOnBatteries': 'false',
        'StopIfGoingOnBatteries': 'false', 'StartWhenAvailable': 'true',
        'AllowStartOnDemand': 'true', 'Enabled': 'true', 'WakeToRun': 'true',
        'ExecutionTimeLimit': 'PT2H',
    }.items():
        add(options, key, value)
    action = add(add(task, 'Actions', Context='Author'), 'Exec')
    add(action, 'Command', str(python))
    add(action, 'Arguments', '-m scripts.run_scheduled_task granville-shadow')
    add(action, 'WorkingDirectory', str(root))
    return ET.tostring(task, encoding='utf-16', xml_declaration=True)


def main():
    if os.name != 'nt':
        raise ValueError('Install only on the quantitative Windows node')
    root = settings.project_root
    python = Path(r'D:\workspace\conda-envs\deepstock\python.exe')
    if not python.is_file():
        raise ValueError('Dedicated interpreter required')
    identity = subprocess.run(['whoami.exe', '/USER', '/FO', 'CSV', '/NH'],
                              capture_output=True, text=True, check=True)
    identities = list(csv.reader(io.StringIO(identity.stdout.strip())))
    if len(identities) != 1 or not re.fullmatch(r'S-\d+(?:-\d+)+', identities[0][-1]):
        raise ValueError('Verified current Windows user SID required')
    user = identities[0][-1]
    folder = root / 'artifacts/research/granville-shadow-v1/scheduler-backups'
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    # Query all names first: a failed same-name export must never be mistaken
    # for an absent task and authorize overwriting without its backup.
    listing = subprocess.run(['schtasks.exe', '/Query', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, check=True)
    names = {row[0].lstrip('\\') for row in csv.reader(io.StringIO(listing.stdout)) if row}
    if TASK in names:
        prior = subprocess.run(['schtasks.exe', '/Query', '/TN', TASK, '/XML'],
                               capture_output=True, check=True)
        (folder / (now.strftime('%Y%m%dT%H%M%S%f') + '-prior.xml')).write_bytes(prior.stdout)
    path = folder / (now.strftime('%Y%m%dT%H%M%S%f') + '-new.xml')
    path.write_bytes(task_xml(root, python, user, now))
    result = subprocess.run(['schtasks.exe', '/Create', '/TN', TASK, '/XML', str(path), '/F'],
                            capture_output=True)
    if result.returncode:
        path.with_suffix('.error.log').write_bytes(result.stdout + result.stderr)
        raise RuntimeError(f'Task registration failed ({result.returncode}); native diagnostic retained beside XML')
    print(json.dumps({'task': TASK, 'schedule_china': '08:10', 'status': 'installed'}))


if __name__ == '__main__':
    main()
