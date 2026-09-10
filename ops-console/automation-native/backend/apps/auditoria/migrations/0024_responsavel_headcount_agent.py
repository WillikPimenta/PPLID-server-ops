# Generated manually: responsavel User -> Agent (headcount)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards_responsavel_to_agent(apps, schema_editor):
    Atividade = apps.get_model("auditoria", "AuditoriaAtividade")
    Agent = apps.get_model("workforce", "Agent")
    for atividade in Atividade.objects.exclude(responsavel_id=None).iterator():
        user = atividade.responsavel
        if not user:
            continue
        username = (getattr(user, "username", "") or "").strip()
        if not username:
            continue
        agent = Agent.objects.filter(user_lan_id__iexact=username).first()
        if agent:
            atividade.responsavel_agent_id = agent.pk
            atividade.save(update_fields=["responsavel_agent_id"])


def backwards_agent_to_user(apps, schema_editor):
    Atividade = apps.get_model("auditoria", "AuditoriaAtividade")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    for atividade in Atividade.objects.exclude(responsavel_agent_id=None).select_related(
        "responsavel_agent"
    ).iterator():
        agent = atividade.responsavel_agent
        if not agent:
            continue
        lan = (agent.user_lan_id or "").strip()
        if not lan:
            continue
        user = User.objects.filter(username__iexact=lan).first()
        if user:
            atividade.responsavel_id = user.pk
            atividade.save(update_fields=["responsavel_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("workforce", "0008_agent_jira_api_token"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auditoria", "0023_situacao_procedente_improcedente"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="responsavel_agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_atividades_responsavel_tmp",
                to="workforce.agent",
            ),
        ),
        migrations.RunPython(forwards_responsavel_to_agent, backwards_agent_to_user),
        migrations.RemoveField(
            model_name="auditoriaatividade",
            name="responsavel",
        ),
        migrations.RenameField(
            model_name="auditoriaatividade",
            old_name="responsavel_agent",
            new_name="responsavel",
        ),
        migrations.AlterField(
            model_name="auditoriaatividade",
            name="responsavel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_atividades_responsavel",
                to="workforce.agent",
            ),
        ),
    ]
