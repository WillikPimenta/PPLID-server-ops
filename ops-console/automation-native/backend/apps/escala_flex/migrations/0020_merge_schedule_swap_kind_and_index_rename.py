from django.db import migrations


class Migration(migrations.Migration):
    """Une ramos paralelos: swap_kind em ScheduleRequest e rename de índice."""

    dependencies = [
        (
            "escala_flex",
            "0019_rename_ef_occ_ext_occ_appr_idx_ef_operatio_occurre_48421c_idx",
        ),
        ("escala_flex", "0019_schedule_request_swap_kind"),
    ]

    operations = []
