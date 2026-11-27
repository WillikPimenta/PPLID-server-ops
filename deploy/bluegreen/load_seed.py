"""Execute with manage.py shell, only inside a verified readiness DB cluster."""
from datetime import timedelta
import json
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection, transaction
from django.utils import timezone

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.automacoes.supervisor_control import _protect
from apps.automacoes.models import AutomacaoRobotModeConfig
from app.config.constants import ROBOT_MODES
from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.falhas_criticas.models import Failure, FalhasAgent


@transaction.atomic
def run():
    lab = Path(os.environ['PPLID_BASE_DIR']).resolve()
    if lab.parent.name != 'bluegreen-readiness' or not lab.name.startswith('build-') or lab.parent.parent.name != '.local':
        raise RuntimeError('Seed is restricted to a disposable readiness cluster')
    db = settings.DATABASES['default']
    if db['HOST'] != '127.0.0.1' or db['NAME'] != 'readiness_lab' or db['USER'] != 'readiness_lab' or int(db['PORT']) < 12000:
        raise RuntimeError('Unexpected database identity')
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory')")
        actual = Path(cursor.fetchone()[0]).resolve()
    if os.path.normcase(str(actual)) != os.path.normcase(str(lab / 'pgdata')):
        raise RuntimeError('PostgreSQL data directory does not belong to this laboratory')
    output = lab / 'load'
    output.mkdir(exist_ok=True)
    if (output / 'seed-report.json').exists():
        raise RuntimeError('Seed has already run; reuse its encrypted sessions instead of duplicating data')
    if Failure.objects.exists() or FalhasAgent.objects.exists():
        raise RuntimeError('Seed requires empty synthetic failure tables')
    if get_user_model().objects.filter(username__startswith='c98').exists():
        raise RuntimeError('Synthetic load users already exist; refusing a partial reseed')
    today = timezone.localdate()
    for mode in ROBOT_MODES:
        AutomacaoRobotModeConfig.objects.update_or_create(mode=mode, defaults={'config': {'auto_start': False}})
    agents = [FalhasAgent(matricula_norm=f'load{i:04}', name=f'Pessoa sintetica {i}',
                          localidade='SAO CARLOS', leader_nome=f'Lider {i % 20}',
                          atividade_atual='Conferencia', team='Laboratorio') for i in range(500)]
    FalhasAgent.objects.bulk_create(agents)
    rows = [Failure(protocolo=f'LOCAL-LOAD-{i:08}', data_analise=today-timedelta(days=i % 30),
                    etapa='Conferencia', categoria='Cadastro', cenario='Erro cadastral',
                    novo_cenario='Falso Negativo', localidade='SAO CARLOS',
                    cliente=f'Cliente {i % 10}', workflow=f'Fluxo {i % 25}',
                    dificuldade='Baixa', tipo_documento='RG', uf_documento='SP',
                    tipo_falha='FN', agent_id=f'load{i % 500:04}') for i in range(20000)]
    Failure.objects.bulk_create(rows, batch_size=1000)
    User = get_user_model()
    password_hash = make_password(os.environ['READINESS_PASSWORD'])
    groups = [Group.objects.get_or_create(name=value)[0] for value in (GROUP_GLOBAL, role_group_name(ROLE_PLAN_ANALISTA))]
    cookies = []
    admin_cookie = None
    for i in range(150):
        user = User.objects.create(username=f'c{98000+i:05}a', password=password_hash,
                                   first_name=f'Pessoa sintetica {i}', is_active=True,
                                   is_staff=i == 0, is_superuser=i == 0)
        user.groups.add(*groups)
        session = SessionStore()
        session['_auth_user_id'] = str(user.pk)
        session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        session['_auth_user_hash'] = user.get_session_auth_hash()
        session.save()
        cookies.append(session.session_key)
        if i == 0:
            admin_cookie = session.session_key
    private = {'cookies': cookies, 'adminCookie': admin_cookie, 'cookieName': settings.SESSION_COOKIE_NAME}
    (output / 'private-sessions.dpapi').write_bytes(_protect(json.dumps(private).encode()))
    report = {'users': 150, 'profiles': {'globalPlanning': 149, 'administrator': 1},
              'legacyFailureRows': 20000, 'agents': 500, 'clients': 10, 'workflows': 25,
              'from': (today-timedelta(days=29)).isoformat(), 'to': today.isoformat(),
              'failureSource': settings.FALHAS_FAILURE_SOURCE,
              'productionTouched': False, 'cookiesEncryptedToWindowsAccount': True}
    (output / 'seed-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


run()
