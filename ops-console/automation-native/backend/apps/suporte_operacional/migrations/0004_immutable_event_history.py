import django.db.models.deletion
from django.db import migrations, models


def _isoformat(value):
    return value.isoformat() if value else None


def _user_snapshot(user):
    if not user:
        return None
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return {
        "id": str(user.pk),
        "username": getattr(user, "username", "") or "",
        "name": name or getattr(user, "username", "") or "",
    }


def _already_happened(event_time, action_time):
    return bool(action_time and event_time and event_time >= action_time)


def backfill_event_history(apps, schema_editor):
    Request = apps.get_model("suporte_operacional", "OperationalSupportRequest")
    Event = apps.get_model("suporte_operacional", "OperationalSupportEvent")
    AgentHistory = apps.get_model("workforce", "AgentHistory")

    requests = Request.objects.select_related(
        "agent",
        "requester",
        "leader_decider",
        "assignee",
        "answered_by",
        "cancelled_by",
    ).iterator(chunk_size=200)

    for request_obj in requests:
        current_history = (
            AgentHistory.objects.filter(
                agent_id=request_obj.agent_id,
                active=True,
                final_date__isnull=True,
            )
            .select_related("leader")
            .order_by("-start_date")
            .first()
        )
        leader = current_history.leader if current_history and current_history.leader_id else None
        agent_snapshot = {
            "id": str(request_obj.agent_id),
            "lan_id": request_obj.agent.user_lan_id or "",
            "name": request_obj.agent.full_name or "",
            "leader_id": str(leader.pk) if leader else None,
            "leader_lan_id": leader.user_lan_id if leader else "",
            "leader_name": leader.full_name if leader else "",
            "office": current_history.location if current_history else "",
            "team": current_history.team if current_history else "",
            "team_sector": current_history.team_sector if current_history else "",
        }

        events = (
            Event.objects.filter(request_id=request_obj.pk)
            .select_related("author")
            .order_by("created_at", "id")
        )
        for sequence, event in enumerate(events, start=1):
            actor = _user_snapshot(event.author) or {
                "id": None,
                "username": "",
                "name": "",
            }
            leader_decided = _already_happened(event.created_at, request_obj.leader_decided_at)
            assigned = _already_happened(event.created_at, request_obj.assigned_at)
            answered = _already_happened(event.created_at, request_obj.answered_at)
            cancelled = _already_happened(event.created_at, request_obj.cancelled_at)

            snapshot = {
                "schema_version": 1,
                "legacy_backfill": True,
                "request": {
                    "id": str(request_obj.pk),
                    "protocol": request_obj.protocol,
                    "workflow": request_obj.workflow,
                    "client": request_obj.client,
                    "subject": request_obj.subject,
                    "category": request_obj.category,
                    "description": request_obj.description,
                    "reference": request_obj.reference,
                    "status": event.to_status or request_obj.status,
                    "requester_type": request_obj.requester_type,
                    "agent": agent_snapshot,
                    "requester": _user_snapshot(request_obj.requester),
                    "leader_decision": {
                        "decider": _user_snapshot(request_obj.leader_decider) if leader_decided else None,
                        "decision": request_obj.leader_decision if leader_decided else "",
                        "justification": request_obj.leader_justification if leader_decided else "",
                        "decided_at": _isoformat(request_obj.leader_decided_at) if leader_decided else None,
                        "auto_approved": request_obj.auto_approved if leader_decided else False,
                    },
                    "assignment": {
                        "assignee": _user_snapshot(request_obj.assignee) if assigned else None,
                        "assigned_at": _isoformat(request_obj.assigned_at) if assigned else None,
                    },
                    "answer": {
                        "text": request_obj.answer if answered else "",
                        "difficulty_level": request_obj.difficulty_level if answered else "",
                        "document_uf": request_obj.document_uf if answered else "",
                        "document_type": request_obj.document_type if answered else "",
                        "answered_by": _user_snapshot(request_obj.answered_by) if answered else None,
                        "answered_at": _isoformat(request_obj.answered_at) if answered else None,
                    },
                    "cancellation": {
                        "cancelled_by": _user_snapshot(request_obj.cancelled_by) if cancelled else None,
                        "reason": request_obj.cancel_reason if cancelled else "",
                        "cancelled_at": _isoformat(request_obj.cancelled_at) if cancelled else None,
                    },
                    "created_at": _isoformat(request_obj.created_at),
                    "updated_at": _isoformat(event.created_at),
                },
                "actor": {**actor, "roles": []},
            }
            Event.objects.filter(pk=event.pk).update(
                sequence=sequence,
                event_version=1,
                actor_username=actor["username"],
                actor_name=actor["name"],
                actor_roles=[],
                snapshot=snapshot,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_operacional", "0003_operationalsupportrequest_client_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportevent",
            name="actor_name",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="operationalsupportevent",
            name="actor_roles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="operationalsupportevent",
            name="actor_username",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AddField(
            model_name="operationalsupportevent",
            name="event_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="operationalsupportevent",
            name="sequence",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="operationalsupportevent",
            name="snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_event_history, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="operationalsupportevent",
            name="sequence",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name="operationalsupportevent",
            name="request",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="events",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
        migrations.AlterModelOptions(
            name="operationalsupportevent",
            options={"ordering": ["request_id", "sequence"]},
        ),
        migrations.AddConstraint(
            model_name="operationalsupportevent",
            constraint=models.UniqueConstraint(
                fields=("request", "sequence"),
                name="ops_evt_req_seq_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="operationalsupportevent",
            index=models.Index(fields=["created_at"], name="ops_evt_created_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalsupportevent",
            index=models.Index(
                fields=["event_type", "created_at"],
                name="ops_evt_type_created_idx",
            ),
        ),
    ]
