from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0048_separate_origin_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="tratado_referencia",
            field=models.ForeignKey(
                blank=True,
                help_text="Tratado anterior espelhado neste protocolo (contestação duplicada).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contestacao_protocolos_espelhados",
                to="auditoria.auditoriafalhacadastro",
            ),
        ),
    ]
