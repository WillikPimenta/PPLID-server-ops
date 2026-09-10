from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0005_seed_qual_specialized_role_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalroledefinition",
            name="granted_routes",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
