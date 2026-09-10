from pathlib import Path

import environ
from corsheaders.defaults import default_headers as CORS_DEFAULT_HEADERS

from config.env_bootstrap import bootstrap_environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173", "http://localhost:5174"]),
)

bootstrap_environ(BASE_DIR)

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-key")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "django_filters",
    "apps.accounts",
    "apps.access",
    "apps.workforce",
    "apps.communication",
    "apps.falhas_criticas",
    "apps.produtividade",
    "apps.produtividade_case",
    "apps.monitoramento_sla",
    "apps.monitor_eventos",
    "apps.rotina_bruto",
    "apps.replicacao_d1",
    "apps.escala_flex",
    "apps.automacoes",
    "apps.psa",
    "apps.cyber_psa",
    "apps.suporte_claro",
    "apps.brb_report",
    "apps.suporte_operacional",
    "apps.portal_notifications",
    "apps.auditoria",
    "apps.qualidade_operacional",
    "apps.dimensoes_processos",
    "apps.planejamento_demandas",
    "apps.controle_sla",
    "apps.prioridades_nh.apps.PrioridadesNhConfig",
    "apps.ops_monitoring",
    "apps.common",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.access_log.AccessLogMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.ops_monitoring.middleware.RequestMetricsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="pplid"),
        "USER": env("POSTGRES_USER", default="pplid"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="pplid"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        # Reusa conexão no worker Waitress (0 = fecha a cada request — churn em polling).
        "CONN_MAX_AGE": env.int("POSTGRES_CONN_MAX_AGE", default=60),
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

_DEV_FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
# Hostname LAN usado no Vite (allowedHosts) — sem isso, POST/CSRF falha ao acessar
# por http://monitoramento.local:517x a partir de outra máquina.
_DEV_FRONTEND_ORIGINS += [
    f"{scheme}://monitoramento.local:{port}"
    for scheme in ("http", "https")
    for port in (5173, 5174, 5175)
]


def _origin_variants_from_url(url: str) -> list[str]:
    """Expande PPLID_FRONTEND_URL / LAN em origens http+https úteis p/ CSRF/CORS."""
    text = (url or "").strip().rstrip("/")
    if not text:
        return []
    if "://" not in text:
        text = f"http://{text}"
    try:
        from urllib.parse import urlparse

        parsed = urlparse(text)
    except Exception:
        return [text]
    if not parsed.hostname:
        return [text]
    host = parsed.hostname
    port = parsed.port
    origins: list[str] = []
    for scheme in ("http", "https"):
        if port:
            origins.append(f"{scheme}://{host}:{port}")
        else:
            origins.append(f"{scheme}://{host}")
    return origins


_cors_from_env = env.list("CORS_ALLOWED_ORIGINS", default=_DEV_FRONTEND_ORIGINS)
_csrf_from_env = env.list("CSRF_TRUSTED_ORIGINS", default=_DEV_FRONTEND_ORIGINS)
# PPLID_FRONTEND_URL pode apontar para IP LAN do servidor — incluir nas origens confiáveis.
_frontend_url_origins = _origin_variants_from_url(
    env("PPLID_FRONTEND_URL", default="http://localhost:5173")
)
_merged_dev_origins = list(
    dict.fromkeys([*_DEV_FRONTEND_ORIGINS, *_frontend_url_origins])
)
CORS_ALLOWED_ORIGINS = (
    list(dict.fromkeys([*_cors_from_env, *_merged_dev_origins]))
    if DEBUG
    else list(dict.fromkeys([*_cors_from_env, *_frontend_url_origins]))
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = list(CORS_DEFAULT_HEADERS) + [
    "x-automacao-client-id",
]

CSRF_TRUSTED_ORIGINS = (
    list(dict.fromkeys([*_csrf_from_env, *_merged_dev_origins]))
    if DEBUG
    else list(dict.fromkeys([*_csrf_from_env, *_frontend_url_origins]))
)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
# Cookies em localhost IGNORAM a porta: 5173/5174/5175 compartilham o jar.
# Cada ambiente (MAIN/DEV/HOM) precisa de nomes distintos para login paralelo.
SESSION_COOKIE_NAME = env("SESSION_COOKIE_NAME", default="pplid_local_sessionid")
_csrf_cookie_default = (
    SESSION_COOKIE_NAME.replace("sessionid", "csrftoken")
    if "sessionid" in SESSION_COOKIE_NAME
    else "pplid_local_csrftoken"
)
CSRF_COOKIE_NAME = env("CSRF_COOKIE_NAME", default=_csrf_cookie_default)

# Frontend PPLID (login Vue) — módulo falhas_criticas reutiliza a mesma sessão Django.
PPLID_FRONTEND_URL = env("PPLID_FRONTEND_URL", default="http://localhost:5173")
PPLID_ENV_PROFILE = env("PPLID_ENV_PROFILE", default="local")

# Planilha do módulo Report Falhas (fases 3+).
EXCEL_SOURCE_PATH = env("EXCEL_SOURCE_PATH", default="")
EXCEL_SYNC_DEBOUNCE_SECONDS = env.int("EXCEL_SYNC_DEBOUNCE_SECONDS", default=60)
EXCEL_SYNC_POLL_SECONDS = env.int("EXCEL_SYNC_POLL_SECONDS", default=30)

# Filas bot→banco: high serializado (pesados); low pode coexistir.
BOT_DB_SYNC_QUEUE_WAIT_S = env.int("BOT_DB_SYNC_QUEUE_WAIT_S", default=900)
BOT_DB_SYNC_POLL_MS = env.int("BOT_DB_SYNC_POLL_MS", default=500)
# Job stuck em running (drain morto) → requeue/failed.
BOT_DB_SYNC_STALE_MINUTES = env.int("BOT_DB_SYNC_STALE_MINUTES", default=45)
BOT_DB_SYNC_STALE_MAX_REQUEUE = env.int("BOT_DB_SYNC_STALE_MAX_REQUEUE", default=2)
# Guard de RAM antes de spawn/claim high (MB livres). Fail-open se API falhar.
BOT_DB_SYNC_MIN_FREE_MB = env.int("BOT_DB_SYNC_MIN_FREE_MB", default=2048)
# Após MemoryError: cooldown extra no spawn da lane (segundos).
BOT_DB_SYNC_OOM_SPAWN_COOLDOWN_SEC = env.int(
    "BOT_DB_SYNC_OOM_SPAWN_COOLDOWN_SEC", default=300
)

# Pasta do relatório de produtividade HxH (brflow-prod-hxh-bruto).
PRODUTIVIDADE_SOURCE_DIR = env("PRODUTIVIDADE_SOURCE_DIR", default="")
MONITOR_EVENTOS_SOURCE_DIR = env("MONITOR_EVENTOS_SOURCE_DIR", default="")
REPLICACAO_D1_SOURCE_DIR = env("REPLICACAO_D1_SOURCE_DIR", default="")
REPLICACAO_D1_REPLICADOS_DIR = env("REPLICACAO_D1_REPLICADOS_DIR", default="")
REPLICACAO_D1_SYNC_LOG_RETENTION_DAYS = env.int("REPLICACAO_D1_SYNC_LOG_RETENTION_DAYS", default=90)
REPLICACAO_D1_INGESTION_RETENTION_DAYS = env.int("REPLICACAO_D1_INGESTION_RETENTION_DAYS", default=180)
# Feature flags rollout D-1 (Fase 15.3) — rollback via env sem perder dados novos.
REPLICACAO_D1_FF_NEW_INGESTION = env.bool("REPLICACAO_D1_FF_NEW_INGESTION", default=True)
REPLICACAO_D1_FF_NEW_RECONCILIATION = env.bool("REPLICACAO_D1_FF_NEW_RECONCILIATION", default=True)
REPLICACAO_D1_FF_DASHBOARD_DB = env.bool("REPLICACAO_D1_FF_DASHBOARD_DB", default=True)
REPLICACAO_D1_FF_DASHBOARD_NO_FILES = env.bool("REPLICACAO_D1_FF_DASHBOARD_NO_FILES", default=False)
REPLICACAO_D1_FF_SHADOW_MODE = env.bool("REPLICACAO_D1_FF_SHADOW_MODE", default=False)
ROTINA_DETALHADO_BRUTO_DIR = env("ROTINA_DETALHADO_BRUTO_DIR", default="")
ROTINA_PROD_BRUTO_DIR = env("ROTINA_PROD_BRUTO_DIR", default="")
ROTINA_MONITOR_TRATADO_DIR = env("ROTINA_MONITOR_TRATADO_DIR", default="")
ROTINA_MONITOR_BRUTO_DIR = env("ROTINA_MONITOR_BRUTO_DIR", default="")  # legado; use TRATADO_DIR
ROTINA_CONFER_BUSCA_TRATADO_DIR = env("ROTINA_CONFER_BUSCA_TRATADO_DIR", default="")
ROTINA_GED_DETALHADO_TRATADO_DIR = env("ROTINA_GED_DETALHADO_TRATADO_DIR", default="")
ROTINA_GED_IRREGULARIDADE_TRATADO_DIR = env("ROTINA_GED_IRREGULARIDADE_TRATADO_DIR", default="")
ROTINA_G_AUDITORIA_TRATADO_DIR = env("ROTINA_G_AUDITORIA_TRATADO_DIR", default="")
DERIVACAO_ETAPA_CSV_DIR = env(
    "DERIVACAO_ETAPA_CSV_DIR",
    default=str(BASE_DIR.parent / "derivacao_etapa"),
)
DERIVACAO_ETAPA_UPLOAD_MAX_FILES = env.int("DERIVACAO_ETAPA_UPLOAD_MAX_FILES", default=400)
DERIVACAO_ETAPA_UPLOAD_MAX_BYTES = env.int(
    "DERIVACAO_ETAPA_UPLOAD_MAX_BYTES",
    default=200 * 1024 * 1024,
)
DERIVACAO_ETAPA_UPLOAD_TTL_HOURS = env.int("DERIVACAO_ETAPA_UPLOAD_TTL_HOURS", default=24)
DERIVACAO_ETAPA_IMPORT_STALE_MINUTES = env.int("DERIVACAO_ETAPA_IMPORT_STALE_MINUTES", default=15)
DERIVACAO_ETAPA_PURGE_SYNC = env.bool("DERIVACAO_ETAPA_PURGE_SYNC", default=False)
ESCALA_FLEX_ADMIN_LAN_IDS = env.list("ESCALA_FLEX_ADMIN_LAN_IDS", default=[])
ESCALA_FLEX_POLL_SECONDS = env.int("ESCALA_FLEX_POLL_SECONDS", default=60)
# Acesso ao Escala Flex controlado exclusivamente por perfis RBAC (role:*).
ESCALA_FLEX_OPEN_ACCESS = env.bool("ESCALA_FLEX_OPEN_ACCESS", default=False)
ESCALA_IMPORT_ASYNC = env.bool("ESCALA_IMPORT_ASYNC", default=True)
SHAREPOINT_SITE_URL = env("SHAREPOINT_SITE_URL", default="")

# Jira Server/DC (formalização Suporte Claro via REST).
# Auth: bearer = Personal Access Token (padrão DC/Experian); basic = user+senha/token.
JIRA_BASE_URL = env("JIRA_BASE_URL", default="")
JIRA_USERNAME = env("JIRA_USERNAME", default="")
JIRA_API_TOKEN = env("JIRA_API_TOKEN", default="")
JIRA_AUTH_MODE = env("JIRA_AUTH_MODE", default="bearer")
JIRA_HTTP_TIMEOUT = env.int("JIRA_HTTP_TIMEOUT", default=30)
# Proxy Jira é explícito por padrão: evita herdar proxies de sandbox/terminal por acidente.
JIRA_PROXY_URL = env("JIRA_PROXY_URL", default="")
JIRA_TRUST_ENV_PROXY = env.bool("JIRA_TRUST_ENV_PROXY", default=False)
# Perfis — keys/campos após validar com: python manage.py jira_createmeta --profile=...
JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY = env("JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY", default="PPLID")
JIRA_PROFILE_PLANEJAMENTO_ISSUETYPE = env("JIRA_PROFILE_PLANEJAMENTO_ISSUETYPE", default="Task")
JIRA_PROFILE_PLANEJAMENTO_COMPONENT = env("JIRA_PROFILE_PLANEJAMENTO_COMPONENT", default="Suporte Claro")
JIRA_PROFILE_PLANEJAMENTO_PRIORITY = env("JIRA_PROFILE_PLANEJAMENTO_PRIORITY", default="High")
JIRA_PROFILE_PLANEJAMENTO_CATEGORY = env("JIRA_PROFILE_PLANEJAMENTO_CATEGORY", default="Informação")
JIRA_CATEGORY_FIELD_ID = env("JIRA_CATEGORY_FIELD_ID", default="customfield_37898")
JIRA_PROFILE_PROCESSOS_PROJECT_KEY = env("JIRA_PROFILE_PROCESSOS_PROJECT_KEY", default="ANTIFRAUDE")
JIRA_PROFILE_PROCESSOS_ISSUETYPE = env("JIRA_PROFILE_PROCESSOS_ISSUETYPE", default="Análise de Compliance")
JIRA_PROFILE_PROCESSOS_COMPONENT = env("JIRA_PROFILE_PROCESSOS_COMPONENT", default="Suporte N1 Claro")
JIRA_PROFILE_PROCESSOS_PRIORITY = env("JIRA_PROFILE_PROCESSOS_PRIORITY", default="High")
# Transições Jira (CSV de nomes, ordem de tentativa) — sync status portal → Jira.
JIRA_TRANSITION_EM_ATENDIMENTO = env(
    "JIRA_TRANSITION_EM_ATENDIMENTO",
    default="In Progress,Em andamento,Start Progress,Start,Iniciar,Iniciar progresso",
)
JIRA_TRANSITION_CONCLUIDO = env(
    "JIRA_TRANSITION_CONCLUIDO",
    default="Fechada,Fechar,Done,Concluído,Concluido,Resolved,Resolve Issue,Close Issue,Close,Finalizar,Concluir,Complete",
)
JIRA_STATUS_EM_ATENDIMENTO = env(
    "JIRA_STATUS_EM_ATENDIMENTO",
    default="In Progress,Em andamento,Em Andamento,Iniciado",
)
JIRA_STATUS_CONCLUIDO = env(
    "JIRA_STATUS_CONCLUIDO",
    default="Fechada,Fechado,Closed,Done,Concluído,Concluido,Resolved,Resolvida,RESOLVIDA,Complete,Completed,Finalizada",
)
# Demandas Jira — status cancelados/descartados (fora da fila ativa).
JIRA_DEMANDAS_STATUS_CANCELADO = env(
    "JIRA_DEMANDAS_STATUS_CANCELADO",
    default="Cancelado,Cancelada,Cancelled,Canceled,Rejeitado,Rejeitada,Rejected,Descartado,Descartada",
)
JIRA_DEMANDAS_STATUS_INATIVO = env(
    "JIRA_DEMANDAS_STATUS_INATIVO",
    default="Pausado,Paused,On Hold,Em pausa,Aguardando informações,Aguardando,"
    "Waiting,Waiting for customer,Waiting for support,Blocked,Bloqueado",
)
# Cláusula JQL para fila ativa (sync/time). Default: statusCategory != Done.
JIRA_DEMANDAS_JQL_OPEN_CLAUSE = env("JIRA_DEMANDAS_JQL_OPEN_CLAUSE", default="")
# Fila de demandas Jira (Planejamento) — projetos e janela de sync.
JIRA_DEMANDAS_PRIMARY_PROJECT = env("JIRA_DEMANDAS_PRIMARY_PROJECT", default="PPLID")
JIRA_DEMANDAS_PROJECT_KEYS = env(
    "JIRA_DEMANDAS_PROJECT_KEYS",
    default="",
)
JIRA_DEMANDAS_SYNC_DAYS = env.int("JIRA_DEMANDAS_SYNC_DAYS", default=120)
JIRA_DEMANDAS_SYNC_OVERLAP_MINUTES = env.int("JIRA_DEMANDAS_SYNC_OVERLAP_MINUTES", default=10)
JIRA_DEMANDAS_SYNC_STALE_SECONDS = env.int("JIRA_DEMANDAS_SYNC_STALE_SECONDS", default=120)
JIRA_DEMANDAS_TEAM_LAN_IDS = env(
    "JIRA_DEMANDAS_TEAM_LAN_IDS",
    default="C91763A,C92928A,C91893A,C93213A,C93199A,C93048A,C93078A,C91903A,C93189A,C93233A,C93123A",
)
# Comentários de andamento no portal (interno / formalizar externo).
SUPORTE_CLARO_COMENTARIOS_ETAPA = env.bool("SUPORTE_CLARO_COMENTARIOS_ETAPA", default=True)

# Senha inicial das contas provisionadas pelo seed (deve trocar no 1º login).
PORTAL_SEED_DEFAULT_PASSWORD = env("PORTAL_SEED_DEFAULT_PASSWORD", default="pplid@12345678")

# Timeout automático de status após fim da escala (thread em background no processo Django).
STATUS_TIMEOUT_ENABLED = env.bool("STATUS_TIMEOUT_ENABLED", default=True)
STATUS_TIMEOUT_INTERVAL_MINUTES = env.int("STATUS_TIMEOUT_INTERVAL_MINUTES", default=5)

# Reset diário de status operacionais (05:30 horário local).
DAILY_STATUS_RESET_ENABLED = env.bool("DAILY_STATUS_RESET_ENABLED", default=True)
DAILY_STATUS_RESET_HOUR = env.int("DAILY_STATUS_RESET_HOUR", default=5)
DAILY_STATUS_RESET_MINUTE = env.int("DAILY_STATUS_RESET_MINUTE", default=30)

# Anti-gargalo tabela-monitor (cache + fila + throttle).
TABELA_MONITOR_CACHE_TTL = env.int("TABELA_MONITOR_CACHE_TTL", default=120)
TABELA_MONITOR_MAX_CONCURRENT = env.int("TABELA_MONITOR_MAX_CONCURRENT", default=1)
TABELA_MONITOR_QUEUE_WAIT_MS = env.int("TABELA_MONITOR_QUEUE_WAIT_MS", default=15_000)
TABELA_MONITOR_BUILD_WAIT_S = env.int("TABELA_MONITOR_BUILD_WAIT_S", default=60)
TABELA_MONITOR_THROTTLE = env("TABELA_MONITOR_THROTTLE", default="30/min")
AUTH_LOGIN_THROTTLE = env("AUTH_LOGIN_THROTTLE", default="5/min")

# Anti-gargalo produtividade (pool separado do monitor).
PRODUTIVIDADE_CACHE_TTL = env.int("PRODUTIVIDADE_CACHE_TTL", default=120)
PRODUTIVIDADE_MAX_CONCURRENT = env.int("PRODUTIVIDADE_MAX_CONCURRENT", default=1)
PRODUTIVIDADE_QUEUE_WAIT_MS = env.int("PRODUTIVIDADE_QUEUE_WAIT_MS", default=15_000)
PRODUTIVIDADE_BUILD_WAIT_S = env.int("PRODUTIVIDADE_BUILD_WAIT_S", default=60)
PRODUTIVIDADE_THROTTLE = env("PRODUTIVIDADE_THROTTLE", default="30/min")

# Anti-gargalo Monitoramento SLA Resumo (pool separado).
MONITORAMENTO_SLA_CACHE_TTL = env.int("MONITORAMENTO_SLA_CACHE_TTL", default=120)
MONITORAMENTO_SLA_MAX_CONCURRENT = env.int("MONITORAMENTO_SLA_MAX_CONCURRENT", default=1)
MONITORAMENTO_SLA_QUEUE_WAIT_MS = env.int("MONITORAMENTO_SLA_QUEUE_WAIT_MS", default=15_000)
MONITORAMENTO_SLA_BUILD_WAIT_S = env.int("MONITORAMENTO_SLA_BUILD_WAIT_S", default=90)
MONITORAMENTO_SLA_THROTTLE = env("MONITORAMENTO_SLA_THROTTLE", default="30/min")
MONITORAMENTO_SLA_PARQUET_MAX_BYTES = env.int(
    "MONITORAMENTO_SLA_PARQUET_MAX_BYTES", default=500 * 1024 * 1024
)
MONITORAMENTO_SLA_PARQUET_UPLOAD_TTL_HOURS = env.int(
    "MONITORAMENTO_SLA_PARQUET_UPLOAD_TTL_HOURS", default=24
)
MONITORAMENTO_SLA_IMPORT_THROTTLE = env("MONITORAMENTO_SLA_IMPORT_THROTTLE", default="5/min")
MONITORAMENTO_SLA_IMPORT_MAX_CONCURRENT = env.int("MONITORAMENTO_SLA_IMPORT_MAX_CONCURRENT", default=1)
MONITORAMENTO_SLA_IMPORT_QUEUE_WAIT_MS = env.int("MONITORAMENTO_SLA_IMPORT_QUEUE_WAIT_MS", default=15_000)
MONITORAMENTO_SLA_IMPORT_BUILD_WAIT_S = env.int("MONITORAMENTO_SLA_IMPORT_BUILD_WAIT_S", default=120)
MONITORAMENTO_SLA_STATUS_COUNTS_TTL = env.int("MONITORAMENTO_SLA_STATUS_COUNTS_TTL", default=300)
MONITORAMENTO_SLA_PARQUET_IMPORT_SYNC = env.bool("MONITORAMENTO_SLA_PARQUET_IMPORT_SYNC", default=False)
MONITORAMENTO_SLA_PARQUET_IMPORT_STALE_HOURS = env.int(
    "MONITORAMENTO_SLA_PARQUET_IMPORT_STALE_HOURS", default=6
)

# Dashboard de Replicação D-1: recorte limitado, cache curto e fila com falha rápida.
REPLICACAO_D1_DASHBOARD_MAX_PERIOD_DAYS = env.int(
    "REPLICACAO_D1_DASHBOARD_MAX_PERIOD_DAYS", default=31
)
REPLICACAO_D1_DASHBOARD_MAX_CONCURRENT = env.int(
    "REPLICACAO_D1_DASHBOARD_MAX_CONCURRENT", default=1
)
REPLICACAO_D1_DASHBOARD_MAX_INFLIGHT = env.int(
    "REPLICACAO_D1_DASHBOARD_MAX_INFLIGHT", default=4
)
REPLICACAO_D1_DASHBOARD_QUEUE_WAIT_MS = env.int(
    "REPLICACAO_D1_DASHBOARD_QUEUE_WAIT_MS", default=1_000
)
REPLICACAO_D1_DASHBOARD_BUILD_WAIT_S = env.int(
    "REPLICACAO_D1_DASHBOARD_BUILD_WAIT_S", default=30
)
REPLICACAO_D1_DASHBOARD_THROTTLE = env("REPLICACAO_D1_DASHBOARD_THROTTLE", default="60/min")
REPLICACAO_D1_DASHBOARD_CACHE_TTL = env.int("REPLICACAO_D1_DASHBOARD_CACHE_TTL", default=60)
REPLICACAO_D1_DASHBOARD_FAST_RECONCILIATION = env.bool(
    "REPLICACAO_D1_DASHBOARD_FAST_RECONCILIATION", default=True
)

QUALIDADE_IMPORT_MAX_BYTES = env.int(
    "QUALIDADE_IMPORT_MAX_BYTES", default=750 * 1024 * 1024
)
QUALIDADE_COMPLIANCE_IMPORT_RETENTION_DAYS = env.int(
    "QUALIDADE_COMPLIANCE_IMPORT_RETENTION_DAYS", default=3
)
QUALIDADE_OPERACIONAL_THROTTLE = env("QUALIDADE_OPERACIONAL_THROTTLE", default="60/min")
QUALIDADE_OPERACIONAL_EXPORT_THROTTLE = env(
    "QUALIDADE_OPERACIONAL_EXPORT_THROTTLE", default="10/min"
)
QUALIDADE_OPERACIONAL_CACHE_TTL = env.int("QUALIDADE_OPERACIONAL_CACHE_TTL", default=180)
QUALIDADE_OPERACIONAL_MAX_CONCURRENT = env.int(
    "QUALIDADE_OPERACIONAL_MAX_CONCURRENT", default=2
)
QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS = env.int(
    "QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS", default=15_000
)
QUALIDADE_OPERACIONAL_BUILD_WAIT_S = env.int(
    "QUALIDADE_OPERACIONAL_BUILD_WAIT_S", default=120
)
# Fonte do Indicador EO: TSV legado × Intranet (auditoria_falha_cadastro).
# Default seguro: legado desabilitado até aprovação explícita do corte.
QUALIDADE_SOURCE_MODE = env("QUALIDADE_SOURCE_MODE", default="legacy").strip().lower()
QUALIDADE_INTRANET_SOURCE_ENABLED = env.bool(
    "QUALIDADE_INTRANET_SOURCE_ENABLED", default=False
)
QUALIDADE_INTRANET_CUTOVER_DATE = env(
    "QUALIDADE_INTRANET_CUTOVER_DATE", default=""
).strip()
QUALIDADE_INTRANET_TSV_OVERRIDE = env.bool(
    "QUALIDADE_INTRANET_TSV_OVERRIDE", default=False
)
# Rollout reversível da nova população de auditados do Parquet G Auditoria.
# O staging continua sendo alimentado com a flag desligada; apenas a projeção EO
# e a reconciliação ficam inativas até a liberação controlada.
QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED = env.bool(
    "QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED", default=False
)
QUALIDADE_DASHBOARD_WARM_ENABLED = env.bool(
    "QUALIDADE_DASHBOARD_WARM_ENABLED", default=True
)
QUALIDADE_PROJECTION_ASYNC_ENABLED = env.bool(
    "QUALIDADE_PROJECTION_ASYNC_ENABLED", default=True
)
QUALIDADE_PROJECTION_MAX_ATTEMPTS = env.int(
    "QUALIDADE_PROJECTION_MAX_ATTEMPTS", default=3
)

# Quality Overview (brb_report): auditados, falhas e contestação da EO.
# False = planilha master legada (7 abas). True = híbrido EO + suplemento NA/Treinamentos.
BRB_REPORT_USE_EO_DB = env.bool("BRB_REPORT_USE_EO_DB", default=False)
_brb_report_storage = env("BRB_REPORT_STORAGE", default="").strip()
BRB_REPORT_STORAGE = _brb_report_storage or str(MEDIA_ROOT)

CACHE_URL = env("CACHE_URL", default="").strip()
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pplid-default",
        }
    }

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.api_exceptions.secure_exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": AUTH_LOGIN_THROTTLE,
        "tabela_monitor": TABELA_MONITOR_THROTTLE,
        "produtividade_heavy": PRODUTIVIDADE_THROTTLE,
        "monitoramento_sla_resumo": MONITORAMENTO_SLA_THROTTLE,
        "monitoramento_sla_import": MONITORAMENTO_SLA_IMPORT_THROTTLE,
        "replicacao_d1_dashboard": REPLICACAO_D1_DASHBOARD_THROTTLE,
        "qualidade_operacional_list": QUALIDADE_OPERACIONAL_THROTTLE,
        "qualidade_operacional_export": QUALIDADE_OPERACIONAL_EXPORT_THROTTLE,
        "agent_history_import": "20/hour",
        "agent_active_align": "30/hour",
    },
}
