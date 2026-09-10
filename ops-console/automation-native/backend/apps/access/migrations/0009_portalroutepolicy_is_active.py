from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0008_seed_customized_quality_roles"),
    ]

    # O campo pertence ao ramo principal em 0010_route_status_and_menu_options.
    # Este nó paralelo permanece apenas para reconciliar o grafo no merge 0013.
    operations = []
