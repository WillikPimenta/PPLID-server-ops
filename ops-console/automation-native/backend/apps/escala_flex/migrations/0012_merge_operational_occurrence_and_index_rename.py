from django.db import migrations


class Migration(migrations.Migration):
    """Une ramos paralelos: rename de índice (0010) e ocorrências operacionais (0010/0011)."""

    dependencies = [
        (
            "escala_flex",
            "0010_rename_ef_status_e_leader_id_idx_ef_status_e_leader__c5c0e7_idx_and_more",
        ),
        ("escala_flex", "0011_operational_occurrence_cancelled"),
    ]

    operations = []
