# Generated manually for tratados rotina/GED sync

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rotina_bruto", "0006_rename_rotina_mon_report__a1b2c3_idx_rotina_moni_report__498e53_idx_and_more"),
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
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="RotinaConferBuscaRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True)),
                ("periodo", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("data_hora_conferencia", models.DateTimeField(blank=True, null=True)),
                ("tempo_por_minuto", models.CharField(blank=True, default="", max_length=64)),
                ("protocolo", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("status", models.CharField(blank=True, default="", max_length=255)),
                ("ilha", models.CharField(blank=True, default="", max_length=255)),
                ("etapa", models.CharField(blank=True, default="", max_length=255)),
                ("matricula", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("nome", models.CharField(blank=True, default="", max_length=255)),
                ("tipo_status_conferencia", models.CharField(blank=True, default="", max_length=255)),
                ("extra", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "rotina_confer_busca_record",
                "ordering": ["-report_date", "protocolo"],
                "indexes": [
                    models.Index(fields=["report_date", "periodo"], name="rotina_conf_report__a8f1d2_idx"),
                    models.Index(fields=["report_date", "protocolo"], name="rotina_conf_report__b3e4c1_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="RotinaGedDetalhadoTratadoRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True)),
                ("periodo", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("protocolo", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("data_recebimento", models.DateField(blank=True, null=True)),
                ("tipo_servico_primario", models.CharField(blank=True, default="", max_length=255)),
                ("data_venda", models.DateField(blank=True, null=True)),
                ("data_batimento", models.DateField(blank=True, null=True)),
                ("data_retorno_inspecao", models.DateField(blank=True, null=True)),
                ("data_envio_inspe", models.DateField(blank=True, null=True)),
                ("canal_ativacao", models.CharField(blank=True, default="", max_length=255)),
                ("status_contrato", models.CharField(blank=True, default="", max_length=255)),
                ("aceite_digital", models.CharField(blank=True, default="", max_length=255)),
                ("extra", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "rotina_ged_detalhado_tratado_record",
                "ordering": ["-report_date", "protocolo"],
                "indexes": [
                    models.Index(fields=["report_date", "periodo"], name="rotina_ged__report__c2d5e8_idx"),
                    models.Index(fields=["report_date", "protocolo"], name="rotina_ged__report__f7a9b3_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="RotinaGedIrregularidadeTratadoRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True)),
                ("periodo", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("protocolo", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("status_contrato", models.CharField(blank=True, default="", max_length=255)),
                ("msisdn", models.CharField(blank=True, default="", max_length=64)),
                ("cpf", models.CharField(blank=True, default="", max_length=64)),
                ("data_recebimento", models.DateField(blank=True, null=True)),
                ("data_contestacao", models.DateField(blank=True, null=True)),
                ("data_resposta", models.DateField(blank=True, null=True)),
                ("tipo_servico", models.CharField(blank=True, default="", max_length=255)),
                ("regional", models.CharField(blank=True, default="", max_length=255)),
                ("estado", models.CharField(blank=True, default="", max_length=64)),
                ("canal_ativacao", models.CharField(blank=True, default="", max_length=255)),
                ("cod_pdv", models.CharField(blank=True, default="", max_length=64)),
                ("usuario", models.CharField(blank=True, default="", max_length=255)),
                ("status_contestacao", models.CharField(blank=True, default="", max_length=255)),
                ("matricula_inspetor", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("descricao_irregularidades", models.TextField(blank=True, default="")),
                ("extra", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "rotina_ged_irregularidade_tratado_record",
                "ordering": ["-report_date", "protocolo"],
                "indexes": [
                    models.Index(fields=["report_date", "periodo"], name="rotina_ged__report__d4e6f1_idx"),
                    models.Index(fields=["report_date", "protocolo"], name="rotina_ged__report__e8b2c7_idx"),
                ],
            },
        ),
    ]
