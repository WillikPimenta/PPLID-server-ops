"""Load the release's pure admission protocol without importing Django/bots."""
import hashlib
from pathlib import Path

from .store import read_json
from .worker_protocol import Controller

CONTRACT = {'schemaVersion': 1, 'admission': 'app.core.worker_drain', 'shutdown': 'drain-without-termination'}


def controller(release, directory):
    release = Path(release).resolve()
    if read_json(release / 'scripts/deploy/bluegreen-workers-contract.json') != CONTRACT:
        raise RuntimeError('Cooperative worker drain contract v1 is required')
    source = release / 'automacoes/app/core/worker_drain.py'
    if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(release):
        raise RuntimeError('Worker admission protocol must be a physical release file')
    # The application vendors this standalone protocol. Verify the paired
    # version without executing application imports in the orchestrator.
    canonical = Path(__file__).with_name('worker_protocol.py')
    normalize = lambda path: path.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(normalize(source)).digest() != hashlib.sha256(normalize(canonical)).digest():
        raise RuntimeError('Application worker protocol differs from the reviewed ops version')
    return Controller(directory)


def verify_stopped(record):
    path = record.get('stoppedFile')
    if record.get('protocol') != 'worker-drain-v1' or not path or not Path(path).is_file():
        raise RuntimeError('Cooperative shutdown receipt missing; replacement refused')
    receipt = read_json(Path(path))
    if (receipt.get('drained') is not True or receipt.get('instanceId') != record.get('instanceId')
            or receipt.get('sha') != record.get('sha')):
        raise RuntimeError('Worker shutdown receipt identity mismatch')
    control = controller(record['release'], record['workerControlDir'])
    status = control.status()
    if status['phase'] != 'stopped' or status['instanceId'] != record['instanceId'] or status['units']:
        raise RuntimeError('Worker admission has not confirmed complete draining')
    return receipt
