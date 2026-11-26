from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

from deploy.bluegreen.probe import run


def test_independent_probe_observes_promotion_and_rollback(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, 'a')
    second = make_release(config, 'b')
    manager.prepare(first)
    manager.activate()
    output = config.base / 'independent-probe.jsonl'
    urls = [f'http://127.0.0.1:{config.backend_port}/api/echo',
            f'http://127.0.0.1:{config.frontend_port}/']
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(run, urls, output, 30, 10)
        manager.promote(second)
        manager.rollback()
        summary = result.result(timeout=40)
    for value in summary.values():
        assert value['samples'] > 100
        assert value['errors'] == 0
    destination = Path(__file__).resolve().parents[2] / '.local/bluegreen-probe-latest.summary.json'
    shutil.copyfile(output.with_suffix('.summary.json'), destination)
    print(json.dumps(summary))
