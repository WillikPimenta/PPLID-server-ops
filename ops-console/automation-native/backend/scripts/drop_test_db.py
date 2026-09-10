"""Drop Django test database (test_<POSTGRES_DB>)."""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings

import psycopg

db = settings.DATABASES["default"]
test_name = f"test_{db['NAME']}"

conn = psycopg.connect(
    dbname="postgres",
    user=db["USER"],
    password=db["PASSWORD"],
    host=db["HOST"],
    port=db["PORT"],
)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (test_name,),
    )
    cur.execute(f'DROP DATABASE IF EXISTS "{test_name}"')
conn.close()
print(f"Dropped {test_name}", file=sys.stderr)
