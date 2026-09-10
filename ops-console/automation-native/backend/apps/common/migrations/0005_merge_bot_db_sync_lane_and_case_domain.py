# -*- coding: utf-8 -*-
# Merge das branches: produtividade_case domain + lane/runtime config.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0003_bot_db_sync_produtividade_case_domain"),
        ("common", "0004_bot_db_sync_runtime_config"),
    ]

    operations = []
