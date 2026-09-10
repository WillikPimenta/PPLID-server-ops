from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rotina_bruto", "0011_detalhado_data_cadastro_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rotinabrutosynclog",
            name="report_type",
            field=models.CharField(
                choices=[
                    ("detalhado", "Detalhado bruto"),
                    ("prod", "Produtividade D-1 bruto"),
                    ("monitor", "Monitor tratado"),
                    ("confer_busca", "Confer busca protocolo tratado"),
                    ("ged_detalhado", "GED detalhado tratado"),
                    ("ged_irregularidade", "GED irregularidade tratado"),
                    ("g_auditoria", "G Auditoria com etapas"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="rotinabrutosynclog",
            name="mapping_version",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="rotinabrutosynclog",
            name="metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="rotinabrutosynclog",
            name="source_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.CreateModel(
            name="RotinaGAuditoriaRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("report_date", models.DateField(db_index=True)),
                ("source_file", models.CharField(max_length=500)),
                ("source_key", models.CharField(max_length=64, unique=True)),
                ("source_record_key", models.CharField(blank=True, default="", max_length=255)),
                ("origin_transaction_id", models.CharField(blank=True, default="", max_length=255)),
                ("origin_transaction_code", models.CharField(blank=True, default="", max_length=255)),
                (
                    "protocolo_origem",
                    models.CharField(blank=True, db_index=True, default="", max_length=100),
                ),
                (
                    "protocolo_normalizado",
                    models.CharField(blank=True, db_index=True, default="", max_length=100),
                ),
                ("protocolo_destino", models.CharField(blank=True, default="", max_length=100)),
                ("cliente_origem", models.CharField(blank=True, default="", max_length=255)),
                ("workflow_origem", models.CharField(blank=True, default="", max_length=255)),
                ("workflow_destino", models.CharField(blank=True, default="", max_length=255)),
                (
                    "matricula_agente",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                ("matricula_auditor", models.CharField(blank=True, default="", max_length=64)),
                ("etapa", models.CharField(blank=True, default="", max_length=512)),
                (
                    "etapa_normalizada",
                    models.CharField(blank=True, db_index=True, default="", max_length=512),
                ),
                ("data_analise", models.DateField(blank=True, db_index=True, null=True)),
                ("data_auditoria", models.DateField(blank=True, db_index=True, null=True)),
                ("resultado_origem", models.CharField(blank=True, default="", max_length=255)),
                ("resultado_destino", models.CharField(blank=True, default="", max_length=255)),
                ("status_destino", models.CharField(blank=True, default="", max_length=255)),
                (
                    "tipo_conclusao_destino",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("dias_prazo", models.IntegerField(blank=True, null=True)),
                (
                    "prazo_status",
                    models.CharField(
                        choices=[
                            ("dentro_prazo", "Dentro do prazo"),
                            ("fora_prazo", "Fora do prazo (120 dias ou mais)"),
                            ("data_analise_ausente", "Data de análise ausente"),
                            ("data_auditoria_ausente", "Data de auditoria ausente"),
                            ("datas_invertidas", "Datas invertidas"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("content_hash", models.CharField(db_index=True, max_length=64)),
                ("validation_errors", models.JSONField(blank=True, default=list)),
                ("is_quarantined", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("imported_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "rotina_g_auditoria_record",
                "ordering": ["-report_date", "protocolo_origem", "etapa"],
                "indexes": [
                    models.Index(
                        fields=["protocolo_normalizado", "etapa_normalizada"],
                        name="rot_gaud_prot_etapa_idx",
                    ),
                    models.Index(
                        fields=["report_date", "is_active", "is_quarantined"],
                        name="rot_gaud_date_state_idx",
                    ),
                    models.Index(
                        fields=["data_auditoria", "data_analise"],
                        name="rot_gaud_dates_idx",
                    ),
                ],
            },
        ),
    ]
