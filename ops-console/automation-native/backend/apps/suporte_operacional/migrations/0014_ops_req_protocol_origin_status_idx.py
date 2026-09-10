from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0013_operationalsupportrequest_operation_origin"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="operationalsupportrequest",
            index=models.Index(
                fields=["protocol", "operation_origin", "status"],
                name="ops_req_prot_origin_st_idx",
            ),
        ),
    ]
