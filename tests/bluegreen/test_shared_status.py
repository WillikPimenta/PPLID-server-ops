"""Independent environments must preserve each other's shared status."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_concurrent_environment_status_updates_preserve_all_entries(tmp_path):
    script = r'''
from pathlib import Path
from types import SimpleNamespace
import subprocess, sys, time
from unittest.mock import patch
from deploy.bluegreen.manager import Manager
base=Path(sys.argv[1]); env=sys.argv[2]
manager=object.__new__(Manager)
manager.config=SimpleNamespace(base=base,root=base/'deploy'/env,environment=env)
state={'phase':'idle','active':'A','slots':{'A':{'sha':env,'shaFull':env,'release':str(base/env)}},'previous':None}
while not (base/'start').exists(): time.sleep(.01)
with patch('deploy.bluegreen.manager.subprocess.run',return_value=SimpleNamespace(returncode=0)):
    for i in range(10): manager.sync_legacy(state)
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path), env],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                 for env in ('MAIN', 'DEV', 'HOM')]
    try:
        (tmp_path / 'start').touch()
        for process in processes:
            out, err = process.communicate(timeout=45)
            assert process.returncode == 0, err.decode(errors='replace')
        status = json.loads((tmp_path / 'logs/deploy-status.json').read_text())
        assert set(status['environments']) == {'MAIN', 'DEV', 'HOM'}
        assert all(status['environments'][env]['deployedSha'] == env for env in ('MAIN', 'DEV', 'HOM'))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
