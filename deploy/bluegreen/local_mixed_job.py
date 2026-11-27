"""One real CSV import, queue execution and artifact download in a disposable lab."""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import requests

from . import processes


def run(*, lab, frontend, manager, database, cookie_name, admin_cookie, rows=20000, timeout=240):
    """Mutations are sent once. Only status GETs repeat; failures preserve jobs.

    This exercises one business batch (two validation jobs plus one import job)
    through the actual HTTP API and spawned drain workers, not the idle executor.
    """
    lab = Path(lab).resolve()
    expected = Path(__file__).resolve().parents[2] / '.local/bluegreen-readiness'
    address = urlparse(frontend)
    if (lab.parent != expected or not lab.name.startswith('build-') or address.scheme != 'http'
            or address.hostname != '127.0.0.1' or not address.port or address.port < 1024):
        raise ValueError('Mixed business jobs require an isolated loopback readiness lab')
    if not 1 <= rows <= 20000 or not 1 <= timeout <= 240:
        raise ValueError('Mixed job row/deadline limits exceeded')
    if manager.config.base != lab or manager.config.frontend_port != address.port:
        raise ValueError('HTTP listener does not match the laboratory configuration')
    state = manager.read()
    proxy = processes.owned(state.get('proxy') or {})
    if proxy is None or not any(connection.status == 'LISTEN' and connection.laddr.port == address.port
                                and connection.laddr.ip == '127.0.0.1'
                                for connection in proxy.net_connections(kind='tcp')):
        raise ValueError('HTTP listener is not owned by the recorded laboratory proxy')
    manager.healthy(state['slots'][state['active']], public=True)
    info = database.info
    if (info.host != '127.0.0.1' or info.dbname != 'readiness_lab'
            or info.user != 'readiness_lab' or info.port < 12000):
        raise ValueError('Mixed job database identity is not a readiness cluster')
    with database.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory')")
        if Path(cursor.fetchone()[0]).resolve() != lab / 'pgdata':
            raise ValueError('Mixed job database data directory does not belong to this laboratory')
    output = lab / 'load'
    output.mkdir(exist_ok=True)
    evidence = output / 'mixed-business-job.json'
    if evidence.exists():
        raise RuntimeError('Mixed job already attempted; inspect its receipt instead of duplicating work')
    reference = date(2020, 1, 17)
    report = {'ok': False, 'productionTouched': False, 'businessBatches': 1, 'rowsExpected': rows,
              'referenceDate': reference.isoformat(), 'http': [], 'jobIds': [],
              'executionOwner': 'real drain child admitted by the active web slot',
              'independentSupervisor': 'idle; identity verified by the surrounding capacity exercise'}
    began = time.monotonic()
    deadline = began + timeout
    session = requests.Session()
    session.trust_env = False
    session.mount('http://', requests.adapters.HTTPAdapter(max_retries=0))
    session.cookies.set(cookie_name, admin_cookie, domain='127.0.0.1', path='/')

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Business import deadline exceeded; accepted jobs are preserved for drain/review')
        return value

    def request(method, path, *, expected_status=200, **kwargs):
        start = time.monotonic()
        response = session.request(method, frontend + path, timeout=(min(5, remaining()), min(30, remaining())),
                                   allow_redirects=False, **kwargs)
        report['http'].append({'method': method, 'path': path, 'status': response.status_code,
                               'ms': round((time.monotonic() - start) * 1000, 2)})
        if response.status_code != expected_status:
            response.close()
            raise RuntimeError(f'Business API {method} returned HTTP {response.status_code}; inspect server logs')
        return response

    def json_request(method, path, **kwargs):
        response = request(method, path, **kwargs)
        try:
            return response.json()
        finally:
            response.close()

    def wait_for_batch(path, item_status, *, prepared=False, batch_status='preview'):
        while True:
            remaining()
            payload = json_request('GET', path)
            current_job = payload.get('job_id')
            if current_job and current_job not in report['jobIds']:
                report['jobIds'].append(current_job)
            if payload['status'] == 'failed':
                raise RuntimeError('Business batch failed; inspect its persisted job and server log')
            item = payload.get('items', [{}])[0]
            if payload['status'] == batch_status and item.get('status') == item_status:
                with database.cursor() as cursor:
                    cursor.execute('SELECT status FROM common_bot_db_sync_job WHERE id=%s', [current_job])
                    job = cursor.fetchone()
                if job and job[0] == 'done' and (not prepared or item.get('metadata', {}).get('prepared')):
                    return payload, item
            time.sleep(min(1, remaining()))

    try:
        # Check the reserved partition before the first mutation.
        with database.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM rotina_prod_bruto_record WHERE report_date=%s', [reference])
            report['rowsBefore'] = cursor.fetchone()[0]
            cursor.execute('SELECT count(*) FROM rotina_bruto_sync_log WHERE report_type=%s AND report_date=%s',
                           ['prod', reference])
            report['auditsBefore'] = cursor.fetchone()[0]
        if report['rowsBefore'] or report['auditsBefore']:
            raise RuntimeError('Reserved synthetic partition is not empty; import refused')
        # Six real product columns; unique matriculas make duplication detectable.
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter=';', lineterminator='\r\n')
        writer.writerow(['desMatricula', 'datAnalise', 'numTempoAnalise', 'nomCliente', 'nomWorkflow', 'nomEtapa'])
        for index in range(rows):
            writer.writerow([f'c{20000 + index:05}a', '17/01/2020 08:01:00', '00:01:00',
                             'CLIENTE SINTETICO', 'FLUXO LOCAL', 'ETAPA LOCAL'])
        content = stream.getvalue().encode('utf-8-sig')
        report.update(sourceBytes=len(content), sourceSha256=hashlib.sha256(content).hexdigest())
        csrf = json_request('GET', '/api/v1/auth/csrf/')['csrfToken']
        session.headers.update({'X-CSRFToken': csrf, 'Origin': frontend})
        base = '/api/v1/rotina-importacoes/'
        payload = json_request('POST', base + 'recebimento/', expected_status=201,
                               json={'files': [{'name': 'producao-local.csv', 'size': len(content)}]})
        batch, item_id = payload['id'], payload['items'][0]['id']
        report['batchId'], report['itemId'] = batch, item_id
        path = base + batch + '/'
        chunk_size = 256 * 1024
        for offset in range(0, len(content), chunk_size):
            data = content[offset:offset + chunk_size]
            received = json_request('PUT', path + f'itens/{item_id}/blocos/?offset={offset}',
                                    data=data, headers={'Content-Type': 'application/octet-stream'})
            if received['offset'] != offset + len(data):
                raise RuntimeError('Upload acknowledged an unexpected byte offset')
            time.sleep(min(.2, remaining()))
        json_request('POST', path + 'recebimento/', expected_status=202, json={})
        wait_for_batch(path, 'needs_input')
        # Product requires explicit reference/completeness review for raw CSV.
        json_request('PATCH', path, expected_status=202,
                     json={'items': [{'id': item_id, 'report_type': 'prod',
                                      'report_date': reference.isoformat(), 'complete': True}]})
        _, preview = wait_for_batch(path, 'ready', prepared=True)
        if preview.get('row_count') != rows or preview['sha256'] != report['sourceSha256']:
            raise RuntimeError('Validated source row count/hash differs from the uploaded synthetic CSV')
        json_request('POST', path + 'confirmar/', expected_status=202, json={})
        _, completed = wait_for_batch(path, 'done', batch_status='done')
        with database.cursor() as cursor:
            cursor.execute('SELECT count(*),count(DISTINCT des_matricula),count(*) FILTER (WHERE dat_analise=%s) '
                           'FROM rotina_prod_bruto_record WHERE report_date=%s', [reference, reference])
            report['rowsAfter'], report['distinctSyntheticRecords'], report['rowsWithExpectedDate'] = cursor.fetchone()
            cursor.execute('SELECT id,success,row_count,finished_at IS NOT NULL FROM rotina_bruto_sync_log '
                           'WHERE report_type=%s AND report_date=%s ORDER BY id', ['prod', reference])
            report['audit'] = [dict(zip(('id', 'success', 'rows', 'finished'), row)) for row in cursor.fetchall()]
            cursor.execute('SELECT id,status,attempts,finished_at IS NOT NULL FROM common_bot_db_sync_job '
                           'WHERE domain=%s AND source_path=%s ORDER BY id', ['rotina_importacao', batch])
            report['jobs'] = [dict(zip(('id', 'status', 'attempts', 'finished'), row)) for row in cursor.fetchall()]
            cursor.execute('SELECT stored_name FROM rotina_bruto_rotinaimportitem WHERE id=%s AND batch_id=%s',
                           [item_id, batch])
            stored_name = cursor.fetchone()[0]
        storage = lab / 'data/rotina-import' / batch
        source = (storage / stored_name).resolve()
        if not source.is_relative_to(storage.resolve()) or hashlib.sha256(source.read_bytes()).hexdigest() != report['sourceSha256']:
            raise RuntimeError('Persisted uploaded bytes differ from the source hash')
        outputs = completed['outputs']
        if not outputs:
            raise RuntimeError('Completed import did not expose an artifact')
        artifact = (storage / outputs[0]).resolve()
        if not artifact.is_relative_to(storage.resolve()):
            raise RuntimeError('Artifact escaped its synthetic batch directory')
        response = request('GET', path + f'itens/{item_id}/arquivos/0/')
        try:
            downloaded = response.content
        finally:
            response.close()
        report['downloadBytes'] = len(downloaded)
        report['downloadSha256'] = hashlib.sha256(downloaded).hexdigest()
        report['downloadParquetMagic'] = downloaded[:4] == b'PAR1' and downloaded[-4:] == b'PAR1'
        report['downloadHashMatches'] = report['downloadSha256'] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        report['ok'] = (report['rowsAfter'] == rows == report['distinctSyntheticRecords'] == report['rowsWithExpectedDate']
                        and len(report['audit']) == 1 and report['audit'][0]['success']
                        and report['audit'][0]['finished'] and report['audit'][0]['rows'] == rows
                        and len(report['jobs']) == len(report['jobIds']) == 3
                        and all(j['status'] == 'done' and j['attempts'] == 1 and j['finished'] for j in report['jobs'])
                        and report['downloadParquetMagic'] and report['downloadHashMatches'])
    except Exception as error:
        report['error'] = str(error)
    finally:
        session.close()
        report['seconds'] = round(time.monotonic() - began, 3)
        evidence.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
