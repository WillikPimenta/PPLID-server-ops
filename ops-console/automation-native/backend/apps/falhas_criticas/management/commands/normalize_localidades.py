# -*- coding: utf-8 -*-
"""Normaliza localidades existentes no banco (BSB/SC canônicos)."""
from django.core.management.base import BaseCommand

from apps.falhas_criticas.models import Contestation, Failure, FalhasAgent, Support, Training
from apps.falhas_criticas.services.localidade import canonicalize_localidade


def _normalize_model(model, field='localidade', batch_size=500):
    updated = 0
    qs = model.objects.exclude(**{f'{field}__exact': ''})
    for obj in qs.iterator(chunk_size=batch_size):
        raw = getattr(obj, field) or ''
        canon = canonicalize_localidade(raw)
        if canon != raw:
            setattr(obj, field, canon)
            obj.save(update_fields=[field])
            updated += 1
    return updated


class Command(BaseCommand):
    help = 'Canonicaliza localidades em falhas_criticas (Brasília / São Carlos / Geral).'

    def handle(self, *args, **options):
        counts = {
            'Failure': _normalize_model(Failure),
            'Support': _normalize_model(Support),
            'Training': _normalize_model(Training),
            'Contestation': _normalize_model(Contestation),
            'FalhasAgent': _normalize_model(FalhasAgent),
        }
        total = sum(counts.values())
        for name, n in counts.items():
            self.stdout.write(f'  {name}: {n} registro(s) atualizado(s)')
        self.stdout.write(self.style.SUCCESS(f'Total: {total} registro(s) normalizado(s).'))
