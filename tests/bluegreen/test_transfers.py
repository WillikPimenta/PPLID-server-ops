from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import urllib.request

from deploy.bluegreen.caddy import OPENER


def test_large_upload_download_and_old_assets_during_switch(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, '1')
    second = make_release(config, '2')
    payload = b'large-synthetic-transfer-' * (1024 * 256)  # 6 MiB
    asset = config.release(first) / 'frontend/dist/assets/large.bin'
    asset.write_bytes(payload)
    manager.prepare(first)
    manager.activate()
    def upload():
        req = urllib.request.Request(f'http://127.0.0.1:{config.frontend_port}/api/echo',
                                     data=payload, method='POST', headers={'Content-Type': 'application/octet-stream'})
        with OPENER.open(req, timeout=30) as response:
            result = json.load(response)
        assert result['method'] == 'POST'
        assert result['body'].encode() == payload
    def download():
        with OPENER.open(f'http://127.0.0.1:{config.frontend_port}/assets/large.bin', timeout=30) as response:
            assert hashlib.sha256(response.read()).digest() == hashlib.sha256(payload).digest()
    with ThreadPoolExecutor(max_workers=2) as pool:
        up = pool.submit(upload)
        down = pool.submit(download)
        manager.promote(second)
        up.result()
        down.result()
    download()  # Client from the old release can still load its old asset.
    manager.rollback()
    upload()
