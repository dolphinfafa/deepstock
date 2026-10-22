from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from scripts import install_observation_tasks as installer

NOW = datetime(2026, 10, 22, 11, tzinfo=timezone(timedelta(hours=8)))
SID = 'S-1-5-21-1000'
IDENTITY = ('HOST\\admin', SID)
NS = {'t': installer.NS}


def prior_xml(name):
    # Existing actions are preserved, including the defensive .cmd wrapper.
    action = ('<Command>D:\\workspace\\deepstock\\scripts\\run_defensive_etf_observation.cmd</Command>'
              if 'Defensive' in name else '<Command>python.exe</Command><Arguments>-m scripts.run_scheduled_task shadow</Arguments>')
    return f'''<Task xmlns="{installer.NS}" version="1.3">
      <Triggers><CalendarTrigger><StartBoundary>2026-10-14T{installer.NAMES[name]}:00+08:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
      <Principals><Principal id="Author"><UserId>{SID}</UserId><LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
      <Settings><WakeToRun>true</WakeToRun><StartWhenAvailable>true</StartWhenAvailable>
      <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries></Settings>
      <Actions Context="Author"><Exec>{action}<WorkingDirectory>D:\\workspace\\deepstock</WorkingDirectory></Exec></Actions>
      </Task>'''.encode('utf-16')


@pytest.mark.parametrize('name,clock', installer.NAMES.items())
def test_s4u_keeps_actions_identity_times_and_sets_background_safety(name, clock):
    old = prior_xml(name)
    value = ET.fromstring(installer.s4u_xml(old, name, IDENTITY, NOW))
    def text(path):
        return value.findtext(path, namespaces=NS)
    assert ET.tostring(value.find('t:Actions', NS)) == ET.tostring(ET.fromstring(old).find('t:Actions', NS))
    assert text('t:Principals/t:Principal/t:UserId') == SID
    assert text('t:Principals/t:Principal/t:LogonType') == 'S4U'
    assert text('t:Principals/t:Principal/t:RunLevel') == 'LeastPrivilege'
    assert text('t:Triggers/t:CalendarTrigger/t:StartBoundary') == f'2026-10-23T{clock}:00+08:00'
    for key, expected in {'WakeToRun': 'true', 'StartWhenAvailable': 'true', 'MultipleInstancesPolicy': 'IgnoreNew',
                          'DisallowStartIfOnBatteries': 'false', 'StopIfGoingOnBatteries': 'false'}.items():
        assert text('t:Settings/t:' + key) == expected
    with pytest.raises(ValueError, match='China timezone'):
        installer.s4u_xml(old, name, IDENTITY, NOW.astimezone(timezone.utc))
    with pytest.raises(ValueError, match='different user'):
        installer.s4u_xml(old, name, ('HOST\\other', 'S-1-5-21-2000'), NOW)


def prepare_install(tmp_path, monkeypatch):
    python = tmp_path / 'python.exe'
    python.touch()
    monkeypatch.setattr(installer, 'PYTHON', python)
    monkeypatch.setattr(installer, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(installer, 'settings', SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr(installer, 'current_identity', lambda: IDENTITY)
    class Clock(datetime):
        @classmethod
        def now(cls):
            return NOW
    monkeypatch.setattr(installer, 'datetime', Clock)


def test_failed_probe_backs_up_every_task_and_does_not_replace_any(tmp_path, monkeypatch):
    prepare_install(tmp_path, monkeypatch)
    calls = []
    def native(args):
        calls.append(args)
        assert '/Query' in args
        return prior_xml(args[args.index('/TN') + 1])
    monkeypatch.setattr(installer, 'native', native)
    def probe(folder, sid):
        assert len(list(folder.glob('*-prior.xml'))) == 3
        raise RuntimeError('Norgate unavailable in S4U')
    monkeypatch.setattr(installer, 'run_probe', probe)
    with pytest.raises(RuntimeError, match='Norgate'):
        installer.install()
    assert len(calls) == 3


def test_registration_failure_restores_all_attempted_tasks(tmp_path, monkeypatch):
    prepare_install(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, 'run_probe', lambda *a: None)
    created = []
    def native(args):
        name = args[args.index('/TN') + 1]
        if '/Query' in args:
            if not created:
                return prior_xml(name)
            return installer.s4u_xml(prior_xml(name), name, IDENTITY, NOW)
        xml = Path(args[args.index('/XML') + 1])
        created.append((name, xml.name))
        if len(created) == 2:
            raise RuntimeError('registration failed')
        return b''
    monkeypatch.setattr(installer, 'native', native)
    with pytest.raises(RuntimeError, match='registration failed'):
        installer.install()
    first, second = list(installer.NAMES)[:2]
    assert created == [(first, first + '-new.xml'), (second, second + '-new.xml'),
                       (second, second + '-prior.xml'), (first, first + '-prior.xml')]


def test_probe_task_is_on_demand_and_removed_after_diagnostic_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'settings', SimpleNamespace(project_root=tmp_path))
    calls = []
    def native(args):
        calls.append(args)
        if '/Run' in args:
            (tmp_path / 'probe-result.json').write_text(json.dumps({'status': 'failed', 'failed_check': 'norgate', 'error_type': 'ValueError'}))
        return b''
    monkeypatch.setattr(installer, 'native', native)
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='existing tasks unchanged'):
        installer.run_probe(tmp_path, SID)
    assert '/Delete' in calls[-1]
    xml = ET.fromstring((tmp_path / 'probe-task.xml').read_bytes())
    assert xml.find('t:Triggers', NS) is None
    assert xml.findtext('t:Principals/t:Principal/t:LogonType', namespaces=NS) == 'S4U'
    assert xml.findtext('t:Actions/t:Exec/t:Command', namespaces=NS) == r'C:\Windows\System32\cmd.exe'
    assert '.console.log' in xml.findtext('t:Actions/t:Exec/t:Arguments', namespaces=NS)


def test_probe_startup_timeout_keeps_native_diagnostic_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'settings', SimpleNamespace(project_root=tmp_path))
    calls = []
    monkeypatch.setattr(installer, 'native', lambda args: calls.append(args) or b'native-status')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='before_result_writer'):
        installer.run_probe(tmp_path, SID, timeout=0)
    assert (tmp_path / 'probe-scheduler-status.csv').read_bytes() == b'native-status'
    assert '/Delete' in calls[-1]
