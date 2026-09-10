import datetime
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='News',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=255, verbose_name='título')),
                ('category', models.CharField(choices=[('Qualidade', 'Qualidade'), ('Operação', 'Operação'), ('Planejamento', 'Planejamento'), ('Processos', 'Processos'), ('Indicadores', 'Indicadores'), ('RH', 'RH'), ('Geral', 'Geral')], default='Geral', max_length=32, verbose_name='categoria')),
                ('author', models.CharField(max_length=255, verbose_name='autor')),
                ('summary', models.CharField(max_length=500, verbose_name='resumo')),
                ('content', models.TextField(verbose_name='conteúdo')),
                ('published_at', models.DateField(default=datetime.date.today, verbose_name='data de publicação')),
                ('active', models.BooleanField(default=True, verbose_name='ativo')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='news_created', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='news_updated', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'notícia',
                'verbose_name_plural': 'notícias',
                'db_table': 'news',
                'ordering': ['-published_at', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='news',
            index=models.Index(fields=['-published_at'], name='news_publish_idx'),
        ),
        migrations.AddIndex(
            model_name='news',
            index=models.Index(fields=['category'], name='news_categor_idx'),
        ),
        migrations.AddIndex(
            model_name='news',
            index=models.Index(fields=['active'], name='news_active_idx'),
        ),
    ]
