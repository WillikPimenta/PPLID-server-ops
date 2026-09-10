from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("controle_sla", "0002_slabreach_criticidade"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="slabreach",
            name="uq_controle_sla_breach_key",
        ),
        migrations.RemoveIndex(
            model_name="slabreach",
            name="controle_sl_cod_cli_1a2b3c_idx",
        ),
        # Remove duplicates before unique (cliente, id_workflow): keep highest pct_sla.
        migrations.RunSQL(
            sql="""
            DELETE FROM controle_sla_breach
            WHERE id NOT IN (
                SELECT keep_id FROM (
                    SELECT MIN(id) AS keep_id
                    FROM controle_sla_breach
                    WHERE id_workflow IS NOT NULL
                    GROUP BY cod_cliente, id_workflow
                ) AS t
            )
            OR id_workflow IS NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterModelOptions(
            name="slabreach",
            options={"ordering": ["-pct_sla", "nom_cliente", "nom_workflow"]},
        ),
        migrations.AddIndex(
            model_name="slabreach",
            index=models.Index(
                fields=["cod_cliente", "id_workflow"],
                name="controle_sl_cod_cli_wf_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="slabreach",
            constraint=models.UniqueConstraint(
                fields=("cod_cliente", "id_workflow"),
                name="uq_controle_sla_breach_cliente_workflow",
            ),
        ),
    ]
