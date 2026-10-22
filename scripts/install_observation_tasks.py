"""Migrate existing observation tasks to verified passwordless S4U execution."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import io
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from deepstock.web.config import settings

NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
NAMES = {'Deepstock_Defensive_ETF_Observation': '07:30', 'Deepstock_ADX_Shadow': '07:45',
         'Deepstock_Granville_Shadow': '08:10'}
PYTHON = Path(r'D:\workspace\conda-envs\deepstock\python.exe')
ET.register_namespace('', NS)


def native(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        folder = settings.project_root / 'artifacts/research/observation-scheduler/native-errors'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (uuid.uuid4().hex + '.log')).write_bytes(result.stdout + result.stderr)
        raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}); native diagnostic retained locally')
    return result.stdout


def current_identity():
    raw = native(['whoami.exe', '/USER', '/FO', 'CSV', '/NH']).decode(locale.getpreferredencoding(False))
    rows = list(csv.reader(io.StringIO(raw.strip())))
    if len(rows) != 1 or not re.fullmatch(r'S-\d+(?:-\d+)+', rows[0][-1]):
        raise ValueError('Verified current Windows user SID required')
    return rows[0][0], rows[0][-1]


def parse_xml(raw):
    # schtasks redirected output may be OEM text with a UTF-16 declaration.
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\x00' in raw[:100]:
        return ET.fromstring(raw.decode('utf-16'))
    return ET.fromstring(raw.decode(locale.getpreferredencoding(False)))


def set_text(parent, path, text):
    item = parent.find(path, {'t': NS})
    if item is None:
        item = ET.SubElement(parent, '{' + NS + '}' + path.removeprefix('t:'))
    item.text = text


def s4u_xml(raw, task_name, identity, now):
    if now.utcoffset() != timedelta(hours=8):
        raise ValueError('Verified China timezone required')
    value = parse_xml(raw)
    principal = value.find('t:Principals/t:Principal', {'t': NS})
    if principal is None:
        raise ValueError('Existing task principal missing')
    old_user = principal.findtext('t:UserId', namespaces={'t': NS})
    username, sid = identity
    if not old_user or old_user.casefold() not in {sid.casefold(), username.casefold(), username.split('\\')[-1].casefold()}:
        raise ValueError('Existing task belongs to a different user')
    triggers = value.findall('t:Triggers/*', {'t': NS})
    if len(triggers) != 1 or triggers[0].tag != '{' + NS + '}CalendarTrigger':
        raise ValueError('Expected one existing daily trigger')
    trigger = triggers[0]
    boundary = trigger.find('t:StartBoundary', {'t': NS})
    if boundary is None or datetime.fromisoformat(boundary.text).strftime('%H:%M') != NAMES[task_name]:
        raise ValueError('Existing task time differs from the authorized schedule')
    if trigger.findtext('t:ScheduleByDay/t:DaysInterval', namespaces={'t': NS}) != '1':
        raise ValueError('Existing task is not daily')
    start = now.replace(hour=int(NAMES[task_name][:2]), minute=int(NAMES[task_name][3:]), second=0, microsecond=0)
    if start <= now:
        start += timedelta(days=1)
    boundary.text = start.isoformat()
    set_text(principal, 't:UserId', sid)
    set_text(principal, 't:LogonType', 'S4U')
    set_text(principal, 't:RunLevel', 'LeastPrivilege')
    options = value.find('t:Settings', {'t': NS})
    if options is None:
        raise ValueError('Existing task settings missing')
    for key, text in {'WakeToRun': 'true', 'StartWhenAvailable': 'true', 'MultipleInstancesPolicy': 'IgnoreNew',
                      'DisallowStartIfOnBatteries': 'false', 'StopIfGoingOnBatteries': 'false',
                      'AllowStartOnDemand': 'true', 'Enabled': 'true', 'ExecutionTimeLimit': 'PT2H'}.items():
        set_text(options, 't:' + key, text)
    return ET.tostring(value, encoding='utf-16', xml_declaration=True)


def probe_xml(root, python, sid, output):
    # No trigger: the diagnostic runs exactly once on demand and is deleted.
    value = ET.Element('{' + NS + '}Task', version='1.2')
    def add(parent, name, text=None, **attrs):
        element = ET.SubElement(parent, '{' + NS + '}' + name, attrs)
        element.text = text
        return element
    principal = add(add(value, 'Principals'), 'Principal', id='Author')
    for key, text in {'UserId': sid, 'LogonType': 'S4U', 'RunLevel': 'LeastPrivilege'}.items():
        add(principal, key, text)
    options = add(value, 'Settings')
    for key, text in {'MultipleInstancesPolicy': 'IgnoreNew', 'DisallowStartIfOnBatteries': 'false',
                      'StopIfGoingOnBatteries': 'false', 'AllowStartOnDemand': 'true',
                      'Enabled': 'true', 'ExecutionTimeLimit': 'PT2M'}.items():
        add(options, key, text)
    action = add(add(value, 'Actions', Context='Author'), 'Exec')
    # Capture interpreter/import failures that occur before the result writer.
    add(action, 'Command', r'C:\Windows\System32\cmd.exe')
    add(action, 'Arguments', f'/d /c ""{python}" -X utf8 -u -m scripts.probe_observation_environment '
        f'--expected-sid {sid} --output "{output}" > "{output.with_suffix(".console.log")}" 2>&1"')
    add(action, 'WorkingDirectory', str(root))
    return ET.tostring(value, encoding='utf-16', xml_declaration=True)


def run_probe(folder, sid, *, timeout=110):
    task = 'Deepstock_S4U_Probe_' + uuid.uuid4().hex
    output, xml = folder / 'probe-result.json', folder / 'probe-task.xml'
    xml.write_bytes(probe_xml(settings.project_root, PYTHON, sid, output))
    created = False
    try:
        native(['schtasks.exe', '/Create', '/TN', task, '/XML', str(xml)])
        created = True
        native(['schtasks.exe', '/Run', '/TN', task])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if output.exists():
                result = json.loads(output.read_text(encoding='utf-8'))
                print(json.dumps({'s4u_probe': result}), flush=True)
                if result.get('status') != 'passed':
                    raise RuntimeError(f"S4U probe failed at {result.get('failed_check')}: {result.get('reason', result.get('error_type'))}; existing tasks unchanged")
                return result
            time.sleep(1)
        status = native(['schtasks.exe', '/Query', '/TN', task, '/V', '/FO', 'CSV'])
        (folder / 'probe-scheduler-status.csv').write_bytes(status)
        progress = output.with_suffix('.progress.json')
        stage = json.loads(progress.read_text(encoding='utf-8')).get('stage') if progress.exists() else 'before_result_writer'
        raise RuntimeError(f'S4U probe timed out at {stage}; existing tasks unchanged; native diagnostics retained')
    finally:
        if created:
            # End is best-effort (a completed task is normally not running).
            subprocess.run(['schtasks.exe', '/End', '/TN', task], capture_output=True)
            native(['schtasks.exe', '/Delete', '/TN', task, '/F'])


def install(names=tuple(NAMES), *, probe_only=False):
    if os.name != 'nt' or not PYTHON.is_file():
        raise ValueError('Dedicated quantitative Windows interpreter required')
    identity = current_identity()
    now = datetime.now().astimezone()
    folder = settings.project_root / 'artifacts/research/observation-scheduler' / now.strftime('%Y%m%dT%H%M%S%f')
    folder.mkdir(parents=True)
    replacements = {}
    # Back up and validate every selected task before any registration.
    for name in names:
        prior = native(['schtasks.exe', '/Query', '/TN', name, '/XML'])
        (folder / (name + '-prior.xml')).write_bytes(prior)
        replacements[name] = s4u_xml(prior, name, identity, now)
    run_probe(folder, identity[1])
    if probe_only:
        return
    changed = []
    try:
        for name, xml in replacements.items():
            path = folder / (name + '-new.xml')
            path.write_bytes(xml)
            changed.append(name)  # Restore even a partially failed registration.
            native(['schtasks.exe', '/Create', '/TN', name, '/XML', str(path), '/F'])
            actual = parse_xml(native(['schtasks.exe', '/Query', '/TN', name, '/XML']))
            if (actual.findtext('t:Principals/t:Principal/t:LogonType', namespaces={'t': NS}) != 'S4U'
                    or actual.findtext('t:Principals/t:Principal/t:UserId', namespaces={'t': NS}) != identity[1]):
                raise RuntimeError('Registered principal verification failed')
    except Exception:
        failures = []
        for name in reversed(changed):
            try:
                native(['schtasks.exe', '/Create', '/TN', name, '/XML', str(folder / (name + '-prior.xml')), '/F'])
            except RuntimeError:
                failures.append(name)
        if failures:
            raise RuntimeError('Task rollback failed for: ' + ', '.join(failures)) from None
        raise
    print(json.dumps({'status': 'installed', 'logon_type': 'S4U', 'schedules_china': {name: NAMES[name] for name in names}}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=NAMES, action='append')
    parser.add_argument('--probe-only', action='store_true')
    args = parser.parse_args()
    install(tuple(args.task) if args.task else tuple(NAMES), probe_only=args.probe_only)


if __name__ == '__main__':
    main()
